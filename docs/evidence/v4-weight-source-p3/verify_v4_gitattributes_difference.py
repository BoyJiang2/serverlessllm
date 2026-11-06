from pathlib import Path
import hashlib,json,requests
base=Path('work/v4-weight-provenance');api=json.loads((base/'official-model-info.json').read_bytes())
expected=next(r for r in api['siblings'] if r['rfilename']=='.gitattributes')
s=requests.Session();s.trust_env=True
url='https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/resolve/'+api['sha']+'/.gitattributes'
with s.get(url,stream=True,timeout=(15,30)) as r:
 r.raise_for_status();chunks=[];size=0
 for block in r.iter_content(4096):
  size+=len(block)
  if size>64*1024:raise ValueError('metadata response cap exceeded')
  chunks.append(block)
 body=b''.join(chunks)
assert len(body)==expected['size'] and hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()==expected['blobId']
(base/'official-gitattributes.txt').write_bytes(body)
local=(base/'local-gitattributes.txt').read_bytes()
current=json.loads((base/'current-local-metadata.json').read_bytes())
row=next(r for r in current['nonweight_files'] if r['file']=='.gitattributes')
assert hashlib.sha256(local).hexdigest()==row['sha256']
report=dict(file='.gitattributes',runtime_asset=False,official_bytes=len(body),local_bytes=len(local),
 official_sha256=hashlib.sha256(body).hexdigest(),local_sha256=row['sha256'],
 matches=False,reason_not_determined=True,model_directory_modified=False,url=url)
(base/'gitattributes-difference.json').write_bytes((json.dumps(report,indent=2)+'\n').encode())
print(json.dumps(report))
