from pathlib import Path
import hashlib,json,os,platform,subprocess,sys,time

root=Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0]=[str(root/'sllm_store'),str(root/'benchmarks')]
from v4_native_release import cgroup_telemetry
base=root.parent/'evidence/kernel-telemetry-cpu-20261107'
base.mkdir(exist_ok=False)
def call(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=10).strip()
image='sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
cid=call('docker','create','--runtime','runc','--network','none','--cpus','1','--memory','128m',
 '--env','NVIDIA_VISIBLE_DEVICES=void','--env','CUDA_VISIBLE_DEVICES=',
 '--cap-drop','ALL','--security-opt','no-new-privileges','--user','2147:2147',
 '--entrypoint','/opt/sllm-v4/bin/python',image,'-u','-c',
 "import glob,time;assert not glob.glob('/dev/nvidia*');print('CPU_ONLY',flush=True);time.sleep(4)")
before=json.loads(call('docker','inspect',cid))[0]
assert not before['HostConfig'].get('DeviceRequests') and not before['HostConfig'].get('Devices')
call('docker','start',cid)
state=json.loads(call('docker','inspect',cid))[0]['State'];assert state['Pid']>0
group=Path('/sys/fs/cgroup')/next(l.split(':',2)[2] for l in Path('/proc',str(state['Pid']),'cgroup').read_text().splitlines() if l.startswith('0::')).lstrip('/')
assert cid in str(group)
rows=[]
for _ in range(3):
 values=cgroup_telemetry(group);assert int(values['memory.current'])>0 and int(values['pids.current'])>0
 assert values['memory.peak'] is None, 'Target kernel capabilities changed: review instead of assuming'
 rows.append(dict(monotonic_ns=time.monotonic_ns(),cgroup=values));time.sleep(.2)
call('docker','wait',cid)
after=json.loads(call('docker','inspect',cid))[0]
assert after['State']['ExitCode']==0 and after['State']['Pid']==0
logs=call('docker','logs',cid);assert logs=='CPU_ONLY'
report=dict(kernel=platform.release(),container_id=cid,cgroup=str(group),samples=rows,
 before=before,after=after,logs=logs,actual_cpu_counter_path_passed=True,kernel_peak_supported=False,
 gpu_or_model_run=False,scope='Real CPU container and same host cgroup telemetry helper; memory.peak remains unknown, not zero')
(base/'result.json').write_text(json.dumps(report,indent=2)+'\n')
call('docker','rm',cid)
with (base/'unit.log').open('x') as log:
 p=subprocess.run(['python3','-m','unittest','discover','-s','sllm_store/tests/python','-p','test_v4_native_release.py','-v'],cwd=root,env=dict(os.environ,PYTHONPATH='sllm_store:benchmarks'),stdout=log,stderr=subprocess.STDOUT)
assert p.returncode==0
print(json.dumps({k:v for k,v in report.items() if k in ('kernel','actual_cpu_counter_path_passed','kernel_peak_supported','gpu_or_model_run')}))
