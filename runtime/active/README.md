# Single active implementation — canonical input boundary

This directory contains the selected existing G1 source, not another frozen
candidate. Old review snapshots remain immutable. Source provenance is recorded
in `evidence/canonical-transition/active-source-selection.json`.

**No workload deployment or computation is approved.** The canonical path and
input boundary is implemented; a complete current R1 package and approval are
still required. The deployer requires a clean main commit and matching actual
review verdict. A path-only review cannot approve that deployment.

`workspace.py` supplies the sole host/container workspace and per-run output
location. The target source directory is `code/experiment/`; outputs belong in
`validation/controls/<run_id>/`. The existing canonical `code/weight_contract.py`
is reused in place rather than copied into this implementation.

`workspace.py --check-inputs` is the read-only input entry. It reuses the frozen
checkpoint contract, verifies gold/Store receipt identities and checks all eight
Store ranks and 80 part sizes. Host launch and container bootstrap call the same
function before workload startup; bootstrap checks before creating outputs.
This reads metadata and stats payloads, not complete payload hashes.

The canonical host root must not be mounted as an old flat `/experiment` root.
Use the explicit `container_mounts()` plan, including the single read-only
`protocol/run-manifest.json` mount. No old-path alias is required or present.

Sixteen CPU tests and eight isolated no-GPU container cases passed, including
empty checkpoint, missing manifest, truncated payloads, missing rank, changed
gold and a bootstrap failure before output creation. Probe source snapshots
under server `migration/runtime-repair` are evidence inputs, not deployed code.

Next: incorporate the preserved verified GPU/swap corrections separately, freeze
the complete current R1 package and seek commit-bound review. Actual guard/probe,
G1 and R2 remain unexecuted; no formal ID was consumed by these checks.
