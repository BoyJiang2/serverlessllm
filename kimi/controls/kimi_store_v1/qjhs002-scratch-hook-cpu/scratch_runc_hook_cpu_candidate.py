"""One-use instrumented CPU stage control; native systemd owns the timeout and whole-tree kill.
Not original GPU cause evidence: original OCI/ancestor limits are gone; strace and scratch differ.
Root reviewer must approve the exact source/config/unit, after the OS guard's release observation.
"""
import hashlib,json,os,pathlib,re,subprocess,sys,time

unit,evidence_arg=sys.argv[1:]
evidence=pathlib.Path(evidence_arg)
assert os.geteuid()==0 and re.fullmatch(r'kimi-scratch-hook-cpu-[a-z0-9-]+\.service',unit)
assert evidence==evidence.resolve() and evidence.parent==pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0')
assert evidence.name==unit.removesuffix('.service') and evidence.is_dir()
relative='/system.slice/'+unit
assert pathlib.Path('/proc/self/cgroup').read_text().strip()=='0::'+relative
group=pathlib.Path('/sys/fs/cgroup'+relative)
properties='Type,Restart,ControlGroup,MainPID,Delegate,DelegateControllers,RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL,FinalKillSignal,MemoryMax,TasksMax,CPUQuotaPerSecUSec'
text=subprocess.check_output(['/usr/bin/systemctl','show',unit,'--no-pager','--property='+properties],text=True)
props=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
required={'Type':'exec','Restart':'no','ControlGroup':relative,'MainPID':str(os.getpid()),'Delegate':'yes','RuntimeMaxUSec':'30s','TimeoutStopUSec':'3s','KillMode':'control-group','SendSIGKILL':'yes','FinalKillSignal':'9','MemoryMax':'3221225472','TasksMax':'1024','CPUQuotaPerSecUSec':'1s'}
assert all(props.get(k)==v for k,v in required.items()),props
assert set(props.get('DelegateControllers','').split())=={'cpu','memory','pids'}
assert group.joinpath('cgroup.type').read_text().strip()=='domain'
assert {'cpu','memory','pids'}<=set(group.joinpath('cgroup.controllers').read_text().split())
supervisor=group/'supervisor';leaf=group/'container'
supervisor.mkdir();leaf.mkdir()
supervisor.joinpath('cgroup.procs').write_text(str(os.getpid()))
assert not group.joinpath('cgroup.procs').read_text().strip()
group.joinpath('cgroup.subtree_control').write_text('+cpu +memory +pids')
assert {'cpu','memory','pids'}<=set(group.joinpath('cgroup.subtree_control').read_text().split())
assert all(p.joinpath('cgroup.type').read_text().strip()=='domain' for p in (supervisor,leaf))
bundle=evidence/'bundle';bundle.mkdir()
rootfs=bundle/'rootfs';rootfs.mkdir()
for name in ('dev','proc','sys'):rootfs.joinpath(name).mkdir()
runtime=evidence/'runc-state';runtime.mkdir()
identifier=unit.removesuffix('.service')
hook='/usr/bin/nvidia-cdi-hook';runc='/usr/bin/runc';strace='/usr/bin/strace'
def sha(path):return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
binary_sha={path:sha(path) for path in (hook,runc,strace)}
assert binary_sha[hook]=='fb9e22c288346e9a99cfbef446a8495932073608aa9982628a750cacb129925c'
assert subprocess.check_output([runc,'--version'],text=True).startswith('runc version 1.3.5\n')
spec={'ociVersion':'1.2.1','root':{'path':str(rootfs),'readonly':True},
      'process':{'terminal':False,'user':{'uid':0,'gid':0},'args':['/__cpu_probe_no_app__'],'cwd':'/','env':[]},
      'mounts':[{'destination':'/dev','type':'tmpfs','source':'tmpfs','options':['nosuid','strictatime','mode=755','size=65536']}],
      'linux':{'rootfsPropagation':'rprivate','cgroupsPath':relative+'/container','namespaces':[{'type':n} for n in ('mount','pid','ipc','uts','network','cgroup')],
               'devices':[],'resources':{'cpu':{'quota':100000,'period':100000},'memory':{'limit':2147483648,'swap':2147483648},'pids':{'limit':128}}},
      'hooks':{'createContainer':[{'path':hook,'args':['nvidia-cdi-hook','--version'],'env':['NVIDIA_CTK_DEBUG=false'],'timeout':5}]}}
config=bundle/'config.json'
with config.open('x') as output:json.dump(spec,output,indent=2)
def publish(name,data,exclusive=False):
    final=evidence/name;temp=evidence/(name+'.tmp')
    with temp.open('x') as output:json.dump(data,output,indent=2)
    if exclusive:os.link(temp,final);temp.unlink()
    else:os.replace(temp,final)
def read(path):
    try:return pathlib.Path(path).read_text().strip()
    except OSError as error:return {'unavailable_errno':error.errno}
def identity(pid):
    proc=pathlib.Path('/proc')/str(pid)
    try:
        first=proc.joinpath('stat').read_text().rsplit(')',1)[1].split()
        cg=proc.joinpath('cgroup').read_text().strip()
        if cg!='0::'+relative and not cg.startswith('0::'+relative+'/'):return None
        status=dict(line.split(':',1) for line in proc.joinpath('status').read_text().splitlines() if ':' in line)
        row={'pid':pid,'startticks':int(first[19]),'ppid':int(first[1]),'cgroup':cg,
             'status':{k:status[k].strip() for k in ('Name','Uid','Gid','Threads','CapEff','Seccomp','Cpus_allowed_list')},'limits':read(proc/'limits')}
        assert proc.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]==first[19]
        return row
    except (OSError,AssertionError):return None
observed={}
def snapshot():
    for path in (group,supervisor,leaf):
        pids=read(path/'cgroup.procs')
        if not isinstance(pids,str):continue
        for value in pids.split():
            row=identity(int(value))
            if row:
                key=str(row['pid'])+':'+str(row['startticks'])
                entry=observed.setdefault(key,{'first':row,'first_seen':time.time()})
                entry.update(last=row,last_seen=time.time())
    names=('cgroup.events','pids.current','pids.max','pids.events','memory.current','memory.max','memory.swap.max','memory.events','cpu.max','cpuset.cpus.effective')
    return {'time':time.time(),'monotonic':time.monotonic(),'leaf':{name:read(leaf/name) for name in names}}
trace=evidence/'runc.strace.raw';assert not trace.exists()
create=[runc,'--root',str(runtime),'--debug','--log',str(evidence/'runc.debug.json'),'--log-format','json','create','--bundle',str(bundle),identifier]
argv=[strace,'-f','-ttt','-s','160','-o',str(trace),'-e','trace=execve,fork,vfork,clone,clone3,mmap,mprotect,munmap,openat,read,write,prlimit64,exit,exit_group',*create]
ready={'unit':unit,'properties':props,'unit_cgroup':str(group),'parent':identity(os.getpid()),'binary_sha256':binary_sha,'oci_sha256':sha(config),'oci':spec,'argv':argv,'create_argv':create,
       'script_sha256':sha(__file__),'selected_parent_env':{k:os.environ.get(k) for k in ('GOMAXPROCS','GODEBUG','LD_PRELOAD','LD_LIBRARY_PATH')},
       'root_procs_empty':not group.joinpath('cgroup.procs').read_text().strip(),'scope':'instrumented CPU createContainer stage control only; no app start/model/Store/GPU; original full profile and limits unknown'}
assert ready['root_procs_empty'] and ready['parent']['cgroup']=='0::'+relative+'/supervisor'
publish('ready.json',ready,True)
first=snapshot();publish('first.json',first,True)
last_event=None
with (evidence/'events.jsonl').open('x') as events,(evidence/'runc.stdout').open('xb') as stdout,(evidence/'runc.stderr').open('xb') as stderr:
    process=subprocess.Popen(argv,stdout=stdout,stderr=stderr)
    while True:
        sample=snapshot()
        selected={k:v for k,v in sample['leaf'].items() if k not in ('pids.current','memory.current')}
        if selected!=last_event:
            events.write(json.dumps(sample)+'\n');events.flush();last_event=selected
        publish('last.json',{'sample':sample,'owned_processes':observed})
        if process.poll() is not None:break
        time.sleep(0.02)
    returncode=process.wait()
publish('create-returned.json',{'returncode':returncode,'last':sample,'owned_processes':observed},True)
error=(evidence/'runc.stderr').read_text(errors='replace')
later_stage=returncode!=0 and '/__cpu_probe_no_app__' in error and 'no such file or directory' in error and 'error running createContainer' not in error
delete=[runc,'--root',str(runtime),'delete','--force',identifier]
with (evidence/'delete.stdout').open('xb') as out,(evidence/'delete.stderr').open('xb') as err:
    cleanup=subprocess.run(delete,stdout=out,stderr=err)
assert all(sha(path)==digest for path,digest in binary_sha.items())
report={'status':'CPU_INSTRUMENTED_POST_HOOK_MISSING_APP' if later_stage else 'UNKNOWN_STAGE_REVIEW_REQUIRED',
        'create_returncode':returncode,'delete_argv':delete,'delete_returncode':cleanup.returncode,'final_sample':snapshot(),'owned_processes':observed,
        'hook_success_inferred_from_later_stage':later_stage,'raw_trace':str(trace),'hook_stdout_not_returned_by_runc_on_success':True,
        'scope':'CPU instrumented stage control; trace and leaf events need review; no original GPU cause/PID-limit attribution or retry authorization',
        'external_release_observation_required':True}
publish('report.json',report,True)
print(json.dumps({'status':report['status'],'unit':unit,'evidence':str(evidence)}),flush=True)
