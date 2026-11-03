# Qwen3-8B official loader benchmark

Measured on qjhs002, one H20-3e GPU, BF16, 4 container CPU cores. Each reported row uses three independent client processes; median load API wall time with CUDA synchronization.

| Measurement | SafeTensors | ServerlessLLM | Speedup |
| --- | ---: | ---: | ---: |
| Cold load | 9.059701 s | 2.678710 s | 3.38x |
| Warm load | 2.657968 s | 0.537354 s | 4.95x |

Official source: `9f50241baa5386e06a9321c51f19a9ef5f964c2b`, tracked files unchanged. Fresh native build and unchanged official Python SDK; runtime image `sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27`, Torch 2.10.0+cu129, Transformers 5.5.1, PEFT 0.18.0. This is an official source standalone loader reproduction using the existing environment, not a reproduction of the published container environment.

Checkpoint revision `b968826d9c46dd6066d109eabc6255188de91218`; 8,190,735,360 parameters. Complete named-parameter BF16 byte SHA256 `68dc53d65a3487ee16ef93f4efb1de37125fe7cc5d323d2d80b1ba9c8e8c935a` and single-token forward logits agree across all 13 loads including warmup.

Cold: unload ServerlessLLM host cache, apply POSIX_FADV_DONTNEED only to this model's files, verify zero resident file pages with mincore. Warm: SafeTensors files resident in OS page cache; ServerlessLLM weights resident in its pinned host pool (32 GiB). These are the respective native warm paths. No global cache drop.

Excluded from timing: module import, CUDA initialization, conversion, Store startup, cache preparation, parameter hashing, forward smoke test. Included: selected load API and completion synchronization. SF uses AutoModelForCausalLM.from_pretrained; SLLM uses official sllm_store.transformers.load_model.

NVML peak GPU use is sampled every 10 ms, includes Store/client CUDA contexts, and is saved per trial. The PyTorch allocation counter excludes ServerlessLLM native cudaMalloc and must not be treated as its GPU footprint.

The official published-image download was active on the same disk during measurement; results characterize this run, not an isolated storage benchmark. ServerlessLLM Store used one I/O thread and 32 MiB chunks. No claim of cluster deployment, HTTP serving throughput, process startup, generation speedup, or end-to-end cold start.

First attempt stopped before conversion because PEFT was missing; preserved in attempt1 evidence. PEFT was then installed into the task SDK directory with no dependency upgrades. Second attempt exited 0. Exact task container removed; 60-second release observation passed with GPU memory 0 MiB throughout.
