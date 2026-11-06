from pathlib import Path
import subprocess,json,hashlib,os
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');out=Path('/data/brick1/boyjiang/sllm-v4-pro-build/pure-wheels')
image='sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27'
def docker(*args,**kwargs):return subprocess.run(['docker',*args],capture_output=True,text=True,check=True,**kwargs)
args=['create','--pull=never','--runtime=runc','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=1','--memory=2g','--memory-swap=2g','--pids-limit=128','--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--user',f'{os.getuid()}:{os.getgid()}']
for value in ('NVIDIA_VISIBLE_DEVICES=void','CUDA_VISIBLE_DEVICES=','PIP_NO_CACHE_DIR=1','PYTHONDONTWRITEBYTECODE=1'):args+=['-e',value]
args+=['--mount',f'type=bind,src={base}/GPUtil-1.4.0.tar.gz,dst=/input/GPUtil-1.4.0.tar.gz,readonly','--mount',f'type=bind,src={out},dst=/out','--entrypoint','/usr/bin/python3',image,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation','--wheel-dir','/out','/input/GPUtil-1.4.0.tar.gz']
cid=docker(*args).stdout.strip();assert len(cid)==64
host=json.loads(docker('inspect','--format','{{json .HostConfig}}',cid).stdout)
assert host['Runtime']=='runc' and host['NetworkMode']=='none' and not host['Devices'] and not host['DeviceRequests'] and host['ReadonlyRootfs']
with (base/'gputil-wheel-hostuid.log').open('w') as log:r=subprocess.run(['docker','start','-a',cid],stdout=log,stderr=subprocess.STDOUT)
state=json.loads(docker('inspect','--format','{{json .State}}',cid).stdout)
receipt=dict(image_id=image,container_id=cid,user=f'{os.getuid()}:{os.getgid()}',runtime=host['Runtime'],network=host['NetworkMode'],readonly_root=host['ReadonlyRootfs'],device_requests=host['DeviceRequests'],devices=host['Devices'],exit_code=state['ExitCode'],oom_killed=state['OOMKilled'],wheel_sha256=hashlib.sha256((out/'gputil-1.4.0-py3-none-any.whl').read_bytes()).hexdigest() if (out/'gputil-1.4.0-py3-none-any.whl').exists() else None,scope='Pure Python GPUtil wheel build only. No Torch/vLLM/native qualification or model mounts.')
(base/'gputil-build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
assert r.returncode==0 and state['ExitCode']==0 and not state['OOMKilled'],receipt
docker('rm',cid);print(json.dumps(receipt))
