```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "changes_required",
  "code_commit": "b63d4213c462d7a8e70cc2a2c36c440ca8bcdb7b",
  "manifest_sha256": "270298642fa4fdd8093299b625daf4fea5e4bfdd1024ece5021b5deff55c89e9",
  "findings": [
    {
      "id": "C3-F01",
      "severity": "high",
      "blocking_for_r1": true,
      "status": "changes_required",
      "title": "The 270298 manifest is not a clean canonical3 freeze: the run identity and local validation still point to canonical2",
      "location": "runtime/active/run-manifest.json:2,1471,1491; runtime/active/control-input-manifest.json:5,278-304; reviews/full-r1-canonical3/cpu-validation.json:2-7; tools/freeze_active.py:6-8,33-39",
      "evidence": "The run id is new (sllm-glm51fp8-20261026T092330Z-97af52ad) and run_store_server13.py, test_runtime_imports.py, test_workspace.py, resource-window and guard scripts have new hashes, but run-manifest.created_utc is still 2026-10-26T07:58:55Z, candidate_revision is still canonical2, control-input-manifest.local_validation.cpu_tests is 94, and repository_inputs point to reviews/full-r1-canonical2/*. reviews/full-r1-canonical3/cpu-validation.json records 95 tests, passed, manifest 270298. freeze_active derives its output directory from candidate_revision, so it cannot reproduce the canonical3 seal from this run-manifest.",
      "impact": "The code hashes bind, but the full manifest/run identity does not: a deployer using this verdict would bind a run-manifest that claims canonical2, a control manifest that claims 94 tests, and a canonical3 validation that claims 95. The exact code plus full manifest binding requirement is not met; the canonical3 seal is not reproducible.",
      "minimal_fix": "Set run-manifest.created_utc to the new run creation time (2026-10-26T09:23:30Z) and candidate_revision to canonical3; run freeze_active from canonical3 so reviews/full-r1-canonical3 gets the actual cpu-tests/shell logs, local_validation.cpu_tests=95, and repository_inputs canonical3; update resource-window.original_manifest_sha256 and regenerate control-input-manifest; commit the new run identity/manifest and re-review. The import-order code change itself may remain."
    },
    {
      "id": "C3-F02",
      "severity": "medium",
      "blocking_for_r1": false,
      "status": "resolved_in_source",
      "title": "verify_store_imports now imports server before direct native extensions",
      "location": "runtime/active/run_store_server13.py:13-22; runtime/active/test_runtime_imports.py:19-28",
      "evidence": "names is now the ordered tuple ('sllm_store','sllm_store.server','sllm_store._C','sllm_store._checkpoint_store') and the loop preserves that order. sllm_store.server imports torch and preloads bundled libglog and imports _checkpoint_store; _C follows. The regression loader raises if _C/_checkpoint_store is imported before server and now passes. The old sorted set imported _C first, reproducing the observed pre-pool failure.",
      "impact": "The observed import-order/libglog failure is fixed in source. Actual G1 and the operator head preflight still required.",
      "minimal_fix": "None for the code order; retain the regression and the head preflight evidence."
    },
    {
      "id": "C3-F03",
      "severity": "low",
      "blocking_for_r1": false,
      "status": "non_blocking_verification",
      "title": "The regression uses a mock loader; the real no-GPU head import is not executed by R1",
      "location": "runtime/active/test_runtime_imports.py:19-28; runtime/active/run_store_server13.py:16-22",
      "evidence": "The loader models the order but does not import the real sllm_store package __init__, real sllm_store.server, libglog, torch, _C, or _checkpoint_store. The proposed operator CPU-only check would exercise the exact own no-GPU head.",
      "impact": "The source order is correct but the real dependency chain remains a G1/preflight check; a pass is not G1.",
      "minimal_fix": "Run the operator CPU-only verify_store_imports in the exact own no-GPU head; record module paths/hashes and torch/vllm/CUDA versions; do not call serve/CheckpointStore/pool/model."
    },
    {
      "id": "C3-F04",
      "severity": "runtime_condition",
      "blocking_for_r1": false,
      "status": "runtime_condition",
      "title": "The head CPU-only verify_store_imports preflight is acceptable but bounded and not G1",
      "location": "operator step; runtime/active/run_store_server13.py:11-22; runtime/active/workspace.py:9,23-25,54-67",
      "evidence": "The check uses the same verify_store_imports/package_root (/experiment/code/Store-source-v7/sllm_store), imports server before _C/_checkpoint_store, records versions, and can write proof under /r without serve or pool. The head is no-GPU, so _C/torch may fail for missing libcuda even when the service Store would work. The Store process runtime-imports receipt and the 32-rank weight/business audit remain authoritative.",
      "impact": "Bounded early detection of the exact import-order/libglog regression; cannot substitute for G1, the Store process receipt, per-Ray-worker import proof, R2, or formal admission.",
      "minimal_fix": "Run only after the actual new guard and head GCS readiness, in the exact own no-GPU head, before the service worker; stop only the recorded CID; record the exception and separate libglog/order failures from no-GPU libcuda absence; a failure stops the run; an inconclusive no-GPU failure is not a service pass or fail; preserve proof under /r."
    },
    {
      "id": "C3-F05",
      "severity": "low",
      "blocking_for_r1": false,
      "status": "non_blocking_metadata",
      "title": "The new window still carries old overlay/snapshot timestamps and the singular booking_id",
      "location": "runtime/active/resource-window-extension-v3.json:6-7,29-31; runtime/active/run-manifest.json:7,1472-1475; runtime/active/README.md:30-33; protocol/CANONICAL-WORKSPACE.md:25-26",
      "evidence": "overlay_created_utc=2026-10-26T03:13:44Z and snapshot_file_mtime_utc=2026-10-26T03:00:41Z predate the second booking and the 09:23 run; overlay_sealed_utc=09:23:30Z and the two bookings are new. run-manifest.booking_id is the first booking while booking_ids contains both. README/CANONICAL still show 22/15 tests and 17:50/one booking.",
      "impact": "Metadata/documentation only; load_window uses bookings, IDs, contiguity, cleanup deadline and timer.",
      "minimal_fix": "Update or annotate snapshot/overlay with the actual booking query/run time; align booking_id with booking_ids; update README/CANONICAL. Do not change runtime source for cosmetic documentation."
    },
    {
      "id": "C3-F06",
      "severity": "carried_non_blocking",
      "blocking_for_r1": false,
      "status": "carried",
      "title": "canonical2 non-blocking runtime conditions are unchanged",
      "location": "runtime/active/service13a-template.json:4; runtime/active/install_guard13.sh:19; runtime/active/launch_control13.py:43; runtime/active/supervise_control13.py:13; runtime/active/bootstrap_control13.py:79; runtime/active/accept_control13.py:184-205; runtime/active/release_control13.py:33-46",
      "evidence": "1700G slice vs 1700g service plus head/monitor; exact 3600/510 second double admission; prepare_host baseline not guaranteed clean; host-local NextElapse string compare; staged release stop order; .venv/proto/grpc external; accept model-result independence. canonical3 does not change these.",
      "impact": "Retained as runtime conditions; not re-opened as blocking.",
      "minimal_fix": "Keep the prior checks; make a separate reviewed revision if any is removed."
    },
    {
      "id": "C3-F07",
      "severity": "low",
      "blocking_for_r1": false,
      "status": "non_blocking",
      "title": "The apparent stale monitor was after cleanup, not a new monitor defect",
      "location": "STATUS.md:185; runtime/active/monitor_control13.py unchanged; runtime/active/accept_control13.py monitor_summary unchanged",
      "evidence": "The raw recorded stream has 429 rows and max begin-to-begin gap 1.43s; the apparent stale state was after cleanup. Monitor code is unchanged in canonical3.",
      "impact": "No new monitor finding; the new G1 must still produce its own continuous raw evidence.",
      "minimal_fix": "None for code; retain raw rows and do not reclassify the post-cleanup observation."
    }
  ],
  "runtime_conditions": [
    "Do not deploy the current 270298 manifest/run identity until C3-F01 is resealed; the new run-manifest, resource-window original hash, control-input-manifest, local validation, repository inputs and commit must be regenerated and re-reviewed.",
    "The new 20:50 guard/probe is not installed; the old failed run 17:50 guard is inactive and must not be reused.",
    "The new booking window is two contiguous boyjiang 10-18 and 18-21 reservations; re-query the booking API and refresh GPU/UUID/boot/memory/NUMA/disk immediately before dispatch; load_window only validates the frozen file.",
    "After the actual new guard and head GCS readiness, the operator may run the CPU-only verify_store_imports/version preflight in the exact own no-GPU head before the service worker; no serve/pool/model; preserve proof under /r; a failure stops the run; distinguish libglog/import-order failure from no-GPU libcuda absence; the preflight is not G1.",
    "Each head must be verified Running and its private GCS TCP port ready before worker dispatch; stop only the recorded CID.",
    "Two Ray controls must each pass and complete their own 60s release before the real service batch.",
    "The real G1 service still requires the 716GiB pool, 148/756GB cache, four native lifecycles, 32 rank records, PSS, 61-row idle60, three 60s releases and root-owned evidence index; none are run.",
    "G1 and R2 are not run; formal ledger remains 5 success / 4 failed / 31 unstarted; store_pool_hit_03 only after G1 then R2 then formal admission.",
    "Any failure must stop new allocation, clean this run, perform the final 60s observation/root index and not overwrite or retry formal IDs.",
    "The old failed run/source is preserved as executed-source-ef53e19 under its closed run; no retry or credential reuse; the post-cleanup stale-monitor observation is not a new monitor issue.",
    "Historical exit137 remains failed; dockerd stop/force is known; upstream requester/reason is unknown; no Ray/OOM attribution and no claim of resolution.",
    "No new global Docker/BPF probe is required; the head CPU check is bounded and target-specific."
  ]
}
```