from pathlib import Path
import json,urllib.request,os,datetime
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');rows=json.loads((base/'python-download-options.json').read_bytes());row=next(r for r in rows if r['key']=='cpython-3.12.3-linux-x86_64-gnu')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:17891','http':'http://127.0.0.1:17891'}));url='https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/20240415'
try:
 with opener.open(url,timeout=30) as r:data=r.read(8*1024*1024);final=r.url
 (base/'python-upstream-release.json').write_bytes(data)
 report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),url=url,final_url=final,assets=[{k:a.get(k) for k in ('name','browser_download_url','digest','size')} for a in json.loads(data)['assets'] if a['name']=='SHA256SUMS' or ('cpython-3.12.3' in a['name'] and 'x86_64-unknown-linux-gnu-install_only.tar.gz' in a['name'])])
except Exception as exc:report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),url=url,error_type=type(exc).__name__,error=str(exc))
(base/'python-release-fetch.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
