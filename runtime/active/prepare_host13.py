"""Guarded empty cache setup; compile only in the untimed G1 runtime."""
import hashlib,json,sys,time
import os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
def require(value,message):
    if not value: raise RuntimeError(message)

from workspace import SOURCE,run_root
from compilation_guard import CACHE_DIRECTORIES

def prepare_home(root):
    home=root/'native-home'
    home.mkdir()
    for name in CACHE_DIRECTORIES:
        (home/name).mkdir()
    return dict(cache_preparation_mode='fresh empty caches; no relocated compiled artifacts',
                cache_source_groups=list(CACHE_DIRECTORIES),cache_reused_bytes=0,
                compilation_cost_scope='all compilation/autotuning charged to untimed initialization; formal timing requires separately frozen warm caches')

def main():
    root=run_root()
    require(len(sys.argv)==3,'reviewed manifest SHA is required')
    require(hashlib.sha256((root/'control-input-manifest.json').read_bytes()).hexdigest()==sys.argv[1],'review manifest changed')
    for row in json.loads((root/'control-input-manifest.json').read_text())['files']:
        path=(SOURCE/row['path']).resolve()
        require(path.is_relative_to(SOURCE) and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'review input changed: '+row['path'])
    from preflight_control13 import check,call
    receipt=check(root,monitor_required=False)
    deployment=json.loads((root/'deployment.json').read_text())
    require(deployment['code_commit']==sys.argv[2] and deployment['input_manifest_sha256']==sys.argv[1],'deployment binding changed')
    receipt.update(code_commit=sys.argv[2],manifest_sha256=sys.argv[1])
    receipt['begin_ns']=time.monotonic_ns()
    receipt.update(prepare_home(root))
    receipt['gpu_baseline_mib']=[int(v) for v in call('nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits').splitlines()]
    receipt['end_ns']=time.monotonic_ns()
    with (root/'host-preparation.json').open('x') as out: json.dump(receipt,out,indent=2)
    print('guarded empty cache setup complete; model, Store and compiled artifacts not copied')

if __name__=='__main__':main()
