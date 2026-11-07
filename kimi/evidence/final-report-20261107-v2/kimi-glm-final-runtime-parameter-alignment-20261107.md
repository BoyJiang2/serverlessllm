# Actual Kimi / GLM runtime parameter alignment

| Parameter | Kimi | GLM C23 | Evidence scope |
|---|---|---|---|
| Torch |2.10.0+cu129 |2.10.0+cu130 |76 actual Kimi imports / GLM head CPU preflight |
| vLLM |0.19.1 |0.19.1.dev1+g43a9b1afb |Same as above |
| CUDA runtime |Not recorded in selected actual Kimi import markers |13.0 |Do not infer runtime from cu129 label |
| TP / eager |8 /true |8 /true |Explicit engine config |
| GPU memory utilization |0.8 |0.9 |Explicit engine config |
| Max model length / sequences / batched tokens |2048 /1 /2048 |2048 /1 /2048 |Explicit engine config |
| Prefix caching |false |false |Explicit engine config |
| Store pool / chunk / threads |640GiB /64MiB /4 |716GiB /512MiB /4 |RPC assertion/restore contract and runner source |
| Pool lifecycle |Fresh percondition |Shared formal-batch pool |Not equivalent initialization/reuse geometry |
| Head CPU / memory / shm |8 /16GiB /1GiB |4 /8g /32g |Configured caps, not utilization |
| Worker CPU / memory / shm |32 /1536GiB /8GiB |32 /1700g /32g |Configured caps, not utilization |
| Request temperature / seed / logprobs |0 /20261024 /1 |0 /20261024 /1 |Explicit HTTP fields |
| Request top_p / top_k / min_p / n |1 /0 /0 /1 |All omitted |No effective defaults guessed |
| Engine seed |Omitted |20261024 |Request seed explicit both |
| max_tokens / ignore_eos in actual request order |128 false;128 false;32 true |Same |Arithmetic,translation,fixed32 |
| Token-ID observability |Trace; return_token_ids omitted |return_token_ids=true |Unsupported flag not passed to Kimi SamplingParams |
| Thinking/template |Frozen own template thinking=false,preserve_thinking=false,add_generation_prompt=true |No explicit switch found in selected gold/runner/prereg; unknown |Both HTTP calls use frozen input_token IDs; no runtime template flags |
| Extra engine fields |moe_backend and GPU block override omitted |deep_gemm; num_gpu_blocks_override256 |No implicit default inferred |

Kimi transformers5.5.1,grpcio1.76.0,ray2.48.0,msgpack1.1.2,GPUtil1.4.0,speedtest-cli2.1.3 observed in76imports. GLM equivalents are not in selected actual import receipt and remainnull. Kimi CUDA runtime remainsnull; CUDA build suffix is not an explicit runtime measurement.

JSON attaches source path+SHA+selector to every parameter family,38 exact version-matched Kimi specs, actual GLM head import scope, original engine config and runner mutations. Configured maxima do not establish utilization. No package imports, default guessing, GPU, Git or GLM changes.

JSON SHA256: 0dee219fe6e7af7ede4d436fea68d24fb8f281e360d64462d6c9c5bf73f96462
