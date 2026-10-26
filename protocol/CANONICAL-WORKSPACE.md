# Canonical workspace transition — input boundary verified, no runtime approval

The sole SSH entry is `qjhs003`. The server workspace is
`/data/brick1/boyjiang/glm5.1-fp8`. The human explicitly moved Git work to `main`;
the deleted experiment branch is not to be recreated. No old gate approves this
transition. Latest actual DeepSeek R1e is `changes_required` at `ebc42c8`.

## Verified state

The migration receipt SHA is
`f70317f5c962181cb686c0b6717fab50a2209fdc78de6b039acc344e80874e9f`.
The independent 2026-10-26 11:31 +08 audit checked all 260 recorded
device/inode/size/mtime identities and all 1,142 frozen source/binary/config hashes
(273,348,388 metadata bytes). Both mismatch lists are empty. This is not a new
payload hash of the checkpoint or Store. The old long path resolves to
`glm5.1-fp8/archive/original-run`; assets are not a second copy.

All nine terminal result hashes still match; 31 unstarted directories are absent.
The original counts are 5 successful, 4 failed, 31 unstarted. The next original ID
is `store_pool_hit_03`, only after current G1, R2 and formal admission.
Raw verification and pending-fix provenance are in `evidence/canonical-transition/`.

The 11:29 resource snapshot has eight original UUIDs, 143771 MiB/card, 0 MiB used
and no compute process. The 11:33 booking query confirms boyjiang's
`bk-20261026T0248-fd6bea51`, 10:00–18:00 +08. A new 17:50 guard is still **not
installed**. Every runtime admission must refresh occupancy, NUMA/memory, window,
remaining budget and the actual run-specific guard. No current control process
has been started by this transition.

## Existing implementations and missing pieces

| Purpose | Canonical existing material | Decision / remaining work |
|---|---|---|
| Official FP8 input | `checkpoint/`, frozen revision `f396cf805182f4ca10fa675e1a99815b3ca384db` | Reuse; no download, copy, dtype conversion or new model identity. |
| TP8 Store | `store/tp8/`, eight ranks, 80 payload files | Reuse; keep original values/scales/config and native model layout inside the container through explicit mounts. |
| SLLM Python | `code/ServerlessLLM/` | Reuse patched source; confirm actual imported files during guarded admission. |
| Store Python/C++ | `code/Store-source-v7/` | Historical PYTHONPATH selects this first; checkpoint extension SHA `259ca21a1b34f5a50667f70d2df4571d0f51572affbc982fec9267f2dfe7b096`. |
| Bundled Store | `code/ServerlessLLM/sllm_store/` | Different checkpoint extension SHA `94a651e6155ff0155a1d0b3001901928f080da608175e0ee6638865548547eee`; do not substitute it by name. Defer archival until actual imports and references are proved. |
| Frozen protocol | `protocol/`, `validation/` and historical archive | Read in place. Preserve gold, preregistration and historical manifests byte-for-byte. |
| G1 control scripts | Not present under canonical `code/` | Import only the needed functions from frozen local `candidate13e`; no whole candidate/evidence/environment copy. |
| Pending CPU fixes | Local `work/precanonical-candidate13f-preserved` | Unpublished, never reviewed/deployed; preserve 66-test evidence and apply only the necessary corrections to the single active implementation in a separate behavior commit. |
| Existing environment/build inputs | Necessary files under `archive/original-run` | Explicit read-only mounts/reuse, no environment copy; inventory exact files and cache needs before preparation. |
| Current deadline and monitors | None deployed for current control | Adapt existing installer/cleanup/monitor to canonical paths, fresh source manifest and run identity; actual probe/evidence still required. |

Static import closure plus explicit subprocess targets identifies 29 necessary
runtime Python modules. This inventory is a starting source selection, not proof
of all transitive runtime imports; templates, native import receipts, binary and
generator/config dependencies must also enter the final gate. The old preparation
generator, obsolete guard/window versions, duplicate validation entry point and
copied evidence source trees are not active deployment inputs.

## Minimal path change

Maintain one editable local implementation at `runtime/active/`, deployed as
`code/experiment/`. Keep each run's mutable output under
`validation/controls/<run_id>/`. One small path module supplies source, workspace
and run-output paths; replace repeated old literals rather than adding another
launcher or wrapper. Existing entry points, fixed budgets and predicates remain.
Run registration may copy the small frozen manifest/window to its output directory;
that is evidence, not a second source tree.

Use explicit Docker mounts from canonical host locations. Mount source,
checkpoint, Store and reused environment inputs read-only; mount only the current
run's output/cache directory writable. Container `/experiment` is a logical
runtime view. The native Store relative layout may be retained by an explicit
`store/tp8` mount; it must not create another physical Store asset. Do not mount
the entire historical parent writable or create nested mountpoints in the archive.

Required path changes cover deploy, source verification, manifest/reference reads,
materialization, container arguments/PYTHONPATH, root guard/probe, observers,
fast monitor, cache helpers, native audits, PSS and final acceptance/release.
All future writes must resolve inside the canonical workspace, outside `archive/`;
the old compatibility symlink was removed after the limited dependency review
and pre/post asset verification. Historical mapping strings remain immutable;
they are not filesystem dependencies.

## Separate changes and validation

1. Path/source inventory and migration evidence: no runtime behavior change.
2. Single active implementation and canonical paths: CPU path/import/config tests,
   syntax checks and old-path write/mount rejection tests. Not runnable approval.
3. Apply verified R1e corrections in place: regressions for early retained engines,
   raw host GPU receipt consistency, post-delete sample freshness and swap limits.
   Root missing non-root-only cgroup interfaces is expected ABI, not a missing
   limit to repair globally. Preserve every existing correctness/resource check.
4. Freeze one complete current file manifest at a review milestone and commit/push
   on main. DeepSeek reads actual files/commit; do not create a source tree for
   every small correction. Re-review affected edits against that same active tree.
5. Only approved source proceeds to actual guard/probe/identity/monitor, bounded G1,
   R2 and formal admission. Store readiness/readback expires on shutdown.

The G1 plan still includes two independent Ray controls (330 s each), one real
716 GiB pool, the separate 600 s full-input cache control and four native
lifecycles (two per format) inside the 3420 s service-control total. Each startup
requires its complete maximum plus 180 s observation/cleanup/forensics margin.
Every delete retains its full 60-second observation, and all 32 rank records
(2 formats × 2 cycles × 8 ranks) are required. These are unexecuted budgets,
not measured durations or an entitlement to the node.

Neither simplification nor path migration changes TP8, utilization0.9, context2048,
seq1, MTPoff, eager, 256 KV blocks, official FP8 values/scales or the public Store
pool semantics. Historical exit137 remains failed: daemon escalation is observed;
the upstream stop requester/reason is unknown. No formal ID has been consumed.
