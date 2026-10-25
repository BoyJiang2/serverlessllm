# GLM-5.1 FP8 experiment status

Updated: 2026-10-25, Asia/Shanghai. Stage: **G0 takeover / R1 changes required; G1 not started; R2/formal continuation forbidden.**

## Git and scope

Origin: https://github.com/BoyJiang2/serverlessllm-glm51-fp8.git (private).
Branch: `experiment/glm51-fp8-takeover-20261025`; only the main agent writes it.
Initial upstream repository HEAD: `87770ab9abb6e922f55bcf694dec5d0a675f8442` (README only).
Latest code commit at this snapshot: the initial takeover commit containing this file, to be identified by `git log`; no self-referential SHA is required.
Initial takeover commit1436dcb08fa665dbf3e834cff60f3040ad397492 was pushed and ls-remote verified at2026-10-25T15:25:52Z. This next candidate commit is not approval; its push must be separately verified.

Only SSH alias `qjhs003` is allowed. Current physical hostname is `qjhs3`. Eight ordered GPU UUIDs match the Oct24 historical records; the current SSH host public fingerprint also matches the preexisting known_hosts file (mtime Oct23). Current machine-id is captured; a historical machine-id snapshot is unavailable. Current boot differs following a reboot. No other compute nodes were accessed during this takeover.

## Current verified state

| Item | Evidence and qualification |
|---|---|
| Hardware/disk | Eight H20-3e, 143771MiB/card; about1.9TiB host RAM. brick1 is local XFS /dev/nvme1n1 with about2.17TB free at current audit; existing model+Store retained. No full download or Store copy. |
| Window | Existing user-authorized Azure bookings `bk-20261025T1009-fd1a95b3` (20:00–23:00) and `bk-20261025T1210-5c71ac5d` (23:00–03:00). Original-run root guard v6 was installed/probed for02:50 Oct26. New-control guard is NOT installed or verified yet. |
| G0 frozen assets |1,142 v11 file hashes rechecked and matched;150 checkpoint sizes and8 metadata hashes matched. No fresh full756GB weight/Store payload read has been performed in this takeover. |
| Historical A2–A4 | Frozen raw/postprocess and native lifecycle evidence exists. Supplementary local AI recheck compared48 rank records and12 business responses successfully. These are historical evidence checks, not current-runtime revalidation. |
| Formal IDs |9/40 terminal:5 success,4 failed;31 unstarted. Original order and failures retained. Next ID: `store_pool_hit_03`. v11 hot-cache control adds no formal result. |
| Historical revisions |v5:0 success/1 failure;v6:0/1;v7:4/1;v8:1/1. Do not pool automatically. |
| Legacy control closure |Ray-only12 completed120s stability but used original run identity, so it does not meet the new G1 identity requirement. Its own head and monitor were stopped.61 samples over60s then showed no GPU processes or running original-run containers; disk assets/reclaimable cache and the root deadline timer are explicitly retained. |

See `evidence/g0/`, `evidence/reference/`, `evidence/store/` and `evidence/import-mapping.json`. Runtime historical-v11 files are byte-mapped to the remote audit; they are archived reference source, **not an approved current launch command**. Their historical windows are stale.

## Review and unresolved failures

DeepSeek Harness installed CLI0.2.1-rc.3 ran a real independent-session read-only AI review, session `session-c073ee25-7d02-412f-9d72-c2cc8cf2dd4d`, exit0,60 read/search tool calls. Configured route was deepseek-official/deepseek-flash; the exact provider model identifier was not returned. This is neither external human nor institutional review.

R1 candidate13 verdict: **changes_required**, bound to manifest `d1136da321fd635d606dda7714e469ab55b450c6f873455f4599758935d079ba`. The review predates repository binding and is preserved as historical evidence; it does not approve this commit. Raw local output is indexed with SHA/size/time rather than uploaded wholesale.

Confirmed package defect: Windows text writing changed evidence-copy bytes, including parent manifest and gold; runtime originals were unchanged. Candidate13 remains immutable and unapproved. A separate candidate13b is in preparation, not yet reviewed, deployed or included as an approved execution path. Other R1 findings cover root-guard identity binding/verification, read-only assets, monitoring readiness/continuity and final release. Some findings need factual clarification: container loopback is isolated by private network namespaces; DSA topk workspaces are deliberate non-weight exclusions; existing direct human authorization covers the stated bounded Azure windows. These clarifications require the next review, not silent pass claims.

Historical exit137 is unexplained. OOMKilled=false/log absence does not prove external SIGKILL or exclude OOM. A separate Ray race/IP mismatch has direct evidence; fixing that race does not establish the cause of exit137.

## Next action and gates

1. Complete a new finite G1 candidate and commit it; bind its review to that commit and input manifest. Preserve original40IDs and gold.
2. DeepSeek R1 must approve static scope before new-control compute. Then verify actual new-run guard/probe, identity, window and monitors; run two fresh Ray-only controls, one bounded real pool initialization and affected native Store/Safetensors lifecycles, audits and60s release observations. No formal ID consumption.
3. R2 reviews complete source/FP8/cache/timing/statistics/gate and committed-to-runtime hashes; admission must pass before remaining formal IDs start in original order.
4. R3 recomputes results and true shared/overlapping costs. Report incomplete or failed conditions honestly and retain cleanup evidence.

Current files are an extension experiment on GLM-5.1 official FP8. They do not claim replication of the paper's original models or complete evaluation. No current G1/R2/R3 pass is asserted.

## Candidate13b milestone

New controls/candidate13b is unapproved and unexecuted, input manifest SHA 7682cfe51de10cc06a2715e8c831626a1ed1335e41ffef5364daa9da64b881f4. All Python candidates compile,9local admission tests and3shell syntax checks pass;35runtime source evidence copies match parent frozen hashes. Root guard/probe and full model lifecycles remain unrun. New booking bk-20261025T1538-fe2ffc58 extends03:00-09:00; planned new control guard08:50. Human permits continued use beyond booked windows if no later conflict; refresh/revalidate before any needed guard renewal, never stop another job. Original-run v6 remains02:50 until separately verified handover. R1b will bind this committed code and manifest.

## R1b conclusion

DeepSeek session session-f3000b94-fe59-4aa3-ad69-87b75b64eebf completed with changes_required, code e1af9dd51055c2d1e4e8e3c2ea3dc0a9227f1592 / manifest7682cfe51de10cc06a2715e8c831626a1ed1335e41ffef5364daa9da64b881f4. No new guard or G1 compute started. Probe status, persistent evidence, acceptance binding and budget checks need a new revision. Existing21Store log prefixes provide18pool-marker counterexamples to the speculative logging blocker; submitted for re-review, not silently dismissed. Latest source/payload statuses unchanged.

## R1c work in progress

Unapproved controls/candidate13c adds real sentinel/probe exit checks, persistent root evidence, stronger acceptance/commit binding and bounded budgets. It is NOT runnable/frozen: input manifest is not regenerated yet, tests/ledger/review pending. Dedicated user-authorized read-only SIGKILL investigator has newly bound CID to hostPID/cgroup and found a33.8s non-atomic monitor gap; synchronous large-Store PSS is being removed from fast heartbeat before any G1 execution. No new node compute or formal ID consumed. Last reviewed candidate13b remains unchanged.

## 2026-10-26 01:21 local-only checkpoint

User resource hold remains active: no new Ray, pool, cache control or model execution. Candidate13c remains unsealed/unapproved; seventeen CPU unit tests pass, including optimized-entry rejection and synthetic acceptance failures. Fast monitoring no longer reads smaps_rollup; bounded private-namespace PSS is separate. Privileged read-only historical journal collection found dockerd escalation of the exact failed formal6 container after a 90s SIGTERM wait; upstream stop requester/reason remains unknown. Original exit137 stays failed and is not attributed to Ray. Current G1/R2 remain pending. Full frozen-input hot-cache control and current R1 review still need completion before runtime.

## R1c sealed for read-only review

Candidate13c now includes frozen148-file hot-cache control, strict full mincore vector acceptance and separate bounded PSS.25CPU tests, all Python compile and3bash syntax checks pass. Input manifest2feefa1e9d74412084fd71cb5b412f9a9f95d6181ab983d95d5c29550e31eb56 (178files). Runtime/G1/R2 unexecuted/unapproved; resource hold active. Historical dockerd escalation evidence and independent joint-review receipts included; upstream requester/reason remains unknown. Current code commit is the commit containing this milestone; next action is actual DeepSeek R1c review, never compute while resources occupied. Formal ledger unchanged9terminal/31unstarted.

## Historical result collection milestone

R1c b5b48807fe04251777c324d52f9e015c5d05aad5 was pushed/remote-SHA verified; real DeepSeek session-f3000b94-fe59-4aa3-ad69-87b75b64eebf is reviewing it read-only. Fresh exact-ID metadata collection retrieved18small files (20789bytes), all9terminal result hashes equal G0 and31unstarted directories remain absent. Interim descriptive recomputation uses8revision/condition strata, excludes failed records from successful-latency aggregates while preserving their raw metrics, and does not manufacture final paired uncertainty. R2-PREPARATION.md records remaining dependency/guard/normal-flags/budget requirements; no G1/R2 approval. Supplemental readonly review found outer3600s vs bootstrap~3721s fail-closed budget mismatch; do not alter active-reviewed candidate until verdict, then fix/reseal/review. Resource hold remains active.

## CPU evidence and owned-resource safety

Thirty historical event-to-event metrics recomputed from raw timestamps agree within1ns; this is not R3 approval. Exact-owned snapshot17:31:49Z shows original-run actual nested cgroup pids.current0/no running containers, charged memory80875520bytes, active02:50guard; new G1 inactive/no timer/no containers. Collector v1 guessed a flat cgroup path, so its nulls are not zero evidence; corrected v2 and explicit boundary are preserved. A supplemental readonly source check also found supervisor total-budget accounting/late-exit false-pass risk; neither confirmed code defect has executed remotely. Candidate13c stays immutable while actual DeepSeek review runs, then a new revision must fix both before compute.

## R1c conclusion and renewed resource window

Actual DeepSeek R1c session-f3000b94-fe59-4aa3-ad69-87b75b64eebf exit0/63read-only tool calls: changes_required, code b5b48807fe04251777c324d52f9e015c5d05aad5 and manifest2feefa1e9d74412084fd71cb5b412f9a9f95d6181ab983d95d5c29550e31eb56. Prior speculative log/loopback/logprob findings withdrawn; remaining deployment/cache/monitor/release/budget findings require individual factual assessment and a new immutable candidate. Two budget defects have synthetic CPU counterexamples; no remote execution. Fresh01:36node snapshot:8GPUs0MiB/no compute process,MemAvailable1974936292KiB. Booking snapshot now has boyjiang bk-20261025T1730-97c53619(01–03) followed by bk-20261025T1538-fe2ffc58(03–09); oldAzure23–03removed. New window must bind actual two rows and08:50guard, not stale overlay. Recheck again before launch. No G1/R2/formal pass.

## 2026-10-26 coordination-only round

Latest human scope is planning and file/user-mediated cross-review only: no compute, shared-lock deployment, permission changes, peer-resource changes or cross-thread contact. Read inherited AGENTS, authoritative goal, current STATUS and actual R1c changes_required verdict. Existing untracked candidate13d is preserved untouched and remains unsealed/unapproved; no R1d/G1/R2 pass. Fresh01:55:41qjhs003 readonly snapshot:MemTotal1923.283GiB,MemAvailable1883.487GiB,8GPUs0MiB/no compute PID; other containers remain; original own slice pids0/anon0/shmem0 and new G1 inactive. Old02:50v6guard remains active; no new guard or shared lock installed. Booking API01:58confirms01–03+03–09boyjiang and another Azure booking from09:00. Existing142shard+80Storepayload files total1518214688848logical bytes, stat-only/no payload reads.

Draft docs/coordination/sllm-proposal.md, shared-contract-proposal.md and cross-review-checklist.md with hashed evidence propose complete-batch exclusivity, one XFS namespace/implementation, durable abnormal-state handling, owner-specific release plus independent incoming verification and FIFO/alternating fairness. Proposed90/120/150min slots are not yet reviewed/enforced runtime budgets. Node8 actual UID, memory/Store/NUMA/RDMA/000-001linked-resource budgets and batch limits remain unconfirmed; no Node8 proposal received, so no review-of-node8 or mutual approval is fabricated. Original40IDs/gold unchanged:5success4failed31unstarted, nextstore_pool_hit_03. Next: user forwards exact Node8 proposal/commit for readonly review, at most two concentrated rounds; do not resume experiment execution merely because GPUs are free. Current milestone commit contains only coordination docs/evidence and STATUS; source code head before this round47ad57803caba6017633ab8d69f8b922b489a420, latest reviewed runtime codeb5b48807fe04251777c324d52f9e015c5d05aad5.

Coordination drafts milestone339bde83097ce1df5b8f67ed3ea187b6352f5a70pushed and remote-SHA verified. Local checks verified every relative document link, next3original IDs, disk arithmetic and exact proposal/evidence hashes against committed blobs. Follow-up pins docs/coordination raw bytes with .gitattributes to avoid checkout newline hash drift and states frozen model/gold/prereg plus final verifier identity explicitly. No Node8 proposal/confirmation received, no shared contract approval, no new runtime code or remote resource mutation.
