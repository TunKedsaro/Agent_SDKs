"""Offline checks only: python -m unittest discover -s pi_agent -p 'test_benchmark.py'."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import nbformat
import benchmark_runtime as runtime
import benchmark_tools as business

ROOT=Path(__file__).resolve().parents[1]
NOTEBOOK=ROOT/'pi_agent/pi_agents_benchmark.ipynb'
SOURCE=ROOT/'deep_agent/2610051200_deepagents_benchmark.ipynb'

def source_assignment(nb,index,name):
    for node in ast.walk(ast.parse(''.join(nb['cells'][index]['source']))):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            return node
    raise KeyError(name)

class NotebookParity(unittest.TestCase):
    def test_schema_prompts_fixtures_and_case_inventory(self):
        nb=nbformat.read(NOTEBOOK,as_version=4);nbformat.validate(nb)
        original=json.loads(SOURCE.read_text())
        self.assertEqual(nb.metadata.benchmark.source_sha256,hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        headings=[c.source for c in nb.cells if c.cell_type=='markdown' and c.source.startswith('# ') and c.source[2:3].isdigit()]
        self.assertEqual(headings,['# 0.Config','# 1.Create Pi Agent','# 2.Mathematic calculation',
            '# 3.MCP','# 4.Web search','# 5.Planning','# 6.Long context','# 7.Session history',
            '# 8.Error handling','# 9.สรุปและสำรวจผล Benchmark'])
        cases=[];assignments={};env={'deepcopy':deepcopy}
        for index,cell in enumerate(nb.cells):
            if cell.cell_type!='code':continue
            self.assertEqual(cell.outputs,[]);self.assertIsNone(cell.execution_count)
            compile(cell.source,f'cell-{index}','exec')
            tree=ast.parse(cell.source)
            for node in ast.walk(tree):
                if isinstance(node,ast.Assign):
                    for target in node.targets:
                        if isinstance(target,ast.Name):assignments[target.id]=node
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='begin_run':
                    cases.append(ast.literal_eval(node.args[1]))
            if cell.metadata.get('tags')==['fixture-or-prompt'] or (cell.metadata.get('tags')==['fixture'] and cell.metadata.get('deepagents_source_cells')):
                with redirect_stdout(io.StringIO()):exec(cell.source,env)
        self.assertEqual(len(cases),18)
        self.assertEqual(len(set(cases)),18)
        self.assertEqual(set(cases),{'smoke',*[f'math_{i:03d}' for i in (1,2,3)],
            *[f'mcp_{i:03d}' for i in (1,2,3)],*[f'web_native_{i:03d}' for i in (1,2)],
            *[f'planning_{i:03d}' for i in (1,2)],*[f'long_context_{i:03d}' for i in (1,2)],
            *[f'history_{i:03d}' for i in (1,2,3)],*[f'error_{i:03d}' for i in (1,2)]})
        for copied in nb.metadata.benchmark.copied_assignments:
            expected=source_assignment(original,copied.source_cell,copied.variable)
            self.assertEqual(ast.dump(expected),ast.dump(assignments[copied.variable]),copied.variable)
        for cell in nb.cells:
            if cell.cell_type=='code' and cell.metadata.get('tags')==['instructions']:
                i=cell.metadata.deepagents_source_cells[0]
                expected=next(n.value for n in ast.walk(ast.parse(''.join(original['cells'][i]['source'])))
                    if isinstance(n,ast.keyword) and n.arg=='system_prompt')
                actual=ast.parse(cell.source).body[0].value
                self.assertEqual(ast.dump(expected),ast.dump(actual))
        source_env={'deepcopy':deepcopy}
        with redirect_stdout(io.StringIO()):
            exec(''.join(original['cells'][103]['source']),source_env)
            exec(ast.unparse(source_assignment(original,104,'long_prompt')),source_env)
            long002=''.join(original['cells'][109]['source'])
            exec(long002[long002.index('remaining = long_documents'):],source_env)
        for name in ('long_documents','long_prompt','long_002_documents','long_002_prompt'):
            self.assertEqual(env[name].encode('utf-8'),source_env[name].encode('utf-8'),name)
        self.assertEqual(runtime.digest(env['long_prompt']),'3e628fe8b42d626292c855a9c91f3e9bd3a910a4e2d44580d482cbf0199021ae')
        print('Long prompt parity:',[(name,len(env[name]),runtime.digest(env[name])) for name in ('long_prompt','long_002_prompt')])
        self.assertEqual(list(env['BACKUP_SOURCES']),['fixture_echo_backup_2024','fixture_echo_backup_2025'])

    def test_original_business_implementations(self):
        original=json.loads(SOURCE.read_text())
        actual_tree=ast.parse((ROOT/'pi_agent/benchmark_tools.py').read_text())
        functions={n.name:n for n in actual_tree.body if isinstance(n,ast.FunctionDef)}
        for i,names in [(16,['calculator']),(42,['search_company','get_financial_statement']),
            (55,['build_retry_mcp']),(84,['list_companies','read_company_financials','calculate_percentage']),
            (90,['read_primary_financials','find_financial_sources','read_financial_source']),
            (142,['fetch_restricted_statement']),(147,['fetch_temporarily_unavailable_statement'])]:
            for node in ast.parse(''.join(original['cells'][i]['source'])).body:
                if isinstance(node,ast.FunctionDef) and node.name in names:
                    node.decorator_list=[]
                    self.assertEqual(ast.dump(node),ast.dump(functions[node.name]),node.name)
        self.assertEqual(business.calculator('subtract',4,7),-3)
        with self.assertRaisesRegex(ValueError,'Cannot divide by zero'):business.calculator('divide',1,0)
        self.assertEqual(business.calculate_percentage(1,0,'ratio'),{'value_pct':None,'error':'ZERO_DENOMINATOR'})
        self.assertEqual(business.fetch_restricted_statement('x',1)['error']['retryable'],False)

class RuntimeChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PI_BENCHMARK_SHARED':self.temp.name});self.env.start()
        self.cfg=runtime.config()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def run_record(self,case='math_001'):
        run=runtime.begin_run(self.cfg,case,'synthetic prompt','synthetic instructions',category='test')
        run.update(runtime_status='completed',answer='{}',final_complete=True,trace=[],active_tools=[])
        return run
    def test_types_tolerance_review_and_exports(self):
        self.assertTrue(runtime.matches({'x':12.009},{'x':12},.01))
        self.assertFalse(runtime.matches({'x':12.02},{'x':12},.01))
        for bad in (True,float('nan'),float('inf')):self.assertFalse(runtime.matches(bad,1))
        self.assertFalse(runtime.matches({'year':2025.0},{'year':2025}))
        run=self.run_record('synthetic_review')
        runtime.score(run,{},review_criteria=('quality','sources'))
        self.assertEqual(run['evaluation_status'],'pending_review')
        runtime.apply_review(run,{'quality':True},'Synthetic test evidence','unit-test')
        runtime.apply_review(run,{'sources':True},'Synthetic test evidence','unit-test')
        self.assertEqual(run['evaluation_status'],'passed')
        run['answer']='changed'
        runtime.evaluate(run)
        self.assertEqual(run['evaluation_status'],'pending_review')
        self.assertTrue(run['review_stale'])
        first=runtime.export_results('test',[run]);second=runtime.export_results('test',[run])
        self.assertNotEqual(first,second)
        rows,_=runtime.summarize();self.assertEqual(rows[0]['unique_runs'],1)
        another=deepcopy(run);another['case_version']='2.0'
        runtime.export_results('test',[another])
        rows,_=runtime.summarize();self.assertEqual(len(rows),2)
        self.assertEqual(json.loads(first.read_text())['sdk'],'pi_agent')
    def test_failure_and_unsupported_are_not_false_scores(self):
        run=self.run_record();run.update(runtime_status='runtime_error',error={'message':'synthetic'})
        runtime.score(run,{})
        self.assertIsNone(run['task_passed']);self.assertEqual(run['evaluation_status'],'runtime_error')
        web=self.run_record('web_native_002');runtime.unsupported_web(web,{'reason':'offline-test'})
        self.assertIsNone(web['task_passed']);self.assertIsNone(web['tool_call_count'])
        self.assertEqual(web['evaluation_status'],'unsupported')
        runtime.export_results('web',[web]);rows,_=runtime.summarize()
        self.assertEqual(rows[0]['unsupported'],1)
    def test_parallel_retry_is_not_sequential_retry(self):
        run=self.run_record('error_002');run['active_tools']=['fetch_temporarily_unavailable_statement']
        name=run['active_tools'][0];args={'company_name':'Aurora','year':2025}
        run['trace']=[{'kind':'tool_call','id':str(i),'name':name,'arguments':args,'sequence':i} for i in (0,1)]+[
            {'kind':'tool_result','id':str(i),'name':name,'is_error':False,'output':business.fetch_temporarily_unavailable_statement(**args),'sequence':i+2} for i in (0,1)]
        runtime.score(run)
        self.assertFalse(run['checks']['retry_after_first_result']);self.assertFalse(run['task_passed'])
    def test_timeout_retains_events_and_kills_process_group(self):
        # Executable fake Node only tests IPC supervision, never produces benchmark scores.
        executable=Path(self.temp.name)/'fake-node'
        pid_file=Path(self.temp.name)/'child.pid'
        executable.write_text('#!'+sys.executable+'\n'+
            'import subprocess,sys,json,time,pathlib\n'+
            'request=json.load(sys.stdin)\n'+
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'+
            f'pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n'+
            'print(json.dumps({"kind":"event","event":{"kind":"synthetic","text":"ภาษาไทย 🐘"}},ensure_ascii=False),flush=True)\n'+
            'time.sleep(60)\n')
        executable.chmod(0o755)
        result=runtime.invoke_bridge({'node':str(executable),'api_key_env':'PI_NO_KEY'},.5)
        self.assertEqual(result['runtime_status'],'runtime_error')
        self.assertEqual(result['error']['type'],'TimeoutError')
        self.assertEqual(result['trace'][0]['text'],'ภาษาไทย 🐘')
        pid=int(pid_file.read_text())
        if Path('/proc').is_dir():
            stat=Path(f'/proc/{pid}/stat')
            self.assertTrue(not stat.exists() or stat.read_text().split(') ',1)[1].startswith('Z'))
        else:
            check=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True)
            self.assertTrue(not check.stdout.strip() or check.stdout.strip().startswith('Z'),check.stdout)
    def test_restart_barrier_without_agent_request(self):
        seed=self.run_record('history_003')
        seed.update(answer='CONTEXT_SAVED',session_file='unused.jsonl')
        identity={'pid':os.getpid(),'instance':'one'}
        manifest=runtime.save_restart_manifest(seed,identity)
        with patch.object(runtime,'invoke_bridge',side_effect=AssertionError('must not call agent')):
            resumed=runtime.resume_from_manifest(manifest,self.cfg,identity,'recall only')
        self.assertEqual(resumed['runtime_status'],'runtime_error')
        self.assertIn('Restart Python kernel',resumed['error']['message'])
        self.assertIsNone(resumed['task_passed'])
    def test_rerun_creates_new_identity_and_drops_old_review(self):
        run=runtime.begin_run(self.cfg,'synthetic','prompt','instructions',category='test')
        first_id=run['run_id']
        result={'runtime_status':'completed','answer':'synthetic','trace':[],
                'session_file':'output-session.jsonl','session_sha256':'output-hash'}
        with patch.object(runtime,'invoke_bridge',return_value=result) as bridge:
            runtime.run_once(run)
            run['review']={'checks':{'quality':True}}
            runtime.run_once(run)
        self.assertNotEqual(first_id,run['run_id'])
        self.assertIsNone(run['review'])
        self.assertNotIn('session_file',bridge.call_args.args[0])
    def test_planning_requires_an_earlier_assistant_message(self):
        run=self.run_record('planning_001');run['active_tools']=['write_todos','list_companies']
        run['trace']=[
            {'kind':'tool_call','id':'p','name':'write_todos','arguments':{'todos':[{'content':'Synthetic','status':'pending'}]},'sequence':1,'assistant_sequence':0},
            {'kind':'tool_call','id':'d','name':'list_companies','arguments':{},'sequence':2,'assistant_sequence':0},
            {'kind':'tool_result','id':'p','sequence':3,'output':'Updated','is_error':False},
            {'kind':'tool_result','id':'d','sequence':4,'output':[],'is_error':False}]
        runtime.score(run)
        self.assertFalse(run['checks']['plan_before_data'])
    def test_incomplete_trace_is_not_zero_or_automatic_failure(self):
        run=self.run_record('synthetic');run['active_tools']=['calculator']
        run['trace']=[{'kind':'tool_call','id':'missing','name':'calculator','arguments':{},'sequence':0}]
        runtime.score(run,{})
        self.assertIsNone(run['tool_call_count'])
        self.assertIsNone(run['task_passed'])
        self.assertEqual(run['evaluation_status'],'pending_review')

if __name__=='__main__':unittest.main()
