"""Local-only R1c sealing: CPU tests, syntax, byte diffs and manifest; no remote I/O."""
import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import datetime,difflib,hashlib,json,pathlib,subprocess
root=pathlib.Path(__file__).resolve().parent
repo=root.parents[1]
if root.name!='candidate13c' or not (repo/'.git').exists(): raise RuntimeError('requires local independent checkout')
def save(path,data): path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8',newline='\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
manifest=json.loads((root/'run-manifest.json').read_text())
manifest.update(candidate_revision='13c',status='R1c pending; unapproved and unexecuted; zero formal IDs')
save(root/'run-manifest.json',manifest)
window=json.loads((root/'resource-window-extension-v3.json').read_text())
window['original_manifest_sha256']=sha(root/'run-manifest.json'); save(root/'resource-window-extension-v3.json',window)
references=[]
for name in ('cache_prepare_v6.py','cache_prepare_v8.py','cache_repair_v9.py'):
    source=repo/'runtime/historical-v11'/name
    if sha(source)!=sha(root/name): raise RuntimeError('historical helper bytes differ')
    references.append(dict(path=name,reference_path=source.relative_to(repo).as_posix(),sha256=sha(source),byte_identical=True))
save(root/'evidence/cache-helper-provenance.json',dict(files=references,scope='reference files are frozen parent-v11 code already checked by G0; not a new runtime pass'))
for path in sorted(root.glob('*.py')): compile(path.read_text(encoding='utf-8-sig'),path.name,'exec')
checks=[]
for name in ('install_guard13.sh','cleanup_guard13.sh','install_observers13.sh'):
    result=subprocess.run(['C:/Program Files/Git/bin/bash.exe','-n',str(root/name)],capture_output=True,text=True)
    if result.returncode: raise RuntimeError(result.stderr)
    checks.append(name+' bash -n passed')
result=subprocess.run([sys.executable,'-X','utf8','-m','unittest','discover','-s',str(root),'-p','test*.py'],capture_output=True,text=True,encoding='utf-8')
(root/'static-tests.log').write_text(result.stdout+result.stderr+'\n'+'\n'.join(checks)+'\nAll candidate Python sources compile; tests use synthetic CPU metadata only. No GPU, Docker, pool, cache workload or remote action.\n',encoding='utf-8',newline='\n')
if result.returncode: raise RuntimeError('local unit tests failed; preserve log')
old=root.parent/'candidate13b'
for path in sorted(root.iterdir()):
    if path.suffix not in ('.py','.sh','.json') or path.name=='control-input-manifest.json': continue
    before=(old/path.name).read_text(encoding='utf-8-sig').splitlines(True) if (old/path.name).exists() else []
    diff=''.join(difflib.unified_diff(before,path.read_text(encoding='utf-8-sig').splitlines(True),'candidate13b/'+path.name,'candidate13c/'+path.name))
    (root/(path.name+'.diff')).write_text(diff,encoding='utf-8',newline='\n')
rows=[]
for path in sorted(root.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts or path.name in ('control-input-manifest.json','REVIEW-PROMPT.md'): continue
    data=path.read_bytes()
    if len(data)>1024**2 or b'\0' in data: raise RuntimeError('unexpected large/binary candidate file')
    rows.append(dict(path=path.relative_to(root).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
save(root/'control-input-manifest.json',dict(stage='R1c',status='pending independent DeepSeek review; no runtime approval',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=rows))
print(json.dumps(dict(manifest_sha256=sha(root/'control-input-manifest.json'),files=len(rows),local_checks='25 tests + Python compile +3 bash syntax checks; not G1')))
