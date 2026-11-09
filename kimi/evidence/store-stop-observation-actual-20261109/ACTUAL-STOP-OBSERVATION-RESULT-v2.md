# One approved Store-cold stop observation: actual result

Run: `kimi-store-stop-observe-20261109-qjhs000-v1-b01-store_disk_cold`.
Source: `15b9e5c2c73c05ec93f3f4101e1868773b5bbe50`; frozen proposal/evidence: `cf7a1413b26ee158b94c3d43301bc7bf0ac1444e`.
Only one activation, exclusive root0600 claim; no automatic rerun or formal block continuation. All original IDs, golds, failures, accepted L1, original40 (38 qualified/2 failed), original8-block primary set and GLM freeze retained.

| Check | Actual result |
|---|---|
| Fixed60 Store criterion | FAIL retained; recorded elapsed60.493904292s, no promotion |
| Fixed300 observation | NORMAL_EXIT_AND_STRICTGONE at136.173089419s |
| Pool Release |160 progress records,10240 attempts, failed0, completed133.484s |
| Three requests / cache / ownership / clocks |All prerequisite gates PASS; math+translation strict, sky actual32/length, actual sampled trace checked |
| Containers |head+worker ExitCode0,OOMKilled=false; scopes gone |
| Independent release |head60.202276041s,worker60.179878012s,observer60.992055655s; owners gone; all8GPU0MiB/no process |
| Formal performance qualification |false by preregistration; diagnostic only |
| New four-condition complete-block n |0; no replacement of old8-block data |

## Timing, original endpoints and scope

Derived v2 corrects the A label/end_ns mapping in derived v1; all original runtime files and v1 remain unchanged.

- Full8rank host envelope31.501797433s; maximum single-rank full duration30.941608998s. Retained only as this diagnostic's special observation; not a formal condition mean or Figure5 complete block.
- Per-rank original endpoints/identities are in unmodified `runtime-public-v1/raw/unified-loading-host-span.json`; CSV is `runtime-public-v1/per-rank-full-endpoints-v1.csv`.
- Ingest/postprocessing inner endpoints are null (not instrumented in this frozen source). No subtraction, f7 substitution, new CUDA synchronization, pure-I/O or exact-device-finish claim.
- Prep observed common-environment event -> HTTP producer pre-register819.732905712s. Includes environment/Store pool/baseline/API/cache preparation; event starts after native controller entry. It is not the full Controller lifetime.
- Frozen consumer A: pre-register -> producer received_ns after parsing/context closure and before gold214.183276139s. The exact JSON-parse endpoint gives214.183258373s; actual endpoints differ by17766ns. Both are retained separately, without changing raw or frozen sample rules. Future GLM-aligned formal consumer must explicitly freeze its selected exact endpoint; this diagnostic does not claim the received endpoint is identical to parser return. Sky fixed32 is third request, not A. Producer read/parse/assert endpoints remain separate in raw receipts.
- Pool CLI launch -> successful config validation666.600970275s (includes launch/config polling/IPC, not allocation-only time).
- Native Controller ExecMainStart -> ExecMainExit1328.304410s, same-host CLOCK_MONOTONIC timestamps recorded in microseconds. Includes initialization, cache, loading, requests, deletion, ordered normal shutdown, release observation, evidence and return. No Prep+A replacement and no force-cleanup in this observed completion.

## Snapshot and counter reconciliation

Both10/20second files refer to hostPID2069228,startticks803266615, workerCID8621d417c0cac60fb5ed6edc492b4504caaacdd8b81cf95427c6b4eb9083f3cd. Main-thread actual snapshots are D with `rwsem_down_write_slowpath -> os_acquire_rwlock_write -> NVIDIA UVM unmap` path, syscall16,fd0x10,request0x42. The owner field's earlier S state is the captured expected identity, not the thread state at snapshot time. Reads are sequential, not atomic; these two points do not prove continuous lock residence or account for all136seconds.

The160 native progress records cover64,128,...,10240 attempted buffers, all failed0; explicit final completion line at133484ms plus normal process exit support full Release completion in this run. They do not give separate unregister/free/container overhead or explain cross-run variation. This is measured, not217second linear extrapolation. The previous interrupted60second failure remains failed; its counterfactual completion time is unknown.

## Storage, versions, evidence and next boundary

qjhs000 local XFS RAID `/dev/md0`, existing640GiB Store pool,64MiB chunks; this is not historical qjhs003 NVMe. Image/ec5b,Store ELF/95d,loader/625ad and runner/f919 unchanged; exact full hashes, mounts/config, resource/booking admission, activation and terminal release retained in small receipts. Full rank metadata and private raw OCI remain on server with original path/size/SHA references; no weight or Store copy, environment creation, code change or deletion.

`runtime-public-v1/RAW-REFERENCES-MANIFEST-v1.json` binds73 unmodified selected small original files (586022bytes); derived ownership capsule is explicitly marked and references the full original8MB record. Raw nonce/credential files are excluded from publication. Summary and independent review are additional derived records, never overwrites of raw receipts.

Next work is implementation/test/review/freeze of the three phase producer endpoints, actual consumer and full Controller capture for a new formal profile. Current evidence does not satisfy that new four-condition supplement. No further GPU/model/Store run is authorized by this single diagnostic approval, and no automatic retry is made. A future protocol cannot silently change the frozen60second rule; any proposed different normal-stop bound must be prospective and independently reviewed.
