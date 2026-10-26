# Formal entry preparation — not admitted

This directory is physically separate from the frozen `runtime/active` G1
source. Nothing here launches a workload or approves an attempt. The original
forty IDs, nine terminal results and gold remain unchanged.

`attempt_state.py` addresses the first migration hazard: a new execution's empty
output directory must not cause the original nine IDs to run again. It verifies
the frozen preregistration, baseline and original result hashes, and combines
only explicitly declared execution roots. Duplicate IDs and incomplete consumed
attempts block admission. Interrupted attempts require a separate evidence-based
terminalization step; absence of a result never means the ID is available.

The v11-derived `formal_trials.py` and `formal_gate.py` are incomplete integration
drafts. The gate rejects G1/old revisions and requires current R2, exact source,
normal-flags evidence and host preflight. Its unit tests stub runtime checks;
they do not prove a live gate passed. The driver retains the original measurement
body and uses the verified baseline instead of directory absence to select IDs.
`source-provenance.json` records the two necessary historical dependencies that
were absent from the active source; common active dependencies are reused.

The bootstrap and cache entry drafts are now present. Store cache coverage is
exactly 80 parts plus 28 required metadata paths, with 22 Store metadata hashes
collected without payload reads. The prepared `validation_control13.py` is a
temporary replacement for the shared active module: it fixes the reproduced
normal-flags audit-environment error and selects its declared identity. Before
R2 it must become the one active imported module; do not maintain two variants.

Remaining integration: formal monitor/role/budget/template mapping, complete
warm-JIT provenance, bounded initialization and per-attempt admission,
actual normal-flags validation,
full source/binary/mount manifest and current R2 gate. Reuse the existing reviewed
primitives rather than add switches to the G1-only entry. All four conditions
retain the common Store pool. Validation and public initialization costs remain
outside per-attempt timing and inside deployment cost accounting.

Formal execution remains forbidden until actual G1, R2, fresh resource/window,
own guard/probe, current remote ledger and full admission pass.
