"""Run two CPU-only containers using the existing exact-ID deadline guardian."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path('/home/boyjiang/sllm-v4-pro')
CODE=ROOT/'code'
IMAGE='sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
base=ROOT/'evidence'/('service-ray-cpu-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
lease=(ROOT/'native-run.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
base.mkdir();output=base/'output';output.mkdir()
origin=time.monotonic_ns();deadline=origin+180_000_000_000
network=base.name
roles={};guards={}
def call(*args,timeout=10):
    return subprocess.check_output(args,stderr=subprocess.STDOUT,text=True,timeout=timeout).strip()
def inspect(cid):
    row,=json.loads(call('docker','inspect',cid,timeout=5))
    assert row['Id']==cid and row['Image']==IMAGE
    return row
def save(name,value):
    with (base/name).open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
try:
    network_id=call('docker','network','create','--internal','--label','sllm.v4.cpu='+base.name,network)
    save('network.json',dict(name=network,id=network_id))
    source={str(p.relative_to(CODE)):hashlib.sha256(p.read_bytes()).hexdigest()
            for package in ('sllm','sllm_store/sllm_store') for p in (CODE/package).rglob('*.py')}
    for name in ('benchmarks/v4_ray_preflight.py','benchmarks/v4_container_guard.py'):
        source[name]=hashlib.sha256((CODE/name).read_bytes()).hexdigest()
    save('source.json',dict(head=call('git','-C',str(CODE),'rev-parse','HEAD'),python_sha256=source,
                           cpu_only=True,weights_mounted=False,formal_id_consumed=False))
    profile=ROOT/'evidence/store-recovery-20261107T063304Z'
    for role in ('head','worker'):
        command=['docker','create','--name',base.name+'-'+role,'--runtime','runc','--network',network,
            '--cpus','2','--memory',str(4*1024**3),'--memory-swap',str(4*1024**3),
            '--pids-limit','1024','--shm-size','512m','--read-only','--restart','no','--init',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--user','2147:2147',
            '--label','sllm.v4.guard=native-service','--tmpfs','/tmp:rw,exec,nosuid,size=1g,mode=1777']
        for value in ('NVIDIA_VISIBLE_DEVICES=void','CUDA_VISIBLE_DEVICES=','HOME=/tmp','USER=boyjiang','LOGNAME=boyjiang',
            'LD_LIBRARY_PATH=/usr/local/cuda/compat:/usr/local/cuda/lib64','OMP_NUM_THREADS=1','MKL_NUM_THREADS=1',
            'PYTHONDONTWRITEBYTECODE=1','RAY_USAGE_STATS_ENABLED=0','VLLM_PLUGINS=sllm_v4_store',
            'PYTHONPATH=/workspace/benchmarks'):
            command+=['--env',value]
        mounts=[(str(CODE),'/workspace',True),(str(base),'/control',True),(str(output),'/output',False),
                (str(CODE/'sllm'),'/opt/sllm-v4/lib/python3.12/site-packages/sllm',True),
                (str(profile/'run-user.passwd'),'/etc/passwd',True),(str(profile/'run-user.group'),'/etc/group',True)]
        for p in (CODE/'sllm_store/sllm_store').rglob('*.py'):
            mounts.append((str(p),'/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/'+str(p.relative_to(CODE/'sllm_store/sllm_store')),True))
        for src,dst,readonly in mounts:
            command+=['--mount','type=bind,src='+src+',dst='+dst+(',readonly' if readonly else '')]
        command+=['--entrypoint','/opt/sllm-v4/bin/python',IMAGE,'-u','/workspace/benchmarks/v4_ray_preflight.py',role]
        cid=call(*command,timeout=20);assert len(cid)==64
        row=inspect(cid);assert row['State']['Status']=='created' and not row['HostConfig']['DeviceRequests']
        roles[role]=dict(container_id=cid,created=row['Created'],command=command)
        env=dict(os.environ,PYTHONPATH=str(CODE/'sllm_store'))
        with (base/(role+'-guard.log')).open('xb') as log:
            guards[role]=subprocess.Popen(['python3',str(CODE/'benchmarks/v4_container_guard.py'),cid,str(deadline),
                str(base/(role+'-guard')),'--purpose','native-service'],stdout=log,stderr=subprocess.STDOUT,
                start_new_session=True,env=env)
        while not (base/(role+'-guard/armed.json')).exists():
            assert guards[role].poll() is None and time.monotonic_ns()<deadline
            time.sleep(.1)
    save('launch.json',dict(roles=roles,begin_ns=origin,deadline_ns=deadline,cpu_limit_total=4,memory_limit_total=8*1024**3))
    (ROOT/'evidence/service-ray-cpu-current-handle.json').write_text(json.dumps(dict(base=str(base),roles=roles),indent=2)+'\n')
    print(json.dumps(dict(base=str(base),containers={role:value['container_id'] for role,value in roles.items()})),flush=True)
    for role in ('head','worker'):call('docker','start',roles[role]['container_id'],timeout=20)
    while time.monotonic_ns()<deadline+10_000_000_000:
        states={role:inspect(value['container_id'])['State'] for role,value in roles.items()}
        if all(state['Status'] in ('exited','dead') for state in states.values()):break
        if any(state['Status'] in ('exited','dead') and state['ExitCode']!=0 for state in states.values()):
            raise RuntimeError('CPU preflight role failed')
        time.sleep(1)
    else:raise TimeoutError('CPU preflight did not reach terminal state')
    assert all(state['ExitCode']==0 for state in states.values())
    assert (output/'cpu-preflight.json').exists()
    save('candidate.json',dict(cpu_two_node_preflight_passed=True,gpu_or_model_started=False,
                             native_sllm_service_qualified=False,formal_id_consumed=False))
except BaseException as error:
    save('failure.json',dict(error=repr(error),failed_ns=time.monotonic_ns()))
    raise
finally:
    cleanup={}
    for role,value in roles.items():
        cid=value['container_id']
        try:
            row=inspect(cid)
            if row['State']['Running']:
                call('docker','kill',cid,timeout=5)
                row=inspect(cid)
            save(role+'-final-inspect.json',row)
            logs=subprocess.run(['docker','logs',cid],capture_output=True,text=True,timeout=5)
            (base/(role+'-container.log')).write_text(logs.stdout+logs.stderr)
            if role in guards:guards[role].wait(timeout=10)
            assert not row['State']['Running'] and row['State']['Pid']==0
            call('docker','rm',cid,timeout=10)
            cleanup[role]=dict(stopped=True,removed=True,exit_code=row['State']['ExitCode'])
        except BaseException as error:cleanup[role]=dict(error=repr(error))
    try:
        info=json.loads(call('docker','network','inspect',network))[0]
        assert info['Id']==network_id and not info['Containers']
        call('docker','network','rm',network)
        cleanup['network_removed']=True
    except BaseException as error:cleanup['network_error']=repr(error)
    save('cleanup.json',cleanup)
    print(json.dumps(dict(base=str(base),cleanup=cleanup)),flush=True)
