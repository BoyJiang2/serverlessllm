import math
def require(value, message):
    if not value:
        raise ValueError(message)

def check_response(result, expected, case):
    require(len(result['choices']) == 1, 'response choice count differs')
    choice = result['choices'][0]
    tokens = choice['token_ids']
    require(tokens == expected['token_ids'] and choice['message']['content'] == expected['text']
            and choice['finish_reason'] == expected['finish_reason'], 'business gold differs')
    require(tokens and choice['message']['content'].strip(), 'empty business output')
    require(len(choice['logprobs']) == len(tokens), 'logprob coverage differs')
    require(all(math.isfinite(row[str(token)]['logprob'])
                for token, row in zip(tokens, choice['logprobs'])), 'nonfinite logprob')
    if case['ignore_eos']:
        require(case['max_tokens'] == 32 and len(tokens) == 32, 'fixed32 coverage differs')
    else:
        require(choice['finish_reason'] == 'stop', 'natural EOS did not complete')


import pathlib,json,hashlib,sys,io,time,subprocess,datetime,os
assets=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8');src=assets/'code/experiment'
run='sllm-glm51fp8-20261026T225201Z-02cd4cdc';out=assets/'validation/controls'/run
code='16e40e9d3a627d9c60079c7f5a56f7d012c363de';sha='6f456dfaaaca5bc3011ba217e481a7c107ff7186722084a1640a8472e76a4ef0'
sys.path.insert(0,str(src));receipts=[]
def load(path):
 require(path.resolve().is_relative_to(assets.resolve()),'evidence outside assets')
 data=path.read_bytes();entry=dict(path=path.relative_to(assets).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
 if path.name=='monitor-v3-ready.json':entry['captured_utf8']=data.decode('utf-8')
 receipts.append(entry);return json.loads(data)
require(hashlib.sha256((src/'control-input-manifest.json').read_bytes()).hexdigest()==sha,'source manifest differs')
manifest=load(src/'control-input-manifest.json')
for row in manifest['files']:require(hashlib.sha256((src/row['path']).read_bytes()).hexdigest()==row['sha256'],'source differs: '+row['path'])
config=load(out/'run-manifest.json');deployment=load(out/'deployment.json')
require(deployment['run_id']==run and deployment['code_commit']==code and deployment['input_manifest_sha256']==sha,'deployment differs')
require((out/'run-manifest.json').read_bytes()==(src/'run-manifest.json').read_bytes(),'registration differs')
identities={}
for role in ('service13a','headservice13a'):
 identity=load(out/(role+'-identity.json'));identities[role]=identity
 require(identity['run_id']==run and identity['code_commit']==code and identity['manifest_sha256']==sha,'native identity differs')
 info=json.loads(subprocess.check_output(['docker','inspect',identity['container_id']],text=True,timeout=10))[0]
 require(info['Image']==config['image_id'] and info['Config']['Labels'].get('sllm.run_id')==run and info['Config']['Labels'].get('sllm.uid')=='2147' and not info['State']['Running'] and info['State']['ExitCode']==0,'native batch not completed')
 require(not any(identity['spec']['env'].get(k) for k in ('SLLM_VERIFY_WEIGHTS','SLLM_SERVICE_WEIGHT_AUDIT_DIR','SLLM_EXPORT_RAW_STORE','VLLM_ALLOW_INSECURE_SERIALIZATION')),'normal audit flags active')
 load(out/(role+'-dispatch.json'));load(out/(role+'-supervisor-result.json'))
from formal_gate import verify_normal_reference,verify_normal_cache
verify_normal_reference(config,assets)
cache_path=out/'normal-jit-qualification.json';cache=verify_normal_cache(out,src,assets,hashlib.sha256(cache_path.read_bytes()).hexdigest());load(cache_path)
from accept_control13 import validate_store_teardown,validate_native_idle,monitor_summary,expand_cpus
from gpu_identity13 import require_helper_only,require_helper_binding
base=out/'service-bootstrap-service13a';launch=load(base/'store-numa-launch.json');imports=load(base/'runtime-imports.json')
validate_store_teardown(load(base/'service-teardown.json'),launch,identities['service13a'],run,config['runtime_identity']['boot_id'],imports)
pool=load(base/'pool-ready.json');affinity=load(base/'store-affinity-ready.json')
require(imports['run_id']==run and imports['pid']==pool['store_pid']==launch['pid'],'actual Store/pool/launch PID differs')
require({name:row['sha256'] for name,row in imports['modules'].items()}==config['store_import_sha256'],'actual Store module hashes differ')
require(all(pathlib.PurePosixPath(row['path']).parent==pathlib.PurePosixPath('/experiment/code/Store-source-v7/sllm_store') for row in imports['modules'].values()),'actual Store module source differs')
require(pool['rpc_config']==dict(mem_pool_size=pool['pool_bytes'],chunk_size=pool['chunk_bytes']) and pool['listener']['pid']==pool['store_pid'] and pool['listener']['port']==8073 and pool['listener']['listening_sockets'],'actual pool RPC/listener differs')
target=set(launch['target_cpu_affinity'])
require(affinity['pid']==pool['store_pid'] and target and target==expand_cpus(launch['sysfs_cpu_list'].strip())&set(launch['prior_cpu_affinity']),'actual NUMA target identity differs')
require(affinity['tasks'] and all(expand_cpus(row['cpus_allowed_list']) and expand_cpus(row['cpus_allowed_list'])<=target for row in affinity['tasks']),'actual Store thread affinity differs')
require(pool['pool_bytes']==768799145984 and pool['chunk_bytes']==536870912 and pool['rss_bytes']>=pool['pool_bytes'],'actual full common pool missing')
probe=load(out/'service13a-cpu-import-preflight.json')['sigint_probe'];cold=probe['cold_boundary_probe']
require(all(probe.get(key)==value for key,value in dict(run_id=run,head_container_id=identities['headservice13a']['container_id'],image_id=config['image_id'],boot_id=config['runtime_identity']['boot_id'],code_commit=code,manifest_sha256=sha,signal=2,returncode=0,cleanup_marker='cleanup').items()),'actual CPU probe binding differs')
require(probe['atomic_publication']==dict(local_same_filesystem=True,exclusive_collision_rejected=True,temporary_removed=True),'actual exclusive publication proof differs')
vectors=[bytes.fromhex(value) for value in cold['mincore_vectors_hex']]
require(len(vectors)==3 and all(len(value)==cold['pages'] for value in vectors),'full small mincore vector closure differs')
require([sum(v&1 for v in value) for value in vectors]==[cold['initial_resident_pages'],cold['old_order_resident_pages'],cold['fixed_order_resident_pages']]==[0,cold['pages'],0],'actual stale/fixed cold boundary differs')
require(cold['cleanup_complete'] is True and cold['metadata_sha256']==config['metadata']['config.json']['sha256'] and cold['pages']==(cold['metadata_bytes']+os.sysconf('SC_PAGE_SIZE')-1)//os.sysconf('SC_PAGE_SIZE'),'small probe metadata identity differs')
gold_bytes=(assets/'validation/native-gold.json').read_bytes();require(hashlib.sha256(gold_bytes).hexdigest()=='9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0','gold changed');gold=json.loads(gold_bytes)
expected_gpu={};business=[];model_proofs=[]
for kind in ('serverless_llm','safetensors'):
 folder=base/kind;proof_path=folder/'model-result.json';proof=load(proof_path)
 require(proof['status']=='normal-flags gold smoke lifecycle checks passed' and proof['weight_audit_performed'] is False and proof['weight_format']==kind and proof['cycles']==2,'normal format proof differs')
 model_proofs.append(dict(path=proof_path.relative_to(out).as_posix(),sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest()))
 helper=load(folder/'helper-gpu-identity.json');baseline=load(folder/'auxiliary-gpu-baseline.json')
 require_helper_binding(helper,launch,identities['service13a'],config['runtime_identity']['boot_id'])
 for cycle in (1,2):
  receipt=load(folder/('cycle'+str(cycle)+'-gpu-before.json'));before=receipt['host_gpu_snapshot']
  require_helper_only(before,helper,receipt['monotonic_ns'],run,config['runtime_identity']['boot_id'])
  require(before['last_sample_ns'] not in expected_gpu or expected_gpu[before['last_sample_ns']]==before,'conflicting raw GPU snapshot')
  expected_gpu[before['last_sample_ns']]=before
  idle=folder/('cycle'+str(cycle)+'-idle60.jsonl');data=idle.read_bytes();receipts.append(dict(path=idle.relative_to(assets).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()));rows=[json.loads(line) for line in data.splitlines()]
  validate_native_idle(rows,baseline,helper,run,config['runtime_identity']['boot_id'])
  for row in rows:expected_gpu[row['host_gpu_snapshot']['last_sample_ns']]=row['host_gpu_snapshot']
  pss=load(folder/('cycle'+str(cycle)+'-pss.json'))
  require(pss['status']=='completed' and pss['end_ns']>=pss['begin_ns'] and pss['store_pid']==launch['pid'] and any(v['pid']==launch['pid'] and v['smaps_rollup'] for v in pss['processes']),'native PSS incomplete or wrong Store PID')
  for case,expected in zip(gold['requests'],gold['results']):
   response=load(folder/('cycle'+str(cycle)+'-'+case['name']+'.json'));check_response(response,expected,case)
   business.append(dict(format=kind,cycle=cycle,case=case['name'],raw_token_ids=response['choices'][0]['token_ids']))
require(model_proofs==cache['normal_flags_proofs'],'current model hashes differ from JIT qualification')
monitor=(out/'resource-monitor-v12.jsonl').read_bytes();end=monitor.rfind(b'\n')+1;raw=monitor[:end]
class Snapshot:
 def open(self,mode):return io.BytesIO(raw)
summary=monitor_summary(Snapshot(),gpu_context=(launch,identities['service13a'],config['runtime_identity']['boot_id']),expected_gpu=expected_gpu)
rows=[json.loads(line) for line in raw.splitlines()];require(all(not row['foreign_gpu_pids'] for row in rows),'foreign GPU process in raw monitor')
for row in rows:
 events={line.split()[0]:int(line.split()[1]) for line in row['memory_events'].splitlines()}
 require(events.get('oom',-1)==0 and events.get('oom_kill',-1)==0,'owned cgroup OOM in raw monitor')
head_end=load(out/'headservice13a-supervisor-result.json')['end_ns'];post=[row for row in rows if row['sample_begin_ns']>head_end]
require(len(post)>2 and post[-1]['monotonic_ns']-post[0]['monotonic_ns']>=60*10**9,'need at least60s post-normal monitor release')
starts=[load(out/(role+'-dispatch.json'))['dispatch_monotonic_ns'] for role in ('headservice13a','service13a')]
require(summary['first_ns']<=min(starts) and summary['last_ns']>=head_end+60*10**9,'raw monitor missing actual startup/cleanup/release coverage')
for boundary in starts+[head_end]:
 before=[row['monotonic_ns'] for row in rows if row['monotonic_ns']<=boundary];after=[row['monotonic_ns'] for row in rows if row['monotonic_ns']>=boundary]
 require(before and after and boundary-max(before)<=5*10**9 and min(after)-boundary<=5*10**9,'raw monitor boundary gap exceeds5s')
require(all(row['registered_store_helper'] is None and not row['gpu_pids'] for row in post),'native helper or GPU process retained after normal')
for row in post:
 fields=[line.split(',') for line in row['gpu'].splitlines()]
 require(len(fields)==8 and [int(parts[0]) for parts in fields]==list(range(8)) and [parts[1].strip() for parts in fields]==config['runtime_identity']['gpu_uuids'],'normal release GPU UUID coverage incomplete')
 used=[int(parts[3].strip().split()[0]) for parts in fields]
 require(all(0<=value<=16 for value in used) and not row['gpu_processes'].strip(),'normal release GPU memory or raw processes retained')
ready=load(out/'monitor-v3-ready.json');from gpu_identity13 import require_monitor_ready
require_monitor_ready(ready,time.monotonic_ns(),run,config['runtime_identity']['boot_id'])
from preflight_control13 import check
preflight=check(out,continuation=True)
# Capture the additional C21 normal phase evidence for independent local review.
for kind in ('serverless_llm','safetensors'):
 for cycle in (1,2):
  load(base/kind/f'cycle{cycle}-phases.json')
for path in sorted((out/'events-service13a').glob('*.jsonl')):
 data=path.read_bytes()
 receipts.append(dict(path=path.relative_to(assets).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
trace=probe['event_trace_probe']
require(trace['source']['sha256']==config['event_trace_sha256'] and trace['source']['source_code_match'],'actual trace import differs')
require(trace['measurement_event_dir']=='/r/events-service13a' and trace['probe_event_dir']=='/r/service13a-event-trace-probe','trace probe directory differs')
require(all(trace[key] is True for key in ('original_env_verified','isolated','environment_restored')),'trace probe isolation/restoration missing')
require([row['name'] for row in trace['raw_events']]==['backend_engine_create_begin','router_instance_published'],'trace probe branches missing')
require(trace['raw_events'][0]['boot_id']==config['runtime_identity']['boot_id'],'trace process boot differs')
from formal_gate import execution_roots
execution_roots(dict(prior_execution_roots=config['formal_prior_execution_roots'],execution_run_id=run),assets,out)
print(json.dumps(dict(status='normal raw evidence recomputation passed; not R2 approval',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),run_id=run,code_commit=code,manifest_sha256=sha,normal_flags_proofs=model_proofs,jit_cache_proof_sha256=hashlib.sha256(cache_path.read_bytes()).hexdigest(),business=business,normal_idle60_observations=4,monitor=summary,raw_monitor_prefix=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),trailing_partial_bytes_excluded=len(monitor)-len(raw),trailing_partial_sha256=hashlib.sha256(monitor[len(raw):]).hexdigest()),post_normal_release=dict(samples=len(post),span_seconds=(post[-1]['monotonic_ns']-post[0]['monotonic_ns'])/1e9,last_cgroup_memory_stat=post[-1]['cgroup_memory_stat']),sampled_cgroup_peak_bytes=max(row['cgroup_memory_current'] for row in rows),preflight=preflight,artifacts=receipts,weight_scope='normal raw business only; C17 full rank/scale mathematical reference remains separately bound; no new rank audit claim',historical_exit137_cause='unknown; unchanged')))
