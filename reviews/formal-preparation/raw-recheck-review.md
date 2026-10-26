# Supplemental readonly review of the offline raw checker

Reviewer: existing user-authorized Codex subagent /root/independent_readonly_review. Scope: tools/check_g1_raw13.py, its CPU tests, current validator, pinned weight contract and gold format. No remote/GPU access, file changes or tests by reviewer. This is supplementary AI review, not DeepSeek R2 or runtime admission.

Initial finding: comparing actual rank records with currently readable reference records did not independently bind those references to the frozen gold's rank_audits SHA256 values. Actual and reference could be corrupted identically. Accepted and fixed: require exactly eight gold rank entries, verify every original reference file SHA before comparing, bind run manifest and coverage contract, and include event hashes in the receipt.

Final reviewer conclusion: the reference-hash gap can be closed; full32rank/12response fixture plus matching-corruption rejection covers it. No new certain blocker found in the final readonly source review. The prepared bootstrap pool_config repair is present and no longer an open finding.

Main-agent verification:5offline-checker CPU tests pass. Prepared formal suite35tests pass, including actual RPC statement regression; before-fix failure retained in bootstrap-config-before.log. Host stdin precheck on qjhs003 returned1 with ValueError:raw audit events incomplete, as expected before current G1 completes; reference paths and pinned hashes were checked before that rejection. No fabricated passing receipt and no formal ID consumed.

The checker processes existing CPU JSON evidence only, never loads checkpoint payloads or mutates the remote run. It reports its narrow scope explicitly. Full G1 still needs lifecycle/release/monitor/root acceptance, then the actual R2 review and formal admission.
