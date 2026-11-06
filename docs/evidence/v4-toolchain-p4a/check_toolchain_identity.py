from pathlib import Path
import json,datetime,hashlib,subprocess,sys,sysconfig,shutil,glob,importlib.metadata as md,os
base=Path('/logs');os.umask(0o002);assert not glob.glob('/dev/nvidia*')
files=[Path('/opt/python/bin/python3.12'),Path('/opt/python/lib/libpython3.12.so.1.0'),Path('/usr/local/bin/uv'),Path('/usr/local/cuda/bin/nvcc'),Path('/usr/bin/git'),Path(shutil.which('g++')),Path(shutil.which('ninja')),Path(md.distribution('cmake').locate_file('cmake/data/bin/cmake'))]
rows=[dict(path=str(p),realpath=str(p.resolve()),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]
commands=[['/usr/local/cuda/bin/nvcc','--version'],['g++','--version'],['git','--version'],['cmake','--version'],['ninja','--version'],['/usr/local/bin/uv','--version'],['ldd','--version']]
versions=[dict(command=cmd,output=subprocess.check_output(cmd,text=True,stderr=subprocess.STDOUT)) for cmd in commands]
packages=subprocess.check_output(['dpkg-query','-W','-f=${Package}\t${Version}\n'],text=True)
(base/'final-os-package-inventory.txt').write_text(packages)
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),python=sys.version,base_prefix=sys.base_prefix,prefix=sys.prefix,soabi=sysconfig.get_config_var('SOABI'),binary_inputs=rows,tool_versions=versions,os_packages_sha256=hashlib.sha256(packages.encode()).hexdigest(),build_flags={k:os.environ.get(k) for k in ['SLLM_BUILD_PROFILE','CUDA_HOME','TORCH_CUDA_ARCH_LIST','MAX_JOBS','NVCC_THREADS','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','NVIDIA_VISIBLE_DEVICES']},scope='Actual private container toolchain and OS identities, no GPU/model execution')
(base/'p4a-toolchain-identity.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('binary_inputs','tool_versions')}))
