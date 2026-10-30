```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "approved",
  "code_commit": "a7c5a2faf98562cdf8b3709423b4e41dc6c66238",
  "manifest_sha256": "d95ab4a15155138f8a10c7ad11421a4cd72085c22b490b44373a2dd6ecbcade2",
  "blockers": [],
  "findings": [
    {
      "id": "C7-F01",
      "severity": "info",
      "status": "withdrawn_stale_read",
      "blocking_for_r1": false,
      "title": "Prior freeze_active.py mismatch was a stale-read artifact; current bytes match the manifest",
      "location": "tools/freeze_active.py:10-19,30-41; runtime/active/snapshot_root13.py:1-8; reviews/full-r1-canonical7/current-binding-recheck.json:1-50; runtime/active/control-input-manifest.json:300-303",
      "evidence": "Fresh reads show the actual tools/freeze_active.py is 4731 bytes with sha256 a8072f8fd4725ffe5a78d2225d81e04e1d44db15529fbfd9c8d23f23760d3737, exactly the manifest repository_inputs row. Line 13 contains the snapshot identity assertion: it requires \"\\nrun='\"+config['run_id']+\"'\\n\" in runtime/active/snapshot_root13.py. snapshot_root13.py line 4 is run='sllm-glm51fp8-20261026T132344Z-a8efabb2'. current-binding-recheck.json confirms freeze actual bytes 4731 and the same sha as the manifest and git, snapshot_assertion_present=true, and all eight repository_inputs expected/actual hashes equal. No reseal is required.",
      "impact": "C7-F01 is withdrawn. The previous finding was based on cached/stale 54-line C6 content and is corrected by the actual current bytes.",
      "minimal_fix": "None; retain the current actual binding a7c5a2f.../d95ab4... and the current-binding-recheck evidence."
    },
    {
      "id": "C7-F02",
      "severity": "high",
      "status": "resolved",
      "blocking_for_r1": false,
      "title": "tensor_audit.py is the exact pinned historical helper and the new head preflight verifies its dependency path",
      "location": "runtime/active/tensor_audit.py:1-42; tools/run_control13.py:107-122; reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/pinned-audit-error.txt:1-2; reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/outcome.json",
      "evidence": "The pinned 1142-source row is tensor_audit.py 2232 bytes / 069ab4d8cfdc3f57819358222b6c1a1e8973c795df2131974eb0a3b9161bbcff. The new runtime/active/tensor_audit.py is that exact historical source: audit_model imports get_tensor_model_parallel_rank from vllm.distributed lazily inside the callback and writes rank documents compatible with weight_contract. The service-head preflight imports tensor_audit, checks resolved origin is SOURCE/tensor_audit.py and the exact hash, round-trips audit_model via cloudpickle, imports vllm.distributed, and writes cpu-import-preflight.json under /r before the service worker/pool/model. C6 failed at vllm_backend.py:223 with ModuleNotFoundError after 388.23s engine initialization; no business/rank audit succeeded.",
      "impact": "The observed C6 missing-helper failure is fixed in source with a before-allocation head preflight. Actual container import remains mandatory runtime evidence; the CPU probe is not G1.",
      "minimal_fix": "None for the helper/preflight; retain exact 2232/069ab4 bytes and actual head proof."
    },
    {
      "id": "C7-F03",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "Canonical7 run and 02:50 Oct27 guard are not deployed; C6 is closed and not reusable",
      "location": "runtime/active/run-manifest.json:2,8-9,1471-1474,1491; runtime/active/resource-window-extension-v3.json; runtime/active/install_guard13.sh; reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/outcome.json",
      "evidence": "The new run is sllm-glm51fp8-20261026T132344Z-a8efabb2 with candidate_revision canonical7, existing 18-21 and 21-03 bookings, cleanup 02:50+08 and a new 02:50 Oct27 guard not installed. C6 is closed; its guard/evidence are not reusable. C6 passed both Ray controls/releases, real 716GiB pool, 148-input hot cache 0 repairs, and all eight weight/KV/warmup pipelines, then failed at the missing tensor_audit import before business/audit. Cleanup first returned 1, retry succeeded, 61 samples/75.755s release and root index passed. Historical exit137 remains unknown and separate. G1/R2 not passed; formal 5/4/31.",
      "impact": "The new guard/run must be independently installed, probed and executed with its own evidence.",
      "minimal_fix": "Install/probe the new guard, refresh window/resources, and run canonical7 G1 with its own release/root evidence."
    },
    {
      "id": "C7-F04",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "Fresh compile/autotune remains inside unchanged budgets and may honestly fail G1",
      "location": "runtime/active/prepare_host13.py:10-19,28-34; runtime/active/budget_control13.py:4; runtime/active/bootstrap_control13.py:80; runtime/active/control_watchdog13.py:4-34; runtime/active/cache_state_control13.py:600s path",
      "evidence": "Service 3420s with 25s TERM/KILL reserve and 3300s bootstrap alarm, cache 600s, six fresh empty cache groups with zero reused compiled bytes. C6 engine initialization took 388.23s before the helper failure. No budget extension, coverage deletion or resource-limit increase was added.",
      "impact": "Fresh compile may make this G1 fail honestly; it is not a static source defect. Formal timing needs separately frozen warm caches under R2/v11.",
      "minimal_fix": "Run unchanged budgets; on overflow retain failure and keep 32-rank/business/60s coverage."
    },
    {
      "id": "C7-F05",
      "severity": "low",
      "status": "non_blocking_metadata",
      "blocking_for_r1": false,
      "title": "Host stdin probe is supplementary C6 evidence, not a canonical7 guard/run or G1 record",
      "location": "reviews/full-r1-canonical6/host-stdin-probe-v2.json:1-310",
      "evidence": "The probe is bound to C6 run_reference sllm-glm51fp8-20261026T115129Z-d1cb0879, not a8efabb2. It imports/compiles bound source, simulates seven actor derivations and rejects the closed old guard; no Docker create/start, Store, weight load or inference. The canonical7 head preflight is new code and actual container import remains a runtime condition.",
      "impact": "No G1/R2 or canonical7 guard/run evidence is supplied by this probe.",
      "minimal_fix": "Retain as supplementary; produce canonical7 actual head/guard/G1 evidence."
    },
    {
      "id": "C7-F06",
      "severity": "low",
      "status": "non_blocking",
      "blocking_for_r1": false,
      "title": "C6 first cleanup returned 1 before a successful retry; no cause inferred",
      "location": "reviews/actual-controls/sllm-glm51fp8-20261026T123417Z-b1692e12/outcome.json:78-80",
      "evidence": "cleanup_first_returncode=1, cleanup_final_passed=true, release 61 samples/75.755247601s and root index passed. Final termination is attributed to Main's exact-owned cleanup after the confirmed import failure, not to tensor_audit/model OOM; historical exit137 remains separate.",
      "impact": "No new C7 source defect; canonical7 must produce its own cleanup/root evidence.",
      "minimal_fix": "Retain first/retry evidence and repeat independent cleanup/release verification."
    },
    {
      "id": "C7-F07",
      "severity": "low",
      "status": "carried_non_blocking",
      "blocking_for_r1": false,
      "title": "Previous operator/root hardening conditions remain unchanged",
      "location": "tools/root_control13.py:21-25; tools/run_control13.py:5-6,200-201",
      "evidence": "root_control13 still reads the hardcoded local HANDOFF.md credential with an exactly-one-token check; the normal SSH client still lacks explicit BatchMode and an outer timeout.",
      "impact": "Portability/credential and SSH-hardening risks remain; they are not new false-pass paths.",
      "minimal_fix": "Prefer a reviewed credential-file/environment path with permission checks and BatchMode hardening."
    },
    {
      "id": "C7-F08",
      "severity": "low",
      "status": "non_blocking_runtime_condition",
      "blocking_for_r1": false,
      "title": "No-GPU head preflight imports vllm.distributed/tensor_audit; environment failures need interpretation",
      "location": "tools/run_control13.py:107-122; runtime/active/tensor_audit.py:12-14",
      "evidence": "The head CPU preflight imports torch, vllm, cloudpickle, tensor_audit and vllm.distributed, verifies origin/hash and round-trips audit_model, without calling audit_model or allocating a pool. tensor_audit.audit_model imports vllm.distributed lazily and synchronizes CUDA only on GPU workers. A no-GPU head may fail for libcuda/environment reasons distinct from the service import.",
      "impact": "The preflight is a bounded early signal, not G1 or the Store/worker import proof.",
      "minimal_fix": "Record exact import/failure and keep actual container import and G1 mandatory."
    }
  ],
  "runtime_conditions": [
    "Use the actual current binding a7c5a2faf98562cdf8b3709423b4e41dc6c66238 / d95ab4a15155138f8a10c7ad11421a4cd72085c22b490b44373a2dd6ecbcade2; no reseal is required. The 4731-byte/a8072f8... freeze and all eight repository_inputs match the actual files, and snapshot identity is asserted.",
    "The new canonical7 02:50+08 guard is not installed; C6 is closed and its guard/evidence are not reusable; install and independently probe the new guard/slice before any control.",
    "Existing boyjiang bookings 18-21 and 21-03 remain the window; re-query bookings and refresh GPU/UUID/boot/memory/NUMA/disk immediately before dispatch.",
    "C6 actual evidence is retained: Ray controls/releases, real 716GiB pool, 148-input hot cache 0 repairs, eight weight/KV/warmup pipelines passed; missing tensor_audit failed before business/32-rank audit; cleanup first 1, retry succeeded, 61 samples/75.755s release and root index passed; historical exit137 unknown and separate.",
    "tensor_audit.py must remain the exact 2232-byte/069ab4d8... helper; the head preflight verifies origin/hash, cloudpickle callback round-trip and lazy vllm.distributed import before service worker/pool/model, with proof under /r. Actual container import remains mandatory; CPU probe is not G1.",
    "Continuation=True remains only for no-allocation observe/reconcile: credit only anon from the already identity-verified own slice, require available>=64GiB and available+owned_anon>=768799145984+weight_bytes+64GiB; new launch/prepare stays continuation=False with strict budget and no file/pagecache credit.",
    "Fresh caches remain six empty CACHE_DIRECTORIES with zero reused compiled bytes; all compile/autotune is untimed G1 initialization. If it exceeds the unchanged 3300s/3420s/600s budgets, fail honestly; never extend or delete 32-rank/business/60s coverage. Formal timing requires separately frozen warm caches under R2/v11.",
    "After head GCS readiness and before the service worker, the head CPU-only helper/store import preflight may run; no serve/pool/model; preserve proof under /r; distinguish import-order/libglog/no-GPU failures from the actual service import.",
    "Each head must be Running with private GCS TCP ready before worker dispatch; stop only the recorded CID. Two Ray controls must pass with their own 60s releases before the service batch; all 32 rank/12 business/60s requirements remain unchanged.",
    "Real G1 still requires the 716GiB pool, 148-input hot cache, four native lifecycles, 32 rank records, PSS, 61-row idle60, three 60s releases and root-owned evidence index; R2 and formal admission remain unpassed. Formal ledger remains 5 success / 4 failed / 31 unstarted; no formal ID consumed.",
    "root_control13 still requires the hardcoded local HANDOFF.md credential with exactly one token; keep it access-controlled, never committed, and prefer a reviewed credential-file/environment path with BatchMode hardening.",
    "The C6 host stdin probe is supplementary, not a canonical7 guard/run or G1 record. runtime/formal-prep and tools/check_g1_raw13.py remain separately prepared offline work, not this R1's executable scope and not formal admission. No historical exit137 attribution is made."
  ]
}
```
