"""CPU-only --version control, never invoke an NVIDIA hook subcommand."""
import hashlib,json,pathlib,subprocess,time,os
hook=pathlib.Path('/hook')
assert hashlib.sha256(hook.read_bytes()).hexdigest()=='fb9e22c288346e9a99cfbef446a8495932073608aa9982628a750cacb129925c'
def snapshot():
    result={}
    for name in ('pids.max','pids.current','pids.peak','pids.events','pids.events.local','memory.max','memory.current','memory.events'):
        p=pathlib.Path('/sys/fs/cgroup')/name
        result[name]=p.read_text() if p.exists() else None
    return result
print(json.dumps({'event':'CPU_DIAG_READY','purpose':'three seconds for read-only exact OCI snapshot'}),flush=True)
time.sleep(3)
before=snapshot()
assert before['pids.max'].strip() in ('128','512')
assert not any(pathlib.Path('/dev').glob('nvidia*'))
start=time.monotonic();error=None
try:
    run=subprocess.run(['/hook','--version'],stdin=subprocess.DEVNULL,capture_output=True,timeout=8)
    output=run.stdout;stderr=run.stderr;code=run.returncode
except subprocess.TimeoutExpired as e:
    output=e.stdout or b'';stderr=e.stderr or b'';code=None;error='timeout'
after=snapshot()
result={'scope':'CPU runc with no GPU devices; /hook --version only','elapsed_seconds':time.monotonic()-start,'returncode':code,'error':error,'before':before,'after':after,'go_environment':{k:os.environ.get(k) for k in ('GOMAXPROCS','GODEBUG')},'affinity_cpu_count':len(os.sched_getaffinity(0)),'stdout_excerpt':output[:512].decode(errors='replace'),'stderr_excerpt':stderr[:512].decode(errors='replace'),'stderr_bytes':len(stderr),'stderr_sha256':hashlib.sha256(stderr).hexdigest(),'hook_sha256':hashlib.sha256(hook.read_bytes()).hexdigest()}
print(json.dumps(result,sort_keys=True),flush=True)
