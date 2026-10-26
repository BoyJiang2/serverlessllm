```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "changes_required",
  "code_commit": "a7c5a2faf98562cdf8b3709423b4e41dc6c66238",
  "manifest_sha256": "d95ab4a15155138f8a10c7ad11421a4cd72085c22b490b44373a2dd6ecbcade2",
  "blockers": [
    "C7-F01: the manifest binds tools/freeze_active.py at sha256 a8072f8fd4725ffe5a78d2225d81e04e1d44db15529fbfd9c8d23f23760d3737 (4731 bytes), but the actual current tools/freeze_active.py is the 54-line C6 version without the claimed snapshot identity assertion. run_control13.load_context and root_control13.load_context verify every repository_inputs hash and will raise 'repository review input differs' before any operator/root action. The manifest/source binding is not self-consistent and the new run cannot start through the reviewed entries."
  ],
  "findings": [
    {
      "id": "C7-F01",
      "severity": "high",
      "status": "changes_required",
      "blocking_for_r1": true,
      "title": "freeze_active.py manifest hash does not match the actual current file; snapshot identity assertion is absent",
      "location": "runtime/active/control-input-manifest.json:300-303,330-338; tools/freeze_active.py:1-54; tools/run_control13.py:22-26; tools/root_control13.py:3,10; runtime/active/snapshot_root13.py:4",
      "evidence": "The control manifest repository_inputs row for tools/freeze_active.py is sha256 a8072f8fd4725ffe5a78d2225d81e04e1d44db15529fbfd9c8d23f23760d3737 / 4731 bytes. The actual tools/freeze_active.py read in this repository is the 54-line C6 version: it checks CONTROL-IDENTITY keys, guard run ids, UTC calendar, 102/9 tests, shell syntax and repository inputs, but it contains no snapshot_root13.py identity assertion and no snapshot identity rebound logic. The C6 freeze is 4593 bytes (e8083e...), not the manifest's 4731/a8072f8.... run_control13.load_context and root_control13.load_context iterate repository_inputs and compare actual bytes to the manifest; the mismatch stops the reviewed operator before guard/observers/cleanup/snapshot.",
      "impact": "The canonical7 manifest is not reproducible from the actual freeze and cannot pass the reviewed operator binding. The new run/guard cannot be started or cleaned through the reviewed entries until the actual freeze is saved and the manifest resealed.",
      "minimal_fix": "Save the actual canonical7 tools/freeze_active.py including the snapshot identity consistency assertion, rerun freeze_active to regenerate the 103 CPU/9 operator/3 shell logs and control-input-manifest, verify the freeze row matches the actual file, then commit the new code/MF and re-review. The tensor_audit.py and run_control preflight code may remain."
    },
    {
      "id": "C7-F02",
      "severity": "high",
      "status": "resolved",
      "blocking_for_r1": false,
      "title": "tensor_audit.py is the exact pinned historical helper and the new head preflight verifies origin/hash/callback/lazy distributed import",
      "location": "runtime/active/tensor_audit.py:1-42; tools/run_control13.py:107-122; reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/pinned-audit-error.txt:1-2; reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/outcome.json",
      "evidence": "The pinned 1142-source row is tensor_audit.py 2232 bytes sha256 069ab4d8cfdc3f57819358222b6c1a1e8973c795df2131974eb0a3b9161bbcff. The new runtime/active/tensor_audit.py is that exact historical source: 42 lines, audit_model imports get_tensor_model_parallel_rank from vllm.distributed lazily inside the callback, and writes rank documents consistent with weight_contract. The service head preflight imports tensor_audit, verifies its resolved path is SOURCE/tensor_audit.py and its exact hash, round-trips audit_model through cloudpickle, imports vllm.distributed, and writes cpu-import-preflight.json under /r before the service worker/pool/model. The C6 actual failure was ModuleNotFoundError: No module named 'tensor_audit' at vllm_backend.py:223 after a 388.23s engine init; no business or rank audit succeeded. The helper was absent; the new file and preflight address that dependency path.",
      "impact": "The observed C6 native-backend import failure is fixed in the affected source and covered by a before-allocation head preflight. The actual container import remains mandatory runtime evidence; the CPU probe is not G1.",
      "minimal_fix": "None for the helper/preflight; retain the exact 2232/069ab4 bytes and actual head preflight proof."
    },
    {
      "id": "C7-F03",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "The new canonical7 run and 02:50 Oct27 guard are not deployed; C6 is closed and its credentials/evidence are not reusable",
      "location": "runtime/active/run-manifest.json:2,8-9,1471-1474,1491; runtime/active/resource-window-extension-v3.json; runtime/active/install_guard13.sh; reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/outcome.json",
      "evidence": "The new run is sllm-glm51fp8-20261026T132344Z-a8efabb2 with candidate_revision canonical7, existing 18-21 and 21-03 bookings, cleanup 02:50+08 and a new 02:50 Oct27 guard not installed. C6 is closed; its guard and evidence are not reusable. C6 failed after real Ray/pool/cache/weight/KV/warmup work at the missing tensor_audit import; no business gold or 32-rank audit success; cleanup first returned 1, retry succeeded, release was 61 samples/75.755s and root index passed. Historical exit137 remains unknown and separate. G1/R2 are not passed; formal ledger is 5/4/31.",
      "impact": "The new guard/run must be independently installed, probed and executed; C6 cannot authorize it.",
      "minimal_fix": "Reseal C7-F01 first, install and probe the new guard, refresh the window/resources and run canonical7 G1 with its own release/root evidence."
    },
    {
      "id": "C7-F04",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "Fresh compile/autotune remains inside the unchanged fixed budgets and may honestly fail G1",
      "location": "runtime/active/prepare_host13.py:10-19,28-34; runtime/active/budget_control13.py:4; runtime/active/bootstrap_control13.py:80; runtime/active/control_watchdog13.py:4-34; runtime/active/cache_state_control13.py:600s path",
      "evidence": "The service remains 3420s with the 25s TERM/KILL reserve and 3300s bootstrap alarm; cache remains 600s; the six cache groups are fresh empty with zero reused compiled bytes. C6 reached a 388.23s engine init before the missing helper failure; the new run still charges all compile/autotune to untimed G1 initialization. No extension, coverage deletion or resource-limit increase was added.",
      "impact": "Fresh compile may make this G1 fail on the fixed budget; that is an honest runtime outcome, not a static source defect. Formal timing must separately freeze warm caches under R2/v11.",
      "minimal_fix": "Run with the unchanged budgets; on overflow retain the failure and do not extend or delete 32-rank/business/60s coverage."
    },
    {
      "id": "C7-F05",
      "severity": "low",
      "status": "non_blocking_metadata",
      "blocking_for_r1": false,
      "title": "The host stdin probe is supplementary C6 evidence, not a canonical7 guard/run or G1 record",
      "location": "reviews/full-r1-canonical6/host-stdin-probe-v2.json:1-310",
      "evidence": "The probe is bound to run_reference sllm-glm51fp8-20261026T115129Z-d1cb0879 (C6), not the new a8efabb2 run. It imports/compiles the bound source, simulates seven actor derivations and rejects the closed old guard, but performs no Docker create/start, Store, weight load or inference. The canonical7 head preflight is new code and its actual container import remains a runtime condition.",
      "impact": "The probe supports the prior stdin/import behavior only; it cannot substitute for the canonical7 actual head preflight or G1.",
      "minimal_fix": "Retain as supplementary; produce canonical7 actual head/guard/G1 evidence."
    },
    {
      "id": "C7-F06",
      "severity": "low",
      "status": "non_blocking",
      "blocking_for_r1": false,
      "title": "C6 first cleanup returned 1 before a successful retry; no cause is inferred",
      "location": "reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/outcome.json:78-80",
      "evidence": "C6 cleanup_first_returncode is 1; cleanup_final_passed is true; release 61 samples/75.755247601s and root index passed. The outcome attributes final termination to Main's exact-owned cleanup after the confirmed import failure, not to tensor_audit or model OOM; historical exit137 remains separate.",
      "impact": "No new C7 source defect; canonical7 must produce its own cleanup/root evidence.",
      "minimal_fix": "Retain first/retry evidence and repeat independent cleanup/release verification for C7."
    },
    {
      "id": "C7-F07",
      "severity": "low",
      "status": "carried_non_blocking",
      "blocking_for_r1": false,
      "title": "Previous C5/C6 non-blocking operator conditions remain unchanged",
      "location": "tools/root_control13.py:21-25; tools/run_control13.py:5-6,200-201",
      "evidence": "root_control13 still reads the hardcoded local HANDOFF.md credential path with an exactly-one-token check; the normal SSH client still lacks explicit BatchMode and an outer timeout. C7 does not alter these.",
      "impact": "Portability/credential and SSH-hardening risks remain; they are not new false-pass paths.",
      "minimal_fix": "Prefer a reviewed credential-file/environment path with permission checks and add BatchMode hardening."
    },
    {
      "id": "C7-F08",
      "severity": "low",
      "status": "non_blocking_runtime_condition",
      "blocking_for_r1": false,
      "title": "The new no-GPU head preflight imports vllm.distributed and tensor_audit; a no-GPU failure must be interpreted carefully",
      "location": "tools/run_control13.py:107-122; runtime/active/tensor_audit.py:12-14",
      "evidence": "The head CPU preflight imports torch, vllm, cloudpickle, tensor_audit and vllm.distributed, verifies helper origin/hash and round-trips audit_model; it does not call audit_model or create a pool. The actual audit_model also imports vllm.distributed lazily and synchronizes CUDA only when the callback runs on GPU workers. A no-GPU head may fail for libcuda/environment reasons distinct from the real service import.",
      "impact": "The preflight is a bounded early signal, not G1 or the Store/worker import proof; a no-GPU environment failure must not be confused with a service failure.",
      "minimal_fix": "Record the exact import/failure and continue to treat the actual container import and G1 as mandatory."
    }
  ],
  "runtime_conditions": [
    "Do not deploy or start canonical7 until C7-F01 is fixed: save the actual tools/freeze_active.py with the snapshot identity assertion, rerun freeze_active to regenerate the 103 CPU/9 operator/3 shell logs and control-input-manifest, verify every repository_inputs hash (including freeze_active.py) against the actual files, and commit a new code/MF for re-review.",
    "The new canonical7 02:50+08 guard is not installed; C6 is closed and its guard/evidence are not reusable; install and independently probe the new guard/slice before any control.",
    "Existing boyjiang bookings 18-21 and 21-03 remain the window; re-query bookings and refresh GPU/UUID/boot/memory/NUMA/disk immediately before dispatch.",
    "C6 actual evidence is retained: Ray controls/releases, real 716GiB pool, 148-input hot cache 0 repairs, eight weight pipelines and KV/warmup passed; missing tensor_audit caused failure before business/32-rank audit; cleanup first 1, retry succeeded, 61 samples/75.755s release and root index passed; historical exit137 unknown and separate.",
    "tensor_audit.py must remain the exact historical 2232-byte/069ab4d8... helper; the service head preflight verifies origin/hash, cloudpickle callback round-trip and lazy vllm.distributed import before the service worker/pool/model, with proof under /r. The actual container import remains mandatory runtime evidence; CPU probe is not G1.",
    "Continuation=True remains only for no-allocation observe/reconcile: credit only anon from the already identity-verified own slice, require available>=64GiB and available+owned_anon>=768799145984+weight_bytes+64GiB; new launch/prepare stays continuation=False with the strict budget and no file/pagecache credit.",
    "Fresh cache policy remains six empty CACHE_DIRECTORIES with zero reused compiled bytes; all compile/autotune is untimed G1 initialization. If it exceeds the unchanged 3300s/3420s/600s budgets, fail honestly; never extend or delete 32-rank/business/60s coverage. Formal timing requires separately frozen warm caches under R2/v11.",
    "After head GCS readiness and before the service worker, the head CPU-only helper/store import preflight may run; no serve/pool/model; preserve proof under /r; distinguish import-order/libglog/no-GPU failures from the actual service import.",
    "Each head must be Running with private GCS TCP ready before worker dispatch; stop only the recorded CID. Two Ray controls must pass with their own 60s releases before the service batch; all 32 rank/12 business/60s requirements remain unchanged.",
    "Real G1 still requires the 716GiB pool, 148-input hot cache, four native lifecycles, 32 rank records, PSS, 61-row idle60, three 60s releases and root-owned evidence index; R2 and formal admission remain unpassed. Formal ledger remains 5 success / 4 failed / 31 unstarted; no formal ID consumed.",
    "root_control13 currently requires the hardcoded local HANDOFF.md credential with exactly one token; keep it access-controlled, never committed, and prefer a reviewed credential-file/environment path with BatchMode hardening.",
    "The host stdin probe is C6 supplementary evidence, not a canonical7 guard/run or G1 record. runtime/formal-prep and tools/check_g1_raw13.py remain separately prepared offline work, not this R1's executable scope and not formal admission. No historical exit137 attribution is made."
  ]
}
```