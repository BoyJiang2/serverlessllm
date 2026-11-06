from pathlib import Path
import json,hashlib,subprocess,glob,os,sys,datetime,importlib.metadata as md
BASE=Path('/logs');WORK=Path('/data/brick1/boyjiang/sllm-v4-pro-build')
def main():
 os.umask(0o002);assert not glob.glob('/dev/nvidia*')
 rows=[]
 for result in ('build-store-a4-result.json','build-service-a2-result.json'):
  d=json.loads((BASE/result).read_bytes());assert d['exit_code']==0 and len(d['wheels'])==1
  row=d['wheels'][0];assert hashlib.sha256(Path(row['file']).read_bytes()).hexdigest()==row['sha256'];rows.append(row)
 req=BASE/'local-base-v2-wheels.txt';req.write_text(''.join(Path(r['file']).as_uri()+' --hash=sha256:'+r['sha256']+'\n' for r in rows))
 cmd=['/usr/local/bin/uv','--no-config','pip','install','--python','/opt/sllm-v4/bin/python','--require-hashes','--no-deps','--no-index','--cache-dir',str(WORK/'container-cache'),'-r',str(req)]
 with (BASE/'local-base-v2-install.log').open('x') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,UV_LINK_MODE='copy'))
 assert r.returncode==0
 import torch
 from sllm_store import _C,_checkpoint_store
 assert torch.__version__.split('+')[0]=='2.11.0' and not torch.cuda.is_available()
 modules={n:str(m.__file__) for n,m in (('_C',_C),('_checkpoint_store',_checkpoint_store))};assert all(p.startswith('/opt/sllm-v4/') for p in modules.values())
 installed={n:md.version(n) for n in ('serverless-llm','serverless-llm-store')};assert set(installed.values())=={'0.8.0+v4pro.2'}
 maps=Path('/proc/self/maps').read_text().splitlines();paths=sorted({line.split()[-1] for line in maps if '.so' in line and '/' in line.split()[-1]})
 report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),wheel_inputs=rows,installed=installed,native_imports=modules,loaded_shared_objects=paths,torch=torch.__version__,cuda=torch.version.cuda,cxx11_abi=torch._C._GLIBCXX_USE_CXX11_ABI,gpu_nodes=[],gpu_available=False,scope='Actual Store extensions imported with target Torch; source vLLM not yet installed, no codec/full model/GPU qualification')
 (BASE/'store-native-import-v2.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('loaded_shared_objects','wheel_inputs')}));return 0
if __name__=='__main__':sys.exit(main())
