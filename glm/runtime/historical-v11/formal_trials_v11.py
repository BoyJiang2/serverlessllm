"""B only: native HTTP trials on explicitly charged shared infrastructure."""
import datetime
import hashlib
import json
import math
import os
import pathlib
import signal
import subprocess
import time
import traceback
import urllib.error
import urllib.request

from phase_summary import summarize
from resource_window_v2 import load_window
from store_numa_exec_v10 import audit_store_threads
from compilation_guard_v6 import artifact_snapshot, read_mutable_log, check_mutable_log_append
from sllm_store.client import SllmStoreClient

ROOT = pathlib.Path('/experiment')
PREREG_SHA = '90d4c91bf8b0c9495e0cb95376a55f84d17351308541414d58dd650d8e2dd37c'


def verify_gate():
    data = pathlib.Path(os.environ['SLLM_FORMAL_GATE_FILE']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == os.environ['SLLM_REVIEW_MANIFEST_SHA256']
    gate = json.loads(data)
    assert gate['decision'] == 'approved' and gate['independent_reviewer']
    assert 8 <= gate['formal_attempts_before_review'] <= 40
    assert gate['protocol_revision'] == 'v11' and gate['preparation_revision'] == 'store_node0_hot_bounded_repair_10'
    old_gate_data = (ROOT/'formal-review-gate-v8.json').read_bytes()
    assert hashlib.sha256(old_gate_data).hexdigest() == gate['previous_gate_sha256']
    assert json.loads(old_gate_data)['formal_attempts_before_review'] == 7
    checked = set()
    for row in gate['files']:
        path = (ROOT/row['path']).resolve()
        assert path.is_relative_to(ROOT) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], str(path)
        checked.add(path)
    import sllm_store._checkpoint_store as actual_extension
    extension_path = pathlib.Path(actual_extension.__file__).resolve()
    assert extension_path.is_relative_to(ROOT/'source-v7/sllm_store')
    assert extension_path in checked
    preload_path = ROOT/'service-bootstrap-normalv7/cpu-preload-confirmation.json'
    assert preload_path in checked
    preload = json.loads(preload_path.read_text())
    assert preload['confirmations'] == {f'vllm/glm51-fp8/rank_{rank}':True for rank in range(8)}
    assert preload['extension_path'] == str(extension_path)
    assert preload['extension_sha256'] == hashlib.sha256(extension_path.read_bytes()).hexdigest()
    assert {ROOT/'formal_trials_v11.py', ROOT/'phase_summary.py',
            ROOT/'service_bootstrap_v11.py', ROOT/'measurement-preregistration.json',
            ROOT/'review-input-manifest-v11.json'} <= checked
    required = json.loads((ROOT/'review-input-manifest-v11.json').read_text())
    assert gate['formal_attempts_before_review'] == required['formal_attempts_started']
    assert ROOT/'resource-window-extension-v2.json' in checked
    assert ROOT/'resource_window_v2.py' in checked
    load_window(ROOT)
    assert {(ROOT/row['path']).resolve() for row in required['files']} <= checked
    assert {(row['path'],row['sha256']) for row in required['files']} <= {
        (row['path'],row['sha256']) for row in gate['files']}
    formats = set()
    for name in gate['A4_evidence']:
        path = ROOT/name
        assert path.resolve() in checked
        proof = json.loads(path.read_text())
        assert proof['status'] == 'A4 native HTTP lifecycle checks passed' and proof['cycles'] == 2
        formats.add(proof['weight_format'])
    assert formats == {'serverless_llm', 'safetensors'}
    probe_path = ROOT/gate['memory_probe_evidence']
    assert probe_path.resolve() in checked
    smoke_formats = set()
    for name in gate['normal_smoke_evidence']:
        smoke_path = ROOT/name
        assert smoke_path.resolve() in checked
        smoke = json.loads(smoke_path.read_text())
        assert smoke['status'] == 'normal-flags gold smoke lifecycle checks passed' and smoke['cycles'] == 2
        assert not smoke['weight_audit_performed']
        smoke_formats.add(smoke['weight_format'])
    assert smoke_formats == {'serverless_llm','safetensors'}
    probe = json.loads(probe_path.read_text())
    assert probe['status'] == 'eight GPU allocations and releases passed' and len(probe['devices']) == 8
    assert hashlib.sha256((ROOT/'measurement-preregistration.json').read_bytes()).hexdigest() == PREREG_SHA
    for key,value in {'VLLM_USE_DEEP_GEMM_E8M0':'0','HF_HUB_OFFLINE':'1',
                      'TRANSFORMERS_OFFLINE':'1','HOME':'/experiment/native-home',
                      'TORCHINDUCTOR_CACHE_DIR':'/experiment/native-home/inductor',
                      'TRITON_CACHE_DIR':'/experiment/native-home/triton',
                      'VLLM_CACHE_ROOT':'/experiment/native-home/vllm',
                      'CUDA_CACHE_PATH':'/experiment/native-home/cuda'}.items():
        assert os.environ.get(key) == value, key
    for key in ('SLLM_VERIFY_WEIGHTS', 'SLLM_SERVICE_WEIGHT_AUDIT_DIR',
                'SLLM_EXPORT_RAW_STORE', 'VLLM_ALLOW_INSECURE_SERIALIZATION','SLLM_NORMAL_FLAGS_SMOKE'):
        assert not os.environ.get(key), key+' must be absent for B'
    return gate


def run(out):
    gate = verify_gate()
    store_launch = json.loads((out/'store-numa-launch.json').read_text())
    store_pid,store_cpus = store_launch['pid'],store_launch['target_cpu_affinity']
    assert (out/'store-affinity-ready.json').is_file()
    gold_data = (ROOT/'native-gold.json').read_bytes()
    assert hashlib.sha256(gold_data).hexdigest() == (ROOT/'native-gold.sha256').read_text().strip()
    gold = json.loads(gold_data)
    prereg = json.loads((ROOT/'measurement-preregistration.json').read_text())
    deadline = datetime.datetime.fromisoformat(load_window(ROOT)['cleanup_deadline']).timestamp()
    base = ROOT/'formal-trials'
    base.mkdir(exist_ok=True)
    (out/'review-admission.json').write_text(json.dumps(gate,indent=2))
    client = SllmStoreClient()
    frozen_cache = artifact_snapshot(ROOT/'native-home')
    assert frozen_cache
    (out/'jit-cache-baseline.json').write_text(json.dumps(frozen_cache,indent=2))
    (out/'autotune-log-baseline.log').write_bytes(read_mutable_log(ROOT/'native-home'))
    monitor_ready = ROOT/'monitor-v3-ready.json'
    assert monitor_ready.is_file(), 'whole-deployment isolation monitor unavailable'
    assert time.monotonic_ns()-json.loads(monitor_ready.read_text())['last_sample_ns'] < 10*10**9
    restored = json.loads((ROOT/'store-restore-validation.json').read_text())
    server_config = client.get_server_config()
    assert server_config == {'chunk_size':restored['chunk_bytes'],'mem_pool_size':restored['pool_bytes']}
    identifiers = [f'vllm/glm51-fp8/rank_{rank}' for rank in range(8)]
    for identifier in identifiers:
        assert client.register_model(identifier) > 0
    pool_loaded = False  # Fresh helper process; never inferred from asynchronous submission.
    auxiliary_gpu = [int(line) for line in subprocess.check_output(
        ['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()]
    assert len(auxiliary_gpu) == 8
    label = out.name.removeprefix('service-bootstrap-')
    worker_identity = json.loads((ROOT/(label+'-identity.json')).read_text())
    head_identity = json.loads(pathlib.Path(os.environ['SLLM_HEAD_IDENTITY_FILE']).read_text())
    run_id = json.loads((ROOT/'run-manifest.json').read_text())['run_id']
    assert worker_identity['run_id'] == head_identity['run_id'] == run_id
    boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    for identity in (worker_identity,head_identity):
        dispatch = json.loads((ROOT/(identity['spec']['name']+'-dispatch.json')).read_text())
        assert dispatch['boot_id'] == boot_id
    assert json.loads(monitor_ready.read_text())['boot_id'] == boot_id
    ready = time.monotonic_ns()
    origin = min(worker_identity['dispatch_monotonic_ns'],head_identity['dispatch_monotonic_ns'])
    (out/'shared-initialization.json').write_text(json.dumps({
        'observations':1,'origin_ns':origin,'common_ready_ns':ready,
        'boot_id':boot_id,
        'elapsed_seconds':(ready-origin)/1e9,'auxiliary_gpu_MiB':auxiliary_gpu,
        'head_identity':head_identity,'worker_identity':worker_identity,
        'scope':'shared private Ray/control plane/Store/pool and registration; retained across trials; not ten node cold boots'},indent=2))

    def http(path, body=None, timeout=1200):
        request = urllib.request.Request('http://127.0.0.1:8343'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            with (target/'http-errors.jsonl').open('a') as stream:
                stream.write(json.dumps({'path':path,'status':error.code,
                    'body':error.read(1024*1024).decode(errors='replace')})+'\n')
            raise

    def timeout_handler(signum, frame):
        raise TimeoutError('preregistered attempt time budget exceeded')

    signal.signal(signal.SIGALRM, timeout_handler)
    for attempt in prereg['attempts']:
        name, condition = attempt['attempt_id'], attempt['condition']
        target = base/name
        if target.exists():
            # An interrupted attempt consumes its ID; a new infrastructure run may continue later IDs.
            if not (target/'result.json').exists():
                prior = json.loads((target/'origin.json').read_text()) if (target/'origin.json').exists() else attempt
                with (target/'result.json').open('x') as stream:
                    json.dump({**prior,'status':'interrupted','success':False,'attempt_id':name,
                               'reason':'earlier infrastructure run ended without a terminal record'},stream)
            continue
        if time.time() >= deadline-prereg['attempt_timeout_seconds']-180:
            (out/'deadline-admission-stop.json').write_text(json.dumps({'next_unstarted_attempt':name}))
            break
        target.mkdir(exist_ok=False)
        record = {**attempt, 'status':'started', 'success':False, 'bootstrap_directory':str(out),
                  'protocol_revision':'v11','resource_window_revision':'v2','preparation_revision':'store_node0_hot_bounded_repair_10',
                  'preparation_begin_ns':time.monotonic_ns(),
                  'memory_events_before':pathlib.Path('/sys/fs/cgroup/memory.events').read_text()}
        with (target/'origin.json').open('x') as stream:
            json.dump(record,stream,indent=2)
        signal.alarm(prereg['attempt_timeout_seconds'])
        try:
            (target/'store-affinity-before.json').write_text(json.dumps(audit_store_threads(store_pid,store_cpus),indent=2))
            assert artifact_snapshot(ROOT/'native-home') == frozen_cache, 'JIT cache changed since baseline'
            mutable_log_before = read_mutable_log(ROOT/'native-home')
            if pool_loaded:
                for identifier in identifiers:
                    response = client.unload_from_cpu(identifier)
                    assert response is not False and response.model_path == identifier
                pool_loaded = False
            if condition == 'store_pool_hit':
                for identifier in identifiers:
                    assert client.load_into_cpu(identifier) is not False
                confirmations = {identifier:client.wait_until_in_cpu(identifier,timeout=600) for identifier in identifiers}
                assert all(confirmations.values())
                (target/'pool-confirmations.json').write_text(json.dumps(confirmations,indent=2))
                pool_loaded = True
            kind = 'store_inputs' if condition.startswith('store_') else 'safetensors_inputs'
            mode = 'check' if condition == 'store_pool_hit' else ('hot' if condition.endswith('_hot') else 'cold')
            record['cache_evidence'] = 'cache_'+name
            with (target/'cache-preparation.log').open('x') as log:
                subprocess.run(['/experiment/.venv/bin/python','/experiment/cache_state_v11.py',mode,kind,name],
                               stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
                other_kind = 'safetensors_inputs' if kind == 'store_inputs' else 'store_inputs'
                subprocess.run(['/experiment/.venv/bin/python','/experiment/cache_state_v11.py','check',other_kind,name+'_other_before'],
                               stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
            record['other_asset_cache_before'] = 'cache_'+name+'_other_before'
            record['preparation_complete_ns'] = time.monotonic_ns()
            backend = dict(gold['engine_config'])
            backend.pop('model')
            backend.pop('load_format')
            backend.update(pretrained_model_name_or_path='/experiment/checkpoint',torch_dtype='bfloat16')
            if condition.startswith('safetensors_'):
                backend['load_format'] = 'safetensors'
            config = {'model':'glm51-fp8','backend':'vllm','num_gpus':8,'backend_config':backend,
                      'placement_config':{'local_disk':['0'],'memory_pool':[]},
                      'auto_scaling_config':{'metric':'concurrency','target':1,'min_instances':0,
                                            'max_instances':1,'keep_alive':3600},'router_config':{}}
            (target/'model-config.json').write_text(json.dumps(config,indent=2))
            record['startup_command_ns'] = time.monotonic_ns()
            assert http('/register',config) == {'status':'ok'}
            record['register_complete_ns'] = time.monotonic_ns()
            for number in (1,2,0):
                case, expected = gold['requests'][number],gold['results'][number]
                request_id = name+'-'+case['name']
                body = {'model':'glm51-fp8','input_tokens':case['input_tokens'], 'temperature':0,
                        'seed':20261024,'max_tokens':case['max_tokens'],'ignore_eos':case['ignore_eos'],
                        'logprobs':1,'return_token_ids':True,'request_id':request_id}
                if number == 1:
                    record['business_trigger_ns'] = time.monotonic_ns()
                response = http('/v1/chat/completions',body)
                received_ns = time.monotonic_ns()
                (target/(case['name']+'-response.json')).write_text(json.dumps(response,ensure_ascii=False,indent=2))
                choice = response['choices'][0]
                assert choice['token_ids'] == expected['token_ids'] and choice['message']['content'] == expected['text']
                assert choice['finish_reason'] == expected['finish_reason']
                assert len(choice['logprobs']) == len(choice['token_ids'])
                assert all(math.isfinite(row[str(token)]['logprob']) for token,row in zip(
                    choice['token_ids'],choice['logprobs'],strict=True))
                if number == 1:
                    record['correct_business_response_ns'] = received_ns
                    phases = summarize(os.environ['SLLM_EVENT_DIR'],record['startup_command_ns'],received_ns,request_id)
                    assert phases['backend_process_creation']['boot_id'] == boot_id
                    (target/'phases.json').write_text(json.dumps(phases,indent=2))
                    record['first_token_observed_ns'] = phases['first_token_observed_ns']
                    record['metrics'] = {
                        'command_to_first_token_s':(record['first_token_observed_ns']-record['startup_command_ns'])/1e9,
                        'command_to_correct_business_response_s':(received_ns-record['startup_command_ns'])/1e9,
                        'trigger_to_first_token_s':(record['first_token_observed_ns']-record['business_trigger_ns'])/1e9,
                        'trigger_to_correct_business_response_s':(received_ns-record['business_trigger_ns'])/1e9,
                        'preparation_s':(record['preparation_complete_ns']-record['preparation_begin_ns'])/1e9,
                        'weight_ingestion_span_s':phases['phases']['weight_ingestion']['min_begin_to_max_end_seconds'],
                        'weights_pipeline_span_s':phases['phases']['weights_pipeline_including_allocation']['min_begin_to_max_end_seconds']}
                    record['metrics'].update({
                        'phase_'+phase+'_span_s':values['min_begin_to_max_end_seconds']
                        for phase,values in phases['phases'].items()})
                    control = phases['control_events']
                    record['metrics']['backend_construct_s'] = (control['backend_engine_ready']['monotonic_ns']-control['backend_engine_create_begin']['monotonic_ns'])/1e9
                    record['metrics']['command_to_route_publication_s'] = (control['router_instance_published']['monotonic_ns']-record['startup_command_ns'])/1e9
            if condition.startswith('store_'):
                assert all(client.wait_until_in_cpu(identifier,timeout=30) for identifier in identifiers)
                pool_loaded = True
            record['delete_command_ns'] = time.monotonic_ns()
            assert http('/delete',{'model':'glm51-fp8'})['status'] == 'deleted model glm51-fp8'
            record['delete_complete_ns'] = time.monotonic_ns()
            listed = http('/v1/models')
            (target/'models-after-delete.json').write_text(json.dumps(listed))
            assert 'glm51-fp8' not in {row['id'] for row in listed['models']}
            with (target/'idle60.jsonl').open('x') as samples:
                start = time.monotonic()
                record['idle_observation_begin_ns'] = time.monotonic_ns()
                for second in range(61):
                    shm = os.statvfs('/dev/shm')
                    row = {'second':second,'monotonic_ns':time.monotonic_ns(),
                           'gpu':subprocess.check_output(['nvidia-smi','--query-gpu=index,memory.used,utilization.gpu','--format=csv,noheader'],text=True),
                           'gpu_processes':subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],text=True),
                           'cpu_stat':pathlib.Path('/sys/fs/cgroup/cpu.stat').read_text(),
                           'memory_stat':pathlib.Path('/sys/fs/cgroup/memory.stat').read_text(),
                           'memory_current':int(pathlib.Path('/sys/fs/cgroup/memory.current').read_text()),
                           'memory_events':pathlib.Path('/sys/fs/cgroup/memory.events').read_text(),
                           'shm_used_bytes':(shm.f_blocks-shm.f_bfree)*shm.f_frsize}
                    samples.write(json.dumps(row)+'\n')
                    samples.flush()
                    if second < 60:
                        time.sleep(max(0,start+second+1-time.monotonic()))
                record['idle_observation_seconds'] = time.monotonic()-start
                assert record['idle_observation_seconds'] >= 60
            remaining = [int(line) for line in subprocess.check_output(
                ['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()]
            record['post_delete_gpu_MiB'] = remaining
            assert len(remaining) == 8 and all(after <= before+1024 for before,after in zip(
                auxiliary_gpu,remaining,strict=True)), 'model GPU memory retained after delete'
            with (target/'post-delete-cache.log').open('x') as log:
                for cache_kind,suffix in ((kind,'_after'),(other_kind,'_other_after')):
                    subprocess.run(['/experiment/.venv/bin/python','/experiment/cache_state_v11.py','check',cache_kind,name+suffix],
                                   stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
            (target/'store-affinity-after.json').write_text(json.dumps(audit_store_threads(store_pid,store_cpus),indent=2))
            record['post_delete_cache_evidence'] = ['cache_'+name+'_after','cache_'+name+'_other_after']
            final_cache = artifact_snapshot(ROOT/'native-home')
            (target/'jit-cache-after.json').write_text(json.dumps(final_cache,indent=2))
            assert final_cache == frozen_cache, 'new or modified JIT artifacts; isolation failed'
            log_receipt = check_mutable_log_append(mutable_log_before,read_mutable_log(ROOT/'native-home'))
            (target/'autotune-log-append.json').write_text(json.dumps(log_receipt,indent=2))
            assert time.monotonic_ns()-json.loads(monitor_ready.read_text())['last_sample_ns'] < 10*10**9
            interference_file = ROOT/'interference-v3.jsonl'
            interference = [json.loads(line) for line in interference_file.read_text().splitlines()] if interference_file.exists() else []
            during = [row for row in interference if row['monotonic_ns'] >= record['preparation_begin_ns']]
            (target/'observed-interference.json').write_text(json.dumps(during,indent=2))
            assert not during, 'observed compiler/download job; isolation failed'
            record.update(status='success',success=True,complete_ns=time.monotonic_ns())
        except BaseException as error:
            failed_ns = time.monotonic_ns()
            timed_out = isinstance(error,(TimeoutError,subprocess.TimeoutExpired)) or (
                isinstance(error,urllib.error.URLError) and isinstance(error.reason,TimeoutError))
            record.update(status='timeout' if timed_out else 'failed',
                          error_type=type(error).__name__,error=str(error),failure_ns=failed_ns,
                          correct_business_response_observed='correct_business_response_ns' in record,
                          right_censored=timed_out and 'correct_business_response_ns' not in record,
                          startup_censor_elapsed_seconds=(failed_ns-record['startup_command_ns'])/1e9 if 'startup_command_ns' in record else None,
                          traceback=traceback.format_exc())
            raise  # Fresh guarded infrastructure is required before remaining IDs can continue.
        finally:
            signal.alarm(0)
            record['memory_events_after'] = pathlib.Path('/sys/fs/cgroup/memory.events').read_text()
            with (target/'result.json').open('x') as stream:
                json.dump(record,stream,indent=2)
            print(json.dumps({'attempt_id':name,'status':record['status']}),flush=True)




