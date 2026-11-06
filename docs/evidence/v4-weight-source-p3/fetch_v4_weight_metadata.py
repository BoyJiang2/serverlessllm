from pathlib import Path
import datetime,hashlib,json,requests
url='https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Pro/revision/b5968e9190ef611bbf34a7229255be88a0e937c1?blobs=true'
s=requests.Session();s.trust_env=True
out=Path('work/v4-weight-provenance');out.mkdir(exist_ok=True)
with s.get(url,stream=True,timeout=(15,60)) as r:
 r.raise_for_status();chunks=[];size=0
 for b in r.iter_content(64*1024):
  size+=len(b)
  if size>5*1024*1024:raise ValueError('metadata response exceeds 5MiB cap')
  chunks.append(b)
 data=b''.join(chunks)
meta=json.loads(data);assert meta['sha']=='b5968e9190ef611bbf34a7229255be88a0e937c1'
assert meta['id']=='deepseek-ai/DeepSeek-V4-Pro'
(out/'official-model-info.json').write_bytes(data)
receipt=dict(url=url,accessed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),revision=meta['sha'],scope='Official pinned model metadata only; no weight content downloaded')
(out/'official-model-info-receipt.json').write_bytes((json.dumps(receipt,indent=2)+'\n').encode())
print(json.dumps(dict(revision=meta['sha'],bytes=len(data),siblings=len(meta['siblings']))))
