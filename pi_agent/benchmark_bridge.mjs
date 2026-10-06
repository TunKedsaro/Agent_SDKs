/** Python/Node transport only. Pi owns the model/tool loop. No benchmark answers. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { performance } from 'node:perf_hooks';
import {
  createAgentSession, ModelRuntime, SessionManager, SettingsManager,
} from '@earendil-works/pi-coding-agent';
import { createHostManagedMcpAdapter } from 'pi-mcp-adapter/host-managed';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const sha = value => createHash('sha256').update(value).digest('hex');
const copy = value => JSON.parse(JSON.stringify(value));
const publicMessage = message => ({...message,
  content: Array.isArray(message.content)
    ? message.content.filter(block => block.type !== 'thinking') : message.content,
});

async function packageVersion(name, resolver = require) {
  for (const directory of resolver.resolve.paths(name) ?? []) {
    try {
      const p = JSON.parse(await fs.readFile(path.join(directory, name, 'package.json'), 'utf8'));
      if (p.name === name) return p.version;
    } catch { /* Check the next Node resolution directory. */ }
  }
  throw new Error(`Cannot locate package metadata: ${name}`);
}

export async function versions() {
  const piRequire = createRequire(import.meta.resolve('@earendil-works/pi-coding-agent'));
  return {
    node: process.version,
    '@earendil-works/pi-coding-agent': await packageVersion('@earendil-works/pi-coding-agent'),
    '@earendil-works/pi-agent-core': await packageVersion('@earendil-works/pi-agent-core', piRequire),
    '@earendil-works/pi-ai': await packageVersion('@earendil-works/pi-ai', piRequire),
    'pi-mcp-adapter': await packageVersion('pi-mcp-adapter'),
    '@modelcontextprotocol/client': await packageVersion('@modelcontextprotocol/client'),
  };
}

function startWorker(request) {
  const child = spawn(request.python, [path.join(here, 'benchmark_tools.py')],
    {stdio:['pipe', 'pipe', 'pipe']});
  child.stderr.on('data', data => process.stderr.write(data));
  const pending = new Map();
  const fail = error => { for (const p of pending.values()) p.reject(error); pending.clear(); };
  child.on('error', fail);
  child.on('exit', code => fail(new Error(`Business tool worker exited (${code})`)));
  createInterface({input:child.stdout}).on('line', line => {
    try {
      const response = JSON.parse(line), waiter = pending.get(response.id);
      pending.delete(response.id);
      if (!waiter) return;
      if (response.error) waiter.reject(Object.assign(new Error(response.error.message), response.error));
      else waiter.resolve(response.output);
    } catch (error) { fail(error); }
  });
  child.stdin.write(JSON.stringify({fixtures:request.fixtures, allowed_tools:request.tools.map(t => t.name)})+'\n');
  return {
    call(name, args) {
      const id = randomUUID();
      return new Promise((resolve, reject) => {
        pending.set(id, {resolve, reject});
        child.stdin.write(JSON.stringify({id, name, arguments:args})+'\n');
      });
    },
    async close() {
      child.stdin.end();
      if (child.exitCode !== null) return;
      await new Promise(resolve => {
        const timer = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 1000);
        child.once('exit', () => { clearTimeout(timer); resolve(); });
      });
    },
  };
}

function decodeResult(result) {
  if (result?.details?.business_output !== undefined) return result.details.business_output;
  if (result?.structuredContent !== undefined) return result.structuredContent;
  const text = result?.content?.filter(x => x.type === 'text').map(x => x.text).join('\n');
  try { return JSON.parse(text); } catch { return text ?? null; }
}

export const WEB_LIMITATION = {
  supported: false,
  reason: 'Pinned pi-ai Responses integration does not expose hosted web search/open_page events and URL annotations in its public assistant trace.',
  evidence: [
    'pi-ai/dist/api/openai-responses.d.ts: OpenAIResponsesOptions has no hosted web tool option.',
    'pi-ai/dist/api/openai-responses-shared.js: createSlot handles reasoning, message, function_call and custom_tool_call; web_search_call is not emitted.',
    'pi-ai/dist/api/openai-responses-shared.js: output_text annotations are not preserved as citation events.',
    'before_provider_request/onPayload can mutate a request, but does not supply the missing hosted result trace; this benchmark does not inject web tools.',
  ],
};

/** sessionHook is dependency injection for offline tests; it is never read from JSON input. */
export async function execute(request, {emit = () => {}, sessionHook} = {}) {
  const started = performance.now();
  const record = {runtime_status:'runtime_error', error:null, answer:null, trace:[], turns:[],
    usage:null, citations:null, hosted_events:null, discovered_tools:null, tool_schemas:null,
    versions:await versions(), node_pid:process.pid, node_instance:randomUUID(),
    session_backend:request.persistence ? 'Pi SessionManager JSONL' : 'Pi SessionManager.inMemory (one bridge invocation)',
    latency:{setup_s:null, agent_s:null, cleanup_s:null, bridge_total_s:null},
    web_capability:WEB_LIMITATION};
  let session, worker, adapter, currentTurn = -1, requestsThisTurn = 0;
  let agentStarted = null;
  const trace = event => {
    const item = {...event, sequence:record.trace.length, turn:currentTurn};
    record.trace.push(copy(item)); emit({kind:'event', event:item});
  };
  try {
    await fs.mkdir(request.run_dir, {recursive:true});
    const modelRuntime = await ModelRuntime.create({
      authPath:path.join(request.run_dir, 'unused-auth.json'), modelsPath:null,
      modelsStorePath:path.join(request.run_dir, 'model-store.json'), allowModelNetwork:false,
    });
    const model = modelRuntime.getModel(request.provider, request.model);
    record.model_found = Boolean(model);
    record.model = model ? {id:model.id, provider:model.provider, api:model.api} : null;
    if (request.mode === 'inspect') {
      record.runtime_status = 'completed'; return record;
    }
    if (!model) throw new Error(`Requested model is absent in Pi catalog: ${request.provider}:${request.model}; no fallback used`);
    if (request.expected_versions && JSON.stringify(record.versions) !== JSON.stringify(request.expected_versions))
      throw new Error('Package versions changed since the session was seeded');
    if (request.mode !== 'discover') {
      const key = process.env[request.api_key_env];
      if (!key && !sessionHook) throw new Error(`Missing environment variable ${request.api_key_env}`);
      if (key) await modelRuntime.setRuntimeApiKey(request.provider, key);
    }
    let mcpDefinitions = [];
    if (request.mcp) {
      const fixtureFile = path.join(request.run_dir, 'mcp-fixture.json');
      await fs.writeFile(fixtureFile, JSON.stringify({fixtures:request.fixtures,
        retry_fixture:request.mcp.retry_fixture}), 'utf8');
      adapter = createHostManagedMcpAdapter({
        servers:{finance:{
          createTransport:() => new StdioClientTransport({command:request.python,
            args:[path.join(here, 'benchmark_tools.py'), '--mcp', fixtureFile], stderr:'inherit'}),
          tools:['search_company', 'get_financial_statement'],
        }}, requestTimeoutMs:30000,
        onToolCall:async call => {
          trace({kind:'mcp_dispatch', id:call.toolCallId, name:call.tool,
            adapter_name:call.toolName, arguments:call.arguments});
          try {
            const result = await call.dispatch();
            trace({kind:'mcp_result', id:call.toolCallId, name:call.tool,
              output:decodeResult(result), raw_result:result});
            return result;
          } catch (error) {
            trace({kind:'mcp_delivery_error', id:call.toolCallId, name:call.tool,
              delivery:error.delivery ?? null, message:error.message});
            throw error;
          }
        },
      });
      await adapter.ready();
    } else if (request.tools.length) worker = startWorker(request);

    const extension = pi => {
      if (adapter) {
        // The host-managed profile requires an explicit broker. The only server
        // is our local read-only fixture, authorized by selecting this MCP case.
        pi.events.on('pi-mcp-adapter:tool-approval-request', event => {
          event.claim(() => event.serverName === 'finance' &&
            ['search_company','get_financial_statement'].includes(event.originalToolName)
            ? 'allow_once' : 'deny');
        });
        adapter.extensionFactory({...pi, registerTool:definition => {
          // Adapter prefixes names. Expose original business names to retain prompt/schema parity.
          const name = definition.name.replace(/^finance_/, '');
          mcpDefinitions.push({...definition, name});
          pi.registerTool({...definition, name});
        }});
      }
    };
    // Explicit loader prevents local .pi/mcp.json, shell tools, skills, and user config discovery.
    // Use the public loader for inline factories only; no directory discovery.
    const { DefaultResourceLoader } = await import('@earendil-works/pi-coding-agent');
    const loader = new DefaultResourceLoader({cwd:request.run_dir, agentDir:request.run_dir,
      noExtensions:true, noSkills:true, noPromptTemplates:true, noThemes:true, noContextFiles:true,
      extensionFactories:[extension], systemPrompt:request.instructions,
      appendSystemPrompt:[],
    });
    await loader.reload();
    const allowed = request.mcp ? ['search_company','get_financial_statement'] : request.tools.map(t => t.name);
    let manager;
    if (request.session_file) {
      const bytes = await fs.readFile(request.session_file);
      if (sha(bytes) !== request.session_sha256) throw new Error('Session file hash changed before restore');
      manager = SessionManager.open(request.session_file, path.dirname(request.session_file), request.run_dir);
      const restored = manager.buildSessionContext().messages;
      record.restored_message_count = restored.length;
      record.restored_context_sha256 = sha(JSON.stringify(restored));
      record.restored_messages_before_model = restored.map(publicMessage);
      if (!restored.some(m => m.role === 'user') || !restored.some(m => m.role === 'assistant'))
        throw new Error('Session has no seed conversation to restore');
      if (request.previous_node_pid === process.pid) throw new Error('Expected a different Node process');
    } else manager = request.persistence
      ? SessionManager.create(request.run_dir, path.join(request.run_dir, 'sessions'))
      : SessionManager.inMemory(request.run_dir);
    const created = await createAgentSession({cwd:request.run_dir, agentDir:request.run_dir,
      model, modelRuntime, resourceLoader:loader, sessionManager:manager,
      tools:allowed, noTools:'builtin', thinkingLevel:request.thinking_level,
      customTools:request.tools.map(spec => ({...spec, label:spec.name,
        execute:async (_id, args) => {
          const output = await worker.call(spec.name, args);
          return {content:[{type:'text', text:typeof output === 'string' ? output : JSON.stringify(output)}],
            details:{business_output:output}};
        }})),
      settingsManager:SettingsManager.inMemory({
        compaction:{enabled:false}, retry:{enabled:false, maxRetries:0, provider:{maxRetries:0, timeoutMs:60000}},
        cacheWarming:'off', enableAnalytics:false, enableInstallTelemetry:false,
      }),
    });
    session = created.session;
    if (created.modelFallbackMessage) throw new Error(created.modelFallbackMessage);
    await session.bindExtensions({});
    session.setActiveToolsByName(allowed);
    record.active_tools = session.getActiveToolNames();
    if (JSON.stringify([...record.active_tools].sort()) !== JSON.stringify([...allowed].sort()))
      throw new Error('Active tool allowlist does not match benchmark');
    record.discovered_tools = request.mcp ? mcpDefinitions.map(t => t.name) : null;
    record.tool_schemas = request.mcp
      ? mcpDefinitions.map(({name,description,parameters}) => ({name,description,parameters})) : request.tools;
    record.effective_instructions = session.systemPrompt;
    record.thinking_level = session.thinkingLevel;
    record.model_budget = {max_model_requests_per_turn:request.max_model_requests_per_turn,
      source_recursion_limit:request.source_recursion_limit ?? null,
      note:'Different units; no equivalence to LangGraph recursion_limit is claimed.',
      api_retries:0, harness_retries:0, auto_compaction:false, cache_warming:false};
    record.latency.setup_s = (performance.now()-started)/1000;
    if (request.mode === 'discover') { record.runtime_status='completed'; return record; }
    if (sessionHook) await sessionHook(session);
    const originalStream = session.agent.streamFunction;
    session.agent.streamFunction = (requestModel, context, options) => {
      // Extension callback exceptions are swallowed by Pi; enforce the budget at
      // the public stream boundary before any provider request is dispatched.
      if (requestsThisTurn >= request.max_model_requests_per_turn) {
        const message = {role:'assistant', content:[], api:requestModel.api,
          provider:requestModel.provider, model:requestModel.id, timestamp:Date.now(),
          stopReason:'error', errorMessage:'Pi benchmark model-request budget exceeded',
          usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,
            cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
        return {async *[Symbol.asyncIterator]() { yield {type:'error',reason:'error',error:message}; },
          result:async () => message};
      }
      requestsThisTurn++;
      return originalStream(requestModel, context, options);
    };
    session.subscribe(event => {
      if (event.type === 'message_end') {
        const message = event.message;
        if (message.role === 'assistant') {
          const assistantSequence=record.trace.length;
          trace({kind:'assistant', message:publicMessage(message)});
          for (const block of message.content ?? []) if (block.type === 'toolCall')
            trace({kind:'tool_call', id:block.id, name:block.name, arguments:block.arguments,
              assistant_sequence:assistantSequence});
        }
      } else if (event.type === 'tool_execution_end') {
        trace({kind:'tool_result', id:event.toolCallId, name:event.toolName,
          output:decodeResult(event.result), is_error:event.isError, raw_result:event.result});
      }
    });
    agentStarted = performance.now();
    for (const prompt of request.prompts) {
      currentTurn++; requestsThisTurn=0;
      record.answer=null; record.final_complete=false;
      const before = session.messages.length, turnStarted=performance.now();
      const timer=setTimeout(() => { void session.abort(); }, request.turn_timeout_s*1000);
      try { await session.prompt(prompt, {expandPromptTemplates:false}); }
      finally { clearTimeout(timer); }
      const fresh = session.messages.slice(before).filter(m => m.role === 'assistant');
      const last = fresh.at(-1);
      record.turns.push({prompt, prompt_sha256:sha(prompt),
        prompt_characters_js_utf16:prompt.length, prompt_code_points:[...prompt].length,
        answer:session.getLastAssistantText() ?? null,
        latency_s:(performance.now()-turnStarted)/1000,
        fresh_assistant_messages:fresh.map(publicMessage), requests:requestsThisTurn});
      if (!last || ['error','aborted'].includes(last.stopReason))
        throw new Error(last?.errorMessage ?? 'Pi returned no complete assistant response');
      record.answer = session.getLastAssistantText() ?? null;
      record.final_complete = last.stopReason === 'stop' && !(last.content ?? []).some(b => b.type === 'toolCall');
    }
    const deliveryErrors=record.trace.filter(e => e.kind === 'mcp_delivery_error');
    if (deliveryErrors.length) {
      record.runtime_status='runtime_error';
      record.error={type:'MCPDeliveryError', message:'MCP protocol/transport did not deliver a validated result',
        deliveries:deliveryErrors};
    } else record.runtime_status='completed';
  } catch (error) {
    record.error = {type:error.name, message:error.message};
    record.runtime_status='runtime_error';
  } finally {
    if (agentStarted !== null) record.latency.agent_s=(performance.now()-agentStarted)/1000;
    const cleanupStarted=performance.now();
    if (session) {
      record.session_id=session.sessionId;
      record.session_file=session.sessionFile ?? null;
      if (record.session_file) {
        try { record.session_sha256=sha(await fs.readFile(record.session_file)); }
        catch { record.session_sha256=null; }
      }
      session.dispose();
    }
    try { await adapter?.close(); await worker?.close(); }
    catch (error) { record.cleanup_error={type:error.name,message:error.message}; record.runtime_status='runtime_error'; }
    record.latency.cleanup_s=(performance.now()-cleanupStarted)/1000;
    record.latency.bridge_total_s=(performance.now()-started)/1000;
    const assistant=record.trace.filter(e => e.kind === 'assistant').map(e => e.message);
    record.usage_per_new_response=assistant.map(m => ({
      response_id:m.responseId ?? null, stop_reason:m.stopReason,
      raw_sdk_usage:m.usage ?? null,
      note:'Pi normalizes missing provider fields to zero. Raw SDK values are not proof of provider-reported usage.',
    }));
    const usages=assistant.map(m => m.usage);
    // Never turn missing/failed partial usage into zero or include restored old turns.
    if (assistant.length && usages.every((u,i) => u?.totalTokens > 0 && !['error','aborted'].includes(assistant[i].stopReason))) {
      const sum = key => usages.every(u => typeof u[key] === 'number') ? usages.reduce((a,u) => a+u[key],0) : null;
      record.usage={requests:assistant.length, input_tokens:sum('input'), output_tokens:sum('output'),
        cache_read_tokens:sum('cacheRead'), cache_write_tokens:sum('cacheWrite'), total_tokens:sum('totalTokens'),
        reasoning_tokens:sum('reasoning'), scope:'SDK-normalized usage of new responses only; input excludes cache tokens'};
    }
  }
  return record;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // Reserve stdout for structured IPC; library diagnostics go to stderr.
  console.log=(...args) => console.error(...args);
  try {
    let input=''; for await (const chunk of process.stdin) input+=chunk;
    const result=await execute(JSON.parse(input), {
      emit:item => process.stdout.write(JSON.stringify(item)+'\n'),
    });
    process.stdout.write(JSON.stringify({kind:'result', result})+'\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({kind:'fatal', error:{type:error.name,message:error.message}})+'\n');
    process.exitCode=1;
  }
}
