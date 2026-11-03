"""One-use TERM-resistant cgroup stimulus; systemd, not this script, enforces time."""
import json,os,pathlib,re,signal,subprocess,sys,time
unit=sys.argv[1]
evidence=pathlib.Path(sys.argv[2])
assert os.geteuid()==0 and re.fullmatch(r'kimi-os-guard-cpu-[a-z0-9-]+\.service',unit)
assert evidence.resolve().is_relative_to(pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0'))
relative='/system.slice/'+unit
assert pathlib.Path('/proc/self/cgroup').read_text().strip()=='0::'+relative
group=pathlib.Path('/sys/fs/cgroup'+relative)
assert group.joinpath('cgroup.type').read_text().strip()=='domain'
assert {'cpu','memory','pids'}<=set(group.joinpath('cgroup.controllers').read_text().split())
supervisor=group/'supervisor';container=group/'container'
supervisor.mkdir();container.mkdir()
supervisor.joinpath('cgroup.procs').write_text(str(os.getpid()))
assert not group.joinpath('cgroup.procs').read_text().strip()
group.joinpath('cgroup.subtree_control').write_text('+cpu +memory +pids')
assert {'cpu','memory','pids'}<=set(group.joinpath('cgroup.subtree_control').read_text().split())
assert supervisor.joinpath('cgroup.type').read_text().strip()=='domain'
assert container.joinpath('cgroup.type').read_text().strip()=='domain'
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child_source='''import json,os,pathlib,signal,subprocess,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
pathlib.Path(sys.argv[1]).joinpath('cgroup.procs').write_text(str(os.getpid()))
grandchild=subprocess.Popen(['/usr/bin/sleep','600'])
final=pathlib.Path(sys.argv[2]);temp=final.with_suffix('.tmp')
with temp.open('x') as output:json.dump({'child':os.getpid(),'grandchild':grandchild.pid},output)
os.link(temp,final);temp.unlink()
while True:time.sleep(1)
'''
child_record=evidence/'children.json'
child=subprocess.Popen(['/usr/bin/python3','-I','-c',child_source,str(container),str(child_record)])
ready_deadline=time.monotonic()+3
while not child_record.exists() and time.monotonic()<ready_deadline:time.sleep(0.05)
assert child_record.exists()
pids={'supervisor':os.getpid(),**json.loads(child_record.read_text())}
assert pids['child']==child.pid
rows={}
for role,pid in pids.items():
    proc=pathlib.Path('/proc')/str(pid)
    fields=proc.joinpath('stat').read_text().split()
    status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in proc.joinpath('status').read_text().splitlines() if ':' in line}
    cgroup=proc.joinpath('cgroup').read_text().strip()
    assert cgroup.startswith('0::'+relative+'/')
    assert int(status['SigIgn'],16)&(1<<(signal.SIGTERM-1))
    rows[role]={'pid':pid,'startticks':int(fields[21]),'ppid':int(fields[3]),'cgroup':cgroup,'sigign':status['SigIgn']}
assert rows['child']['ppid']==os.getpid() and rows['grandchild']['ppid']==child.pid
receipt={'unit':unit,'ready_at':time.time(),'unit_cgroup':str(group),'root_procs_empty':True,'subtree_control':group.joinpath('cgroup.subtree_control').read_text().strip(),'processes':rows,'scope':'three TERM-resistant CPU-only processes in two delegated descendant cgroups; no GPU/runtime hook/model/Store','expected_termination':'native systemd RuntimeMaxSec followed by FinalKillSignal=SIGKILL'}
temp=evidence/'ready.tmp'
with temp.open('x') as output:json.dump(receipt,output,indent=2)
os.link(temp,evidence/'ready.json');temp.unlink()
print(json.dumps(receipt),flush=True)
while True:time.sleep(1)
