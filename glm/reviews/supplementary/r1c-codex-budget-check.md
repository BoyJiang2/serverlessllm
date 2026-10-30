# Supplementary Codex read-only source check of R1c

Requested by main under the user's existing authorization for an independent read-only subagent. Agent /root/independent_readonly_review reviewed candidate13c code b5b48807fe04251777c324d52f9e015c5d05aad5 / manifest2feefa1e9d74412084fd71cb5b412f9a9f95d6181ab983d95d5c29550e31eb56. It performed no SSH, execution, edits or Git actions. This supplements the actual ongoing DeepSeek R1c and does not replace/approve R1 or R2.

| Confirmed source defect | Evidence | Required correction / test |
|---|---|---|
| Admission thresholds disagree | budget_control13.py permits remaining>=3600; bootstrap_control13.py first deducts120, truncates to int, then asserts>3600, effectively about3721. | Use one budget definition at all entries; test boundary values. Fail-closed refusal, not evidence of an overrun. |
| Outer total budget is not enforced as documented | supervise_control13.py checks3420/330 only while child is running. A child may exit between polls after the limit and be marked passed. Finally may wait20s plus unbounded wait after kill. | Reserve cleanup inside total budget, bound TERM/KILL waits and check elapsed after child exit before success. Test short-budget child exits across boundary, delayed TERM and ignored TERM. Current independent root deadline remains another layer, not proof of this bound. |

Source compatibility checks found no inevitable producer/consumer mismatch in the frozen cache path and fields: bootstrap→cache_g1hot13→accept, initial eviction vectors and repaired final full vectors. PSS output paths correspond; timed-out/incomplete/not-exited collector is rejected before next cycle. Root snapshot/cleanup/release paths show no inevitable filename conflict. These are source-level findings, not an executed real cache/PSS/guard/lifecycle pass.25synthetic CPU tests do not prove runtime behavior.

Main independently read the cited code and agrees these two budget defects need a new revision. Candidate13c is kept immutable during DeepSeek's active review. Correct in a new candidate, rerun CPU tests, commit/push, bind new manifest/commit, and obtain affected-file R1 review before any compute.
