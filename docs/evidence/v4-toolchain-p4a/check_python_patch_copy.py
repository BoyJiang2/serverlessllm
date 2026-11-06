from pathlib import Path
import json,hashlib,datetime
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');d=json.loads((base/'python-installed-archive-comparison.json').read_bytes());rows=[]
for r in d['different']:
 p=work/'python/cpython-3.12.3-linux-x86_64-gnu'/r['path'];sha=hashlib.sha256(p.read_bytes()).hexdigest();assert sha==r['installed_sha256'];rows.append(dict(path=r['path'],sha256=sha,host_managed_equals_container=True))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),patched_files=rows,scope='All three archive differences already present in UV-managed source copied to container; no hidden container-only patch')
(base/'python-uv-patch-copy-proof.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
