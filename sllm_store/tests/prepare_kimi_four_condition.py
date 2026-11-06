"""Prepare four-condition candidates only; no service, cache or model execution."""
import argparse
import ast
import copy
import hashlib
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
 save('event-'+name+'.json',{'event':name,'monotonic':time.monotonic(),'monotonic_ns':time.monotonic_ns(),'wall':time.time(),'clock_identity':clock_identity,'source_commit':s['source_commit'],'protocol_sha256':s['protocol_sha256'],'run_id':run,'attempt':s['attempt'],'condition':s['condition']})
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

def render(protocol, spec, command, attempt_id, source_pins):
    validate_protocol(protocol)
    attempt = next(a for a in protocol["attempts"] if a["attempt_id"] == attempt_id)
    assert re.fullmatch(r"kimi-four-v1-b[0-9]{2}-[a-z_]+", attempt_id)
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
    spec.update(run_id=attempt_id, condition=attempt["condition"], status="CANDIDATE_NOT_FROZEN_NOT_EXECUTABLE",
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
ledger=[{**a,'status':'NOT_STARTED','attempted':False,'result_path':None,'result_sha256':None} for a in b['attempts']]
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
  argv=[argv[0],'--wait',*[x.replace('--property=RemainAfterExit=yes','--property=RemainAfterExit=no') for x in argv[1:-1]],str(expiry)]
  a['status']='START_REQUESTED'
  try:result=subprocess.run(argv,timeout=7205)
  finally:
   a['attempted']=(runs/a['run_id']).is_dir()
   for name in ('controller-final.json','controller-failed.json'):
    final=runs/a['run_id']/name
    if final.exists():
     a.update(result_path=str(final),result_sha256=hashlib.sha256(final.read_bytes()).hexdigest())
     a['status']=json.loads(final.read_text())['status'];break
  assert result.returncode==0,'attempt failed; no retry or replacement'
  assert a['status']=='FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS'
 assert time.time()<expiry
 status='FOUR_CONDITION_BLOCK_RELEASE60_PASS'
except BaseException as failure:
 error={'type':type(failure).__name__,'message':str(failure)}
 raise
finally:
 final={'status':status,'error':error,'block_id':b['block_id'],'source_commit':b['source_commit'],'protocol_sha256':b['protocol_sha256'],'attempts':ledger,'planned_attempts':b['planned_attempts']}
 fd=os.open(claims/(b['block_id']+'-final.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(final,f);f.flush();os.fsync(f.fileno())
"""


def render_block(protocol, block_number, entries, source_commit):
    validate_protocol(protocol)
    planned=[a for a in protocol['attempts'] if a['block']==block_number]
    assert len(planned)==len(entries)==4
    assert source_commit is None or re.fullmatch('[0-9a-f]{40}',source_commit)
    for row,entry in zip(planned,entries):
        assert row['attempt_id']==entry['run_id']
        assert pathlib.Path(entry['command_path']).is_absolute()
        assert re.fullmatch('[0-9a-f]{64}',entry['command_sha256'])
    compile(BLOCK_BODY,'<four-condition-block>','exec')
    return {'status':'CANDIDATE_BLOCK_NOT_FROZEN_NOT_EXECUTABLE','block_id':'kimi-four-v1-b%02d'%block_number,
            'source_commit':source_commit,'attempts':copy.deepcopy(entries),'planned_attempts':copy.deepcopy(protocol['attempts']),
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
    for key in ('conditions','design','attempts','statistics'):
        assert protocol[key]==original[key],('preregistered contract changed',key)
    correctness=copy.deepcopy(protocol['correctness'])
    observability=correctness['observability']
    assert isinstance(observability,str) and all(term in observability for term in ('logprobs=1','return_token_ids','trace')),'explicit observability delta disclosure required'
    for request in correctness['requests']:
        assert type(request.get('logprobs')) is int and request.pop('logprobs')==1,'GLM-aligned logprobs=1 required'
    correctness['observability']=original['correctness']['observability']
    assert correctness==original['correctness'],'only logprobs=1 and its observability disclosure may change correctness'



def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+chr(10)).encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--attempt-id');selection.add_argument('--block',type=int,choices=range(1,11))
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
        declared.update(pathlib.Path(path).relative_to(base.ROOT).as_posix() for path in original_spec['code_pins'] if pathlib.Path(path).is_relative_to(base.ROOT) and pathlib.Path(path).suffix=='.py' and 'build' not in pathlib.Path(path).relative_to(base.ROOT).parts)
        final_commit=committed_source(args.source_commit,declared)
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
        sp=base.EVIDENCE/(attempt['attempt_id']+'-'+version+'-spec.json')
        cp=base.EVIDENCE/(attempt['attempt_id']+'-'+version+'-command.json')
        command['argv'][-3]=str(sp);sb=json_bytes(spec)
        command['argv'][-2]=command['spec_sha256']=hashlib.sha256(sb).hexdigest()
        cb=json_bytes(command)
        outputs.extend(((sp,sb),(cp,cb)))
        entries.append({'run_id':attempt['attempt_id'],'condition':attempt['condition'],'command_path':str(cp),'command_sha256':hashlib.sha256(cb).hexdigest()})
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
