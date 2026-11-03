import pathlib,json,subprocess
root=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM')
evidence=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/baseline-relocation-20261103')
archive=evidence/'retired-adapted-checkout'
base='9f50241baa5386e06a9321c51f19a9ef5f964c2b'
adapted='00097eaafd60dbae262a954a29863b5c633aade9'
remote='https://github.com/ServerlessLLM/ServerlessLLM.git'
def run(*args):return subprocess.check_output(args,text=True,timeout=60).strip()
assert not root.exists() and archive.is_dir()
assert pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/upstream-original-pin.txt').read_text().split()==[base,'refs/heads/main']
assert run('git','-C',str(archive),'rev-parse','HEAD')==adapted
run('git','clone','--no-hardlinks','--no-checkout','--origin','migration-cache','--single-branch','--branch','main',str(archive),str(root))
def git(*args):return run('git','-C',str(root),*args)
git('switch','--detach',base)
git('update-ref','refs/heads/main',base,adapted)
git('remote','remove','migration-cache')
git('remote','add','origin',remote)
git('update-ref','refs/remotes/origin/main',base,'0'*40)
git('branch','--set-upstream-to=origin/main','main')
git('switch','main')
assert git('rev-parse','HEAD')==base and not git('status','--porcelain')
assert (root/'sllm_store/sllm_store/proto/storage.proto').is_symlink()
assert not (root/'.git/objects/info/alternates').exists()
receipt={'path':str(root),'head':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),'status_porcelain':git('status','--porcelain'),'remote':git('remote','-v'),'tracking':git('rev-parse','--abbrev-ref','@{upstream}'),'tree':git('rev-parse','HEAD^{tree}'),'same_tree_as_original_cached_commit':git('rev-parse','HEAD^{tree}')==run('git','-C',str(archive),'rev-parse',base+'^{tree}'),'clone_transport':'local Git clone --no-hardlinks from retained verified objects after direct GitHub TLS failure and bounded retry; independent object store; no build/weight/environment copied','upstream_ref_verified_through':'existing Windows Git credential helper; official GitHub main SHA equals original baseline','adaptation_directory':'/data/brick1/boyjiang/kimi-new/ServerlessLLM'}
(evidence/'pristine-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
