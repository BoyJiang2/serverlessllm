# Kimi three-stage Figure 5 supplement: completed block

Block: `kimi-load-stages-20261109-qjhs000-v1-b01`.
Source: `87bc7ab2a3106eaea50ace11e2a000e228a0c9c5`.
Protocol SHA256: `78beeac12a703ad7c26c7e42475a5e0918cf6741d08fbba1ec24ed933dd3bdde`.

## Observed outcome

All four planned conditions completed once with normal condition/controller/container exits and independent release observations >=60 seconds. The final post-release query found all eight GPUs at 0 MiB with no compute applications. The parent ledger records `THREE_STAGE_EXIT300_BLOCK_RELEASE60_PASS`. No automatic retry or replacement ID was used.

This is **one new supplementary complete block**, four qualified attempts, n=1 per condition. It is not added to the original eight-block primary cohort. Original Kimi 38/40, original failures, accepted L1, f7 observations, and GLM history remain unchanged.

| Condition | Full eight-rank envelope (s) | Ingestion envelope (s) | Postprocessing envelope (s) | Maximum full rank duration (s) | Store normal exit (s) |
|---|---:|---:|---:|---:|---:|
| SF disk cold |337.711999025|332.823070212|4.954095935|337.630975796|2.270278043|
| SF disk hot |306.654224588|301.595182918|4.321631015|306.463373885|2.260670487|
| Store disk cold |31.048521166|25.276455937|1.495703999|30.945390612|132.529682278|
| Store pool hit |25.309462256|21.775261680|19.362923020|25.246641716|135.574149844|

Each metric has raw array [the displayed value], mean=median=that value, n=1. No significance or repeated-sample stability claim is made.

## Endpoints and controller scope

The primary loading metric is `(max(full.end_ns)-min(full.begin_ns))/1e9`, computed within each attempt across ranks0..7. Endpoints surround the selected loader's complete `load_model` call before fresh model construction/storage through its return, excluding optional runner setup afterward. No CUDA synchronization was added. This is a host envelope, not pure I/O or precise GPU completion.

Each rank also has complete ingestion and postprocessing begin/end endpoints. Cross-rank phase envelopes overlap and must not be added. For Store pool hit, postprocessing envelope=19.362923020s while maximum individual rank postprocessing=0.011842329s: early and late rank calls are staggered; this is not a 19-second individual postprocessing call.

| Condition | Prep (s) | A: registration to arithmetic JSON parsed (s) | Actual child-controller full process (s) |
|---|---:|---:|---:|
| SF disk cold |57.715386236|495.498346539|848.733441|
| SF disk hot |283.300369523|464.335577610|1050.478528|
| Store disk cold |26.886681245|212.185357261|1301.303054|
| Store pool hit |23.299220065|198.875450450|1296.598739|

Prep is target-condition construction through cache/pool admission, excluding common setup and initial Store-pool creation. A uses the same HTTP producer's pre-register timestamp through arithmetic JSON parse completion, before the gold assertion; body-read, parse, received, and validation endpoints remain separate. The secondary received endpoint is retained for the structural historical GLM comparison; exact parser-completion and post-context-return endpoints are not claimed identical.

Controller spans come from retained systemd native child-process entry/exit endpoints (microsecond source precision). They include initialization, cache prep, registration/loading, requests, delete, normal shutdown and the child's release checks, with the exact lifecycle in the raw events. They are never Prep+A. The separate parent unit was reclaimed before post-run native endpoint retrieval: parent-native span=null, not estimated or reconstructed.

## Cache, correctness, ownership, and shutdown

Actual order: SF hot -> Store cold -> SF cold -> Store pool hit.
Cache/pool admission passed in each condition. SF uses a64MiB control pool; Store uses640GiB. SF hot has the actual full-read/construction and pre-registration checks; it does not inherit the matched-cache diagnostic's five scheduled scans.

All32 original per-rank evidence files were rehashed after completion and matched both their before/after lifecycle receipts. Capsules explicitly identify derived extracts and retain original absolute paths, complete-file SHA256, PID/startticks, rank, instance, GPU UUID and clock identity. Registered parameter/storage metadata remains in the original server files; no weights were reread during this CPU extraction.

Arithmetic and translation passed strict validation. All four fixed32 requests generated exactly32 tokens and ended with length. Store-cold fixed32 text and31 token positions differ from the strict Safetensors gold; those differences remain reported. They pass the prospectively approved length-only performance qualification, not strict token equality. No weight-value identity or numerical-determinism conclusion follows.

The prospective300-second Store normal-stop rule applies only to this new profile. The previous30/60-second failures are not relabeled. Normal exits and guard cleanup are reported separately; no forced cleanup or background release was counted as a normal exit.

## Figure 5 and retained comparison limits

Use `figure5-v1/rendered/figure5-loading-supplement-v1.png` (also SVG/PDF). Its input and script preserve GLM n=6 and V4 n=9 selected historical arrays unchanged. Kimi n=1 is explicitly supplementary, with historical f7 and missing-endpoint primary results retained separately.

Kimi's new full loader boundary and historical GLM/V4 Worker outer loading boundaries are not strictly identical atomic scopes. V4 includes additional model-shape communication-buffer/offloader work outside the narrower loader call; no estimated subtraction is used. Inner loading/postprocessing implementations also differ. The figure is a descriptive side-by-side comparison, not a proof of equal work or cross-model performance causality.

This run used qjhs000. Both SF `/mnt/raid/models/Kimi-K2.6` and Store `/home/boyjiang/kimi-new/store-conversion-20261105-once` resolve to XFS on /dev/md0. This is not the historical qjhs003 local-NVMe route. Pool size, node, storage route, model/quantization and eager configuration differences remain disclosed; do not pool these measurements with the original cohort.

## Reproducible evidence

- `EXPORT-MANIFEST.json`: exact raw-copy and derived-capsule provenance; original root evidence remains unchanged.
- `post-release-verification.json`: all-condition terminal/release/GPU-free verification.
- `derived-v1/endpoints-and-summary.json`: all32rank endpoints, arrays/n/means/medians and source references.
- `derived-v1/rank-endpoints.csv`:96 rows (32ranks x3stages).
- `derived-v1/attempt-metrics.csv`, `condition-statistics.csv`.
- `derived-v1/derive_load_stage_results.py` and CPU reproducibility receipt.
- `figure5-v1/figure5-inputs.json`, plot-values CSV, rendering script and manifests.
- `launch-preflight-v1/`: fresh booking/budget/pin/guard admission and one-time activation receipts.

No new GPU, model, Store, cache read, environment or code deployment is required to reproduce the CPU summaries or plot from these small inputs.

Version/source boundary details: see `version-and-boundary-pins.json` for all86 admitted source/ELF/asset pins and actual mount Source-to-Destination overrides. The raw directory name is not an identity proof.
