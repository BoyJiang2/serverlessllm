from pathlib import Path
import json,urllib.request,hashlib,os,datetime,sys
os.umask(0o002);base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');meta=json.loads((base/'uv-python-metadata-fetch.json').read_bytes())['metadata'];target=work/'python-source-3.12.3-20240415.tar.gz';partial=target.with_suffix('.partial');assert not target.exists() and not partial.exists()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:17891','http':'http://127.0.0.1:17891'}));h=hashlib.sha256();size=0
try:
 with opener.open(urllib.request.Request(meta['url'],headers={'User-Agent':'uv/0.11.28'}),timeout=30) as response,partial.open('xb') as out:
  while data:=response.read(1024*1024):
   size+=len(data);assert size<128*1024**2;h.update(data);out.write(data)
 assert h.hexdigest()==meta['sha256'];partial.rename(target)
 report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),archive=str(target),bytes=size,sha256=h.hexdigest(),matches_uv_0_11_28_official_metadata=True,url=meta['url'],scope='Exact Python build archive verified; installed files compared separately')
 (base/'python-archive-receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
except Exception as exc:
 (base/'python-archive-failure.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),error_type=type(exc).__name__,error=str(exc),bytes=size,partial=str(partial)))+'\n');raise
