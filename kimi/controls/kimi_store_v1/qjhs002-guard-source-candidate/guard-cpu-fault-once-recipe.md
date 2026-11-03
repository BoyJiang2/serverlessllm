# One CPU fault injection — command recipe for independent review only

**Do not run this document automatically.** Root reviews/executes once after exact guard source approval. Guard deployed SHA: `48c3729fb1d6137f3bd44bc3c87ffe18da40dd101b5afaf280ed2f794910b59d`; do not use earlier drafts. Only active-root guard is used. No GPU, NVIDIA/CDI hook, model, Store, weights, Docker API, shared-cgroup change, or modification of an existing CPU probe.

## Prepare everything before starting the ARM10 clock

Run as root in the already authorized CPU window. Pick one fresh run directory and full64-hex fixture CID; never reuse any historical container ID/evidence. This is synthetic scratch-runc, not a Docker-created container. Existing installed runc and the prior scratch CPU rootfs construction are reused; no environment/image/checkouts are created.

```bash
RUN=kimi-guard-fault-cpu-20261104-review-once
BASE=/data/brick1/boyjiang/kimi-k2.6/p0/$RUN
CID=$(printf '%s' "$RUN" | sha256sum | cut -d' ' -f1)
BUNDLE=$BASE/$CID
RUNC_ROOT=$BASE/runc-state
GUARD=/data/brick1/boyjiang/kimi-new/ServerlessLLM/sllm_store/tests/guard_cuda_micro.py
GUARD_UNIT=$RUN-guard.service
PARENT_UNIT=$RUN-launcher.service
SCOPE=docker-$CID.scope
[ "$(id -u)" = 0 ] && [ ! -e "$BASE" ]
mkdir -m 700 "$BASE"
mkdir -m 700 "$BUNDLE" "$RUNC_ROOT" "$BUNDLE/rootfs"
mkdir "$BUNDLE/rootfs/dev" "$BUNDLE/rootfs/proc" "$BUNDLE/rootfs/sys"
sha256sum "$GUARD" /usr/bin/runc > "$BASE/prepared-source.sha256"
```

Before ARM, construct/review `<BUNDLE>/config.json` using the previous small scratch config shape, with these **exact differences**. Its parent directory is the full CID, which the guard verifies. Root filesystem is only the newly empty scratch directory with dev/proc/sys; app stays `/__cpu_probe_no_app__`.

- `linux.cgroupsPath = "system.slice:docker:<CID>"`, not a filesystem path.
- Resources: CPU quota100000/period100000; memory.limit=2147483648, memory.swap=2147483648; pids.limit=512. Network namespace remains isolated, devices empty, no NVIDIA env/device/mount.
- One `createContainer` hook only: host `/bin/sh`, args `['sh','-c', <below>]`, env empty, timeout90. Source text substitutes the already reviewed absolute BASE path; no other shell interpolation. It writes only its exclusive run marker and then sleeps, ignoring TERM:

```bash
trap '' TERM
printf 'CPU_HOOK_ENTERED\n' > /ABSOLUTE_THIS_BASE/hook-entered
while :; do /bin/sleep 1; done
```

- Native leaf fallback annotations (CPU fixture only): RuntimeMaxUSec=`uint64 40000000`, TimeoutStopUSec=`uint64 3000000`, KillMode=`'control-group'`, SendSIGKILL=`true`, FinalKillSignal=`int32 9`, all under `org.systemd.property.`. No annotation change to the actual GPU profile.

Save the reviewed config/source/SHA before ARM. Precompute both following native service commands and handshake controller inputs before ARM; do not spend ARM10 constructing files, looking up versions or reviewing source. Confirm no currently matching CID runtime exists. Guard ARM will independently reject it if present.

## Arm guard, atomically GO, then launch one runc

Guard service native140/stop3 contains only this guard, not an assumed parent containment of daemon processes. Suggested guard service resource ceiling: CPU1/memory256MiB/tasks64; no Docker/nvidia-smi called inside it. Its stdout/stderr and raw OCI remain in this exclusive directory.

```bash
systemd-run --unit="$GUARD_UNIT" --property=Type=exec --property=Restart=no \
  --property=RuntimeMaxSec=140 --property=TimeoutStopSec=3 \
  --property=KillMode=control-group --property=SendSIGKILL=yes \
  --property=FinalKillSignal=SIGKILL --property=CPUQuota=100% \
  --property=MemoryMax=256M --property=TasksMax=64 \
  --property="StandardOutput=append:$BASE/guard-unit.log" \
  --property="StandardError=append:$BASE/guard-unit.log" \
  /usr/bin/python3 "$GUARD" "$CID" "$BASE" \
  --oci "$BUNDLE/config.json" --runc-root "$RUNC_ROOT" \
  --deadline 25 --kill-after 3 --overall 65
```

Controller: wait for complete atomic ARMED.json; verify full CID, guard PID/startticks, nonce and native service properties. Publish GO.json atomically with `{cid,nonce,monotonic: time.monotonic()}` from this host. Wait for matching GO-ACK. Root's source-approved controller uses exclusive temp0600 + fsync + rename; do not echo partially written JSON into GO.json. No target starts before ACK. Abort without launching if ARM10 expires, GO becomes stale, guard exits, identity differs or ABORT exists.

Then launch the prepared parent unit **without --wait**, so the controller remains available to inspect and inject:

```bash
systemd-run --unit="$PARENT_UNIT" --property=Type=exec --property=Restart=no \
  --property=RuntimeMaxSec=40 --property=TimeoutStopSec=3 \
  --property=KillMode=control-group --property=SendSIGKILL=yes \
  --property=FinalKillSignal=SIGKILL --property=CPUQuota=100% \
  --property=MemoryMax=3G --property=TasksMax=1024 \
  --property="StandardOutput=append:$BASE/runc.stdout" \
  --property="StandardError=append:$BASE/runc.stderr" \
  /usr/bin/runc --systemd-cgroup --root "$RUNC_ROOT" \
  --log "$BASE/runc-log.json" --log-format json create \
  --bundle "$BUNDLE" "$CID"
```

No shim is forged or simulated. The real external runtime under test is this actual /usr/bin/runc process; its own --root is inside BASE and its final argv token is CID. The native parent40 fallback and native leaf40 fallback are independent of guard25/28, but a test depending on either fallback is a **guard failure**, not a pass.

## Before SIGSTOP: prove entry, exact identity and native remaining window

Wait only a short bounded startup interval, ending strictly before GO+20. Require hook-entered marker **and** the guard's actual raw OCI SHA/selected cgroup identity/scope inode/runtime-pinned evidence. Capture:

```bash
systemctl show "$GUARD_UNIT" "$PARENT_UNIT" "$SCOPE" --no-pager \
  --property=Id,ActiveState,SubState,MainPID,ControlGroup,ActiveEnterTimestampMonotonic,RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL,FinalKillSignal \
  > "$BASE/pre-injection-unit-properties.txt"
```

Review/verify parent and scope are active; both report RuntimeMaxUSec40s/TimeoutStopUSec3s, control-group kill, SendSIGKILL=yes, FinalKillSignal9. Convert both positive ActiveEnterTimestampMonotonic values from microseconds to host CLOCK_MONOTONIC seconds. Let `T_GO` be the acknowledged GO timestamp, `T_parent`, `T_scope` their active-enter times, and `now=time.monotonic()`. Require:

```
now < T_GO + 20
T_GO + 28 + 3 < min(T_parent + 40, T_scope + 40)
```

The additional3s margin keeps the intended guard KILL before either native fallback. Capture actual cgroup.events populated1, hook/init member PIDs, own scope cgroup.kill existence, and the external runc PID/startticks/exe/UID0/--root/finalCID/cgroup outside the leaf scope. Do not infer these from a unit name alone. The guard must already hold that runc identity's pidfd before injection.

Only then controller `pidfd_open(runcPID)`, rechecks the same exact identity after opening, and sends `signal.pidfd_send_signal(fd,SIGSTOP)`. Save the injected target identity and monotonic time. It is a **single PID**, not killpg or a unit/cgroup stop. Do not SIGCONT to help cleanup; the runtime KILL28 must terminate the intentionally stopped survivor.

If any property, monotonic origin, remaining time, scope identity, pidfd binding or readiness is unknown, **do not inject**. Atomically create this run's ABORT file and let the guard kill only its already proven own scope/pinned runtime. Preserve that as a failed/uninjected attempt. Do not widen targets or synthesize an identity to proceed.

## Observe and classify once

Expected discriminating sequence: own scope cgroup.kill around GO+25 removes the TERM-resistant hook/init; pidfd TERM25/KILL28 removes the stopped actual runc outside that scope. Actions must precede the native40 fallbacks. Retain actual kernel/cgroup snapshots, raw OCI remotely0600, all source hashes, event times, pin/inode identities and both units' final properties. No Docker API cleanup is permitted as the mechanism under test.

Cleanup cutoff GO+65 is separate from at least60 continuous seconds after verified empty/removed own scope and every exact PID/starttick absent; the guard's own native lifetime is140 and the full plan is180. Late actors reset release observation and are handled within these fixed budgets. Any target surviving, dependency on native40/manual cleanup, missing actual OCI, wrong identities/limits, signal/write failure or incomplete60 release is FAILED_RETAINED. If fallback40 fires, its evidence is retained; do not repeat the run under a new ID to convert failure into success.

No GPU observer is exercised here. Future GPU resource observer must retain the existing fixed UUID and2048MiB stop rule, with its own bounded CLI timeout and own-run ABORT channel. A CPU pass is evidence for this kernel recovery mechanism only; a separately reviewed fresh512 GPU microprobe still needs all booking, budget, scope, source and independent-resource-observer gates.
