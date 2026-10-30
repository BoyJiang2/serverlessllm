# Bounded monitor diagnostic plan — canonical8

Draft pending actual bound full-runtime R1 and fresh runtime admission. Diagnostic source derives from74fe529; exact new commit accompanies review. Trace recording itself received DeepSeek approve_static_trace against74fe529 (byte-identical since65e8c73). That static verdict does not approve this run or G1. Current manifest52b75d7de761346564491f732fa91ac251d4261deb67cf7d2f6010c3568c248f.

## Purpose and invariant

Locate operations delayed in the actual existing monitor during own CPU Ray-head creation/import/exit. No model, checkpoint/cache payload reads, Store server/pool, CUDA context, inference or formal IDs. Monitor NVML queries remain read-only. No JIT/warmup/compilation. Strict5s evidence criteria, original model/TP/gold/IDs/history remain. A clean short CPU diagnostic cannot prove loaded716GiB behavior or explain C7/historical137. C7 remains failed; formal5success/4failed/31unstarted.

## Exact identity, window and gates

New run sllm-glm51fp8-20261026T161004Z-817be4b6. Single current existing boyjiang booking bk-20261026T1014-d9d26872,21:00Oct26–03:00Oct27; independent new02:50Oct27 guard.03–09 booking also exists, but is not used to extend this diagnostic. Unique qjhs003, canonical /data/brick1/boyjiang/glm5.1-fp8. Latest actual read-only resource/window receipt is current-resource-window.json,00:01Asia/Shanghai; recheck before allocation. OldC7 closed, immutable executed source retained. New guard not installed, no runtime approval claimed.

Freeze/commit/hash/actual full R1 before deployment; actual new root guard/probe and observer registration, physical/boot/UID/GPU/memory/NUMA/localdisk/window checks before containers. Existing conservative1700GiB slice cap and full host-budget preflight retained (cap is not an allocation); no smaller-memory admission claimed. Prepare only creates six empty private cache directories, zero copied compiled artifacts. Every diagnostic monitor/head launch, including direct launcher, requires >=900s remaining, covering180phase+300cleanup+180release+60snapshot+180margin. Guard installation precedes this launch check and has its own bound. No fixed wallclock shortcut.

## Existing entry and allocation limits

Reuse tools/run_control13.py and root_control13.py, materialize/launch/preflight, monitor13 and headservice13a templates. No new monitor/operator/wrapper/source tree. scope exactly `monitor diagnostics only; no model or pool`, monitor_diagnostic_trace=true. Materializer and launcher forbid every identity except monitor13/headservice13a. Operator forbids ray/service phases in diagnostic run. CPU head runc4CPU/8GiB/0GPU,256MiB Ray object-store; monitor existing1CPU/2GiB/NVML only. Diagnostic-derived head argv prefixes /usr/bin/timeout --signal=TERM --kill-after=5s175s. Missing tool fails closed, no unbounded fallback.

Prepare launches verified own monitor under guard, then same-image docker exec timeout --version with10s maximum. Receipt binds run/code/MF/boot/containerID/image/argv/rc/stdoutSHA; before head both operator and direct launcher require receipt and actual same live own monitor/PID/image inspection. No throwaway probe container needed.

## Bounds and evidence

Remote diagnostic phase180s starts at phase entry; every call is capped by original remaining time. Existing headGCS wait45s, CPU imports90s, headstop25s are additionally capped by that same remaining budget. Main client has210s SSH deadline for phase and300s for prepare; TimeoutExpired becomes execution-unknown/owncleanup, never retry/restart. Internal materializer/launcher commands now each15s maximum. SSH255 has one bounded query then diagnostic cleanup, no observe/redispatch. Other G1 reconciliation remains unchanged.

Head175sTERM+5sKILL clock starts inside container after start and differs from phase clock. Existing dispatch/create-complete/start-complete fields bound the startup interval; actual internal timeout origin is not directly observed, so report as configured lifetime and startup bounds, not one shared180s total bound.

After exact headstop acknowledgment, wait within original phase budget for a ready sample whose sample_begin_ns>=stop_ns; stale pre-stop receipt cannot finish CPU phase. Record stop/sample times. The result only means CPU phase completed; raw trace/cadence and complete release require review. Sidecar xb/no-clobber,256MiB cap, begin/end/pending, original errors preserved, no result/cmdline payload copy; resource log retains4GiB cap. Missing end/unknown birth is unavailable, notzero. Exact operation causing C7 delays remains unknown.

After CPU phase, exact own root cleanup stops monitor/head/observers; existing allstopped collector61points>=60s checks baseline8GPUs/no own containers/PIDs and memory/locks/shm/CPU; root snapshot/private-log index and source hashes preserved. No completeG1 accept invoked. Cleanup/release/control costs retained, regardless of diagnostic outcome. No automatic repeated diagnostic.

## Verification and remaining work

117runtime CPU and15operator tests pass,3bash -n checks pass. New four blocker tests against74fe529 fail2F/2E as expected. CPU fixtures explicitly isolate G1 vs diagnostic scope, preserving original G1 mutation rejection. Test a real REMOTE branch with fake APIs, forbidden scope/observe, original budget, stale post-stop sample, client timeout and255 exact-cleanup,900boundary and tool receipt identity/hash corruption. These are CPU tests, not node runtime evidence. Frozen manifest and actual R1 must bind the new source before any probe.

Before later full G1, trace must be disabled under a new G1 identity/manifest/R1. The independently proved ready mid-sample/sticky-history early-fail gap still needs minimal shared validation and all-caller tests; do not reanchor timestamps or relax5s. A diagnostic run alone never promotes C7 to success or authorizes formal IDs. Only after complete actual G1, normal-flags checks, R2 and formal admission may store_pool_hit_03 begin.
