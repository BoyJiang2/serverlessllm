import pathlib,subprocess,json,datetime,os,socket
root=pathlib.Path('/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57')
assert socket.gethostname()=='qjhs3'
assert pathlib.Path('/etc/machine-id').read_text().strip()=='000c69369afa00000001fc4f0d971280'
def call(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=12)
 return dict(argv=args,exitcode=p.returncode,stdout=p.stdout,stderr=p.stderr[:1000])
data=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),ssh_alias='qjhs003',hostname=socket.gethostname(),uid=os.getuid(),groups=call(['id']),machine_id=pathlib.Path('/etc/machine-id').read_text().strip(),boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),meminfo=pathlib.Path('/proc/meminfo').read_text(),numa={p.parent.name:p.read_text() for p in pathlib.Path('/sys/devices/system/node').glob('node[0-9]*/meminfo')})
data['commands']=[call(args) for args in [
 ['nvidia-smi','--query-gpu=index,uuid,memory.total,memory.used','--format=csv,noheader'],
 ['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],
 ['docker','ps','--format','{{.ID}} {{.Names}} {{.Image}} {{.Status}}'],
 ['findmnt','-J','-T',str(root)],['df','-B1',str(root)],
 ['stat','-c','%n %u %g %a','/data/brick1/boyjiang','/data/brick1'],
 ['ss','-H','-lntup'],['rdma','link','show']]]
runs=['sllm-glm51fp8-20261024T051606Z-deb76e57','sllm-glm51fp8-20261025T145409Z-0b3a25a1']; data['owned']=[]
for run in runs:
 row=dict(run_id=run,running=call(['docker','ps','--filter','label=sllm.run_id='+run,'--format','{{.ID}} {{.Names}}']))
 info=call(['systemctl','show',run+'.slice','-p','ControlGroup','-p','ActiveState']); row['slice']=info
 group=next((line.split('=',1)[1] for line in info['stdout'].splitlines() if line.startswith('ControlGroup=')),'')
 if group:
  assert pathlib.PurePosixPath(group).name==run+'.slice'
  cgroup=pathlib.Path('/sys/fs/cgroup')/group.lstrip('/')
  row['cgroup']={name:(cgroup/name).read_text() if (cgroup/name).exists() else None for name in ('pids.current','memory.current','memory.stat','memory.events','cpu.stat')}
 version='v6' if run==runs[0] else 'v13'
 row['timer']=call(['systemctl','show',run+'-deadline-'+version+'.timer','-p','ActiveState','-p','LoadState','-p','NextElapseUSecRealtime'])
 data['owned'].append(row)
files=list((root/'checkpoint').glob('model-*.safetensors'))
store=[]
for rank in range(8):store+=list((root/'store/vllm/glm51-fp8'/f'rank_{rank}').glob('tensor.data_*'))
assert len(files)==142 and len(store)<=256
seen=set(); assets=[]
for kind,paths in [('official_safetensors',files),('store_payload',store)]:
 logical=allocated=unique_allocated=0
 for path in paths:
  s=path.stat();logical+=s.st_size;allocated+=s.st_blocks*512
  key=(s.st_dev,s.st_ino)
  if key not in seen:seen.add(key);unique_allocated+=s.st_blocks*512
 assets.append(dict(kind=kind,files=len(paths),logical_bytes=logical,allocated_bytes=allocated,unique_allocated_bytes=unique_allocated))
data['assets']=assets
data['scope']='readonly bounded metadata queries/file stat; no model reads, pool/cache operations, RDMA registration, process changes, reservations, locks or permission changes; ports/other running-container names are observations without ownership inference'
print(json.dumps(data))
