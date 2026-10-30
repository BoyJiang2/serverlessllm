"""Copy reviewed metadata/source from a clean commit to qjhs003; never start compute."""
import hashlib,json,pathlib,re,subprocess,sys,os,shutil,tempfile
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
repo=pathlib.Path(__file__).resolve().parents[2]
package=repo/'runtime/active'
sha,reviewed_commit,verdict_file=sys.argv[1:]
verdict=json.loads(pathlib.Path(verdict_file).read_text())
from package_inventory13 import require_full_r1
require_full_r1(verdict,reviewed_commit,sha)
origin=subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()
if origin!='https://github.com/BoyJiang2/serverlessllm-glm51-fp8.git': raise RuntimeError('wrong origin')
if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!='main': raise RuntimeError('main branch required')
if not re.fullmatch('[0-9a-f]{40}',reviewed_commit): raise RuntimeError('full reviewed commit required')
subprocess.run(['git','merge-base','--is-ancestor',reviewed_commit,'HEAD'],cwd=repo,check=True)
subprocess.run(['git','diff','--exit-code',reviewed_commit,'HEAD','--','runtime/active'],cwd=repo,check=True)
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
if subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True).strip(): raise RuntimeError('deployment requires a clean committed checkout')
if hashlib.sha256((package/'control-input-manifest.json').read_bytes()).hexdigest()!=sha: raise RuntimeError('review manifest changed')
manifest=json.loads((package/'run-manifest.json').read_text())
run=manifest['run_id']
if not re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',run): raise RuntimeError('invalid identity')
rows=json.loads((package/'control-input-manifest.json').read_text())['files']
from package_inventory13 import reviewed_files
allowed=reviewed_files(package,rows)
tracked=set(subprocess.check_output(['git','ls-files','--','runtime/active'],cwd=repo,text=True).splitlines())
if any('runtime/active/'+name not in tracked for name in allowed): raise RuntimeError('uncommitted package file')
for row in rows:
    path=(package/row['path']).resolve()
    if not path.is_relative_to(package) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']: raise RuntimeError('input mismatch: '+row['path'])
destination='/data/brick1/boyjiang/glm5.1-fp8/code/experiment'
run_destination='/data/brick1/boyjiang/glm5.1-fp8/validation/controls/'+run
init="""import json,os,pathlib,subprocess,sys
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
m=json.loads(%r)
e=m['runtime_identity']
assert pathlib.Path('/etc/machine-id').read_text().strip()==e['machine_id']
assert pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==e['boot_id']
assert subprocess.check_output(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],text=True).strip().splitlines()==e['gpu_uuids']
assert subprocess.check_output(['hostname'],text=True).strip()==e['hostname']
assert os.getuid()==2147
p=pathlib.Path(%r)
assert not p.exists(), 'existing control destination; inspect partial deployment, do not overwrite'
p.mkdir(mode=0o700)
r=pathlib.Path(%r)
assert not r.exists(), 'existing run output; never overwrite prior control evidence'
r.parent.mkdir(parents=True,exist_ok=True)
r.mkdir(mode=0o700)
"""%(json.dumps(manifest),destination,run_destination)
subprocess.run(['ssh','-o','StrictHostKeyChecking=yes','qjhs003','python3','-'],input=init,text=True,check=True)
# Copy exactly reviewed files through one bytecode-free local staging directory.
work=repo.parent/'work'
with tempfile.TemporaryDirectory(prefix='active-deploy-',dir=work) as temporary:
    staging=pathlib.Path(temporary).resolve()
    if not staging.is_relative_to(work.resolve()): raise RuntimeError('staging escaped workspace')
    for name in sorted(allowed):
        target=staging/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(package/name,target)
    subprocess.run(['scp','-q','-o','StrictHostKeyChecking=yes','-r',str(staging)+r'/.','qjhs003:'+destination+'/'],check=True)
receipt=dict(code_commit=reviewed_commit,repository_metadata_commit=head,input_manifest_sha256=sha,run_id=run,origin='https://github.com/BoyJiang2/serverlessllm-glm51-fp8',repository_prefix='runtime/active')
verify="""import hashlib,json,pathlib,sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
p=pathlib.Path(%r)
r=pathlib.Path(%r)
m=p/'control-input-manifest.json'
assert hashlib.sha256(m.read_bytes()).hexdigest()==%r
for row in json.loads(m.read_text())['files']:
 f=(p/row['path']).resolve()
 assert f.is_relative_to(p) and hashlib.sha256(f.read_bytes()).hexdigest()==row['sha256'],row['path']
for name in ('run-manifest.json','resource-window-extension-v3.json','cache-inputs13.json','control-input-manifest.json'):
 (r/name).write_bytes((p/name).read_bytes())
with (r/'deployment.json').open('x') as stream: json.dump(json.loads(%r),stream,indent=2)
print('canonical source and run registration verified; no workload started')
"""%(destination,run_destination,sha,json.dumps(receipt))
subprocess.run(['ssh','-o','StrictHostKeyChecking=yes','qjhs003','python3','-'],input=verify,text=True,check=True)
