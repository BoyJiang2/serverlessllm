from pathlib import Path
import subprocess,json,os,sys,datetime,glob,zipfile,hashlib
BASE=Path('/logs');source=Path('/opt/src/sllm/sllm_store/build/lib.linux-x86_64-cpython-312/sllm_store');backup=source.with_name('sllm_store.p4a-hidden')
def check():
 import torch,importlib.metadata as md
 from sllm_store import _C,_checkpoint_store
 assert not glob.glob('/dev/nvidia*') and not torch.cuda.is_available()
 paths=sorted({l.split()[-1] for l in Path('/proc/self/maps').read_text().splitlines() if '.so' in l and '/' in l.split()[-1]})
 glogs=[p for p in paths if '/libglog.so.' in p];assert len(glogs)==1 and glogs[0].startswith('/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/')
 assert not any('/opt/src/' in p for p in paths)
 wheel=json.loads((BASE/'build-store-a4-result.json').read_bytes())['wheels'][0]
 with zipfile.ZipFile(wheel['file']) as z:
  entries=z.namelist();assert 'sllm_store/libglog.so.1' in entries
  bundled_sha=hashlib.sha256(z.read('sllm_store/libglog.so.1')).hexdigest()
 assert hashlib.sha256(Path(glogs[0]).read_bytes()).hexdigest()==bundled_sha
 elf=subprocess.check_output(['readelf','-d',_checkpoint_store.__file__],text=True)
 assert '$ORIGIN' in elf
 report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),installed=md.version('serverless-llm-store'),build_directory_invisible=True,library_path=glogs[0],bundled_soname_sha256=bundled_sha,native_imports={m.__name__:m.__file__ for m in (_C,_checkpoint_store)},elf_dynamic=elf,scope='Fresh-process actual wheel import with build library hidden; no GPU/model or full Store restore')
 (BASE/'store-closure-positive.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':
 os.umask(0o002)
 if '--child' in sys.argv:check()
 else:
  assert source.is_dir() and not backup.exists();source.rename(backup)
  try:
   with (BASE/'store-closure-positive.log').open('x') as log:r=subprocess.run(['/opt/sllm-v4/bin/python',__file__,'--child'],cwd='/tmp',stdout=log,stderr=subprocess.STDOUT)
  finally:backup.rename(source)
  assert r.returncode==0,(BASE/'store-closure-positive.log').read_text()
  print('Standalone Store wheel closure passed; build directory restored')
