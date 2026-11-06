from pathlib import Path
import subprocess,sys,os,json,datetime,glob,importlib
base=Path('/logs');os.umask(0o002);assert not glob.glob('/dev/nvidia*')
if '--child' in sys.argv:
 import torch
 results=[]
 for name in ['vllm._flashmla_C','vllm._flashmla_extension_C','vllm.third_party.deep_gemm._C']:
  try:
   m=importlib.import_module(name);assert m.__file__.startswith('/opt/sllm-v4/');results.append(dict(module=name,path=m.__file__,passed=True))
  except Exception as e:results.append(dict(module=name,passed=False,error_type=type(e).__name__,error=str(e)))
 assert not torch.cuda.is_initialized()
 maps=sorted({l.split()[-1] for l in Path('/proc/self/maps').read_text().splitlines() if '.so' in l and '/' in l.split()[-1]});assert not any('/opt/src/' in p or '/stubs/' in p for p in maps)
 report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),imports=results,loaded_shared_objects=maps,cuda_initialized=False,gpu_nodes=[],scope='Only three previously unloaded built native extensions, canonical imports, no model or GPU operations')
 (base/'extra-native-imports.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='loaded_shared_objects'}));sys.exit(0 if all(r['passed'] for r in results) else 1)
hidden=[];home=Path('/tmp/p4a-extra-native-home');home.mkdir(exist_ok=False)
try:
 for p in [Path('/opt/src/vllm'),Path('/opt/src/sllm')]:
  assert p.resolve()==p and p.parent==Path('/opt/src')
  b=p.with_name(p.name+'.p4a-hidden');assert not b.exists();p.rename(b);hidden.append((p,b))
 env=dict(os.environ,LD_LIBRARY_PATH='/usr/local/cuda/compat:'+os.environ['LD_LIBRARY_PATH'],HOME=str(home),XDG_CACHE_HOME=str(home/'cache'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',VLLM_PLUGINS='')
 with (base/'extra-native-imports.log').open('x') as log:r=subprocess.run(['/opt/sllm-v4/bin/python',__file__,'--child'],cwd='/tmp',env=env,stdout=log,stderr=subprocess.STDOUT)
finally:
 for p,b in reversed(hidden):b.rename(p)
(base/'extra-native-imports-result.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=r.returncode,both_source_trees_hidden=True,both_source_trees_restored=True))+'\n');print(r.returncode);sys.exit(r.returncode)
