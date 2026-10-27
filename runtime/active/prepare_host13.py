"""Guarded private cache setup; validate any disk seed before current normal."""
import hashlib,json,sys,time,pathlib,re
import os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
def require(value,message):
    if not value: raise RuntimeError(message)

from workspace import SOURCE,run_root
from compilation_guard import CACHE_DIRECTORIES

def prepare_home(root,config=None):
    home=root/'native-home'
    home.mkdir()
    for name in CACHE_DIRECTORIES:
        (home/name).mkdir()
    if config and config.get('jit_seed_contract_sha256'):
        from attempt_state import checked_json
        from compilation_guard_v6 import artifact_snapshot,MUTABLE_LOG
        seed=checked_json(SOURCE/'jit-seed-contract.json',config['jit_seed_contract_sha256'])
        run=seed['source_run_id']
        require(re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',run) and run!=config['run_id'],'invalid JIT seed identity')
        source=root.parent/run;old_home=source/'native-home'
        require(not source.is_symlink() and source.resolve().parent==root.parent.resolve() and not old_home.is_symlink(),'JIT seed root escaped')
        original=checked_json(source/'run-manifest.json',seed['source_run_manifest_sha256'])
        qualification=checked_json(source/'normal-jit-qualification.json',seed['qualification_sha256'])
        require(qualification['status']=='normal native lifecycle and warm artifact qualification' and qualification['run_id']==original['run_id']==run and qualification['code_commit']==seed['source_code_commit'] and qualification['manifest_sha256']==seed['source_manifest_sha256'],'JIT qualification identity differs')
        for key in ('image_id','model','revision','tp','max_model_len','max_num_seqs','speculative_config','selected_vllm_commit','selected_sllm_commit','store_import_sha256','reference_sha256','formal_required_env','runtime_identity','event_trace_sha256'):
            require(config[key]==original[key],'JIT seed engine/dependency compatibility differs: '+key)
        cpu=checked_json(source/'service13a-cpu-import-preflight.json',seed['source_cpu_import_sha256'])
        require(config['jit_seed_runtime_versions']==seed['runtime_versions']=={k:cpu[k] for k in ('torch','vllm','cuda')},'JIT runtime versions differ')
        artifacts=seed['artifacts'];require(len(artifacts)==583 and qualification['artifacts']==artifacts,'JIT seed whitelist differs')
        for directory in CACHE_DIRECTORIES:
            require(not (old_home/directory).is_symlink() and all(not p.is_symlink() for p in (old_home/directory).rglob('*')),'JIT seed symlink')
        require(artifact_snapshot(old_home)==artifacts,'JIT seed assets changed or incomplete')
        begin=time.monotonic_ns();copied=0
        for name,row in artifacts.items():
            relative=pathlib.PurePosixPath(name)
            require(not relative.is_absolute() and '..' not in relative.parts and relative.as_posix()==name and relative.parts[0] in CACHE_DIRECTORIES,'JIT seed path escaped')
            source_path=old_home/relative;target=home/relative
            require(source_path.resolve().is_relative_to(old_home.resolve()),'JIT source escaped')
            data=source_path.read_bytes();require(len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],'JIT source changed during copy')
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(data)
            require(hashlib.sha256(target.read_bytes()).hexdigest()==row['sha256'],'JIT copied bytes differ');copied+=len(data)
        log=home/MUTABLE_LOG;log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('xb'):pass
        require(artifact_snapshot(old_home)==artifacts and artifact_snapshot(home)==artifacts,'JIT seed changed across copy')
        return dict(cache_preparation_mode='hash-pinned immutable disk JIT seed; current normal qualification remains mandatory',cache_source_groups=list(CACHE_DIRECTORIES),cache_reused_bytes=copied,seed_run_id=run,seed_qualification_sha256=seed['qualification_sha256'],seed_contract_sha256=config['jit_seed_contract_sha256'],seed_files=len(artifacts),copy_begin_ns=begin,copy_end_ns=time.monotonic_ns(),mutable_log='new empty current-run log; old mutable log not copied',compilation_cost_scope='source C21 compilation retained in its ledger; this copy and current normal initialization charged separately; no old readiness or Store pool proof reused')
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
    config=json.loads((root/'run-manifest.json').read_text())
    if config.get('jit_seed_contract_sha256'):
        seed=json.loads((SOURCE/'jit-seed-contract.json').read_text())
        require(call('nvidia-smi','--query-gpu=driver_version','--format=csv,noheader').splitlines()==[seed['driver_version']]*8,'JIT driver compatibility differs')
    receipt.update(prepare_home(root,config))
    receipt['gpu_baseline_mib']=[int(v) for v in call('nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits').splitlines()]
    receipt['end_ns']=time.monotonic_ns()
    with (root/'host-preparation.json').open('x') as out: json.dump(receipt,out,indent=2)
    print('guarded cache setup complete; model and Store payloads not copied; current normal qualification required')

if __name__=='__main__':main()
