# Actual partial block result (failed block, no retry)

Source: 9ee423906654d3105b330bfc6b75935b325090aa. Protocol: 8cba8aef2142ecd26812e3c5790086c989da03574bb4afeae468d917cdef34f2. User approved exactly this complete block and extension through 2026-11-09 23:00 Asia/Taipei; the extension was successfully booked before activation.

| Frozen condition order | Actual terminal | Observed complete 8-rank host span (s) | Max individual-rank duration (s) |
|---|---|---:|---:|
| SF hot | FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS | 302.153546032 | 302.043261813 |
| Store cold | NEW_PROTOCOL_EXIT60_L1_FAILED_RETAINED_NO_RETRY | 31.413848904 | 31.076709263 |
| SF cold | NOT_STARTED | null | null |
| Store pool hit | NOT_STARTED | null | null |

The Store-cold number is a bounded loading observation from a failed lifecycle attempt. It is not a qualified complete-block performance result. New complete blocks n=0. The first condition passed its full lifecycle; the second reached correct request results and complete 8-rank endpoints but failed normal Store shutdown. No replacement ID or automatic retry was used, and the last two conditions remain unstarted.

The common loading number is (max(rank.end_ns)-min(rank.begin_ns))/1e9 from eight complete same-instance/same-clock host endpoints. It excludes pool/cache preparation and post-load runner setup. It is not pure I/O or synchronized device completion. The runtime JSON field weight_load_host_span_s is this actual eight-rank envelope; it is not an f7 scalar relabel. Do not add it to or substitute it for max individual-rank duration.

Store-cold actual shutdown logs end at attempted=2880, failed=0, total=10240, elapsed_ms=59859. These are raw log labels; count semantics and source mapping are undergoing CPU review. There is no full normal shutdown duration measurement. No constant-rate extrapolation is an acceptance criterion or grounds to alter the frozen 60-second deadline.

The existing controller reported Store exit60 criterion exceeded and requested abort. Store worker container exited 137, OOMKilled=false. This is forced cleanup after the failed normal-exit criterion, not a normal-exit PASS and not proof of OOM. Both guards retained abort_requested failure labels. Safety release was separately verified: head/worker/observer quiet intervals all >=60 seconds, all related units MainPID=0, all 16 captured rank owners gone or PID reused, and eight GPUs used 0 MiB with no compute processes. Root terminal proof SHA: 6a03a63a3250528acc3ac2302352932149572173f6e106b19498815387c5d651.

No Git or synchronization was performed during the running block or release. This evidence publication occurs only after terminal/release confirmation. Independent actual-result review 0d237ee600a38e17305dee072daa7be7c2c437f6d8f42cabd8dd74ea31612b4b confirms the partial result, rank endpoints, request correctness, failure and safety release. Detailed CPU shutdown forensics and derived presentation are separate follow-up artifacts; no new GPU authorization or run is scheduled.

Original Kimi 40 conditions (38 qualified/2 failed), original eight primary blocks, accepted L1, GLM records and every historical failure remain unchanged. Do not backfill old Figure5 missing endpoints using these new IDs. This qjhs000 RAID/SF64MiB-Store640GiB cohort differs from historical qjhs003 NVMe and must remain separately disclosed.

EXPORT-MANIFEST contains original small receipts copied unchanged and source path/hash identities. Large metadata, raw ownership records, OCI and full telemetry remain server-only with hashes in RAW-REFERENCE-MANIFEST. Those raw paths are not substitutes for accessible public data, and missing observations are not filled with zeros.
