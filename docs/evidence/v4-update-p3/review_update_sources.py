from pathlib import Path
import hashlib,json,subprocess
root=Path('/home/boyjiang/sllm-v4-pro/code')
def git(*args): return subprocess.check_output(['git','-C',str(root),*args])
head=git('rev-parse','HEAD').decode().strip()
suite=json.loads(Path('/home/boyjiang/sllm-v4-pro/evidence/update-suite-receipt.json').read_bytes())
diff=git('diff','--binary','HEAD','--','benchmarks','sllm','sllm_store','tests')
assert suite['tests_run']==286 and suite['cpu_suite_passed'] and not suite['skipped']
assert hashlib.sha256(diff).hexdigest()==suite['working_diff_sha256']
base='89ced92416f8342a03c3df83670249cd626ac0d2'
paths=git('diff','--name-only',base,'--','sllm','sllm_store','benchmarks').decode().splitlines()
rows=[]
for name in paths:
 if '/tests/' in name or name.startswith('tests/') or not (root/name).is_file(): continue
 data=(root/name).read_bytes()
 blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
 index=git('ls-files','-s','--',name).decode().split()
 assert index[1]==blob,(name,'raw source differs from staged bytes')
 rows.append(dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),git_blob=blob))
report=dict(source_head=head,working_diff_sha256=suite['working_diff_sha256'],base_commit=base,
 scope='Cumulative changed runtime sources versus Kimi base; raw bytes match current index; no native/GPU qualification',
 source_files=len(rows),files=rows)
Path('/home/boyjiang/sllm-v4-pro/evidence/update-reviewed-source-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('source_head','working_diff_sha256','source_files')}))
