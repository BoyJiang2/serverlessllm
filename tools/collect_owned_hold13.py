from pathlib import Path
import subprocess,json,datetime
code=r'''import pathlib,subprocess,json,datetime,socket
assert socket.gethostname()=='qjhs3'
assert pathlib.Path('/etc/machine-id').read_text().strip()=='000c69369afa00000001fc4f0d971280'
runs=['sllm-glm51fp8-20261024T051606Z-deb76e57','sllm-glm51fp8-20261025T145409Z-0b3a25a1'];rows=[]
def call(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=15)
 return dict(exitcode=p.returncode,stdout=p.stdout,stderr=p.stderr[:1000])
for run in runs:
 row=dict(run_id=run,running=call(['docker','ps','--filter','label=sllm.run_id='+run,'--format','{{.ID}} {{.Names}}']),slice=call(['systemctl','show',run+'.slice','-p','ActiveState','-p','ControlGroup']))
 version='v6' if run==runs[0] else 'v13'
 row['deadline']=call(['systemctl','show',run+'-deadline-'+version+'.timer','-p','LoadState','-p','ActiveState','-p','NextElapseUSecRealtime'])
 row['cgroup']={}
 group_text=next((line.split('=',1)[1] for line in row['slice']['stdout'].splitlines() if line.startswith('ControlGroup=')), '')
 if group_text: assert pathlib.PurePosixPath(group_text).name==run+'.slice'
 group=pathlib.Path('/sys/fs/cgroup')/group_text.lstrip('/') if group_text else None
 for name in ['pids.current','memory.current','memory.events']:
  path=group/name if group else None; row['cgroup'][name]=path.read_text() if path and path.exists() else None
 rows.append(row)
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),ssh_alias='qjhs003',boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),scope='exact own identities only; read-only; no resource or guard mutation',rows=rows)))
'''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','qjhs003','python3','-'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=60,check=True)
data=json.loads(p.stdout)
assert all(row['running']['exitcode']==0 and not row['running']['stdout'].strip() for row in data['rows'])
(Path(__file__).resolve().parent.parent/'evidence/g0/resource-hold-owned-state-20261026-v2.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(data,indent=2))
