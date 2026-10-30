# ServerlessLLM / GLM-5.1 FP8

Private repository for experiment code, patches, protocols, AI cross-review records, and sanitized lightweight results.

## Scope

Only compute entry: `qjhs003` (verified physical host `qjhs3`). Use official `zai-org/GLM-5.1-FP8` revision `f396cf805182f4ca10fa675e1a99815b3ca384db`, single-node TP=8, preserving FP8 weights, scales, and metadata. Distinguish backend reference, component validation, and full native service results. Historical claims require verification.

## Commit policy

- Commit relevant changes at milestones and every 30 minutes of active work at the next safe boundary. No empty commits.
- Keep commit and push outside formal measurement and monitoring intervals. Do not interrupt trials for Git.
- Push each commit and verify the remote SHA; record push failures honestly.
- Work directly on `main` under the user's current instruction; no force-push or published history rewrites.
- Stage explicit files after inspecting the staged diff.
- Bind reviews to commit SHAs and manifests. Use DeepSeek Harness or the user-authorized independent local read-only reviewer; distinguish code review, runtime qualification and result review.
- Record runtime code/config hashes, revisions, attempts, failures, and evidence indexes.

## Exclusions

Never commit passwords, tokens, private keys, sensitive handoff documents, model weights, Store data, environments, build caches, or large raw logs. Large evidence stays local with sanitized indexes, sizes, timestamps, and SHA256 hashes.

## Evidence and current state

- [STATUS.md](STATUS.md) gives the dated live state; its later historical sections preserve earlier failures and gates.
- [Global formal ledger](results/README.md) indexes all40 original IDs and their original result/origin paths and hashes. It is a snapshot at a documented safe boundary, not a live scheduler; refresh only after durable terminal and release evidence.
- [C17 full G1](reviews/actual-controls/sllm-glm51fp8-20261026T193424Z-685d6426/OBSERVATIONS.md) records the separately bound mathematical FP8/scale and native lifecycle reference.
- [C21 closed formal batch](reviews/actual-controls/sllm-glm51fp8-20261026T225201Z-02cd4cdc/OBSERVATIONS.md) records four audited formal successes, costs and complete release.
- [C23 continuation](reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313/OBSERVATIONS.md) records the closed26-ID continuation, approved raw/cache/monitor/release evidence and source archive. [Final stratified statistics](results/statistics.md) and [C23 costs](results/c23-costs.json) have separate independent reviews; all-history cost consolidation remains pending.

Keep node, code, protocol and preparation strata separate until equivalence and statistical review approve pooling. Historical failures retain their original denominator and are never retried or reclassified. This is an extension experiment on official GLM-5.1 FP8, not a claim to reproduce the paper's original models or all evaluations.
