"""Copy reviewed metadata/source from a clean commit to qjhs003; never start compute."""
import hashlib,json,pathlib,re,subprocess,sys
repo=pathlib.Path(__file__).resolve().parents[2]
package=repo/'controls/candidate13b'
sha=sys.argv[1]
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
if subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True).strip(): raise RuntimeError('deployment requires a clean committed checkout')
if hashlib.sha256((package/'control-input-manifest.json').read_bytes()).hexdigest()!=sha: raise RuntimeError('review manifest changed')
manifest=json.loads((package/'run-manifest.json').read_text())
run=manifest['run_id']
if not re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',run): raise RuntimeError('invalid identity')
rows=json.loads((package/'control-input-manifest.json').read_text())['files']
allowed={row['path'] for row in rows}|{'control-input-manifest.json','REVIEW-PROMPT.md'}
actual={p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
if actual!=allowed: raise RuntimeError('unexpected or missing package files')
tracked=set(subprocess.check_output(['git','ls-files','--','controls/candidate13b'],cwd=repo,text=True).splitlines())
if any('controls/candidate13b/'+name not in tracked for name in allowed): raise RuntimeError('uncommitted package file')
for row in rows:
    path=(package/row['path']).resolve()
    if not path.is_relative_to(package) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']: raise RuntimeError('input mismatch: '+row['path'])
destination='/data/brick1/boyjiang/'+run
init="""import json,os,pathlib,subprocess,sys
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
m=json.loads(%r)
e=m['runtime_identity']
assert pathlib.Path('/etc/machine-id').read_text().strip()==e['machine_id']
assert pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==e['boot_id']
assert subprocess.check_output(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],text=True).strip().splitlines()==e['gpu_uuids']
assert os.getuid()==2147
p=pathlib.Path(%r)
assert not p.exists(), 'existing control destination; inspect partial deployment, do not overwrite'
p.mkdir(mode=0o700)
"""%(json.dumps(manifest),destination)
subprocess.run(['ssh','-o','StrictHostKeyChecking=yes','qjhs003','python3','-'],input=init,text=True,check=True)
subprocess.run(['scp','-q','-o','StrictHostKeyChecking=yes','-r',str(package)+r'/.','qjhs003:'+destination+'/'],check=True)
receipt=dict(code_commit=head,input_manifest_sha256=sha,run_id=run,origin='https://github.com/BoyJiang2/serverlessllm-glm51-fp8',repository_prefix='controls/candidate13b')
verify="""import hashlib,json,pathlib,sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
p=pathlib.Path(%r)
m=p/'control-input-manifest.json'
assert hashlib.sha256(m.read_bytes()).hexdigest()==%r
for row in json.loads(m.read_text())['files']:
 f=(p/row['path']).resolve()
 assert f.is_relative_to(p) and hashlib.sha256(f.read_bytes()).hexdigest()==row['sha256'],row['path']
with (p/'deployment.json').open('x') as stream: json.dump(json.loads(%r),stream,indent=2)
print('committed source deployed and all hashes verified; no workload started')
"""%(destination,sha,json.dumps(receipt))
subprocess.run(['ssh','-o','StrictHostKeyChecking=yes','qjhs003','python3','-'],input=verify,text=True,check=True)
