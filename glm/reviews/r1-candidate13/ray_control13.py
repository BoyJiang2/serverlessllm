"""Untimed Ray readiness/IP control: no Store, checkpoint reads, or inference."""
import datetime
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
import traceback

root = pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')
label = sys.argv[1]
assert label.isalnum()
out = root/('ray-only-'+label)
out.mkdir(exist_ok=False)
ip = socket.gethostbyname(socket.gethostname())
address = os.environ['SLLM_PRIVATE_RAY_ADDRESS']
env = os.environ.copy()
env.update(RAY_TMPDIR=str(root/('ray-'+label)),RAY_ADDRESS=address,RAY_USAGE_STATS_ENABLED='0')
os.environ.update(RAY_TMPDIR=env['RAY_TMPDIR'],RAY_ADDRESS=address,RAY_USAGE_STATS_ENABLED='0')
record = dict(scope='Ray-only control; no Store or inference; GPU resources advertised but no model allocated',
              begin_ns=time.monotonic_ns(),worker_ip=ip,head_address=address,
              boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip())
def expired(signum,frame):
    raise TimeoutError('Ray-only control bounded to 240s')
signal.signal(signal.SIGALRM,expired)
signal.alarm(240)
worker = None
ray = None
with (out/'ray-worker.log').open('x') as log:
    try:
        worker = subprocess.Popen(['/experiment/.venv/bin/python','-m','ray.scripts.scripts','start',
            '--address='+address,'--node-ip-address='+ip,'--num-cpus=16','--num-gpus=8',
            '--resources={"worker_node":1,"worker_id_0":1}',
            '--min-worker-port=22000','--max-worker-port=22999','--object-store-memory=268435456',
            '--disable-usage-stats','--block'],env=env,stdout=log,stderr=subprocess.STDOUT)
        record['worker_pid'] = worker.pid
        stop = time.monotonic()+60
        while time.monotonic()<stop:
            if worker.poll() is not None:
                raise RuntimeError(f'worker exited {worker.returncode} before readiness')
            if 'Ray runtime started.' in (out/'ray-worker.log').read_text():
                break
            time.sleep(.2)
        else:
            raise TimeoutError('worker runtime-ready marker absent')
        record['worker_runtime_ready_ns'] = time.monotonic_ns()
        import ray
        record['driver_connect_begin_ns'] = time.monotonic_ns()
        ray.init(address=address,_node_ip_address=ip,log_to_driver=False)
        record['driver_connected_ns'] = time.monotonic_ns()
        record['driver_node_id'] = str(ray.get_runtime_context().get_node_id())
        nodes = [row for row in ray.nodes() if row['Alive']]
        assert len(nodes)==2 and len({row['NodeManagerAddress'] for row in nodes})==2
        own = next(row for row in nodes if row['NodeManagerAddress']==ip)
        assert own['NodeID'] == record['driver_node_id'] and own['Resources']['GPU']==8
        assert own['Resources']['worker_id_0']==1
        record['nodes'] = nodes
        with (out/'stability.jsonl').open('x') as stream:
            for number in range(61):
                assert worker.poll() is None
                alive = [row for row in ray.nodes() if row['Alive']]
                assert {row['NodeID'] for row in alive} == {row['NodeID'] for row in nodes}
                stream.write(json.dumps(dict(second=number*2,monotonic_ns=time.monotonic_ns(),
                    memory_events=pathlib.Path('/sys/fs/cgroup/memory.events').read_text()))+'\n')
                stream.flush()
                if number<60:
                    time.sleep(2)
        record['node_ip_files'] = {str(path):path.read_text() for path in pathlib.Path(env['RAY_TMPDIR']).rglob('node_ip_address.json')}
        record['status'] = 'passed'
    except BaseException as error:
        record.update(status='failed',error=repr(error),traceback=traceback.format_exc())
        raise
    finally:
        signal.alarm(0)
        record.update(end_ns=time.monotonic_ns(),ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        (out/'result.json').write_text(json.dumps(record,indent=2))
        if ray is not None:
            ray.shutdown()
        if worker is not None:
            worker.terminate()
        subprocess.run(['/experiment/.venv/bin/python','-m','ray.scripts.scripts','stop','--force'],env=env,timeout=30)
        if worker is not None:
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
