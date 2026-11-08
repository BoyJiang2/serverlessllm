# Kimi unified loading metric: historical evidence gap (CPU-only)

Original main set remains B1/B2/B3/B5/B6/B7/B8/B10: eight complete blocks, 32 attempts. Original40 remains 38 qualified / 2 failed. One supplemental attempt and five earlier historical attempts are separately retained, never substituted.

## Figure 5 decision

No Kimi condition currently has a usable historical `weight_load_host_all_rank_span_s` in the audited surface. Both that metric and the separate `max_rank_weight_load_host_duration_s` have eight null entries per condition, valid n=0, mean=null, median=null. Missing is not zero.

| Condition | Unified span valid n | Unified mean / median | Specialized single-f7 n | Specialized mean (s) | Specialized median (s) |
|---|---:|---|---:|---:|---:|
| safetensors_disk_cold | 0 | null / null | 8 | 1121.366857500 | 1126.745787500 |
| safetensors_disk_hot | 0 | null / null | 8 | 284.510617250 | 283.961106000 |
| store_disk_cold | 0 | null / null | 8 | 87.859090625 | 90.141445500 |
| store_pool_hit | 0 | null / null | 8 | 9.998397500 | 10.117383500 |

The f7 columns are a separate observation, not the unified eight-rank metric, a TP8 maximum, or pure I/O. Each underlying scalar retains its original path/SHA/line in attempts.json. Arrays are in condition-summaries.json in fixed block order.

## Boundary and optional feature audit

Active old overlay SHA256: `f7a958706ee01c2c0c1d8c424fa9bfefa4ac80bb20a2eab9a8f70d0a99f351eb`. The actual deployment is vLLM 0.19.1; exact module/dist distinctions and loader/factory source SHA/positions are in source-boundary.json. Do not substitute the newer tracked patch for the active old overlay.

f7 starts at gpu_model_runner.py:4750, includes loader factory selection at4753 and load_model at4754-4756; ends at4810 after conditional LoRA/drafter/aux handling. Fresh construction/storage allocation, restoration and required processing are inside the selected load_model call. Starting only at load_weights/load_dict would omit construction. Even if optional branches are inactive, f7 retains only a local elapsed scalar, without authenticated rank or absolute endpoints.

All38 frozen qualified configs omit LoRA/speculative enablers. Exact-commit EngineArgs has explicit enable_lora=False and constructs LoRAConfig conditionally; this is source/config inference, not a newly recovered per-worker object dump, and installed arg_utils byte identity was not independently recovered. Two saved actual SFhot/Storecold engine configs explicitly show speculative_config=None; drafter/aux paths follow that configuration. Remaining limits are retained, not guessed away.

## Endpoints and identities

Raw loading endpoints were not recovered. rank-endpoints.json/csv contains 368 explicit expected-rank slots for all46 listed attempts, not fabricated observed raw endpoints. The primary32 contain256 such slots. history-endpoint-audit.json separately maps the256 primary rank ownership records (container PID, host PID/startticks/cgroup/device/CID), model-instance/backend-clock associations and source hashes. Those identities do not authenticate missing load endpoints.

The pinned f7 writer dictionary at4872-4890, serialized at4891 and written unchanged, has no full-load begin/end or endpoint clock/instance fields. Its capture_seconds starts during later synchronized metadata capture and is not weight loading. All256 exact private full-rank paths were inaccessible (PermissionError); no private payload was read and no universal absence claim about arbitrary historical logs is made. This audit therefore reports unavailable historical endpoints, not evidence that all conceivable private history lacks them.

## Figure 4 controller boundary

controller-boundary.json maps the two actual frozen controller bodies (12 and20 main attempts) and exact source/command/spec SHAs. Begin is the native controller unit ActiveEnterTimestampMonotonic, not condition-preparation begin. Success end is evaluation of `time.monotonic()-started` while building controller-final.json after normal stop, ownership/files/network checks, qualification and release observations.

This observed controller envelope includes environment setup, pool initialization/filling as applicable, cache preparation, model creation/load, all three HTTP requests, delete, deleted-baseline observation, ordered Store/API/container normal stop and >=60s release. It is not Prep+A. It excludes later unit-properties evaluation, final JSON writing, finally child-handle cleanup/journal capture and process tail. Absolute numeric controller start/end are not persisted in the reviewed final receipts and remain null; the directly reported elapsed is retained. Independently timestamped common_environment_begin -> release_quiet60_complete is a separately named event envelope, not substituted for controller elapsed.

All32 primary cases have checked normal Store stop, exit0 container receipts and release guards. B04 SFcold and B09 SFhot failed receipts keep normal=false/NOT_OBSERVED; failed elapsed and any forced/abort cleanup must not be called normal shutdown. controller-exit-classification.json retains all46 original cleanup classifications/refs. controller-attempts.json retains reported normal elapsed, event endpoints and Prep+A in separate fields.

## What can be plotted

- Unified Figure5 full-weight host span: no Kimi points available; all four conditions n=0. Do not plot zero bars or reuse the f7 scalar.
- Specialized Kimi f7 observations: available n=8 per main condition with raw arrays above; label scope explicitly and keep separate from the three-model unified metric.
- Figure4: Kimi measured controller elapsed may be shown with the exact controller scope above; cross-model equivalence requires the other models to use that same boundary. Prep+A remains only its original online window.
- Fresh collection would be needed to obtain authenticated eight-rank paired host timestamps at the requested boundary. No such collection, synchronization change or experiment is authorized or started here.

## Reproducibility and integrity

METRIC-DEFINITION.md freezes the requested formula and scope. organize_historical_metrics.py SHA-checks3 frozen inputs plus449 snapshot refs, generates46 attempt records and368 null endpoint slots, and never opens weights. test_historical_metrics.py has6 CPU consumer tests, including JSON/CSV null handling, original IDs, main-set membership and separation from f7 values. Source/boundary audits and independent review are separate files. verified-inputs.json is the original evidence path/SHA inventory; source/history/controller audits add their exact source/export references. ARTIFACTS-SHA256.json freezes the delivered small artifacts.

No GPU, model, Store, container, weight read, rerun or ID replacement was performed. Historical code, figures, golds, failures and classifications remain unchanged.
