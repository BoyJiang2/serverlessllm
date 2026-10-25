"""Guarded host preparation: metadata and bounded19MB cache copy, never model copies."""
import hashlib,json,pathlib,shutil,sys,time
import os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
def require(value,message):
    if not value: raise RuntimeError(message)

root=pathlib.Path(__file__).resolve().parent
require(len(sys.argv)==2,'reviewed manifest SHA is required')
require(hashlib.sha256((root/'control-input-manifest.json').read_bytes()).hexdigest()==sys.argv[1],'review manifest changed')
for row in json.loads((root/'control-input-manifest.json').read_text())['files']:
    path=(root/row['path']).resolve()
    require(path.is_relative_to(root) and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'review input changed: '+row['path'])
from preflight_control13 import check
receipt=check(root,monitor_required=False)
manifest=json.loads((root/'run-manifest.json').read_text())
asset=pathlib.Path(manifest['parent_asset_root'])
require(root==pathlib.Path('/data/brick1/boyjiang')/manifest['run_id'],'unexpected control root')
source=asset/'native-home'
files=[p for p in source.rglob('*') if p.is_file()]
require(all(not p.is_symlink() for p in source.rglob('*')),'cache source symlink needs explicit review')
require(sum(p.stat().st_size for p in files)<8*1024**3,'cache copy exceeded8GiB budget')
receipt.update(begin_ns=time.monotonic_ns(),cache_source={p.relative_to(source).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files})
shutil.copytree(source,root/'native-home')
(asset/'controls'/manifest['run_id']).mkdir(parents=True,exist_ok=True)
from preflight_control13 import call
receipt['gpu_baseline_mib']=[int(v) for v in call('nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits').splitlines()]
receipt['end_ns']=time.monotonic_ns()
with (root/'host-preparation.json').open('x') as out: json.dump(receipt,out,indent=2)
print('guarded metadata/cache preparation complete; model and Store not copied')
