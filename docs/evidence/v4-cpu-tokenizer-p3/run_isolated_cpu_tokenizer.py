from pathlib import Path
import hashlib,json,subprocess
base=Path('/home/boyjiang/sllm-v4-pro/evidence/cpu-tokenizer-20261106')
image='sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27'
def docker(*args,timeout=30):return subprocess.run(['docker',*args],text=True,capture_output=True,timeout=timeout)
args=['create','--pull=never','--runtime=runc','--network=none','--read-only','--cap-drop=ALL',
 '--security-opt=no-new-privileges','--memory=2g','--memory-swap=2g','--cpus=1','--pids-limit=64',
 '--tmpfs','/tmp:rw,nosuid,nodev,size=128m']
for value in ('NVIDIA_VISIBLE_DEVICES=void','CUDA_VISIBLE_DEVICES=','USE_TORCH=0','USE_TF=0','USE_FLAX=0',
 'HF_HUB_OFFLINE=1','TRANSFORMERS_OFFLINE=1','HF_HOME=/tmp/hf','PYTHONDONTWRITEBYTECODE=1',
 'TOKENIZERS_PARALLELISM=false','RAYON_NUM_THREADS=1','OMP_NUM_THREADS=1'):
 args+=['-e',value]
mounts=[(str(base/'inputs'),'/inputs'),(str(base/'tokenize_cpu.py'),'/job.py'),
 ('/mnt/raid/models/DeepSeek-V4-Pro/tokenizer.json','/assets/tokenizer.json'),
 ('/mnt/raid/models/DeepSeek-V4-Pro/tokenizer_config.json','/assets/tokenizer_config.json')]
for src,dst in mounts:args+=['--mount',f'type=bind,src={src},dst={dst},readonly']
args+=['--entrypoint','python3',image,'/job.py']
r=docker(*args);assert r.returncode==0,r.stderr
cid=r.stdout.strip();assert len(cid)==64 and all(c in '0123456789abcdef' for c in cid)
(base/'container-id.txt').write_text(cid+'\n')
try:
 r=docker('inspect','--format','{{json .HostConfig}}',cid)
 assert r.returncode==0,r.stderr
 host=json.loads(r.stdout)
 assert host['Runtime']=='runc' and host['NetworkMode']=='none' and host['ReadonlyRootfs']
 assert not host['DeviceRequests'] and not host['Devices']
 assert host['Memory']==2*1024**3 and host['NanoCpus']==10**9 and host['PidsLimit']==64
 observed=docker('inspect','--format','{{.Image}}',cid).stdout.strip();assert observed==image
 (base/'container-receipt.json').write_text(json.dumps(dict(image_id=image,container_id=cid,
  network=host['NetworkMode'],runtime=host['Runtime'],readonly_root=host['ReadonlyRootfs'],
  gpu_device_requests=host['DeviceRequests'],gpu_devices=host['Devices'],memory_bytes=host['Memory'],
  memory_swap_bytes=host['MemorySwap'],nano_cpus=host['NanoCpus'],pids_limit=host['PidsLimit'],
  mounts=host['Mounts'],cap_drop=host['CapDrop'],security_opt=host['SecurityOpt'],
  source_script_sha256=hashlib.sha256((base/'tokenize_cpu.py').read_bytes()).hexdigest()),indent=2)+'\n')
 r=docker('start','--attach',cid,timeout=120)
 (base/'stdout.json').write_text(r.stdout)
 (base/'stderr.log').write_text(r.stderr)
 state=json.loads(docker('inspect','--format','{{json .State}}',cid).stdout)
 (base/'terminal-state.json').write_text(json.dumps(state,indent=2)+'\n')
 assert not state['Running'] and not state['OOMKilled'] and state['ExitCode']==0,r.stderr[-2000:]
 report=json.loads(r.stdout)
 assert report['model_libraries_imported'] is False and not report['gpu_execution']
 print(json.dumps(dict(results=[dict(file=x['file'],tokens=x['token_count'],bos=x['bos_count']) for x in report['results']],
                      packages=report['packages'],gpu_execution=False)))
finally:
 state=json.loads(docker('inspect','--format','{{json .State}}',cid).stdout)
 if not state['Running']:
  removed=docker('rm',cid);assert removed.returncode==0,removed.stderr
  (base/'container-removed.txt').write_text(removed.stdout)
