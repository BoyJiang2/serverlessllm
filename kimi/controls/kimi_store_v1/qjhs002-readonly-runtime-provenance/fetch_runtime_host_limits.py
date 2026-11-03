import json
import pathlib
import subprocess

code = r'''
import datetime, hashlib, json, os, pathlib, struct, subprocess
def read(path):
    try: return pathlib.Path(path).read_text()
    except Exception as e: return {'unavailable': str(e)}
def command(args):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=8)
        return {'rc':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    except Exception as e: return {'unavailable':str(e)}
def uvar(data,i):
    v=s=0
    while True:
        b=data[i]; i+=1; v|=(b&127)<<s
        if b<128:return v,i
        s+=7
def gostr(data,i):
    n,i=uvar(data,i);return data[i:i+n],i+n
binary=pathlib.Path('/usr/bin/nvidia-cdi-hook')
b=binary.read_bytes(); at=b.find(b'\xff Go buildinf:')
build={'sha256':hashlib.sha256(b).hexdigest(),'resolved_path':str(binary.resolve()),'offset':at}
if at>=0:
    build['pointer_size']=b[at+14]; build['flags']=b[at+15]
    if b[at+15]&2:
        version,i=gostr(b,at+32); module,i=gostr(b,i)
        build['go_version']=version.decode(errors='replace')
        lines=module[16:-16].decode(errors='replace').splitlines()
        build['selected_module_lines']=[s for s in lines if s.startswith('path\t') or s.startswith('mod\t') or (s.startswith('build\t') and any(k in s for k in ('CGO_ENABLED=','GOARCH=','GOOS=','-ldflags=','-compiler=')))]
out={'captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'current host read-only snapshot; not failure-time proof','hook_buildinfo':build,'processes':{},'cgroups':{},'units':{},'kernel':{}}
fields={'Name','State','Pid','PPid','Uid','Gid','Threads','VmPeak','VmSize','VmRSS','CapEff','Seccomp','Cpus_allowed_list','Mems_allowed_list'}
for p in pathlib.Path('/proc').iterdir():
    if not p.name.isdigit():continue
    comm=read(p/'comm')
    if not isinstance(comm,str) or comm.strip() not in ('dockerd','containerd'):continue
    status=read(p/'status'); selected={}
    if isinstance(status,str):
        selected={k:v.strip() for s in status.splitlines() if ':' in s for k,v in [s.split(':',1)] if k in fields}
    cg=read(p/'cgroup')
    out['processes'][p.name]={'comm':comm.strip(),'status':selected,'limits':read(p/'limits'),'cgroup':cg}
    if isinstance(cg,str):
        for line in cg.splitlines():
            if not line.startswith('0::'):continue
            q=pathlib.Path('/sys/fs/cgroup')/line[3:].lstrip('/')
            while str(q).startswith('/sys/fs/cgroup'):
                out['cgroups'][str(q)]={n:read(q/n) for n in ('pids.current','pids.max','pids.peak','pids.events','pids.events.local','memory.current','memory.max','memory.events','cpu.max','cpuset.cpus.effective')}
                if str(q)=='/sys/fs/cgroup':break
                q=q.parent
for name in ('docker.service','containerd.service'):
    out['units'][name]=command(['systemctl','show',name,'--no-pager','--property=Id,MainPID,ControlGroup,TasksCurrent,TasksMax,LimitNPROC,LimitNPROCSoft,LimitSTACK,LimitSTACKSoft,LimitAS,LimitASSoft,LimitDATA,LimitDATASoft'])
for n in ('threads-max','pid_max'):
    out['kernel'][n]=read('/proc/sys/kernel/'+n)
out['kernel']['uname']=command(['uname','-r'])
out['kernel']['meminfo_selected']='\n'.join(s for s in read('/proc/meminfo').splitlines() if s.startswith(('MemAvailable:','CommitLimit:','Committed_AS:','SwapFree:')))
out['glibc_package']=command(['dpkg-query','-W','-f=${Package} ${Version}\n','libc6'])
print(json.dumps(out,indent=2))
'''
r = subprocess.run(['C:/Windows/System32/OpenSSH/ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','qjhs002','python3 -'],input=code.encode(),capture_output=True,timeout=60)
if r.returncode:
    raise SystemExit(r.stderr.decode(errors='replace'))
data=json.loads(r.stdout)
pathlib.Path('work/runtime-host-limits.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print(json.dumps({'saved':'work/runtime-host-limits.json','bytes':len(r.stdout),'hook_go':data['hook_buildinfo'].get('go_version'),'process_ids':list(data['processes']),'cgroup_count':len(data['cgroups'])}))
