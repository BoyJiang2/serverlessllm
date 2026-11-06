from pathlib import Path
import json,subprocess,os,hashlib,glob,importlib.metadata as md,datetime,tomllib,sys
base=Path('/logs');work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');os.umask(0o002)
assert not glob.glob('/dev/nvidia*')
receipt=json.loads((base/'resolve-final-receipt.json').read_bytes());assert receipt['exit_code']==0
for name,sha in receipt['artifacts'].items():assert hashlib.sha256((base/name).read_bytes()).hexdigest()==sha
for row in receipt['wheel_inputs']:assert hashlib.sha256(Path(row['file']).read_bytes()).hexdigest()==row['sha256']
cmd=['/usr/local/bin/uv','--no-config','pip','sync','--python','/opt/sllm-v4/bin/python','--require-hashes','--no-build','--offline','--no-index','--find-links',str(work/'pure-wheels'),'--cache-dir',str(work/'container-cache'),str(base/'v4-final-requirements.txt')]
with (base/'final-sync.log').open('x') as log:
 r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,UV_LINK_MODE='copy'));assert r.returncode==0
with (base/'final-pip-check.log').open('x') as log:
 r=subprocess.run(['/usr/local/bin/uv','--no-config','pip','check','--python','/opt/sllm-v4/bin/python'],stdout=log,stderr=subprocess.STDOUT);assert r.returncode==0
expected={p['name']:p['version'] for p in tomllib.loads((base/'pylock.v4-final.toml').read_text())['packages']}
def normalized(name):return name.lower().replace('_','-').replace('.','-')
installed={normalized(d.metadata['Name']):d.version for d in md.distributions()};assert installed==expected,(set(installed)^set(expected))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sync_exit_code=0,pip_check_exit_code=0,installed_packages=installed,exact_final_graph_match=True,scope='Real isolated environment sync to full final hash lock; no native import, GPU, or model claim')
(base/'final-install-receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='installed_packages'}))
