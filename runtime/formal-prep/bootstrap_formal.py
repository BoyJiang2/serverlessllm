import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Native normal-flags control or R2-admitted original-ID formal continuation."""
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
verify_input_layout()
config=json.loads((control/'run-manifest.json').read_text())
label=sys.argv[1]
assert label.isalnum() and label==config['service_worker_label']
purpose=config['execution_purpose']
assert purpose in ('normal-flags-control','formal-measurement')
normal_smoke=purpose=='normal-flags-control'
if normal_smoke:
    assert config['scope']=='normal-flags controls only; zero formal IDs'
    from formal_gate import verify_normal_reference
    verify_normal_reference(config,root)
else:
    from formal_gate import verify_gate
    verify_gate()  # Before private worker, Store pool or any attempt.
out=control/('service-bootstrap-'+label)
out.mkdir(exist_ok=False)
env=os.environ.copy()
for key in ('SLLM_VERIFY_WEIGHTS','SLLM_SERVICE_WEIGHT_AUDIT_DIR','SLLM_EXPORT_RAW_STORE','VLLM_ALLOW_INSECURE_SERIALIZATION'):
    assert not env.get(key),key+' must be absent'
restored=json.loads((root/'validation/store-restore-validation.json').read_text())
assert restored['status']=='full Store component restore validation passed'
pool_bytes,chunk_bytes=restored['pool_bytes'],restored['chunk_bytes']
env.update(STORAGE_PATH=str(root/'store'),
           RAY_TMPDIR=str(control/('ray-'+label)), RAY_USAGE_STATS_ENABLED='0')
pathlib.Path(env['STORAGE_PATH']).mkdir(exist_ok=True)
python = '/experiment/.venv/bin/python'
ip = socket.gethostbyname(socket.gethostname())
address = os.environ['SLLM_PRIVATE_RAY_ADDRESS']
env['RAY_ADDRESS'] = address
os.environ.update(RAY_TMPDIR=env['RAY_TMPDIR'],RAY_ADDRESS=address,RAY_USAGE_STATS_ENABLED='0')
processes, logs = [], []

def launch(name, args):
    output = (out/(name+'.log')).open('x')
    logs.append(output)
    process = subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,env=env)
    processes.append(process)
    return process

def wait_ready(check, seconds, process):
    stop = time.monotonic()+seconds
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
from budget_control13 import require_budget
require_budget(label,deadline-time.time())
signal.alarm(config['bootstrap_budget_seconds'])

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
    wait_ready(store_ready,1800,store)
    from listener_control13 import listener
    store_listener=listener(store.pid,8073)
    import grpc
    from sllm_store.proto import storage_pb2,storage_pb2_grpc
    with grpc.insecure_channel('127.0.0.1:8073') as channel:
        config=storage_pb2_grpc.StorageStub(channel).GetServerConfig(storage_pb2.GetServerConfigRequest(),timeout=5)
    assert config.mem_pool_size==pool_bytes and config.chunk_size==chunk_bytes
    status=pathlib.Path(f'/proc/{store.pid}/status').read_text()
    rss=int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:')))*1024
    assert rss>=pool_bytes, 'real pool not resident'
    assert 'Memory pool created with 716GB' in (out/'store.log').read_text()
    (out/'pool-ready.json').write_text(json.dumps(dict(monotonic_ns=time.monotonic_ns(),pool_bytes=pool_bytes,chunk_bytes=chunk_bytes,store_pid=store.pid,listener=store_listener,rpc_config=dict(mem_pool_size=config.mem_pool_size,chunk_size=config.chunk_size),rss_bytes=rss),indent=2))
    from store_numa_exec_v13 import audit_store_threads
    target = json.loads((out/'store-numa-launch.json').read_text())['target_cpu_affinity']
    (out/'store-affinity-ready.json').write_text(json.dumps(audit_store_threads(store.pid,target),indent=2))
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
    if normal_smoke:
        from attempt_state import checked_json
        from validation_control13 import run
        os.environ['SLLM_NORMAL_FLAGS_SMOKE']='1'
        for weight_format in ('serverless_llm','safetensors'):
            row=config['full_a4_proofs'][weight_format]
            proof_path=root/'validation/g1-reference'/row['path']
            proof=checked_json(proof_path,row['sha256'])
            assert proof['status']=='A4 native HTTP lifecycle checks passed' and proof['cycles']==2
            assert proof['weight_format']==weight_format and proof['weight_audit_performed'] is True
            os.environ['SLLM_FULL_A4_PROOF_FILE']=str(proof_path)
            os.environ['SLLM_SERVICE_WEIGHT_FORMAT']=weight_format
            target=out/weight_format;target.mkdir(exist_ok=False)
            run(target,audit_weights=False)
    else:
        from formal_trials import run
        run(out)

finally:
    for process in reversed(processes):
        if process.poll() is None: process.terminate()
    # Private PID/network namespace: these Ray processes belong only to this run.
    subprocess.run([python,'-m','ray.scripts.scripts','stop','--force'],env=env,timeout=30)
    for process in processes:
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for output in logs: output.close()


