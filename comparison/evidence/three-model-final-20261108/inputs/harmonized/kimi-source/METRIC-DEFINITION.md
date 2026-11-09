# Unified Kimi historical loading metric: frozen definition

`weight_load_host_all_rank_span_s = (max(rank_end_ns) - min(rank_begin_ns)) / 1e9`, evaluated within each attempt over exactly TP ranks 0..7 before any across-attempt aggregation.

Begin: each rank enters the full weight loading flow, before fresh model construction / weight-storage allocation. End: assignment/restoration of all weights and necessary native quantization / layout postprocessing return. Include allocation, source reads or Store waiting, copy / IPC confirmation and weight handling inside that flow. Exclude device and communication initialization, KV allocation, profiling, warmup, CUDA graph, routing and requests.

This is a host envelope. Add no CUDA synchronization; do not call it pure I/O or exact device completion. Both endpoints need clock domain/units, rank, process identity and the same attempt/model instance. Clock joins require verified compatible host clock, boot and time namespace. PID alone is insufficient because of reuse.

Also report `max_rank_weight_load_host_duration_s = max(rank_end_ns - rank_begin_ns) / 1e9` separately. Neither eight durations nor a single f7 duration reconstruct the all-rank envelope. No estimated subtraction from wider lifecycle or backend timing is allowed.

Historical eligibility: original main blocks [1,2,3,5,6,7,8,10], four attempts each. Original40 stays 38 qualified / 2 failed. Supplement1 and prior historical5 remain separate and cannot fill missing original IDs.

Current exported endpoint slots are null, not fabricated raw measurements. `expected_rank` is a schema slot, not a claim that a rank endpoint was observed. JSON null / CSV empty cell means missing; valid n excludes missing. Retained f7 scalar observations have independent names and source hashes and never populate unified fields.

A future collection would have to retain all eight endpoint pairs and endpoint ownership/clock identity at the stated functional boundary. This document does not authorize, schedule or execute that collection. No GPU, model, Store, weights read, or new experiment is part of this audit.
