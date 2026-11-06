"""Reproduce and repair the actual UID/read-only/cache profile without GPU devices."""
from pathlib import Path
import json,os,subprocess,time

root=Path('/home/boyjiang/sllm-v4-pro')
base=root/'evidence/runtime-user-profile-20261107'
base.mkdir(exist_ok=False)
image='sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
failed='436684667b632451475ce36701318d743530e79c290e78e86f03a0a8c65d7485'
def call(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=15).strip()
for name in ('passwd','group'):
 call('docker','cp',failed+':/etc/'+name,str(base/(name+'.original')))
 text=(base/(name+'.original')).read_text()
 field=2
 assert not any(line.split(':')[field]=='2147' for line in text.splitlines() if line and ':' in line)
 added='boyjiang:x:2147:2147::/runtime-cache/home:/bin/bash' if name=='passwd' else 'boyjiang:x:2147:'
 (base/name).write_text(text.rstrip()+'\n'+added+'\n')
probe='''import json,os,getpass,pwd,sys,multiprocessing
from pathlib import Path
import torch
from vllm import LLM,SamplingParams
import vllm
from torch._inductor.runtime.cache_dir_utils import cache_dir
def check(name):
 assert getpass.getuser()=='boyjiang' and pwd.getpwuid(os.getuid()).pw_name=='boyjiang'
 assert not list(Path('/dev').glob('nvidia*')) and not torch.cuda.is_initialized()
 path=Path(cache_dir());path.mkdir(parents=True,exist_ok=True)
 (path/(name+'.probe')).write_text('writable')
 assert vllm.__file__.startswith('/opt/sllm-v4/lib/python3.12/site-packages/')
 d=dict(uid=os.getuid(),gid=os.getgid(),getpass_user=getpass.getuser(),passwd_user=pwd.getpwuid(os.getuid()).pw_name,
  torch_version=torch.__version__,vllm_version=vllm.__version__,vllm_path=vllm.__file__,cache_dir=str(path),
  cuda_initialized=torch.cuda.is_initialized(),model_constructed=False,role=name)
 Path('/output/'+name+'.json').write_text(json.dumps(d,indent=2)+'\\n')
if __name__=='__main__':
 check('parent')
 p=multiprocessing.get_context('spawn').Process(target=check,args=('spawn-child',));p.start();p.join(45)
 assert p.exitcode==0
 print('UID_PROFILE_IMPORT_AND_SPAWN_PASS',flush=True)
'''
(base/'probe.py').write_text(probe)
reports=[]
for name in ('old-profile','fixed-profile'):
 directory=base/name;directory.mkdir(mode=0o775)
 cache=Path('/data/brick1/boyjiang/sllm-v4-pro-build')/('uid-profile-'+name+'-20261107')
 cache.mkdir(mode=0o775)
 for item in ('home','xdg','pycache','torchinductor','torch_extensions','triton'):(cache/item).mkdir(mode=0o775)
 command=['docker','create','--runtime','runc','--network','none','--cpus','2','--memory','8g',
  '--user','2147:2147','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
  '--tmpfs','/tmp:rw,exec,nosuid,size=4g,mode=1777','--entrypoint','/opt/sllm-v4/bin/python',
  '--mount','type=bind,src='+str(base)+',dst=/control,readonly',
  '--mount','type=bind,src='+str(directory)+',dst=/output',
  '--mount','type=bind,src='+str(cache)+',dst=/runtime-cache']
 env=dict(NVIDIA_VISIBLE_DEVICES='void',CUDA_VISIBLE_DEVICES='',VLLM_PLUGINS='',
  HOME='/runtime-cache/home',XDG_CACHE_HOME='/runtime-cache/xdg',PYTHONPYCACHEPREFIX='/runtime-cache/pycache',
  TRITON_CACHE_DIR='/runtime-cache/triton',PYTHONPATH='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
  LD_LIBRARY_PATH='/usr/local/cuda/compat:/usr/local/cuda/lib64',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
 if name=='fixed-profile':
  command+=['--mount','type=bind,src='+str(base/'passwd')+',dst=/etc/passwd,readonly',
            '--mount','type=bind,src='+str(base/'group')+',dst=/etc/group,readonly']
  env.update(USER='boyjiang',LOGNAME='boyjiang',TORCHINDUCTOR_CACHE_DIR='/runtime-cache/torchinductor',
             TORCH_EXTENSIONS_DIR='/runtime-cache/torch_extensions')
 for k,v in env.items():command+=['--env',k+'='+v]
 command += [image,'-u','/control/probe.py']
 cid=call(*command)
 before=json.loads(call('docker','inspect',cid))[0]
 assert not before['HostConfig'].get('DeviceRequests') and not before['HostConfig'].get('Devices')
 started=time.monotonic()
 with (directory/'run.log').open('x') as log:
  process=subprocess.run(['docker','start','-a',cid],stdout=log,stderr=subprocess.STDOUT,timeout=90)
 final=json.loads(call('docker','inspect',cid))[0]
 log=(directory/'run.log').read_text()
 if name=='old-profile':assert final['State']['ExitCode']!=0 and 'getpwuid' in log,log[-2000:]
 else:
  assert final['State']['ExitCode']==0 and 'UID_PROFILE_IMPORT_AND_SPAWN_PASS' in log,log[-2000:]
  assert (directory/'parent.json').exists() and (directory/'spawn-child.json').exists()
 reports.append(dict(name=name,container_id=cid,before=before,final=final,elapsed_seconds=time.monotonic()-started,
                     expected_result_observed=True,gpu_devices_exposed=False))
 call('docker','rm',cid)
(base/'result.json').write_text(json.dumps(dict(reports=reports,old_failure_reproduced=True,
 fixed_import_and_spawn_passed=True,model_constructed=False,gpu_retry=False,
 cpu_cuda_library_scope='Official compat library only for no-device CPU import; not real driver/GPU qualification'),indent=2)+'\n')
print(json.dumps(dict(old_failure_reproduced=True,fixed_import_and_spawn_passed=True,gpu_retry=False)))
