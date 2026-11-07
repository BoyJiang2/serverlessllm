"""One two-container native service acceptance; reuse the existing deadline/release guards."""
import ast
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT=Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0]=[str(ROOT/'sllm_store'),str(ROOT/'benchmarks')]
from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once
from v4_native_reference import model_inventory
from v4_native_release import collect, cgroup_telemetry

IMAGE='sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
MODEL=Path('/mnt/raid/models/DeepSeek-V4-Pro')
STORAGE=Path('/data/brick1/boyjiang/sllm-v4-pro-store')
BUILD=Path('/data/brick1/boyjiang/sllm-v4-pro-build')
CALL_DEADLINE=None
MEMORY_BYTES={'head':16*1024**3,'worker':1024**4-16*1024**3}


def call(*args,timeout=5):
    if CALL_DEADLINE is not None:
        timeout=min(timeout,(CALL_DEADLINE-time.monotonic_ns())/1e9)
        if timeout<=0:raise TimeoutError('Native service total deadline exhausted')
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=timeout).strip()


def gpu_snapshot():
    raw=call('nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.used','--format=csv,noheader,nounits')
    return dict(raw=raw,rows=[[value.strip() for value in line.split(',')] for line in raw.splitlines()],
                apps=call('nvidia-smi','--query-compute-apps=pid,gpu_uuid,used_memory','--format=csv,noheader,nounits'))


def inspect(cid):
    row,=json.loads(call('docker','inspect',cid))
    assert row['Id']==cid and row['Image']==IMAGE
    return row


def group_for(pid,cid):
    text=Path('/proc',str(pid),'cgroup').read_text()
    relative,=[line.split(':',2)[2] for line in text.splitlines() if line.startswith('0::')]
    path=Path('/sys/fs/cgroup')/relative.lstrip('/')
    assert cid in str(path) and '..' not in path.parts
    return path


def process_rows(group):
    rows=[]
    if group and group.exists():
        for file in group.rglob('cgroup.procs'):
            for pid in file.read_text().split():
                try:
                    fields=Path('/proc',pid,'stat').read_text().rsplit(')',1)[1].split()
                except FileNotFoundError:
                    continue
                rows.append(dict(pid=int(pid),start_ticks=int(fields[19])))
    return rows


def store_limits(store):
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available=int(memory['MemAvailable'].split()[0])*1024
    free=shutil.disk_usage(STORAGE).free
    logical=sum(p.stat().st_size for p in store.rglob('*') if p.is_file())
    allocated=sum(p.stat().st_blocks*512 for p in store.rglob('*') if p.is_file())
    return dict(host_available_bytes=available,storage_free_bytes=free,logical_bytes=logical,
                allocated_bytes=allocated,within_limits=available>=64*1024**3 and free>=128*1024**3
                and max(logical,allocated)<=1024**4)


def release_roles(base,roles,groups,workers,baseline,clock,origin,deadline):
    assert set(groups)==set(roles) and workers, 'Missing component cgroup/process binding'
    jobs={role:dict(identity={k:value[k] for k in ('container_id','created','image')},
        baseline_gpu_mib={row[1]:int(row[4]) for row in baseline['rows']},cgroup=str(groups[role]),
        worker_processes=list(workers.values()),boot_id=clock['boot_id'],time_namespace=clock['time_namespace'],
        preparation_begin_ns=origin,delete_ack_ns=time.monotonic_ns(),deadline_ns=deadline) for role,value in roles.items()}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(collect,jobs[role],base/(role+'-release')) for role in roles]
        errors=[]
        for future in futures:
            try:future.result()
            except BaseException as error:errors.append(error)
        if errors:raise errors[0]


def model_unloaded(gpu,daemon_pid,context_gpu):
    apps=[line.split(',') for line in gpu['apps'].splitlines() if line.strip()]
    return (len(gpu['rows'])==8 and {row[1] for row in gpu['rows']}==set(context_gpu)
        and all(0<=int(row[4])<=context_gpu[row[1]]+16 for row in gpu['rows'])
        and len(apps)==8 and {row[1].strip() for row in apps}==set(context_gpu)
        and all(int(row[0])==daemon_pid and 0<=int(row[2])<=338 for row in apps))


def process_alive(proc):
    try:fields=Path('/proc',str(proc['pid']),'stat').read_text().rsplit(')',1)[1].split()
    except FileNotFoundError:return False
    return int(fields[19])==proc['start_ticks']


def main():
    global CALL_DEADLINE
    base=Path(sys.argv[1]).resolve();head=sys.argv[2]
    execute=sys.argv[3:]==['--execute']
    assert sys.argv[3:] in ([],['--execute'])
    if not execute:
        print(json.dumps(dict(source_head=head,output=str(base),cycles=2,gpu_or_model_started=False,
             head_cpu=4,worker_cpu=4,memory_limit_total=sum(MEMORY_BYTES.values()),memory_limits=MEMORY_BYTES,
             container_seconds=1080,total_seconds=1200)))
        return
    assert base.parent==ROOT.parent/'evidence' and not base.exists()
    origin=time.monotonic_ns();deadline=origin+1200_000_000_000;container_deadline=origin+1080_000_000_000
    CALL_DEADLINE=deadline
    lease=(ROOT.parent/'native-run.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert call('git','-C',str(ROOT),'rev-parse','HEAD')==head
    assert not call('git','-C',str(ROOT),'status','--porcelain')
    base.mkdir();output=base/'output';output.mkdir()
    clock=process_identity();roles={};guards={};groups={};workers={};network=base.name
    initial=model_inventory(MODEL);baseline=gpu_snapshot()
    assert len(baseline['rows'])==8 and not baseline['apps']
    assert all(int(row[4])==0 and 'H20' in row[2] for row in baseline['rows'])
    assert set(call('nvidia-smi','--query-gpu=driver_version','--format=csv,noheader').splitlines())=={'580.95.05'}
    qualified=json.loads((ROOT/'docs/evidence/v4-store-data-integrity/qualified-store.json').read_bytes())
    store=Path(qualified['store_root']);assert store.parent==STORAGE
    limits=store_limits(store);assert limits['within_limits'] and limits['host_available_bytes']>=1024**4+64*1024**3
    assert hashlib.sha256((store/'COMPLETE.json').read_bytes()).hexdigest()==qualified['complete_sha256']
    recovery=ROOT/'docs/evidence/v4-store-recovery-success/offline-recovery-review.json'
    proof=json.loads(recovery.read_bytes());assert proof['offline_full_recovery_requests_qualified']
    calibration=json.loads((ROOT/'docs/evidence/v4-native-service-entry/store-context-calibration.json').read_bytes())
    assert calibration['source_review_sha256']==hashlib.sha256(recovery.read_bytes()).hexdigest()
    assert calibration['image']==IMAGE and calibration['driver']=='580.95.05' and calibration['tolerance_mib']==16
    assert all(value==322 for value in calibration['process_context_mib'].values())
    context_gpu=calibration['gpu_context_mib']
    assert set(context_gpu)=={row[1] for row in baseline['rows']}
    scratch=BUILD/('native-service-'+base.name);scratch.mkdir()
    cache=BUILD/'cache-native-reference-20261106T205752Z'
    manifest=json.loads((ROOT/'docs/evidence/v4-native-reference-success/native-jit-artifacts.json').read_bytes())
    for artifact in manifest['files']:
        path=cache/artifact['path'];assert path.stat().st_size==artifact['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256']
    preparation=dict(source_head=head,ray_namespace='models',memory_limits=MEMORY_BYTES,gpu_initial=baseline,initial_limits=limits,model_inventory=initial,
        pool_bytes=890937278464,storage_path=str(STORAGE),store_path=str(store),identity=qualified['identity'],
        context_gpu_baseline_mib=context_gpu,context_process_baseline_mib=322,
        context_baseline_scope='Same driver/software/H20 UUIDs; observed daemon322MiB and total345MiB in qualified offline recovery;16MiB tolerance',
        preparation_begin_ns=origin,deadline_ns=deadline,container_deadline_ns=container_deadline)
    write_once(base/'preparation.json',preparation)
    wrapper=next(node.value.value for node in ast.walk(ast.parse((ROOT/'benchmarks/v4_run_once.py').read_text()))
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='wrapper' for target in node.targets)
        and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str))
    wrapper+="\nsys.argv=['v4_native_service','worker','--execute']\nrunpy.run_path('/workspace/benchmarks/v4_native_service.py',run_name='__main__')\n"
    (base/'gpu-entry.py').write_text(wrapper)
    try:
        network_id=call('docker','network','create','--internal','--label','sllm.v4.service='+base.name,network)
        write_once(base/'network.json',dict(name=network,id=network_id))
        for role in ('head','worker'):
            ray_dir=scratch/(role+'-ray');ray_dir.mkdir()
            role_cache=(scratch/'head-cache') if role=='head' else cache
            role_cache.mkdir(exist_ok=True)
            for directory in ('home','hf','cuda','triton','pycache','xdg','torchinductor','torch_extensions'):
                (role_cache/directory).mkdir(exist_ok=True)
            memory=MEMORY_BYTES[role]
            command=['docker','create','--name',base.name+'-'+role,'--network',network,
                '--runtime','runc' if role=='head' else 'nvidia','--cpus','4',
                '--memory',str(memory),'--memory-swap',str(memory),'--pids-limit','4096',
                '--shm-size','512m' if role=='head' else '16g','--read-only','--restart','no','--init',
                '--cap-drop','ALL','--security-opt','no-new-privileges','--user','2147:2147',
                '--workdir','/workspace',
                '--ulimit','memlock=-1:-1','--ulimit','core=0:0','--label','sllm.v4.guard=native-service',
                '--tmpfs','/tmp:rw,exec,nosuid,size=16g,mode=1777','--entrypoint','/opt/sllm-v4/bin/python']
            if role=='worker':command+=['--gpus','"device='+','.join(row[1] for row in baseline['rows'])+'"']
            env=['CUDA_VISIBLE_DEVICES=' + ('' if role=='head' else '0,1,2,3,4,5,6,7'),
                 'LD_LIBRARY_PATH='+('/usr/local/cuda/compat:/usr/local/cuda/lib64' if role=='head' else '/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'),
                 'HOME=/runtime-cache/home','USER=boyjiang','LOGNAME=boyjiang','OMP_NUM_THREADS=1','MKL_NUM_THREADS=1',
                 'VLLM_PLUGINS=sllm_v4_store','HF_HUB_OFFLINE=1','TRANSFORMERS_OFFLINE=1','RAY_USAGE_STATS_ENABLED=0',
                 'STORAGE_PATH='+str(STORAGE),'SLLM_EVENT_DIR=/output/events','SLLM_EVENT_RUN_ID='+base.name,
                 'PYTHONPATH=/workspace/benchmarks','PYTHONPYCACHEPREFIX=/runtime-cache/pycache','HF_HOME=/runtime-cache/hf',
                 'XDG_CACHE_HOME=/runtime-cache/xdg','TRITON_CACHE_DIR=/runtime-cache/triton','CUDA_CACHE_PATH=/runtime-cache/cuda',
                 'TORCHINDUCTOR_CACHE_DIR=/runtime-cache/torchinductor','TORCH_EXTENSIONS_DIR=/runtime-cache/torch_extensions']
            if role=='head':env+=['NVIDIA_VISIBLE_DEVICES=void']
            for value in env:command+=['--env',value]
            profile=ROOT/'docs/evidence/v4-runtime-user-failure/cpu-profile'
            mounts=[(ROOT,'/workspace',True),(base,'/control',True),(output,'/output',False),
                    (role_cache,'/runtime-cache',False),(ray_dir,'/tmp/ray',False),
                    (ROOT/'sllm','/opt/sllm-v4/lib/python3.12/site-packages/sllm',True),
                    (profile/'passwd','/etc/passwd',True),(profile/'group','/etc/group',True)]
            if role=='worker':mounts+=[(MODEL,str(MODEL),True),(STORAGE,str(STORAGE),True)]
            for path in (ROOT/'sllm_store/sllm_store').rglob('*.py'):
                mounts.append((path,'/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/'+str(path.relative_to(ROOT/'sllm_store/sllm_store')),True))
            for src,dst,readonly in mounts:command+=['--mount','type=bind,src='+str(src)+',dst='+dst+(',readonly' if readonly else '')]
            command+=[IMAGE,'-u']+(['/workspace/benchmarks/v4_native_service.py','head','--execute'] if role=='head' else ['/control/gpu-entry.py'])
            cid=call(*command,timeout=20);row=inspect(cid);assert row['State']['Status']=='created'
            roles[role]=dict(container_id=cid,created=row['Created'],image=row['Image'],command=command)
            with (base/(role+'-guard.log')).open('x') as log:
                guards[role]=subprocess.Popen(['python3',str(ROOT/'benchmarks/v4_container_guard.py'),cid,
                     str(container_deadline),str(base/(role+'-guard')),'--purpose','native-service'],
                     stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
                     env=dict(os.environ,PYTHONPATH=str(ROOT/'sllm_store')))
            while not (base/(role+'-guard/armed.json')).exists():
                assert guards[role].poll() is None and time.monotonic_ns()<container_deadline
                time.sleep(.1)
        write_once(base/'READY.json',dict(roles=roles,controller=clock))
        while not (base/'GO.json').exists():
            assert time.monotonic_ns()<origin+120_000_000_000
            time.sleep(.2)
        go=json.loads((base/'GO.json').read_bytes())
        assert go['source_head']==head and go['purpose']=='native-service' and go['notified_before_gpu'] and go['authorization']
        current=gpu_snapshot();assert not current['apps'] and all(int(row[4])==0 for row in current['rows'])
        assert store_limits(store)['host_available_bytes']>=1024**4+64*1024**3
        write_once(base/'launch.json',dict(launch_ns=time.monotonic_ns(),authorization=go))
        for role in ('head','worker'):
            cid=roles[role]['container_id'];call('docker','start',cid,timeout=20)
            state=inspect(cid)['State']
            if state['Pid']:
                groups[role]=group_for(state['Pid'],cid)
                for proc in process_rows(groups[role]):workers[(proc['pid'],proc['start_ticks'])]=proc
        unload=[];unload_begin=None;daemon_host_pid=None;first_model_processes={}
        with (base/'telemetry.jsonl').open('x') as stream:
            while True:
                states={role:inspect(value['container_id'])['State'] for role,value in roles.items()}
                sample=dict(monotonic_ns=time.monotonic_ns(),states=states,workers={},cgroups={})
                for role,state in states.items():
                    if state['Pid'] and role not in groups:groups[role]=group_for(state['Pid'],roles[role]['container_id'])
                    observed=process_rows(groups.get(role));sample['workers'][role]=observed
                    for proc in observed:workers[(proc['pid'],proc['start_ticks'])]=proc
                    if role in groups and groups[role].exists():sample['cgroups'][role]=cgroup_telemetry(groups[role])
                try:sample['gpu']=gpu_snapshot()
                except subprocess.TimeoutExpired as error:sample.update(gpu=None,gpu_observation_error=repr(error))
                sample['limits']=store_limits(store);sample['observation_end_ns']=time.monotonic_ns()
                stream.write(json.dumps(sample)+'\n');stream.flush()
                assert sample['limits']['within_limits']
                if daemon_host_pid is None and (output/'store-daemon-started.json').exists():
                    namespace_pid=json.loads((output/'store-daemon-started.json').read_bytes())['pid']
                    for proc in sample['workers']['worker']:
                        try:status=Path('/proc',str(proc['pid']),'status').read_text()
                        except FileNotFoundError:continue
                        nspid=next(line for line in status.splitlines() if line.startswith('NSpid:')).split()[-1]
                        if int(nspid)==namespace_pid:daemon_host_pid=proc['pid']
                if sample['gpu'] and daemon_host_pid and not (output/'cycle1-delete-ack.json').exists():
                    for line in sample['gpu']['apps'].splitlines():
                        if not line.strip():continue
                        pid=int(line.split(',')[0]);procpath=Path('/proc',str(pid))
                        try:
                            cgroup=(procpath/'cgroup').read_text()
                            fields=(procpath/'stat').read_text().rsplit(')',1)[1].split()
                        except FileNotFoundError:continue
                        assert roles['worker']['container_id'] in cgroup, 'Foreign GPU process entered selected devices'
                        if pid!=daemon_host_pid:
                            key=(pid,int(fields[19]));first_model_processes[key]=dict(pid=pid,start_ticks=key[1])
                if any(state['Status'] in ('exited','dead') and state['ExitCode']!=0 for state in states.values()):
                    raise RuntimeError('Native service role failed')
                if (output/'cycle1-delete-ack.json').exists() and not (output/'cycle1-unload-observation.json').exists():
                    assert daemon_host_pid is not None and first_model_processes
                    gpu=sample['gpu'];ok=gpu is not None
                    if gpu:
                        ok=model_unloaded(gpu,daemon_host_pid,context_gpu) and not any(process_alive(proc) for proc in first_model_processes.values())
                    if ok:
                        if unload and sample['monotonic_ns']-unload[-1]['observation_end_ns']>5_000_000_000:
                            unload=[];unload_begin=None
                        if unload_begin is None:unload_begin=sample['monotonic_ns']
                        unload.append(sample)
                        if (sample['monotonic_ns']-unload_begin)/1e9>=60:
                            write_once(output/'cycle1-unload-observation.json',dict(model_unload_observed=True,
                                sample_span_seconds=(sample['monotonic_ns']-unload_begin)/1e9,samples=unload,
                                original_gpu_model_processes=list(first_model_processes.values()),
                                scope='GPU model processes gone; only calibrated Store daemon context remains; not entire Store release'))
                    else:
                        unload=[];unload_begin=None
                if all(not state['Running'] for state in states.values()):break
                assert time.monotonic_ns()<origin+1100_000_000_000
                time.sleep(2)
        finals={role:inspect(value['container_id']) for role,value in roles.items()}
        for role,row in finals.items():
            assert not row['State']['Running'] and row['State']['Pid']==0
            write_once(base/(role+'-final.json'),row)
            (base/(role+'-container.log')).write_text(call('docker','logs',roles[role]['container_id'],timeout=10))
        release_roles(base,roles,groups,workers,baseline,clock,origin,deadline)
        codes={role:guard.wait(timeout=5) for role,guard in guards.items()}
        assert all(row['State']['ExitCode']==0 for row in finals.values()) and all(code==0 for code in codes.values())
        assert model_inventory(MODEL)==initial and (output/'head-candidate.json').exists()
        assert call('git','-C',str(ROOT),'rev-parse','HEAD')==head and not call('git','-C',str(ROOT),'status','--porcelain')
        write_once(base/'candidate.json',dict(container_exit_codes={role:row['State']['ExitCode'] for role,row in finals.items()},
            guard_exit_codes=codes,total_elapsed_seconds=(time.monotonic_ns()-origin)/1e9,
            release_observation_passed=True,formal_id_consumed=False,scope='Native two-cycle service candidate; separate review required'))
    except BaseException as error:
        failure=dict(error=repr(error),traceback=traceback.format_exc(),failed_ns=time.monotonic_ns(),formal_id_consumed=False)
        for role,value in roles.items():
            try:
                row=inspect(value['container_id'])
                if row['State']['Pid']:
                    groups[role]=group_for(row['State']['Pid'],value['container_id'])
                    for proc in process_rows(groups[role]):workers[(proc['pid'],proc['start_ticks'])]=proc
                if row['State']['Running']:call('docker','kill',value['container_id'],timeout=90)
                failure[role+'-final']=inspect(value['container_id'])
                (base/(role+'-failure.log')).write_text(call('docker','logs',value['container_id'],timeout=10))
            except BaseException as cleanup_error:
                failure[role+'-cleanup-error']=repr(cleanup_error)
        if roles and set(groups)==set(roles) and workers and not any((base/(role+'-release')).exists() for role in roles):
            try:
                release_roles(base,roles,groups,workers,baseline,clock,origin,deadline)
                failure['release_observation_passed']=True
            except BaseException as release_error:failure['release_error']=repr(release_error)
        if roles and (base/'launch.json').exists() and not failure.get('release_observation_passed'):
            try:
                observed=[]
                with (base/'failure-release60.jsonl').open('x') as stream:
                    for second in range(61):
                        assert time.monotonic_ns()<deadline
                        sample=dict(second=second,monotonic_ns=time.monotonic_ns(),gpu=gpu_snapshot(),
                            components={role:inspect(value['container_id'])['State'] for role,value in roles.items()})
                        stream.write(json.dumps(sample)+'\n');stream.flush();observed.append(sample)
                        if second<60:time.sleep(1)
                failure['raw_observation_span_seconds']=(observed[-1]['monotonic_ns']-observed[0]['monotonic_ns'])/1e9
                failure['raw_observation_scope']='Temporal/GPU/component evidence only; incomplete lineage is not cleanup PASS'
            except BaseException as observe_error:failure['raw_observation_error']=repr(observe_error)
        write_once(base/'failure.json',failure)
        raise
    finally:
        cleanup={}
        for role,value in roles.items():
            try:
                guard=guards.get(role)
                if guard and guard.poll() is None:
                    guard.terminate();guard.wait(timeout=5)
                row=inspect(value['container_id'])
                assert row['Created']==value['created'] and not row['State']['Running'] and row['State']['Pid']==0
                call('docker','rm',value['container_id'])
                cleanup[role]=dict(stopped=True,removed=True)
            except BaseException as cleanup_error:cleanup[role]=dict(error=repr(cleanup_error))
        if 'network_id' in locals():
            try:
                info,=json.loads(call('docker','network','inspect',network))
                assert info['Id']==network_id and not info['Containers']
                call('docker','network','rm',network)
                cleanup['network_removed']=True
            except BaseException as cleanup_error:cleanup['network_error']=repr(cleanup_error)
        cleanup['completed_ns']=time.monotonic_ns()
        write_once(base/'cleanup.json',cleanup)


if __name__=='__main__':main()
