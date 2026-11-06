"""Build fixed vLLM source in the bounded no-device P4A container."""
from pathlib import Path
import os,sys,json,hashlib,subprocess,time,glob,datetime,signal
BASE=Path('/logs');WORK=Path('/data/brick1/boyjiang/sllm-v4-pro-build');SOURCE=Path('/opt/src/vllm')
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(name,data):
 with (BASE/name).open('x') as f:f.write(json.dumps(data,indent=2)+'\n')
def ident(pid):return dict(pid=pid,start_ticks=int(Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]))
def main():
 os.umask(0o002);child=None;start=time.monotonic()
 try:
  assert json.loads((BASE/'install-external-02-result.json').read_bytes())['external_sync_succeeded']
  assert not glob.glob('/dev/nvidia*')
  assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()=='132765e3560659ff63ebd236203672e991b70e08'
  assert subprocess.check_output(['git','-C',str(SOURCE),'describe','--tags','--exact-match','HEAD'],text=True).strip()=='v0.20.1'
  import torch
  assert torch.__version__.split('+')[0]=='2.11.0' and torch.version.cuda=='13.0' and not torch.cuda.is_available()
  out=WORK/'native-wheels/vllm-a2';out.mkdir(parents=True,exist_ok=False)
  env=dict(os.environ,HTTPS_PROXY='http://127.0.0.1:17891',HTTP_PROXY='http://127.0.0.1:17891',GIT_TERMINAL_PROMPT='0',MAX_JOBS='4',NVCC_THREADS='1',TORCH_CUDA_ARCH_LIST='9.0',VLLM_TARGET_DEVICE='cuda',VLLM_USE_PRECOMPILED='0',CMAKE_BUILD_TYPE='Release')
  env.pop('VLLM_PRECOMPILED_WHEEL_LOCATION',None)
  cmd=['/opt/sllm-v4/bin/python','-m','build','--wheel','--no-isolation','--outdir',str(out),str(SOURCE)]
  with (BASE/'build-vllm-a2.log').open('x') as log:
   child=subprocess.Popen(cmd,cwd=SOURCE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
   write('build-vllm-a2-handle.json',dict(started_utc=utc(),supervisor=ident(os.getpid()),builder=ident(child.pid),command=cmd,python_executable=sys.executable,torch=torch.__version__,cuda=torch.version.cuda,cxx11_abi=torch._C._GLIBCXX_USE_CXX11_ABI,source_commit='132765e3560659ff63ebd236203672e991b70e08',flags={k:env[k] for k in ('MAX_JOBS','NVCC_THREADS','TORCH_CUDA_ARCH_LIST','VLLM_TARGET_DEVICE','VLLM_USE_PRECOMPILED','CMAKE_BUILD_TYPE')},gpu_nodes=[],scope='Actual fixed-source vLLM compilation; no model/GPU execution'))
   try:code=child.wait(timeout=28800)
   except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGTERM)
    try:child.wait(timeout=30)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    raise TimeoutError('vLLM source build exceeded eight-hour bound')
  wheels=[dict(file=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.glob('*.whl')]
  write('build-vllm-a2-result.json',dict(completed_utc=utc(),exit_code=code,elapsed_seconds=time.monotonic()-start,wheels=wheels,native_vllm_built=code==0 and len(wheels)==1,abi_import_validated=False,gpu_execution=False,model_execution=False))
  return 0 if code==0 and len(wheels)==1 else 1
 except Exception as exc:
  if child is not None and child.poll() is None:child.terminate();child.wait(timeout=30)
  write('build-vllm-a2-failure.json',dict(utc=utc(),error_type=type(exc).__name__,error=str(exc),native_vllm_built=False));return 1
if __name__=='__main__':sys.exit(main())
