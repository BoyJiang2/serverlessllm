from pathlib import Path
import os,subprocess,json,datetime,hashlib
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');cid='ac3a81f1426c8697120ddcadca8f3688654d0aa7370acba9f86cee6b924f0d8c'
def docker(*args):return subprocess.check_output(['docker',*args],text=True)
inspection=json.loads(docker('inspect',cid))[0];h=inspection['HostConfig'];assert h['Runtime']=='runc' and not h['Devices'] and not h['DeviceRequests'] and h['NanoCpus']==4000000000 and h['Memory']==48*1024**3
assert all('/mnt/raid/models' not in m['Source'] and '/dev/' not in m['Source'] for m in inspection['Mounts'])
code="import pathlib,json;root=pathlib.Path('/sys/fs/cgroup');names=['memory.max','memory.current','memory.events','cpu.max','pids.max','memory.peak'];print(json.dumps({n:(root/n).read_text() if (root/n).exists() else None for n in names}))"
cgroup=json.loads(docker('exec',cid,'/opt/sllm-v4/bin/python','-c',code));assert 'oom_kill 0' in cgroup['memory.events']
mounts=inspection['Mounts'];space={}
for p in ['/data/brick0','/data/brick1/boyjiang','/home/boyjiang']:
 s=os.statvfs(p);space[p]=dict(available_bytes=s.f_bavail*s.f_frsize,total_bytes=s.f_blocks*s.f_frsize)
assert space['/data/brick0']['available_bytes']>=128*1024**3
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),container_id=cid,image_id=inspection['Image'],host_config={k:h[k] for k in ['Runtime','Devices','DeviceRequests','NanoCpus','Memory','MemorySwap','PidsLimit','CapDrop','SecurityOpt','NetworkMode']},mounts=mounts,cgroup=cgroup,filesystem_space=space,scope='Live no-device resource-bound audit; cumulative cgroup counters since container creation, no GPU/model admission')
(base/'p4a-live-resource-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
