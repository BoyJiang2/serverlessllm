# Llama / Qwen baseline-matched reproduction

The user-identified generation-config mismatch was real. The loader saved generation_config.json but did not restore it. The correction restores it before the SLLM load API returns (file IO included in loading time). A second proven gap cast nonpersistent RoPE buffers to FP16/BF16; specifying parameter dtype at empty-model construction preserves baseline FP32 buffer values.

Both models use `The quick brown fox jumps over the lazy dog.` with `generate(max_new_tokens=50)`, unchanged checkpoint sampling settings and seeds paired across formats. No forced greedy decoding. All reported rounds actually generate50 new tokens.

| Model / protocol | SF load seconds | SLLM load seconds | Speedup |
|---|---:|---:|---:|
| Qwen3-8B cold, median3 | 9.124485 | 2.650246 | 3.443x |
| Qwen3-8B warm, median3 | 2.852776 | 0.539807 | 5.285x |
| Llama3.1-8B random, mean30 | 11.421353 | 2.392457 | 4.774x |
| Llama3.1-8B cached, mean5 | 2.667581 | 0.512304 | 5.207x |

Qwen:13 total rounds (12 measured+1warmup), complete parameter-byte SHA equality, all buffer values/config equal, measured outputs paired by three seeds all equal; three seeds produce three different sequences. Llama:30/30 random and5/5 cached pairs equal in config/input/seed/output, buffers asserted equal; additionally one complete parameter hash pair at previously failing seed20261127 matches. The config-only run's row23/token43 divergence is preserved, as is the missing-task-directory setup failure. Both final containers exit0 and pass >60seconds release observation with GPU7memory0, PID0 and cgroup scope removed. Llama temporary replicas are cleaned.

These are H20-3e GPU7 standalone loader measurements using official SDK9f50241 and task overlays, Torch2.10+cu129/Transformers5.5.1. They are not full native service, H100-paper, arbitrary-input accuracy, or fully_parallel GPU validation. Qwen cold/warm and Llama random/cached protocols/timing differ and should not be conflated. Official tracked checkout, shared SDK, checkpoints, existing Qwen Store and Kimi code remain unchanged/read-only. Exact sources/launches/raw results/release evidence are in the linked sibling task folders; final publication receipt records the verified GitHub SHA.
