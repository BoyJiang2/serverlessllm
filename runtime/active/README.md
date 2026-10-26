# Single active implementation — path migration work in progress

This directory contains the selected existing G1 source, not another frozen
candidate. Old review snapshots remain immutable. Source provenance is recorded
in `evidence/canonical-transition/active-source-selection.json`.

**Do not deploy or run this incomplete implementation.** No input manifest or
current R1 approval exists. Existing imported modules still contain old paths
until the canonical-path commit is complete. The previous deployer also rejects
the current main branch. No remote compute is authorized by this directory.

`workspace.py` supplies the sole host/container workspace and per-run output
location. The target source directory is `code/experiment/`; outputs belong in
`validation/controls/<run_id>/`. The existing canonical `code/weight_contract.py`
is reused in place rather than copied into this implementation.

Next: adapt callers, Docker mounts and readonly reference paths; run CPU path and
config regressions. Apply the preserved verified GPU/swap corrections separately,
then freeze and seek actual commit-bound DeepSeek R1. No G1 or R2 is implied.
