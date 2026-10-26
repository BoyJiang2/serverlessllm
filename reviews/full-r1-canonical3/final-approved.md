```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "approved",
  "code_commit": "ae4c5557894ff3b21a560d94315a2bd749ae9b67",
  "manifest_sha256": "b352bf336c39efb2d08288c0bb724f8cf3f33b8ce3602c5102329733f01f3d63",
  "findings": [
    {
      "id": "C3-F01",
      "severity": "high",
      "status": "closed",
      "blocking_for_r1": false,
      "title": "Canonical3 run identity and manifest are now consistent",
      "location": "runtime/active/run-manifest.json:2,1471,1491; runtime/active/resource-window-extension-v3.json:2,5,30; runtime/active/control-input-manifest.json:1-5,183-195,278-303; reviews/full-r1-canonical3/cpu-tests.log:98-100; reviews/full-r1-canonical3/shell-syntax.json:1-20; reviews/full-r1-canonical3/cpu-validation.json:2-9; tools/freeze_active.py:6-8,33-39",
      "evidence": "run-manifest.run_id is sllm-glm51fp8-20261026T092330Z-97af52ad, candidate_revision is canonical3 and created_utc is 2026-10-26T09:23:30+00:00. resource-window original_manifest_sha256 is 67e1ff4a4a3c2851bd5be15b9bff0684b8d47a517a9582d20ac388ccfdd8d6bc, matching the control-input-manifest run-manifest row (bytes 48795, same sha). The control-input-manifest was created 2026-10-26T09:31:48Z, records local_validation.cpu_tests=95, and its repository_inputs point to reviews/full-r1-canonical3/cpu-tests.log (61e8871a..., 9630 bytes) and reviews/full-r1-canonical3/shell-syntax.json (04afa44d..., 547 bytes). The canonical3 cpu-tests.log ran 95 tests OK; shell-syntax has three per-script hashes (4db2d11b..., 68f5fb4c..., 94176867...) matching the control manifest script rows; cpu-validation records tests=95, passed=true, manifest_sha256=b352bf33..., and raw_test_log=reviews/full-r1-canonical3/cpu-tests.log. freeze_active derives its output from candidate_revision, so it now reproduces reviews/full-r1-canonical3 and 95 tests.",
      "impact": "The former C3-F01 metadata mismatch is closed: code, run identity, window original hash, local validation, repository inputs, raw 95-test log, shell hashes and canonical3 summary all bind the new manifest b352bf33...; old b63d421/270298 and its changes_required raw receipt/manifest remain preserved under reviews/full-r1-canonical3.",
      "minimal_fix": "None; the new seal binds ae4c555.../b352bf33... and the old seal is retained as history."
    },
    {
      "id": "C3-F04",
      "severity": "runtime_condition",
      "status": "accepted_bounded_preflight",
      "blocking_for_r1": false,
      "title": "CPU-only head import preflight remains bounded and is not G1",
      "location": "runtime/active/run_store_server13.py:11-22; runtime/active/workspace.py:9,23-25,54-67",
      "evidence": "The preflight uses the same verify_store_imports and package_root /experiment/code/Store-source-v7/sllm_store, imports server before _C/_checkpoint_store, records selected module paths/hashes and torch/vllm/CUDA versions, does not call serve/CheckpointStore/pool/model, and writes proof under /r. The head is no-GPU, so _C/torch may fail for missing libcuda even when the service Store would work.",
      "impact": "Bounded early detection of the exact import-order/libglog failure; it does not substitute for G1, the Store process runtime-imports receipt, per-Ray-worker proof, R2 or formal admission.",
      "minimal_fix": "Run only after the actual new guard and head GCS readiness, in the exact own no-GPU head, before the service worker; stop only the recorded CID; a failure stops the run; distinguish libglog/import-order failure from no-GPU libcuda absence; preserve proof under /r."
    }
  ],
  "runtime_conditions": [
    "The new canonical3 20:50 guard is not deployed; the old C2 actual 20:50 guard was cleaned inactive; 17:50 belongs to the earlier C1 run; the new guard must be installed and probed independently.",
    "Two contiguous boyjiang bookings 10-18 and 18-21 remain the window; re-query booking and refresh GPU/UUID/boot/memory/NUMA/disk before dispatch; load_window only validates the frozen file.",
    "After the actual new guard and head GCS readiness, run the CPU-only verify_store_imports/version preflight in the exact own no-GPU head before the service worker; no serve/pool/model; preserve proof under /r; a failure stops the run; the preflight is not G1.",
    "Each head must be verified Running and its private GCS TCP port ready before worker dispatch; stop only the recorded CID.",
    "Two Ray controls must each pass and complete their own 60s release before the real service batch.",
    "The service G1 still requires the 716GiB pool, 148/756GB cache, four native lifecycles, 32 rank records, PSS, 61-row idle60, three 60s releases and root-owned evidence index.",
    "G1 and R2 are not run; formal ledger remains 5 success / 4 failed / 31 unstarted; store_pool_hit_03 only after G1 then R2 then formal admission.",
    "Any failure must stop new allocation, clean this run, perform final 60s observation/root index and not overwrite or retry formal IDs.",
    "The old failed runs and sources remain preserved; do not retry them. The post-cleanup apparent stale monitor (429 raw rows, max gap 1.43s) is not a new monitor issue.",
    "Historical exit137 remains failed; dockerd stop/force is known; upstream requester/reason is unknown; no Ray/OOM attribution and no claim of resolution.",
    "Retain all prior canonical2/canonical3 non-blocking conditions: 1700G slice vs 1700g service plus head/monitor, exact 3600/510 second admission boundary, clean GPU baseline, Asia/Shanghai timer comparison, staged release stop order, .venv/proto/grpc, accept model-result independence, window timestamp/README/booking_id metadata.",
    "No new global Docker/BPF probe is required; the head CPU check is bounded and target-specific."
  ]
}
```