# V4-Pro CPU tokenizer companion

2026-11-06. Parent: `0fec905db8d96f358bcf8091119190dd12aa8bc3`.

The existing user-selected model at qjhs2:/mnt/raid/models/DeepSeek-V4-Pro was used in place, read-only. The job mounted only tokenizer.json and tokenizer_config.json, not weight files. Their hashes match the previously verified pinned model revision `b5968e9190ef611bbf34a7229255be88a0e937c1` assets.

## Observed CPU results

| Frozen input | Actual input tokens | Canonical JSON IDs SHA-256 |
|---|---:|---|
| business request | 30 | 7acd634c883de63235e830f40432454566484c243e2431da81b45d26ea15f354 |
| fixed32 request | 43 | 1cb6d8d933c62759be85c424ca1c45f75c6315c9583008751897d28499c8bc8a |

Both inputs fit the frozen 2016-token input limit, contain BOS 0 exactly once, recompute identically and decode to the original encoded text. Official reference encoding equals target vLLM encoding and the frozen prompt hashes. Actual IDs and original request hashes are in `evidence/v4-cpu-tokenizer-p3/result.json` and `sllm_store/tests/fixtures/v4_cpu_bpe_candidates.json` (repository-root relative).

The original P2 request/preregistration files remain unchanged. This companion adds measured CPU input IDs, not model output gold. It does not revise the sampling plan or consume a formal experiment ID.

## Reused environment and boundary

No packages or inference environment were installed. The existing cached Kimi image was selected by immutable ID `sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27`, with transformers 5.5.1, tokenizers 0.22.2 and regex 2026.5.9. The first two satisfy the pinned vLLM 0.20.1 dependency constraints; regex has no version constraint there. Cached Torch 2.10.0+cu129 and vLLM 0.19.1 are not the target runtime and were neither imported nor qualified.

The temporary container used runc, no network, no device requests/devices, read-only root/mounts, one CPU and 2 GiB memory. The script blocked Torch/vLLM/model-library imports and found no NVIDIA device nodes. It ran actual PreTrainedTokenizerFast/BPE and pinned target `132765e3560659ff63ebd236203672e991b70e08` tokenizer wrapper/cache function bodies selected by AST, with TokenizerLike represented by object. This avoids importing the complete engine and must be revalidated in the qualified target runtime. Container exit 0 and subsequent removal were checked by exact container ID. stderr states PyTorch was not found, as expected under USE_TORCH=0.

## CPU contract validation

One new backend test forwards the actual 30/43 token ID arrays and frozen sampling options through the existing backend source with a fake engine. It checks exact token forwarding, no second message encoding and no request mutation. Its output is a stand-in, not model gold. The focused backend suite passed 15/15, with no failures or errors. All 29 production source hashes match the preceding 281-test manifest; only tests/data/docs changed. The full suite was not rerun, so this is not a claimed 282/282 run.

Raw result, inputs, scripts, source receipts, container limits/terminal cleanup, requirements checks, focused log and artifact SHA manifest are archived under `docs/evidence/v4-cpu-tokenizer-p3/`. Remote execution evidence remains `/home/boyjiang/sllm-v4-pro/evidence/cpu-tokenizer-20261106/`.

## Remaining gates

Full 64-file weight payload hashes remain unverified; the separate approximately 805 GiB read scope question is pending. No full weight read, Store conversion, native extension build/ABI check, GPU/model execution, model output gold, H20 capacity admission or physical release proof is implied. Native target tokenizer/input parity, full Store recovery, service lifecycle and at least 60 seconds of release observation remain required before runtime acceptance.
