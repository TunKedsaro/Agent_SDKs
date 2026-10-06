"""Notebook transport, evidence scoring and append-only export; no agent loop."""
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from importlib.metadata import version, PackageNotFoundError

SDK = 'pi_agent'
HERE = Path(__file__).resolve().parent
WEB_REVIEW = ['search_observed', 'microsoft_open_attempt', 'alphabet_open_attempt',
              'open_success', 'content_supports_answer', 'citations_correct']

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def shared_root():
    # Never create pi_agent/shared when the repository/container shared mount is absent.
    root = Path(os.getenv('PI_BENCHMARK_SHARED', str(HERE.parent/'shared'))).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f'Shared directory is missing: {root}')
    return root

def config():
    spec = os.getenv('BENCHMARK_MODEL', 'openai:gpt-6-luna')
    if ':' not in spec:
        raise ValueError('BENCHMARK_MODEL must be provider:model; no implicit provider substitution')
    provider, model = spec.split(':', 1)
    if not provider or not model:
        raise ValueError('Empty provider/model')
    key_env = os.getenv('PI_BENCHMARK_API_KEY_ENV', 'OPENAI_API_KEY' if provider == 'openai' else '')
    if not key_env:
        raise ValueError('Set PI_BENCHMARK_API_KEY_ENV for this provider')
    python_versions={'python':sys.version.split()[0]}
    for package in ('fastmcp','mcp','ipykernel'):
        try: python_versions[package]=version(package)
        except PackageNotFoundError: python_versions[package]=None
    return {'provider':provider, 'model':model, 'api_key_env':key_env,
            'python_versions':python_versions,
            'thinking_level':os.getenv('PI_BENCHMARK_THINKING', 'medium'),
            'python':sys.executable, 'node':os.getenv('PI_BENCHMARK_NODE', 'node')}

def invoke_bridge(request, timeout_s):
    started = time.perf_counter()
    child = subprocess.Popen([request['node'], '--import', 'tsx', str(HERE/'benchmark_bridge.mjs')],
        cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = child.communicate(json.dumps(request, ensure_ascii=False, allow_nan=False), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Kill the process group, including Python business worker or MCP subprocess.
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        stdout, stderr = child.communicate()
    elapsed = time.perf_counter()-started
    events, result, protocol_errors = [], None, []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
            if item['kind'] == 'event': events.append(item['event'])
            elif item['kind'] == 'result': result = item['result']
            elif item['kind'] == 'fatal': protocol_errors.append(item['error'])
            else: protocol_errors.append({'message':'Unexpected IPC message kind'})
        except (ValueError, KeyError):
            protocol_errors.append({'message':'Invalid JSON IPC output', 'line':line[:500]})
    if result is None:
        result = {'runtime_status':'runtime_error', 'error':None, 'answer':None,
                  'trace':events, 'usage':None, 'latency':{}}
    if timed_out or child.returncode != 0 or protocol_errors:
        result.update(runtime_status='runtime_error', error={
            'type':'TimeoutError' if timed_out else 'BridgeError',
            'message':f'Bridge timeout after {timeout_s}s' if timed_out else f'Bridge exited {child.returncode}',
            'protocol_errors':protocol_errors})
    key = os.getenv(request.get('api_key_env', ''))
    result['bridge_stderr'] = stderr.replace(key, '[REDACTED]') if key else stderr
    result.setdefault('latency', {})['python_wall_s'] = elapsed
    result['latency']['scope'] = 'python_wall includes Node startup/imports, setup, all new prompts, tools, persistence and cleanup; bridge_total begins after imports'
    # Startup cannot be isolated accurately by subtracting clocks in two runtimes.
    result['latency']['node_startup_s'] = None
    return result

def begin_run(cfg, case_id, prompt, instructions, *, category, tools=None, fixtures=None,
              case_version=None, fixture_version=None, timeout_s=120,
              source_recursion_limit=None, max_model_requests_per_turn=40, prompts=None):
    run_id = uuid4().hex
    return {
        'sdk':SDK, 'case_id':case_id, 'case_version':case_version, 'fixture_version':fixture_version,
        'category':category, 'run_id':run_id, 'started_at_utc':utc_now(),
        'model':cfg['model'], 'provider':cfg['provider'], 'requested_thinking_level':cfg['thinking_level'],
        'prompt':prompt, 'prompts':prompts or [prompt], 'instructions':instructions,
        'prompt_sha256':digest(prompt), 'prompt_characters_python':len(prompt),
        'prompt_utf8_bytes':len(prompt.encode('utf-8')),
        'tools':deepcopy(tools or []), 'fixtures':deepcopy(fixtures or {}),
        'source_recursion_limit':source_recursion_limit,
        'max_model_requests_per_turn':max_model_requests_per_turn, 'turn_timeout_s':timeout_s,
        'runtime_status':'not_run', 'evaluation_status':'pending_review', 'task_passed':None,
        'versions':deepcopy(cfg.get('versions')), 'tool_call_count':None,
        'python_versions':deepcopy(cfg.get('python_versions')),
        'citations':None, 'hosted_events':None, 'usage':None, 'error':None,
        'latency':{'setup_s':None,'agent_s':None,'cleanup_s':None,'node_startup_s':None,
                   'python_wall_s':None,'scope':'Not executed'},
        'checks':{}, 'review':None, 'record_revision':0,
        'implementation_differences':[
            'Pi coding-agent harness; Python/Node bridge does not implement the agent loop.',
            'Only explicit business tools are active; no coding tools, skills or config discovery.',
            'Custom authored instructions retained; Pi appends its cwd section (effective text captured).',
            'Business functions use original Python implementations through JSON IPC; IPC time is included.',
            'Source recursion_limit and Pi provider-request budget have different semantics.',
            'Pi thinking level is explicit and recorded; source provider reasoning default was implicit.',
        ],
        '_config':deepcopy(cfg),
    }

def request_for(run):
    cfg = run['_config']
    request = {**cfg, **{k:run[k] for k in (
        'prompts','instructions','tools','fixtures','turn_timeout_s',
        'source_recursion_limit','max_model_requests_per_turn')},
        'run_dir':str(shared_root()/'state'/SDK/run['run_id'])}
    for key in ('mcp','persistence','session_file','session_sha256','previous_node_pid','expected_versions'):
        if key in run: request[key] = run[key]
    return request

def run_once(run):
    # Re-executing a run cell is a NEW experiment, never a duplicate export.
    # Preserve the original input session reference rather than reopening this
    # run's newly created output session. A stale reference fails hash validation.
    if run.get('_executed'):
        original=deepcopy(run['_experiment_spec'])
        run.clear(); run.update(original)
        run['run_id']=uuid4().hex
        run['started_at_utc']=utc_now()
    run['_experiment_spec']=deepcopy({k:v for k,v in run.items() if k not in ('_experiment_spec','_executed')})
    run['_executed']=True
    run.update(checks={},review=None,task_passed=None,evaluation_status='pending_review')
    request = request_for(run)
    try:
        result = invoke_bridge(request, len(run['prompts'])*run['turn_timeout_s']+45)
    except Exception as exc:
        result = {'runtime_status':'runtime_error', 'error':{'type':type(exc).__name__, 'message':str(exc)},
                  'trace':[], 'answer':None, 'usage':None}
    # Requested model/provider are stable top-level fields, actual resolution is separate.
    run['resolved_model'] = result.pop('model', None)
    run.update(result)
    run['finished_at_utc'] = utc_now()
    evaluate(run)
    return run

def inspect_runtime(cfg):
    request = {**cfg, 'run_dir':str(shared_root()/'state'/SDK/('inspect-'+uuid4().hex)),
               'mode':'inspect', 'tools':[], 'fixtures':{}, 'instructions':'', 'prompts':[]}
    return invoke_bridge(request, 30)

def unsupported_web(run, capability):
    run.update(runtime_status='unsupported', error=None, answer=None, trace=None, usage=None,
               tool_call_count=None, citations=None, hosted_events=None,
               unsupported_evidence=deepcopy(capability), finished_at_utc=utc_now())
    run['checks'] = {key:None for key in WEB_REVIEW}
    evaluate(run)
    return run

def matches(actual, expected, tolerance=0.0, key=None):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual)==set(expected) and all(
            matches(actual[k], v, tolerance, k) for k,v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(
            matches(a,b,tolerance) for a,b in zip(actual,expected))
    if type(expected) in (int,float):
        return (type(actual) in (int,float) and math.isfinite(actual)
                and (key not in ('year','attempts') or type(actual) is int)
                and math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance))
    return type(actual) is type(expected) and actual==expected

def evidence_hash(run):
    return digest({k:run.get(k) for k in ('run_id','answer','trace','turns','restored_messages_before_model','unsupported_evidence')})

def evaluate(run):
    status = run['runtime_status']
    if status in ('runtime_error','unsupported'):
        run.update(evaluation_status=status, task_passed=None)
        return
    checks = dict(run.get('checks', {}))
    review = run.get('review')
    if review and review.get('run_id')==run['run_id'] and review.get('evidence_sha256')==evidence_hash(run):
        checks.update(review['checks'])
    elif review:
        run['review_stale'] = True
    values = list(checks.values())
    passed = (False if any(v is False for v in values) else
              True if values and all(v is True for v in values) and status=='completed' else None)
    run['task_passed'] = passed
    run['evaluation_status'] = 'passed' if passed is True else 'failed' if passed is False else 'pending_review'

def score(run, expected=None, *, tolerance=0.0, review_criteria=()):
    if run['runtime_status'] != 'completed':
        run['checks']={}; evaluate(run); return run
    trace = run.get('trace') or []
    calls = [e for e in trace if e['kind']=='tool_call']
    results = {e['id']:e for e in trace if e['kind']=='tool_result'}
    mcp_results = {e['id']:e for e in trace if e['kind']=='mcp_result'}
    complete_trace=all(c['id'] in results for c in calls)
    run['tool_call_count']=len(calls) if complete_trace else None
    run['observations']={'observed_tool_calls':len(calls), 'tool_execution_errors':[
        r for r in results.values() if r.get('is_error')]}
    allowed = set(run.get('active_tools', []))
    checks = {'final_answer_complete':bool(run.get('answer')) and run.get('final_complete') is True,
              'only_allowed_tools':all(c['name'] in allowed for c in calls),
              'all_calls_have_results':True if complete_trace else None}
    case = run['case_id']
    if expected is not None:
        try: parsed = json.loads(run.get('answer') or '')
        except ValueError: parsed = None
        run['parsed_answer']=parsed
        checks['expected_json_and_types']=(isinstance(parsed,dict) and parsed==expected
            if case.startswith('mcp_') or case=='history_001' else matches(parsed, expected, tolerance))
    if case.startswith('math_'):
        checks['calculator_used']=any(c['name']=='calculator' for c in calls)
    if case.startswith(('long_context_','history_')) or case=='smoke':
        checks['no_tool_calls']=not calls
    if case=='smoke': checks['fixed_token']=run['answer'].strip()=='DEEP_AGENTS_SDK_OK'
    if case.startswith('mcp_'):
        checks['discovery']=set(run.get('discovered_tools') or [])=={'search_company','get_financial_statement'}
        search=[c for c in calls if c['name']=='search_company']
        fetch=[c for c in calls if c['name']=='get_financial_statement']
        successful_search=[mcp_results.get(c['id'],{}) for c in search]
        if case=='mcp_002':
            checks['empty_search_and_no_fetch']=bool(search) and not fetch and any(
                r.get('output')=={'companies':[]} for r in successful_search)
        else:
            target={'company_id':run['fixtures']['STATEMENT']['company_id'],'year':2025}
            if case=='mcp_001':
                # Source cell 46 requires at least one successful dependent chain,
                # not that every earlier attempt also used the correct identifier.
                checks['resolved_successful_chain']=any(c['arguments']==target and
                    mcp_results.get(c['id'],{}).get('output')==run['fixtures']['STATEMENT'] and any(
                        r.get('sequence',float('inf'))<c['sequence'] and
                        r.get('output',{}).get('companies')==[run['fixtures']['COMPANY']]
                        for r in successful_search) for c in fetch)
            else:
                checks['resolved_before_fetch']=bool(fetch) and all(any(
                    r.get('sequence',float('inf')) < c['sequence'] and
                    r.get('output',{}).get('companies')==[run['fixtures']['COMPANY']]
                    for r in successful_search) for c in fetch)
                checks['fetch_arguments']=bool(fetch) and all(c['arguments']==target for c in fetch)
                checks['exactly_two_fetches']=len(fetch)==2
                first=mcp_results.get(fetch[0]['id'],{}) if fetch else {}
                second=mcp_results.get(fetch[1]['id'],{}) if len(fetch)>1 else {}
                checks['temporary_then_success']=(first.get('output')=={'error':{'code':'TEMPORARY_UNAVAILABLE','retryable':True}}
                    and second.get('output')==run['fixtures']['STATEMENT'])
                checks['retry_after_first_result']=len(fetch)==2 and first.get('sequence',float('inf'))<fetch[1]['sequence']
    if case.startswith('planning_'):
        def arguments(call):
            return call['arguments'] if isinstance(call.get('arguments'),dict) else {}
        plans=[c for c in calls if c['name']=='write_todos']
        data=[c for c in calls if c['name'] in ('list_companies','read_company_financials',
            'read_primary_financials','find_financial_sources','read_financial_source')]
        checks['plan_before_data']=bool(plans and data) and (
            plans[0].get('assistant_sequence',plans[0]['sequence'])<
            data[0].get('assistant_sequence',data[0]['sequence']))
        checks['progress_updated']=len({digest(p['arguments']) for p in plans})>1
        checks['calculation_tool_used']=any(c['name']=='calculate_percentage' for c in calls)
        todos=arguments(plans[-1]).get('todos',[]) if plans else []
        checks['last_todos_completed']=isinstance(todos,list) and bool(todos) and all(isinstance(t,dict) and t.get('status')=='completed' for t in todos)
        if case=='planning_002':
            failed=[c for c in calls if c['name']=='read_primary_financials' and arguments(c).get('company_id')=='co-e42']
            alternatives=[c for c in calls if c['name'] in ('find_financial_sources','read_financial_source')]
            checks['nonretryable_primary_not_repeated']=len(failed)==1
            error_sequence=results.get(failed[0]['id'],{}).get('sequence',float('inf')) if failed else float('inf')
            checks['plan_revised_before_alternative']=bool(alternatives) and any(error_sequence<p['sequence']<alternatives[0]['sequence'] for p in plans)
            checks['correct_backup_read']=any(c['name']=='read_financial_source' and c['arguments']=={'source_id':'fixture_echo_backup_2025'} for c in calls)
    if case.startswith('error_'):
        required = 1 if case=='error_001' else 2
        code = 'ACCESS_DENIED' if required==1 else 'TEMPORARY_UNAVAILABLE'
        checks['business_attempt_count']=len(calls)==required
        checks['same_requested_arguments']=bool(calls) and all(c['arguments']=={'company_name':'Aurora','year':2025} for c in calls)
        checks['expected_errors_observed']=len(calls)==required and all(
            isinstance(results.get(c['id'],{}).get('output'),dict) and
            results[c['id']]['output'].get('error',{}).get('code')==code and
            results[c['id']]['output'].get('error',{}).get('retryable') is (required==2) for c in calls)
        if required==2:
            checks['retry_after_first_result']=len(calls)==2 and results.get(calls[0]['id'],{}).get('sequence',float('inf'))<calls[1]['sequence']
    if case=='history_001':
        checks['seed_acknowledged']=bool(run.get('turns')) and (run['turns'][0].get('answer') or '').strip()=='CONTEXT_SAVED'
    if case=='history_002':
        checks['correction_acknowledged']=bool(run.get('turns')) and (run['turns'][0].get('answer') or '').strip()=='CONTEXT_UPDATED'
        checks['history_restored_before_call']=run.get('restored_message_count',0)>0
    if case=='history_003':
        checks['new_python_kernel']=run.get('restart_verified') is True
        checks['new_node_process']=run.get('node_pid')!=run.get('previous_node_pid')
        checks['history_restored_before_call']=run.get('restored_message_count',0)>0
        checks['seed_acknowledged']=run.get('seed_acknowledged') is True
    checks.update({criterion:None for criterion in review_criteria})
    run['checks']=checks
    run['evidence_sha256']=evidence_hash(run)
    evaluate(run)
    return run

def apply_review(run, checks, notes, reviewer):
    pending={k for k,v in run['checks'].items() if v is None}
    if not checks or not set(checks)<=pending or not all(type(v) is bool for v in checks.values()):
        raise ValueError('Review must supply booleans for this run’s pending criteria only')
    if not notes.strip() or not reviewer.strip(): raise ValueError('Reviewer and evidence notes are required')
    previous=run.get('review')
    if previous and previous.get('evidence_sha256')==evidence_hash(run):
        checks={**previous['checks'], **checks}
        run.setdefault('review_history', []).append(deepcopy(previous))
    run['review']={'run_id':run['run_id'], 'evidence_sha256':evidence_hash(run),
                   'checks':checks,'notes':notes,'reviewer':reviewer,'reviewed_at_utc':utc_now()}
    run['record_revision']+=1
    evaluate(run)

def show(run):
    print(run.get('answer') or '(ไม่มีคำตอบ)')
    print(json.dumps({k:run.get(k) for k in ('case_id','run_id','runtime_status','evaluation_status',
        'task_passed','checks','error','usage','latency')}, ensure_ascii=False, indent=2))

def clean_record(run):
    return {k:deepcopy(v) for k,v in run.items() if not k.startswith('_')}

def write_unique(directory, prefix, payload):
    directory.mkdir(parents=True, exist_ok=True)
    filename=f'{prefix}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}_{uuid4().hex}.json'
    destination=directory/filename
    with destination.open('x',encoding='utf-8') as f:
        json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False)
    return destination

def export_results(category, runs):
    for run in runs: evaluate(run)
    return write_unique(shared_root()/'results'/SDK, category, {
        'schema_version':'1.0', 'saved_at_utc':utc_now(), 'sdk':SDK,
        'category':category, 'results':[clean_record(r) for r in runs]})

def save_restart_manifest(run, kernel_identity):
    if run['runtime_status']!='completed' or not run.get('session_file'):
        raise RuntimeError('Seed did not complete; its runtime_error can still be exported')
    run['seed_acknowledged']=(run.get('answer') or '').strip()=='CONTEXT_SAVED'
    run['runtime_status']='seeded'
    run['checks']={}; evaluate(run)
    manifest={'sdk':SDK,'case_id':'history_003','run_id':run['run_id'], 'seed_kernel':kernel_identity,
              'record':clean_record(run)}
    return write_unique(shared_root()/'state'/SDK/run['run_id'], 'restart-manifest', manifest)

def resume_from_manifest(manifest_path, cfg, kernel_identity, resume_prompt):
    manifest_path=Path(manifest_path).resolve()
    base=(shared_root()/'state'/SDK).resolve()
    if not manifest_path.is_relative_to(base): raise ValueError('Manifest is outside Pi state directory')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['sdk']!=SDK or manifest['case_id']!='history_003': raise ValueError('Wrong manifest')
    run=manifest['record']
    run['_config']=deepcopy(cfg)
    # Preparation errors are recorded and exportable, never converted into a pass.
    try:
        if manifest['seed_kernel']['pid']==kernel_identity['pid']:
            raise RuntimeError('Restart Python kernel before resuming (PID must change)')
        if manifest['seed_kernel']['instance']==kernel_identity['instance']:
            raise RuntimeError('Kernel identity did not change')
        if (run['model'],run['provider'],run['requested_thinking_level'])!=(cfg['model'],cfg['provider'],cfg['thinking_level']):
            raise RuntimeError('Model/provider/thinking setting changed since seed')
        if run.get('python_versions')!=cfg.get('python_versions'):
            raise RuntimeError('Python runtime/package versions changed since seed')
        marker=manifest_path.parent/'resume-started.json'
        with marker.open('x',encoding='utf-8') as f: json.dump({'kernel':kernel_identity,'at':utc_now()},f)
        run['seed_execution']={k:deepcopy(run.get(k)) for k in ('answer','trace','turns','usage','latency','node_pid')}
        run['previous_node_pid']=run['node_pid']
        run['expected_versions']=run['versions']
        run['prompts']=[resume_prompt]; run['prompt']=resume_prompt
        run['prompt_sha256']=digest(resume_prompt)
        run['prompt_characters_python']=len(resume_prompt)
        run['prompt_utf8_bytes']=len(resume_prompt.encode('utf-8'))
        run['restart_verified']=True
        run['record_revision']+=1
        run_once(run)
    except Exception as exc:
        run.update(runtime_status='runtime_error',error={'type':type(exc).__name__,'message':str(exc)})
        evaluate(run)
    return run

def summarize():
    newest={}
    for filename in sorted((shared_root()/'results'/SDK).glob('*.json')):
        envelope=json.loads(filename.read_text(encoding='utf-8'))
        if envelope.get('sdk')!=SDK: continue
        for run in envelope.get('results',[]):
            if run.get('sdk')!=SDK or not run.get('run_id'): continue
            key=(run['case_id'],run.get('case_version'),run['run_id'])
            order=(run.get('record_revision',0),envelope['saved_at_utc'])
            if key not in newest or order>newest[key][0]: newest[key]=(order,run)
    groups=defaultdict(list)
    for _,run in newest.values():
        evaluate(run)
        groups[(run['case_id'],run.get('case_version'))].append(run)
    rows=[]
    for (case,version),runs in sorted(groups.items(),key=lambda pair:str(pair[0])):
        counts=Counter(r['evaluation_status'] for r in runs)
        rows.append({'case_id':case,'case_version':version,'unique_runs':len(runs),
            **{state:counts[state] for state in ('passed','failed','pending_review','runtime_error','unsupported')},
            'pass_rate_evaluated_only':counts['passed']/(counts['passed']+counts['failed']) if counts['passed']+counts['failed'] else None})
    payload={'schema_version':'1.0','saved_at_utc':utc_now(),'sdk':SDK,'category':'summary','results':rows,
             'note':'Versions are separate; duplicate exports deduplicated by run_id. Unsupported/pending/runtime_error are not counted as failed answers.'}
    return rows,write_unique(shared_root()/'summaries',SDK,payload)
