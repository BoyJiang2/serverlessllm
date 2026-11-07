"""Two real Ray nodes and source imports only; no Store daemon or model weights."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import time
import traceback


def worker_import_probe():
    import importlib.metadata
    import torch
    from sllm.controller import SllmController
    from sllm.backends.vllm_backend import VllmBackend
    from sllm_store import _C
    assert not torch.cuda.is_available() and not torch.cuda.is_initialized()
    assert _C.v4_raw_restore_contract == 1
    return dict(hostname=socket.gethostname(), controller=SllmController.__module__,
                backend=VllmBackend.__module__, cuda_initialized=False,
                native_restore_contract=_C.v4_raw_restore_contract,
                packages={name:importlib.metadata.version(name) for name in ('ray','vllm','torch')})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role',choices=('head','worker'))
    args=parser.parse_args()
    role=args.role
    output=Path('/output')
    armed=json.loads(Path('/control',role+'-guard/armed.json').read_bytes())
    assert armed['purpose']=='native-service'
    assert socket.gethostname()==armed['identity']['container_id'][:12]
    deadline=armed['deadline_ns']
    def remaining(limit):
        value=min(limit,(deadline-time.monotonic_ns())/1e9-3)
        if value<=0:raise TimeoutError('Ray CPU preflight deadline exhausted')
        return value
    def save(name,value):
        with (output/name).open('x') as stream:json.dump(value,stream);stream.write('\n')
    def wait(name):
        while not (output/name).exists():
            remaining(1)
            time.sleep(.2)
        return json.loads((output/name).read_bytes())
    def ray_command(command,name):
        with (output/name).open('x') as stream:
            result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,
                                  timeout=remaining(60))
        assert result.returncode==0,(name,result.returncode)
    address=socket.gethostbyname(socket.gethostname())
    ray_binary='/opt/sllm-v4/bin/ray'
    command=[ray_binary,'start','--node-ip-address='+address,'--num-cpus=2',
             '--num-gpus=0','--object-store-memory=268435456','--disable-usage-stats']
    if role=='head':
        command+=['--head','--port=6379','--include-dashboard=false',
                  '--resources={"control_node":1}']
    else:
        head=wait('head-ray-ready.json')
        command+=['--address='+head['address']+':6379',
                  '--resources={"worker_node":1,"worker_id_v4cpu":1}']
    ray_started=False
    try:
        ray_command(command,role+'-ray-start.log');ray_started=True
        save(role+'-ray-ready.json',dict(address=address,hostname=socket.gethostname()))
        if role=='worker':
            wait('stop-worker.json')
        else:
            worker=wait('worker-ray-ready.json')
            import ray
            from sllm.utils import get_worker_nodes
            from sllm.controller import SllmController
            ray.init(address=address+':6379',namespace='sllm-v4-cpu-preflight')
            nodes=[node for node in ray.nodes() if node['Alive']]
            found=get_worker_nodes()
            assert len(nodes)==2 and set(found)=={'v4cpu'}
            probe=ray.remote(num_cpus=1,resources={'worker_id_v4cpu':.01})(worker_import_probe)
            actual=ray.get(probe.remote(),timeout=remaining(60))
            assert actual['hostname']==worker['hostname']
            save('cpu-preflight.json',dict(actual_worker_probe=actual,worker_nodes=found,
                 actual_alive_nodes=len(nodes),head_hostname=socket.gethostname(),
                 head_controller_module=SllmController.__module__,
                 gpu_or_model_started=False,weights_mounted=False,
                 native_sllm_service_qualified=False,
                 scope='Actual two-node Ray discovery and remote source/native imports only; no Store, controller.start, HTTP, model or lifecycle proof'))
            ray.shutdown()
    except BaseException as error:
        save(role+'-failure.json',dict(error=repr(error),traceback=traceback.format_exc()))
        raise
    finally:
        if role=='head':
            save('stop-worker.json',dict(requested_ns=time.monotonic_ns()))
            if ray_started:wait('worker-stopped.json')
        if ray_started:
            ray_command([ray_binary,'stop','--grace-period=15'],role+'-ray-stop.log')
        save(role+'-stopped.json',dict(completed_ns=time.monotonic_ns()))


if __name__=='__main__':
    main()
