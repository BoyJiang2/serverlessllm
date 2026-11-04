"""One CPU fault injection for guard_cuda_micro; never starts an app or GPU."""
import hashlib,json,os,pathlib,select,signal,subprocess,time
ROOT=pathlib.Path('/data/brick1/boyjiang/kimi-new/ServerlessLLM')
GUARD=ROOT/'sllm_store/tests/guard_cuda_micro.py'
RUN='kimi-guard-fault-cpu-20261104-logfix-once'
BASE=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0')/RUN
CID=hashlib.sha256(RUN.encode()).hexdigest()
GUARD_UNIT=RUN+'-guard.service'; PARENT_UNIT=RUN+'-launcher.service'
SCOPE='docker-'+CID+'.scope'; CG=pathlib.Path('/sys/fs/cgroup/system.slice')/SCOPE
assert os.geteuid()==0 and not BASE.exists()
assert hashlib.sha256(GUARD.read_bytes()).hexdigest()=='9387374428ae285f9740a4fe6f638aec2ba781384a5a9fdc01b47f6b2249ea3d'
BASE.mkdir(mode=0o700); bundle=BASE/CID; bundle.mkdir(); state=BASE/'runc-state'; state.mkdir()
rootfs=bundle/'rootfs';rootfs.mkdir()
for name in ('dev','proc','sys'): (rootfs/name).mkdir()
def publish(name,data):
    raw=json.dumps(data,indent=2).encode(); temporary=BASE/(name+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    assert not (BASE/name).exists();os.rename(temporary,BASE/name)
def cmd(argv):
    result=subprocess.run(argv,text=True,capture_output=True,timeout=4)
    assert result.returncode==0,(argv,result.returncode,result.stderr[:1000])
    return result.stdout
def props(unit):
    names='Id,LoadState,ActiveState,SubState,MainPID,ControlGroup,ActiveEnterTimestampMonotonic,RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL,FinalKillSignal,Result,ExecMainCode,ExecMainStatus'
    return dict(row.split('=',1) for row in cmd(['/usr/bin/systemctl','show',unit,'--property='+names]).splitlines())
def identity(pid):
    root=pathlib.Path('/proc')/str(pid); fields=(root/'stat').read_text().rsplit(')',1)[1].split()
    return {'pid':pid,'startticks':int(fields[19]),'exe':os.readlink(root/'exe'),'cgroup':(root/'cgroup').read_text().strip()}
def gone(row):
    try:return int((pathlib.Path('/proc')/str(row['pid'])/'stat').read_text().rsplit(')',1)[1].split()[19])!=row['startticks']
    except FileNotFoundError:return True
def events():
    rows=[]
    for line in (BASE/'guard-events.jsonl').read_text().splitlines():
        try: rows.append(json.loads(line))
        except json.JSONDecodeError:pass
    return rows
def unit_command(unit,maximum,memory,tasks,out,argv):
    return ['/usr/bin/systemd-run','--unit='+unit,'--property=Type=exec','--property=Restart=no','--property=RuntimeMaxSec='+str(maximum),'--property=TimeoutStopSec=3','--property=KillMode=control-group','--property=SendSIGKILL=yes','--property=FinalKillSignal=SIGKILL','--property=CPUQuota=100%','--property=MemoryMax='+memory,'--property=TasksMax='+str(tasks),*(['--property=RemainAfterExit=yes'] if unit==GUARD_UNIT else []),'--property=StandardOutput=append:'+str(BASE/(out+'.stdout')),'--property=StandardError=append:'+str(BASE/(out+'.stderr')),*argv]
hook="trap '' TERM; printf 'CPU_HOOK_ENTERED\n' > "+str(BASE/'hook-entered')+"; kill -STOP $$; exit 0"
annotations={'org.systemd.property.'+k:v for k,v in {'RuntimeMaxUSec':'uint64 40000000','TimeoutStopUSec':'uint64 3000000','KillMode':"'control-group'",'SendSIGKILL':'true','FinalKillSignal':'int32 9'}.items()}
spec={'ociVersion':'1.2.1','root':{'path':str(rootfs),'readonly':True},'process':{'terminal':False,'user':{'uid':0,'gid':0},'args':['/__cpu_probe_no_app__'],'cwd':'/','env':[]},'annotations':annotations,'mounts':[{'destination':'/dev','type':'tmpfs','source':'tmpfs','options':['nosuid','strictatime','mode=755','size=65536']}],'linux':{'rootfsPropagation':'rprivate','cgroupsPath':'system.slice:docker:'+CID,'namespaces':[{'type':n} for n in ('mount','pid','ipc','uts','network','cgroup')],'devices':[],'resources':{'cpu':{'quota':100000,'period':100000},'memory':{'limit':2147483648,'swap':2147483648},'pids':{'limit':512}}},'hooks':{'createContainer':[{'path':'/bin/sh','args':['sh','-c',hook],'env':[],'timeout':90}]}}
config=bundle/'config.json';config.write_text(json.dumps(spec,indent=2))
guard_argv=['/usr/bin/python3',str(GUARD),CID,str(BASE),'--oci',str(config),'--runc-root',str(state)]
parent_argv=['/usr/bin/runc','--systemd-cgroup','--root',str(state),'--log',str(BASE/'runc-log.json'),'--log-format','json','create','--bundle',str(bundle),CID]
publish('preflight.json',{'cid':CID,'guard_sha256':hashlib.sha256(GUARD.read_bytes()).hexdigest(),'oci_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'guard_argv':guard_argv,'parent_argv':parent_argv,'scope':'CPU fault only, no NVIDIA/app/model/Store/GPU','budget_seconds':180})
started=time.monotonic();go=None;parent_started=False;rescuefd=None;rescue_authenticated=False
try:
    assert not CG.exists()
    assert all(props(unit)['LoadState']=='not-found' for unit in (GUARD_UNIT,PARENT_UNIT,SCOPE))
    cmd(unit_command(GUARD_UNIT,140,'256M',64,'guard-unit',guard_argv))
    while not (BASE/'ARMED.json').exists():
        assert time.monotonic()-started<7,'ARM startup timeout';time.sleep(.03)
    armed=json.loads((BASE/'ARMED.json').read_text());guardprops=props(GUARD_UNIT)
    assert armed['cid']==CID and int(guardprops['MainPID'])==armed['guard_pid']
    assert identity(armed['guard_pid'])['startticks']==armed['guard_startticks']
    assert guardprops['RuntimeMaxUSec']=='2min 20s' and guardprops['TimeoutStopUSec']=='3s'
    go=time.monotonic();publish('GO.json',{'cid':CID,'nonce':armed['nonce'],'monotonic':go})
    while not (BASE/'GO-ACK.json').exists():
        assert time.monotonic()-go<2,'GO ack timeout';time.sleep(.02)
    ack=json.loads((BASE/'GO-ACK.json').read_text());assert ack=={'cid':CID,'go_monotonic':go}
    cmd(unit_command(PARENT_UNIT,40,'3G',1024,'runc',parent_argv));parent_started=True
    while True:
        rows=events();pins=[r['identity'] for r in rows if r['event']=='runtime_pinned'];scopes=[r for r in rows if r['event']=='scope_pinned']
        if (BASE/'hook-entered').exists() and pins and scopes:break
        assert time.monotonic()<go+15,'bounded CPU startup failed';time.sleep(.03)
    parentprops=props(PARENT_UNIT);scopeprops=props(SCOPE)
    for row in (parentprops,scopeprops):
        assert row['ActiveState']=='active' and row['RuntimeMaxUSec']=='40s' and row['TimeoutStopUSec']=='3s'
        assert row['KillMode']=='control-group' and row['SendSIGKILL']=='yes' and row['FinalKillSignal']=='9'
    native_deadline=min(int(r['ActiveEnterTimestampMonotonic'])/1e6+40 for r in (parentprops,scopeprops))
    assert time.monotonic()<go+20 and go+31<native_deadline
    pid=int(parentprops['MainPID']);row=next(p for p in pins if p['pid']==pid)
    assert identity(pid)==row and row['cgroup']!='0::/system.slice/'+SCOPE
    assert (CG/'cgroup.kill').exists() and CG.stat().st_ino==scopes[-1]['inode']
    rescuefd=os.open(CG,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW)
    assert (os.fstat(rescuefd).st_dev,os.fstat(rescuefd).st_ino)==(CG.stat().st_dev,scopes[-1]['inode'])
    rescue_authenticated=True
    members=[]
    for value in (CG/'cgroup.procs').read_text().split():
        fields=(pathlib.Path('/proc')/value/'stat').read_text().rsplit(')',1)[1].split();members.append({'pid':int(value),'startticks':int(fields[19])})
    assert members
    fd=os.pidfd_open(pid)
    try:
        assert identity(pid)==row;signal.pidfd_send_signal(fd,signal.SIGSTOP)
    finally:os.close(fd)
    publish('injection.json',{'go':go,'injected_monotonic':time.monotonic(),'runc':row,'members':members,'scope_inode':CG.stat().st_ino,'native_deadline':native_deadline,'guard_unit':guardprops,'parent_unit':parentprops,'scope_unit':scopeprops})
    print(json.dumps({'event':'CPU_STOP_INJECTED','cid':CID,'base':str(BASE),'go_monotonic':go}),flush=True)
    while time.monotonic()<go+32:time.sleep(.1)
    assert time.monotonic()<native_deadline
    assert gone(row) and all(gone(m) for m in members)
    assert not CG.exists() or 'populated 0' in (CG/'cgroup.events').read_text().splitlines()
    rows=events();kills=[r for r in rows if r['event']=='scope_kill'];signals=[r for r in rows if r['event']=='runtime_signal' and r['signal']==9 and r['identity']==row]
    assert kills and signals and all(r['mono']<native_deadline for r in kills+signals)
    assert go+25<=kills[0]['mono']<go+28 and go+28<=signals[0]['mono']<go+31
    publish('pre-fallback-release.json',{'monotonic':time.monotonic(),'native_deadline':native_deadline,'all_owned_identities_absent':True,'scope_empty_or_removed':True,'guard_scope_kill':kills[0],'guard_runc_kill':signals[0]})
    print(json.dumps({'event':'BOTH_TARGET_DOMAINS_RELEASED_BEFORE_FALLBACK','cid':CID}),flush=True)
    while not (BASE/'guard-final.json').exists():
        assert time.monotonic()<go+130,'guard final timeout';time.sleep(1)
    final=json.loads((BASE/'guard-final.json').read_text());assert final['status']=='KERNEL_CLEANUP_RELEASE_PASS' and not final['failures'] and final['quiet_seconds']>=60
    while int((guardexit:=props(GUARD_UNIT))['MainPID'])!=0:
        assert time.monotonic()<go+130;time.sleep(.05)
    assert guardexit['ExecMainCode']=='1' and guardexit['ExecMainStatus']=='0' and guardexit['Result']=='success'
    assert all(gone(p) for p in final['pinned_identities']) and all(gone(p) for p in members)
    assert hashlib.sha256((BASE/'oci.raw.json').read_bytes()).hexdigest()==final['raw_oci_sha256']
    assert (BASE/'oci.raw.json').stat().st_mode&0o777==0o600
    publish('controller-final.json',{'status':'CPU_KERNEL_GUARD_FAULT_INJECTION_PASS','cid':CID,'elapsed_seconds':time.monotonic()-started,'guard_result':final,'parent_unit':props(PARENT_UNIT),'guard_unit_exit':guardexit})
    cmd(['/usr/bin/systemctl','stop',GUARD_UNIT])
    print(json.dumps({'status':'CPU_KERNEL_GUARD_FAULT_INJECTION_PASS','cid':CID,'base':str(BASE)}),flush=True)
except BaseException as error:
    publish('controller-failed.json',{'status':'FAILED_RETAINED','error':str(error),'go':go,'parent_started':parent_started})
    (BASE/'ABORT').touch(exist_ok=True)
    if parent_started:
        # Emergency CPU-only rescue is failure, never a guard pass.
        if rescuefd is not None and rescue_authenticated:
            try:
                current=os.stat(CG,follow_symlinks=False);opened=os.fstat(rescuefd)
                if (current.st_dev,current.st_ino)==(opened.st_dev,opened.st_ino):
                    killfd=os.open('cgroup.kill',os.O_WRONLY|os.O_NOFOLLOW,dir_fd=rescuefd)
                    try:os.write(killfd,b'1\n')
                    finally:os.close(killfd)
            except FileNotFoundError:pass
        subprocess.run(['/usr/bin/systemctl','kill',PARENT_UNIT,'--signal=KILL'],timeout=4,capture_output=True)
    raise

finally:
    if rescuefd is not None:os.close(rescuefd)
