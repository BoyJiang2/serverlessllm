# Response to actual DeepSeek path review v1

Old verdict changes_required and its commit/manifest are preserved unchanged.

| Finding | Decision | Evidence / minimal correction |
|---|---|---|
| PB-01 | Accepted | Before-fix unit counterexample changed valid restore JSON was accepted. Bind restore and native-weight-contract hashes from historical manifest and fresh canonical sha256sum; verify both in shared pre-output check. Six actual missing/empty/changed-valid-JSON container cases pass. |
| PB-02 | Factual finding rejected | All eight original docker_args contain --env=SLLM_CONTROL_WORKSPACE=/experiment, including bootstrap; verified programmatically. No env repair was necessary. New receipt explicitly records env presence and adds a writable sentinel output mount; bootstrap failure leaves it empty. |
| PB-03 | Accepted | Before-fix synthetic directory-link test did not raise. Reject rank symlinks/non-directories/out-of-Store resolution. Actual Linux rank symlink escaping to another readonly-mounted rank is now rejected. |
| PB-04 | Host-only option implemented | historical_path and final accept enforce host_workspace() before file reads. INDEX/results remain host-only instead of adding broader container mounts. Unit tests reject both inside /experiment. |
| PB-05 | Accepted | Reuse package inventory module for require_full_r1. Explicit approved/gate R1/scope full-runtime and matching identities required; partial markers rejected. No new wrapper. Positive full-R1 schema and eight negative mutations tested. |
| PB-06 | Optional change declined | Keep the two-line fresh gold check at business validation as defense after startup. It reads the same single manifest reference; no separate rule/configuration or helper abstraction was added. |

Probe-only snapshots are not active control deployment. All original protocol, gold, IDs and failures are unchanged. Full runtime R1/guard/G1/R2 remains required.
