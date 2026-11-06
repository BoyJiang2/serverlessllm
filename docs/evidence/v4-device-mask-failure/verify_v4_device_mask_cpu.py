from pathlib import Path
import hashlib,json,subprocess

root=Path('/home/boyjiang/sllm-v4-pro')
base=root/'evidence/device-mask-cpu-20261107'
base.mkdir(exist_ok=False)
profile=root/'code/docs/evidence/v4-runtime-user-failure/cpu-profile'
failed=root/'evidence/native-reference-20261106T200833Z'
rows=json.loads((failed/'preparation.json').read_bytes())['gpu_initial']['rows']
uuids=[row[1] for row in rows];indices=[int(row[0]) for row in rows]
assert indices==list(range(8)) and len(set(uuids))==8
(base/'inputs.json').write_text(json.dumps(dict(uuids=uuids,indices=indices),indent=2)+'\n')
probe='''import inspect,json,os,hashlib,glob
from pathlib import Path
import torch
from vllm.platforms.cuda import CudaPlatform
assert not glob.glob('/dev/nvidia*') and not torch.cuda.is_initialized()
data=json.loads(Path('/control/inputs.json').read_bytes())
os.environ['CUDA_VISIBLE_DEVICES']=','.join(data['uuids'])
try:
 CudaPlatform.device_id_to_physical_device_id(0)
except ValueError as error:negative=repr(error)
else:raise RuntimeError('Expected upstream UUID parsing failure was not reproduced')
os.environ['CUDA_VISIBLE_DEVICES']=','.join(map(str,data['indices']))
mapped=[CudaPlatform.device_id_to_physical_device_id(i) for i in range(8)]
assert mapped==data['indices'] and not torch.cuda.is_initialized()
source=Path(inspect.getsourcefile(CudaPlatform.device_id_to_physical_device_id))
result=dict(uuid_failure_reproduced=negative,integer_mapping=mapped,cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES'],
 source_path=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),cuda_initialized=False,model_constructed=False,
 scope='Actual installed fixed-version CUDA platform mapping function; no GPU/NVML capability calls or model inspector execution')
Path('/output/result.json').write_text(json.dumps(result,indent=2)+'\\n')
print(json.dumps(result),flush=True)
'''
(base/'probe.py').write_text(probe)
cache=Path('/data/brick1/boyjiang/sllm-v4-pro-build/device-mask-cpu-20261107');cache.mkdir(mode=0o775)
for name in ('home','xdg','pycache','torchinductor','torch_extensions'):(cache/name).mkdir(mode=0o775)
image='sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
cmd=['docker','create','--runtime','runc','--network','none','--user','2147:2147','--read-only',
 '--cpus','2','--memory','8g','--cap-drop','ALL','--security-opt','no-new-privileges',
 '--tmpfs','/tmp:rw,exec,nosuid,size=4g,mode=1777','--entrypoint','/opt/sllm-v4/bin/python',
 '--mount','type=bind,src='+str(base)+',dst=/control,readonly',
 '--mount','type=bind,src='+str(base)+',dst=/output',
 '--mount','type=bind,src='+str(cache)+',dst=/runtime-cache']
for name in ('passwd','group'):cmd+=['--mount','type=bind,src='+str(profile/name)+',dst=/etc/'+name+',readonly']
env=dict(NVIDIA_VISIBLE_DEVICES='void',CUDA_VISIBLE_DEVICES='',VLLM_PLUGINS='',USER='boyjiang',LOGNAME='boyjiang',HOME='/runtime-cache/home',XDG_CACHE_HOME='/runtime-cache/xdg',PYTHONPYCACHEPREFIX='/runtime-cache/pycache',TORCHINDUCTOR_CACHE_DIR='/runtime-cache/torchinductor',TORCH_EXTENSIONS_DIR='/runtime-cache/torch_extensions',LD_LIBRARY_PATH='/usr/local/cuda/compat:/usr/local/cuda/lib64',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONPATH='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
for k,v in env.items():cmd+=['--env',k+'='+v]
cmd+=[image,'-u','/control/probe.py']
def call(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=15).strip()
cid=call(*cmd);before=json.loads(call('docker','inspect',cid))[0]
assert not before['HostConfig'].get('DeviceRequests') and not before['HostConfig'].get('Devices')
with (base/'run.log').open('x') as log:p=subprocess.run(['docker','start','-a',cid],stdout=log,stderr=subprocess.STDOUT,timeout=60)
final=json.loads(call('docker','inspect',cid))[0];assert final['State']['ExitCode']==0,(base/'run.log').read_text()[-3000:]
result=json.loads((base/'result.json').read_bytes())
expected=hashlib.sha256((root/'engine-src/vllm-132765e35606/vllm/platforms/interface.py').read_bytes()).hexdigest()
assert result['source_sha256']==expected
(base/'container-receipt.json').write_text(json.dumps(dict(before=before,final=final,installed_mapper_matches_fixed_source=True,gpu_retry=False),indent=2)+'\n')
call('docker','rm',cid)
print(json.dumps(result))
