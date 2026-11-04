# Llama-3.1-8B-Instruct: final baseline parity rerun

Full official random30 distinct-copy protocol and cached warmup+5 protocol were rerun after the configuration and buffer fixes. Both loaders use the unchanged baseline generation config, plain prompt `The quick brown fox jumps over the lazy dog.` and `generate(max_new_tokens=50)`. Random permutation is paired using seed20261104; generation uses seed20261104+measurement index for both loaders.

| Case | SF mean load s | SLLM mean load s | Load speedup | SF mean generation s | SLLM mean generation s |
|---|---:|---:|---:|---:|---:|
| random | 11.421353 | 2.392457 | 4.774x | 0.763959 | 0.754356 |
| cached | 2.667581 | 0.512304 | 5.207x | 0.815758 | 0.779179 |

All35 measured pairs have identical input/config/seed/output tokens; each generates50 new tokens. RoPE buffers remain FP32 and match the real CPU SafeTensors baseline values. Full loaded-parameter SHA equality is additionally verified in the separate GPU diagnostic at the previously failing seed20261127; this is not a claim that all35 pairs underwent a full byte hash.

Two minimal SDK corrections: restore saved generation_config inside the timed loader API; specify parameter dtype during empty model construction instead of casting the entire model and reducing RoPE precision. Both constructor branches pass CPU comparison; runtime tests exercise best_effort_load only. SDK overlay SHA8115dbbcc9310bd93009f9fa66c93ee24f6beb581de9e1c9fceabce53bf11e23.

Environment: H20-3e GPU7; Torch2.10+CUDA12.9, Transformers5.5.1, official SDK/C++9f50241 plus task-only generation-config, dtype and private-API compatibility overlays. This is a standalone loader reproduction on this host, not an H100 or full native service result. Original API wall-clock loading interval is preserved without an extra CUDA synchronization at the endpoint; conversion/imports/warmup/hash/assertions/generation are outside that interval. Generation timings synchronize CUDA on both loaders. Random30 means30 physical independent replicas in random order, not guaranteed cold-cache loads.

Container5e334c8b5a41f901355bee4b1e325b4e9f474dcfdb8b773153c447c83ccc484e exits0. Temporary replicas are cleaned. GPU7memory0, PID0 and scope removed throughout61.16seconds release observation. Original checkpoints/shared SDK are read-only; no Kimi sources or historical failures changed. Earlier config-only Llama random mismatch is preserved in a separate directory. These finite prompt/seed checks do not establish arbitrary-input accuracy equivalence.
