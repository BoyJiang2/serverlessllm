# ServerlessLLM / GLM-5.1 FP8

Private repository for experiment code, patches, protocols, AI cross-review records, and sanitized lightweight results.

## Scope

Only compute host: `qjhs003`. Verify whether it is the historical `qjhs3` before resuming. Use official `zai-org/GLM-5.1-FP8` revision `f396cf805182f4ca10fa675e1a99815b3ca384db`, single-node TP=8, preserving FP8 weights, scales, and metadata. Distinguish backend reference, component validation, and full native service results. Historical claims require verification.

## Commit policy

- Commit relevant changes at milestones and every 30 minutes of active work at the next safe boundary. No empty commits.
- Keep commit and push outside formal measurement and monitoring intervals. Do not interrupt trials for Git.
- Push each commit and verify the remote SHA; record push failures honestly.
- Use a dedicated experiment branch; no force-push or published history rewrites.
- Stage explicit files after inspecting the staged diff.
- Bind reviews to commit SHAs and manifests; DeepSeek Harness performs read-only AI cross-review.
- Record runtime code/config hashes, revisions, attempts, failures, and evidence indexes.

## Exclusions

Never commit passwords, tokens, private keys, sensitive handoff documents, model weights, Store data, environments, build caches, or large raw logs. Large evidence stays local with sanitized indexes, sizes, timestamps, and SHA256 hashes.

## Initial state

This initial commit contains repository guidance only. The main agent must import audited experiment code and the updated full goal prompt, add ignore rules, and verify correspondence with runtime files. Repository creation does not certify any experimental result.
