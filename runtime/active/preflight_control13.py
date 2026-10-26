"""Fail closed on node, booking, root-guard and monitoring identity drift."""
import datetime,hashlib,json,os,pathlib,socket,stat,subprocess,sys,time
from workspace import SOURCE

if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden for verification code')

def require(value,message):
    if not value: raise RuntimeError(message)

def call(*args):
    return subprocess.check_output(args,text=True,timeout=15).strip()

def validate_ancestor_values(values,is_root,is_owned=False):
    for name in ('memory.max','memory.high'):
        value=values[name]
        if value is None: require(is_root,'missing non-root memory limit interface')
        else: require(value=='max' or int(value)>=1825361100800,'ancestor '+name+' below frozen budget')
    swap=values.get('memory.swap.max')
    if swap is None: require(is_root,'missing non-root swap limit interface')
    else:
        require(swap=='max' or int(swap)>=0,'invalid ancestor swap limit')
        if is_owned: require(swap=='0','own slice must prohibit swap')

def validate_memory_budget(available,weight_bytes,owned_anon=0):
    require(available>=64*1024**3,'insufficient uncommitted working margin')
    require(owned_anon>=0,'invalid owned anonymous memory')
    require(available+owned_anon>=768799145984+weight_bytes+64*1024**3,'insufficient host memory for pool/checkpoint/working margin')


def check(root,monitor_required=True,continuation=False):
    require((root/'run-manifest.json').read_bytes()==(SOURCE/'run-manifest.json').read_bytes(),'run registration differs from reviewed source')
    manifest=json.loads((root/'run-manifest.json').read_text())
    run=manifest['run_id']
    expected=manifest['runtime_identity']
    require(socket.gethostname()==expected['hostname'],'physical hostname changed')
    for name,path in [('machine_id','/etc/machine-id'),('boot_id','/proc/sys/kernel/random/boot_id')]:
        require(pathlib.Path(path).read_text().strip()==expected[name],name+' changed')
    uuids=call('nvidia-smi','--query-gpu=uuid','--format=csv,noheader').splitlines()
    require(uuids==expected['gpu_uuids'],'GPU UUID order changed')
    require(os.getuid()==manifest['uid']==2147,'UID changed')
    memory=pathlib.Path('/proc/meminfo').read_text()
    require(next(line.split()[1] for line in memory.splitlines() if line.startswith('SwapTotal:'))=='0','host swap configuration changed')
    available=int(next(line.split()[1] for line in memory.splitlines() if line.startswith('MemAvailable:')))*1024
    from resource_window_control13 import load_window
    window=load_window(root)
    guard=pathlib.Path('/run')/run
    proof_path=guard/'guard-verification.json'
    require(proof_path.stat().st_uid==0 and stat.S_IMODE(proof_path.stat().st_mode)==0o444,'untrusted root guard proof')
    proof=json.loads(proof_path.read_text())
    require(proof['run_id']==run and proof['probe_passed'] and proof['boot_id']==expected['boot_id'],'stale guard proof')
    script=guard/'cleanup-v13.sh'
    require(script.parent.stat().st_uid==0 and stat.S_IMODE(script.parent.stat().st_mode)==0o755,'untrusted guard directory')
    current=script.stat()
    require(all(getattr(current,key)==value for key,value in proof['script_stat'].items()),'root cleanup script metadata changed')
    require(current.st_uid==0 and stat.S_IMODE(current.st_mode)==0o700,'cleanup must be root-only')
    require(proof['cleanup_sha256']==hashlib.sha256((SOURCE/'cleanup_guard13.sh').read_bytes()).hexdigest(),'installed cleanup differs from reviewed source')
    service=run+'-deadline-v13.service'
    observed_exec=call('systemctl','show',service,'-p','ExecStart','--value')
    require(observed_exec==proof['service_exec'] and f'/bin/bash {script} {run} main' in observed_exec,'deadline service command changed')
    require(call('systemctl','is-active',window['guard_timer'])=='active','guard timer inactive')
    deadline=datetime.datetime.fromisoformat(window['cleanup_deadline'])
    require(deadline.strftime('%Y-%m-%d %H:%M:%S') in call('systemctl','show',window['guard_timer'],'-p','NextElapseUSecRealtime','--value'),'deadline changed')
    limits={'MemoryMax':'1825361100800','MemorySwapMax':'0','TasksMax':'8192'}
    for key,value in limits.items():
        require(call('systemctl','show',run+'.slice','-p',key,'--value')==value,'slice limit changed: '+key)
    group=call('systemctl','show',run+'.slice','-p','ControlGroup','--value')
    require(pathlib.PurePosixPath(group).name==run+'.slice','slice identity changed')
    # Observation allocates nothing: credit only existing anon in this verified slice.
    owned_anon=0
    if continuation:
        values=(pathlib.Path('/sys/fs/cgroup')/group.lstrip('/')/'memory.stat').read_text()
        owned_anon=int(next(line.split()[1] for line in values.splitlines() if line.startswith('anon ')))
    validate_memory_budget(available,manifest['weight_bytes'],owned_anon)
    ancestors={}
    current=pathlib.Path('/sys/fs/cgroup')/group.lstrip('/')
    while True:
        values={name:(current/name).read_text().strip() if (current/name).exists() else None for name in ('memory.max','memory.high','memory.swap.max','memory.events')}
        validate_ancestor_values(values,current==pathlib.Path('/sys/fs/cgroup'),current==pathlib.Path('/sys/fs/cgroup')/group.lstrip('/'))
        ancestors[str(current)]=values
        if current==pathlib.Path('/sys/fs/cgroup'): break
        current=current.parent
    if monitor_required:
        ready=json.loads((root/'monitor-v3-ready.json').read_text())
        require(ready['run_id']==run and ready['boot_id']==expected['boot_id'],'monitor identity changed')
        require(0<=time.monotonic_ns()-ready['last_sample_ns']<=5_000_000_000,'monitor not fresh')
        require(ready['observers_ok'],'observer liveness failed')
        for suffix in ['kernelobserver13','dockerobserver13']:
            unit=run+'-'+suffix+'.service'
            require(call('systemctl','is-active',unit)=='active','observer inactive: '+suffix)
            observer_group=call('systemctl','show',unit,'-p','ControlGroup','--value')
            require(observer_group.startswith(group+'/'),'observer escaped run slice')
    return dict(run_id=run,checked_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),boot_id=expected['boot_id'],gpu_uuids=uuids,guard_script_sha256=proof['cleanup_sha256'],control_group=group,monitor_required=monitor_required,host_meminfo=memory,available_memory_bytes=available,continuation=continuation,credited_owned_anon_bytes=owned_anon,ancestor_limits=ancestors)
