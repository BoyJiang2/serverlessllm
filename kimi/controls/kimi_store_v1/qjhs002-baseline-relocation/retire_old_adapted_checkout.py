import json,pathlib,os,subprocess
old=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM')
evidence=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/baseline-relocation-20261103')
archive=evidence/'retired-adapted-checkout'
new=pathlib.Path('/data/brick1/boyjiang/kimi-new/ServerlessLLM')
def run(argv):return subprocess.check_output(argv,text=True,timeout=20).strip()
assert old.resolve()==old and new.resolve()==new and archive.parent.resolve()==evidence
assert not archive.exists() and old.is_dir()
assert run(['git','-C',str(new),'rev-parse','HEAD'])=='00097eaafd60dbae262a954a29863b5c633aade9'
assert run(['git','-C',str(old),'rev-parse','HEAD'])=='00097eaafd60dbae262a954a29863b5c633aade9'
ids=run(['docker','ps','-q']).splitlines()
if ids:
    rows=run(['docker','inspect','--format','{{json .Mounts}}',*ids]).splitlines()
    for line in rows:
        for mount in json.loads(line):
            source=mount.get('Source','')
            assert source!=str(old) and not source.startswith(str(old)+'/'),source
os.rename(old,archive)
receipt={'original':str(old),'archive':str(archive),'head':'00097eaafd60dbae262a954a29863b5c633aade9','operation':'same-filesystem rename; no recursive deletion or build/large-file copy','no_running_container_binds_to_old_checkout':True,'active_adaptation_root':str(new)}
(evidence/'retire-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
