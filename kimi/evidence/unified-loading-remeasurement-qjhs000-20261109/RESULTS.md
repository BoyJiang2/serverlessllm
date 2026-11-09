# qjhs000 supplementary measurement: failed before model loading

The approved one-block/four-condition run was actually started. Parent status: FAILED_RETAINED_NO_RETRY. First condition safetensors_disk_hot failed cache admission; remaining Store cold / SF cold / Store pool hit were NOT_STARTED. No model register/load endpoints were produced. New complete-block n=0, all four unified loading means/medians remain null. The32 expected rank slots are missing, not zero. The original40 and old failures/golds are unchanged.

Observed cache result:84 files were read completely (595,204,634,151 bytes), but unified end-of-warm mincore found98,275,914/145,313,657 pages resident (67.630198%). The first21 weight shards were only partly resident; the final43 were fully resident. The existing warm operation does not lock/pin cache pages. No short read was found; the cause of later nonresidency is unresolved. NUMA-local pressure, competing reclaim or external eviction are hypotheses, not conclusions. Do not lower the hot-cache criterion or claim this was a model/weight-loading failure.

The first condition retained its exact failure and cleanup classifications. Head guard quiet=60.1790005s; worker guard quiet=52.0636392s with abort_cleanup_exceeded/release60_incomplete. Observer quiet=60.7599779s, one query gap, max query21.8408149s; sampled peak is a lower bound. No normal-exit PASS is inferred.

After the original run stopped, a separately labeled read-only release observation covered65.9522545s with five snapshots: all eight GPU memory0/no compute apps, both own containers exited/PID0 (exit137), own scopes empty/gone and original pinned process identities gone. This confirms later safety release only; it does not repair original guard completeness or normal_exit=false.

Source91fb2a10481b44c9d50a2cd963ecf0ac0d250c80; frozen plan/actual commands were independently reviewed and published as75a4e5310d825ccdcede0d210ef89cd47a8861d3 before activation. Protocol b0346585d59ddc993041e94e4062a621bbb8cc7b56b9ae241c0c3a7385f4a165. q0md0 XFS RAID remains distinct from q3NVMe.

Raw small receipts are in runtime-public-v1 with exact source-path/SHA manifest; full root-private logs are retained on q0. Existing summarize_unified_loading.py produced JSON/CSV missing values without borrowing f7 or earlier measurements. Actual results independently audited in actual-results-independent-review-qjhs000-v1.json. The read-only cache analysis compares five located historical q3 SFhot PASS receipts, without rerunning them.

No GPU retry or later condition was launched. Figure5 cannot yet receive new Kimi bars from this cohort. The next runtime needs a concrete reviewed change/diagnostic and separate approval under the user's same-failure stop rule; CPU analysis and evidence publication do not grant it.
