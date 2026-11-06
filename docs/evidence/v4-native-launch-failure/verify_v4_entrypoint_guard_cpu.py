from pathlib import Path
import json
import os
import subprocess
import sys
import time

root=Path('/home/boyjiang/sllm-v4-pro/code')
base=root.parent/'evidence/entrypoint-guard-fix-cpu-20261107'
base.mkdir(exist_ok=False)
image='sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
env=dict(os.environ,PYTHONPATH=str(root/'sllm_store')+':'+str(root/'benchmarks'))
def call(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=10).strip()
reports=[]
for purpose in ('normal','deadline'):
 directory=base/purpose;directory.mkdir()
 script="import sys,json,glob,time;assert sys.argv[1:] == ['native-entry-arg'];assert not glob.glob('/dev/nvidia*');print(json.dumps(dict(executable=sys.executable,arguments=sys.argv[1:],gpu_devices=[])),flush=True);time.sleep("+('1' if purpose=='normal' else '60')+")"
 cid=call('docker','create','--runtime','runc','--network','none','--cpus','1','--memory','128m',
  '--cap-drop','ALL','--security-opt','no-new-privileges','--user','2147:2147',
  '--env','NVIDIA_VISIBLE_DEVICES=void','--env','CUDA_VISIBLE_DEVICES=',
  '--entrypoint','/opt/sllm-v4/bin/python','--label','sllm.v4.guard=native-reference',
  image,'-u','-c',script,'native-entry-arg')
 before=json.loads(call('docker','inspect',cid))[0]
 assert before['Path']=='/opt/sllm-v4/bin/python' and before['Args'][0]=='-u'
 assert not before['HostConfig'].get('DeviceRequests') and not before['HostConfig'].get('Devices')
 deadline=time.monotonic_ns()+5_000_000_000
 with (directory/'guard.log').open('x') as log:
  guard=subprocess.Popen(['python3',str(root/'benchmarks/v4_container_guard.py'),cid,str(deadline),str(directory/'guard')],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  while not (directory/'guard/armed.json').exists():
   assert guard.poll() is None and time.monotonic_ns()<deadline
   time.sleep(.05)
  call('docker','start',cid)
  guard_exit=guard.wait(timeout=12)
 logs=call('docker','logs',cid)
 assert json.loads(logs.splitlines()[0])['executable']=='/opt/sllm-v4/bin/python'
 result=json.loads((directory/'guard/result.json').read_bytes())
 after=json.loads(call('docker','inspect',cid))[0]
 assert result['container_stopped'] and after['State']['Pid']==0
 assert result['reason']==('container_exit' if purpose=='normal' else 'deadline')
 assert guard_exit==(0 if purpose=='normal' else 1)
 report=dict(purpose=purpose,container_id=cid,actual_python_entrypoint=True,logs=logs,
  before=before,after=after,guard_result=result,no_gpu=True)
 (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n');reports.append(report)
 call('docker','rm',cid)
with (base/'unit.log').open('x') as log:
 p=subprocess.run(['python3','-m','unittest','discover','-s','sllm_store/tests/python','-p','test_v4_container_guard.py','-v'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
assert p.returncode==0
(base/'result.json').write_text(json.dumps(dict(actual_cpu_cases=2,unit_tests=6,reports=reports,gpu_model_retry=False),indent=2)+'\n')
print(json.dumps(dict(actual_cpu_cases=2,unit_tests=6,gpu_model_retry=False)))
