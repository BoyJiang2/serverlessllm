import concurrent.futures,hashlib,json,pathlib,signal,time
from modelscope.hub.api import HubApi
from modelscope.hub.file_download import model_file_download
signal.alarm(900)
mid='LLM-Research/Meta-Llama-3.1-8B-Instruct'
root=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM/models/Llama-3.1-8B-Instruct')
receipt=root/'download-receipt.json';started=time.time()
files=[f for f in HubApi().get_model_files(mid,recursive=True) if f['Type']=='blob' and '/' not in f['Path']]
assert len([f for f in files if f['Path'].endswith('.safetensors')])==4
record={'model':mid,'transport':'ModelScope SDK; master transport; per-file source revision recorded and SHA256 content pinned','started_epoch':started,'manifest':files,'status':'downloading'}
receipt.write_text(json.dumps(record,indent=2))
def download(f):
 path=root/f['Path']
 if path.exists():assert path.stat().st_size==f['Size'],'existing partial target requires inspection'
 else:model_file_download(mid,f['Path'],revision='master',local_dir=str(root))
 h=hashlib.sha256()
 with path.open('rb') as stream:
  while block:=stream.read(8*1024**2):h.update(block)
 assert path.stat().st_size==f['Size'] and h.hexdigest()==f['Sha256'],f['Path']
 print(json.dumps({'verified':f['Path'],'bytes':f['Size'],'sha256':h.hexdigest()}),flush=True)
 return {'path':f['Path'],'bytes':f['Size'],'sha256':h.hexdigest(),'revision':f['Revision']}
try:
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:record['files']=list(pool.map(download,files))
 record.update(status='all_files_hash_verified',elapsed_seconds=time.time()-started,total_bytes=sum(f['Size'] for f in files));receipt.write_text(json.dumps(record,indent=2));print(json.dumps({'complete':True,'elapsed_seconds':record['elapsed_seconds'],'bytes':record['total_bytes']}),flush=True)
except BaseException as error:
 record.update(status='failed',error=type(error).__name__+': '+str(error),elapsed_seconds=time.time()-started);receipt.write_text(json.dumps(record,indent=2));raise
