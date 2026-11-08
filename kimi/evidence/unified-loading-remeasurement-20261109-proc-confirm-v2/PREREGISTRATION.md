# Proposed single revised supplemental block: proc-confirm-v2

READY FOR USER APPROVAL AND FRESH FULL RESOURCE WINDOW. No new GPU/model/Store execution has occurred for this revision. Published source98696ed4334fdb47d76ccf2127d481db59cbe5be, guard SHAe974f8976100d87aaa59a51abd036d4784cc16820b70326213412f70bae7519e.

## Retained earlier execution

The preceding supplemental block remainsFAILED: SFhotPASS, Storecold guardFAIL after successful endpoint/request gates, SFcold and poolhitNOT_STARTED. Its complete-block n=0. The16 observed rank endpoint pairs are retained separately (SFhot285.231648676s; Storecold95.4651402s); no old result/ID is reused here. See the published [earlier result](../unified-loading-remeasurement-20261109/runtime-public-v1/RESULTS.md).

Worker guard first recorded E13 reading /proc/1988981/exe and ABORTed3ms later. PID startticks/UID/cgroup at that error were not recorded, so neither ownership nor transience is known. No claim is made that this specific historical failure has been reproduced or conclusively explained.

## Minimal reviewed repair

Only four existing files changed: guard_cuda_micro.py and its existing test, prepare_kimi_four_condition.py and its existing test. The guard now retains partial identity/read-stage and the first error, and performs at most one20ms-budgeted confirmation per scan for the narrowly eligible initial exe-permission case. It binds the same PID/startticks and applies the existing exclusion rules to a validated fresh identity; persistent unreadability, owned/pinned/postowned/pidfd/recheck anomalies remain fatal. Before GO, no confirmation is attempted and the original discovery failure is retained. No deadline, normal-exit requirement or60s quiet window was relaxed.

Initial39 CPU tests missed a pre-GO None-time boundary. Independent review reproduced it and required a correction; that rejected snapshot and its review remain archived. The corrected guard passed40 independent CPU tests, and the four-condition renderer passed69 independent CPU tests. The final ten real spec/command/bundle artifacts were independently checked. These are CPU/source proofs, not runtime success.

The existing loader, native compiled libraries, image, TP8 configuration, all request/gold/cache rules and f919 endpoint overlay are unchanged. No FP/D2H fingerprinting, CUDA synchronization, capacity adjustment, new environment, payload copy, conversion, or second deployment framework was added.

## Single execution proposed

New cohort `kimi-unified-loading-20261109-proc-confirm-v2`, one new blockb01 with four unique new IDs. Order SFhot -> Storecold -> SFcold -> Storepoolhit. Each condition has one new observation only if the whole block passes; n=1 is descriptive, with no significance claim. No data is backfilled from the failed v1 or original40. Original40 remains38qualified/2failed, original complete blocks1/2/3/5/6/7/8/10 unchanged; GLM remains35success/5failed, no GLM rerun.

Metric: per attempt exactly8 authenticated host endpoint pairs, `(max(end_ns)-min(begin_ns))/1e9`; separate `max(end_ns-begin_ns)/1e9`. Begin before full model_loader.load_model, before fresh construction/storage allocation; end immediately after successful return and required weight processing, before optional runner setup. Host envelope only, not pure I/O or exact device completion. No added CUDA sync.

Success retains all original endpoint/ownership/request/normal Store/API/container exit and release>=60 gates. A single failed condition stops this block, later conditionsNOT_STARTED, no automatic retry. Failed partial observations are preserved separately, complete-block primary n=0 on any failure.

Maximum budget7200s/condition,28920s(8h2m)/block. All setup, pool/cache filling, shutdown, release and evidence remain covered. The prior12:00 expiry no longer supplies that maximum budget; provide a new continuous window, for example extend qjhs003 until14:00. No budget will be shortened to force admission.

Frozen protocol SHA256 `cadef37ff00c05fe8f3a5235b1548042e27e6d5f58b28c3f82dd52345b160a0f`; actual root command SHA256 `cab67c159401d9b077a98d8ebd3a3e41344fd0d74486d29d2aabda3875ef60b1`; bundle SHA256 `3e0a4fd5ca2f86376c96413f6ba48d3bc979e91d0cedbcb2467c070fed17af3b`.

The frozen protocol records approval/windowPENDING at preparation time; a later external receipt must bind this exact source/protocol/command if the user approves. Startup still needs a fresh read-only booking/resource/pin/root/guard/claim admission. Inform the user before occupying8GPUs. No Git or synchronization during execution/release. Actual four-condition results are required before replacing Figure5 primary data.
