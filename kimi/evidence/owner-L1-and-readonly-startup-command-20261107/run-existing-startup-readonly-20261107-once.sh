#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" -eq 0
exec timeout --signal=TERM --kill-after=5s 60s python3 -B - <<'PY'
import hashlib,json,os,pathlib,pwd,sys
base=pathlib.Path('/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104')
source=pathlib.Path('/home/boyjiang/kimi-new/ServerlessLLM/sllm_store/tests/collect_existing_kimi_startup_evidence.py')
raw=source.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='91b0656e73cd918795c18cdc563b4320e235e60db04baca70c7719eeb5ce99d3'
private=base/'kimi-existing-startup-log-readonly-20261107-once-private.json'
public=base/'kimi-existing-startup-log-readonly-20261107-once-public.json'
assert not public.exists() and not public.is_symlink(), 'public once output already exists'
manifest=base/'kimi-four-analysis-input-snapshot-v1-20261107/input-manifest-v3.json'
sys.argv=[str(source),str(manifest),'--manifest-sha','4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801','--output',str(private)]
os.umask(0o077)
exec(compile(raw,str(source),'exec'),{'__name__':'__main__','__file__':str(source)})
data=private.read_bytes()
assert 0<len(data)<(8<<20) and json.loads(data)['schema']=='kimi-existing-startup-evidence-v1'
fd=os.open(public,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 with os.fdopen(fd,'wb',closefd=False) as f:
  f.write(data);f.flush();os.fsync(f.fileno())
 owner=pwd.getpwnam('boyjiang')
 os.fchown(fd,owner.pw_uid,owner.pw_gid)
 os.fchmod(fd,0o644)
finally:os.close(fd)
assert hashlib.sha256(public.read_bytes()).digest()==hashlib.sha256(data).digest()
print(json.dumps({'status':'READONLY_SANITIZED_PUBLIC_SUMMARY_EXPORTED','path':str(public),'sha256':hashlib.sha256(data).hexdigest(),'no_GPU_or_containers_started':True}))
PY
