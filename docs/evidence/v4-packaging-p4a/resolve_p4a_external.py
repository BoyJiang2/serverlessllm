from pathlib import Path
import subprocess,os,json,hashlib
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');root=Path('/home/boyjiang/sllm-v4-pro/code');engine=Path('/home/boyjiang/sllm-v4-pro/engine-src/vllm-132765e35606');work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');python=work/'python/cpython-3.12.3-linux-x86_64-gnu/bin/python3.12'
assert subprocess.check_output([str(python),'--version'],text=True).strip()=='Python 3.12.3'
paths=[root/'requirements-v4.txt',root/'requirements-worker-v4.txt',root/'sllm_store/requirements-v4.txt',root/'sllm_store/requirements-v4-build.txt',engine/'requirements/build/cuda.txt']
(base/'v4-bootstrap-linux.in').write_text(''.join('-r '+str(p)+'\n' for p in paths))
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
cmd=[str(work/'tools/uv'),'--no-config','pip','compile',str(base/'v4-bootstrap-linux.in'),'-c',str(base/'core-constraints.txt'),'--python',str(python),'--python-platform','x86_64-manylinux_2_35','--no-python-downloads','--no-build','--find-links',str(work/'pure-wheels'),'--cache-dir',str(work/'uv-cache'),'--exclude-newer','2026-11-05T00:00:00+08:00','--color','never','--offline','--format','requirements.txt','--generate-hashes','--no-header','--no-annotate','--no-emit-package','vllm','-o',str(base/'v4-bootstrap-external.txt')]
env=dict(os.environ,HTTPS_PROXY='http://127.0.0.1:17891',HTTP_PROXY='http://127.0.0.1:17891')
with (base/'resolve-external.log').open('w') as log:r=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
assert {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}==before
report=dict(command=cmd,exit_code=r.returncode,input_sha256=before,python_version='3.12.3',scope='Actual Linux CPython 3.12.3 metadata resolution; no dependency build/install or model/GPU imports')
if r.returncode==0:report['lock_sha256']=hashlib.sha256((base/'v4-bootstrap-external.txt').read_bytes()).hexdigest()
(base/'resolve-external-receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('command','input_sha256')}));raise SystemExit(r.returncode)
