"""V7 five-step sequence, reusing the qualified fresh-Store controller."""
import collections, fcntl, hashlib, json, os, pathlib, re, shutil, socket, subprocess, sys, time, traceback

B=pathlib.Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
sys.path.insert(1,str(B))
from cache_ledger import Ledger, self_test
from v7_admission import deadline_ns
V7=B/'v7'
run_id=os.environ['SLLM_V7_RUN_ID']
assert re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',run_id)
assert socket.gethostname()=='qjhs3'
lock=(V7/'active.lock').open('a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
O=V7/'runs'/run_id
O.mkdir(parents=True)
P=json.loads((V7/'protocol.json').read_bytes())
assert P['stage']=='frozen'
assert P['sequence']==['kimi','glm','v4','kimi','kimi']
assert P['vllm_commit']=='132765e3560659ff63ebd236203672e991b70e08'
# Storage root stays '/data' for the qjhs003 record; a migrated node can point
# the same controller at another absolute path without changing any code.
ROOT=P.get('store_root','/data')
assert ROOT.startswith('/') and '..' not in ROOT
profiles=json.loads((V7/'candidates-v2/profiles.json').read_bytes())
GPUS=P['gpu_uuids']; containers={}; guards={}; handles=[]
ledger=Ledger(P['path_sizes']); parsed=[]; completed=[]
outcome=dict(scope=P['scope'], planned_fixed=5, planned_random=0)
store_pid=store_ticks=None
observer=None
store_gpu_baseline=None

def call(*args,timeout=30):
    r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(str(args)+'\n'+r.stdout)
    return r.stdout.strip()

def row(cid):return json.loads(call('docker','inspect',cid))[0]

def write(path,data):
    pending=path.with_name(path.name+'.pending')
    with pending.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
    os.link(pending,path);pending.unlink()


def verify_legacy_metadata(protocol):
    """Verify the archived Kimi/GLM index identities without reading weight files."""
    for model in ('kimi', 'glm'):
        manifest = protocol['payload_metadata'][model]
        root = pathlib.Path(manifest['root'])
        assert hashlib.sha256((root/'config.json').read_bytes()).hexdigest() == manifest['config_sha256'], model
        ranks = manifest['ranks']
        assert sorted(rank['rank'] for rank in ranks) == list(range(8)), model
        expected = {str((root/f"rank_{rank['rank']}").relative_to(ROOT)) for rank in ranks}
        assert expected == set(protocol['model_paths'][model]), model
        for rank in ranks:
            path = root/f"rank_{rank['rank']}"
            file_bytes = sum(file.stat().st_size for file in path.glob('tensor.data_*'))
            assert file_bytes == protocol['path_sizes'][str(path.relative_to(ROOT))], path
            assert 0 < rank['payload_bytes'] <= file_bytes, path
            for filename, digest in rank['metadata_sha256'].items():
                source = path/filename
                assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, source

def store_identity():
    r=row(containers['store']);pid=r['State']['Pid']
    assert r['State']['Running'] and r['RestartCount']==0 and pid==store_pid
    ticks=int(pathlib.Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19])
    assert ticks==store_ticks
    return dict(cid=containers['store'],host_pid=pid,start_ticks=ticks,ns=time.monotonic_ns())

def snapshot(destination):
    raw=call('docker','logs','--timestamps',containers['store'])
    relevant=[]
    for line in raw.splitlines():
        if any(s in line for s in ['SLLM_CACHE_','PinnedMemoryPool ','Deallocated ',' is freed from memory',' size ','Finished loading model ','Not enough memory for model',' chunks, remaining ','Confirm model ']):relevant.append(line)
    assert relevant[:len(parsed)]==parsed,'Native log ordering changed'
    delta=relevant[len(parsed):];ledger.feed(delta);parsed.extend(delta)
    destination.write_text('\n'.join(delta)+'\n')
    return ledger.settled()


def coverage(model,output,native_log):
    confirms=collections.Counter(path for path,uuid in re.findall(r'Confirm model (\S+) replica (\S+) success',native_log.read_text()))
    assert confirms==collections.Counter(P['model_paths'][model]),confirms
    full=collections.defaultdict(lambda:collections.defaultdict(list))
    groups={name:collections.Counter() for name in ['grouped_store_copy_end','grouped_expert_postprocess_host_end']}
    for path in (output/'events').glob('*.jsonl'):
        for line in path.read_text().splitlines():
            event=json.loads(line);name=event.get('name')
            if name in ('full_weight_load_begin','full_weight_load_end'):
                assert type(event['rank']) is int
                full[event['rank']][name].append(event)
            if name in groups:groups[name][event['rank'],event['group_id']]+=1
    assert set(full)==set(range(8))
    spans=[];clocks=set()
    for rank in range(8):
        rows=full[rank]
        assert set(rows)=={'full_weight_load_begin','full_weight_load_end'}
        assert all(len(values)==1 for values in rows.values())
        begin,end=rows['full_weight_load_begin'][0],rows['full_weight_load_end'][0]
        for key in ('pid','process_start_ticks','boot_id','time_namespace','clock_id'):
            assert begin[key]==end[key]
        assert begin['clock_id']=='CLOCK_MONOTONIC'
        clocks.add((begin['boot_id'],begin['time_namespace'],begin['clock_id']))
        assert begin['monotonic_ns']<=end['monotonic_ns'] and begin['sequence']<end['sequence']
        proc=pathlib.Path('/proc',str(end['pid']),'stat')
        try:live_ticks=int(proc.read_text().rsplit(')',1)[1].split()[19])
        except FileNotFoundError:live_ticks=None
        assert live_ticks!=end['process_start_ticks'],'Backend rank process still alive'
        spans.append(dict(rank=rank,pid=end['pid'],start_ticks=end['process_start_ticks'],begin_ns=begin['monotonic_ns'],end_ns=end['monotonic_ns'],seconds=(end['monotonic_ns']-begin['monotonic_ns'])/1e9))
    assert len(clocks)==1,'Cannot combine different host clock domains'
    if model=='v4':
        experts={f'expert_{i:04d}' for i in range(61)}
        for name,counts in groups.items():
            expected={(rank,group) for rank in range(8) for group in (experts|{'remainder'} if name=='grouped_store_copy_end' else experts)}
            assert set(counts)==expected and all(n==1 for n in counts.values())
    return dict(gpu_confirmed_paths=len(confirms),rank_load_spans=spans,
                max_rank_seconds=max(r['seconds'] for r in spans),
                cohort_host_span_seconds=(max(r['end_ns'] for r in spans)-min(r['begin_ns'] for r in spans))/1e9,
                scope='host load_model call; no added CUDA synchronization',
                v4_copy_groups=len(groups['grouped_store_copy_end']),
                v4_postprocess_groups=len(groups['grouped_expert_postprocess_host_end']))

def create(key,model):
    output=O/key;output.mkdir();profile=profiles[model]
    args=['docker','create','--network','host','--ipc','host','--pid','host','--gpus','"device='+','.join(GPUS)+'"','--cpus','4' if key=='store' else '32','--memory','1280g' if key=='store' else '512g','--pids-limit','4096','--ulimit','memlock=-1:-1','--stop-signal','SIGINT','--restart','no','--user','2147:2147','--label','sllm.v4.guard=shared-store-validation','--name','sllm-'+run_id+'-'+key,'--tmpfs','/tmp:rw,exec,nosuid,size=16g,mode=1777','--entrypoint',profile['python']]
    env=dict(profile['env'],STORAGE_PATH=ROOT,CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',NVIDIA_VISIBLE_DEVICES=','.join(GPUS),HOME='/output',USER='boyjiang',LOGNAME='boyjiang',PYTHONDONTWRITEBYTECODE='1',SLLM_EVENT_DIR='/output/events',SLLM_EVENT_RUN_ID=run_id+'-'+key,SLLM_REQUEST_PREFIX=key,LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/host-cuda/lib64:/usr/local/cuda/lib64:/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')
    for k,v in env.items():args+=['-e',k+'='+v]
    mounts=[] if key=='store' else list(profile['mounts'])
    mounts += [[str(B/'shared_store.py' if key=='store' else V7/'v7_backend.py'),'/control/script.py'],[ROOT,ROOT],['/etc/passwd','/etc/passwd'],['/etc/group','/etc/group']]
    if key=='store':mounts.append([str(B/'cache-observer-build-v1/observed_checkpoint_store.so'),'/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/_checkpoint_store.cpython-312-x86_64-linux-gnu.so'])
    else:mounts.append([str(V7/'candidates-v2'/(model+'-input.json')),'/control/input.json'])
    for source,target in mounts:args+=['--mount','type=bind,src='+source+',dst='+target+',readonly']
    args+=['--mount','type=bind,src='+str(output)+',dst=/output']
    if key!='store':
        for sub in ['cache','.cache']:args+=['--mount','type=bind,src='+str(O/'jit-cache'/model/sub)+',dst=/output/'+sub]
    args += [profile['image'],'/control/script.py']
    if key=='store':args+=['--root',ROOT,'--pool',str(1024**4),'--chunk','33554432']
    cid=call(*args,timeout=120);containers[key]=cid;state=row(cid)
    assert state['HostConfig']['DeviceRequests'][0]['DeviceIDs']==GPUS
    assert state['Image']==profile['image']
    assert state['HostConfig']['NanoCpus']==(4 if key=='store' else 32)*10**9
    assert state['HostConfig']['Memory']==(1280 if key=='store' else 512)*1024**3
    write(O/(key+'-created.json'),dict(id=cid,command=args,inspect=state))
    f=(O/(key+'-guard.log')).open('x');handles.append(f)
    guard=subprocess.Popen(['python3',str(B/'sequence_guard.py'),cid,str(deadline),str(O/(key+'-guard')),'--purpose','shared-store-validation'],stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,PYTHONPATH=str(B/'full-preparation/v4-source/sllm_store')),start_new_session=True);guards[key]=guard
    until=time.monotonic()+15
    while not (O/(key+'-guard/armed.json')).exists():
        assert guard.poll() is None and time.monotonic()<until
        time.sleep(.1)
    return cid

def idle(path):
    global store_gpu_baseline
    samples=[]
    for i in range(61):
        identity=store_identity();gpu=call('nvidia-smi','--query-gpu=index,memory.used','--format=csv,noheader,nounits');apps=call('nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits')
        assert all(int(line.split(',')[0])==store_pid for line in apps.splitlines()),apps
        memory=[int(line.split(',')[1]) for line in gpu.splitlines()]
        assert [int(line.split(',')[0]) for line in gpu.splitlines()]==list(range(8))
        if store_gpu_baseline is not None:
            assert all(value<=base+P['store_idle_gpu_tolerance_mib'] for value,base in zip(memory,store_gpu_baseline)), 'GPU memory did not return to the empty Store baseline'
        samples.append(dict(identity=identity,gpu=gpu,apps=apps))
        if i<60:time.sleep(1)
    assert (samples[-1]['identity']['ns']-samples[0]['identity']['ns'])>=60e9
    if store_gpu_baseline is None:
        store_gpu_baseline=[max(int(row['gpu'].splitlines()[i].split(',')[1]) for row in samples) for i in range(8)]
        write(O/'store-gpu-baseline.json',dict(mib_by_gpu=store_gpu_baseline,tolerance_mib=P['store_idle_gpu_tolerance_mib']))
    write(path,samples)

try:
    self_test()
    admission=json.loads((V7/'resource-admission.json').read_bytes())
    deadline=deadline_ns(admission,P,7200)
    write(O/'resource-admission.json',admission)
    for filename,digest in P['input_hashes'].items():assert hashlib.sha256((V7/'candidates-v2'/filename).read_bytes()).hexdigest()==digest
    for filename,digest in P['runtime_source_hashes'].items():assert hashlib.sha256(pathlib.Path(filename).read_bytes()).hexdigest()==digest,filename
    verify_legacy_metadata(P)
    assert hashlib.sha256((B/'cache-observer-build-v1/observed_checkpoint_store.so').read_bytes()).hexdigest()==P['store_extension_sha256']
    assert json.loads((B/'cache-observer-build-v1/import.json').read_bytes())['exit']==0
    assert json.loads((B/'cache-observer-build-v1/policy/receipt.json').read_bytes())['run_exit']==0
    gpu=call('nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader,nounits');apps=call('nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits')
    baseline=[int(line.split(',')[2]) for line in gpu.splitlines()]
    assert not apps and [l.split(',')[1].strip() for l in gpu.splitlines()]==GPUS and len(baseline)==8 and max(baseline)<=P['prelaunch_driver_baseline_mib'],gpu
    available=int(next(l.split()[1] for l in pathlib.Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    assert available>=1800*1024**3
    with socket.socket() as s:assert s.connect_ex(('127.0.0.1',8073))!=0
    write(O/'admission.json',dict(gpu=gpu,apps=apps,available_bytes=available,ns=time.monotonic_ns()))
    write(O/'protocol.json',P)
    observer_log=(O/'observer.log').open('x');handles.append(observer_log)
    observer=subprocess.Popen(['python3',str(V7/'v7_observe.py'),str(O),str(deadline+240*10**9)],stdout=observer_log,stderr=subprocess.STDOUT)
    for model in profiles:
        for sub in ['cache','.cache']:
            source=B/'cache-sequence-directed-v1/jit-cache'/model/sub;target=O/'jit-cache'/model/sub
            if model=='v4' and source.exists():shutil.copytree(source,target)
            else:target.mkdir(parents=True)
    create('store','v4')
    assert 0<=time.time_ns()-admission['checked_at_wall_ns']<120e9
    assert not call('nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits')
    call('docker','start',containers['store']);store_pid=row(containers['store'])['State']['Pid'];store_ticks=int(pathlib.Path('/proc',str(store_pid),'stat').read_text().rsplit(')',1)[1].split()[19]);write(O/'store-identity.json',store_identity())
    until=time.monotonic()+1200
    while True:
        store_identity()
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',8073))==0:break
        assert time.monotonic()<until
        time.sleep(2)
    idle(O/'empty-store-idle.json');snapshot(O/'empty-store-native.log')
    for index,model in enumerate(P['sequence']):
        for filename,digest in P['runtime_source_hashes'].items():assert hashlib.sha256(pathlib.Path(filename).read_bytes()).hexdigest()==digest,filename
        phase='fixed' if index<5 else 'random';key=f'{index:02d}-{phase}-{model}';expected=set(P['model_paths'][model]);offset=len(ledger.events);initial=ledger.settled();before=set(ledger.resident)&expected
        if index==3:assert not before,'Kimi was not fully evicted'
        if index==4:assert before==expected,'Kimi not resident before repeat'
        preparation=time.monotonic_ns();cid=create(key,model);output=O/key
        log=(O/(key+'-backend.log')).open('x');handles.append(log)
        process=subprocess.Popen(['docker','start','--attach',cid],stdout=log,stderr=subprocess.STDOUT)
        until=time.monotonic()+30
        while not (output/'waiting.json').exists():
            assert process.poll() is None and time.monotonic()<until
            time.sleep(.1)
        activation=time.monotonic_ns();(output/'start-model').touch(exist_ok=False)
        inputs=json.loads((V7/'candidates-v2'/(model+'-input.json')).read_bytes());business=next(r for r in inputs['requests'] if r['name']!='fixed32' and r['name']!='workload32');name='before-'+business['name'];until=time.monotonic()+1800
        while not ((output/(name+'-response.json')).exists() and (output/(name+'-checks.json')).exists()):
            assert process.poll() is None and time.monotonic()<until,key+' exited before correct response'
            store_identity();time.sleep(.2)
        response=json.loads((output/(name+'-response.json')).read_bytes());checks=json.loads((output/(name+'-checks.json')).read_bytes());choice=response['response']['choices'][0];assert checks['passed'] and choice['message']['content']==business['expected']['content'] and choice['finish_reason']==business['expected']['finish_reason']
        parsed_ns=time.monotonic_ns();write(output/'host-business-parsed.json',dict(preparation_ns=preparation,activation_ns=activation,correct_parsed_ns=parsed_ns,worker_received_ns=response['received_ns'],activation_to_correct_seconds=(parsed_ns-activation)/1e9,preparation_to_correct_seconds=(parsed_ns-preparation)/1e9,scope=P['timing_scope']))
        code=process.wait(timeout=max(1,until-time.monotonic()));assert code==0 and (output/'backend-passed.json').exists(),key+' failed'
        assert guards[key].wait(timeout=15)==0
        final=row(cid);write(O/(key+'-final.json'),final);assert final['State']['ExitCode']==0
        removed=subprocess.run(['docker','rm',cid],capture_output=True,text=True,timeout=30)
        write(output/'container-removal.json',dict(exit=removed.returncode,stdout=removed.stdout,stderr=removed.stderr))
        assert removed.returncode==0 or 'No such container' in removed.stderr or 'No such object' in removed.stderr
        containers.pop(key)
        state=snapshot(output/'native-store.log');cache=ledger.step(offset,expected)
        if index in (0,3):
            assert cache['cache_state']=='full_miss' and cache['allocated_blocks']>0
            assert cache['file_read_bytes']==sum(P['path_sizes'][p] for p in expected)
        if index==4:assert cache['cache_state']=='full_hit' and cache['allocated_blocks']==cache['file_read_bytes']==0
        rank_coverage=coverage(model,output,output/'native-store.log')
        result=dict(index=index,model=model,phase=phase,cache_initial_hit_paths=sorted(before),cache=cache,activation_ns=activation,correct_parsed_ns=parsed_ns,activation_to_correct_seconds=(parsed_ns-activation)/1e9,container_exit=0,store=store_identity(),rank_coverage=rank_coverage)
        write(output/'step-result.json',result);completed.append(result)
        idle(output/'store-idle.json');write(O/f'progress-{index:02d}.json',dict(completed=len(completed),planned=len(P['sequence']),last_model=model,cache_state=cache['cache_state']))
    kimi_paths=set(P['model_paths']['kimi'])
    victims={path for step in completed[1:3] for path in step['cache']['evicted_paths']}
    assert kimi_paths <= victims and sum(step['cache']['freed_blocks'] for step in completed[1:3])>0
    outcome['fixed_chain_passed']=len(completed)==5
except BaseException as error:
    outcome.update(error=repr(error),traceback=traceback.format_exc());write(O/'failure.json',outcome)
finally:
    for key,cid in reversed(list(containers.items())):
        try:
            r=row(cid)
            if r['State']['Running']:call('docker','stop','--time','120',cid,timeout=150)
            final=row(cid);write(O/(key+'-exit.json'),final);(O/(key+'-final.log')).write_text(call('docker','logs',cid));assert not final['State']['Running']
        except BaseException as error:outcome.setdefault('cleanup_errors',{})[key]=repr(error)
    for key,guard in guards.items():
        try:outcome.setdefault('guard_exit',{})[key]=guard.wait(timeout=15)
        except subprocess.TimeoutExpired:
            outcome.setdefault('cleanup_errors',{})[key+'-guard']='still running; independent lease guard retained'
            outcome.setdefault('guard_exit',{})[key]='pending'
    from v7_observe import collect_sample
    from v7_qualify_sleep import gpu_window
    release=[];begin=time.monotonic_ns()
    while time.monotonic_ns()-begin<75e9:
        release.append(collect_sample(O));time.sleep(1)
    write(O/'release.json',release)
    try:
        window=gpu_window(release,begin,time.monotonic_ns(),(release[0]['boot_id'],release[0]['time_namespace']),GPUS,minimum_seconds=60)
        outcome['release_seconds']=window['seconds'];outcome['release_missing']=len(window['missing'])
        outcome['all_gpu_zero']=all(not r['sample']['apps']['stdout'].strip() and not any(r['memory']) for r in window['valid'])
    except Exception as error:
        outcome.update(release_seconds=None,all_gpu_zero=None,release_error=repr(error))
    for key,cid in containers.items():
        try:
            if not row(cid)['State']['Running']:call('docker','rm',cid)
        except BaseException as error:outcome.setdefault('cleanup_errors',{})[key+'-remove']=repr(error)
    if observer is not None:
        write(O/'observation-stop.json',dict(ns=time.monotonic_ns()))
        try:outcome['observer_exit']=observer.wait(timeout=30)
        except subprocess.TimeoutExpired:
            observer.terminate();observer.wait(timeout=10);outcome['observer_exit']='terminated_after_stop_timeout'
    outcome['completed_fixed']=sum(r['phase']=='fixed' for r in completed);outcome['completed_random']=sum(r['phase']=='random' for r in completed);write(O/'result.json',outcome);print(json.dumps(outcome),flush=True)
assert outcome.get('fixed_chain_passed') and isinstance(outcome['release_seconds'],(int,float)) and outcome['release_seconds']>=60 and outcome['all_gpu_zero'] is True and outcome.get('observer_exit')==0 and not outcome.get('cleanup_errors') and all(v==0 for v in outcome.get('guard_exit',{}).values())
