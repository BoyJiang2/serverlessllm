import datetime, hashlib, json, os, subprocess
from pathlib import Path
root=Path('/data/brick1/boyjiang/glm5.1-fp8').resolve(strict=True)
run='sllm-glm51fp8-20261027T011726Z-28c92313'
out=root/'validation/controls'/run
src=root/'code/experiment'
dest=out/'executed-source-f176e05'
receipt=out/'executed-source-archive.json'
expected='3eac8c63cdd670a49d872922c11b821d07a0e252f2fbe80f790e169749091fb9'
assert src.is_dir() and not src.is_symlink() and not dest.exists() and not receipt.exists()
assert out.is_dir() and not out.is_symlink() and out.resolve()==out
assert src.resolve()==src and dest.parent.resolve()==out
manifest_bytes=(src/'control-input-manifest.json').read_bytes()
assert hashlib.sha256(manifest_bytes).hexdigest()==expected
manifest=json.loads(manifest_bytes)
identity=json.loads((src/'CONTROL-IDENTITY.json').read_bytes())
assert identity['run_id']==run and identity['workspace_root']==str(root)
ri=json.loads((out/'root-evidence-index13.json').read_bytes())
assert ri['run_id']==run and ri['cleanup_passed'] is True and ri['owned_containers']=='' and ri['deadline_timer_state']=='inactive'
deployment=json.loads((out/'deployment.json').read_bytes())
assert deployment['run_id']==run and deployment['code_commit']=='f176e05607d04999ab28e8ea8cd49b41270cb528' and deployment['input_manifest_sha256']==expected
release_bytes=(out/'release60-allstopped.json').read_bytes()
assert hashlib.sha256(release_bytes).hexdigest()=='cc142b267328eab2b93f448ca12a58fc3e5629e411f4f72f9a8a9ada48277dc6'
release=json.loads(release_bytes)
assert len(release)==61 and all(not r['processes'] and not r['own_running'] and int(r['cgroup']['pids.current'])==0 for r in release)
timer=subprocess.check_output(['systemctl','show',run+'-deadline-v13.timer','-p','ActiveState','--value'],text=True).strip()
assert timer=='inactive'
group=subprocess.check_output(['systemctl','show',run+'.slice','-p','ControlGroup','--value'],text=True).strip()
assert Path(group).name==run+'.slice'
cg=Path('/sys/fs/cgroup')/group.lstrip('/')
assert cg.is_dir() and int((cg/'pids.current').read_text())==0
procs=list(cg.rglob('cgroup.procs'))
assert procs and all(not f.read_text().strip() for f in procs)
containers=subprocess.check_output(['docker','ps','-q','--filter','label=sllm.run_id='+run],text=True).strip()
# Independently inspect names as well, avoiding reliance on a possibly absent label.
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
assert not containers and not any(run in name for name in names)
checked=[]
for f in manifest['files']:
 rel=Path(f['path']); p=src/rel
 assert not rel.is_absolute() and '..' not in rel.parts and not p.is_symlink()
 assert p.resolve().is_relative_to(src.resolve())
 b=p.read_bytes(); assert len(b)==f['bytes'] and hashlib.sha256(b).hexdigest()==f['sha256'],str(p)
 checked.append(f)
assert len(checked)==81
def publish(path,record):
 data=(json.dumps(record,indent=2)+'\n').encode()
 temp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,'wb') as stream:
  stream.write(data); stream.flush(); os.fsync(stream.fileno())
 os.link(temp,path)  # Fail rather than replace an existing evidence record.
 temp.unlink()
 fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(fd)
 finally: os.close(fd)
 return data
record=dict(run_id=run,code_commit=deployment['code_commit'],manifest_sha256=expected,
 utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source=str(src),archive=str(dest),
 files_checked_before_and_after=checked,owned_containers=[],cgroup=str(cg),pids_current=0,
 deadline_timer_state=timer,release60_sha256=hashlib.sha256(release_bytes).hexdigest(),
 root_release_index_sha256=hashlib.sha256((out/'root-evidence-index13.json').read_bytes()).hexdigest(),
 scope='Same-filesystem rename after owned deployment release; no model, Store, historical results or third-party resource changed.')
publish(out/'executed-source-archive-intent.json',dict(record,status='verified before rename'))
try:
 os.rename(src,dest)
 for parent in (src.parent,dest.parent):
  fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
  try: os.fsync(fd)
  finally: os.close(fd)
 for f in checked:
  b=(dest/f['path']).read_bytes()
  assert len(b)==f['bytes'] and hashlib.sha256(b).hexdigest()==f['sha256']
 assert hashlib.sha256((dest/'control-input-manifest.json').read_bytes()).hexdigest()==expected
 assert not src.exists()
 record.update(status='archived and reverified',canonical_source_absent=True)
except BaseException as exc:
 publish(out/'executed-source-archive-failure.json',dict(record,status='archive failure; inspect intent/source/archive before any recovery',error_type=type(exc).__name__,error=str(exc),source_exists=src.exists(),archive_exists=dest.exists()))
 raise
print(publish(receipt,record).decode(),end='')
