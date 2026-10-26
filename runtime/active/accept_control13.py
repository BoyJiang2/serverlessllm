"""Full post-control acceptance; no model execution or formal samples."""
import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import gzip,hashlib,json,pathlib,stat
from workspace import SOURCE,run_root,workspace
from gpu_identity13 import require_helper_binding,require_helper_only,store_identity

def require(value,message):
    if not value: raise RuntimeError(message)

def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): result.update(block)
    return result.hexdigest()

def monitor_summary(path,gpu_context=None,expected_gpu=None):
    count=0; first=None; last=None; last_begin=None; max_gap=0; max_duration=0; partial=None; found_gpu=set()
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
            if expected_gpu is not None and row['monotonic_ns'] in expected_gpu:
                expected=expected_gpu[row['monotonic_ns']]
                launch,service,boot=gpu_context
                require(row['registered_store_helper']==store_identity(launch,service,row['processes'],boot),'host monitor helper derivation differs')
                for key in ('registered_store_helper','gpu_pids','foreign_gpu_pids'):
                    require(row[key]==expected[key],'GPU snapshot differs from raw host monitor: '+key)
                require(row['gpu_pids']==sorted({int(line.split(',',1)[0]) for line in row['gpu_processes'].splitlines()}),'host NVML PID list differs')
                found_gpu.add(row['monotonic_ns'])
            now=row['monotonic_ns']
            begin=row['sample_begin_ns']; end=row['sample_end_ns']
            require(begin<=now<=end and end-begin<=5_000_000_000,'slow or inconsistent monitor sample')
            max_duration=max(max_duration,(end-begin)/1e9)
            if last_begin is not None: require(0<begin-last_begin<=5_000_000_000,'monitor begin-to-begin gap')
            last_begin=begin
            if first is None: first=now
            if last is not None:
                require(now>last,'nonmonotonic monitor')
                max_gap=max(max_gap,(now-last)/1e9)
            last=now; count+=1
    require(count>2 and max_gap<=5,'monitor coverage/cadence failed')
    if expected_gpu is not None: require(found_gpu==set(expected_gpu),'GPU snapshot missing from raw host monitor')
    return dict(samples=count,first_ns=first,last_ns=last,max_gap_seconds=max_gap,max_sample_duration_seconds=max_duration,partial_final_row=partial)

def validate_release(rows,stage,run,baseline):
    require(len(rows)==61 and [v['second'] for v in rows]==list(range(61)),'release interval incomplete')
    require(rows[-1]['monotonic_ns']-rows[0]['monotonic_ns']>=60_000_000_000,'release shorter than60s')
    for row in rows:
        require(isinstance(row['processes'],list),'missing release process accounting')
        memory={line.split()[0]:int(line.split()[1]) for line in row['cgroup']['memory.stat'].splitlines()}
        require(all(key in memory and memory[key]>=0 for key in ('anon','file','shmem','unevictable')),'missing charged/shared/locked accounting')
        if stage=='allstopped': require(not row['processes'],'process retained after full cleanup')
        for process in row['processes']:
            require(all(field in process['status'] for field in ('VmRSS:','VmLck:','Threads:')) and ('smaps_rollup' in process or 'PSS_unavailable' in process),'incomplete release RSS/PSS/locked accounting')
        require(not row['gpu_processes'].strip(),'GPU process retained')
        running=set(row['own_running'].splitlines())
        if stage=='allstopped':
            require(not running and int(row['cgroup']['pids.current'])==0,'owned infrastructure retained')
            used=[int(line.split(',')[-1].strip().split()[0]) for line in row['gpu'].splitlines()]
            require(len(used)==len(baseline)==8 and all(a<=b+16 for a,b in zip(used,baseline)),'GPU memory retained')
        else: require(running<={run+'-monitor13'},'owned workload retained')

def validate_cache(root):
    contract=json.loads((root/'cache-inputs13.json').read_text())
    dest=root/'cache_g1hot13'
    load=lambda name:json.loads((dest/name).read_text())
    summary=load('summary.json')
    require(summary['input_manifest_sha256']==digest(root/'cache-inputs13.json'),'cache input manifest differs')
    require(summary['mode']=='hot' and summary['kind']=='safetensors_inputs' and summary['cache_condition_passed'] is True,'hot cache control failed')
    history=summary['repair_history']
    require(summary['hot_workers']==8 and 0<=summary['repair_passes']==len(history)<=2 and 0<=summary['total_cache_admission_end_ns']-summary['total_cache_admission_begin_ns']<=600_000_000_000,'cache budget differs')
    expected={r['path']:r for r in contract['files']}
    require(len(expected)==148,'cache input closure differs')
    sources={'/experiment/checkpoint/'+name:row for name,row in expected.items()}
    placement=summary['hot_placement']; store=json.loads((root/'service-bootstrap-service13a/store-numa-launch.json').read_text())
    cpus=set(placement['reader_cpu_affinity'])
    require(placement==load('hot-placement-final.json') and placement['node']=='node1' and placement['excluded_store_cpu_node']==store['node']=='node0' and cpus and not cpus&set(store['target_cpu_affinity']),'cache CPU placement differs')
    require(placement['required_bytes']==sum(r['bytes'] for r in expected.values()) and placement['margin_bytes']==64*1024**3 and placement['capacity_proxy_bytes']>=placement['required_bytes']+placement['margin_bytes'],'cache capacity proxy below frozen margin')
    def flags(row):
        path=(dest/row['vector']).resolve()
        require(path.is_relative_to(dest.resolve()),'mincore vector escaped')
        value=gzip.decompress(path.read_bytes())
        require(len(value)==row['pages'] and hashlib.sha256(value).hexdigest()==row['vector_sha256'],'mincore vector identity differs')
        return sum(v&1 for v in value)
    def vectors(rows,full=False):
        require(len(rows)==148 and {r['file'] for r in rows}==set(expected),'cache names/coverage differ')
        for row in rows:
            require(row['source_path']=='/experiment/checkpoint/'+row['file'] and row['bytes']==expected[row['file']]['bytes'],'cache source differs')
            require(row['page_size']>0 and row['pages']==(row['bytes']+row['page_size']-1)//row['page_size'],'cache page coverage differs')
            resident=flags(row)
            require(resident==row['resident_pages'] and 0<=resident<=row['pages'] and (not full or resident==row['pages']),'cache residency differs')
    def reads(rows,selected):
        require(len(rows)==len(selected) and {r['file'] for r in rows}==set(selected),'reader input coverage differs')
        require(all(r['bytes_read']==sources[r['file']]['bytes'] and r['cpu_begin'] in cpus and r['cpu_end'] in cpus for r in rows),'reader byte/CPU proof differs')
    def idle(rows):
        require(len(rows)>=2 and all(r['read_inflight']>=0 and r['write_inflight']>=0 for r in rows),'I/O idle proof missing')
        require(all(a['monotonic_ns']<b['monotonic_ns'] for a,b in zip(rows,rows[1:])) and all(r['read_inflight']==0 for r in rows[-2:]),'I/O read idle failed')
    initial=load('initial-scan.json'); vectors(initial['files'])
    require(initial['verification_begin_ns']<=initial['verification_complete_ns'] and initial['read_threads_joined_ns']<=initial['verification_begin_ns'],'initial cache event ordering differs')
    initial_reads=[json.loads(line) for line in (dest/'preparation.jsonl').read_text().splitlines()]
    require(initial_reads==load('initial-reads.json'),'initial reader receipts differ')
    reads(initial_reads,sources)
    idle(initial['read_idle_receipt'])
    require(initial['read_idle_receipt']==summary['read_idle_receipt'],'initial idle receipts differ')
    previous=initial['files']
    for number,record in enumerate(history,1):
        require(record['pass_number']==number and record==load(f'repair_{number}/summary.json'),'repair summary differs')
        missing=[r for r in previous if r['resident_pages']!=r['pages']]
        selected={r['source_path'] for r in missing}
        require(selected and set(record['selected_files'])==selected and record['prior_missing']==missing and record['selected_bytes']==sum(sources[n]['bytes'] for n in selected),'repair selection differs')
        selection=load(f'repair_{number}/selection.json')
        require(all(record[k]==value for k,value in selection.items()),'repair initial selection changed')
        proof=load(f'repair_{number}/reads.json')
        require(proof['reads']==record['reads'] and proof['read_threads_joined_ns']==record['read_threads_joined_ns'],'repair read receipts differ')
        reads(record['reads'],selected); idle(record['read_idle_receipt']); vectors(record['files'])
        require(record['begin_ns']<=record['read_threads_joined_ns']<=record['verification_begin_ns']<=record['verification_complete_ns'],'repair event ordering differs')
        previous=record['files']
    require(summary['files']==previous,'final cache proof not final full scan')
    require(summary['total_cache_admission_begin_ns']<=initial['read_threads_joined_ns']<=initial['verification_begin_ns']<=initial['verification_complete_ns']<=summary['verification_complete_ns']<=summary['total_cache_admission_end_ns'],'cache event coverage differs')
    vectors(summary['files'],full=True)
    eviction=[json.loads(line) for line in (dest/'eviction-mincore.jsonl').read_text().splitlines()]
    require(len(eviction)==148 and {r['source_path'] for r in eviction}==set(sources),'eviction closure differs')
    final={r['source_path']:r for r in summary['files']}
    for row in eviction:
        require(row['pages']==final[row['source_path']]['pages'] and flags(row)==row['resident_pages']==0,'initial eviction not fully cold')
    return dict(summary_sha256=digest(dest/'summary.json'),inputs=148,threshold='all pages resident; initial eviction zero',pgsteal='diagnostic only',repair_passes=len(history),placement_scope='reader CPU placement only; no physical-page NUMA guarantee')

def validate_native_idle(rows,baseline,helper,run,boot):
    require(len(rows)==61 and [row['second'] for row in rows]==list(range(61)),'native idle interval incomplete')
    require(rows[-1]['monotonic_ns']-rows[0]['monotonic_ns']>=60_000_000_000,'native idle shorter than60s')
    for row in rows:
        require_helper_only(row['host_gpu_snapshot'],helper,row['monotonic_ns'],run,boot,allow_engine=row['second']<60)
        require(row['memory_current']>=0 and row['shm_used_bytes']>=0 and int(row['pids_current'])>=0,'native resource counters invalid')
        memory={line.split()[0]:int(line.split()[1]) for line in row['memory_stat'].splitlines()}
        cpu={line.split()[0]:int(line.split()[1]) for line in row['cpu_stat'].splitlines()}
        require(all(key in memory and memory[key]>=0 for key in ('anon','file','shmem','unevictable')) and cpu.get('usage_usec',-1)>=0,'native resource accounting incomplete')
        used=[int(line.split(',')[1].strip().split()[0]) for line in row['gpu'].splitlines()]
        require(len(used)==8 and all(value>=0 for value in used),'native GPU accounting incomplete')
    final=[int(line.split(',')[1].strip().split()[0]) for line in rows[-1]['gpu'].splitlines()]
    require(len(baseline)==8 and all(a<=b+1024 for a,b in zip(final,baseline)),'native model memory retained after60s')

def expand_cpus(value):
    result=set()
    for part in value.split(','):
        bounds=part.split('-'); result.update(range(int(bounds[0]),int(bounds[-1])+1))
    return result

def accept(root,sha,code_commit):
    load=lambda name:json.loads((root/name).read_text())
    require(digest(root/'control-input-manifest.json')==sha,'input manifest changed')
    for row in load('control-input-manifest.json')['files']:
        path=(SOURCE/row['path']).resolve()
        require(path.is_relative_to(SOURCE) and digest(path)==row['sha256'],'input changed: '+row['path'])
    manifest=load('run-manifest.json'); deployment=load('deployment.json'); run=manifest['run_id']; boot=manifest['runtime_identity']['boot_id']
    require(deployment['code_commit']==code_commit and deployment['run_id']==run and deployment['input_manifest_sha256']==sha,'deployment binding changed')
    require(pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot,'boot changed')
    ends=[]; expected_gpu={}
    def register_gpu(snapshot):
        key=snapshot['last_sample_ns']
        require(key not in expected_gpu or expected_gpu[key]==snapshot,'same host sample has conflicting receipts')
        expected_gpu[key]=snapshot
    for label in ('ray13a','ray13b','service13a'):
        result=load(label+'-supervisor-result.json')
        require(result['status']=='passed including cleanup','supervisor failure')
        ends.append(result['end_ns'])
        for name in (label,'head'+label):
            identity=load(name+'-identity.json')
            require(identity['run_id']==run and identity['code_commit']==code_commit and identity['preflight']['boot_id']==boot,'dispatch binding changed')
    for label in ('ray13a','ray13b'): require(load('ray-only-'+label+'/result.json')['status']=='passed','Ray control failed')
    contract={r['rank']:r for r in json.loads((workspace()/'validation/native-weight-contract.json').read_text())['ranks']}
    for fmt in ('serverless_llm','safetensors'):
        proof=load('service-bootstrap-service13a/'+fmt+'/model-result.json')
        require(proof['status']=='A4 native HTTP lifecycle checks passed' and proof['cycles']==2 and proof['weight_format']==fmt,'native lifecycle failed')
        coverage=proof['rank_coverage']
        require(len(coverage)==16 and {(r['cycle'],r['rank']) for r in coverage}=={(cycle,rank) for cycle in (1,2) for rank in range(8)},'rank coverage incomplete')
        for row in coverage:
            expected=contract[row['rank']]
            require(row['weight_scale_derived_references']==expected['verified_weight_references'] and row['dsa_workspace_references_excluded']==expected['runtime_index_references_excluded'] and row['kv_references_excluded']==expected['kv_runtime_references_excluded'],'rank contract differs')
        helper=load(f'service-bootstrap-service13a/{fmt}/helper-gpu-identity.json')
        require_helper_binding(helper,load('service-bootstrap-service13a/store-numa-launch.json'),load('service13a-identity.json'),boot)
        for cycle in (1,2):
            before=load(f'service-bootstrap-service13a/{fmt}/cycle{cycle}-gpu-before.json')
            require_helper_only(before['host_gpu_snapshot'],helper,before['monotonic_ns'],run,boot)
            register_gpu(before['host_gpu_snapshot'])
            idle=[json.loads(line) for line in (root/f'service-bootstrap-service13a/{fmt}/cycle{cycle}-idle60.jsonl').read_text().splitlines()]
            validate_native_idle(idle,load(f'service-bootstrap-service13a/{fmt}/auxiliary-gpu-baseline.json'),helper,run,boot)
            for row in idle: register_gpu(row['host_gpu_snapshot'])
            pss=load(f'service-bootstrap-service13a/{fmt}/cycle{cycle}-pss.json')
            require(pss['status']=='completed' and pss['end_ns']>=pss['begin_ns'] and any(v['pid']==pss['store_pid'] and v['smaps_rollup'] is not None for v in pss['processes']),'PSS sample incomplete')
    cache_proof=validate_cache(root)
    pool=load('service-bootstrap-service13a/pool-ready.json'); restored=json.loads((workspace()/'validation/store-restore-validation.json').read_text())
    require(pool['pool_bytes']==restored['pool_bytes'] and pool['chunk_bytes']==restored['chunk_bytes'],'pool capacity differs')
    require(pool['rpc_config']==dict(mem_pool_size=pool['pool_bytes'],chunk_size=pool['chunk_bytes']) and pool['rss_bytes']>=pool['pool_bytes'],'real resident pool unproven')
    require(pool['listener']['pid']==pool['store_pid'] and pool['listener']['port']==8073 and pool['listener']['listening_sockets'],'pool listener differs')
    affinity=load('service-bootstrap-service13a/store-affinity-ready.json'); launch=load('service-bootstrap-service13a/store-numa-launch.json')
    target=set(launch['target_cpu_affinity'])
    require(affinity['pid']==pool['store_pid']==launch['pid'] and target and target==expand_cpus(launch['sysfs_cpu_list'].strip())&set(launch['prior_cpu_affinity']),'Store identity/target differs')
    require(affinity['tasks'] and all(expand_cpus(row['cpus_allowed_list']) and expand_cpus(row['cpus_allowed_list'])<=target for row in affinity['tasks']),'Store thread affinity differs')
    baseline=load('host-preparation.json')['gpu_baseline_mib']
    for stage in ('ray13a','ray13b','allstopped'): validate_release(load('release60-'+stage+'.json'),stage,run,baseline)
    summary=monitor_summary(root/'resource-monitor-v12.jsonl',gpu_context=(load('service-bootstrap-service13a/store-numa-launch.json'),load('service13a-identity.json'),boot),expected_gpu=expected_gpu)
    starts=[load('head'+label+'-dispatch.json')['dispatch_monotonic_ns'] for label in ('ray13a','ray13b','service13a')]
    require(summary['first_ns']<=min(starts) and summary['last_ns']>=max(ends),'monitor missing startup/cleanup coverage')
    index_path=root/'root-evidence-index13.json'
    require(index_path.stat().st_uid==0 and stat.S_IMODE(index_path.stat().st_mode)==0o444,'untrusted root evidence index')
    index=load('root-evidence-index13.json')
    require(index['run_id']==run and index['boot_id']==boot and index['code_commit']==code_commit and index['manifest_sha256']==sha and index['cleanup_passed'] and index['guard_probe_passed'] and index['deadline_timer_state']=='inactive' and not index['owned_containers'],'root evidence binding failed')
    require(index['guard_sha256']==digest(SOURCE/'cleanup_guard13.sh'),'root guard differs')
    required={'guard-verification.json','cleanup-v13-main.log','cleanup-v13-probe.log','kernel-private.jsonl'}
    require(required<={pathlib.PurePosixPath(r['path']).name for r in index['files']},'persistent root evidence missing')
    asset=workspace()
    for row in json.loads((SOURCE/'initial-ledger.json').read_text())['attempts']:
        directory=asset/'results/formal-trials'/row['attempt_id']
        if row['status']=='unstarted': require(not directory.exists(),'formal ID consumed')
        else: require(digest(directory/'result.json')==row['result_sha256'],'terminal result changed')
    receipt=dict(status='G1 acceptance passed; no formal samples',run_id=run,boot_id=boot,code_commit=code_commit,manifest_sha256=sha,monitor=summary,cache_control=cache_proof,root_evidence_index_sha256=digest(index_path),pool_initializations=1,ray_only_controls=2,native_lifecycles_per_format=2,scope='bounded current stability only; historical exit137 cause unknown')
    with (root/'G1-acceptance.json').open('x') as stream: json.dump(receipt,stream,indent=2)
    print(json.dumps(receipt,indent=2))

if __name__=='__main__': accept(run_root(),*sys.argv[1:])
