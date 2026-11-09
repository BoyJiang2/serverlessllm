# Four-condition Figure5 supplement: frozen delivery and run request

Status: FINAL_ARTIFACTS_INDEPENDENTLY_ACCEPTED_NOT_EXECUTED. New formal samples: 0. This document is a concrete request, not an execution approval.

| Item | Frozen value |
|---|---|
| Node/source | qjhs000 (qjhs0), /home/boyjiang/kimi-new/ServerlessLLM; 9ee423906654d3105b330bfc6b75935b325090aa, pushed and remote SHA verified |
| Scope/IDs | kimi-unified-loading-20261109-qjhs000-formal64-v1-b01; four new condition IDs; one complete descriptive block, no inference statistics |
| Actual order | safetensors_disk_hot -> store_disk_cold -> safetensors_disk_cold -> store_pool_hit |
| Storage | q0 local /dev/md0 XFS RAID; SF /mnt/raid/models/Kimi-K2.6; existing Store /home/boyjiang/kimi-new/store-conversion-20261105-once. Differs from historical q3 NVMe; no new copy or conversion |
| SF / Store pool | SF 64 MiB, Store 640 GiB; chunk 64 MiB. Real CLI, spec and GetServerConfig consumer agree |
| Version | Same validated loader, f919 endpoint overlay, native ELFs and E974 guard; exact hashes in actual specs. 61 unique runtime pins independently verified |
| Cache | Existing per-file cold/hot and pool-hit checks; no global drop_cache, no rewarm-until-pass. The accepted 64-MiB diagnostic is dependency evidence, not model success |
| Weight-load metric | (max(rank_end_ns)-min(rank_begin_ns))/1e9 for complete matched 8-rank host endpoints. Missing identity/clock/endpoints => null. No new CUDA sync; cache/pool prep excluded |
| Other timing | Existing GLM-aligned register producer boundary and actual HTTP read/parse/validation receipts retained. Arithmetic, translation and fixed32 sky requests retained with Kimi template/gold |
| Correctness | Arithmetic/translation remain strict. Sky must generate actual 32 tokens and finish length; text/token differences separately retained |
| Budget | Per case 7200 s; phase caps sum 6480 s + 720 s supervision slack. Whole block 28920 s = 4*7200+120. Hard maxima, not expected runtime |
| Exit/release | Store normal shutdown <=60 s; API/containers normal exit, 8-rank ownership, exact-CID guards and >=60 s release. Forced cleanup is not normal PASS |
| Failure | First failed case stops remaining cases as NOT_STARTED. Append-only IDs/claims/evidence; no automatic retry, no deadline extension after failure |
| Acceptance | All four qualified for one new complete block. Report condition sample validity, terminal status, block terminal and cleanup separately |

Only two existing source files changed: prepare_kimi_four_condition.py and its test. Author 88 CPU tests passed; independent 88 CPU tests passed. Source/candidate review cd0196fb5f830ba76758faf9e19a4144aea425c525ab2f4e09036c30fe384fdd; final actual10 review 75d7ff3515270be475cc651ccd880fdb8931419f3ee8bf6156a5027961987b30. Actual source API gate passed; no GPU/model/full-weight/cache operation was performed during preparation.

## Actual executable entry

Run the argv from the immutable root-command JSON below exactly once as root, after approval and fresh admission. Substitute only the newly verified booking-expiry placeholder. Do not paste or execute the JSON as shell text; do not rerun the preparer as the runtime entry.

/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-unified-loading-20261109-qjhs000-formal64-v1-b01-formal-v3-8cba8aef2142-root-command.json

Root command SHA256: 703b3cd140ac1ff9739b56bee441861adcba4c47073ef0f7261969c11b2c4c40
Protocol SHA256: 8cba8aef2142ecd26812e3c5790086c989da03574bb4afeae468d917cdef34f2.

## Outstanding gates

The existing goal section 6 explicitly requires separately freezing and authorizing the post-diagnostic four-condition run. The approved cache-only diagnostic has completed; it did not register/load/infer a model. Broad permission to use the machine is retained, and will not be requested again as a generic machine-use question.

Request: authorize only this one frozen four-condition block, after a fresh booking window covers the entire 28920 seconds. The latest read-only API check showed qjhs000 booked by boyjiang until 20:00 Asia/Taipei on 2026-11-09, which is insufficient from current preparation time. Extend the actual booking (suggested to 23:00, subject to fresh API verification), or provide another full window. No reservation has been modified by this preparation.

Immediately before activation verify booking coverage, root authentication, all pins/actual OCI, idle resources and memory, unused run/unit/claim identities and independent guards/observer. Announce eight-card use before activation. No Git/synchronization during timing or release.

All old failures, original 40 conditions (38 qualified/2 failed), original eight primary blocks and accepted L1 remain unchanged. GLM records are read-only. Figure5 remains null until valid complete eight-rank model endpoints actually exist. No extra L1 probe, GLM run, conversion, or new 40-run campaign is included.
