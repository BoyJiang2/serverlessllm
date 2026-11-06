from pathlib import Path
import json,subprocess,hashlib,datetime
root=Path('/home/boyjiang/sllm-v4-pro/code');base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106')
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
assert git('rev-parse','HEAD').decode().strip()=='f3fd0593973879e6a9d744bf7aeb97d539f30472'
receipt=json.loads((base/'packaging-final-suite-receipt.json').read_bytes());assert receipt['tests_run']==297 and receipt['cpu_suite_passed'] and not receipt['skipped']
diff=git('diff','--binary','HEAD','--','benchmarks','sllm','sllm_store','tests');assert hashlib.sha256(diff).hexdigest()==receipt['working_diff_sha256']
names=['setup.py','MANIFEST.in','requirements-v4.txt','requirements-worker-v4.txt','sllm_store/setup.py','sllm_store/MANIFEST.in','sllm_store/requirements-v4.txt','sllm_store/requirements-v4-build.txt','sllm_store/tests/python/test_v4_packaging.py'];rows=[]
for name in names:
 data=(root/name).read_bytes();staged=git('show',':'+name);assert data==staged;rows.append(dict(path=name,sha256=hashlib.sha256(data).hexdigest(),git_blob=git('rev-parse',':'+name).decode().strip()))
legacy=['requirements.txt','requirements-worker.txt','sllm_store/requirements.txt','sllm_store/requirements-build.txt']
for name in legacy:assert git('show','HEAD:'+name)==(root/name).read_bytes()
report=dict(parent=git('rev-parse','HEAD').decode().strip(),scope='Actual tested packaging inputs incl root files outside old runner diff scope; not native wheel/ABI proof',cpu_tests_run=297,runner_source_diff_sha256=receipt['working_diff_sha256'],full_packaging_diff_sha256=hashlib.sha256(git('diff','--binary','HEAD','--',*names)).hexdigest(),files=rows,legacy_requirement_files_unchanged=legacy)
(base/'packaging-tested-source-manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('files','legacy_requirement_files_unchanged')}))
