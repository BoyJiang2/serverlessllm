from pathlib import Path
import json,hashlib,datetime,os
os.umask(0o002);base=Path('/logs');d=json.loads((base/'local-wheel-git-source-manifest.json').read_bytes());root=Path('/opt/sllm-v4/lib/python3.12/site-packages')
for r in d['files']:assert hashlib.sha256((root/r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_commit=d['source_commit'],matching_installed_source_files=len(d['files']),scope='Actual installed SLLM/Store source bytes equal real wheels and Git blobs; no runtime, GPU or model construction claim')
(base/'local-installed-git-source-proof.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
