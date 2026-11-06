"""Build the committed Store wheel in the no-device P4A container."""
from pathlib import Path
import os,sys,json,hashlib,subprocess,time,glob,datetime,signal
BASE=Path('/logs');WORK=Path('/data/brick1/boyjiang/sllm-v4-pro-build');SOURCE=Path('/opt/src/sllm/sllm_store')
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(name,data):
 with (BASE/name).open('x') as f:f.write(json.dumps(data,indent=2)+'\n')
def ident(pid):return dict(pid=pid,start_ticks=int(Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]))
def main():
 os.umask(0o002);child=None;start=time.monotonic()
 try:
  assert json.loads((BASE/'install-external-02-result.json').read_bytes())['external_sync_succeeded']
  assert not glob.glob('/dev/nvidia*') and os.environ['SLLM_BUILD_PROFILE']=='v4-pro'
  import torch
  assert torch.__version__.split('+')[0]=='2.11.0' and torch.version.cuda=='13.0' and not torch.cuda.is_available()
  out=WORK/'native-wheels/store-a4';out.mkdir(parents=True,exist_ok=False)
  cmd=['/opt/sllm-v4/bin/python','-m','build','--wheel','--no-isolation','--outdir',str(out),str(SOURCE)]
  with (BASE/'build-store-a4.log').open('x') as log:
   child=subprocess.Popen(cmd,cwd=SOURCE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,HTTPS_PROXY="http://127.0.0.1:17891",HTTP_PROXY="http://127.0.0.1:17891",GIT_TERMINAL_PROMPT="0"))
   write('build-store-a4-handle.json',dict(started_utc=utc(),supervisor=ident(os.getpid()),builder=ident(child.pid),command=cmd,python_executable=sys.executable,torch=torch.__version__,cuda=torch.version.cuda,cxx11_abi=torch._C._GLIBCXX_USE_CXX11_ABI,source_snapshot=json.loads((BASE/'closure-source-snapshot.json').read_bytes()),gpu_nodes=[],scope='Actual native Store compilation only; no model/GPU execution'))
   try:code=child.wait(timeout=3600)
   except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGTERM)
    try:child.wait(timeout=30)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    raise TimeoutError('Store source build exceeded one-hour bound')
  wheels=[dict(file=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.glob('*.whl')]
  write('build-store-a4-result.json',dict(completed_utc=utc(),exit_code=code,elapsed_seconds=time.monotonic()-start,wheels=wheels,native_store_built=code==0 and len(wheels)==1,abi_import_validated=False,gpu_execution=False,model_execution=False))
  return 0 if code==0 and len(wheels)==1 else 1
 except Exception as exc:
  if child is not None and child.poll() is None:child.terminate();child.wait(timeout=30)
  write('build-store-a4-failure.json',dict(utc=utc(),error_type=type(exc).__name__,error=str(exc),native_store_built=False));return 1
if __name__=='__main__':sys.exit(main())
