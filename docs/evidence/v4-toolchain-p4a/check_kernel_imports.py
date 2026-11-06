from pathlib import Path
import subprocess,json,datetime,os,glob,sys
base=Path('/logs');os.umask(0o002);assert not glob.glob('/dev/nvidia*')
rows=[]
for name in ['flashinfer','flashinfer_cubin','tilelang','tvm_ffi','compressed_tensors']:
 code="import importlib,glob,json;assert not glob.glob('/dev/nvidia*');m=importlib.import_module("+repr(name)+");print(json.dumps(dict(module="+repr(name)+",path=m.__file__)))"
 try:
  with (base/('kernel-import-'+name+'.log')).open('x') as log:r=subprocess.run(['/opt/sllm-v4/bin/python','-c',code],cwd='/tmp',stdout=log,stderr=subprocess.STDOUT,timeout=120,env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false'))
  rows.append(dict(module=name,exit_code=r.returncode,log='kernel-import-'+name+'.log'))
 except subprocess.TimeoutExpired:rows.append(dict(module=name,exit_code=None,timeout_seconds=120))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),results=rows,scope='Actual target auxiliary native/Python imports without GPU devices or model construction; no kernel execution')
(base/'kernel-package-imports.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if all(r['exit_code']==0 for r in rows) else 1)
