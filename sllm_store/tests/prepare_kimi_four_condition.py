"""Prepare four-condition candidates only; no service, cache or model execution."""
import argparse
import ast
import copy
import datetime
import hashlib
import inspect
import math
import json
import pathlib
import re
import subprocess

import prepare_native_stop_diagnostic as base

PROTOCOL = base.EVIDENCE / "kimi-four-condition-10block-protocol-candidate-v1-20261106.json"
PROTOCOL_SHA = "4a1ec5e26789486abcc9cd5d3ef9c79273723f3a320b8796a77fb2b5e0cdd068"
INVENTORY = base.EVIDENCE / "kimi-four-condition-candidate-input-file-inventory-20261106.json"
INVENTORY_SHA = "3940a24be49a9de6e533a64e46e6cc17bca0609ed18a1f997fbfc51faf662eae"
SOURCE = "70b23a3af65fae77b4b762942cf4a682a7c474f6"
SF_HOST = "/data/brick1/boyjiang/kimi-k2.6/checkpoint/7eb5002f6aadc958aed6a9177b7ed26bb94011bb"
STORE_ARCHIVE = "/data/brick1/boyjiang/kimi-k2.6/store-conversion-20261105-once"
RUNS_ROOT = "/data/brick1/boyjiang/kimi-k2.6/performance-runs"
DIAGNOSTIC_OVERLAY_SHA = '86643c1b628f293873fb00a8071b1feab59c4112b87ae43875fe8ca9692b1f25'
DIAGNOSTIC_MANIFEST = base.ROOT/'sllm_store/build/kimi-vllm0191-diagnostic-values-v1/build-manifest.json'
DIAGNOSTIC_MANIFEST_SHA = '95b872094858c38438b31a7ed10a8c56474a6faba3c8bee63d00e060096dde21'
DIAGNOSTIC_PATCH_SHA = '6fc825b0f3069a73d6ea7094ed134c56f020f501b2acd2fcd4bea2f5271051db'
CONDITIONS = ("safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit")


def checked(path, sha):
    return base.checked_json(path, sha)


def validate_protocol(protocol):
    assert protocol["audited_events_source_commit"] == SOURCE
    assert tuple(protocol["conditions"]) == CONDITIONS
    assert protocol["design"]["blocks"] == 10
    attempts = protocol["attempts"]
    assert len(attempts) == 40 and len({a["attempt_id"] for a in attempts}) == 40
    for block, order in enumerate(protocol["design"]["realized_condition_orders"], 1):
        rows = [a for a in attempts if a["block"] == block]
        assert [a["condition"] for a in rows] == order and set(order) == set(CONDITIONS)
        assert [a["position"] for a in rows] == [1, 2, 3, 4]
    assert [a["order"] for a in attempts] == list(range(1, 41))
    requests = protocol["correctness"]["requests"]
    assert [r["case"] for r in requests] == ["arithmetic", "translation", "fixed32"]
    for row, maximum, eos in zip(requests, (128, 128, 32), (False, False, True)):
        assert row["seed"] == 20261024 and row["max_tokens"] == maximum and row["ignore_eos"] is eos
        assert row["n"] == 1 and row["temperature"] == 0 and row["top_p"] == 1
        assert row["top_k"] == 0 and row["min_p"] == 0
        assert len(row["input_tokens"]) == row["expected"]["prompt_tokens"]
        assert len(row["expected"]["token_ids"]) == row["expected"]["completion_tokens"]
    execution_mapping(protocol)


def execution_mapping(protocol):
    attempts=protocol['attempts']
    runs={row['attempt_id']:row['attempt_id'] for row in attempts}
    blocks={str(number):'kimi-four-v1-b%02d'%number for number in range(1,11)}
    revision=protocol.get('execution_revision')
    if revision is None:
        return runs,blocks
    datetime.date.fromisoformat(revision['authorization_date'])
    assert revision['user_instruction']=='修复完重新跑b01 然后是2~10'
    assert isinstance(revision['reason'],str) and revision['reason'].strip()
    assert revision['rerun_blocks']==[1,2] and revision['suffix']=='retry02'
    assert revision['actual_launch_requests_at_authorization']==2 and revision['target_total_launch_requests_max']==42
    assert revision['stop_on_new_failure'] is True
    assert revision['selection_rule']=='fixed_40_mapped_runs_no_latency_selection'
    expected={row['attempt_id']:row['attempt_id']+('-retry02' if row['block'] in (1,2) else '') for row in attempts}
    expected_blocks={number:name+('-retry02' if number in ('1','2') else '') for number,name in blocks.items()}
    assert revision['run_id_map']==expected,'only explicitly authorized b01/b02 retry02 runtime IDs permitted'
    assert len(set(expected.values()))==40 and revision['block_run_ids']==expected_blocks
    historical=revision['historical_attempts']
    old_first=[row for row in attempts if row['block'] in (1,2) and row['position']==1]
    assert len(historical)==2
    for row,old in zip(historical,old_first):
        assert row['run_id']==row['logical_attempt_id']==old['attempt_id'] and row['block']==old['block']
        assert row['launch_requested'] is True and row['primary_sample_qualified'] is False
        assert isinstance(row['status'],str) and 'FAIL' in row['status']
        assert pathlib.Path(row['result_path']).is_absolute() and re.fullmatch('[0-9a-f]{64}',row['result_sha256'])
    assert revision['historical_unstarted']==[row['attempt_id'] for row in attempts if row['block'] in (1,2) and row['position']>1]
    return expected,expected_blocks


def validate_inventory(inventory, expected_roots=None):
    assert set(inventory['roots']) == {'safetensors','store'}
    if expected_roots is not None:
        assert {name:group['root'] for name,group in inventory['roots'].items()}==expected_roots,'cache inventory does not match mounted model assets'
    for group in inventory['roots'].values():
        assert group['files'] and len({r['path'] for r in group['files']}) == len(group['files'])
        root=pathlib.Path(group['root']).resolve()
        for row in group['files']:
            path=pathlib.Path(row['path']);v=path.stat()
            assert path.is_file() and path.resolve().is_relative_to(root)
            assert str(path.resolve()) == row['resolved']
            assert (v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_uid) == (row['dev'],row['ino'],row['bytes'],row['mtime_ns'],row['uid'])

def replace_function(body, name, replacement):
    node = next(n for n in ast.parse(body).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return body.replace(ast.get_source_segment(body, node), replacement, 1)


STORAGE_ROUTE_CODE = """
storage_route_proof={}
if s['storage_route']['route']=='local_nvme':
 for name,path in {'safetensors':s['safetensors_host'],'store_archive':s['archive'],'runs_root':s['runs_root']}.items():
  target=pathlib.Path(path);assert str(target.resolve(strict=True))==path and target.is_dir()
  row=json.loads(subprocess.check_output(['/usr/bin/findmnt','--json','--target',path,'--output','SOURCE,FSTYPE,TARGET'],text=True,timeout=3))['filesystems']
  assert len(row)==1 and row[0]['source']=='/dev/nvme1n1' and row[0]['fstype']=='xfs','asset/output is not the approved local NVMe XFS'
  storage_route_proof[name]={'path':path,'dev':target.stat().st_dev,'inode':target.stat().st_ino,'mount':row[0]}
"""

CACHE_CODE = """
def frozen_files():
 manifest=json.loads(pathlib.Path(s['input_inventory']['path']).read_text())
 assert hashlib.sha256(pathlib.Path(s['input_inventory']['path']).read_bytes()).hexdigest()==s['input_inventory']['sha256']
 group=manifest['roots']['safetensors' if s['condition'].startswith('safetensors') else 'store']
 for row in group['files']:
  p=pathlib.Path(row['path']);v=p.stat()
  assert str(p.resolve())==row['resolved'] and p.is_file()
  assert (v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_uid)==(row['dev'],row['ino'],row['bytes'],row['mtime_ns'],row['uid']),'asset identity drift'
 return group
def event(name):
 save('event-'+name+'.json',{'event':name,'monotonic':time.monotonic(),'monotonic_ns':time.monotonic_ns(),'wall':time.time(),'pid':os.getpid(),'measurement':'controller_observed_operation_boundary_not_pure_CUDA_or_IO','clock_identity':clock_identity,'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'run_id':run,'attempt':s['attempt'],'condition':s['condition']})
def construct_cache():
 group=frozen_files();paths=[r['path'] for r in group['files']];roots=[group['root']];deadline=time.monotonic()+1800
 event('target_condition_construction_begin')
 if s['condition']=='store_pool_hit':
  log=services['store'][2];offset=log.stat().st_size
  execute('worker','store_config',['pool'],seconds=min(1790,int(deadline-time.monotonic())),monitor=True)
  expected_paths={'vllm/'+s['config']['model']+'/rank_'+str(rank) for rank in range(8)}
  while True:
   healthy();assert time.monotonic()<deadline
   with log.open() as stream:stream.seek(offset);text=stream.read((8<<20)+1);assert len(text)<=8<<20
   loaded=re.findall(r'Finished loading model (\\S+) from disk',text)
   assert not any(word in text for word in ('Unload','ClearMem','evict')),'pool admission invalidation'
   assert len(loaded)<=8 and set(loaded)<=expected_paths
   if len(loaded)==8 and set(loaded)==expected_paths:break
   time.sleep(.2)
  save('pool-admission.json',{'paths':sorted(expected_paths),'offset':offset,'store_owner':store_owner,'success_log':text})
 else:
  action=cache.warm_file_cache if s['condition']=='safetensors_disk_hot' else cache.evict_file_cache
  result=action(paths,allowed_roots=roots,deadline=deadline)
  healthy();assert result['admitted'] is True;frozen_files()
  state=cache.query_cache_state(paths,allowed_roots=roots,deadline=deadline)
  save('cache-admission.json',{'construction':result,'state':state})
  assert_cache_state(state,s['condition']=='safetensors_disk_hot')
 if s['condition']=='store_disk_cold':
  assert identity(store_owner['pid'])['startticks']==store_owner['startticks']
  assert 'Finished loading model ' not in services['store'][2].read_text()
 frozen_files();event('cache_pool_admission_complete')
"""

# Verify actual residency and opened-file identities, rather than advice return codes.
JIT_CODE = """
def jit_snapshot(label,roles=('head','worker')):
 code="import json,pathlib,sys;root=pathlib.Path('/tmp/formal-jit');before=sys.argv[1]=='initial';assert not before or not root.exists();root.mkdir(exist_ok=not before);print(json.dumps([{'path':str(p.relative_to(root)),'bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file()]))"
 for role in roles:
  healthy();value=json.loads(call(D+['exec','--user=2147:2147',cids[role],'/usr/bin/python3','-I','-c',code,label],5));save('jit-'+role+'-'+label+'.json',value)
  if label=='initial':assert value==[]
"""

CACHE_SCHEMA_GATE = """
def assert_cache_state(state,hot):
 assert state['pages']>0 and state['all_hot' if hot else 'all_cold'] is True
 assert state['resident_pages']==(state['pages'] if hot else 0)
 group=frozen_files();expected={row['path']:row for row in group['files']}
 assert len(state['files'])==len(expected)
 counts={'metadata':{'pages':0,'resident_pages':0},'payload':{'pages':0,'resident_pages':0}}
 for row in state['files']:
  want=expected.pop(row['path']);identity=row['identity']
  assert identity=={'dev':want['dev'],'ino':want['ino'],'size':want['bytes'],'mtime_ns':want['mtime_ns'],'uid':want['uid']}
  assert row['resident_pages']==(row['pages'] if hot else 0)
  groupname='payload' if want['payload'] else 'metadata'
  for key in ('pages','resident_pages'):counts[groupname][key]+=row[key]
 assert not expected
 save('cache-admission-page-counts.json',counts)
"""


HTTP_BODY = """import json,sys,urllib.request,time,os
sys.path[:0]=['/source','/source/sllm_store']
from sllm.logger import get_clock_identity
HTTP_CLOCK=get_clock_identity();HTTP_PID=os.getpid()

def perform_http(path,payload,timeout):
 url='http://127.0.0.1:8343'+path
 data=None if payload is None else json.dumps(payload).encode()
 request=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
 clock=HTTP_CLOCK;pid=HTTP_PID;begin=time.monotonic_ns()
 with urllib.request.urlopen(request,timeout=timeout) as response:
  raw=response.read((1<<20)+1);body_complete=time.monotonic_ns()
  assert len(raw)<=1<<20
  value=json.loads(raw);parse_complete=time.monotonic_ns();status=response.status
 return {'status':status,'response':value,'elapsed':(parse_complete-begin)/1e9,'path':path,'request_id':None if payload is None else payload.get('request_id'),'pid':pid,'clock_identity':clock,'request_begin_ns':begin,'response_body_complete_ns':body_complete,'json_parse_complete_ns':parse_complete}
def finish_http(path,payload,timeout,want,register_config=None):
 registration=None
 if register_config is not None:
  registration_command=time.monotonic_ns()
  registration=perform_http('/register',register_config,timeout)
  register_complete=time.monotonic_ns()
  assert registration['status']==200
 trigger=time.monotonic_ns()
 row=perform_http(path,payload,timeout)
 row['received_ns']=time.monotonic_ns()
 row['business_trigger_ns']=trigger
 if registration is not None:
  row.update(registration_command_ns=registration_command,register_complete_ns=register_complete,registration_response=registration)
 if want is not None:
  row['child_gold_validation_begin_ns']=time.monotonic_ns()
  try:
   expected=want['expected'];response=row['response'];choices=response['choices'];usage={'prompt_tokens':expected['prompt_tokens'],'completion_tokens':expected['completion_tokens'],'total_tokens':expected['prompt_tokens']+expected['completion_tokens']}
   correct=row['status']==200 and len(choices)==1 and choices[0]['message']['content']==expected['content'] and choices[0]['finish_reason']==expected['finish_reason'] and response['usage']==usage
  except (KeyError,TypeError,IndexError) as error:
   correct=False;row['child_gold_validation_error']={'type':type(error).__name__,'message':str(error)}
  row['child_gold_validation_complete_ns']=time.monotonic_ns();row['child_gold_validation_pass']=correct
 return row
row=finish_http(sys.argv[1],json.loads(sys.argv[2]),float(sys.argv[3]),json.loads(sys.argv[4]) if len(sys.argv)>4 else None,json.loads(sys.argv[5]) if len(sys.argv)>5 else None)
print(json.dumps(row),flush=True)
assert row.get('child_gold_validation_pass',True)

"""

HTTP_EVENTS = """
def same_clock(left,right):
 for key in ('boot_id','time_namespace','monotonic_clock_implementation'):
  assert left.get(key) is not None and left.get(key)==right.get(key),'incompatible monotonic clock domains'
 assert not left.get('errors') and not right.get('errors')
def validated_http_events(row,want,index):
 same_clock(row['clock_identity'],clock_identity)
 assert row['request_id']==run+'-'+str(index) and row['path']=='/v1/chat/completions' and type(row['pid']) is int and row['pid']>0
 assert 0<row['business_trigger_ns']<=row['request_begin_ns']<=row['response_body_complete_ns']<=row['json_parse_complete_ns']<=row['received_ns']<=row['child_gold_validation_begin_ns']<=row['child_gold_validation_complete_ns']
 parent_end=time.monotonic_ns()
 shared={'run_id':run,'attempt':s['attempt'],'condition':s['condition'],'request_id':row['request_id'],'client_pid':row['pid'],'http_producer_times':{key:row[key] for key in ('business_trigger_ns','request_begin_ns','response_body_complete_ns','json_parse_complete_ns','received_ns','child_gold_validation_begin_ns','child_gold_validation_complete_ns')},'client_cid':cids['head'],'clock_identity':row['clock_identity'],'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256']}
 begin={**shared,'event':want['case']+'_http_begin','monotonic_ns':row['business_trigger_ns']}
 complete={**shared,'event':want['case']+'_http_complete_correct','monotonic_ns':row[s['primary_http_endpoint']],'response_body_complete_ns':row['response_body_complete_ns'],'endpoint':s['primary_http_endpoint'],'parent_validation_end_ns':parent_end,'parent_clock_identity':clock_identity}
 if index==0:
  assert 0<row['registration_command_ns']<=row['register_complete_ns']<=row['business_trigger_ns']
  complete.update(primary_A_ns=row['received_ns']-row['registration_command_ns'],business_B_ns=row['received_ns']-row['business_trigger_ns'],registration_command_ns=row['registration_command_ns'],register_complete_ns=row['register_complete_ns'])
  save('event-command_register_begin.json',{**shared,'event':'command_register_begin','monotonic_ns':row['registration_command_ns'],'producer':'same_http_client_as_arithmetic'})
  save('event-command_register_complete.json',{**shared,'event':'command_register_complete','monotonic_ns':row['register_complete_ns']})
 else:complete['business_B_ns']=row['received_ns']-row['business_trigger_ns']
 save('event-'+begin['event']+'.json',begin);save('event-'+complete['event']+'.json',complete)
 return complete
"""

RESPONSE = """def check_response(row,want,index,life):
 record_http_sample(row,want,index)
 response=row['response'];value=response['choices'][0];expected=want['expected'];usage={'prompt_tokens':expected['prompt_tokens'],'completion_tokens':expected['completion_tokens'],'total_tokens':expected['prompt_tokens']+expected['completion_tokens']}
 assert row['child_gold_validation_pass'] is True
 assert row['status']==200 and len(response['choices'])==1 and value['message']['content']==expected['content'] and value['finish_reason']==expected['finish_reason'] and response['usage']==usage
 try:complete=validated_http_events(row,want,index)
 except BaseException as error:
  mark_sample_gate('clock',False,type(error).__name__+':'+str(error));raise
 save('life'+str(life)+'-response'+str(index)+'.json',{'actual':row,'expected':want,'expected_usage':usage,'validated_endpoint':complete})
 return complete
"""

LOOP = """ for life in (1,):
  positions=log_positions();router_positions=head_log_positions();create_end=time.monotonic()+1500;history=[];rank_owners=[];aab_end=None
  sample_active_gate='cache';construct_cache();mark_sample_gate('cache',True,'finite cache/pool admission passed');sample_active_gate=None
  for index,want in enumerate(gold):
   payload={key:want[key] for key in ('input_tokens','temperature','top_p','top_k','min_p','seed','n','max_tokens','ignore_eos')};payload.update(model=s['config']['model'],request_id=run+'-'+str(index),logprobs=1)
   until=create_end if index==0 else aab_end;remaining=int(until-time.monotonic());assert remaining>5
   if index==0:event('parent_register_arithmetic_dispatch')
   sample_active_gate='gold'
   response=HTTP('/v1/chat/completions',payload,remaining,monitor=True,want=want,register_config=s['config'] if index==0 else None);check_response(response,want,index,life)
   tokens=json.loads(execute('head','tokens',[ips['worker']],seconds=35));history.append(want['input_tokens']+want['expected']['token_ids'])
   assert len({tuple(r['input_tokens']) for r in gold})==3
   assert collections.Counter(json.dumps(x) for x in tokens['tokens'])==collections.Counter(json.dumps(x) for x in history)
   save('life'+str(life)+'-tokens'+str(index)+'.json',tokens)
   if index==0:
    sample_active_gate='ownership'
    assert time.monotonic()<create_end;aab_end=time.monotonic()+180;records=records_since(positions);assert len(records)==8;rank_owners=[owned_host_pid(r['pid']) for r in records];backend_owner=owned_host_pid(tokens['metadata']['Pid']);physical=sample()
    for r,o in zip(records,rank_owners):assert str(o['pid']) in [str(x) for x in physical[r['device_uuid']]['processes']]
    save('life'+str(life)+'-eight-rank-load-and-ownership.json',{'records':records,'owners':rank_owners,'physical':physical});mark_sample_gate('ownership',True,'all8 rank owners physically authenticated');sample_active_gate=None
"""

SAMPLE_EVIDENCE = """
sample_metrics={}
sample_gates={name:{'status':'NOT_OBSERVED','reason':'gate_not_completed'} for name in ('cache','gold','clock','ownership')}
sample_active_gate=None
condition_normal=False
cleanup_started=False
def mark_sample_gate(name,passed,reason):
 sample_gates[name]={'status':'PASS' if passed else 'FAIL','reason':reason}
def record_http_sample(row,want,index):
 times={key:row.get(key) for key in ('registration_command_ns','register_complete_ns','business_trigger_ns','request_begin_ns','response_body_complete_ns','json_parse_complete_ns','received_ns','child_gold_validation_begin_ns','child_gold_validation_complete_ns')}
 received=times['received_ns'];trigger=times['business_trigger_ns'];command=times['registration_command_ns']
 valid_business=type(received) is int and type(trigger) is int and 0<trigger<=received
 valid_primary=index==0 and type(command) is int and type(received) is int and 0<command<=received
 value={'case':want['case'],'request_id':row.get('request_id'),'observation_status':'OBSERVED','producer_times':times,'primary_A_ns':received-command if valid_primary else None,'business_B_ns':received-trigger if valid_business else None,'raw_interval_status':'NUMERIC' if valid_business and (index!=0 or valid_primary) else 'INVALID_OR_MISSING_TIMESTAMPS','child_gold_validation_pass':row.get('child_gold_validation_pass'),'stdout_reference':row.get('stdout_reference'),'clock_identity':row.get('clock_identity')}
 old=sample_metrics.get(want['case'])
 if old is not None:
  assert old['producer_times']==times,'conflicting same-request HTTP observations'
  return
 sample_metrics[want['case']]=value
 save('sample-metrics-'+want['case']+'.json',value)
def recover_http_samples():
 for _,output,_ in children:
  path=pathlib.Path(output.name)
  if not path.name.endswith('-head-http.stdout') or not path.is_file():continue
  size=path.stat().st_size
  if not 0<size<=2<<20:continue
  raw=path.read_bytes()
  try:row=json.loads(raw)
  except (ValueError,UnicodeDecodeError):continue
  if not isinstance(row,dict):continue
  for index,want in enumerate(s['correctness']['requests']):
   if row.get('request_id')==run+'-'+str(index):
    row['stdout_reference']={'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    record_http_sample(row,want,index)
def preserve_sample_metrics():
 path=base/'sample-metrics.json'
 if path.exists():return
 values=[]
 for want in s['correctness']['requests']:
  values.append(sample_metrics.get(want['case'],{'case':want['case'],'observation_status':'NOT_OBSERVED','primary_A_ns':None,'business_B_ns':None}))
 save('sample-metrics.json',{'run_id':run,'attempt':s['attempt'],'condition':s['condition'],'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'requests':values,'policy':'Raw observed producer intervals retained regardless of qualification or later cleanup failure; missing observations remain explicit.'})
def sample_qualification(gates,terminal,cleanup,metrics):
 reasons=[name+':'+str(gates.get(name,{}).get('status','NOT_OBSERVED'))+':'+str(gates.get(name,{}).get('reason','missing')) for name in ('cache','gold','clock','ownership') if gates.get(name,{}).get('status')!='PASS']
 for case in ('arithmetic','translation','fixed32'):
  row=metrics.get(case,{})
  if row.get('observation_status')!='OBSERVED' or row.get('raw_interval_status')!='NUMERIC' or not isinstance(row.get('stdout_reference'),dict):reasons.append('metrics:'+case+':MISSING_OR_INVALID_EVIDENCE')
 if terminal.get('normal') is not True:reasons.append('condition_terminal:'+str(terminal.get('status','NOT_OBSERVED'))+':'+str(terminal.get('reason','missing')))
 if cleanup.get('normal') is not True or type(cleanup.get('quiet_seconds')) not in (int,float) or not 60<=cleanup['quiet_seconds']<float('inf'):reasons.append('cleanup_normal_quiet60:'+str(cleanup.get('status','NOT_OBSERVED'))+':'+str(cleanup.get('reason','missing')))
 return {'primary_sample_qualified':not reasons,'invalid_reasons':reasons,'rule':'cache+gold(text/usage/finish and exact trace IDs)+clock+ownership PASS AND normal condition terminal AND normal cleanup quiet>=60; NOT_OBSERVED never passes'}
def condition_terminal(normal,reason):
 preserve_sample_metrics()
 path=base/'condition-terminal.json'
 if not path.exists():save('condition-terminal.json',{'run_id':run,'attempt':s['attempt'],'condition':s['condition'],'normal':normal,'status':'NORMAL' if normal else 'FAILED_OR_INCOMPLETE','reason':reason,'monotonic_ns':time.monotonic_ns()})
def finalize_sample_evidence(cleanup,reason):
 if (base/'sample-validity.json').exists():return json.loads((base/'sample-validity.json').read_text())
 recover_http_samples();condition_terminal(condition_normal,reason)
 if not (base/'cleanup-result.json').exists():
  refs=[]
  for name in ('store-stop-exit60.json','head-exited.json','worker-exited.json','guard-head/guard-final.json','guard-worker/guard-final.json','observer/observer-final.json'):
   path=base/name
   if path.is_file() and path.stat().st_size<=2<<20:refs.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
  save('cleanup-result.json',{'run_id':run,'attempt':s['attempt'],'condition':s['condition'],**cleanup,'evidence':refs})
 terminal=json.loads((base/'condition-terminal.json').read_text())
 verdict=sample_qualification(sample_gates,terminal,cleanup,sample_metrics)
 references=[]
 for name in ('sample-metrics.json','condition-terminal.json','cleanup-result.json'):
  path=base/name;references.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 save('sample-validity.json',{'run_id':run,'attempt':s['attempt'],'condition':s['condition'],'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'gates':sample_gates,**verdict,'evidence':references})
 return verdict
"""

STRUCTURED_EVENTS = """
def collect_structured_events(router_positions,worker_positions,tokens,backend_owner):
 groups={'router_instance_published':[],'backend_init_begin':[],'backend_init_ready':[],'backend_first_token_observed':[]}
 sources=[];total=0
 paths=[('head',p,router_positions) for p in head_log_paths() if p.is_relative_to(base/'head/ray')]+[('worker',p,worker_positions) for p in (base/'worker/ray').rglob('worker-*') if p.is_file() and p.suffix in ('.out','.err')]
 for role,path,offsets in paths:
  start=offsets.get(str(path),0)
  with path.open() as stream:stream.seek(start);text=stream.read((16<<20)+1)
  assert len(text)<=16<<20;total+=len(text);assert total<=128<<20
  sources.append({'role':role,'path':str(path),'offset':start,'bytes':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest()})
  for line in text.splitlines():
   marker=line.find('{"event":')
   if marker<0:continue
   try:row,_=json.JSONDecoder().raw_decode(line[marker:])
   except ValueError:continue
   name=row.get('event')
   if name not in groups or row.get('model')!=s['config']['model']:continue
   assert role==('head' if name=='router_instance_published' else 'worker')
   same_clock(row['clock_identity'],clock_identity);assert type(row['monotonic_ns']) is int and row['monotonic_ns']>0
   groups[name].append(row)
 for name in ('router_instance_published','backend_init_begin','backend_init_ready'):assert len(groups[name])==1,('structured event cardinality',name,len(groups[name]))
 route=groups['router_instance_published'][0];begin=groups['backend_init_begin'][0];ready=groups['backend_init_ready'][0]
 assert route['instance_id']==tokens['actor']['name']
 for row in (begin,ready,*groups['backend_first_token_observed']):
  assert row['pid']==tokens['metadata']['Pid'] and row['clock_identity']['process_startticks']==backend_owner['startticks']
 assert begin['monotonic_ns']<=ready['monotonic_ns']<=route['monotonic_ns']
 first=groups['backend_first_token_observed'];assert len(first)==3 and {r['request_id'] for r in first}=={run+'-'+str(i) for i in range(3)}
 for row in first:
  index=int(row['request_id'].rsplit('-',1)[1]);case=s['correctness']['requests'][index]['case']
  http=json.loads((base/('event-'+case+'_http_complete_correct.json')).read_text())
  request=json.loads((base/('event-'+case+'_http_begin.json')).read_text())
  assert request['monotonic_ns']<=row['monotonic_ns']<=http['response_body_complete_ns']
 router_owner=owned_host_pid(route['pid'],'head');assert router_owner['startticks']==route['clock_identity']['process_startticks']
 save('structured-lifecycle-events.json',{'run_id':run,'attempt':s['attempt'],'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'events':groups,'sources':sources,'backend_owner':backend_owner,'router_owner':router_owner,'instance_id':route['instance_id']})
"""

def milestone_assert_messages(body):
    # This execution body comes from the frozen exit60 command, not s4a probes.
    messages = {
        "row['State']['Running']":
            'f"normal_exit role={role}: container not running; State={row[\'State\']!r}"',
        "row['State']['ExitCode']==0 and not row['State']['OOMKilled'] and row['State']['Pid']==0 and row['RestartCount']==0":
            'f"normal_exit role={role}: expected exit0/Pid0/noOOM/restart0; State={row[\'State\']!r}; RestartCount={row[\'RestartCount\']!r}"',
        "result['status']=='KERNEL_CLEANUP_RELEASE_PASS' and not result['failures'] and result['quiet_seconds']>=60":
            "f'guard release role={role}: expected PASS/no failures/quiet>=60; actual={result!r}'",
        "not pathlib.Path('/sys/fs/cgroup/system.slice/docker-'+cids[role]+'.scope').exists()":
            "f'cgroup release role={role}: scope remains for cid={cids[role]}'",
        "observed['quiet_seconds']>=60":
            "f'observer release: expected quiet>=60; actual={observed!r}'",
        "p['Result']=='success' and p['ExecMainCode']=='1' and p['ExecMainStatus']=='0'":
            "f'unit exit metadata unit={u}: expected success/code1/status0; actual={p!r}'",
        'time.monotonic()-started<7020':
            "f'release wait deadline7020 exceeded; units={units!r}'",
        "int(p['MainPID'])>0":
            "f'final-stop admission role={role}: unit no longer active; actual={p!r}'",
        "all(v=={'used_mib':0,'processes':[]} for v in m.gpu().values()) and gone(store_owner) and gone(observer_owner)":
            "f'final GPUzero/owned-gone failed: expected every GPU used_mib0/no processes and both identities gone; store_owner={store_owner!r}; observer_owner={observer_owner!r}'",
        "len({tuple(r['input_tokens']) for r in gold})==3":
            'f"input history life={life} request={index}: expected3 unique prompts; actual={[r[\'input_tokens\'] for r in gold]!r}"',
        "collections.Counter(json.dumps(x) for x in tokens['tokens'])==collections.Counter(json.dumps(x) for x in history)":
            'f"token history life={life} request={index}: actual={tokens[\'tokens\']!r}; expected={history!r}"',
    }
    expected = {ast.dump(ast.parse(test, mode='eval').body): message for test, message in messages.items()}
    edits = []; found = set(); lines = body.splitlines(keepends=True)
    offsets = [0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    for node in ast.walk(ast.parse(body)):
        if not isinstance(node, ast.Assert) or node.msg is not None: continue
        key = ast.dump(node.test); message = expected.get(key)
        if ast.unparse(node.test)=='time.monotonic() < limit' and "inspect(role)['State']['Status']" in lines[node.lineno-1]:
            message = "f'normal_exit role={role}: container exit wait deadline exceeded; cid={cids[role]}'"
        if ast.unparse(node.test)=='gone(owner)' and "result['pinned_identities']" in lines[node.lineno-1]:
            message = "f'pinned identity release role={role}: owner remains; actual={owner!r}'"
        if message is not None:
            edits.append((offsets[node.end_lineno-1]+node.end_col_offset, ', '+message)); found.add(key)
    assert set(expected)<=found, ('milestone assertion template drift', set(expected)-found)
    for position, message in sorted(edits, reverse=True): body=body[:position]+message+body[position:]
    return body


def render(protocol, spec, command, attempt_id, source_pins, *, diagnostic_run_id=None):
    validate_protocol(protocol)
    attempt = next(a for a in protocol["attempts"] if a["attempt_id"] == attempt_id)
    logical_attempt_id=attempt_id
    attempt_id=execution_mapping(protocol)[0][logical_attempt_id]
    if diagnostic_run_id is None:
        assert re.fullmatch(r"kimi-four-v1-b[0-9]{2}-[a-z_]+(?:-retry02)?", attempt_id)
    else:
        assert diagnostic_run_id=='kimi-sfhot-repeatability-20261107-once' or re.fullmatch(r"kimi-diagnostic-value-20261107-(?:sf1|sf2|storecold|poolhit)", diagnostic_run_id)
        attempt_id=diagnostic_run_id
    assert not (pathlib.Path(protocol["resource_budget"].get("runs_root",str(base.EVIDENCE.parent/"runs"))) / attempt_id).exists(), "identity already used"
    assert spec["image"] == protocol["deployment"]["image"]
    assert spec["store_extension"]["sha256"] == protocol["deployment"]["extension_sha256"]
    assert len(spec["role_bodies"]) == 9
    for name, body in spec["role_bodies"].items():
        assert base.hashlib.sha256(body.encode()).hexdigest() == spec["role_body_sha256"][name]
    sb, cb = base.render_pool_exit60(spec, command, SOURCE, base.HELPER_SHA)
    spec, command = json.loads(sb), json.loads(cb)
    index = command["argv"].index("-c") + 1
    body = command["argv"][index]
    old_run = spec["run_id"]
    spec.update(run_id=attempt_id, logical_attempt_id=logical_attempt_id, execution_revision=copy.deepcopy(protocol.get("execution_revision")), condition=attempt["condition"], status="CANDIDATE_NOT_FROZEN_NOT_EXECUTABLE",
                protocol_sha256=PROTOCOL_SHA, cache_helper_sha256="3340bf6a700d5e6477137ecf937235a07b76d0e5b95044758b5a0c3f0782b4f8", input_inventory={"path":protocol["assets"].get("input_inventory_path",str(INVENTORY)),"sha256":protocol["assets"].get("input_inventory_sha256",INVENTORY_SHA)},
                correctness=protocol["correctness"], attempt=attempt, native_source_pins=copy.deepcopy(source_pins), primary_http_endpoint=protocol['events'].get('primary_http_endpoint','received_ns'))
    spec["native_source_pins"]["sllm_store/tests/file_cache_state.py"]=spec["cache_helper_sha256"]
    spec["actual_request_parameters"]=[{"case":row["case"],**{key:row[key] for key in ("input_tokens","temperature","top_p","top_k","min_p","seed","n","max_tokens","ignore_eos")},"logprobs":1} for row in protocol["correctness"]["requests"]]
    spec["request_observability"]="Actual HTTP logprobs=1; generated token IDs use existing trace, return_token_ids is not passed to SamplingParams. Explicit top_p=1 remains vs omitted GLM default. Default V1 candidate lacks this explicit logprobs field and is not claimed fully aligned or executable."
    spec["planned_attempts"]=copy.deepcopy(protocol["attempts"])
    spec["missing_before_freeze"] = ["full block admission/claims", "final source/pin audit", "phase actual-clock review"]
    spec["runs_root"]=protocol["resource_budget"].get("runs_root",str(base.EVIDENCE.parent/"runs"))
    spec["safetensors_host"]=protocol["assets"]["safetensors"]
    spec["archive"]=protocol["assets"].get("store_archive",spec["archive"])
    spec["storage_route"]={"route":protocol["assets"].get("route","candidate_legacy_not_executable"),"asset_and_raw_output_filesystem":"xfs on /dev/nvme1n1" if protocol["assets"].get("route")=="local_nvme" else "NOT_ADMITTED","scope":"Weight inputs and raw formal outputs on approved local NVMe; shared /home source, libraries, S0 control specs and claims remain NFS. Not all IO is local."}
    config=spec["config"];config["auto_scaling_config"].update(min_instances=0,max_instances=1,keep_alive=3600)
    config["backend_config"]["enable_prefix_caching"]=False
    config["placement_config"]={"memory_pool":[]}
    if attempt["condition"].startswith("safetensors"):
        config["backend_config"].update(pretrained_model_name_or_path="/mnt/raid/models/Kimi-K2.6",load_format="safetensors")
    for role, request in spec["requests"].items():
        request["name"]=attempt_id+"-"+role
        for mount in request["mounts"]:
            if mount["Target"]=="/store":mount["Source"]=spec["archive"]
        request["mounts"].append({"Type":"bind","Source":spec["safetensors_host"],"Target":"/mnt/raid/models/Kimi-K2.6","ReadOnly":True})
        for key in ("TORCHINDUCTOR_CACHE_DIR","TRITON_CACHE_DIR","CUDA_CACHE_PATH","VLLM_CACHE_ROOT"):
            request["env"][key]="/tmp/formal-jit/"+key.lower()
    spec["role_bodies"]["store_config"] += """
if sys.argv[1]=='pool':
 client=SllmStoreClient('127.0.0.1:8073')
 for rank in range(8):
  path='vllm/kimi-k2.6-sf-reference/rank_'+str(rank);assert client.register_model(path)>0;assert client.load_into_cpu(path)
"""
    # Existing config query retains its numeric timeout; pool action uses a separate branch.
    poolbody=spec["role_bodies"]["store_config"]
    poolbody=poolbody.replace("end=time.monotonic()+float(sys.argv[1])","end=time.monotonic()+(1700 if sys.argv[1]=='pool' else float(sys.argv[1]))")
    spec["role_bodies"]["http"]=HTTP_BODY
    spec["role_body_sha256"]["http"]=hashlib.sha256(HTTP_BODY.encode()).hexdigest()
    spec["role_bodies"]["store_config"]=poolbody
    spec["role_body_sha256"]["store_config"]=hashlib.sha256(poolbody.encode()).hexdigest()
    body=body.replace("run=s['run_id'];ROOT=", "run=s['run_id'];assert s['status']=='FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW' and s['source_commit'];ROOT=",1)
    old_base="base=pathlib.Path('/home/boyjiang/kimi-new/evidence/runs')/run"
    assert body.count(old_base)==1
    body=body.replace(old_base,"base=pathlib.Path(s['runs_root'])/run",1)
    body=body.replace("base.mkdir(mode=0o700);",STORAGE_ROUTE_CODE+"\nbase.mkdir(mode=0o700);",1)
    body=body.replace("try:\n assert time.monotonic()+30<end","try:\n save('storage-route-admission.json',storage_route_proof)\n assert time.monotonic()+30<end",1)
    body=body.replace("for life in (1,2):", "for life in (1,):",1)
    begin=body.index(" for life in (1,):");end=body.index("  delete_end=",begin)
    body=body[:begin]+LOOP+"  mark_sample_gate('gold',True,'all3 child/parent gold and cumulative exact trace IDs passed');sample_active_gate='clock'\n  collect_structured_events(router_positions,positions,tokens,backend_owner);mark_sample_gate('clock',True,'all client/backend/route producer domains and lifecycle joins verified');sample_active_gate=None\n"+body[end:]
    old="golden=[json.loads(l) for l in pathlib.Path(s['gold']['path']).read_text().splitlines()];gold=next(r['validated'] for r in golden if r['event']=='SF_API_AAB_PASS');assert len(gold)==3 and hashlib.sha256(pathlib.Path(s['gold']['path']).read_bytes()).hexdigest()==s['gold']['sha256']"
    assert body.count(old)==1
    body=body.replace(old,"gold=s['correctness']['requests'];assert len(gold)==3;assert (pathlib.Path(s['archive'])/'vllm'/s['config']['model']).is_dir(),'missing converted model directory; download forbidden'")
    body=replace_function(body,"check_response",RESPONSE)
    body=replace_function(body,"HTTP","def HTTP(path,payload,seconds,monitor=False,want=None,register_config=None):\n raw=execute('head','http',[path,json.dumps(payload),str(seconds-3),json.dumps(want),json.dumps(register_config)],seconds=seconds,monitor=monitor);row=json.loads(raw);p=base/(str(seq)+'-head-http.stdout');row['stdout_reference']={'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()};return row")
    insertion="sys.path.insert(0,str(ROOT));from sllm.logger import get_clock_identity;clock_identity=get_clock_identity()\nassert hashlib.sha256((ROOT/'sllm_store/tests/file_cache_state.py').read_bytes()).hexdigest()==s['cache_helper_sha256'];loader=importlib.util.spec_from_file_location('finite_cache',ROOT/'sllm_store/tests/file_cache_state.py');cache=importlib.util.module_from_spec(loader);loader.loader.exec_module(cache)\n"
    body=body.replace("try:\n save('storage-route-admission.json',storage_route_proof)\n assert time.monotonic()+30<end",insertion+CACHE_CODE+JIT_CODE+CACHE_SCHEMA_GATE+HTTP_EVENTS+SAMPLE_EVIDENCE+STRUCTURED_EVENTS+"\ntry:\n save('storage-route-admission.json',storage_route_proof)\n assert time.monotonic()+30<end",1)
    body=body.replace("create_end=time.monotonic()+1500;history=[]","create_end=None;history=[]")
    body=body.replace("construct_cache();mark_sample_gate","construct_cache();create_end=time.monotonic()+1500;mark_sample_gate",1)
    body=body.replace(" for life in (1,):"," jit_snapshot('common_initialized')\n for life in (1,):",1)
    for role in ('head','worker'):
        old="create_arm_start('"+role+"');execute('"+role+"','bootstrap'"
        new="create_arm_start('"+role+"');jit_snapshot('initial',('"+role+"',));execute('"+role+"','bootstrap'"
        assert body.count(old)==1
        body=body.replace(old,new,1)
    body=body.replace(" stop_started=time.monotonic();stop_end=stop_started+600"," jit_snapshot('after')\n stop_started=time.monotonic();stop_end=stop_started+600",1)
    body=body.replace("NEW_PROTOCOL_EXIT60_L1_CREATE_AAB_DELETE_RECREATE_ALL_EXIT_GPUZERO60_PASS","FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS")
    body=body.replace("'scope':'Officialnative2lifecycles exactSFgoldtokens/text/usage,8rankload/physicalownership; no modelcallback/nativepickle'","'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'attempt':s['attempt'],'condition':s['condition'],'planned_attempts':s['planned_attempts'],'structured_events_path':str(base/'structured-lifecycle-events.json'),'scope':'Prospective singlecondition correct requests, real producer HTTP endpoint, structured init/route/firsttoken join, owned release60'")
    body=body.replace("'cids':cids,'elapsed':time.monotonic()-started","'cids':cids,'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'attempt':s['attempt'],'condition':s['condition'],'planned_attempts':s['planned_attempts'],'elapsed':time.monotonic()-started")
    body=body.replace(" jit_snapshot('after')"," condition_normal=True;condition_terminal(True,'all requests and delete/baseline60 normal')\n jit_snapshot('after')",1)
    body=body.replace(" stop_started=time.monotonic();stop_end=stop_started+600"," cleanup_started=True\n stop_started=time.monotonic();stop_end=stop_started+600",1)
    body=body.replace(" save('controller-final.json',"," qualification=finalize_sample_evidence({'normal':True,'status':'NORMAL_QUIET60','quiet_seconds':min(observed['quiet_seconds'],*[json.loads((base/('guard-'+role)/'guard-final.json').read_text())['quiet_seconds'] for role in ('head','worker')])},'normal completion');assert qualification['primary_sample_qualified'],'missing or invalid sample evidence'\n save('controller-final.json',",1)
    body=body.replace("except BaseException as error:\n for name in ('guard-head','guard-worker','observer'):","except BaseException as error:\n if sample_active_gate is not None:mark_sample_gate(sample_active_gate,False,type(error).__name__+':'+str(error))\n try:finalize_sample_evidence({'normal':False,'status':'FAILED' if cleanup_started else 'NOT_OBSERVED','quiet_seconds':None,'reason':type(error).__name__+':'+str(error)},type(error).__name__+':'+str(error))\n except Exception as evidence_error:\n  try:save('sample-evidence-error.json',{'type':type(evidence_error).__name__,'message':str(evidence_error),'primary_error':str(error)})\n  except Exception as diagnostic_error:\n   try:print(json.dumps({'event':'SAMPLE_EVIDENCE_DIAGNOSTIC_SAVE_FAILED','primary_error_type':type(error).__name__,'evidence_error_type':type(evidence_error).__name__,'diagnostic_error_type':type(diagnostic_error).__name__}),flush=True)\n   except Exception:pass\n for name in ('guard-head','guard-worker','observer'):",1)
    # Cost endpoints surround existing calls; event IO consumes unchanged phase slack.
    endpoints=[
        (" create_arm_start('head');"," event('common_environment_begin')\n create_arm_start('head');"),
        (" pool_end=time.monotonic()+1200;"," event('common_environment_complete')\n pool_end=time.monotonic()+1200;"),
        (" official('head','ray.scripts.scripts',headargs,"," event('ray_join_begin')\n official('head','ray.scripts.scripts',headargs,"),
        ("save('ray-two-nodes.json',state)","save('ray-two-nodes.json',state);event('ray_join_complete')"),
        (" official('worker','sllm_store.cli',['start',"," event('store_pool_initialization_begin')\n official('worker','sllm_store.cli',['start',"),
        (" initial=baseline_window(None,pool_end,label='initial');"," event('store_pool_initialization_complete');event('initial_baseline60_begin')\n initial=baseline_window(None,pool_end,label='initial');"),
        ("save('initial-fresh-store-baseline.json',initial)","save('initial-fresh-store-baseline.json',initial);event('initial_baseline60_complete')"),
        (" official('head','sllm.cli.clic',['start',"," event('api_startup_begin')\n official('head','sllm.cli.clic',['start',"),
        ("save('controller-initial-authenticated-identity.json',controller_initial)","save('controller-initial-authenticated-identity.json',controller_initial);event('api_controller_authentication_complete')"),
        ("  delete_end=time.monotonic()+180;HTTP('/delete',","  delete_end=time.monotonic()+180;event('delete_begin');HTTP('/delete',"),
        ("min(120,int(delete_end-time.monotonic())))","min(120,int(delete_end-time.monotonic())));event('delete_acknowledged')"),
        ("  after=baseline_window(initial['snapshot'],delete_end,","  event('delete_baseline60_begin')\n  after=baseline_window(initial['snapshot'],delete_end,"),
        ("save('life'+str(life)+'-deleted-fresh-baseline.json',after)","save('life'+str(life)+'-deleted-fresh-baseline.json',after);event('delete_baseline60_complete')"),
        (" cleanup_started=True\n"," cleanup_started=True;event('ordered_final_stop_begin')\n"),
        (" execute('head','controller_shutdown',"," event('controller_shutdown_and_api_stop_begin')\n execute('head','controller_shutdown',"),
        ("stop_service('api','head',min(stop_end,time.monotonic()+30));","stop_service('api','head',min(stop_end,time.monotonic()+30));event('controller_shutdown_and_api_stop_complete');"),
        ("normal_exit('worker');normal_exit('head');","normal_exit('worker');normal_exit('head');event('containers_normal_exit_complete');"),
        ("gone(store_owner) and gone(observer_owner);checkfiles();","gone(store_owner) and gone(observer_owner);event('release_quiet60_complete');checkfiles();"),
    ]
    for old,new in endpoints:
        assert body.count(old)==1,('cost event insertion target drift',old,body.count(old))
        body=body.replace(old,new,1)
    spec["controller_cost_event_semantics"]={
        "scope":"Actual controller-observed operation boundaries include bounded IPC, polling, evidence IO and scheduling; not pure CUDA/IO or exclusive additive phase spans. Main HTTP producer A/B/D endpoints remain unchanged.",
        "api_stop":"Original controller_shutdown plus API process stop/finish inclusive; Store normal exit uses the separate unchanged strict60 receipt.",
        "store_pool_initialization":"Existing Store CLI launch through successful GetServerConfig pool/chunk validation; no added probe.",
        "release_quiet60_complete":"After both guards, observer and owned unit exits plus final physical GPUzero/gone checks; subsequent source rehash/network evidence work separate.",
        "budget":"Event writes consume existing7200 condition/28920 block slack. No deadline extension or shortened quiet period.",
    }
    body=milestone_assert_messages(body)
    compile(body,"<formal-candidate>","exec")
    command["argv"][index]=body
    command["argv"][command["argv"].index("--unit="+old_run+"-controller.service")]="--unit="+attempt_id+"-controller.service"
    spec["budgets"]["phase_names"]=["headbootstrap","workerbootstrap","rayjoin_pool","api","cache_admission","register_arithmetic","translation_fixed32","delete_baseline60","orderedstop600_release120","evidence"]
    spec["budgets"]["phase_max"]=[300,300,1200,120,1800,1500,180,180,720,180]
    spec["budgets"]["sum"]=sum(spec["budgets"]["phase_max"])
    assert spec["budgets"]["sum"]==6480<7200
    command.update(status="CANDIDATE_NOT_FROZEN_NOT_EXECUTABLE",root_python_sha256=hashlib.sha256(body.encode()).hexdigest())
    return spec,command


BLOCK_BODY = """
import hashlib,json,os,pathlib,subprocess,sys,time
assert os.geteuid()==0
bundle=pathlib.Path(sys.argv[1]);expected=sys.argv[2];expiry=float(sys.argv[3])
assert hashlib.sha256(bundle.read_bytes()).hexdigest()==expected
b=json.loads(bundle.read_text());assert b['status']=='FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW'
assert len(b['attempts'])==4 and len({a['run_id'] for a in b['attempts']})==4
assert time.time()+b['full_max_seconds']<expiry,'full block budget unavailable'
claims=pathlib.Path(b['claims_root']);assert claims.is_dir() and claims.stat().st_uid==0 and claims.stat().st_mode&0o777==0o700
runs=pathlib.Path(b['runs_root'])
def claim(name):
 fd=os.open(claims/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:
  json.dump({'pid':os.getpid(),'wall':time.time(),'monotonic':time.monotonic(),'expiry':expiry},f);f.flush();os.fsync(f.fileno())
claim(b['block_id']+'.claim')
ledger=[{**a,'status':'NOT_STARTED','condition_terminal':'NOT_STARTED','launch_requested':False,'attempted':False,'runtime_evidence_created':False,'returncode':None,'launch_error':None,'evidence_missing':None,'result_path':None,'result_sha256':None} for a in b['attempts']]
status='FAILED_RETAINED_NO_RETRY';error=None
try:
 for index,a in enumerate(ledger):
  assert time.time()+(4-index)*7200+120<expiry,'remaining full block budget unavailable'
  p=pathlib.Path(a['command_path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==a['command_sha256']
  c=json.loads(p.read_text());argv=c['argv'];specpath=pathlib.Path(argv[-3])
  assert hashlib.sha256(specpath.read_bytes()).hexdigest()==argv[-2]==c['spec_sha256']
  spec=json.loads(specpath.read_text());assert spec['status']=='FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW' and spec['run_id']==a['run_id']
  assert spec['source_commit']==b['source_commit'] and spec['protocol_sha256']==b['protocol_sha256']
  assert not (runs/a['run_id']).exists()
  assert argv[-1]=='FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED'
  assert argv[0]=='/usr/bin/systemd-run' and '--wait' not in argv
  claim(a['run_id']+'.claim')
  argv=[argv[0],'--wait',*['--property=RemainAfterExit=no' if x=='--property=RemainAfterExit=yes' else x for x in argv[1:-1]],str(expiry)]
  a.update(status='START_REQUESTED',condition_terminal='START_REQUESTED',launch_requested=True)
  try:
   result=subprocess.run(argv,timeout=7205);a['returncode']=result.returncode
  except BaseException as launch_error:
   a['launch_error']={'type':type(launch_error).__name__,'message':str(launch_error)}
   raise
  finally:
   a['runtime_evidence_created']=(runs/a['run_id']).is_dir();a['attempted']=a['runtime_evidence_created'];has_receipt=False
   for name in ('controller-final.json','controller-failed.json'):
    final=runs/a['run_id']/name
    if final.exists():
     a.update(result_path=str(final),result_sha256=hashlib.sha256(final.read_bytes()).hexdigest())
     a['status']=a['condition_terminal']=json.loads(final.read_text())['status'];has_receipt=True;break
   if not has_receipt:
    a['status']=a['condition_terminal']='TIMED_OUT_NO_RECEIPT' if a['launch_error'] and a['launch_error']['type']=='TimeoutExpired' else 'CONTROLLER_FAILED_WITHOUT_RECEIPT'
    a['evidence_missing']=['controller-final.json','controller-failed.json'];a['cleanup_result']='NOT_OBSERVED_NO_CHILD_RECEIPT'
  assert result.returncode==0,'attempt failed; no retry or replacement'
  assert a['status']=='FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS'
 assert time.time()<expiry
 status='FOUR_CONDITION_BLOCK_RELEASE60_PASS'
except BaseException as failure:
 error={'type':type(failure).__name__,'message':str(failure)}
 raise
finally:
 final={'status':status,'error':error,'block_id':b['block_id'],'logical_block_id':b.get('logical_block_id',b['block_id']),'execution_revision':b.get('execution_revision'),'source_commit':b['source_commit'],'protocol_sha256':b['protocol_sha256'],'attempts':ledger,'planned_attempts':b['planned_attempts'],'success_rate_denominator':'launch_requested','launch_requested_count':sum(a['launch_requested'] for a in ledger),'runtime_evidence_created_count':sum(a['runtime_evidence_created'] for a in ledger),'attempted_legacy_semantics':'runtime evidence directory created; not the success-rate denominator'}
 fd=os.open(claims/(b['block_id']+'-final.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(final,f);f.flush();os.fsync(f.fileno())
"""


def render_block(protocol, block_number, entries, source_commit):
    validate_protocol(protocol)
    planned=[a for a in protocol['attempts'] if a['block']==block_number]
    assert len(planned)==len(entries)==4
    assert source_commit is None or re.fullmatch('[0-9a-f]{40}',source_commit)
    for row,entry in zip(planned,entries):
        assert execution_mapping(protocol)[0][row['attempt_id']]==entry['run_id']
        assert entry.get('logical_attempt_id',row['attempt_id'])==row['attempt_id']
        assert pathlib.Path(entry['command_path']).is_absolute()
        assert re.fullmatch('[0-9a-f]{64}',entry['command_sha256'])
    compile(BLOCK_BODY,'<four-condition-block>','exec')
    return {'status':'CANDIDATE_BLOCK_NOT_FROZEN_NOT_EXECUTABLE','block_id':execution_mapping(protocol)[1][str(block_number)],'logical_block_id':'kimi-four-v1-b%02d'%block_number,'execution_revision':copy.deepcopy(protocol.get('execution_revision')),
            'source_commit':source_commit,'success_rate_denominator':'launch_requested','attempts':copy.deepcopy(entries),'planned_attempts':copy.deepcopy(protocol['attempts']),
            'protocol_sha256':PROTOCOL_SHA,'runs_root':protocol['resource_budget'].get('runs_root','/home/boyjiang/kimi-new/evidence/runs'),
            'condition_max_seconds':7200,'parent_overhead_seconds':120,'full_max_seconds':28920,
            'claims_root':'/home/boyjiang/kimi-new/evidence/formal-claims-root',
            'block_python':BLOCK_BODY,'block_python_sha256':hashlib.sha256(BLOCK_BODY.encode()).hexdigest(),
            'limits':'Requires root-owned claims directory, independent root unit coverage28920, fresh full-block authorized booking/resource admission, frozen committed child specs and independent review. Any child failure stops permanently without retry.'}


def committed_source(source_commit, declared):
    assert re.fullmatch("[0-9a-f]{40}",source_commit)
    def git(*args):
        return subprocess.check_output(["git",*args],cwd=base.ROOT)
    assert git("rev-parse","HEAD").decode().strip()==source_commit
    assert not git("status","--porcelain=v1","--untracked-files=no").strip(),"tracked source is dirty"
    branch=git("symbolic-ref","--short","HEAD").decode().strip()
    assert git("ls-remote","--exit-code","origin","refs/heads/"+branch).decode().split()[0]==source_commit,"remote branch is not synchronized"
    required=set(declared)|{"sllm_store/tests/prepare_kimi_four_condition.py","sllm_store/tests/prepare_native_stop_diagnostic.py","sllm_store/tests/file_cache_state.py","sllm_store/tests/python/test_prepare_kimi_four_condition.py"}
    for relative in required:
        assert git("show",source_commit+":"+relative)==(base.ROOT/relative).read_bytes(),("uncommitted declared source",relative)
    return source_commit


def validate_frozen_protocol(protocol, source_commit):
    validate_protocol(protocol)
    assert protocol['status'].startswith('FROZEN_') and 'DRAFT' not in protocol['status']
    assert protocol['final_deploy_commit']==source_commit
    assert protocol['resource_budget']['per_condition_max_seconds']==7200
    assert protocol['resource_budget']['per_complete_block_max_seconds']==28920
    assert protocol['resource_budget']['parent_overhead_seconds']==120
    assets=protocol['assets']
    assert assets['route']=='local_nvme'
    assert assets['safetensors']==SF_HOST and assets['store_archive']==STORE_ARCHIVE
    assert assets.get('store',STORE_ARCHIVE+'/vllm/kimi-k2.6-sf-reference')==STORE_ARCHIVE+'/vllm/kimi-k2.6-sf-reference'
    assert protocol['resource_budget']['runs_root']==RUNS_ROOT
    assert pathlib.Path(assets['input_inventory_path']).is_absolute()
    assert assets['input_inventory_path']!=str(INVENTORY) and assets['input_inventory_sha256']!=INVENTORY_SHA,'old NFS inventory cannot admit local assets'
    assert re.fullmatch('[0-9a-f]{64}',assets['input_inventory_sha256'])
    assert protocol['events']['primary_http_endpoint']=='received_ns'
    assert protocol['events']['primary_start_event']=='same_http_client_registration_command_ns'
    # Keep the preregistered orders, own gold and statistics intact.
    original=checked(PROTOCOL,PROTOCOL_SHA)
    for key in ('conditions','design','attempts'):
        assert protocol[key]==original[key],('preregistered contract changed',key)
    statistics=copy.deepcopy(protocol['statistics'])
    assert statistics.pop('success_rate_denominator',None)=='launch_requested','launch-requested success-rate denominator must be preregistered'
    assert statistics==original['statistics'],'other preregistered statistics changed'
    correctness=copy.deepcopy(protocol['correctness'])
    observability=correctness['observability']
    assert isinstance(observability,str) and all(term in observability for term in ('logprobs=1','return_token_ids','trace')),'explicit observability delta disclosure required'
    for request in correctness['requests']:
        assert type(request.get('logprobs')) is int and request.pop('logprobs')==1,'GLM-aligned logprobs=1 required'
    correctness['observability']=original['correctness']['observability']
    assert correctness==original['correctness'],'only logprobs=1 and its observability disclosure may change correctness'




def validate_diagnostic_fingerprint(record, index, index_sha256):
    fp=record['diagnostic_value_fingerprint']
    assert fp['schema']=='kimi-load-boundary-value-fingerprint-v1' and fp['stage']=='before_first_forward'
    assert fp['status']=='PASS' and fp['coverage_complete'] is True
    assert fp.get('error_type') is None and fp.get('error') is None
    assert fp['chunk_bytes']==67108864 and fp['budget_seconds']==600
    assert math.isfinite(fp['elapsed_seconds']) and 0<=fp['elapsed_seconds']<=600
    assert fp['index_sha256']==index_sha256
    assert fp['index_path']=='/store/vllm/kimi-k2.6-sf-reference/rank_'+str(record['tp_rank'])+'/tensor_index.json'
    tensors={row['name']:row for row in record['registered_tensors']}
    assert len(tensors)==len(record['registered_tensors'])
    def signature(row):
        widths={**dict.fromkeys(('torch.bool','torch.uint8','torch.int8','torch.float8_e4m3fn','torch.float8_e5m2','torch.float8_e4m3fnuz','torch.float8_e5m2fnuz'),1),**dict.fromkeys(('torch.int16','torch.uint16','torch.float16','torch.bfloat16'),2),**dict.fromkeys(('torch.int32','torch.uint32','torch.float32'),4),**dict.fromkeys(('torch.int64','torch.uint64','torch.float64','torch.complex64'),8),'torch.complex128':16}
        assert row['dtype'] in widths,'unsupported fingerprint dtype'
        # storage_id is already canonicalized by (device, untyped-storage baseptr).
        return (row['device'],row['storage_id'],row['storage_offset']*widths[row['dtype']],row['bytes'],row['dtype'],tuple(row['shape']),tuple(row['stride']))
    def metadata(row):
        return (row['dtype'],row['shape'],row['stride'],row['numbytes'])
    groups={name:{row['name']:row for row in fp[name]} for name in ('persistent','runtime_buffers','aliases')}
    assert all(len(groups[name])==len(fp[name]) for name in groups)
    names=[name for group in groups.values() for name in group]
    assert len(names)==len(set(names)) and set(names)==set(tensors),'fingerprint registered coverage incomplete'
    assert set(groups['persistent'])==set(index),'fingerprint index coverage incomplete'
    index_ranges={signature(tensors[name]) for name in index}
    expected_aliases={name for name,t in tensors.items() if name not in index and t['bytes']>0 and signature(t) in index_ranges}
    expected_runtime=set(tensors)-set(index)-expected_aliases
    assert set(groups['aliases'])==expected_aliases and set(groups['runtime_buffers'])==expected_runtime
    for group,items in groups.items():
        for name,row in items.items():
            t=tensors[name]
            assert row['kind']==t['kind'] and metadata(row)==(t['dtype'],t['shape'],t['stride'],t['bytes'])
            assert re.fullmatch('[0-9a-f]{64}',row['sha256'])
            if row['numbytes']==0:assert row['sha256']==hashlib.sha256(b'').hexdigest() and 'alias_of' not in row
            if group=='persistent':
                entry=index[name]
                assert len(entry)==5 and metadata(row)==(entry[4],entry[2],entry[3],entry[1])
            elif group=='runtime_buffers':assert row['kind']=='buffer','nonindex unaliased parameter'
            if group=='aliases' or 'alias_of' in row:
                target=row['alias_of'];targets=groups['persistent'] if group=='aliases' else items
                assert target!=name and target in targets and signature(t)==signature(tensors[target])
                assert row['sha256']==targets[target]['sha256']
    counts=fp['counts']
    assert counts['index_keys']==len(index) and counts['registered_names']==len(tensors)
    assert all(counts[k]==len(groups[k]) for k in groups)
    ranges={signature(t) for t in tensors.values() if t['bytes']>0}
    assert counts['unique_hashed_ranges']==len(ranges)
    assert counts['hashed_bytes']==sum(key[3] for key in ranges)
    assert counts['chunks']==sum((key[3]+67108863)//67108864 for key in ranges)
    assert all(type(counts[k]) is int and counts[k]>=0 for k in ('unique_hashed_ranges','hashed_bytes','chunks'))
    return {'rank':record['tp_rank'],'index_sha256':index_sha256,'counts':counts,'elapsed_seconds':fp['elapsed_seconds']}


DIAGNOSTIC_FUNCTIONS = """
diagnostic_observations={}
def diagnostic_response(row,want,index,life):
 record_http_sample(row,want,index)
 response=row['response'];assert row['status']==200 and len(response['choices'])==1
 choice=response['choices'][0];assert isinstance(choice['message']['content'],str);entries=choice['logprobs'];ids=[]
 for entry in entries:
  assert len(entry)==want['logprobs'] and sorted(v['rank'] for v in entry.values())==list(range(1,want['logprobs']+1))
  assert all(math.isfinite(v['logprob']) for v in entry.values())
  ids.append(int(next(k for k,v in entry.items() if v['rank']==1)))
 usage=response['usage'];assert usage=={'prompt_tokens':len(want['input_tokens']),'completion_tokens':len(ids),'total_tokens':len(want['input_tokens'])+len(ids)}
 assert 0<len(ids)<=want['max_tokens']
 if want['case'].startswith('fixed32'):assert len(ids)==32 and choice['finish_reason']=='length'
 else:assert choice['finish_reason'] in ('stop','length')
 expected=want['expected'];match=ids==expected['token_ids'] and choice['message']['content']==expected['content'] and choice['finish_reason']==expected['finish_reason']
 diagnostic_observations[want['case']]={'actual_ids':ids,'gold_match':match,'request_id':row['request_id'],'top2':entries}
 complete=validated_http_events(row,want,index)
 save('diagnostic-response-'+want['case']+'.json',{'actual':row,'expected':want,'gold_false':not match,'observation':diagnostic_observations[want['case']],'validated_endpoint':complete})
 return complete
def verify_diagnostic_records(records):
 summaries=[]
 for record in records:
  path=pathlib.Path(s['archive'])/'vllm'/s['config']['model']/('rank_'+str(record['tp_rank']))/'tensor_index.json'
  raw=path.read_bytes();assert len(raw)<=4<<20
  receipt=pathlib.Path(s['conversion_receipt']['path']);assert hashlib.sha256(receipt.read_bytes()).hexdigest()==s['conversion_receipt']['sha256']
  expected=next(row['index_sha256'] for row in json.loads(receipt.read_text())['archive_validation']['ranks'] if row['rank']==record['tp_rank'])
  assert hashlib.sha256(raw).hexdigest()==expected,'diagnostic frozen index drift'
  summaries.append(validate_diagnostic_fingerprint(record,json.loads(raw),hashlib.sha256(raw).hexdigest()))
 assert len(summaries)==8 and {v['rank'] for v in summaries}==set(range(8))
 save('diagnostic-fingerprint-validation.json',{'records':summaries,'coverage':'all registered persistent/index aliases/runtimebuffers only; native nonregistered workspaces excluded; before-first-forward only'})
def diagnostic_qualification(gates,terminal,cleanup,metrics):
 complete=all(gates.get(k,{}).get('status')=='PASS' for k in ('cache','clock','ownership')) and terminal.get('normal') is True and cleanup.get('normal') is True and isinstance(cleanup.get('quiet_seconds'),(int,float)) and 60<=cleanup['quiet_seconds']<float('inf')
 expected=len(s['correctness']['requests']) if 's' in globals() else 5
 complete=complete and len(diagnostic_observations)==expected and all(row.get('raw_interval_status')=='NUMERIC' and isinstance(row.get('stdout_reference'),dict) for row in metrics.values()) and len(metrics)==expected
 return {'primary_sample_qualified':False,'diagnostic_observation_complete':bool(complete),'gold_matches':{k:v['gold_match'] for k,v in diagnostic_observations.items()},'invalid_reasons':[] if complete else ['diagnostic technical evidence incomplete'],'rule':'DIAGNOSTIC_OBSERVATION_NOT_FORMAL; strict gold mismatches preserved, no formal qualification'}
"""


def render_diagnostic(protocol, original_spec, original_command, source_pins, overlay_path=None, overlay_sha256=None, *, repeatability=False):
    if repeatability:
        overlay_path,overlay_sha256=base.NEW_RUNNER,base.NEW_RUNNER_SHA
    assert pathlib.Path(overlay_path).is_absolute() and re.fullmatch('[0-9a-f]{64}',overlay_sha256)
    assert overlay_sha256==(base.NEW_RUNNER_SHA if repeatability else DIAGNOSTIC_OVERLAY_SHA) and base.digest(pathlib.Path(overlay_path))==overlay_sha256
    if not repeatability:assert base.digest(DIAGNOSTIC_MANIFEST)==DIAGNOSTIC_MANIFEST_SHA
    patch=base.ROOT/'sllm_store/vllm_patch/sllm_load.patch';assert base.digest(patch)==DIAGNOSTIC_PATCH_SHA
    compile(pathlib.Path(overlay_path).read_text(),'<diagnostic-overlay>','exec')
    outputs=[]
    cases=[('sfhot-repeatability','safetensors_disk_hot')] if repeatability else [('sf1','safetensors_disk_hot'),('sf2','safetensors_disk_hot'),('storecold','store_disk_cold'),('poolhit','store_pool_hit')]
    for label,condition in cases:
        run='kimi-sfhot-repeatability-20261107-once' if repeatability else 'kimi-diagnostic-value-20261107-'+label
        logical=next(a['attempt_id'] for a in protocol['attempts'] if a['condition']==condition)
        spec,command=render(protocol,original_spec,original_command,logical,source_pins,diagnostic_run_id=run)
        index=command['argv'].index('-c')+1;body=command['argv'][index]
        spec.update(status='DIAGNOSTIC_CANDIDATE_NOT_EXECUTABLE_NOT_FORMAL',primary_sample_qualified=False,logical_attempt_id=None,execution_revision=None,attempt={'attempt_id':run,'diagnostic_case':label,'condition':condition},planned_attempts=[],diagnostic=True)
        requests=copy.deepcopy(protocol['correctness']['requests'])
        for number in ((1,) if repeatability else (1,2)):
            row=copy.deepcopy(requests[2]);row['case']='fixed32-repeat'+str(number);requests.append(row)
        for request_index,row in enumerate(requests):row['logprobs']=1 if repeatability and request_index<3 else 2
        spec['correctness']=copy.deepcopy(spec['correctness']);spec['correctness']['requests']=requests
        spec['actual_request_parameters']=[{'case':row['case'],**{key:row[key] for key in ('input_tokens','temperature','top_p','top_k','min_p','seed','n','max_tokens','ignore_eos')},'logprobs':row['logprobs']} for row in requests]
        spec['sfhot_repeatability']=repeatability;spec['fingerprint_required']=not repeatability
        spec['request_observability']='DIAGNOSTIC_NOT_FORMAL: first three original requests LP1, fourth same-shape fixed32 LP2 independent observation, no retroactive LP2 margin claim' if repeatability else 'DIAGNOSTIC_NOT_FORMAL: all five requests LP2, actual rank1 IDs checked against trace; strict gold differences preserved'
        spec['correctness']['observability']=spec['request_observability']
        spec['fingerprint_scope']='NOT_APPLICABLE: same-condition SFhot repeatability, no value fingerprint or D2H' if repeatability else 'before-first-forward,600seconds INCLUDED within firstHTTP1500; no after/nativeworkspace equivalence claim'
        for role,request in spec['requests'].items():
            if not repeatability:request['env'].update(SLLM_DIAGNOSTIC_VALUE_FINGERPRINT='1',SLLM_CAPTURE_LOAD_EVIDENCE='1')
            else:assert not request['env'].get('SLLM_DIAGNOSTIC_VALUE_FINGERPRINT')
            matches=[mount for mount in request['mounts'] if mount['Target'].endswith('/v1/worker/gpu_model_runner.py')];assert len(matches)==1
            matches[0]['Source']=str(overlay_path)
        active=[item for item in spec['overlays'] if item['target'].endswith('/v1/worker/gpu_model_runner.py')]
        assert len(active)==1 and active[0]['sha256']==base.NEW_RUNNER_SHA
        active[0].update(source=str(overlay_path),sha256=overlay_sha256)
        spec['code_pins'][str(overlay_path)]=overlay_sha256
        if not repeatability:spec['code_pins'][str(DIAGNOSTIC_MANIFEST)]=DIAGNOSTIC_MANIFEST_SHA
        spec['code_pins'][str(patch)]=DIAGNOSTIC_PATCH_SHA
        spec['role_bodies']['http']=spec['role_bodies']['http'].replace("assert row.get('child_gold_validation_pass',True)","assert row['status']==200").replace("print(json.dumps(row),flush=True)","row.update(diagnostic=True,experiment_scope='DIAGNOSTIC_OBSERVATION_NOT_FORMAL',primary_sample_qualified=False);print(json.dumps(row),flush=True)")
        imports=spec['role_bodies']['imports']
        imports+="\nimport importlib.metadata,importlib.util,hashlib,pathlib\nidentity={}\nfor package in ('flashinfer-python','flashinfer-cubin'):\n try:identity[package]=importlib.metadata.version(package)\n except importlib.metadata.PackageNotFoundError:identity[package]='NOT_INSTALLED'\norigin=importlib.util.find_spec('flashinfer').origin;identity['module_origin']=origin;identity['module_sha256']=hashlib.sha256(pathlib.Path(origin).read_bytes()).hexdigest();print('DIAGNOSTIC_FLASHINFER_IDENTITY '+json.dumps(identity),flush=True)\n"
        spec['role_bodies']['imports']=imports
        for name in ('http','imports'):spec['role_body_sha256'][name]=hashlib.sha256(spec['role_bodies'][name].encode()).hexdigest()
        body=body.replace("s['status']=='FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW'","s['status']=='FROZEN_DIAGNOSTIC_OBSERVATION_NOT_FORMAL'")
        save_node=next(n for n in ast.parse(body).body if isinstance(n,ast.FunctionDef) and n.name=='save')
        old_save=ast.get_source_segment(body,save_node)
        lines=old_save.splitlines();lines.insert(1," value={**value,'experiment_scope':'DIAGNOSTIC_OBSERVATION_NOT_FORMAL','primary_sample_qualified':False} if isinstance(value,dict) else {'experiment_scope':'DIAGNOSTIC_OBSERVATION_NOT_FORMAL','primary_sample_qualified':False,'items':value}")
        body=body.replace(old_save,'\n'.join(lines),1)
        body=replace_function(body,'check_response',"def check_response(row,want,index,life):return diagnostic_response(row,want,index,life)")
        body=replace_function(body,'sample_qualification',"def sample_qualification(gates,terminal,cleanup,metrics):return diagnostic_qualification(gates,terminal,cleanup,metrics)")
        count=len(requests)
        body=body.replace("gold=s['correctness']['requests'];assert len(gold)==3","gold=s['correctness']['requests'];assert len(gold)=="+str(count))
        body=body.replace('logprobs=1)',"logprobs=want['logprobs'])")
        body=body.replace("tokens=json.loads(execute('head','tokens',[ips['worker']],seconds=35))", "token_deadline=create_end if index==0 else aab_end;token_seconds=min(35,int(token_deadline-time.monotonic()));assert token_seconds>0;tokens=json.loads(execute('head','tokens',[ips['worker']],seconds=token_seconds));assert time.monotonic()<token_deadline,'diagnostic trace exceeded request budget'")
        body=body.replace("collect_structured_events(router_positions,positions,tokens,backend_owner);mark_sample_gate", "collect_structured_events(router_positions,positions,tokens,backend_owner);assert time.monotonic()<aab_end,'diagnostic ready180 cap exceeded';mark_sample_gate")
        body=body.replace("history.append(want['input_tokens']+want['expected']['token_ids'])","history.append(want['input_tokens']+diagnostic_observations[want['case']]['actual_ids'])")
        if not repeatability:body=body.replace('records=records_since(positions);assert len(records)==8;','records=records_since(positions);assert len(records)==8;verify_diagnostic_records(records);')
        body=body.replace("mark_sample_gate('gold',True,'all3 child/parent gold and cumulative exact trace IDs passed')","mark_sample_gate('gold',all(v['gold_match'] for v in diagnostic_observations.values()),'strict gold differences observed without changing gold; cumulative actual trace IDs verified')")
        body=body.replace("assert len(first)==3 and {r['request_id'] for r in first}=={run+'-'+str(i) for i in range(3)}","assert len(first)=="+str(count)+" and {r['request_id'] for r in first}=={run+'-'+str(i) for i in range("+str(count)+")}")
        body=body.replace('_http_complete_correct','_http_complete_observed')
        body=body.replace("assert qualification['primary_sample_qualified']","assert qualification['diagnostic_observation_complete']")
        body=body.replace('FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS','DIAGNOSTIC_OBSERVATION_RELEASE60_COMPLETE_NOT_FORMAL').replace('NEW_PROTOCOL_EXIT60_L1_FAILED_RETAINED_NO_RETRY','DIAGNOSTIC_OBSERVATION_FAILED_RETAINED_NOT_FORMAL')
        marker="try:\n save('storage-route-admission.json',storage_route_proof)"
        assert body.count(marker)==1
        functions=DIAGNOSTIC_FUNCTIONS
        if repeatability:
            unused=next(node for node in ast.parse(functions).body if isinstance(node,ast.FunctionDef) and node.name=='verify_diagnostic_records')
            functions=functions.replace(ast.get_source_segment(functions,unused),'',1)
            functions=functions.replace("'gold_match':match,","'gold_match':match,'strict_gold_match':match,'known_fixed32_variant_match':want['case'].startswith('fixed32') and len(ids)==32 and len(expected['token_ids'])==32 and all(value==expected['token_ids'][at] if at!=11 else value in (47516,12635) for at,value in enumerate(ids)),")
        body=body.replace(marker,(inspect.getsource(validate_diagnostic_fingerprint) if not repeatability else '')+functions+'\n'+marker,1)
        compile(body,'<diagnostic-root>','exec');command['argv'][index]=body;command['root_python_sha256']=hashlib.sha256(body.encode()).hexdigest();command['status']=spec['status'];command.update(diagnostic=True,primary_sample_qualified=False)
        outputs.append((spec,command))
    return outputs



def prepare_diagnostic(protocol, original_spec, original_command, pins, final_commit, protocol_path, protocol_sha, overlay_path=None, overlay_sha=None, *, repeatability=False):
    generated=render_diagnostic(protocol,original_spec,original_command,pins,overlay_path,overlay_sha,repeatability=repeatability)
    if repeatability:overlay_sha=base.NEW_RUNNER_SHA
    outputs=[];entries=[]
    for spec,command in generated:
        spec.update(source_commit=final_commit,protocol_path=str(protocol_path),protocol_sha256=protocol_sha)
        if final_commit:spec['status']=command['status']='FROZEN_DIAGNOSTIC_OBSERVATION_NOT_FORMAL'
        spec['code_pins'][str(protocol_path)]=protocol_sha
        version='diagnostic-v1-'+protocol_sha[:12]+'-'+overlay_sha[:12]
        sp=base.EVIDENCE/(spec['run_id']+'-'+version+'-spec.json');cp=sp.with_name(sp.name.replace('-spec.json','-command.json'))
        command['argv'][-3]=str(sp);sb=json_bytes(spec);command['argv'][-2]=command['spec_sha256']=hashlib.sha256(sb).hexdigest();cb=json_bytes(command)
        outputs.extend(((sp,sb),(cp,cb)));entries.append({'run_id':spec['run_id'],'condition':spec['condition'],'command_path':str(cp),'command_sha256':hashlib.sha256(cb).hexdigest()})
    if repeatability:
        assert len(outputs)==2 and all(not path.exists() for path,_ in outputs),'repeatability identity already prepared'
        for path,data in outputs:base.write_new(path,data)
        return outputs
    body=BLOCK_BODY.replace('FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW','FROZEN_DIAGNOSTIC_OBSERVATION_NOT_FORMAL').replace('FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS','DIAGNOSTIC_OBSERVATION_RELEASE60_COMPLETE_NOT_FORMAL').replace('FOUR_CONDITION_BLOCK_RELEASE60_PASS','DIAGNOSTIC_OBSERVATION_BLOCK_RELEASE60_COMPLETE_NOT_FORMAL').replace('FAILED_RETAINED_NO_RETRY','DIAGNOSTIC_OBSERVATION_FAILED_RETAINED_NOT_FORMAL')
    body=body.replace("final={'status':status,","final={'diagnostic':True,'primary_sample_qualified':False,'status':status,")
    compile(body,'<diagnostic-block>','exec')
    bundle={'status':'FROZEN_DIAGNOSTIC_OBSERVATION_NOT_FORMAL' if final_commit else 'DIAGNOSTIC_CANDIDATE_NOT_EXECUTABLE_NOT_FORMAL','diagnostic':True,'primary_sample_qualified':False,'block_id':'kimi-diagnostic-value-20261107-block','source_commit':final_commit,'protocol_sha256':protocol_sha,'attempts':entries,'planned_attempts':[spec['attempt'] for spec,_ in generated],'full_max_seconds':28920,'condition_max_seconds':7200,'claims_root':'/home/boyjiang/kimi-new/evidence/formal-claims-root','runs_root':generated[0][0]['runs_root'],'block_python':body,'block_python_sha256':hashlib.sha256(body.encode()).hexdigest(),'scope':'DIAGNOSTIC_OBSERVATION_NOT_FORMAL; four fresh conditions; no formal attempt substitution or primary qualification'}
    bp=base.EVIDENCE/('kimi-diagnostic-value-20261107-'+version+'-bundle.json');bb=json_bytes(bundle)
    argv=['/usr/bin/systemd-run','--unit='+bundle['block_id']+'-controller.service','--property=Type=exec','--property=RuntimeMaxSec=28917','--property=TimeoutStopSec=1','--property=Restart=no','--property=KillMode=control-group','--property=SendSIGKILL=yes','--property=FinalKillSignal=SIGKILL','/usr/bin/python3','-I','-c',body,str(bp),hashlib.sha256(bb).hexdigest(),'FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED']
    outputs.extend(((bp,bb),(bp.with_name(bp.name.replace('-bundle.json','-root-command.json')),json_bytes({'status':bundle['status'],'diagnostic':True,'primary_sample_qualified':False,'block_max_seconds':28920,'bundle_sha256':hashlib.sha256(bb).hexdigest(),'argv':argv}))))
    assert all(not path.exists() for path,_ in outputs),'diagnostic output identity already used'
    for path,data in outputs:base.write_new(path,data)
    return outputs


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+chr(10)).encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--attempt-id');selection.add_argument('--block',type=int,choices=range(1,11));selection.add_argument('--diagnostic',action='store_true');selection.add_argument('--sfhot-repeatability',action='store_true')
    parser.add_argument('--diagnostic-overlay',type=pathlib.Path);parser.add_argument('--diagnostic-overlay-sha')
    parser.add_argument('--source-commit')
    parser.add_argument('--protocol',type=pathlib.Path,default=PROTOCOL)
    parser.add_argument('--protocol-sha',default=PROTOCOL_SHA)
    args=parser.parse_args()
    assert re.fullmatch('[0-9a-f]{64}',args.protocol_sha)
    protocol=checked(args.protocol,args.protocol_sha);validate_protocol(protocol)
    inventory_path=pathlib.Path(protocol['assets'].get('input_inventory_path',str(INVENTORY)))
    inventory_sha=protocol['assets'].get('input_inventory_sha256',INVENTORY_SHA)
    expected_roots=None
    if protocol['assets'].get('route')=='local_nvme':
        expected_roots={'safetensors':protocol['assets']['safetensors'],'store':str(pathlib.Path(protocol['assets']['store_archive'])/'vllm/kimi-k2.6-sf-reference')}
    validate_inventory(checked(inventory_path,inventory_sha),expected_roots)
    original_spec=checked(base.EXIT60_BASE_SPEC,base.EXIT60_BASE_SPEC_SHA)
    original_command=checked(base.EXIT60_BASE_COMMAND,base.EXIT60_BASE_COMMAND_SHA)
    pins={relative:base.digest(base.ROOT/relative) for relative in original_spec['native_source_pins']}
    pins['sllm/logger.py']=base.digest(base.ROOT/'sllm/logger.py')
    pins['sllm_store/tests/file_cache_state.py']='3340bf6a700d5e6477137ecf937235a07b76d0e5b95044758b5a0c3f0782b4f8'
    final_commit=None
    if args.source_commit:
        validate_frozen_protocol(protocol,args.source_commit)
        declared=set(pins)
        declared.update(pathlib.Path(original_spec[key]).relative_to(base.ROOT).as_posix() for key in ('guard','observer'))
        declared.add('sllm_store/tests/gpu512_once_controller.py')
        if args.diagnostic or args.sfhot_repeatability:declared.add('sllm_store/vllm_patch/sllm_load.patch')
        declared.update(pathlib.Path(path).relative_to(base.ROOT).as_posix() for path in original_spec['code_pins'] if pathlib.Path(path).is_relative_to(base.ROOT) and pathlib.Path(path).suffix=='.py' and 'build' not in pathlib.Path(path).relative_to(base.ROOT).parts)
        final_commit=committed_source(args.source_commit,declared)
    if args.diagnostic or args.sfhot_repeatability:
        if not args.sfhot_repeatability:assert args.diagnostic_overlay and args.diagnostic_overlay_sha,'frozen diagnostic overlay path and SHA required'
        files=prepare_diagnostic(protocol,original_spec,original_command,pins,final_commit,args.protocol,args.protocol_sha,args.diagnostic_overlay,args.diagnostic_overlay_sha,repeatability=args.sfhot_repeatability)
        print(json.dumps({'status':'DIAGNOSTIC_OBSERVATION_PREPARED_NOT_FORMAL','files':[{'path':str(path),'sha256':hashlib.sha256(data).hexdigest()} for path,data in files]}));return
    attempts=[a for a in protocol['attempts'] if a['block']==args.block] if args.block else [next(a for a in protocol['attempts'] if a['attempt_id']==args.attempt_id)]
    outputs=[];entries=[]
    # Include the protocol hash in new filenames so reviewed versions never overwrite.
    version='formal-v3-'+args.protocol_sha[:12]
    for attempt in attempts:
        spec,command=render(protocol,original_spec,original_command,attempt['attempt_id'],pins)
        spec.update(audited_events_source_commit=SOURCE,source_commit=final_commit,protocol_path=str(args.protocol),protocol_sha256=args.protocol_sha)
        if final_commit:spec['status']=command['status']='FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW'
        spec['source_pin_status']='COMMITTED_REMOTE_VERIFIED' if final_commit else 'CANDIDATE_CURRENT_BYTES_NOT_SOURCE_ADMISSION'
        spec['code_pins'][str(args.protocol)]=args.protocol_sha
        sp=base.EVIDENCE/(spec['run_id']+'-'+version+'-spec.json')
        cp=base.EVIDENCE/(spec['run_id']+'-'+version+'-command.json')
        command['argv'][-3]=str(sp);sb=json_bytes(spec)
        command['argv'][-2]=command['spec_sha256']=hashlib.sha256(sb).hexdigest()
        cb=json_bytes(command)
        outputs.extend(((sp,sb),(cp,cb)))
        entries.append({'run_id':spec['run_id'],'logical_attempt_id':attempt['attempt_id'],'condition':attempt['condition'],'command_path':str(cp),'command_sha256':hashlib.sha256(cb).hexdigest()})
    if args.block:
        bundle=render_block(protocol,args.block,entries,final_commit);bundle['protocol_sha256']=args.protocol_sha
        if final_commit:bundle['status']='FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW'
        bp=base.EVIDENCE/(bundle['block_id']+'-'+version+'-bundle.json');bb=json_bytes(bundle)
        unit=bundle['block_id']+'-formal-controller.service'
        argv=['/usr/bin/systemd-run','--unit='+unit,'--property=Type=exec','--property=RuntimeMaxSec=28917','--property=TimeoutStopSec=1','--property=Restart=no','--property=KillMode=control-group','--property=SendSIGKILL=yes','--property=FinalKillSignal=SIGKILL','/usr/bin/python3','-I','-c',BLOCK_BODY,str(bp),hashlib.sha256(bb).hexdigest(),'FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED']
        launch={'status':bundle['status'],'block_max_seconds':28920,'parent_overhead_seconds':120,'bundle_sha256':hashlib.sha256(bb).hexdigest(),'argv':argv}
        outputs.extend(((bp,bb),(bp.with_name(bp.name.replace('-bundle.json','-root-command.json')),json_bytes(launch))))
    assert all(not path.exists() for path,_ in outputs),'prepared output already exists'
    for path,data in outputs:base.write_new(path,data)
    print(json.dumps({'status':'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW' if final_commit else 'CANDIDATE_NOT_EXECUTABLE','attempts':len(attempts),'files':[{'path':str(path),'sha256':hashlib.sha256(data).hexdigest()} for path,data in outputs]}))


if __name__=='__main__':
    main()
