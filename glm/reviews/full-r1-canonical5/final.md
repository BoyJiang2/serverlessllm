```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "approved",
  "code_commit": "538f0a26c79614afe25b80b0483b7ae9b775fd97",
  "manifest_sha256": "4f62e0eb849baaf738af766ff8f74dc60c30b2505f48040c8f4d16c41e9c152e",
  "findings": [
    {
      "id": "C5-F01",
      "severity": "high",
      "status": "resolved",
      "blocking_for_r1": false,
      "title": "Transport loss now reconciles once and does not relaunch or auto-kill a verified running control",
      "location": "tools/run_control13.py:5-6,167-184,186-212; tools/root_control13.py:19-25; tools/test_control_transport13.py:28-102; reviews/full-r1-canonical5/operator-tests.log; reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/outcome.json:67-74",
      "evidence": "The two tracked operator/root entries are repository-bound. SSH uses ServerAliveInterval=15 and ServerAliveCountMax=4. On return 255 the operator performs exactly one bounded 25s reconciliation, verifies run/code/manifest identity, guard, monitor and original 330/3420 monotonic budget, and returns 75 with next_phase=observe for running_verified or completed_verified; it does not relaunch, reset budget or overwrite release. Unknown or failed reconciliation falls through to an exact-owned cleanup request. Observe resumes the original CID and dispatch budget, validates an existing release without overwriting, and never materializes or starts a container. The cleanup marker is explicitly request-only. Eight operator tests pass, including the actual REMOTE observe branch with exactly three docker inspect calls and no create/start/exec.",
      "impact": "The C4 failure mode (SSH reset then immediate unbound cleanup) is addressed by a fail-closed, bounded operator path while preserving the actual C4 evidence and not reclassifying historical exit137.",
      "minimal_fix": "None for the transport behavior; retain the original C4 outcome and use the new run's own evidence."
    },
    {
      "id": "C5-F02",
      "severity": "high",
      "status": "resolved",
      "blocking_for_r1": false,
      "title": "Continuation memory accounting credits only existing own-slice anon and keeps the fresh-launch budget strict",
      "location": "runtime/active/preflight_control13.py:25-28,69-74; runtime/active/test_continuation_memory13.py:1-26; reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/continuation-memory-evidence.json",
      "evidence": "validate_memory_budget(available, weight_bytes, owned_anon=0) requires available >= 64GiB and available+owned_anon >= 768799145984+weight_bytes+64GiB. continuation=True reads only anon from the already identified run slice; it does not add file/pagecache and does not raise cgroup limits. The actual C4 row (available 1192909017088, own anon 792834703360, sum 1985743720448) passes continuation while the same available alone fails the fresh-launch check. Four CPU boundary tests cover strict fresh launch, the C4 row, the 64GiB working margin and negative anon.",
      "impact": "Observe/reconcile can verify a healthy resident run without a second full allocation entitlement; new launch and prepare remain on the strict original budget.",
      "minimal_fix": "None; keep continuation only on no-allocation observe/reconcile."
    },
    {
      "id": "C5-F03",
      "severity": "runtime_condition",
      "status": "accepted_non_blocking_risk",
      "blocking_for_r1": false,
      "title": "Fresh compile/autotune may exceed the unchanged G1 budget and must fail honestly",
      "location": "runtime/active/prepare_host13.py:10-19,28-34; runtime/active/budget_control13.py:4; runtime/active/bootstrap_control13.py:80; runtime/active/control_watchdog13.py:4-34; runtime/active/cache_state_control13.py:600s path",
      "evidence": "The service remains 3420s with the 25s TERM/KILL reserve and 3300s bootstrap alarm; cache remains 600s. The six cache directories are created empty with cache_reused_bytes=0; all compile/autotune is untimed G1 initialization. No extension, coverage deletion or midrun budget increase was added. The actual C4 run reached the first KV/warmup but did not return business; first cleanup and transport reset are separately recorded.",
      "impact": "Fresh compile may make this G1 fail on the fixed budget; that is an honest runtime outcome, not a static source defect and not a reason to require an unguarded pre-approval compile. Formal timing remains separate and must freeze warm JIT caches under its own R2/v11 guard.",
      "minimal_fix": "Run G1 with the unchanged budget; on overflow retain the failure and do not extend or reduce coverage. Freeze warm caches only for separately reviewed formal timing."
    },
    {
      "id": "C5-F04",
      "severity": "medium",
      "status": "non_blocking_runtime_condition",
      "blocking_for_r1": false,
      "title": "root_control13 hardcodes an external local credential-file path",
      "location": "tools/root_control13.py:21-25",
      "evidence": "The root entry reads D:/Documents/deepseek-harness/default-workspace/HANDOFF.md, extracts exactly one password-like token from a sudo/密码 line, passes it to sudo -S and then deletes it from memory. The credential file is outside the repository and outside the manifest repository_inputs; no credential is committed or logged. If the path is absent, the token set is not exactly one, or the file is moved, root guard/observers/cleanup/snapshot fail closed.",
      "impact": "This is a real operator portability and secret-handling dependency; the actual operator environment must have the exact path and token. It does not affect the G1 runtime semantics or the reviewed source/model configuration.",
      "minimal_fix": "Prefer a reviewed --credential-file or environment-provided secret path with permission/ownership checks and no hardcoded user profile path; keep the existing fail-closed token-count check and never commit the credential."
    },
    {
      "id": "C5-F05",
      "severity": "low",
      "status": "non_blocking_hardening",
      "blocking_for_r1": false,
      "title": "Operator SSH lacks explicit BatchMode and the normal client run has no outer timeout",
      "location": "tools/run_control13.py:5-6,200-201",
      "evidence": "StrictHostKeyChecking, ConnectTimeout=10 and ServerAliveInterval/CountMax are explicit, but BatchMode=yes is not and subprocess.run for the normal client has no outer timeout. The bounded reconciliation is 25s and remote command calls have timeouts; a normal long control can legitimately run for the service budget.",
      "impact": "An auth prompt or a remote hang is not converted into a silent success; the operator can interrupt. This is a hardening gap, not a new false-pass path.",
      "minimal_fix": "Add BatchMode=yes/PasswordAuthentication=no and document the expected long outer client lifetime; keep the bounded 25s reconciliation and remote command timeouts."
    },
    {
      "id": "C5-F06",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "New 02:50 guard and the new canonical5 run are not yet deployed; C4/G1/R2 remain unpassed",
      "location": "runtime/active/run-manifest.json:2,8-9,1471-1474,1491; runtime/active/resource-window-extension-v3.json:10-31; runtime/active/install_guard13.sh:3,8-9,28; reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/outcome.json:1-82",
      "evidence": "The new run is sllm-glm51fp8-20261026T115129Z-d1cb0879 with candidate_revision canonical5, two bookings 18-21 and 21-03, cleanup 02:50+08 and timer 18:50 UTC. The new guard is not installed. Actual C4 ended after SSH reset and owned cleanup with exit137/OOMKilled=false, no business and no 32-rank audit; first cleanup returned 1 while final cleanup/release/root index passed. Historical exit137 remains separate and unknown.",
      "impact": "The new guard/run must be deployed and independently probed; C4 credentials/evidence cannot be reused. G1/R2 are not passed.",
      "minimal_fix": "Install and probe the new guard, refresh the two-booking window/resources, run the new canonical5 G1, and retain all C4 failure evidence."
    },
    {
      "id": "C5-F07",
      "severity": "low",
      "status": "non_blocking_metadata",
      "blocking_for_r1": false,
      "title": "C4 first cleanup returned 1 and was followed by a successful retry; no cause is inferred",
      "location": "reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/outcome.json:67-74; reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/first-cleanup-client-stdout.log:1-17",
      "evidence": "The first cleanup started 19:19:09+08, logged docker stop context canceled, showed service still running, ended remaining=5549ec44672e failed=1. The service then exited 137 at 19:19:34 with OOMKilled=false. A later exact cleanup/release/root index passed. The outcome attributes termination to the local operator request after SSH reset but does not claim the network reset origin or exact signal delivery.",
      "impact": "No source defect established; the new operator must produce its own cleanup/root evidence and preserve the first failure.",
      "minimal_fix": "None for source; retain first and retry evidence and repeat independent cleanup/release verification for canonical5."
    }
  ],
  "runtime_conditions": [
    "The new canonical5 02:50+08 guard is not deployed; C4 and earlier guards are not reusable; install and independently probe the new guard and slice before any G1.",
    "Two contiguous bookings 18-21 and 21-03 remain the window; re-query bookings and refresh GPU/UUID/boot/memory/NUMA/disk immediately before dispatch.",
    "Use only tools/run_control13.py and tools/root_control13.py for operator/root actions; they are repository-bound in the manifest. Record every client/root output with a unique immutable filename.",
    "On SSH return 255 perform exactly one bounded 25s identity/guard/monitor/original-budget reconciliation; running_verified or completed_verified returns 75 and observe only, never relaunch or call it G1; unknown/failed requests exact-owned cleanup, and the cleanup marker is request-only, never release proof.",
    "Observe must read the original CID and original 330/3420 monotonic budget, never create/restart/materialize or re-infer, and validate an existing release without overwriting it.",
    "Continuation=True is allowed only for no-allocation observe/reconcile: credit only anon from the already identity-verified own slice, require available>=64GiB and available+owned_anon>=768799145984+weight_bytes+64GiB; new launch/prepare defaults to continuation=False and the original strict budget. Do not add file/pagecache or raise cgroup limits.",
    "Fresh cache policy remains six empty CACHE_DIRECTORIES with zero reuse; all compile/autotune is untimed G1 initialization. If it exceeds 3300s/3420s or 600s cache, fail honestly; do not extend or delete 32-rank/business/60s coverage.",
    "After head GCS readiness and before the service worker, the CPU-only verify_store_imports/version preflight may run in the exact own no-GPU head; no serve/pool/model; preserve proof under /r; distinguish import-order/libglog failure from no-GPU libcuda absence; it is not G1.",
    "Each head must be Running with private GCS TCP ready before worker dispatch; stop only the recorded CID. Two Ray controls must each pass and complete their 60s release before the service batch.",
    "Real G1 still requires the 716GiB pool, 148-input hot cache, four native lifecycles, 32 rank records, PSS, 61-row idle60, three 60s releases and root-owned evidence index; R2 and formal admission remain unpassed.",
    "root_control13 currently requires the exact hardcoded local HANDOFF.md credential path with exactly one password-like token; ensure it exists and is access-controlled, never commit it, and prefer a reviewed credential-file/environment path. Add BatchMode/hardening without weakening the fail-closed checks.",
    "runtime/formal-prep remains an isolated incomplete draft, not in this G1 package and not admitted by this R1. Formal ledger remains 5 success / 4 failed / 31 unstarted; no formal ID consumed. No historical exit137 attribution is made."
  ]
}
```