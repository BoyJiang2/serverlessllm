"""Root-only final evidence index; raw kernel output remains private on local disk."""
import datetime,hashlib,json,os,pathlib,subprocess,sys
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python forbidden')
run='sllm-glm51fp8-20261026T092330Z-97af52ad'
if os.getuid()!=0 or sys.argv[1:]!=[run]: raise RuntimeError('wrong root identity')
base=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')/'validation/controls'/run
private=base/'root-evidence-private13'
if private.is_symlink() or private.stat().st_uid!=0: raise RuntimeError('untrusted archive')
def call(*args): return subprocess.check_output(args,text=True,timeout=15).strip()
rows=[]
for path in sorted(private.iterdir()):
    if path.is_symlink() or not path.is_file(): raise RuntimeError('unexpected archive entry')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    rows.append(dict(path=path.relative_to(base).as_posix(),sha256=digest.hexdigest(),bytes=path.stat().st_size))
cleanup=(private/'cleanup-v13-main.log').read_text()
if 'cleanup_finished' not in cleanup or not cleanup.rstrip().endswith('failed=0'): raise RuntimeError('cleanup did not pass')
group=call('systemctl','show',run+'.slice','-p','ControlGroup','--value')
if int((pathlib.Path('/sys/fs/cgroup')/group.lstrip('/')/'pids.current').read_text())!=0: raise RuntimeError('owned processes remain')
observers={suffix:call('systemctl','show',run+'-'+suffix+'.service','-p','ActiveState','--value') for suffix in ['kernelobserver13','dockerobserver13']}
if any(value not in ('inactive','failed') for value in observers.values()): raise RuntimeError('observer remains live')
timer_state=call('systemctl','show',run+'-deadline-v13.timer','-p','ActiveState','--value')
owned_containers=call('docker','ps','--filter','label=sllm.run_id='+run,'--format','{{.ID}}')
if timer_state!='inactive' or owned_containers: raise RuntimeError('final timer/containers not released')
proof=json.loads((private/'guard-verification.json').read_text())
deployment=json.loads((base/'deployment.json').read_text())
manifest_sha=hashlib.sha256((base/'control-input-manifest.json').read_bytes()).hexdigest()
if deployment['run_id']!=run or deployment['input_manifest_sha256']!=manifest_sha: raise RuntimeError('deployment binding changed')
index=dict(run_id=run,boot_id=proof['boot_id'],code_commit=deployment['code_commit'],manifest_sha256=manifest_sha,guard_sha256=proof['cleanup_sha256'],guard_probe_passed=proof['probe_passed'],cleanup_passed=True,deadline_timer_state=timer_state,owned_containers=owned_containers,observers=observers,files=rows,collected_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope='root-owned persistent private raw logs; index contains no kernel content')
path=base/'root-evidence-index13.json'
with path.open('x') as stream: json.dump(index,stream,indent=2)
path.chmod(0o444)
print(json.dumps(index,indent=2))
