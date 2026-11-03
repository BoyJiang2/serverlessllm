import subprocess,pathlib,json,hashlib,zipfile
code = r'''
import pathlib,json,hashlib,zipfile
root=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM')
snapshot=pathlib.Path('/data/brick1/boyjiang/kimi-new/checkpoint/7eb5002f6aadc958aed6a9177b7ed26bb94011bb')
def entry(b):return {'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'text':b.decode()}
files={str(p):entry(p.read_bytes()) for p in [root/'sllm_store/requirements.txt',root/'sllm_store/setup.py',root/'requirements-worker.txt',snapshot/'README.md',snapshot/'docs/deploy_guidance.md',snapshot/'config.json']}
wheel=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/downloads/vllm-0.19.1-cp38-abi3-manylinux_2_31_x86_64.whl')
with zipfile.ZipFile(wheel) as z:
 names=['vllm/model_executor/models/registry.py','vllm/model_executor/models/kimi_k25.py','vllm/transformers_utils/configs/kimi_k25.py','vllm/transformers_utils/config.py','vllm/_version.py']
 names.extend(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))
 for n in names:
  if n in z.namelist():files[str(wheel)+'!'+n]=entry(z.read(n))
print(json.dumps({'files':files,'scope':'host read-only metadata/source; no container or model execution'},ensure_ascii=False))
'''
r=subprocess.run(['C:/Windows/System32/OpenSSH/ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','qjhs002','python3 -'],input=code.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
assert r.returncode==0,r.stderr.decode()
pathlib.Path('work/runtime-dependency-inputs.json').write_bytes(r.stdout)
print('captured',len(r.stdout),'bytes')
