"""Full post-control acceptance; no model execution or formal samples."""
import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import gzip,hashlib,json,pathlib,stat

def require(value,message):
    if not value: raise RuntimeError(message)

def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): result.update(block)
    return result.hexdigest()

def monitor_summary(path):
    count=0; first=None; last=None; max_gap=0; partial=None
    with path.open('rb') as stream:
        while True:
            raw=stream.readline(4*1024**2+1)
            if not raw: break
            require(len(raw)<=4*1024**2,'monitor row exceeds4MiB')
            try: row=json.loads(raw)
            except (ValueError,UnicodeDecodeError):
                require(not raw.endswith(b'\n') and not stream.read(1),'non-final monitor corruption')
                partial=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),reason='possible final interrupted write during controlled shutdown; raw retained')
                break
            require(len(row['observers'])==2 and all(v['ok'] for v in row['observers']),'observer liveness gap')
            now=row['monotonic_ns']
            if first is None: first=now
            if last is not None:
                require(now>last,'nonmonotonic monitor')
                max_gap=max(max_gap,(now-last)/1e9)
            last=now; count+=1
    require(count>2 and max_gap<=5,'monitor coverage/cadence failed')
    return dict(samples=count,first_ns=first,last_ns=last,max_gap_seconds=max_gap,partial_final_row=partial)

def validate_release(rows,stage,run,baseline):
    require(len(rows)==61 and [v['second'] for v in rows]==list(range(61)),'release interval incomplete')
    require(rows[-1]['monotonic_ns']-rows[0]['monotonic_ns']>=60_000_000_000,'release shorter than60s')
    for row in rows:
        require(not row['gpu_processes'].strip(),'GPU process retained')
        running=set(row['own_running'].splitlines())
        if stage=='allstopped':
            require(not running and int(row['cgroup']['pids.current'])==0,'owned infrastructure retained')
            used=[int(line.split(',')[-1].strip().split()[0]) for line in row['gpu'].splitlines()]
            require(len(used)==len(baseline)==8 and all(a<=b+16 for a,b in zip(used,baseline)),'GPU memory retained')
        else: require(running<={run+'-monitor13'},'owned workload retained')

def validate_cache(root):
    contract=json.loads((root/'cache-inputs13.json').read_text())
    dest=root/'cache_g1hot13'; summary=json.loads((dest/'summary.json').read_text())
    require(summary['input_manifest_sha256']==digest(root/'cache-inputs13.json'),'cache input manifest differs')
    require(summary['mode']=='hot' and summary['kind']=='safetensors_inputs' and summary['cache_condition_passed'] is True,'hot cache control failed')
    require(summary['hot_workers']==8 and summary['repair_passes']<=2 and (summary['total_cache_admission_end_ns']-summary['total_cache_admission_begin_ns'])<=600_000_000_000,'cache budget differs')
    expected={r['path']:r for r in contract['files']}
    require(len(expected)==len(summary['files'])==148,'cache input closure differs')
    require({r['file'] for r in summary['files']}==set(expected),'cache file names differ')
    def flags(row):
        path=(dest/row['vector']).resolve()
        require(path.is_relative_to(dest.resolve()),'mincore vector escaped')
        value=gzip.decompress(path.read_bytes())
        require(len(value)==row['pages'] and hashlib.sha256(value).hexdigest()==row['vector_sha256'],'mincore vector identity differs')
        return sum(v&1 for v in value)
    for row in summary['files']:
        require(row['source_path']=='/experiment/checkpoint/'+row['file'] and row['bytes']==expected[row['file']]['bytes'],'cache source differs')
        require(row['page_size']>0 and row['pages']==(row['bytes']+row['page_size']-1)//row['page_size'],'cache page coverage differs')
        require(flags(row)==row['resident_pages']==row['pages'],'hot residency not full')
    eviction=[json.loads(line) for line in (dest/'eviction-mincore.jsonl').read_text().splitlines()]
    require(len(eviction)==148 and {r['source_path'] for r in eviction}=={'/experiment/checkpoint/'+name for name in expected},'eviction closure differs')
    final={r['source_path']:r for r in summary['files']}
    for row in eviction:
        require(row['pages']==final[row['source_path']]['pages'] and flags(row)==row['resident_pages']==0,'initial eviction not fully cold')
    return dict(summary_sha256=digest(dest/'summary.json'),inputs=148,threshold='all pages resident; initial eviction zero',pgsteal='diagnostic only')

def expand_cpus(value):
    result=set()
    for part in value.split(','):
        bounds=part.split('-'); result.update(range(int(bounds[0]),int(bounds[-1])+1))
    return result

def accept(root,sha,code_commit):
    load=lambda name:json.loads((root/name).read_text())
    require(digest(root/'control-input-manifest.json')==sha,'input manifest changed')
    for row in load('control-input-manifest.json')['files']:
        path=(root/row['path']).resolve()
        require(path.is_relative_to(root) and digest(path)==row['sha256'],'input changed: '+row['path'])
    manifest=load('run-manifest.json'); deployment=load('deployment.json'); run=manifest['run_id']; boot=manifest['runtime_identity']['boot_id']
    require(deployment['code_commit']==code_commit and deployment['run_id']==run and deployment['input_manifest_sha256']==sha,'deployment binding changed')
    require(pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot,'boot changed')
    ends=[]
    for label in ('ray13a','ray13b','service13a'):
        result=load(label+'-supervisor-result.json')
        require(result['status']=='passed including cleanup','supervisor failure')
        ends.append(result['end_ns'])
        for name in (label,'head'+label):
            identity=load(name+'-identity.json')
            require(identity['run_id']==run and identity['code_commit']==code_commit and identity['preflight']['boot_id']==boot,'dispatch binding changed')
    for label in ('ray13a','ray13b'): require(load('ray-only-'+label+'/result.json')['status']=='passed','Ray control failed')
    contract={r['rank']:r for r in load('evidence/native-weight-contract.json')['ranks']}
    for fmt in ('serverless_llm','safetensors'):
        proof=load('service-bootstrap-service13a/'+fmt+'/model-result.json')
        require(proof['status']=='A4 native HTTP lifecycle checks passed' and proof['cycles']==2 and proof['weight_format']==fmt,'native lifecycle failed')
        coverage=proof['rank_coverage']
        require(len(coverage)==16 and {(r['cycle'],r['rank']) for r in coverage}=={(cycle,rank) for cycle in (1,2) for rank in range(8)},'rank coverage incomplete')
        for row in coverage:
            expected=contract[row['rank']]
            require(row['weight_scale_derived_references']==expected['verified_weight_references'] and row['dsa_workspace_references_excluded']==expected['runtime_index_references_excluded'] and row['kv_references_excluded']==expected['kv_runtime_references_excluded'],'rank contract differs')
        for cycle in (1,2):
            pss=load(f'service-bootstrap-service13a/{fmt}/cycle{cycle}-pss.json')
            require(pss['status']=='completed' and pss['end_ns']>=pss['begin_ns'] and any(v['pid']==pss['store_pid'] and v['smaps_rollup'] is not None for v in pss['processes']),'PSS sample incomplete')
    cache_proof=validate_cache(root)
    pool=load('service-bootstrap-service13a/pool-ready.json'); restored=load('evidence/store-restore-validation.json')
    require(pool['pool_bytes']==restored['pool_bytes'] and pool['chunk_bytes']==restored['chunk_bytes'],'pool capacity differs')
    require(pool['rpc_config']==dict(mem_pool_size=pool['pool_bytes'],chunk_size=pool['chunk_bytes']) and pool['rss_bytes']>=pool['pool_bytes'],'real resident pool unproven')
    require(pool['listener']['pid']==pool['store_pid'] and pool['listener']['port']==8073 and pool['listener']['listening_sockets'],'pool listener differs')
    affinity=load('service-bootstrap-service13a/store-affinity-ready.json'); launch=load('service-bootstrap-service13a/store-numa-launch.json')
    target=set(launch['target_cpu_affinity'])
    require(affinity['pid']==pool['store_pid']==launch['pid'] and target and target==expand_cpus(launch['sysfs_cpu_list'].strip())&set(launch['prior_cpu_affinity']),'Store identity/target differs')
    require(affinity['tasks'] and all(expand_cpus(row['cpus_allowed_list']) and expand_cpus(row['cpus_allowed_list'])<=target for row in affinity['tasks']),'Store thread affinity differs')
    baseline=load('host-preparation.json')['gpu_baseline_mib']
    for stage in ('ray13a','ray13b','allstopped'): validate_release(load('release60-'+stage+'.json'),stage,run,baseline)
    summary=monitor_summary(root/'resource-monitor-v12.jsonl')
    starts=[load('head'+label+'-dispatch.json')['dispatch_monotonic_ns'] for label in ('ray13a','ray13b','service13a')]
    require(summary['first_ns']<=min(starts) and summary['last_ns']>=max(ends),'monitor missing startup/cleanup coverage')
    index_path=root/'root-evidence-index13.json'
    require(index_path.stat().st_uid==0 and stat.S_IMODE(index_path.stat().st_mode)==0o444,'untrusted root evidence index')
    index=load('root-evidence-index13.json')
    require(index['run_id']==run and index['boot_id']==boot and index['code_commit']==code_commit and index['manifest_sha256']==sha and index['cleanup_passed'] and index['guard_probe_passed'],'root evidence binding failed')
    require(index['guard_sha256']==digest(root/'cleanup_guard13.sh'),'root guard differs')
    required={'guard-verification.json','cleanup-v13-main.log','cleanup-v13-probe.log','kernel-private.jsonl'}
    require(required<={pathlib.PurePosixPath(r['path']).name for r in index['files']},'persistent root evidence missing')
    asset=pathlib.Path(manifest['parent_asset_root'])
    for row in load('evidence/handoff-current-audit-v2.json')['attempts']:
        directory=asset/'formal-trials'/row['attempt_id']
        if row['status']=='unstarted': require(not directory.exists(),'formal ID consumed')
        else: require(digest(directory/'result.json')==row['result_sha256'],'terminal result changed')
    receipt=dict(status='G1 acceptance passed; no formal samples',run_id=run,boot_id=boot,code_commit=code_commit,manifest_sha256=sha,monitor=summary,cache_control=cache_proof,root_evidence_index_sha256=digest(index_path),pool_initializations=1,ray_only_controls=2,native_lifecycles_per_format=2,scope='bounded current stability only; historical exit137 cause unknown')
    with (root/'G1-acceptance.json').open('x') as stream: json.dump(receipt,stream,indent=2)
    print(json.dumps(receipt,indent=2))

if __name__=='__main__': accept(pathlib.Path(__file__).resolve().parent,*sys.argv[1:])
