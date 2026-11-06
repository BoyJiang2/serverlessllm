from pathlib import Path
import json,urllib.request,os,datetime
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');rows=json.loads((base/'python-download-options.json').read_bytes());row=next(r for r in rows if r['key']=='cpython-3.12.3-linux-x86_64-gnu')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:17891','http':'http://127.0.0.1:17891'}));url='https://raw.githubusercontent.com/astral-sh/uv/0.11.28/crates/uv-python/download-metadata.json'
try:
 with opener.open(url,timeout=30) as r:data=r.read(16*1024*1024);final=r.url
 (base/'uv-python-download-metadata.json').write_bytes(data)
 report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),url=url,final_url=final,metadata=json.loads(data).get('cpython-3.12.3-linux-x86_64-gnu'))
except Exception as exc:report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),url=url,error_type=type(exc).__name__,error=str(exc))
(base/'uv-python-metadata-fetch.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
