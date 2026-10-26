"""One source for canonical workspace, source and per-run output locations."""
import hashlib,json,os,pathlib,re,sys

if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')

HOST=pathlib.PurePosixPath('/data/brick1/boyjiang/glm5.1-fp8')
SOURCE=pathlib.Path(__file__).resolve().parent
RUN_ALIAS=pathlib.PurePosixPath('/r')

def workspace():
    value=os.environ.get('SLLM_CONTROL_WORKSPACE',str(HOST))
    if value not in (str(HOST),'/experiment'):
        raise RuntimeError('unexpected canonical workspace')
    return pathlib.Path(value)

def manifest():
    value=json.loads((SOURCE/'run-manifest.json').read_text())
    if not re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',value['run_id']):
        raise RuntimeError('invalid control run identity')
    return value

def run_root():
    run=manifest()['run_id'];root=workspace()
    return pathlib.Path(str(RUN_ALIAS)) if root==pathlib.Path('/experiment') else root/'validation/controls'/run

def archive():
    return workspace()/'archive/original-run'

def host_workspace():
    root=workspace()
    if root==pathlib.Path('/experiment'):raise RuntimeError('operation is host-only')
    return root

def historical_path(relative):
    name=pathlib.PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts:
        raise RuntimeError('invalid historical reference')
    root=host_workspace();data=(root/'INDEX.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=manifest()['canonical_index_sha256']:
        raise RuntimeError('canonical mapping changed')
    index=json.loads(data);old=index['old_path_compatibility_only'].rstrip('/')+'/'
    for row in sorted(index['entries'],key=lambda x:len(x['historical_path']),reverse=True):
        prefix=row['historical_path'].removeprefix(old)
        if relative==prefix or relative.startswith(prefix+'/'):
            target=root/pathlib.PurePosixPath(row['actual_path']).relative_to(HOST)
            if relative!=prefix:target=target/relative[len(prefix)+1:]
            break
    else:target=archive()/name
    if not target.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('historical reference escaped canonical workspace')
    return target

def container_mounts():
    """Readonly existing assets and one writable run; no broad archive/root mount."""
    root=HOST;old=root/'archive/original-run';run=manifest()['run_id']
    readonly=[(root/'checkpoint','checkpoint'),(root/'store/tp8','store/vllm/glm51-fp8'),
              (root/'code/experiment','code/experiment'),(root/'code/ServerlessLLM','code/ServerlessLLM'),
              (root/'code/Store-source-v7','code/Store-source-v7'),(root/'code/weight_contract.py','code/weight_contract.py')]
    readonly += [(root/'code/experiment/store_server13.py','code/Store-source-v7/sllm_store/server.py')]
    readonly += [(old/name,name) for name in ('.venv','serving-deps','runtime-metadata','buildtools')]
    readonly += [(root/'protocol/run-manifest.json','protocol/run-manifest.json')]
    readonly += [(root/'validation'/name,'validation/'+name) for name in ('native-gold.json','native-weight-contract.json','store-restore-validation.json')]
    # Frozen gold uses underscores, while canonical navigation links use hyphens.
    readonly += [(root/'validation'/('native-repeat'+n),'validation/native_repeat'+n) for n in ('7','8')]
    config=manifest()
    if config.get('execution_purpose')=='formal-continuation':
        readonly.append((root/'protocol/measurement-preregistration.json','protocol/measurement-preregistration.json'))
        baseline=json.loads((SOURCE/'initial-ledger.json').read_text())['attempts']
        readonly.extend((root/'results/formal-trials'/row['attempt_id']/'result.json','results/formal-trials/'+row['attempt_id']+'/result.json') for row in baseline if row['status']!='unstarted')
        from formal_gate import g1_reference_root
        reference=g1_reference_root(config,pathlib.Path(str(root)))
        names=['G1-acceptance.json']+[row['path'] for row in config['full_a4_proofs'].values()]
        for name in names:
            relative=pathlib.PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts:raise RuntimeError('G1 proof mount escaped')
            source=reference/relative
            readonly.append((source,source.relative_to(pathlib.Path(str(root))).as_posix()))
        for name in config['formal_prior_execution_roots']:
            relative=pathlib.PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts:raise RuntimeError('prior result mount escaped')
            readonly.append((root/relative,name))
    rows=[dict(source=source.as_posix(),destination='/experiment/'+dest,readonly=True) for source,dest in readonly]
    rows.append(dict(source=str(root/'validation/controls'/run),destination=str(RUN_ALIAS),readonly=False))
    return rows

def verify_input_layout():
    """Read metadata and stat payloads; never load weights or create directories."""
    from cache_contract13 import frozen_paths
    root=workspace();config=manifest()
    paths=frozen_paths(root/'checkpoint',SOURCE/'cache-inputs13.json')
    store=root/('store/vllm/glm51-fp8' if str(root)=='/experiment' else 'store/tp8')
    references={'native-gold.json':root/'validation/native-gold.json',
                'native-weight-contract.json':root/'validation/native-weight-contract.json',
                'store-restore-validation.json':root/'validation/store-restore-validation.json',
                'weight_contract.py':root/'code/weight_contract.py',
                'raw-disk-verification.json':store/'raw-disk-verification.json'}
    for name,path in references.items():
        if not path.resolve().is_relative_to(root.resolve()):raise RuntimeError('input escaped workspace: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=config['reference_sha256'][name]:
            raise RuntimeError('reference hash differs: '+name)
    # The frozen receipt also records inactive MTP, which is outside the TP8 service.
    rows=[row for row in json.loads(references['raw-disk-verification.json'].read_text())['ranks'] if row['rank']!='inactive_mtp']
    if len(rows)!=8 or sorted(row['rank'] for row in rows)!=list(range(8)):
        raise RuntimeError('Store rank receipt differs')
    if {p.name for p in store.glob('rank_*')}!={f'rank_{i}' for i in range(8)}:
        raise RuntimeError('Store rank layout differs')
    for row in rows:
        rank=store/f"rank_{row['rank']}";sizes=row['partition_bytes']
        if rank.is_symlink() or not rank.is_dir() or not rank.resolve().is_relative_to(store.resolve()):
            raise RuntimeError('Store rank type/path differs: '+rank.name)
        if len(sizes)!=10 or {p.name for p in rank.glob('tensor.data_*')}!={f'tensor.data_{i}' for i in range(10)}:
            raise RuntimeError('Store partition layout differs: '+rank.name)
        for number,size in enumerate(sizes):
            path=rank/f'tensor.data_{number}'
            if path.is_symlink() or not path.is_file() or size<=0 or path.stat().st_size!=size:
                raise RuntimeError('Store partition size/type differs: '+str(path))
    return dict(checkpoint_inputs=len(paths),store_ranks=8,store_parts=80)

if __name__=='__main__':
    if sys.argv[1:]!=['--check-inputs']:raise SystemExit('usage: workspace.py --check-inputs')
    print(json.dumps(verify_input_layout(),indent=2))
