"""One-time relocation into the user's existing checkout; no build/environment copies."""
import hashlib,json,os,pathlib,shutil,stat,subprocess
old=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM')
new=pathlib.Path('/data/brick1/boyjiang/kimi-new/ServerlessLLM')
evidence=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/baseline-relocation-20261103')
head='00097eaafd60dbae262a954a29863b5c633aade9'
base='9f50241baa5386e06a9321c51f19a9ef5f964c2b'
branch='kimi-qjhs002-aligned-tail'
remote='https://github.com/BoyJiang2/serverlessllm.git'
proto='sllm_store/sllm_store/proto/storage.proto'
expected_proto='95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b'
def command(*args):return subprocess.check_output(args,text=True).strip()
def git(repo,*args):return command('git','-C',str(repo),*args)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert command('hostname')=='qjhs2' and old.resolve()!=new.resolve()
assert git(old,'rev-parse','HEAD')==head and git(new,'rev-parse','HEAD')==base
subprocess.run(['git','-C',str(old),'merge-base','--is-ancestor',base,head],check=True)
paths=git(old,'diff','--name-only',base,head).splitlines()
assert len(paths)==13
assert proto not in paths
assert git(new,'diff','--name-only').splitlines()==[proto]
assert not git(new,'diff','--cached','--name-only')
assert not git(new,'ls-files','--others','--exclude-standard')
sdk='sllm_store/sllm_store'
artifacts={name:sha(old/sdk/name) for name in ('_C.cpython-312-x86_64-linux-gnu.so','_checkpoint_store.cpython-312-x86_64-linux-gnu.so','libglog.so.0.6.0')}
aliases={'libglog.so':'libglog.so.0.6.0','libglog.so.1':'libglog.so.0.6.0'}
for name,digest in artifacts.items():
    assert not (old/sdk/name).is_symlink() and (old/sdk/name).read_bytes()[:4]==b'\x7fELF'
    target=new/sdk/name
    assert not target.is_symlink()
    if target.exists():assert target.is_file() and sha(target)==digest, str(target)
for name,value in aliases.items():
    assert (old/sdk/name).is_symlink() and os.readlink(old/sdk/name)==value
    target=new/sdk/name
    if target.exists() or target.is_symlink():assert target.is_symlink() and os.readlink(target)==value
pin=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/source-remote-pin-relocation.txt').read_text().split()
assert pin==[head,'refs/heads/'+branch]
evidence.mkdir()
receipt={'old':str(old),'new':str(new),'old_head':head,'target_before':base,'remote':remote,'branch':branch,'remote_verification':'live GitHub ls-remote through existing Windows credential helper; server has no GitHub HTTPS credential; source objects fetched from existing same-host checkout','user_confirmed_copy_method':'recursive scp','scp_reference':'https://man.openbsd.org/scp.1#r','before':{},'source_files':{},'artifacts':artifacts,'aliases':aliases}
for label,repo in [('old',old),('target',new)]:
    p=repo/proto
    assert p.is_file() and not p.is_symlink() and sha(p)==expected_proto
    s=p.lstat();receipt['before'][label]={'status':git(repo,'status','--short'),'proto_sha256':sha(p),'proto_lstat':{'mode':s.st_mode,'size':s.st_size,'uid':s.st_uid,'gid':s.st_gid,'inode':s.st_ino,'mtime_ns':s.st_mtime_ns},'head':git(repo,'rev-parse','HEAD')}
    shutil.copy2(p,evidence/(label+'-storage.proto.regular-before'))
    (evidence/(label+'-worktree.diff')).write_text(git(repo,'diff','--binary')+'\n')
(evidence/'before.json').write_text(json.dumps(receipt,indent=2)+'\n')
git(new,'fetch','--no-tags',str(old),'main')
assert git(new,'rev-parse','FETCH_HEAD')==head
git(new,'switch','-c',branch)
git(new,'merge','--ff-only','FETCH_HEAD')
assert git(new,'rev-parse','HEAD')==head
git(new,'remote','set-url','origin',remote)
tracking='refs/remotes/origin/'+branch
existing=subprocess.run(['git','-C',str(new),'rev-parse','--verify',tracking],capture_output=True,text=True)
previous=existing.stdout.strip() if existing.returncode==0 else '0'*40
assert previous in ('0'*40,head)
git(new,'update-ref',tracking,head,previous)
assert git(new,'rev-parse','refs/remotes/origin/'+branch)==head
git(new,'branch','--set-upstream-to=origin/'+branch,branch)
for name,digest in artifacts.items():
    target=new/sdk/name
    if not target.exists():
        with target.open('xb') as out,(old/sdk/name).open('rb') as source:shutil.copyfileobj(source,out)
        shutil.copymode(old/sdk/name,target)
    assert sha(target)==digest
for name,value in aliases.items():
    target=new/sdk/name
    if not target.is_symlink():os.symlink(value,target)
    assert target.resolve()==(new/sdk/value).resolve()
p=new/proto
link=git(new,'show',head+':'+proto)
assert git(new,'ls-tree',head,proto).split()[0]=='120000' and link=='../../proto/storage.proto'
assert sha(p)==expected_proto and not p.is_symlink() and sha(p.parent/link)==expected_proto
stage=p.with_name(p.name+'.kimi-link-stage')
assert not stage.exists() and not stage.is_symlink()
os.symlink(link,stage);os.replace(stage,p)
assert p.is_symlink() and os.readlink(p)==link and sha(p)==expected_proto
assert not git(new,'diff','--name-only') and not git(new,'diff','--cached','--name-only')
for name in paths:
    assert (old/name).read_bytes()==(new/name).read_bytes(),name
    receipt['source_files'][name]=sha(new/name)
receipt.update({'target_after':git(new,'rev-parse','HEAD'),'target_status_after':git(new,'status','--short'),'target_remote_after':git(new,'remote','-v'),'target_tracking_after':git(new,'rev-parse','--abbrev-ref','@{upstream}'),'proto_restored_link':os.readlink(p),'proto_content_sha256_after':sha(p),'old_status_after':git(old,'status','--short'),'scope':'existing checkout only, source commits and five small native artifacts; no build tree, environment, weight, GPU or model migration'})
(evidence/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
