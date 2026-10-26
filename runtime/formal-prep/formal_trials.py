import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
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
from resource_window_control13 import load_window
from store_numa_exec_v13 import audit_store_threads
from compilation_guard_v6 import artifact_snapshot, read_mutable_log, check_mutable_log_append

from workspace import SOURCE, run_root
from attempt_state import collect
ROOT = run_root()
ASSETS = pathlib.Path('/experiment')
PREREG_SHA = '90d4c91bf8b0c9495e0cb95376a55f84d17351308541414d58dd650d8e2dd37c'


from formal_gate import verify_gate


def run(out):
    gate = verify_gate()
    from sllm_store.client import SllmStoreClient
    from gpu_identity13 import require_helper_binding,require_helper_only
    store_launch = json.loads((out/'store-numa-launch.json').read_text())
    store_pid,store_cpus = store_launch['pid'],store_launch['target_cpu_affinity']
    assert (out/'store-affinity-ready.json').is_file()
    gold_data = (ASSETS/'validation/native-gold.json').read_bytes()
    assert hashlib.sha256(gold_data).hexdigest() == gate['gold_sha256']
    gold = json.loads(gold_data)
    prereg = json.loads((ASSETS/'protocol/measurement-preregistration.json').read_text())
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
    restored = json.loads((ASSETS/'validation/store-restore-validation.json').read_text())
    server_config = client.get_server_config()
    assert server_config == {'chunk_size':restored['chunk_bytes'],'mem_pool_size':restored['pool_bytes']}
    identifiers = [f'vllm/glm51-fp8/rank_{rank}' for rank in range(8)]
    for identifier in identifiers:
        assert client.register_model(identifier) > 0
    pool_loaded = False  # Fresh helper process; never inferred from asynchronous submission.
    auxiliary_gpu = [int(line) for line in subprocess.check_output(
        ['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()]
    assert len(auxiliary_gpu) == 8
    label = gate['worker_label']
    worker_identity = json.loads((ROOT/(label+'-identity.json')).read_text())
    head_identity = json.loads(pathlib.Path(os.environ['SLLM_HEAD_IDENTITY_FILE']).read_text())
    run_id = json.loads((ROOT/'run-manifest.json').read_text())['run_id']
    assert worker_identity['run_id'] == head_identity['run_id'] == run_id
    boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    for identity in (worker_identity,head_identity):
        dispatch = json.loads((ROOT/(identity['spec']['name']+'-dispatch.json')).read_text())
        assert dispatch['boot_id'] == boot_id
    assert json.loads(monitor_ready.read_text())['boot_id'] == boot_id
    def host_gpu():return json.loads(monitor_ready.read_text())
    manifest=json.loads((ROOT/'run-manifest.json').read_text())
    stop=time.monotonic()+5;helper=None
    while time.monotonic()<stop:
        candidate=host_gpu()['registered_store_helper']
        if candidate is not None:
            require_helper_binding(candidate,store_launch,worker_identity,boot_id)
            helper=candidate;break
        time.sleep(.1)
    assert helper is not None,'Store helper registration unavailable'
    (out/'helper-gpu-identity.json').write_text(json.dumps(helper,indent=2))
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
    state = collect(ASSETS/'protocol/measurement-preregistration.json',
                    SOURCE/'initial-ledger.json', ASSETS/'results/formal-trials',
                    [pathlib.Path(value) for value in gate['prior_execution_roots']] + [base])
    initial = {row['attempt_id']: row for row in state}
    (out/'continuation-ledger.json').write_text(json.dumps(state,indent=2))
    for attempt in prereg['attempts']:
        name, condition = attempt['attempt_id'], attempt['condition']
        target = base/name
        if initial[name]['status'] != 'unstarted':
            continue
        if target.exists():
            raise RuntimeError('ID appeared after admission: '+name)
        if time.time() >= deadline-prereg['attempt_timeout_seconds']-prereg['post_delete_observation_seconds']-180:
            (out/'deadline-admission-stop.json').write_text(json.dumps({'next_unstarted_attempt':name}))
            break
        require_helper_only(host_gpu(),helper,time.monotonic_ns(),run_id,boot_id)
        target.mkdir(exist_ok=False)
        record = {**attempt, 'status':'started', 'success':False, 'bootstrap_directory':str(out),
                  'execution_run_id':gate['execution_run_id'],'code_commit':gate['code_commit'],
                  'source_manifest_sha256':gate['source_manifest_sha256'],'preregistration_sha256':PREREG_SHA,
                  'protocol_revision':gate['protocol_revision'],'resource_window_revision':gate['resource_window_revision'],'preparation_revision':gate['preparation_revision'],
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
                subprocess.run(['/experiment/.venv/bin/python',str(pathlib.Path(__file__).with_name('cache_state_formal.py')),mode,kind,name],
                               stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
                other_kind = 'safetensors_inputs' if kind == 'store_inputs' else 'store_inputs'
                subprocess.run(['/experiment/.venv/bin/python',str(pathlib.Path(__file__).with_name('cache_state_formal.py')),'check',other_kind,name+'_other_before'],
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
            delete_ack_ns=record['delete_complete_ns']
            listed = http('/v1/models')
            (target/'models-after-delete.json').write_text(json.dumps(listed))
            assert 'glm51-fp8' not in {row['id'] for row in listed['models']}
            pss_path=target/'pss.json';pss_log=(target/'pss.log').open('x')
            pss=subprocess.Popen(['/experiment/.venv/bin/python','-u','/experiment/code/experiment/collect_pss13.py',str(pss_path),str(store_pid)],stdout=pss_log,stderr=subprocess.STDOUT)
            fresh_deadline=time.monotonic()+5
            while host_gpu()['sample_begin_ns']<delete_ack_ns:
                assert time.monotonic()<fresh_deadline,'no post-delete sample within5s'
                time.sleep(.1)
            record['idle_observation_begin_ns']=time.monotonic_ns()
            with (target/'idle60.jsonl').open('x') as samples:
                start=time.monotonic()
                for second in range(61):
                    shm=os.statvfs('/dev/shm');snapshot=host_gpu();now=time.monotonic_ns()
                    samples.write(json.dumps(dict(second=second,monotonic_ns=now,delete_ack_ns=delete_ack_ns,
                        gpu=snapshot['gpu'],gpu_processes=snapshot['gpu_processes'],host_gpu_snapshot=snapshot,
                        memory_current=int(pathlib.Path('/sys/fs/cgroup/memory.current').read_text()),
                        pids_current=int(pathlib.Path('/sys/fs/cgroup/pids.current').read_text()),
                        memory_events=pathlib.Path('/sys/fs/cgroup/memory.events').read_text(),
                        memory_stat=pathlib.Path('/sys/fs/cgroup/memory.stat').read_text(),
                        cpu_stat=pathlib.Path('/sys/fs/cgroup/cpu.stat').read_text(),
                        shm_used_bytes=(shm.f_blocks-shm.f_bfree)*shm.f_frsize))+'\n')
                    samples.flush()
                    if second<60:time.sleep(1)
                record['idle_observation_seconds']=time.monotonic()-start
            if pss.poll() is None:
                pss.kill();pss.wait(timeout=5)
            pss_log.close()
            assert pss.returncode==0 and json.loads(pss_path.read_text())['status']=='completed','PSS probe incomplete'
            from accept_control13 import validate_native_idle
            idle_rows=[json.loads(line) for line in (target/'idle60.jsonl').read_text().splitlines()]
            validate_native_idle(idle_rows,auxiliary_gpu,helper,run_id,boot_id)
            remaining = [int(line) for line in subprocess.check_output(
                ['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()]
            record['post_delete_gpu_MiB'] = remaining
            assert len(remaining) == 8 and all(after <= before+1024 for before,after in zip(
                auxiliary_gpu,remaining,strict=True)), 'model GPU memory retained after delete'
            with (target/'post-delete-cache.log').open('x') as log:
                for cache_kind,suffix in ((kind,'_after'),(other_kind,'_other_after')):
                    subprocess.run(['/experiment/.venv/bin/python',str(pathlib.Path(__file__).with_name('cache_state_formal.py')),'check',cache_kind,name+suffix],
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




