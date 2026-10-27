"""Read-only post-run recomputation; no payload tensor reads or model execution."""
import pathlib,json,hashlib,gzip,sys,io,math,datetime
assets=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8');src=assets/'code/experiment'
run='sllm-glm51fp8-20261027T011726Z-28c92313';out=assets/'validation/controls'/run
sys.path.insert(0,str(src))
from accept_control13 import monitor_summary,validate_native_idle,validate_store_teardown,validate_release
from phase_summary import summarize,require_process
from formal_gate import execution_roots
from attempt_state import collect
from cache_contract13 import frozen_paths
from store_cache_inputs import store_inputs
from gpu_identity13 import store_identity
from budget_control13 import formal_batch_budget,iso_time_ns
receipts=[]
def require(value,message):
 if not value:raise ValueError(message)
def load(path):
 data=path.read_bytes();receipts.append(dict(path=path.relative_to(assets).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()));return json.loads(data)
def jsonl(path):
 data=path.read_bytes();receipts.append(dict(path=path.relative_to(assets).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()));return [json.loads(line) for line in data.splitlines()]
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

config=load(out/'run-manifest.json');deployment=load(out/'deployment.json');gate=load(out/'formal-admission.json');boot=config['runtime_identity']['boot_id']
require(deployment['code_commit']==gate['code_commit']=='f176e05607d04999ab28e8ea8cd49b41270cb528','code binding')
require(deployment['input_manifest_sha256']==gate['source_manifest_sha256']=='3eac8c63cdd670a49d872922c11b821d07a0e252f2fbe80f790e169749091fb9','manifest binding')
manifest=load(src/'control-input-manifest.json');require(hashlib.sha256((src/'control-input-manifest.json').read_bytes()).hexdigest()==gate['source_manifest_sha256'],'manifest bytes')
from package_inventory13 import reviewed_files
reviewed_files(src,manifest['files'])
for row in manifest['files']:require(hashlib.sha256((src/row['path']).read_bytes()).hexdigest()==row['sha256'],'runtime source differs')
gold=load(assets/'validation/native-gold.json');require(hashlib.sha256((assets/'validation/native-gold.json').read_bytes()).hexdigest()==gate['gold_sha256'],'gold binding')
prereg=load(assets/'protocol/measurement-preregistration.json')
state=collect(assets/'protocol/measurement-preregistration.json',src/'initial-ledger.json',assets/'results/formal-trials',execution_roots(gate,assets,out))
prior=collect(assets/'protocol/measurement-preregistration.json',src/'initial-ledger.json',assets/'results/formal-trials',execution_roots(gate,assets,out)[:-1])
require({s:sum(r['status']==s for r in prior) for s in ('success','failed','unstarted')}==dict(success=9,failed=5,unstarted=26),'prior counts')
initial_rows=load(out/'service-bootstrap-formal13/continuation-ledger.json')
require([{k:v for k,v in r.items() if k!='result_path'} for r in initial_rows]==[{k:v for k,v in r.items() if k!='result_path'} for r in prior],'batch initial ledger identity/status/hash; host/container paths differ')
remaining=[r['attempt_id'] for r in prior if r['status']=='unstarted']
actual={p.name for p in (out/'formal-trials').iterdir()}
expected_order=remaining[:len(actual)]
require(actual and actual==set(expected_order),'executed original prefix')
for before,after in zip(prior,state,strict=True):
 if before['status']!='unstarted':require(before==after,'prior terminal changed')
counts={s:sum(r['status']==s for r in state) for s in ('success','failed','unstarted')}
require(counts==dict(success=9+len(actual),failed=5,unstarted=26-len(actual)),'final counts')
def issued_batch_from_evidence(dispatch,config,deployment,window):
 require(config['scope']=='formal continuation of original forty IDs' and config['execution_purpose']=='formal-continuation','formal scope differs')
 require(dispatch['spec']['name']=='headformal13' and dispatch['run_id']==deployment['run_id']==config['run_id'],'dispatch run/role differs')
 require(dispatch['boot_id']==config['runtime_identity']['boot_id'] and dispatch['code_commit']==deployment['code_commit'] and dispatch['manifest_sha256']==deployment['input_manifest_sha256'],'dispatch recorded identity differs')
 observed=dispatch['batch_budget'];resource_ns=iso_time_ns(window['cleanup_deadline'])
 require(resource_ns==iso_time_ns(config['cleanup_deadline']),'recorded window differs')
 require(observed['wall_anchor_ns']//1000==iso_time_ns(dispatch['dispatch_utc'])//1000,'dispatch wall anchor differs')
 require(window['bookings'][0]['start_ms']*10**6<=observed['wall_anchor_ns']<window['bookings'][-1]['end_ms']*10**6,'original dispatch outside recorded window')
 expected=formal_batch_budget(dispatch['dispatch_monotonic_ns'],observed['wall_anchor_ns'],resource_ns,'formal')
 require(observed==expected,'batch differs from originally issued constraints')
 return expected
window=load(out/'resource-window-extension-v3.json')
require((out/'resource-window-extension-v3.json').read_bytes()==(src/'resource-window-extension-v3.json').read_bytes(),'window differs from reviewed bytes')
require(hashlib.sha256((out/'run-manifest.json').read_bytes()).hexdigest()==window['original_manifest_sha256'],'recorded config bytes differ')
require(config==load(src/'run-manifest.json'),'recorded config differs from reviewed source')
batch=issued_batch_from_evidence(load(out/'headformal13-dispatch.json'),config,deployment,window)
base=out/'service-bootstrap-formal13';launch=load(base/'store-numa-launch.json');worker=load(out/'formal13-identity.json');imports=load(base/'runtime-imports.json');helper=load(base/'helper-gpu-identity.json');shared=load(base/'shared-initialization.json');baseline=shared['auxiliary_gpu_MiB']
validate_store_teardown(load(base/'service-teardown.json'),launch,worker,run,boot,imports)
for label in ('formal13','headformal13'):
 result=load(out/(label+'-supervisor-result.json'));require(result['status']=='passed including cleanup' and not result['forced_kill'] and not result['child_alive_at_end'],'formal cleanup failure')
release=load(out/'release60-allstopped.json');validate_release(release,'allstopped',run,load(out/'host-preparation.json')['gpu_baseline_mib'])
root_index=load(out/'root-evidence-index13.json');require(root_index['cleanup_passed'] and root_index['guard_probe_passed'] and not root_index['owned_containers'] and root_index['deadline_timer_state']=='inactive','root closure')
next_unstarted=next((r['attempt_id'] for r in state if r['status']=='unstarted'),None)
if next_unstarted is not None:
 stop=load(base/'deadline-admission-stop.json');require(stop['next_unstarted_attempt']==next_unstarted and stop['work_end_ns']==batch['work_end_ns'] and stop['resource_deadline']==batch['resource_deadline_ns']/1e9 and 0<stop['work_end_ns']-stop['observed_ns']<1440*10**9,'budget stop')
else:require(not (base/'deadline-admission-stop.json').exists(),'unexpected stop after all IDs')
# Exact frozen input closures, reading only small metadata and statting payloads.
sf_paths=frozen_paths(assets/'checkpoint',src/'cache-inputs13.json')
store_metadata=load(src/'store-metadata-inputs.json');disk=load(assets/'store/tp8/raw-disk-verification.json');store_paths=store_inputs(assets/'store/tp8',assets/'checkpoint',disk['ranks'],store_metadata['files'])
def virtual(p):
 if p.is_relative_to(assets/'checkpoint'):return '/experiment/checkpoint/'+p.name
 return '/experiment/store/vllm/glm51-fp8/'+p.relative_to(assets/'store/tp8').as_posix()
closures={'safetensors_inputs':{virtual(p):p.stat().st_size for p in sf_paths},'store_inputs':{virtual(p):p.stat().st_size for p in store_paths}}
expected_gpu={};trials=[];vectors=0;vector_pages=0;cache_proofs=[]
def require_cache_condition(summary,kind,mode):
 require(summary['kind']==kind,'target input kind differs')
 require(summary['mode']==mode,'cache mode/pass')
 require(summary['cache_condition_passed'] is (None if mode=='check' else True),'cache mode/pass')
def require_pool_confirmations(confirmations):
 require(set(confirmations)=={f'vllm/glm51-fp8/rank_{rank}' for rank in range(8)} and all(v is True for v in confirmations.values()),'all eight pool ranks required')
def cache(label,expected_mode=None,expected_kind=None):
 global vectors,vector_pages
 directory=out/label;summary=load(directory/'summary.json');closure=closures[summary['kind']]
 require(len(summary['files'])==len(closure) and {r['source_path'] for r in summary['files']}==set(closure),'cache complete input closure')
 if expected_mode:require_cache_condition(summary,expected_kind,expected_mode)
 require(summary['verification_begin_ns']>=summary['preparation_complete_ns'] and summary['verification_complete_ns']>=summary['verification_begin_ns'],'cache boundary order')
 for row in summary['files']:
  path=(directory/row['vector']).resolve();require(path.is_relative_to(directory.resolve()),'vector escaped')
  compressed=path.read_bytes();flags=gzip.decompress(compressed)
  require(row['bytes']==closure[row['source_path']] and row['pages']==(row['bytes']+row['page_size']-1)//row['page_size'],'cache shape')
  require(len(flags)==row['pages'] and hashlib.sha256(flags).hexdigest()==row['vector_sha256'],'vector full bytes/hash')
  resident=flags.translate(bytes(v&1 for v in range(256))).count(b'\x01');require(resident==row['resident_pages'],'vector resident count')
  if expected_mode in ('cold','hot'):require(resident==(0 if expected_mode=='cold' else row['pages']),'declared cache condition')
  vectors+=1;vector_pages+=row['pages']
  # Large full vectors stay remote with exact compressed and uncompressed identities.
  vector_receipts.append(dict(path=path.relative_to(assets).as_posix(),bytes=len(compressed),sha256=hashlib.sha256(compressed).hexdigest(),pages=row['pages'],resident_pages=resident,uncompressed_sha256=row['vector_sha256']))
 cache_proofs.append(dict(label=label,kind=summary['kind'],mode=summary['mode'],files=len(summary['files']),resident_pages=sum(r['resident_pages'] for r in summary['files']),pages=sum(r['pages'] for r in summary['files'])))
 return summary
vector_receipts=[]
def require_attempt_contract(record,origin,attempt,prereg,batch):
 for key,value in attempt.items():
  if key!='status':require(record.get(key)==origin.get(key)==value,'preregistered attempt fields differ: '+key)
 expected=min(batch['work_end_ns'],record['preparation_begin_ns']+prereg['attempt_timeout_seconds']*10**9)
 require(record['attempt_deadline_ns']==origin['attempt_deadline_ns']==expected,'issued single attempt deadline differs')
 require(record['batch_work_end_ns']==origin['batch_work_end_ns']==batch['work_end_ns'],'issued batch work deadline differs')
 for key in ('preparation_begin_ns','memory_events_before','code_commit','source_manifest_sha256','execution_run_id','protocol_revision','preparation_revision','resource_window_revision'):
  require(origin[key]==record[key],'origin immutable field differs: '+key)
 require(origin['status']=='started' and origin['success'] is False,'durable started origin required')
preregistered={r['attempt_id']:r for r in prereg['attempts']}
previous_end=-1
for name in expected_order:
 target=out/'formal-trials'/name;record=load(target/'result.json');origin=load(target/'origin.json')
 require_attempt_contract(record,origin,preregistered[name],prereg,batch)
 require(record['attempt_id']==name and record['status']=='success' and record['success'] is True,'terminal success')
 require(record['code_commit']==gate['code_commit'] and record['source_manifest_sha256']==gate['source_manifest_sha256'] and record['execution_run_id']==run,'trial identity')
 require(previous_end<record['preparation_begin_ns']<record['startup_command_ns']<record['correct_business_response_ns']<record['complete_ns']<record['attempt_deadline_ns'],'trial order/deadline')
 previous_end=record['complete_ns']
 for k in ('protocol_revision','preparation_revision','resource_window_revision'):require(record[k]==config[k],'trial revision')
 require(origin['preparation_begin_ns']==record['preparation_begin_ns'] and origin['attempt_id']==name,'origin identity')
 condition=record['condition'];mode='check' if condition=='store_pool_hit' else ('hot' if condition.endswith('_hot') else 'cold')
 expected_kind='store_inputs' if condition.startswith('store_') else 'safetensors_inputs'
 other=cache(record['other_asset_cache_pre_preparation']);admission=cache(record['cache_evidence'],mode,expected_kind)
 require(other['kind']!=expected_kind,'other cache input kind differs')
 if condition=='store_pool_hit':require_pool_confirmations(load(target/'pool-confirmations.json'))
 require(other['total_cache_admission_end_ns']<=admission['preparation_begin_ns'] and admission['total_cache_admission_end_ns']<=record['preparation_complete_ns']<=record['startup_command_ns'],'target final admission ordering')
 for label in record['post_delete_cache_evidence']:cache(label)
 phases=load(target/'phases.json');require(phases==summarize(out/'events-formal13',record['startup_command_ns'],record['correct_business_response_ns'],name+'-business_arithmetic'),'phase raw recomputation');require_process(phases,boot)
 for case,expected in zip(gold['requests'],gold['results']):check_response(load(target/(case['name']+'-response.json')),expected,case)
 idle=jsonl(target/'idle60.jsonl');validate_native_idle(idle,baseline,helper,run,boot)
 for row in idle:
  snap=row['host_gpu_snapshot'];key=snap['last_sample_ns'];require(key not in expected_gpu or expected_gpu[key]==snap,'conflicting monitor receipt');expected_gpu[key]=snap
 pss=load(target/'pss.json');require(pss['status']=='completed' and pss['store_pid']==launch['pid'] and pss['end_ns']>=pss['begin_ns'] and any(r['pid']==launch['pid'] and r['smaps_rollup'] for r in pss['processes']),'PSS complete actual Store')
 require(not load(target/'observed-interference.json'),'interference')
 require(load(target/'jit-cache-after.json')==load(base/'jit-cache-baseline.json'),'new JIT artifact')
 for field in ('memory_events_before','memory_events_after'):
  values={k:int(v) for k,v in (line.split() for line in record[field].splitlines())};require(values['oom']==values['oom_kill']==0,'trial OOM')
 m=record['metrics'];derived={'command_to_first_token_s':(phases['first_token_observed_ns']-record['startup_command_ns'])/1e9,'command_to_correct_business_response_s':(record['correct_business_response_ns']-record['startup_command_ns'])/1e9,'weight_ingestion_span_s':phases['phases']['weight_ingestion']['min_begin_to_max_end_seconds'],'weights_pipeline_span_s':phases['phases']['weights_pipeline_including_allocation']['min_begin_to_max_end_seconds'],'preparation_s':(record['preparation_complete_ns']-record['preparation_begin_ns'])/1e9}
 derived.update(trigger_to_first_token_s=(phases['first_token_observed_ns']-record['business_trigger_ns'])/1e9,trigger_to_correct_business_response_s=(record['correct_business_response_ns']-record['business_trigger_ns'])/1e9)
 derived.update({'phase_'+phase+'_span_s':value['min_begin_to_max_end_seconds'] for phase,value in phases['phases'].items()})
 derived.update(backend_construct_s=(phases['control_events']['backend_engine_ready']['monotonic_ns']-phases['control_events']['backend_engine_create_begin']['monotonic_ns'])/1e9,command_to_route_publication_s=(phases['control_events']['router_instance_published']['monotonic_ns']-record['startup_command_ns'])/1e9)
 require(m==derived and all(math.isfinite(v) and v>=0 for v in m.values()),'metrics recomputation')
 trials.append(dict(attempt_id=name,condition=record['condition'],metrics=m,idle_span_seconds=(idle[-1]['monotonic_ns']-idle[0]['monotonic_ns'])/1e9,post_delete_gpu_MiB=record['post_delete_gpu_MiB'],raw_fixed32=load(target/'fixed32-response.json')['choices'][0]['token_ids']))
for path in sorted((out/'events-formal13').glob('*.jsonl')):jsonl(path)
monitor=out/'resource-monitor-v12.jsonl';summary=monitor_summary(monitor,gpu_context=(launch,worker,boot),expected_gpu=expected_gpu)
with monitor.open('rb') as stream:require(hashlib.sha256(stream.read(108387309)).hexdigest()=='deb32624282ab0772a9c90f2674747d15db5da3b699f1b6967cd0c1a88345573','normal reviewed monitor prefix changed')
peak=0;peak_gpu=[0]*8;foreign=False;oom=False
with monitor.open('rb') as stream:
 for line in stream:
  try:row=json.loads(line)
  except ValueError:continue # only controlled final tail is accepted by monitor_summary and retained
  foreign=foreign or bool(row['foreign_gpu_pids']);ev={k:int(v) for k,v in (s.split() for s in row['memory_events'].splitlines())};oom=oom or bool(ev.get('oom',0) or ev.get('oom_kill',0));peak=max(peak,row['cgroup_memory_current'])
  for i,line in enumerate(row['gpu'].splitlines()):peak_gpu[i]=max(peak_gpu[i],int(line.split(',')[3].strip().split()[0]))
require(not foreign and not oom,'whole monitor foreign/OOM')
result=dict(status='post-run formal raw recomputation passed; immutable original results retained',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),run_id=run,code_commit=gate['code_commit'],manifest_sha256=gate['source_manifest_sha256'],formal_counts=counts,next_unstarted_id=next_unstarted,trials=trials,formal_successes=len(trials),business_responses=3*len(trials),fixed32_responses=len(trials),weight_scope='no new tensor payload audit; unchanged loader qualification remains separately bound C17',cache=dict(summary_count=len(cache_proofs),full_vectors=vectors,pages_recomputed=vector_pages,proofs=cache_proofs),monitor=summary,sampled_cgroup_peak_bytes=peak,kernel_peak_bytes=None,peak_gpu_MiB=peak_gpu,release=dict(points=len(release),span_seconds=(release[-1]['monotonic_ns']-release[0]['monotonic_ns'])/1e9),shared_initialization_seconds=shared['elapsed_seconds'],artifacts=receipts,vector_artifacts=vector_receipts,final_ledger=state,scope='persistent common infrastructure; prep/pool/compiler/approval idle/init/cleanup/release cost separate; page cache only, hardware/storage/CUDA/JIT caches not claimed cleared')
print(json.dumps(result))
