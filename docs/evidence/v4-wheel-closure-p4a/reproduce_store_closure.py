from pathlib import Path
import subprocess,json,os,sys,datetime
BASE=Path('/logs');source=Path('/opt/src/sllm/sllm_store/build/lib.linux-x86_64-cpython-312/sllm_store');backup=source.with_name('sllm_store.p4a-hidden')
assert source.resolve().is_relative_to(Path('/opt/src/sllm/sllm_store/build')) and source.is_dir() and not backup.exists()
try:
 source.rename(backup)
 with (BASE/'store-closure-negative.log').open('x') as log:r=subprocess.run(['/opt/sllm-v4/bin/python','-c','import torch; from sllm_store import _checkpoint_store'],cwd='/tmp',stdout=log,stderr=subprocess.STDOUT)
finally:backup.rename(source)
text=(BASE/'store-closure-negative.log').read_text();assert r.returncode!=0 and 'libglog.so.1' in text
(BASE/'store-closure-negative.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),returncode=r.returncode,build_lib_directory_hidden=True,build_lib_directory_restored=True,missing_dependency='libglog.so.1',scope='Real installed wheel fails without build directory; no GPU/model execution'))+'\n');print(text)
