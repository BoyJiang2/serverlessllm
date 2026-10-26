"""Guarded preparation of only configured kernel caches; no model/environment copy."""
import hashlib,json,pathlib,shutil,sys,time
import os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
def require(value,message):
    if not value: raise RuntimeError(message)

from workspace import SOURCE,run_root,archive
root=run_root()
require(len(sys.argv)==3,'reviewed manifest SHA is required')
require(hashlib.sha256((root/'control-input-manifest.json').read_bytes()).hexdigest()==sys.argv[1],'review manifest changed')
for row in json.loads((root/'control-input-manifest.json').read_text())['files']:
    path=(SOURCE/row['path']).resolve()
    require(path.is_relative_to(SOURCE) and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'review input changed: '+row['path'])
from preflight_control13 import check
receipt=check(root,monitor_required=False)
deployment=json.loads((root/'deployment.json').read_text())
require(deployment['code_commit']==sys.argv[2] and deployment['input_manifest_sha256']==sys.argv[1],'deployment binding changed')
receipt.update(code_commit=sys.argv[2],manifest_sha256=sys.argv[1])
manifest=json.loads((root/'run-manifest.json').read_text())
source=archive()/'native-home'
groups=('inductor','vllm','triton')
files=[p for name in groups for p in (source/name).rglob('*') if p.is_file()]
require(all(not p.is_symlink() for name in groups for p in (source/name).rglob('*')),'cache source symlink needs explicit review')
require(sum(p.stat().st_size for p in files)<64*1024**2,'selected kernel cache copy exceeded64MiB budget')
receipt.update(begin_ns=time.monotonic_ns(),cache_source={p.relative_to(source).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files})
home=root/'native-home'
home.mkdir()
for name in groups:shutil.copytree(source/name,home/name)
for name,row in receipt['cache_source'].items():
    require(hashlib.sha256((root/'native-home'/name).read_bytes()).hexdigest()==row['sha256'],'cache copy differs: '+name)
receipt['cache_copy_verified']=True
receipt['cache_source_groups']=groups
receipt['cache_copy_reason']='reuse only the configured kernel caches with byte provenance; no whole archived environment or parent mount'
from preflight_control13 import call
receipt['gpu_baseline_mib']=[int(v) for v in call('nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits').splitlines()]
receipt['end_ns']=time.monotonic_ns()
with (root/'host-preparation.json').open('x') as out: json.dump(receipt,out,indent=2)
print('guarded metadata/cache preparation complete; model and Store not copied')
