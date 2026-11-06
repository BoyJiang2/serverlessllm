from pathlib import Path
import json,hashlib,subprocess,datetime,os
os.umask(0o002);base=Path('/logs');d=json.loads((base/'store-native-source-inputs.json').read_bytes());source=Path('/opt/src/sllm')
for r in d['files']:assert hashlib.sha256((source/r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
rows=[]
for path in sorted((source/'sllm_store/build/temp.linux-x86_64-cpython-312/_deps').glob('*-src')):
 if not (path/'.git').exists():continue
 def git(*a):return subprocess.check_output(['git','-c','safe.directory='+str(path),'-C',str(path),*a],text=True).strip()
 status=git('status','--porcelain');assert not status
 rows.append(dict(path=str(path),commit=git('rev-parse','HEAD'),tag=git('describe','--tags','--exact-match','HEAD'),working_tree_clean=True))
assert {r['tag'] for r in rows}=={'v0.6.0','v1.13.0'}
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_commit=d['source_commit'],matching_native_source_inputs=len(d['files']),dependencies=rows,scope='Actual Store C++/CMake/packaging inputs match Git; fetched dependency commits/tags checked. GPU paths not executed')
(base/'store-native-source-identity.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
