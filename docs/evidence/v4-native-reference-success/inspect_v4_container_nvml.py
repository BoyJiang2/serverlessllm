from pathlib import Path
import json,subprocess

root=Path('/home/boyjiang/sllm-v4-pro')
base=root/'evidence/container-nvml-identity-20261107';base.mkdir(exist_ok=False)
prep=json.loads((root/'evidence/native-reference-20261106T204932Z/preparation.json').read_bytes())
uuids=[row[1] for row in prep['gpu_initial']['rows']]
source=root/'engine-src/vllm-132765e35606/vllm/third_party/pynvml.py'
script='''import sys,importlib.util,os,json
s=importlib.util.spec_from_file_location('nvml_probe','/control/pynvml.py');m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
m.nvmlInit()
try:values=[m.nvmlDeviceGetUUID(m.nvmlDeviceGetHandleByIndex(i)) for i in range(8)]
finally:m.nvmlShutdown()
print(json.dumps(dict(values=values,types=[type(v).__name__ for v in values],cuda_visible=os.getenv('CUDA_VISIBLE_DEVICES'),nvidia_visible=os.getenv('NVIDIA_VISIBLE_DEVICES'),torch_imported='torch' in sys.modules)),flush=True)
'''
(base/'probe.py').write_text(script)
cmd=['docker','create','--runtime','nvidia','--gpus','"device='+','.join(uuids)+'"',
 '--env','NVIDIA_VISIBLE_DEVICES='+','.join(uuids),'--env','CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7',
 '--env','PYTHONDONTWRITEBYTECODE=1','--env','LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64',
 '--network','none','--user','2147:2147','--read-only','--cpus','1','--memory','256m','--cap-drop','ALL',
 '--security-opt','no-new-privileges','--entrypoint','/opt/sllm-v4/bin/python',
 '--mount','type=bind,src='+str(base)+',dst=/control,readonly',
 '--mount','type=bind,src='+str(source)+',dst=/control/pynvml.py,readonly',
 'sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d','-u','/control/probe.py']
def call(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=15).strip()
cid=call(*cmd);before=json.loads(call('docker','inspect',cid))[0]
output=call('docker','start','-a',cid)
after=json.loads(call('docker','inspect',cid))[0];assert after['State']['ExitCode']==0
result=json.loads(output)
assert not result['torch_imported'] and set(result['values'])==set(uuids)
report=dict(expected_uuids=uuids,actual=result,before=before,after=after,
 scope='Read-only NVML identity query inside exact image/device binding; no Torch, CUDA context, LLM or model',native_model_retry=False)
(base/'result.json').write_text(json.dumps(report,indent=2)+'\n')
call('docker','rm',cid)
print(json.dumps(dict(expected=uuids,actual=result)))
