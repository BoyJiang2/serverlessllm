# Supplemental loading block b01: retained partial observations and failed block

Source c9aa31cee4315a9254b1f72ca32dc91731f5c8c0; protocol SHA5a0fa51dfccab65892be035332ea6d0f356e9db9f55fdc8112bb1ef1116483ea. This is separate from original40 and the original eight complete blocks.

Four planned new IDs: two launch-requested, SFhot passed, Storecold failed during final shutdown after successful loading/requests, SFcold and Storepoolhit NOT_STARTED. Parent status FAILED_RETAINED_NO_RETRY. No automatic retry occurred. The new complete-block main analysis has n=0 for every condition, null mean/median; partial observations below cannot replace complete-block means or original samples.

| Condition | Observed8-rank host span/s | Observed max single-rank duration/s | Workload and terminal |
|---|---:|---:|---|
| SFhot |285.231648676|285.202741492|Three requests, normal exit and release>=60 passed|
| Storecold |95.465140200|95.342349852|Three requests and endpoint proof passed; guard failure retained; normal final cleanup failed|
| SFcold |null|null|NOT_STARTED|
| Storepoolhit |null|null|NOT_STARTED|

The spans were recomputed independently from16 actual rank endpoint pairs. CSV has32 expected slots,16 observed and16 null; neither missing values nor failures are replaced with zero. All8 endpoints per observed condition retain PID/startticks/model-instance/clock/cgroup/device-owner evidence. `condition-terminal NORMAL` refers to the workload/delete-baseline stage and does not imply successful full lifecycle cleanup.

## Proven failure sequence

At host monotonic1290172.048064297, worker guard recorded PermissionError errno13 reading /proc/1988981/exe. At1290172.051174345 it wrote ABORT(proc_discovery), killed its owned worker scope and signalled pinned runtime processes. Controller healthy() then failed during head controller_shutdown execution; that command ended with context canceled. This is not evidence that the Store shutdown call exceeded60seconds: final normal Store shutdown was not reached.

The failing discovery record did not retain PID1988981 startticks, UID or cgroup. Its ownership and whether the permission error was transient remain unproven; no claim of unrelated PID, numerical error, OOM or PID quota failure is made. Own worker cgroup samples adjacent to the error have pids.max4096/pids.events max0 and memory.events oom0/oom_kill0. Cumulative ancestor counters are not attributed to this attempt.

Both failed-case container exits were137 after ABORT, OOMKilled=false; preserve this as forced failure cleanup, never normal-exit PASS. Guard quiet observations: head60.044712207s, worker60.000663415s, independent GPU observer60.369230816s. Postrun verification found all8 GPUs0MiB and relevant unit MainPID0. Failure classification and original cleanup-result remain unchanged.

## Artifacts and limits

results-v1.json/csv contains the actual recomputation and separate observed/qualified/normal-cleanup/parent status fields. EXPORT-MANIFEST.json SHA-binds84 copied small raw files; the large8MB ownership metadata files and raw private logs remain at their original server paths with source digests, not duplicated. failure-proc-discovery-sequence.json records the first error and adjacent original events. postrun-release-verification.json records the later release check. Source/CPU-only guard revisions, if proposed, do not modify this run, its pins or its labels; a further GPU attempt requires separate approval and a new full resource window.

Original40 remains38 qualified/2 failed, primary blocks1/2/3/5/6/7/8/10 unchanged. GLM historical40 remains35success/5failed, no GLM rerun. This failed new block does not provide the full four-condition replacement Figure5 data.
