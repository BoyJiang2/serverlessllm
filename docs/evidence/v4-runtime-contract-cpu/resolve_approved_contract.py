"""Resolve final native wheel graph offline; execute only after source build succeeds."""
from pathlib import Path
import subprocess,os,json,hashlib,tomllib,datetime
base=Path('/home/boyjiang/sllm-v4-pro/evidence/runtime-contract-20261106/final');root=Path('/home/boyjiang/sllm-v4-pro/code');engine=Path('/home/boyjiang/sllm-v4-pro/engine-src/vllm-132765e35606');work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');python=work/'python/cpython-3.12.3-linux-x86_64-gnu/bin/python3.12'
assert not (base/'resolve-final-receipt.json').exists()
rows=[]
for name in ['build-vllm-a2-result.json','repack-store-result.json','repack-service-result.json']:
 d=json.loads((base/name).read_bytes());assert d['exit_code']==0 and len(d['wheels'])==1
 r=d['wheels'][0];assert hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256'];rows.append(r)
bootstrap=tomllib.loads((base/'pylock.v4-bootstrap-linux.toml').read_text());versions={p['name']:p['version'] for p in bootstrap['packages']}
assert 'serverless-llm' not in versions and 'serverless-llm-store' not in versions
constraints=base/'final-exact-constraints.txt';constraints.write_text(''.join(n+'=='+v+'\n' for n,v in sorted(versions.items())))
inputs=base/'final-native-wheels.in';inputs.write_text('vllm @ '+Path(rows[0]['file']).as_uri()+'\nserverless-llm-store @ '+Path(rows[1]['file']).as_uri()+'\nserverless-llm[worker] @ '+Path(rows[2]['file']).as_uri()+'\n-r '+str(root/'sllm_store/requirements-v4-build.txt')+'\n-r '+str(engine/'requirements/build/cuda.txt')+'\n')
common=[str(work/'tools/uv'),'--no-config','pip','compile',str(inputs),'-c',str(constraints),'--python',str(python),'--python-platform','x86_64-manylinux_2_35','--no-python-downloads','--no-build','--offline','--find-links',str(work/'pure-wheels'),'--cache-dir',str(work/'uv-cache'),'--exclude-newer','2026-11-05T00:00:00+08:00','--color','never']
with (base/'resolve-final.log').open('x') as log:
 for opts in [['--format','pylock.toml','-o',str(base/'pylock.v4-final.toml')],['--format','requirements.txt','--generate-hashes','--no-header','--no-annotate','-o',str(base/'v4-final-requirements.txt')]]:
  r=subprocess.run(common+opts,stdout=log,stderr=subprocess.STDOUT);assert r.returncode==0
final=tomllib.loads((base/'pylock.v4-final.toml').read_text());actual={p['name']:p['version'] for p in final['packages']}
assert actual==dict(versions,**{'serverless-llm':'0.8.0+v4pro.4','serverless-llm-store':'0.8.0+v4pro.4'}),(set(actual)^set(versions))
for name,row in zip(['vllm','serverless-llm-store','serverless-llm'],rows):
 p=next(p for p in final['packages'] if p['name']==name);archive=p['archive']
 assert Path(archive['path']).resolve()==Path(row['file']).resolve() and archive['hashes']['sha256']==row['sha256'],p
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=0,package_records=len(actual),exact_bootstrap_external_versions_preserved=True,wheel_inputs=rows,artifacts={name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in ['final-native-wheels.in','final-exact-constraints.txt','pylock.v4-final.toml','v4-final-requirements.txt']},scope='Offline dependency closure with actual three native/local wheels; installation and import checked separately, no GPU/model proof')
(base/'resolve-final-receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
