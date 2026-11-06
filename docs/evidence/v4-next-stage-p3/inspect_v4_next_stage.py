from pathlib import Path
import json,hashlib,subprocess,datetime
from packaging.requirements import Requirement
from packaging.version import Version
root=Path('work/sllm-v4-p3');dest=Path('work/v4-next-stage');dest.mkdir(exist_ok=True)
paths=['requirements.txt','requirements-worker.txt','setup.py','sllm_store/requirements-build.txt','sllm_store/requirements.txt','sllm_store/pyproject.toml','sllm_store/setup.py','sllm_store/CMakeLists.txt','sllm_store/cmake/utils.cmake','sllm_store/Dockerfile.builder']
manifest=[]
for name in paths:
 data=subprocess.check_output(['git','-C',str(root),'show','HEAD:'+name]);p=dest/(name.replace('/','__')+'.txt');p.write_bytes(data);manifest.append(dict(path=name,artifact=p.name,sha256=hashlib.sha256(data).hexdigest()))
target=Path('outputs/version-p2-evidence/0201__requirements__common.txt').read_bytes();(dest/'vllm0201-common.txt').write_bytes(target)
cuda=Path('outputs/version-p2-evidence/0201__requirements__cuda.txt').read_bytes();(dest/'vllm0201-cuda.txt').write_bytes(cuda)
def exact(rows,name):return next(Requirement(x.split('#',1)[0].strip()) for x in rows.decode().splitlines() if x.strip() and not x.startswith('#') and x.split('=',1)[0].strip()==name)
server=(dest/'requirements.txt.txt').read_bytes();worker=(dest/'requirements-worker.txt.txt').read_bytes();build=(dest/'sllm_store__requirements-build.txt.txt').read_bytes()
conflicts=[]
for name,old,new in [('vllm',exact(worker,'vllm'),Requirement('vllm==0.20.1')),('torch',exact(build,'torch'),Requirement('torch==2.11.0')),('openai',exact(server,'openai'),Requirement('openai>=2.0.0')),('pydantic',exact(server,'pydantic'),Requirement('pydantic>=2.12.0'))]:
 pin=next(s.version for s in old.specifier if s.operator=='==');assert Version(pin) not in new.specifier
 conflicts.append(dict(package=name,legacy_requirement=str(old),v4_requirement=str(new),legacy_pin_allowed_by_target=False))
assert b'openai >= 2.0.0' in target and b'pydantic >= 2.12.0' in target and b'torch==2.11.0' in cuda
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),scope='Static requirement intersections and source inspection only; no resolver, install, build, import or GPU',conflicts=conflicts,sources=manifest,target_common_sha256=hashlib.sha256(target).hexdigest(),target_cuda_sha256=hashlib.sha256(cuda).hexdigest())
(dest/'packaging-conflicts.json').write_bytes((json.dumps(report,indent=2)+'\n').encode());print(json.dumps(conflicts))
