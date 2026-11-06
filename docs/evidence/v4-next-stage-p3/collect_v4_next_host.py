from pathlib import Path
import datetime,json,os,shutil,subprocess
root=Path('/home/boyjiang/sllm-v4-pro/evidence/next-stage-20261106');root.mkdir(exist_ok=True)
paths=['/data/brick0','/home/boyjiang','/data/brick1/boyjiang','/mnt/raid/models/DeepSeek-V4-Pro']
r=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),host=os.uname().nodename,
 driver_module_text=Path('/proc/driver/nvidia/version').read_text(),meminfo=Path('/proc/meminfo').read_text(),
 tools={name:shutil.which(name) for name in ('docker','nvcc','python3','cmake','ninja')},
 filesystems=[dict(path=p,available_bytes=(s:=os.statvfs(p)).f_bavail*s.f_frsize,total_bytes=s.f_blocks*s.f_frsize) for p in paths],
 docker_root=subprocess.check_output(['docker','info','--format','{{.DockerRootDir}}'],text=True).strip(),
 source_head=subprocess.check_output(['git','-C','/home/boyjiang/sllm-v4-pro/code','rev-parse','HEAD'],text=True).strip(),
 gpu_calls=False,package_installation=False,model_import=False,
 scope='Only procfs, statvfs, executable discovery, Docker metadata and Git; not GPU admission or reservation')
(root/'host-readonly-preflight.json').write_bytes((json.dumps(r,indent=2)+'\n').encode());print(json.dumps({k:v for k,v in r.items() if k!='meminfo'}))
