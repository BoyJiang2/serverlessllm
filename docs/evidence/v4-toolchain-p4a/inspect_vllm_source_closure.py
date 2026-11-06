from pathlib import Path
import subprocess,json,hashlib,datetime,glob
base=Path('/logs');source=Path('/opt/src/vllm');rows=[]
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.DEVNULL).strip()
for directory in [source, *sorted((source/'.deps').glob('*-src'))]:
 if not directory.is_dir():continue
 try:
  head=run('git','-c','safe.directory='+str(directory),'-C',str(directory),'rev-parse','HEAD')
  refs=run('git','-c','safe.directory='+str(directory),'-C',str(directory),'remote','-v')
  status=run('git','-c','safe.directory='+str(directory),'-C',str(directory),'status','--porcelain')
  submodules=run('git','-c','safe.directory='+str(directory),'-C',str(directory),'submodule','status','--recursive')
  rows.append(dict(path=str(directory),head=head,remotes=refs,working_status=status,submodules=submodules))
 except subprocess.CalledProcessError as e:rows.append(dict(path=str(directory),git_query_error=e.returncode))
cmake=source/'build/temp.linux-x86_64-cpython-312/CMakeCache.txt'
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sources=rows,cmake_cache_sha256=hashlib.sha256(cmake.read_bytes()).hexdigest(),gpu_nodes=glob.glob('/dev/nvidia*'),scope='Read-only source closure snapshot during active compile; not terminal build qualification')
(base/'vllm-source-closure-active.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
