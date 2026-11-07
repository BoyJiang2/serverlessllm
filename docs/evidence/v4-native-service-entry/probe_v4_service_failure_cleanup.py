"""Execute the production failure handler against two CPU-only bounded containers."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

r=Path('/home/boyjiang/sllm-v4-pro/code');sys.path[:0]=[str(r/'benchmarks'),str(r/'sllm_store')]
import v4_service_once as service
from sllm_store.event_trace import process_identity

b=r.parent/'evidence'/('service-failure-cpu-'+str(time.monotonic_ns()))
b.mkdir();origin=time.monotonic_ns();deadline=origin+180_000_000_000
roles={};groups={};workers={};guards={}
baseline=service.gpu_snapshot();assert len(baseline['rows'])==8 and not baseline['apps'] and all(int(row[4])==0 for row in baseline['rows'])
source=(r/'benchmarks/v4_service_once.py').read_bytes()
tree=ast.parse(source)
main=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main')
outer=next(node for node in main.body if isinstance(node,ast.Try))
handler=outer.handlers[0]
program=compile(ast.Module(body=handler.body,type_ignores=[]),'actual-service-failure-handler','exec')
try:
    for role in ('head','worker'):
        body='import time;time.sleep(4);raise SystemExit(7)' if role=='head' else 'import time;time.sleep(120)'
        cid=service.call('docker','create','--name',b.name+'-'+role,'--runtime','runc','--network','none',
            '--cpus','1','--memory','268435456','--memory-swap','268435456','--pids-limit','64',
            '--read-only','--restart','no','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--user','2147:2147','--env','NVIDIA_VISIBLE_DEVICES=void','--label','sllm.v4.guard=native-service',
            '--entrypoint','/opt/sllm-v4/bin/python',service.IMAGE,'-c',body)
        row=service.inspect(cid);assert not row['HostConfig']['DeviceRequests']
        roles[role]=dict(container_id=cid,created=row['Created'],image=row['Image'])
        with (b/(role+'-guard.log')).open('x') as stream:
            guards[role]=subprocess.Popen(['python3',str(r/'benchmarks/v4_container_guard.py'),cid,str(deadline),str(b/(role+'-guard')),'--purpose','native-service'],
                stdout=stream,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,PYTHONPATH=str(r/'sllm_store')))
        while not (b/(role+'-guard/armed.json')).exists():
            assert guards[role].poll() is None and time.monotonic_ns()<deadline
            time.sleep(.1)
    service.write_once(b/'launch.json',dict(scope='CPU negative control; no GPU requests or model/data mounts',roles=roles))
    for role,value in roles.items():
        service.call('docker','start',value['container_id'])
        state=service.inspect(value['container_id'])['State'];assert state['Pid']
        groups[role]=service.group_for(state['Pid'],value['container_id'])
        for proc in service.process_rows(groups[role]):workers[(proc['pid'],proc['start_ticks'])]=proc
    while service.inspect(roles['head']['container_id'])['State']['Running']:
        assert time.monotonic_ns()<deadline
        time.sleep(.2)
    assert service.inspect(roles['head']['container_id'])['State']['ExitCode']==7
    namespace=dict(vars(service),base=b,roles=roles,groups=groups,workers=workers,
                   baseline=baseline,clock=process_identity(),origin=origin,deadline=deadline)
    try:
        raise RuntimeError('Injected CPU head failure; not a native model failure')
    except RuntimeError as injected:
        namespace['error']=injected
        try:exec(program,namespace)
        except RuntimeError as returned:assert returned is injected
        else:raise AssertionError('Primary failure was swallowed')
    failure=json.loads((b/'failure.json').read_bytes())
    assert failure['release_observation_passed'] and not failure['formal_id_consumed']
    spans={}
    for role in roles:
        release=json.loads((b/(role+'-release/result.json')).read_bytes())
        assert release['native_release_observation_passed']
        spans[role]=release['coverage']['sample_span_seconds'];assert spans[role]>=60
    report=dict(production_handler_sha256=hashlib.sha256(source).hexdigest(),base=str(b),
        cpu_negative_control_passed=True,primary_exit_code=7,peer_killed=True,
        release_spans_seconds=spans,gpu_allocated=False,model_or_weights_used=False,
        scope='Actual production exception handler, exact-CID CPU peer kill and concurrent release only; no CUDA failure or native service qualification')
    service.write_once(b/'cpu-negative-review.json',report);print(json.dumps(report),flush=True)
finally:
    cleanup={}
    for role,value in roles.items():
        row=service.inspect(value['container_id'])
        if row['State']['Running']:service.call('docker','kill',value['container_id']);row=service.inspect(value['container_id'])
        service.write_once(b/(role+'-final.json'),row)
        guards[role].wait(timeout=10)
        assert not row['State']['Running'] and row['State']['Pid']==0
        cleanup[role]=dict(exit_code=row['State']['ExitCode'],removed=service.call('docker','rm',value['container_id'])==value['container_id'])
    service.write_once(b/'cleanup.json',cleanup);print(json.dumps(dict(base=str(b),cleanup=cleanup)),flush=True)
