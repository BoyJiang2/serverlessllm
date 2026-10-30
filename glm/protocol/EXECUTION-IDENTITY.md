# Source responsibilities and execution identities

This additive audit does not change the reviewed G1 source, authorize deployment,
or replace R1/G1/R2. It explicitly records the remaining formal-entry gap.

## Source inventory

| Responsibility | Repository source | Status |
|---|---|---|
| G1 dispatch and supervision | `runtime/active/launch_control13.py`, `supervise_control13.py` | Control-only; input hashes must match `control-input-manifest.json`. |
| G1 business and weight checks | `runtime/active/validation_control13.py` | Control-only; not the forty-attempt formal driver. |
| Deadline installation and exact-owned cleanup | `runtime/active/install_guard13.sh`, `cleanup_guard13.sh` | Manifest-bound, run-specific systemd timer/slice and container label checks. Presence is not proof of installation. |
| Window validation and control watchdog | `runtime/active/resource_window_control13.py`, `control_watchdog13.py` | Additional checks; not a replacement for actual root guard/probe evidence. |
| Historical formal measurement | `runtime/historical-v11/formal_trials_v11.py` and its adjacent dependencies | Preserved historical source, pinned here; stale `/experiment` layout and gate bindings prevent treating it as the new formal entry. |
| New canonical formal measurement | **Not yet admitted** | Must be implemented/frozen and pass actual G1/R2 and formal admission before any remaining ID. |

The claimed original `measure.py` (58 KB) and `window_guard.py` (23 KB) have
not been identified by original path, hash and launch record in this audit.
The table proves current responsibilities exist; it does **not** claim those
two filenames were equivalent renames. Close their provenance only with the
original files or an immutable original manifest and caller evidence. Do not
copy unrelated dependency files with matching names or add empty wrappers.

The deployed G1 source belongs at
`/data/brick1/boyjiang/glm5.1-fp8/code/experiment/`, with run output at
`validation/controls/<control_run_id>/`. The canonical host root is not a flat
container `/experiment` mount. Repository inspection alone proves neither current
remote contents nor actual runtime readiness.

## Identity and statistical continuity

* **Experiment identity:** `sllm-glm51fp8-20261024T051606Z-deb76e57` identifies
  the preserved experiment lineage, not a requirement to reuse old process labels.
* **Preregistration:** `measurement-preregistration.json`, SHA256
  `90d4c91bf8b0c9495e0cb95376a55f84d17351308541414d58dd650d8e2dd37c`,
  owns the forty unique ordered attempt IDs (ten per condition).
* **Execution identity:** each control or later formal infrastructure run gets
  a unique identity for its own containers, PIDs, cgroup, guard and receipts.
  G1 manifests explicitly authorize zero formal IDs. Failed controls do not
  consume, reset or replace an attempt, but their costs remain recorded.
* **Results:** the nine original terminal results remain byte-identical.
  Current repository baseline is five successes, four failures, 31 unstarted;
  the next original ID is `store_pool_hit_03`. This is a baseline, not a live
  remote ledger. Recollect actual directories and interrupted attempts before
  formal admission. Never infer "unstarted" from a missing result alone.

A future formal gate must bind **both** experiment/preregistration lineage and
actual execution identity, together with code commit, complete file inventory,
gold, previous terminal hashes, current ledger, actual G1/R2, window, guard and
derived configuration constraints. Simply restoring the original `run_id` does
not establish continuity or authorize cleanup of a different execution.

Keeping the planned denominator forty does not authorize pooling revisions.
Report original failures and all substantive protocol/node/code strata;
equivalence and aggregation need independent evidence and review.

## Read-only audit

From the repository root:

```powershell
python -B tools/check_execution_contract.py
python -B tools/check_execution_contract.py --require-formal
python -B -m unittest discover -s tools -p test_execution_contract.py
```

The first command checks the pinned preregistration/ledger/historical driver,
all nine raw result hashes, original order, current control source manifest and
guard identity. It reports `formal_ready: false` even when those checks pass.
The second intentionally exits **2** while the formal blockers remain. Invalid
or missing inputs exit **1**. Neither command launches workloads, writes receipts,
checks remote resources, installs guards, or grants R2 approval.

`execution-contract.json` is additive review material, not itself an independently
approved gate. No reviewed active manifest is modified by adding this audit.
