from pathlib import Path
import importlib,importlib.metadata as md,glob,datetime,json,torch,os
os.umask(0o002);assert not glob.glob('/dev/nvidia*') and not torch.cuda.is_available()
names=['sllm_store.torch','sllm_store.client','sllm_store.utils','sllm_store.server','sllm_store.cli']
results=[]
for n in names:
 try:
  m=importlib.import_module(n);results.append(dict(module=n,path=m.__file__,passed=True))
 except Exception as e:results.append(dict(module=n,passed=False,error_type=type(e).__name__,error=str(e)))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),results=results,scope='Actual installed Python component imports only, no daemon/client method, model construction or GPU execution')
Path('/logs/store-python-imports.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));assert all(r['passed'] for r in results)
