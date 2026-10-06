/** Offline integration checks. Synthetic messages never become benchmark records. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { execute } from './benchmark_bridge.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const python=process.env.PI_TEST_PYTHON ?? 'python3';
// Pi checks auth before entering the stream function. This non-secret placeholder
// is used only with the injected stream; no HTTP provider is invoked by this file.
process.env.PI_OFFLINE_TEST_NO_KEY='offline-test-placeholder';

function fakeStream(script, observed) {
  let cursor=0;
  return (model, context) => {
    observed.push(structuredClone(context.messages));
    const item=script[cursor++];
    assert.ok(item, 'unexpected extra model request');
    const message={role:'assistant', content:item.content ?? [{type:'text',text:item.text}],
      api:model.api, provider:model.provider, model:model.id, timestamp:Date.now(),
      stopReason:item.stopReason ?? (item.content?.some(c => c.type==='toolCall') ? 'toolUse' : 'stop'),
      rawStopReason:'completed', responseId:`offline-${cursor}`,
      usage:{input:2,output:1,cacheRead:0,cacheWrite:0,totalTokens:3,
        cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
    return {async *[Symbol.asyncIterator]() { yield {type:'done',reason:message.stopReason,message}; },
      result:async () => message};
  };
}

async function mocked(request, script) {
  const observed=[];
  const result=await execute(request,{sessionHook:session => {
    session.agent.streamFunction=fakeStream(script,observed);
  }});
  return {result,observed};
}

if (process.argv[2]==='--child') {
  let input=''; for await (const c of process.stdin) input+=c;
  const {request,script}=JSON.parse(input);
  console.log(JSON.stringify(await mocked(request,script)));
} else {
  const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'pi-offline-check-'));
  try {
    const toolInfo=spawnSync(python,['-c',
      'import json; from benchmark_tools import tool_specs; print(json.dumps(tool_specs(["calculator", "write_todos"])))'],
      {cwd:here,encoding:'utf8'});
    assert.equal(toolInfo.status,0,toolInfo.stderr);
    const tools=JSON.parse(toolInfo.stdout);
    const base={provider:'openai',model:'gpt-6-luna',api_key_env:'PI_OFFLINE_TEST_NO_KEY',
      thinking_level:'medium',python,run_dir:temporary,tools:[],fixtures:{},
      instructions:'Offline synthetic transport check.',prompts:['ทดสอบ Unicode 🐘'],
      max_model_requests_per_turn:10,turn_timeout_s:10};
    const calc={type:'toolCall',id:'offline-call-1',name:'calculator',arguments:{operation:'add',a:2,b:3}};
    const success=await mocked({...base,run_dir:path.join(temporary,'math'),tools:[tools[0]]},[
      {content:[calc]}, {text:'ทดสอบสำเร็จ 🐘'},
    ]);
    assert.equal(success.result.runtime_status,'completed',JSON.stringify(success.result.error));
    assert.deepEqual(success.result.active_tools,['calculator']);
    assert.equal(success.result.trace.find(e => e.kind==='tool_result').output,5);
    assert.equal(success.result.answer,'ทดสอบสำเร็จ 🐘');
    assert.equal(success.result.usage.total_tokens,6);
    assert.equal(success.result.turns[0].requests,2);
    assert.ok(success.result.effective_instructions.startsWith(base.instructions));
    assert.ok(!success.result.active_tools.includes('bash'));
    assert.equal(success.result.turns[0].prompt_characters_js_utf16,base.prompts[0].length);
    assert.equal(success.result.turns[0].prompt_code_points,[...base.prompts[0]].length);
    const error=await mocked({...base,run_dir:path.join(temporary,'tool-error'),tools:[tools[0]]},[
      {content:[{...calc,arguments:{operation:'divide',a:2,b:0}}]}, {text:'Handled synthetic error.'},
    ]);
    assert.equal(error.result.trace.find(e => e.kind==='tool_result').is_error,true);
    assert.match(error.result.trace.find(e => e.kind==='tool_result').output,/Cannot divide by zero/);
    const budget=await mocked({...base,run_dir:path.join(temporary,'budget'),tools:[tools[0]],max_model_requests_per_turn:1},[
      {content:[calc]},
    ]);
    assert.equal(budget.result.runtime_status,'runtime_error');
    assert.match(budget.result.error.message,/budget exceeded/);
    assert.equal(budget.observed.length,1);
    assert.equal(budget.result.usage,null);

    function child(request,script) {
      const result=spawnSync(process.execPath,['--import','tsx',fileURLToPath(import.meta.url),'--child'],
        {input:JSON.stringify({request,script}),encoding:'utf8',cwd:here,timeout:30000});
      assert.equal(result.status,0,result.stderr);
      return JSON.parse(result.stdout);
    }
    const seed=child({...base,run_dir:path.join(temporary,'seed'),persistence:true,prompts:['Remember synthetic marker unit-test-42.']},[{text:'Saved synthetic marker.'}]);
    assert.equal(seed.result.runtime_status,'completed',JSON.stringify(seed.result.error));
    assert.ok(seed.result.session_file);
    const restored=child({...base,run_dir:path.join(temporary,'resume'),persistence:true,
      session_file:seed.result.session_file,session_sha256:seed.result.session_sha256,
      previous_node_pid:seed.result.node_pid,expected_versions:seed.result.versions,
      prompts:['Recall the earlier synthetic marker.']},[{text:'Synthetic resumed answer.'}]);
    assert.equal(restored.result.runtime_status,'completed',JSON.stringify(restored.result.error));
    assert.notEqual(restored.result.node_pid,seed.result.node_pid);
    assert.ok(restored.result.restored_message_count>0);
    assert.ok(JSON.stringify(restored.observed[0]).includes('unit-test-42'));
    assert.equal(restored.result.usage.total_tokens,3,'must not double count seed usage');

    const fixtures={COMPANY:{company_id:'cmp_a17',company_name:'Alpha'},
      STATEMENT:{company_id:'cmp_a17',year:2025,revenue_million:1375,net_profit_million:165,source_id:'fixture_alpha_2025_v1'}};
    const mcp=await mocked({...base,run_dir:path.join(temporary,'mcp'),fixtures,mcp:{retry_fixture:true}},[
      {content:[{type:'toolCall',id:'mcp-a',name:'search_company',arguments:{query:' Alpha '}}]},
      {content:[{type:'toolCall',id:'mcp-b',name:'get_financial_statement',arguments:{company_id:'cmp_a17',year:2025}}]},
      {content:[{type:'toolCall',id:'mcp-c',name:'get_financial_statement',arguments:{company_id:'cmp_a17',year:2025}}]},
      {text:'Synthetic MCP integration check complete.'},
    ]);
    assert.equal(mcp.result.runtime_status,'completed',JSON.stringify(mcp.result.error));
    assert.deepEqual(mcp.result.discovered_tools.sort(),['get_financial_statement','search_company']);
    const outputs=mcp.result.trace.filter(e => e.kind==='mcp_result');
    assert.equal(outputs.length,3,JSON.stringify(mcp.result.trace));
    assert.deepEqual(outputs[0].output,{companies:[fixtures.COMPANY]});
    assert.equal(outputs[1].output.error.code,'TEMPORARY_UNAVAILABLE');
    assert.deepEqual(outputs[2].output,fixtures.STATEMENT);
    assert.equal(mcp.result.latency.cleanup_s>0,true);
    assert.equal(mcp.result.cleanup_error,undefined);
    console.log('PASS: Pi loop with mocked model, Unicode, tools/errors, allowlist, request budget, native cross-process session, fresh usage, real MCP discovery/calls/cleanup. No model API called.');
  } finally {
    await fs.rm(temporary,{recursive:true,force:true});
  }
}
