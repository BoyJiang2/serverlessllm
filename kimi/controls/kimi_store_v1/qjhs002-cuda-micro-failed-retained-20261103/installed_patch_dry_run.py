import hashlib,importlib.util,json,pathlib,subprocess
root=pathlib.Path(importlib.util.find_spec('vllm').origin).parent
patch=pathlib.Path('/src/sllm_store/vllm_patch/sllm_load.patch')
targets={line.split()[1][7:] for line in patch.read_text().splitlines() if line.startswith('+++ b/vllm/')}
def snapshot():
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).is_file() else None for name in sorted(targets)}
before=snapshot();results={}
for name,flags in [('forward',['--forward']),('reverse',['--force','--reverse'])]:
    argv=['patch','--batch','--fuzz=0','--dry-run','-p2','--directory',str(root),'--input',str(patch),*flags]
    result=subprocess.run(argv,capture_output=True,text=True,timeout=45)
    results[name]={'argv':argv,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
after=snapshot()
assert before==after
f=results['forward']['returncode'];r=results['reverse']['returncode']
classification='unapplied' if f==0 and r!=0 else 'applied' if r==0 and f!=0 else 'conflict_or_incompatible'
print(json.dumps({'vllm_path':str(root),'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'before':before,'after':after,'unchanged':before==after,'classification':classification,'results':results},indent=2))
