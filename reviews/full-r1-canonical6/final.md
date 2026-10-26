```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "approved",
  "code_commit": "953900f630d077ce67b053e9b0418a1e624a6bde",
  "manifest_sha256": "99540f6e0ffcf632414e8f3a53206a51dc97f7560990d3abbb1ea4d5f80c5b29",
  "findings": [
    {
      "id": "C6-F01",
      "severity": "high",
      "status": "resolved",
      "blocking_for_r1": false,
      "title": "REMOTE now inserts the bound source directory into sys.path before importing preflight",
      "location": "tools/run_control13.py:31-37; tools/test_control_transport13.py:104-114; reviews/full-r1-canonical6/operator-tests.log; reviews/actual-controls/sllm-glm51fp8-20261026T115129Z-d1cb0879/outcome.json",
      "evidence": "The C5 failure was a stdin REMOTE that defined src but did not insert it into sys.path before importing preflight_control13; the failure occurred before any Ray container creation and was cleaned. The C6 REMOTE defines src, imports sys and inserts str(src) immediately; RECONCILE already had the same behavior. The new isolated Python -I stdin test first reaches the bound-source preflight sentinel and asserts that ModuleNotFoundError is absent, then stops before Docker/GPU/allocation. Nine operator tests pass; the C5 source/guard failure evidence is retained.",
      "impact": "The C5 preflight import failure is fixed in the affected operator path without changing G1 runtime semantics.",
      "minimal_fix": "None for the import behavior; keep the isolated stdin regression."
    },
    {
      "id": "C6-F02",
      "severity": "medium",
      "status": "resolved_supplementary_evidence",
      "blocking_for_r1": false,
      "title": "Actual host stdin probe imports/compiles the bound source and rejects the closed old guard; it is not G1",
      "location": "reviews/full-r1-canonical6/host-stdin-probe-v2.json:1-310",
      "evidence": "On the actual qjhs003 host Python 3.10 no-GPU probe, 11 necessary runtime modules imported with recorded paths and hashes, REMOTE and RECONCILE compiled, seven actor materialization derivations were inspect-simulated, and the old guard was inactive with the preflight refusal 'deadline service command changed'. The first probe assertion failed on a narrower expected-text match; its stderr is retained. The second probe verified timer inactive and refusal by the closed guard; no real guard was skipped for startup. The probe performs no Docker create/start, Store, weight load or inference and is not a G1 pass.",
      "impact": "Supports the C6 stdin/import and guard-refusal behavior; it is supplementary actual evidence, not a G1/R2 result.",
      "minimal_fix": "None; retain both probe attempts and do not treat the CPU probe as G1."
    },
    {
      "id": "C6-F03",
      "severity": "low_medium",
      "status": "carried_non_blocking",
      "blocking_for_r1": false,
      "title": "Previous C5 non-blocking operator/security conditions remain unchanged",
      "location": "tools/root_control13.py:21-25; tools/run_control13.py:5-6,200-201; runtime/active/prepare_host13.py:10-19; runtime/active/budget_control13.py:4",
      "evidence": "root_control13 still reads the hardcoded local HANDOFF.md credential path and fails closed unless exactly one token is found; the normal SSH client still lacks explicit BatchMode and an outer timeout; fresh compile/autotune remains inside the unchanged 3420/3300/600 budgets. C6 does not alter these; C5 was approved with these as runtime conditions.",
      "impact": "Portability/credential and SSH-hardening risks remain; fresh compile may honestly fail G1. None is a new C6 false-pass path.",
      "minimal_fix": "Prefer a reviewed credential-file/environment path with permission checks, add BatchMode=yes, and keep the fixed compile budget without extending or deleting coverage."
    },
    {
      "id": "C6-F04",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "New canonical6 run and 02:50 Oct27 guard are not deployed; C5 credentials and C4/C5 outcomes are not reusable",
      "location": "runtime/active/run-manifest.json:2,8-9,1471-1474,1491; runtime/active/resource-window-extension-v3.json; runtime/active/install_guard13.sh:3,8-9,28; reviews/actual-controls/sllm-glm51fp8-20261026T115129Z-d1cb0879/outcome.json",
      "evidence": "The new run is sllm-glm51fp8-20261026T123417Z-b1692e12 with candidate_revision canonical6, existing 18-21 and 21-03 bookings, cleanup 02:50+08 and a new 02:50 Oct27 guard not installed. C5 is stopped; C5 guard/probe/preparation plus 60s release/root index passed but no pool/model/formal ID, and its stdin preflight failure preceded Ray creation and was cleaned. C4 remains the prior failed run. G1/R2 are not passed; formal ledger is 5/4/31.",
      "impact": "The new guard/run must be independently deployed, probed and executed; old credentials/evidence cannot authorize it.",
      "minimal_fix": "Install and probe the new guard, refresh the two-booking window/resources, and run canonical6 G1 with its own release/root evidence."
    },
    {
      "id": "C6-F05",
      "severity": "low",
      "status": "non_blocking_metadata",
      "blocking_for_r1": false,
      "title": "Host probe references the C5 run, not the new C6 run; the C6 runtime source is unchanged",
      "location": "reviews/full-r1-canonical6/host-stdin-probe-v2.json:4; runtime/active/run-manifest.json:2,1471,1491",
      "evidence": "The probe run_reference is sllm-glm51fp8-20261026T115129Z-d1cb0879 (C5), while C6 is 20261026T123417Z-b1692e12. The probe source hashes match the C6 unchanged runtime modules; the new C6 guard/run is not probed by this file. The probe is not in the manifest repository_inputs.",
      "impact": "The probe is supplementary source evidence, not a C6 guard/run or G1 record.",
      "minimal_fix": "Retain as supplementary; use the actual C6 guard/probe and G1 evidence for the new run."
    },
    {
      "id": "C6-F06",
      "severity": "low",
      "status": "non_blocking",
      "blocking_for_r1": false,
      "title": "C4 first cleanup returned 1 before a successful retry; no cause is inferred",
      "location": "reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/outcome.json:67-74; reviews/actual-controls/sllm-glm51fp8-20261026T101414Z-b7e048db/first-cleanup-client-stdout.log",
      "evidence": "C4 first cleanup logged docker stop context canceled, service still running and remaining nonempty, then later exact cleanup/release/root index passed. The outcome attributes termination to local operator action after SSH reset but does not prove the network reset origin or exact signal delivery; historical exit137 remains separate. C6 does not change cleanup code.",
      "impact": "No new C6 source defect; the new run must produce its own cleanup/root evidence.",
      "minimal_fix": "Retain first and retry evidence; repeat independent cleanup/release verification for C6."
    }
  ],
  "runtime_conditions": [
    "The new canonical6 02:50+08 guard is not installed; C5 is stopped and its guard/probe/evidence are not reusable; install and independently probe the new guard and slice before G1.",
    "Existing boyjiang bookings 18-21 and 21-03 remain the window; re-query bookings and refresh GPU/UUID/boot/memory/NUMA/disk immediately before dispatch.",
    "Use only tools/run_control13.py and tools/root_control13.py; keep every client/root output in a unique immutable file. The hardcoded HANDOFF.md credential path must exist with exactly one token, stay access-controlled and never be committed; prefer a reviewed credential-file/environment path and add BatchMode hardening.",
    "On SSH return 255 perform exactly one bounded 25s identity/guard/monitor/original-budget reconciliation; running_verified/completed_verified returns 75 and observe only, never relaunch or call it G1; unknown/failed requests exact-owned cleanup. The cleanup marker is request-only, not release proof; existing releases are validated, not overwritten.",
    "Continuation=True remains only for no-allocation observe/reconcile; credit only anon from the already identity-verified own slice, require available>=64GiB and available+owned_anon>=768799145984+weight_bytes+64GiB; new launch/prepare stays continuation=False with the strict original budget. Do not add file/pagecache or raise cgroup limits.",
    "Fresh cache policy remains six empty CACHE_DIRECTORIES with zero reused compiled bytes; all compile/autotune is untimed G1 initialization. If it exceeds the unchanged 3300s/3420s/600s budgets, fail honestly; never extend or delete 32-rank/business/60s coverage. Formal timing must separately freeze warm JIT caches under R2/v11.",
    "After head GCS readiness and before the service worker, the CPU-only verify_store_imports/version preflight may run in the exact own no-GPU head; no serve/pool/model; proof under /r; distinguish import-order/libglog failure from no-GPU libcuda absence; it is not G1.",
    "Each head must be Running with private GCS TCP ready before worker dispatch; stop only the recorded CID. Two Ray controls must pass and complete their own 60s releases before the service batch.",
    "Real G1 still requires the 716GiB pool, 148-input hot cache, four native lifecycles, 32 rank records, PSS, 61-row idle60, three 60s releases and root-owned evidence index; R2 and formal admission remain unpassed.",
    "The C5 CPU/stdin probe is supplementary source evidence only, not a C6 guard/run or G1 record. The C4/C5 failed outcomes and first cleanup failures remain immutable.",
    "runtime/formal-prep remains an isolated incomplete draft, not in this G1 package and not admitted by this R1. Formal ledger remains 5 success / 4 failed / 31 unstarted; no formal ID consumed; no historical exit137 attribution."
  ]
}
```