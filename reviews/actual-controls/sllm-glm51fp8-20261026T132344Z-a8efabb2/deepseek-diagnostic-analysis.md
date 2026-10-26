# Read-only diagnostic analysis — no execution/R1 approval

Scope read: the four review artifacts; `runtime/active/{monitor,bootstrap,supervise,control_watchdog,accept,gpu_identity}13.py`; supporting `workspace.py`, `compilation_guard.py`, `resource_window_control13.py`, `budget_control13.py`, `materialize_control13.py`, `preflight_control13.py`, `tools/run_control13.py`, `monitor13-template.json`, and the three relevant test files. No credentials, private kernel logs, network, shell, writes, or experiments. I assume `runtime/active` is the executed source as stated; I cannot re-hash the remote `executed-source-a7c5a2f` from here.

## 1. Confirmed facts

**Run result.** Native correctness is a component result, not G1: two formats × two cycles, 32 rank records, 12 gold responses, four 60s idle observations; standalone raw recomputation passed. Complete G1 remains failed because the unchanged `accept_control13` returned 1 at `supervisor failure` (`accept-rejected-stderr.log:4-8`), before it ever reached `monitor_summary`. Cleanup, 61-point/75.7569s release, root index, and source preservation passed. Formal counts stay 5/4/31; historical 137 remains an unknown, separate event.

**Two slow monitor samples** (`outcome.json:55-103`):

| sample | begin A | row timestamp C | end F | A→C | C→F | A→F |
|---|---|---|---|---|---|---|
| early | 146885422339049 | 146901702225816 | 146905964141014 | **16.279886767 s** | 4.261915198 s | 20.541801965 s |
| final | 149353687637042 | 149353690368107 | 149363776468860 | **0.002731065 s** | **10.086100753 s** | 10.088831818 s |

Max C→C gap is 17.60155992 s, dominated by the early sample's pre-C block. Early sample end 146905964141014 is before GPU worker dispatch 146913697437674. No partial rows (2391 complete).

**Exact source/field ordering in `monitor_control13.py`** — the critical boundary is `row.monotonic_ns` (C), taken at line 72 before all post-C reads:

| region | operations | lines |
|---|---|---|
| A | `sample_begin_ns = monotonic_ns()` | 34 |
| A→C | `memory.current`; `cgroup.rglob('cgroup.procs')`+reads; per-PID `/proc/<pid>/{status,cgroup,stat,cmdline,ns/pid}`; per-observer `/proc/<pid>/{stat,cgroup}`; `utc`; then C | 35-72 |
| C→F | `memory.peak`, `cpu.stat`, `memory.stat`, `pids.current`, `memory.events.local`, `memory.events`, `/proc/meminfo`, node `meminfo`, `nvidia-smi --query-gpu`, `nvidia-smi --query-compute-apps`, first `sample_end` D, `observed_interference`, foreign `/proc/<pid>/cgroup`, lazy `from gpu_identity13 import store_identity`, `store_identity`, final `sample_end` F (overwrites D) | 71-100 |
| after F | `json.dumps`+`'\n'`, 4GiB budget check, `output.write`, `output.flush`, ready `write_text`, ready `replace`, interference append, `sleep(1)` | 101-113 |

So: the early 16.28 s is inside A→C and cannot be any GPU query. The final 10.086 s is inside C→F (fields/GPU/helper), not the publication tail. The final `sample_end_ns` F is before JSON encoding, flush, ready write, and rename. The first D at line 83 is dead, overwritten at line 100.

**Ready-file anchor and consumers.** `monitor-v3-ready.json` publishes `last_sample_ns = row['monotonic_ns']` (C, line 108) — a timestamp taken *before* the GPU data it accompanies. The supervisor (`supervise_control13.py:18`), preflight (`preflight_control13.py:86`), and host driver (`tools/run_control13.py:64`) all test `0 <= now - last_sample_ns <= 5 s`. The monitor is a separate container launched in `prepare` (`tools/run_control13.py:74-79`, `monitor13-template.json`: 1 CPU/2 GiB), while `service13a` supervises `bootstrap_control13.py`. The monitor therefore continued after the supervisor failed: supervisor end 149357499866561 is 3.81 s after the final sample began and 6.28 s before it ended. The timing is consistent with the supervisor tripping on the **previous** sample's C (149352439214735 + 5 s = 149357439214735; recorded end 149357499866561, ~60 ms later), while the final sample's ready file was still unpublished. That exact instant is not logged; it is a timing inference, not a source proof.

**Observer liveness.** `observers_ok` is a one-instant `/proc` check per sample (`monitor_control13.py:62-70`), copied into the ready file. It does not prove continuous liveness across a 16-20 s gap. The early sample's `gpu_pids` is empty; the final sample's is empty and the ready tail shows `registered_store_helper: null`, so the Store helper was already gone during the final post-C work.

## 2. Facts vs hypotheses

**Facts:** two independent 5 s violations (early 20.54 s, final 10.09 s); the early block is pre-GPU; the final block is post-C and pre-encode/flush/ready-write; sampled observer flags are not continuous; the ready anchor is pre-GPU; the supervisor error is generic.

**Hypotheses, ranked, with confirming evidence:**

- **H1 (early, strongest): CPU scheduling starvation of the 1-CPU monitor container.** Confirm if the locator shows a large *inter-op gap* with no single stage spanning 16 s, and low `thread_time_ns`/wall ratio. Refute if one cgroup/proc stage spans the gap.
- **H2 (early): cgroup/proc read block.** Candidates: `cgroup.rglob('cgroup.procs')` over a large run-slice subtree; `/proc/<pid>/status|cmdline` on a D-state/exiting PID; observer reads. Confirm by per-stage wall time, PID, `start_ticks`, per-file split, and child/thread CPU.
- **H3 (final): `nvidia-smi` blocked on the driver during Store CUDA-context/teardown.** Confirm if a `gpu.query-*` stage spans most of the 10 s with near-zero child CPU, helper already gone, and the teardown window overlapping. This is the strongest GPU hypothesis, but not proved.
- **H4 (final): proc reads of exiting Ray/Store processes or CPU starvation.** Confirm by `proc.pid`/observer stage spans or inter-op gaps.
- **H5 (final publication tail): likely small.** Tail arithmetic: next C is 11.089 s after final C, F is 10.086 s after C, leaving ≈1.003 s for F→next C including the fixed 1 s sleep. So encode/write/flush/ready-write/rename are probably milliseconds here, but they are not yet directly timed. The user's point stands: an async heartbeat cannot substitute for missing GPU data.
- **H6: import/helper derivation.** Likely cheap and, for the early block, impossible (the lazy import is post-C). Rule out with the import probe.
- **Not established:** exact blocking syscall; NVML-only causation; continuous observer liveness. Do not reclassify C7.

## 3. Confirmed defects (source-level, not causal)

1. **Anchor mismatch.** Ready `last_sample_ns` = pre-GPU C, while `gpu`, `gpu_processes`, `gpu_pids`, and helper are collected after C. The heartbeat can look fresh before GPU data exists, and can look stale just after correct GPU data completes.
2. **Admission blind spot.** Row `sample_end_ns` F excludes JSON encode, budget check, write, flush, ready write/rename. `accept_control13.py:42` bounds only A→F; begin-to-begin (`:44`) covers the next cycle but does not bound or localize the publication path.
3. **No per-operation localization.** The retained row has only aggregate A/C/F and no stage durations or gaps, so the exact stall is unrecoverable from the 2391-row log.
4. **Observer liveness is point-sampled**, not continuous; a gap can hide observer death.
5. **Supervisor failure is generic** (`supervise_control13.py:18-19`), so the failing condition and staleness were not recorded.
6. **Hot-loop lazy import/helper/nvidia-smi costs are unmeasured**; the first `gpu_identity13` import, `store_identity`, `observed_interference`, and two `nvidia-smi` subprocesses sit on the sample path.

## 4. Minimal exact-file edits (proposal only)

**`runtime/active/monitor_control13.py`**
- Add one local recorder before the loop: `_record(stages, op, begin_ns, end_ns=None, outcome='ok', **extra)` returning the end time. `end_ns=None` means unknown, never 0.
- Instrument A→C: `cgroup.memory.current`; `cgroup.procs.scan` with `visited`/`missing`; per-PID `proc.pid` with `pid`, `start_ticks` (from the existing `stat` read), `namespace_pid`, per-file ns in `files`, and outcome `ok|excluded|anomaly|missing|error:<repr>`; per-observer `observer` with `unit`, `pid`, `start_ticks`, `files`, outcome. Use `try/finally` so `continue`/exceptions still record.
- Take `utc` then `row_timestamp_ns = monotonic_ns()` explicitly (preserve current order), do the post-C reads, then build the row.
- Instrument C→F individually: `cgroup.memory.peak`, `cgroup.cpu.stat`, `cgroup.memory.stat`, `cgroup.pids.current`, `cgroup.memory.events.local`, `cgroup.memory.events`, `proc.meminfo`, `sys.node.meminfo`, `gpu.query-gpu`, `gpu.query-compute-apps`, `gpu.foreign-cgroup` (pid/start_ticks), `import.gpu_identity13`, `helper.derive`, `observed_interference`.
- Record `gpu.query-*` child CPU with `resource.getrusage(RUSAGE_CHILDREN)` before/after if the reviewer wants blocking-vs-CPU proof; wall time alone is insufficient.
- At F: `sample_end_ns=monotonic_ns()`; add `row_timestamp_ns`, `stages`, and `field_errors` (if any op failed).
- Instrument the tail after F: `json.encode`, `log.budget`, `log.write`, `log.flush`, `ready.write`, `ready.rename`, `interference.append`, `sleep`; carry them into the next row as `previous_publication` (no new file; final-sample caveat: if the monitor is killed before the next row, the last tail is lost).
- Ready payload: keep existing keys and values exactly (especially `last_sample_ns`); add `sample_end_ns`, `row_timestamp_ns`, `publication_begin_ns`, `encode_end_ns`, `log_write_end_ns`, `log_flush_end_ns`, `ready_write_begin_ns/end_ns`, `ready_rename_end_ns`, `previous_publication`. Do **not** switch the heartbeat anchor to `sample_end_ns` in this diagnostic.
- Fail-closed: if an op errors, record `outcome='error:...'` and the field as `None`/error text; write the row so evidence survives; `accept` must reject any `field_errors`. Do not zero-fill, and do not silently continue as if valid.

**`runtime/active/accept_control13.py`**
- Add a pure `stage_report(row)` (no raise) returning `status` (`ok|unknown`), `max_op`, `max_ns` (or `None`), `max_region` (`pre|post|publication|spanning`), `max_gap` with `prev_op`/`next_op`, and `unknown` entries. Unknown/`None` timings are reported as unknown, never 0.
- Use it unchanged in the existing `require(begin<=now<=end and end-begin<=5e9, ...)` and begin-to-begin/gap messages, e.g. append `json.dumps(stage_report(row))`, so the failure names the stage or the inter-op gap. Keep all thresholds, `max_gap<=5`, `count>2`, coverage, and `last_ns>=max(ends)` exactly as-is.
- Add `field_errors` rejection if the monitor adopts fail-closed recording. Return `stage_report`s in the summary for diagnosis.
- Do not relax or re-anchor `monitor_summary`; the old log without stages must report `unknown`, not pass.

**`runtime/active/supervise_control13.py`**
- Replace the single generic `if ... raise` (lines 18-19) with named problem accumulation (`run_id`, `boot_id`, `observers_ok`, `foreign_gpu_pids`, `stale_ns`) and include `stale_ns`, `now_ns`, and the relevant ready snapshot in the message. Keep the 5 s condition and all other conditions identical. This is the smallest edit that would have told us which heartbeat condition failed.

**Tests (reuse existing files; no new test tree)**
- `test_accept_control13.py`: fake-delay matrix — inject a 6 s delay into each stage name and assert `stage_report` names exactly that op and `monitor_summary` still raises; assert an unknown/error stage is reported as unknown with `max_ns is None`, never 0; assert exactly 5.000000000 s still passes and 5.000000001 s fails; assert `begin>now` and `now>end` still fail; assert a `previous_publication` slow `ready.rename` is located; assert `pid`/`start_ticks` are preserved and missing/None is not coerced to `0`.
- `test_gpu_identity13.py`: add a synthetic row where `helper.derive` is slow and `gpu.query-*` is fast, and the inverse, to prove the locator does not default to NVML; keep the existing helper/receipt cases.
- Optional `test_evidence13.py`/`test_runtime_imports.py`: no change needed.

**Avoid:** new wrapper scripts, new candidate trees, async heartbeat, moving `last_sample_ns` to F, raising/zero-filling thresholds, or reading/loading checkpoint/cache/model.

## 5. Smallest bounded diagnostic sequence

1. **Step 1 — CPU-only, own-process/import probe (first).** Single reviewed, bounded (≤60 s) run on the Linux side, no new container unless unavoidable; if containerized, it needs an independent new guard/current-resource check/window. Spawn only own trivial CPU children (sleeping, busy, short-lived/zombie, exit race); time the exact A→C read order (`/proc/<pid>/{status,cgroup,stat,cmdline,ns/pid}`, `/proc/meminfo`, node meminfo, readable `cgroup.procs` scan/`memory.current`), and time imports including the lazy `gpu_identity13` import. No GPU, Store, model, checkpoint, cache, or network. It establishes baseline per-op/import costs and validates the stage schema; it cannot reproduce a loaded-node 16 s stall, and should say so.
2. **Step 2 — only if Step 1 exonerates the trivial reads/imports.** Separately reviewed, bounded CUDA-context or Store-teardown component probe (no checkpoint/full-cache/model load), to measure `nvidia-smi` latency during context destruction and `/proc` reads of the exiting Store helper. A new node container probe requires a fresh independent guard, current-resource check, and window.
3. **Step 3 — actual next R1 only after Step 1/2 evidence.** Instrumented monitor in a bounded run/scope; not a full G1. The monitor remains evidence-producing; the full G1 gate stays failed until the cause is fixed and revalidated under the unchanged criteria.

## 6. Evidence needed before actual next R1

- For the early sample: the exact stage spanning 16.28 s, **or** an inter-op gap proving descheduling; if a proc read, the PID and `start_ticks`; whether it repeats.
- For the final sample: which C→F stage (fields/NVML/helper/proc) consumes ~10 s; wall vs child/thread CPU; whether the Store teardown overlaps; the measured encode/write/flush/ready-write/rename tail; `last_sample_ns` vs `sample_end_ns` vs publication time.
- The specific supervisor heartbeat condition and staleness (from the improved error message), not the generic message.
- A repeatable CPU baseline showing normal per-op/import costs; unknown/error stages preserved as unknown.
- If GPU/teardown is implicated, the bounded CUDA/Store probe result.
- Reviewer sign-off on the exact next-run scope, fresh guard, current resources, and window; all 5 s/budget/model/gold/cache/ID criteria unchanged.

## 7. Boundaries

No execution, R1, R2, or formal approval is given by this analysis. Do not reclassify C7; do not loosen 5 s/budgets/model/gold/cache/ID; do not make an async heartbeat or a re-anchored timestamp substitute for missing GPU data; native correctness remains a component result, not complete G1; `runtime/formal-prep` (the separate incomplete 49-CPU-test draft) was not inspected or admitted and is not this diagnostic's target; historical 137 remains separate and unexplained.
