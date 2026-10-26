import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Native single-machine control plane only; no model or inference substitute."""
import json
import os
import pathlib
import socket
import signal
import subprocess
import sys
import time
import urllib.request
import datetime

from workspace import run_root,verify_input_layout
root = pathlib.Path('/experiment')
control = run_root()
from budget_control13 import POOL_DIAGNOSTIC_SCOPE,POOL_DIAGNOSTIC_WORK_BUDGET,STORE_SHUTDOWN_GRACE,G1_SERVICE_WORK_BUDGET
manifest=json.loads((control/'run-manifest.json').read_text())
pool_diagnostic=manifest['scope']==POOL_DIAGNOSTIC_SCOPE
if not pool_diagnostic:verify_input_layout()
label = sys.argv[1] if len(sys.argv)>1 else 'probe'
assert label.isalnum()
out = control/('service-bootstrap-'+label)
out.mkdir(exist_ok=False)
validate_model = os.environ.get('SLLM_VALIDATE_FULL_MODEL') == '1'
formal = bool(os.environ.get('SLLM_FORMAL_GATE_FILE'))
normal_smoke = os.environ.get('SLLM_NORMAL_FLAGS_SMOKE') == '1'
formal_purpose=manifest.get('execution_purpose')=='formal-continuation'
if formal_purpose or manifest['scope']=='formal continuation of original forty IDs':
    assert formal_purpose and manifest['scope']=='formal continuation of original forty IDs'
    assert label in ('service13a','formal13')
    normal_smoke=label=='service13a'
    if normal_smoke:
        from formal_gate import verify_normal_reference,g1_reference_root
        verify_normal_reference(manifest,root)
    else:
        from formal_gate import verify_gate
        verify_gate()
elif pool_diagnostic:
    if os.environ.get('SLLM_POOL_DIAGNOSTIC')!='1' or validate_model or formal or normal_smoke or label!='service13a':raise RuntimeError('invalid pool-only diagnostic flags')
elif manifest['scope']!='G1 independent untimed controls only; zero formal IDs' or not validate_model or formal or normal_smoke:
    raise RuntimeError('G1 permits only full real-weight untimed validation')
env = os.environ.copy()
if formal_purpose:
    for key in ('SLLM_VERIFY_WEIGHTS','SLLM_SERVICE_WEIGHT_AUDIT_DIR','SLLM_EXPORT_RAW_STORE','VLLM_ALLOW_INSECURE_SERIALIZATION'):
        assert not env.get(key),key+' must be absent'
pool_bytes = 67108864
chunk_bytes = 32*1024*1024
if validate_model or pool_diagnostic or formal_purpose:
    restored = json.loads((root/'validation/store-restore-validation.json').read_text())
    assert restored['status'] == 'full Store component restore validation passed'
    pool_bytes, chunk_bytes = restored['pool_bytes'], restored['chunk_bytes']
    if not pool_diagnostic and not formal_purpose and env.get('SLLM_SERVICE_WEIGHT_FORMAT') == 'safetensors' and not (
            formal or env.get('SLLM_USE_COMMON_POOL') == '1'):
        pool_bytes = 67108864
        chunk_bytes = 32*1024*1024
env.update(STORAGE_PATH=str(root/'store' if validate_model or formal_purpose else out/'store'),
           RAY_TMPDIR=str(control/('ray-'+label)), RAY_USAGE_STATS_ENABLED='0')
pathlib.Path(env['STORAGE_PATH']).mkdir(exist_ok=True)
if pool_diagnostic:
    assert pool_bytes==768799145984 and chunk_bytes==536870912
    assert not list(pathlib.Path(env['STORAGE_PATH']).iterdir()), 'pool diagnostic storage must be empty'
python = '/experiment/.venv/bin/python'
ip = socket.gethostbyname(socket.gethostname())
address = os.environ['SLLM_PRIVATE_RAY_ADDRESS']
env['RAY_ADDRESS'] = address
os.environ.update(RAY_TMPDIR=env['RAY_TMPDIR'],RAY_ADDRESS=address,RAY_USAGE_STATS_ENABLED='0')
processes, logs = [], []
store = None

def launch(name, args):
    output = (out/(name+'.log')).open('x')
    logs.append(output)
    process = subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,env=env)
    processes.append(process)
    return process

def wait_ready(check, seconds, process):
    stop = min(time.monotonic()+seconds,work_deadline)
    while time.monotonic()<stop:
        if process.poll() is not None:
            raise RuntimeError(f'process {process.pid} exited {process.returncode}')
        try:
            if check(): return
        except (OSError,ValueError): pass
        time.sleep(1)
    raise TimeoutError('native bootstrap readiness timeout')

def expired(signum, frame):
    raise TimeoutError('native service validation deadline reached')

signal.signal(signal.SIGALRM,expired)
from resource_window_control13 import load_window
deadline = datetime.datetime.fromisoformat(load_window(control)['cleanup_deadline']).timestamp()
from budget_control13 import require_budget,load_formal_batch,arm_formal_timer
if formal_purpose:
    batch_budget=load_formal_batch(control,label)
    work_deadline=batch_budget['initialization_end_ns']/1000000000
    arm_formal_timer(batch_budget['initialization_end_ns'])
else:
    require_budget(label,deadline-time.time(),pool_diagnostic=pool_diagnostic)
    work_deadline=time.monotonic()+(POOL_DIAGNOSTIC_WORK_BUDGET if pool_diagnostic else G1_SERVICE_WORK_BUDGET)
    signal.alarm(POOL_DIAGNOSTIC_WORK_BUDGET if pool_diagnostic else G1_SERVICE_WORK_BUDGET)

try:
    worker = launch('ray-worker',[python,'-m','ray.scripts.scripts','start','--address='+address,'--node-ip-address='+ip,
        '--num-cpus=16','--num-gpus=8','--resources={"worker_node":1,"worker_id_0":1}',
        '--min-worker-port=22000','--max-worker-port=22999',
        '--object-store-memory=268435456','--disable-usage-stats','--block'])
    wait_ready(lambda:'Ray runtime started.' in (out/'ray-worker.log').read_text(),60,worker)
    import ray
    ray.init(address=address,_node_ip_address=ip,log_to_driver=False)
    own = next(node for node in ray.nodes() if node['Alive'] and node['NodeManagerAddress']==ip)
    assert own['NodeID'] == str(ray.get_runtime_context().get_node_id()) and own['Resources']['GPU']==8
    wait_ready(lambda:sum(node['Alive'] for node in ray.nodes())==2,60,worker)
    nodes = ray.nodes()
    assert len({node['NodeManagerAddress'] for node in nodes}) == 2
    assert sum(node['Resources'].get('GPU',0) for node in nodes)==8
    (out/'ray-nodes.json').write_text(json.dumps(nodes,indent=2))
    ray.shutdown()
    store = launch('store',[python,'/experiment/code/experiment/store_numa_exec_v13.py',str(out/'store-numa-launch.json'),python,'-u','/experiment/code/experiment/run_store_server13.py','--storage-path',env['STORAGE_PATH'],
                            '--pool-bytes',str(pool_bytes),'--chunk-bytes',str(chunk_bytes),'--host','0.0.0.0','--identity-receipt',str(out/'runtime-imports.json')])
    def store_ready():
        with socket.create_connection(('127.0.0.1',8073),timeout=1): return True
    wait_ready(store_ready,max(0,min(480 if pool_diagnostic else 1800,work_deadline-time.monotonic())),store)
    from listener_control13 import listener
    store_listener=listener(store.pid,8073)
    import grpc
    from sllm_store.proto import storage_pb2,storage_pb2_grpc
    with grpc.insecure_channel('127.0.0.1:8073') as channel:
        pool_config=storage_pb2_grpc.StorageStub(channel).GetServerConfig(storage_pb2.GetServerConfigRequest(),timeout=5)
    assert pool_config.mem_pool_size==pool_bytes and pool_config.chunk_size==chunk_bytes
    status=pathlib.Path(f'/proc/{store.pid}/status').read_text()
    rss=int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:')))*1024
    assert rss>=pool_bytes, 'real pool not resident'
    assert 'Memory pool created with 716GB' in (out/'store.log').read_text()
    (out/'pool-ready.json').write_text(json.dumps(dict(monotonic_ns=time.monotonic_ns(),pool_bytes=pool_bytes,chunk_bytes=chunk_bytes,store_pid=store.pid,listener=store_listener,rpc_config=dict(mem_pool_size=pool_config.mem_pool_size,chunk_size=pool_config.chunk_size),rss_bytes=rss),indent=2))
    from store_numa_exec_v13 import audit_store_threads
    target = json.loads((out/'store-numa-launch.json').read_text())['target_cpu_affinity']
    (out/'store-affinity-ready.json').write_text(json.dumps(audit_store_threads(store.pid,target),indent=2))
    if pool_diagnostic:
        from gpu_identity13 import require_monitor_ready,require_helper_binding,require_helper_only
        launch_receipt=json.loads((out/'store-numa-launch.json').read_text())
        service_identity=json.loads((control/'service13a-identity.json').read_text())
        pool_ready=json.loads((out/'pool-ready.json').read_text())
        stop=time.monotonic()+15
        while True:
            snapshot=json.loads((control/'monitor-v3-ready.json').read_text())
            require_monitor_ready(snapshot,time.monotonic_ns(),manifest['run_id'],manifest['runtime_identity']['boot_id'])
            helper=snapshot['registered_store_helper']
            if helper is not None and snapshot['sample_begin_ns']>=pool_ready['monotonic_ns']:
                require_helper_binding(helper,launch_receipt,service_identity,manifest['runtime_identity']['boot_id'])
                require_helper_only(snapshot,helper,time.monotonic_ns(),manifest['run_id'],manifest['runtime_identity']['boot_id'])
                break
            if time.monotonic()>=stop:raise TimeoutError('resident pool helper fresh sample unavailable')
            time.sleep(.1)
        (out/'pool-diagnostic-result.json').write_text(json.dumps(dict(status='resident pool initialization checked; teardown/release still required',run_id=manifest['run_id'],pool_bytes=pool_bytes,chunk_bytes=chunk_bytes,helper=helper,sample_begin_ns=snapshot['sample_begin_ns'],ready_ns=time.monotonic_ns(),checkpoint_read=False,model_registered=False),indent=2))
        raise SystemExit(0)
    server = launch('sllm',[python,'-u','-c','from sllm.cli._cli_utils import start_server; start_server(host="127.0.0.1",port=8343)'])
    def healthy():
        with urllib.request.urlopen('http://127.0.0.1:8343/health',timeout=2) as response:
            return json.load(response)=={'status':'ok'}
    wait_ready(healthy,90,server)
    (out/'sllm-listener.json').write_text(json.dumps(listener(server.pid,8343),indent=2))
    (out/'result.json').write_text(json.dumps({'status':'native control plane healthy','physical_host':'qjhs3',
        'ray_logical_roles':2,'gpus':8,'model_registered':False,'inference_performed':False,
        'entrypoint':'sllm.cli._cli_utils.start_server + native FastAPI /health'},indent=2))
    print('native_control_plane_healthy_no_model_registered',flush=True)
    if formal_purpose:
        if normal_smoke:arm_formal_timer(batch_budget['work_end_ns'])
        if normal_smoke:
            from attempt_state import checked_json
            from validation_control13 import run
            os.environ['SLLM_NORMAL_FLAGS_SMOKE']='1'
            for weight_format in ('serverless_llm','safetensors'):
                row=manifest['full_a4_proofs'][weight_format]
                proof_path=g1_reference_root(manifest,root)/row['path']
                proof=checked_json(proof_path,row['sha256'])
                assert proof['status']=='A4 native HTTP lifecycle checks passed' and proof['cycles']==2
                assert proof['weight_format']==weight_format and proof['weight_audit_performed'] is True
                os.environ['SLLM_FULL_A4_PROOF_FILE']=str(proof_path)
                os.environ['SLLM_SERVICE_WEIGHT_FORMAT']=weight_format
                target=out/weight_format;target.mkdir(exist_ok=False)
                run(target,audit_weights=False)
            from formal_gate import seal_normal_cache
            seal_normal_cache(control,pathlib.Path(__file__).resolve().parent,root)
        else:
            from formal_trials import run
            run(out,batch_budget)
    elif validate_model:
        # Independent untimed full-cache control; does not label later lifecycle cache state.
        cache_log=(out/'hot-cache-control.log').open('x')
        try:
            subprocess.run([python,str(pathlib.Path(__file__).resolve().parent/'cache_state_control13.py'),'hot','safetensors_inputs','g1hot13'],stdout=cache_log,stderr=subprocess.STDOUT,env=env,timeout=600,check=True)
        finally: cache_log.close()
        from validation_control13 import run
        if normal_smoke:
            run(out,audit_weights=False)
        else:
            assert not formal
            for weight_format in ('serverless_llm','safetensors'):
                os.environ['SLLM_SERVICE_WEIGHT_FORMAT'] = weight_format
                validation_out = out/weight_format
                validation_out.mkdir(exist_ok=False)
                run(validation_out)
finally:
    signal.alarm(0)  # Outer supervisor and root guard still bound cleanup.
    store_grace_deadline=None
    store_forced=False
    teardown=[]
    teardown_identity={}
    teardown_namespace={}
    identity_file=control/(label+'-identity.json')
    namespace_file=out/'store-numa-launch.json'
    if identity_file.exists():teardown_identity=json.loads(identity_file.read_text())
    if namespace_file.exists():teardown_namespace=json.loads(namespace_file.read_text())
    def teardown_event(stage,process=None,**values):
        row=dict(stage=stage,monotonic_ns=time.monotonic_ns(),run_id=manifest['run_id'],boot_id=manifest['runtime_identity']['boot_id'],namespace_pid=process.pid if process is not None else None,container_id=teardown_identity.get('container_id'),pid_namespace=teardown_namespace.get('pid_namespace'),**values)
        teardown.append(row)
        with (out/('pool-diagnostic-teardown.jsonl' if pool_diagnostic else 'service-teardown.jsonl')).open('a') as stream:
            stream.write(json.dumps(row)+'\n');stream.flush()
    for process in reversed(processes):
        if process.poll() is None:
            if process is store:
                teardown_event('interrupt_begin',process,signal=signal.SIGINT)
                process.send_signal(signal.SIGINT)
                sent_ns=time.monotonic_ns()
                store_grace_deadline=sent_ns+STORE_SHUTDOWN_GRACE*1000000000
                teardown_event('interrupt_sent',process,signal=signal.SIGINT,sent_ns=sent_ns,grace_deadline_ns=store_grace_deadline)
            else:
                teardown_event('term_begin',process)
                process.terminate()
                teardown_event('term_sent',process)
    # Private PID/network namespace: these Ray processes belong only to this run.
    teardown_event('ray_stop_begin')
    ray_stop=subprocess.run([python,'-m','ray.scripts.scripts','stop','--force'],env=env,timeout=30)
    teardown_event('ray_stop_end',returncode=getattr(ray_stop,'returncode',None))
    for process in processes:
        teardown_event('wait_begin',process)
        wait_seconds=max(0,(store_grace_deadline-time.monotonic_ns())/1e9) if process is store and store_grace_deadline is not None else 10
        try: process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            if process is store:store_forced=True
            teardown_event('kill_begin',process)
            process.kill()
            teardown_event('kill_sent',process)
            process.wait(timeout=5)
        teardown_event('wait_end',process,returncode=process.returncode)
    for output in logs: output.close()
    teardown_event('children_reaped',children=[dict(pid=p.pid,returncode=p.returncode) for p in processes])
    (out/('pool-diagnostic-teardown.json' if pool_diagnostic else 'service-teardown.json')).write_text(json.dumps(teardown,indent=2))

    if store is None or store_forced or store.returncode!=0:
        raise RuntimeError('Store did not exit gracefully')
