# Qwen3-8B final baseline parity rerun

Both loader formats use the unchanged checkpoint generation config, the same plain prompt `The quick brown fox jumps over the lazy dog.` and `generate(max_new_tokens=50)`. Sampling seed1000+trial is paired across backend and cold/warm. Parameters are BF16; RoPE buffers retain baseline FP32. Full parameter bytes, buffer values, complete generation config, inputs and selected paired output token sequences match. This proves the tested prompt/seeds, not general accuracy equivalence.

| Case | SF load median s | SLLM load median s | Load speedup | SF generation median s | SLLM generation median s |
|---|---:|---:|---:|---:|---:|
| cold | 9.124485 | 2.650246 | 3.443x | 1.340586 | 1.367180 |
| warm | 2.852776 | 0.539807 | 5.285x | 1.318845 | 1.343227 |

12 measured trials plus one warmup; each generates50 new tokens. Three measured seeds produce three distinct sequences. GPU7 H20-3e, Torch2.10+cu129/Transformers5.5.1; official SDK9f50241 plus minimal task overlays. Original cold/warm protocol preserved: file page residency is checked, cold eviction requested only for this model, SF warm files pre-read, Store CPU cache controlled. Loading API timing retains CUDA synchronization; generation/hash/config assertions are outside load timing except generation_config file restore inside SLLM load API.

Complete13-row parameter SHA: `68dc53d65a3487ee16ef93f4efb1de37125fe7cc5d323d2d80b1ba9c8e8c935a`. Container exits0; GPU7 stays0 MiB, PID0, native scope removed for >60seconds. Shared SDK/checkpoint/existing Store mounted read-only. Earlier generation-config-only results remain preserved; these fresh runs add explicit nonpersistent-buffer parity.
