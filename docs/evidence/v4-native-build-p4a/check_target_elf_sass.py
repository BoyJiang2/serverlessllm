"""Read-only ELF/SASS audit of installed target extensions, without CUDA execution."""
from pathlib import Path
import importlib.metadata as md,json,hashlib,subprocess,os,re,datetime,glob
os.umask(0o002);assert not glob.glob('/dev/nvidia*');base=Path('/logs');rows=[]
root=Path('/opt/sllm-v4/lib/python3.12/site-packages');torchlib=root/'torch/lib';compat=Path('/usr/local/cuda/compat')
loader_paths=[str(torchlib),'/usr/local/cuda/lib64',str(compat)]
env=dict(os.environ,LD_LIBRARY_PATH=':'.join(loader_paths))
for package in ['vllm','sllm_store']:
 for p in sorted((root/package).rglob('*.so*')):
  if not p.is_file():continue
  dynamic=subprocess.check_output(['readelf','-d',str(p)],text=True)
  needed=re.findall(r'\(NEEDED\).*?\[(.*?)\]',dynamic)
  paths=re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]',dynamic)
  ldd=subprocess.run(['ldd',str(p)],env=env,capture_output=True,text=True)
  missing=[line.strip() for line in ldd.stdout.splitlines() if 'not found' in line]
  cmd=['/usr/local/cuda/bin/cuobjdump','--list-elf',str(p)];cubin=subprocess.run(cmd,capture_output=True,text=True)
  arches=sorted(set(re.findall(r'sm_[0-9]+[af]?',cubin.stdout)))
  rows.append(dict(path=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),needed=needed,rpath=paths,ldd_exit=ldd.returncode,unresolved=missing,ldd=ldd.stdout,cuobjdump_exit=cubin.returncode,cuobjdump_arches=arches,cuobjdump_output=cubin.stdout,cuobjdump_stderr=cubin.stderr))
assert rows and any(r['cuobjdump_arches'] and any(a.startswith('sm_90') for a in r['cuobjdump_arches']) for r in rows if '/vllm/' in r['path'])
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=rows,ldd_library_paths=loader_paths,compat_library_realpath=str((compat/'libcuda.so.1').resolve()),scope='Actual ELF dependencies and embedded SASS identities; explicit real toolkit compat driver for static resolution, no stubs, kernel execution or GPU/model proof')
(base/'target-elf-sass-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(files=len(rows),unresolved=[dict(path=r['path'],missing=r['unresolved']) for r in rows if r['unresolved']],architectures=sorted({a for r in rows for a in r['cuobjdump_arches']}))))
