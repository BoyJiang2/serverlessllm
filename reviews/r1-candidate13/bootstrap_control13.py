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

root = pathlib.Path('/experiment')
label = sys.argv[1] if len(sys.argv)>1 else 'probe'
assert label.isalnum()
out = pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')/('service-bootstrap-'+label)
out.mkdir(exist_ok=False)
env = os.environ.copy()
validate_model = os.environ.get('SLLM_VALIDATE_FULL_MODEL') == '1'
formal = bool(os.environ.get('SLLM_FORMAL_GATE_FILE'))
normal_smoke = os.environ.get('SLLM_NORMAL_FLAGS_SMOKE') == '1'
assert not (formal and normal_smoke)
if formal:
    assert validate_model
    from formal_trials_v11 import verify_gate
    verify_gate()  # Before private Ray worker, Store pool, or formal trial dispatch.
pool_bytes = 67108864
chunk_bytes = 32*1024*1024
if validate_model:
    restored = json.loads((root/'store-restore-validation.json').read_text())
    assert restored['status'] == 'full Store component restore validation passed'
    pool_bytes, chunk_bytes = restored['pool_bytes'], restored['chunk_bytes']
    if env.get('SLLM_SERVICE_WEIGHT_FORMAT') == 'safetensors' and not (
            formal or env.get('SLLM_USE_COMMON_POOL') == '1'):
        pool_bytes = 67108864
        chunk_bytes = 32*1024*1024
env.update(STORAGE_PATH=str(root/'store' if validate_model else out/'store'),
           RAY_TMPDIR=str(pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')/('ray-'+label)), RAY_USAGE_STATS_ENABLED='0')
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
from resource_window_v3 import load_window
deadline = datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
budget = int(deadline-time.time()-120)
assert budget > (3600 if validate_model else 300)
signal.alarm(min(2400 if validate_model else 300,budget))

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
    store = launch('store',[python,'/experiment/store_numa_exec_v10.py',str(out/'store-numa-launch.json'),python,'-u','/experiment/run_store_server.py','--storage-path',env['STORAGE_PATH'],
                            '--pool-bytes',str(pool_bytes),'--chunk-bytes',str(chunk_bytes),'--host','0.0.0.0'])
    def store_ready():
        with socket.create_connection(('127.0.0.1',8073),timeout=1): return True
    wait_ready(store_ready,1800 if validate_model else 30,store)
    from store_numa_exec_v10 import audit_store_threads
    target = json.loads((out/'store-numa-launch.json').read_text())['target_cpu_affinity']
    (out/'store-affinity-ready.json').write_text(json.dumps(audit_store_threads(store.pid,target),indent=2))
    server = launch('sllm',[python,'-u','-c','from sllm.cli._cli_utils import start_server; start_server(host="127.0.0.1",port=8343)'])
    def healthy():
        with urllib.request.urlopen('http://127.0.0.1:8343/health',timeout=2) as response:
            return json.load(response)=={'status':'ok'}
    wait_ready(healthy,90,server)
    (out/'result.json').write_text(json.dumps({'status':'native control plane healthy','physical_host':'qjhs3',
        'ray_logical_roles':2,'gpus':8,'model_registered':False,'inference_performed':False,
        'entrypoint':'sllm.cli._cli_utils.start_server + native FastAPI /health'},indent=2))
    print('native_control_plane_healthy_no_model_registered',flush=True)
    if validate_model:
        if formal:
            from formal_trials_v11 import run
        else:
            from service_model_validation import run
        if normal_smoke:
            run(out,audit_weights=False)
        else:
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


