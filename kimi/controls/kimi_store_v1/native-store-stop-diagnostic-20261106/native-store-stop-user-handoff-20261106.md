# Native Store final-stop diagnostic: user-run handoff

Prepared only, NOT RUN. Source branch kimi-qjhs002-aligned-tail, commit 757eda602a5a99d224e1d7cd353d2ba334744d6f, GitHub exact ref verified. Same two AAB lifecycle checks, original gold/config/640GiB pool/64MiB chunks/4threads/7200-second total budget and strict release. This is diagnosis, not a proven shutdown repair; no native fixed32 performance claim.

Read-only snapshots at Store-stop10/20seconds: authenticated process/task identities, wchan/syscall/available kernel stack. Original SIGINT,30-second stop deadline, finish, gone and independent guards unchanged. Diagnostic errors/timeouts recorded without permission fallback. Reader timeout1second is configured subprocess timeout, not an absolute process creation/kill-reap wall-time guarantee. Raw proc results stay root0600 server-only. Prior failed runs untouched.

Before user launch: verify fresh qjhs000 booking/coordination covers the full7200 seconds plus the exact root admission margin; eight GPUs and host memory must be available. Do not stop other users. Read the independent candidate review before execution. Controller rechecks pinned files/resources/outer unit and arms independent guards; server sync is not admission. Do NOT execute the preparer again: the candidate already exists and intentionally refuses overwriting.

To launch ONCE, on qjhs000 with sudo/root, after the fresh booking gate:

```bash
read -rp 'Fresh verified booking expiry epoch (seconds): ' kimi_booking_expiry
sudo /usr/bin/python3 -I - "$kimi_booking_expiry" <<'PY'
import os,sys,time,json,pathlib,hashlib,subprocess,math
assert os.geteuid()==0
expiry=float(sys.argv[1]);assert math.isfinite(expiry) and time.time()+7200<expiry
root=pathlib.Path('/home/boyjiang/kimi-new/ServerlessLLM')
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()=='757eda602a5a99d224e1d7cd353d2ba334744d6f'
assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain=v1','--untracked-files=no']).strip()
s=pathlib.Path('/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104')
p=s/'native-qjhs000-store-stop-diagnostic-manual-root-command-20261106.json'
assert hashlib.sha256(p.read_bytes()).hexdigest()=='2bcf5125732ac61f13f7c464b76595f2bf2b66396e626186e08366c9129c7a41'
v=json.loads(p.read_text());argv=v['argv'];assert argv[-1]=='FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED';argv[-1]=str(expiry)
run='kimi-native-qjhs000-20261106-store-stop-diagnostic-once'
assert not (s.parent/'runs'/run).exists(),'run already used; no retry'
unit=run+'-controller.service'
assert subprocess.check_output(['systemctl','show',unit,'-p','LoadState','--value'],text=True).strip()=='not-found','unit already exists; inspect only'
claim=s/(run+'-user-launch-claim.json')
fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 json.dump({'run_id':run,'epoch':time.time(),'expiry':expiry,'command_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'scope':'one native two-AAB lifecycle Store-stop diagnostic, no automatic retry'},f);f.flush();os.fsync(f.fileno())
print('Starting qjhs000 eight-GPU single diagnostic; full7200-second budget',flush=True)
os.execv(argv[0],argv)
PY
```

Track the SAME controller; never rerun the launch block after an error. Inspect the exclusive claim, unit and run evidence instead. No Git/sync during run and >=60-second release observation. A systemd-run exit0 is only submission, not model/cleanup PASS.

```bash
sudo journalctl -fu kimi-native-qjhs000-20261106-store-stop-diagnostic-once-controller.service
```

Formal evidence: /home/boyjiang/kimi-new/evidence/runs/kimi-native-qjhs000-20261106-store-stop-diagnostic-once . Acceptance requires both cycles' own PID/GPU evidence, correct AAB responses, DELETE/release, final normal Store/API/container exit and guards >=60seconds; retain failure and stop if any fails. Snapshot unavailable does not prove absence of Store blocking. No automatic GPU retry, timer, model conversion or new benchmark.
