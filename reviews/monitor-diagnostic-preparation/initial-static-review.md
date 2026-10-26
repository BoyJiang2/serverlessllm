# Supplemental static trace review — verdict: `changes_required`

No execution admission. This is not R1/R2 and not permission for any node probe, containerized or otherwise. C7 remains failed; native 32-rank/12-response/four-idle components are component results only; formal 5/4/31 unchanged. The source package is intentionally unsealed; the old `d95…` manifest cannot launch this draft, and I cannot re-hash the remote executed source in read-only mode.

## Corrected prior wording

I withdraw the earlier “data appears in ready before it exists” wording. The fresh code and `readiness-predicate-proof.json` are right: the ready file is published **after** the post-GPU row fields are collected. The defect is narrower: `last_sample_ns` is the **mid-sample/pre-GPU** timestamp C, so at the post-collection evaluation point (`now = F`) the CPU predicate `0 <= now - last_sample_ns <= 5 s` is **true at age 4.261915198 s**, while the whole sample `sample_end_ns - sample_begin_ns` is **20.541801965 s** and the strict 5 s sample-duration gate is **false**. The ready file also carries no sticky history of earlier invalid rows. This is a fail-early readiness gap, not permission to accept C7. Do **not** re-anchor to F and do **not** loosen the 5 s/sample/budget standards; the future correction is an additional completed-duration/sticky-invalid predicate, which is outside this trace review.

## Verified as sound

- The disabled path is real: `trace_output is None` makes `measured()` call `action()` directly (line 30), opens no sidecar, imports no `resource`, and increments no sequence. The only disabled-path behavior change I found is that `cgroup.rglob('cgroup.procs')` is now materialized by `list(...)` (line 89) instead of iterated lazily; that changes internal ordering/memory slightly but not the row/ready timestamps, 5 s predicates, budgets, or acceptance. Flag it as a documented instrumentation change, not a security change.
- Row/ready timestamps and gates are unchanged: `monotonic_ns` (line 125), `sample_begin_ns` (85), the two `sample_end_ns` assignments (136/153), and the ready payload keys (161) match the prior monitor; `accept_control13.monitor_summary` still checks `begin <= now <= end`, `end-begin <= 5e9`, begin-to-begin, and C-to-C (lines 42-51) unchanged. The 109 CPU + 9 operator logs are consistent, including the six new trace tests and the `-O` rejection.
- Trace bounds are mostly correct: `open('xb')` (line 76) prevents clobbering; the 256 MiB check (21-22) is before each write; each event is one compact JSON line; no partial event is written; `flush()` is per event. No hidden error-to-zero in the recorder: errors use `outcome='error'`, `error=repr(...)`, and `None` end/timestamps; `child_cpu_ns` is `None` when not measured; missing PIDs/`start_ticks` are `None`, not `0`; a failed `nvidia-smi` re-raises rather than producing empty GPU data. The baseline (max ~0.51 ms `memory.stat`, ~0.18 ms `import.gpu_identity13`, all others <1 ms) is correctly labeled “not reproduction/exoneration.”
- IO provenance is mostly separated: `duration_ns` brackets only the action (begin after the begin-event write, end before the end-event write); `begin_trace_io_ns` is the begin-event write latency; the end-event write latency appears as the next event’s `previous_trace_io_ns`; `thread_cpu_ns`/`child_cpu_ns` are separate. The tests exercise this.

## Blockers (confirmed bugs/misattribution)

**B1 — Observer PID birth is misattributed (PID-birth provenance).**  
Lines 119-120 pass `start_ticks=registered['start_ticks']` into `measured('observer.stat'/'observer.cgroup')`. That is the **registered/expected** birth, not the birth observed by the read. If an observer PID was reused, the trace would record the old expected birth as if observed, defeating the “PID birth known/unknown” check. The row’s `same_start` is still correct, but the diagnostic trace is misleading.  
**Smallest correction:** add `expected_start_ticks=None` to `measured()`’s identity; record `observer.stat` with `start_ticks=None, expected_start_ticks=registered['start_ticks']`; compute the observed birth from the stat text and pass it as `start_ticks` on `observer.cgroup`. Add one test for observed ≠ registered. This is a small signature/line change, not a framework.

**B2 — Begin-event `begin_ns` and end-event `begin_ns` are different quantities under the same key (IO-timing provenance).**  
The begin event’s `begin_ns` (line 33) is captured **before** the begin trace write; the end event’s `begin_ns` (line 53) is the operation begin **after** that write. A downstream join computing `end.end_ns - begin.begin_ns` would overcount by the begin trace-write latency, which is exactly the kind of misattribution this trace is meant to prevent. `duration_ns` is correct, but the schema is ambiguous.  
**Smallest correction:** rename the begin event’s field to `event_ns` (or set it to `None` and expose a single `operation_begin_ns` consistently on both events); keep `duration_ns` authoritative. The existing tests do not read `begin['begin_ns']`, so this is a one-line change plus one assertion.

**B3 — The end-event trace write can replace the action’s original error (error propagation edge).**  
In the `finally` (lines 47-55), `trace_event(end)` can raise the 256 MiB `RuntimeError` or an I/O error after `action()` has already raised. That new exception replaces the action’s exception, violating “missing/action errors propagate.” The begin-event budget failure is correctly tested and fails before the action; the end-event case is not.  
**Smallest correction:** remember the active action failure and wrap the end `trace_event` in a nested `try/except` that re-raises the trace failure only when there is no action failure; add a test with a stream that fails on the second write and assert the original action exception survives. Low likelihood at 256 MiB, but it is a confirmed code path.

## Nits / clarifications

- **Trace scope allowlist:** line 74 permits `'G1 independent untimed controls only; zero formal IDs'` as well as the diagnostic scope. The current `run-manifest.json` has this G1 scope and no flag, so nothing is enabled today. If “nonformal” means diagnostic-only, restrict the allowlist to the monitor-diagnostics scope or obtain explicit approval for the G1-scope entry; otherwise a future G1-scope run could enable 256 MiB of trace I/O.
- **Unbounded `error=repr(failure)`:** a `JSONDecodeError`/`TypeError` repr can embed a document/object, and the budget check happens after `json.dumps`. Cap `error` (e.g., type name + first 512-1024 chars) to keep records bounded and avoid accidentally copying payloads.
- **`helper.derive` conflates** two `read_text`+`json.loads` calls and `store_identity`. If it is the slow stage, the trace cannot say whether the file read, JSON parse, or derivation blocked. Split into two/three `measured` calls only if the helper stage is slow.
- **`gpu.foreign-cgroup` has no birth:** `start_ticks` is honestly absent (`None`). If birth is required for foreign GPU PIDs, add a best-effort `proc.stat` read; otherwise document the limitation.
- **First-event sentinel:** `previous_trace_io_ns=0` on sequence 1 is a “no previous write” sentinel, not a measured zero. Consider `None` to avoid the same “never zero” ambiguity.
- **End-write latency of the last event** is only in the unpersisted global `trace_io_ns`; every intermediate end-write latency appears in the next event’s `previous_trace_io_ns`. Document this, or add a small final flush/record.
- **No `try/finally` around `trace_output.close()`** on loop exception. Every event is flushed, so data survival is fine; the fd is closed by process exit. Optional cleanup.
- **Parse/convert steps outside the timed action** (`int`, `split`, `Uid` extraction, `birth` indexing) can raise after the read was traced `ok`, crashing the monitor without an `error` event. Minor; the read duration is still captured.
- **Test coverage gap:** the six tests exercise the AST-extracted recorder and synthetic `monitor_summary`, not the live loop. A small static assertion that the expected operation names are wrapped, and a disabled-path equivalence check, would catch a missed site without adding a secondary monitor.
- **Evidence retention:** the sidecar is not referenced by the current acceptance/evidence index. The next diagnostic’s plan must include it (or explicitly mark it diagnostic-only) and preserve it through cleanup; it is unbounded by the main 4 GiB/row budgets except its own 256 MiB.

## Smallest correction summary

1. Fix observer birth attribution (`start_ticks=None` + `expected_start_ticks`; observed birth on the follow-up event).
2. Rename the begin event’s timestamp to `event_ns` so begin/end keys mean one thing; keep `duration_ns` authoritative.
3. Preserve the action exception when the end trace write fails.
4. Optional nits: cap `error`, restrict/confirm the G1-scope allowlist, split `helper.derive`, add the static “all sites wrapped” test.

No generic framework or secondary monitor is needed. After these fixes and a fresh source/manifest identity, the next actual limited CPU diagnostic still needs the exact plan/code/identity/window/independent-guard review before any containerized work; no full-G1 blind rerun.
