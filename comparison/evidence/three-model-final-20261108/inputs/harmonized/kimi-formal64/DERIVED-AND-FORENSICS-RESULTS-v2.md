# Kimi partial supplemental loading evidence and shutdown closure limits

Actual run source: 9ee423906654d3105b330bfc6b75935b325090aa. Raw/result publication: 7c7b0f111b15884c39f236a568380ab4b4c9838f. New whole block FAILED_RETAINED_NO_RETRY: 1 qualified, 1 failed, 2 not started. New complete-block n=0; old 40/38/2, eight primary blocks and accepted L1 unchanged.

| Condition | Complete raw rank endpoints | Observed host envelope (s) | Max single rank (s) | Qualified-condition descriptive n | Complete-block primary n |
|---|---:|---:|---:|---:|---:|
| SF hot | 8 | 302.153546032 | 302.043261813 | 1 | 0 |
| Store cold | 8 | 31.413848904 | 31.076709263 | 0; failed observation n=1 | 0 |
| SF cold | 0 | null | null | 0 | 0 |
| Store pool hit | 0 | null | null | 0 | 0 |

The failed Storecold measurement is a special observation, never a qualified four-condition comparison or a replacement for any historical ID. All four complete-block primary arrays are empty with mean/median=null. The valid SFhot descriptive mean/median equals its sole value; failed Storecold is separately grouped. Derived CSVs contain exactly 16 actual rows and 16 explicit unstarted/null rank slots. No zeros, f7 substitutions, ratios or significance claims were fabricated.

## Common metric and source

weight_load_host_all_rank_span_s = (max(rank_end_ns)-min(rank_begin_ns))/1e9, calculated inside each attempt before any aggregation. The runtime key weight_load_host_span_s is this same eight-rank envelope. max_rank_weight_load_host_duration_s is a different, separately reported quantity.

Pinned runner f91922c41b220fc9abd9378cff936716b8457e115989f436fdd1a81d51f582c7 captures begin at line4780, calls the complete loader at4783, captures end at4788. Actual Base/Default and SLLM loader source audit covers fresh construction/storage and required weight processing through return. Device/communication setup, KV/profile/warmup/graph, routing/requests and pool/cache preparation are outside this envelope. No CUDA synchronization was added for these endpoints; this is host elapsed time, not pure I/O or exact device completion.

The older f7 timer begins before get_model_loader and ends after optional LoRA/drafter/aux runner setup, before later capture/synchronization. It is not the exact common boundary and a single f7 scalar is never an eight-rank span. Endpoint manifest/source audit pins the old f7 input and actual f919 overlay.

Actual logs show vLLM0.19.1, speculative_config=None, eager=True, prefix cache=False; loader formats are safetensors and serverless_llm. LoRA disabled/defaultNone and no drafter/aux are supported by the frozen kwargs plus exact EngineArgs/runner source paths and the observed speculative_config=None. This is configuration/source derivation, not a new live-object dump; missing LoRA/aux log fields alone are not treated as proof. Source files were read only from an exited container archive and existing pinned overlays; no container/model was started.

Raw inputs, PID/rank/instance/clock domains/host owner linkage and hashes are in each endpoints JSON/CSV. Original full metadata, raw physical telemetry and OCI remain server-only with source paths/hashes; the public derivation does not pretend to independently re-read private full metadata.

## Controller timing is not Prep+A

Actual producer register/read/parse/validation boundaries, initial environment and Store/cache preparation, gaps, load/create, requests, deletion, shutdown and release are kept separate in summary.timing_context. SFhot controller common_begin through release completion is 886.961596807 s. It is an explicitly bounded event envelope, not a claim to include OS scheduling before the first event or interpreter exit after the last event. Prep+A does not cover the contiguous whole process.

For failed Storecold, normal release is missing/null. Its controller failed-receipt elapsed (~1175.690 s) ends at failure; it does not include subsequent forced cleanup/release. Safety release after abort is independently recorded and never called normal exit. No estimated stage subtraction was used to manufacture a full controller or weight-loading metric.

## Shutdown forensics: proved and unproved

Same Store owner PID1495474/startticks802293651/NSpid1238: the 10.507 s snapshot is R/running; at20.423 s a bounded sequential proc read shows D/NVIDIA read-lock/ioctl stack. Later same-thread logs continue. Not permanent-deadlock proof and not userspace attribution to one CUDA call.

All eight borrowers return1150 chunks each (9200 total) before native pool Release begins for10240 buffers. Last actual progress: attempted2880/failed0/total10240 at59859 ms; no completion line. attempted is not completed whole-pool release. Exact source follows DrainAsyncTasks -> ClearMem -> pool.Release; Release loop covers unregister/free/erase without separate call timings. ELF95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd and build manifest59d70f4710ca19d95515e5ca99c5f14ddabd963b2051720908c748e766c2ee2d plus27 source pins match.

Historical q3 actual33.139352623 s normal exit used the same ELF/640GiB/10240 buffers; logs reach10240/failed0 and completion31318 ms. It remains accepted in its original scope. Current q0 has a different node/boot/GPU set, one lifecycle rather than two, and uncontrolled differences in resource history/driver/kernel/page placement/lock state. Neither that one q3 time nor a constant-rate217 s extrapolation supplies a q0 completion guarantee or deadline-change approval.

The forensics JSON mentions a separate appendix in its proposal text; no such appendix/current executable diagnostic is delivered by that v1. Treat it as an unimplemented proposal, as the independent review explicitly does. Raw reports remain unchanged.

## Reviews and reproduction

Actual-result review: 0d237ee600a38e17305dee072daa7be7c2c437f6d8f42cabd8dd74ea31612b4b.
Derived review: fc51534a9189eb3583461d0f0c245640e39754e057883c62659562615517f938.
Forensics narrow review: 75aa917f3bf76d42aea4ce6d0a5f1e893ba4636157f6ec94ad296d5177230661.
Boundary/optional-path review: 454da052223c0b93caf105e90a92895b02bb8d830e674fbd69d67ed00e47936f.

CPU reproduction: python3 derived-runtime-v1/summarize_existing_endpoints_v2.py runtime-public-v1 NEW_NONEXISTENT_OUTPUT_DIR, then copy the included small split_rank_csv.py into that new output directory and run python3 NEW_NONEXISTENT_OUTPUT_DIR/split_rank_csv.py; it partitions the already-derived CSV alongside itself and refuses overwrites. This reads only the small existing receipts, never model data. The first CPU hash gate rejected a transcribed65-hex reference before generating measurements; the confirmed64-hex corrected script and original refusal are retained. No experimental runtime retry occurred.

New bounded Store-stop observation preparation is separate CPU work in the existing renderer/tests; its source/actual commands/review are not claimed complete here. It will retain the60-second FAIL, use a prospective fixed300-second observation cap only, never qualify a formal sample, and require a new explicit one-shot GPU/Store approval after delivery. No native optimization, timeout widening in the old formal protocol, new environment, model copy, GLM run or 40-run campaign was performed.

This v2 corrects one manually transcribed native build-manifest hash in the unpublished v1 overview and clarifies the splitter invocation. The v1 overview remains on the server. No raw receipt, calculated result, review or source pin changed.
