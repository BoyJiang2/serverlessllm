# User-run CPU-only genuine extension build (not GPU retry)

Source794a0f3b08185d0ec7d054480922b2a5c5e24505 on qjhs000 existing checkout, published exactGitHub source ref. Build only _checkpoint_store using existing imageec5 and official CMake; no Store constructor, model, weights or real GPU exposure. CPU4cores/memory64GiB/PIDs512, networknone/rootfsRO/runc/noDeviceRequests. Full1260seconds includes >=60seconds release observation. No automaticretry; retain old ELFcb885 and old failednative run.

Before executing, read the new CPU-candidate independent review, verify a fresh CPU/whole-node booking or explicit coordinated window covers all1260seconds, and verify CPU/memory capacity. An idle GPU or an old GPU booking is not a CPU admission receipt. This document supplies the reviewed launch form; it does not assert fresh admission. No Git/sync during formalbuild/release.

```bash
read -rp 'Fresh verified CPU booking expiry epoch (seconds): ' kimi_cpu_expiry
sudo /usr/bin/python3 -I - "$kimi_cpu_expiry" <<'PY'
import os,sys,time,json,pathlib,hashlib,subprocess,math
assert os.geteuid()==0
expiry=float(sys.argv[1]);assert math.isfinite(expiry) and time.time()+1260<expiry
root=pathlib.Path('/home/boyjiang/kimi-new/ServerlessLLM')
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()=='794a0f3b08185d0ec7d054480922b2a5c5e24505'
assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain=v1','--untracked-files=no']).strip()
s=pathlib.Path('/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104')
p=s/'pinned-pool-shutdown-progress-official-build-cpu-manual-root-command-20261106.json'
assert hashlib.sha256(p.read_bytes()).hexdigest()=='b120073fbf2a498e0e73561a357350ae33492c2bbb0019fe88481e9ad77d7eff'
v=json.loads(p.read_text());argv=v['argv'];assert argv[-1]=='FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED';argv[-1]=str(expiry)
run='kimi-pool-shutdown-progress-build-cpu-20261106-once'
assert not (s.parent/'runs'/run).exists(),'run already used; inspect only'
assert not (root/'sllm_store/build/kimi-pool-shutdown-progress-v1').exists(),'output already exists; inspect only'
assert subprocess.check_output(['systemctl','show',run+'-controller.service','-p','LoadState','--value'],text=True).strip()=='not-found'
claim=s/(run+'-user-launch-claim.json');fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 json.dump({'run_id':run,'epoch':time.time(),'expiry':expiry,'command_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'scope':'one CPU-only genuine extension build and ABI import; no Store/GPU/model constructor or automaticretry'},f);f.flush();os.fsync(f.fileno())
os.execv(argv[0],argv)
PY
```

Submissionexit0 is not buildPASS. Monitor only this same controller:

```bash
sudo journalctl -fu kimi-pool-shutdown-progress-build-cpu-20261106-once-controller.service
```

Output: /home/boyjiang/kimi-new/ServerlessLLM/sllm_store/build/kimi-pool-shutdown-progress-v1 . Evidence: /home/boyjiang/kimi-new/evidence/runs/kimi-pool-shutdown-progress-build-cpu-20261106-once . Must inspect genuine artifact manifest/ABI/noCUDAinit and guard normalexit/release>=60 before any new nativecandidate repin. Do NOT run previousnativecommand again; its identity is used and oldELF does not contain newlogging. A new GPU retry needs separate reviewed candidate, fresh fullresource/window and failure-stop admission.
