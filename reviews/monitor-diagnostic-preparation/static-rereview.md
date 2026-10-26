# Trace verdict: `approve_static_trace`

No execution admission. This approves only the diagnostic trace schema/remediation; it is not R1/R2 and not permission for any node probe.

I re-read the fresh `runtime/active/monitor_control13.py` (175 lines, current working tree) and `test_monitor_trace13.py` (200 lines), plus `REMEDIATION.md`, `cpu-tests.log`, and `trace-regressions-before.log`. I accept the stated provenance: current pushed main `74fe529…`, monitor trace source byte-unchanged since `65e8c73…`; I cannot independently git/hash-verify in read-only mode.

## B1/B2/B3 verified with actual evidence

**B1 — expected/observed birth separated.**  
`monitor_control13.py:124-126` now does:
- `observer.stat` with `start_ticks=None` (unknown at read time) and `expected_start_ticks=registered['start_ticks']`;
- computes `observed_birth` from the returned stat;
- `observer.cgroup` with `start_ticks=observed_birth` and the same `expected_start_ticks`.
`measured()` now carries `expected_start_ticks` in the identity (`:27,:32`). The actual-observer-loop test (`test_monitor_trace13.py:116-135`) simulates registered `100` vs observed `200` and asserts end-event `start_ticks=None`/`expected='100'` for stat, `start_ticks='200'`/`expected='100'` for cgroup, and `observer_states[0]['ok'] is False`. This is the required separation, not the prior misattribution.

**B2 — `event_ns` vs operation `begin_ns`.**  
Begin event now uses `event_ns=` (`:33`); end event keeps `begin_ns=begin`, `end_ns=end`, `duration_ns=end-begin` (`:55-57`). The slow-trace-write test (`test_monitor_trace13.py:81-97`) asserts the begin event has `event_ns` and no `begin_ns`, the end event’s `begin_ns` is after the 6 s begin write, `duration_ns==0`, `begin_trace_io_ns==6e9`, and the next begin’s `previous_trace_io_ns==6e9`. The operation duration is not inflated by recorder I/O; the two keys now have one meaning each.

**B3 — original exception survives secondary trace/counter failure.**  
`measured()` records `action_failure` in the action `except` and wraps the whole final recording/counter block; the nested `except` suppresses a secondary failure only when `action_failure is not None`, otherwise re-raises (`:41-59`). Tests cover:
- action fails + end trace write fails → original `TimeoutError` identity preserved, trace has one pending begin (`:137-149`);
- action succeeds + end trace write fails → `OSError` propagates (`:151-159`);
- action fails + child counter fails → original `TimeoutError` preserved (`:161-172`).
The `error` field is now `type(failure).__name__[:128]`, and `test_error_payload_is_not_copied` proves a 100 k payload is not copied and the trace stays <4096 bytes.

## Other remediation confirmed

- Trace enabled only for `manifest['scope']=='monitor diagnostics only; no model or pool'` (`:78`); the prior G1-scope allowlist is gone. `materialize_control13.validate_scope` rejects forbidden names and rejects the trace flag in non-diagnostic scope (`:11-19`).
- Disabled path remains lazy: `paths=cgroup.rglob(...)`; `list(paths)` only under `if trace_output is not None` (`:92-93`); the actual-scan test `test_disabled_cgroup_scan_remains_lazy` confirms no traversal when disabled.
- Initial `previous_trace_io_ns` is `None`, not `0` (`:14`); test asserts (`:90`).
- 256 MiB bound is checked before each event write; `open('xb')` prevents clobbering; errors/bounds do not substitute zero/empty GPU data.
- The 12 trace tests fail 4F/3E against the immutable `e8bb907` source (`trace-regressions-before.log`) and pass on the current source (`cpu-tests.log` 115 OK; `diagnostic-cpu-tests.log` 116 OK; `diagnostic-operator-tests.log` 12 OK). That is credible regression evidence, not just a green run.

Residual **documented limitations** (not blockers for the trace verdict): `helper.derive` remains one grouped stage; `gpu.foreign-cgroup` has no birth; final end-event recorder latency is not persisted; observer observed birth is only in the following cgroup event (lost if that read fails); `trace_output.close()` is not in a `try/finally` (every event is flushed); parse/convert work outside the timed action can raise after an `ok` read. These are disclosed in `REMEDIATION.md` and are acceptable for this diagnostic pass.

**Trace verdict: `approve_static_trace`.** This does not approve the bounded plan or any execution.

---

# Bounded-plan findings: `changes_required` before admission

The plan’s core scope is right: only `monitor13`/`headservice13a`; CPU-only `headservice13a` (4 CPU/8 GiB/0 GPU, 256 MiB object store); existing CPU import probe reused; no pool/model/cache; monitor unchanged 1 CPU/2 GiB with read-only NVML; operator 180 s and container `/usr/bin/timeout --signal=TERM --kill-after=5s 175s` as separate clocks; SSH loss goes to own cleanup, no redispatch; no new run/window/guard/manifest/deployment; old `d95…` cannot launch the draft. I also accept the readiness correction: ready is written after GPU queries, and the defect is the mid-sample timestamp satisfying freshness while the full sample is 20.54 s; the 5 s threshold and C7 rejection remain; formal 5/4/31 unchanged.

## Confirmed blockers

**P1 — Client-side main SSH invocation is unbounded.**  
`tools/run_control13.py:239-240` calls `subprocess.run(SSH, input=prefix+REMOTE, ..., stdout=stream, stderr=STDOUT)` with **no `timeout`**. The SSH keepalives (`ServerAliveInterval=15`, `ServerAliveCountMax=4`, `:5-6`) detect a dead link, not a hung remote process. The remote `diagnostic_end` (`:39`) bounds only `call` subprocesses; a remote hang in `preflight.check`, `ready()` file reads, `log.write`, or other non-`call` work leaves the client waiting indefinitely, violating “max180 s / no unbounded wait.”  
**Smallest fix:** add a client-side `timeout` to the main `subprocess.run` (e.g., 180 s + margin, or the 900 s admission) and on `subprocess.TimeoutExpired` request exact-owned cleanup and return 1/unknown, never redispatch. Add a transport test that times out the first SSH call and asserts cleanup.

**P2 — 900 s admission is not enforced on the direct launch path.**  
`run_control13.py:95-100` enforces `required={'monitor-diagnostic':900}` at the diagnostic phase entry. But `launch_control13.py:42-43` calls `require_budget(..., diagnostic=True)`, and `budget_control13.py:2-10` gives the diagnostic head `budget=180` and only requires `remaining >= budget+180 = 360`. A direct `launch_control13.py` head launch can therefore pass with ~360 s, not the plan’s 900 s. `test_control13.py:48-49` tests the 360/359.999 boundary only.  
**Smallest fix:** enforce `remaining >= 900` for the diagnostic `headservice13a` in `launch_control13.py` (or add a diagnostic minimum to `budget_control13.py`), and add 899/900 tests. If the operator is the only admitted entry, document that the direct launcher is not independently admissible.

**P3 — `/usr/bin/timeout` availability is still only an open plan item, not a checked precondition.**  
`materialize_control13.py:27-29` prepends `/usr/bin/timeout ... 175s`; nothing verifies the image actually contains it before the head. The plan itself (`BOUNDED-PLAN.md:33`) says R1 must include an actual bounded check before head and that a missing tool must fail container start, never fall back to unbounded Ray. The code’s failure mode is fail-closed (container entrypoint fails / `pid>1` assert fails, client requests cleanup), but there is no pre-head proof/receipt.  
**Smallest fix:** add a bounded pre-head image check under the reviewed new guard (e.g., a short `docker create/start`/`wait` with `/usr/bin/timeout --version` or equivalent), record the receipt, and fail closed. This belongs in the exact R1 plan before containerized work.

**P4 — The “short post-stop monitor interval” is not guaranteed within the 180 s.**  
`run_control13.py:140-145` does `ready()`, `identity()`, `docker stop --time=5`, `identity()`, `ready()`, then writes the result. The post-stop `ready()` may read a ready file produced **before** the stop; there is no wait for a fresh `last_sample_ns` after the stop. The monitor may produce its next post-stop sample only during cleanup, which is charged separately, not within the 180 s the plan says should contain the post-stop interval.  
**Smallest fix:** after `docker stop`, bounded-poll `monitor-v3-ready.json` until `last_sample_ns` advances past the stop time (or one fresh sample) within `diagnostic_end`, then `ready()`/result. Add a fake-ready test that advances `last_sample_ns`.

## Nits (not blockers, but should be fixed or documented)

- **Nested Docker calls lack explicit timeouts.** `launch_control13.py:45-53,94-99` uses `subprocess.check_output` for `docker create/start`, `docker inspect`, and `nvidia-smi` without a timeout. The outer `run_control13.invoke/call` timeout kills the Python wrapper, not necessarily the Docker client child. Add explicit timeouts or process-group kill; the exact-owned cleanup remains the backstop.
- **Container 175 s start/expiry is not separately recorded.** `launch_control13.py:85,105-108` records `dispatch_monotonic_ns`, `docker_create_complete_ns`, and `docker_start_complete_ns`; the `/usr/bin/timeout` process starts at container start, not at `docker_start_complete_ns`. The plan says the clocks are reported separately; the operator/identity records support that conceptually, but the container’s own timeout start/expiry is inferred. Record `State.StartedAt` or a timeout receipt, or state the approximation.
- **Diagnostic SSH-loss main path is not directly tested.** The transport tests (`tools/test_control_transport13.py:28-67`) exercise `service13a`; the diagnostic remote branch tests (`:104-158`) do not call `main` with SSH rc 255. `RECONCILE` (`run_control13.py:174`) correctly raises for `monitor-diagnostic`, and `main` requests cleanup, but add a parameterized `monitor-diagnostic` transport test for the no-redispatch/own-cleanup claim.
- **900 boundary test is missing.** The tests check 360/359.999 (`test_control13.py:48-49`); add 899/900 for the operator and any direct-launch enforcement.
- **Plan source header is stale.** `BOUNDED-PLAN.md:3` says “Source base: main65e8c73”; the current clean pushed main is `74fe529…`, with the monitor trace source byte-unchanged since `65e8c73…`. Update the header to the exact diagnostic-source commit and keep the trace-source provenance note separately; otherwise the exact source review can bind the wrong tree.
- **`prepare` phase launches `monitor13` before the 900 check.** `run_control13.py:82` launches the monitor with the diagnostic budget 180 (via `launch_control13`/`budget_control13`), while the 900 check is only at the `monitor-diagnostic` phase entry (`:95-100`). If the plan requires 900 before any diagnostic allocation, add it to the prepare phase; if the 900 is only the head gate, say so explicitly.
- **Trace `error` is type-only.** This is intentional and tested (`error='TimeoutError'`, no payload), but it loses returncode/errno; note that the operator/main exception still carries the full error. Not a blocker.

## What checks out

The diagnostic scope admits only `monitor13`/`headservice13a` (`materialize_control13.py:11-19`, `budget_control13.py:2-10`), rejects forbidden G1 names before Docker create, and rejects trace in non-diagnostic scope. The `monitor-diagnostic` branch launches only `headservice13a` (`run_control13.py:103-105`), runs only the existing `docker exec` CPU import probe (`:119-134`), never calls `bootstrap/ray/validation/store/cache`, and records only “CPU phase completed; trace and release still require review” (`:147-148`). The operator 180 s and container 175 s+5 s are distinct; SSH rc 255 on the diagnostic path goes to `probe_existing` → `RECONCILE` raises → own cleanup, no redispatch; the 900 s phase-entry check is present. The 116/12 test logs are consistent, and the “before” logs show the new regression tests catch the old behavior.

**Bounded-plan verdict: `changes_required` for P1-P4 before admission.** This is not execution approval, not R1/R2, and not permission for a node probe. The next actual limited CPU diagnostic still needs the exact plan/code/identity/window/independent-guard review before any containerized work; no full-G1 blind rerun. The CPU diagnostic cannot prove the loaded 716 GiB state, solve C7, or explain historical 137; C7 remains failed and formal 5/4/31 unchanged.
