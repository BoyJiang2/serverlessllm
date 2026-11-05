# qjhs000 official SF fixed32 reference: preparation only

No model/container/GPU execution, source edit, new environment or weight payload read was performed. Original AAB gold/receipts and all failures remain unchanged. This is a new Kimi-specific protocol reference candidate, not another GLM experiment or a performance qualification.

## Existing entry and minimal reuse

| Item | Actual source/evidence and required minimal adaptation |
|---|---|
| Official model entry | Immutable ec5 image `/usr/local/bin/vllm serve /checkpoint -tp 8`; existing v8 spec/manual root command in S remain historical originals. Existing helper is `/home/boyjiang/kimi-new/ServerlessLLM/sllm_store/tests/gpu512_once_controller.py`, SHA `f293c9b94b45d33c1cbe8a301a203ab03e63817e4f5f012bc17d1a1add2bd8cf`. It is inside the authorized migrated active source directory; no outside-source launcher migration is needed. |
| Controller compatibility | Mature official SF controller can be reused as commanddata, but is not drop-in: old ROOT/BASE/UUID constants and observer SHA target qjhs002. Bind existing helper globals to qjhs000 ROOT/BASE/GUARD/OBSERVER/UUIDs before assertions; current observer SHA `f02ae256d4cfec86608d733476b64291248b1f20901c4bb8ca05d908992dffdb` and helper GPU_QUERY_TIMEOUT15 must remain. No replacement helper/framework is needed. |
| Actual client consumer | Root consumes `s.client_code` in Docker exec, then explicitly accepts `SF_API_AAB_PASS`. New client and consumed acceptance must use a distinct fixed32 protocol marker/schema, with positive/negative mock actual argv tests. Do not merely add an unused new client field. |
| Approved identity lesson | Require logical device cuda:rank plus approved physical UUID membership, all eight ranks/PIDs/distinct UUID set, namespacePID/startticks/fullCID cgroup and per-row physical UUID→NVML PID correlation. Do not assume NVML ordinal equals worker UUID order. |
| Raw model path | Host `/mnt/raid/models/Kimi-K2.6` → `/checkpoint` RO, no Store/archive/ServerlessLLM loader. Only the same three HF/runner/RoPE overlays are needed; all migrated overlay hashes independently match old SF spec. |
| Configuration | Preserve existing TP8, mp, native MM encoder data, trust_remote_code, Kimi reasoning/tool parser, safetensors/lazy, max_model_len2048, max_num_seqs1, max_num_batched_tokens2048, gpu_memory_utilization0.8. No quantization/dtype/graph/eager/capacity change to align the prompt protocol. |
| Resources | Preserve CPU32, Memory/Swap1280GiB, PID4096, SHM8GiB, tmpfs8GiB exec/nosuid/nodev, networknone, all8 UUIDs, GPU sampled limit131072MiB. Node changed qjhs002→qjhs000 and checkpoint storage changed local→shared mount; both must be explicit. |

## Protocol and source bindings

Requests are sequential arithmetic17+25 → cat translation → fixed32 sky explanation. Exact prompt literals independently read from qjhs003 `/data/brick1/boyjiang/glm5.1-fp8/validation/native-gold.json`, whose full small-file SHA256 matches frozen `9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0`:

1. `请计算17加25。只输出整数结果，不要解释。`
2. `请将英文单词cat翻译成中文。只输出译文，不要解释。`
3. `用中文解释为什么天空通常呈蓝色，写一段完整且连贯的说明。`

These strings exactly match the user-specified new Kimi protocol. Only prompt text/protocol is reused; no GLM token IDs, EOS IDs, reference outputs or result ledger are modified or copied into Kimi gold. The small gold was read once via existing SSH without any runtime, package import or GPU work.

All three use temperature0, seed20261024, n1, streamfalse, return_token_ids true, omit top_p. Kimi chat_template_kwargs are thinkingfalse/preserve_thinkingfalse and add_generation_prompt true. Business requests max_tokens128/ignore_eos false require exact normalized42/猫 and finish stop. Sky max_tokens32/ignore_eos true requires exactly32 returned token IDs, finish length; it is a bounded generation, not necessarily a complete natural answer. Record every exact request/messages, response, prompt_token_ids, output token IDs, usage and logprobs. These become new Kimi reference evidence; no GLM IDs/EOS/output gold is copied. Old AAB gold is never overwritten.

Actual installed0.19.1 OpenAI Chat protocol source `/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/chat_completion/protocol.py` SHA `1a2492bdb99e944ca177853496408a2a00d578bb2fa51c6e49241567d6601a82`: logprobs is bool at157, top_logprobs int at158, ignore_eos at198, add_generation_prompt defaultTrue at216–220, prompt token ID return supported. Use HTTP `logprobs:true, top_logprobs:1` to produce SamplingParams.logprobs1 via actual to_sampling_params at505; do not copy native HTTP integerlogprobs1 as if it were this schema. Omitted top_p is None at173 and resolves from model defaults then class defaults at440–443. Actual SamplingParams source SHA `8512e73c45a6d2f8015f2ca8d94c9fc9a05ca390d6206e2b387ad6550e9b014a` defaults top_p1, but GLM effective omitted default remains independently unverified. Omission parity is not proof of every effective setting being identical. Sources were read from the existing stopped CPU CID, no import/start.

Kimi shared small assets: config SHA `85825ca6e18cbe539eb83ee09eedfb3f4222265929f06e9f535a6d9364f55899`, generation config SHA `5431db52f431099f309ece737670e977de148f8cfd5340e920ff1f44d7cff596`, template SHA `8bf859698fd4781c0e1e1c63ce74422aab27e53ccc5f47116317c64cda06132f`, tokenizer config SHA `12fcab43d2b6068f46769f5ff373960bf7c17a94d7abbc50e2491306b2f6cf58`. Native compressed-tensors packed INT4/group32 and BF16 nonquantized components remain; old reference recorded60 MarlinMoE methods/rank and unchanged registered ownership. README398 describes nativeINT4,429/464 describe vLLM thinkingfalse. Template final branch emits assistant generation prefix and closed thinking tags when thinkingfalse; preserve_thinking defaultsfalse. Record Kimi's actual server prompt IDs, not GLM IDs.

## Full budget, measured history and limits

Prefer the existing **2100-second** full SF budget unchanged, rather than invent a need for2400. Original real reference READY was **539.145413s** after GO; complete controller **609.680748s**, including guardian quiet **60.016868s** and observer quiet **60.535569s**. These are qjhs002 observations, not predictions for qjhs000/NFS or the new prompts. Original sampled peak116595MiB is a lower bound, not a reservation estimate.

Existing hard structure: prep<=60; readiness<=GO+1500; all3 requests<=GO+1680 (each<=60 and total180); ordered normal exit<=GO+1800; guard deadline1800/overall1840/release60; guard and observer native1980+stop3, observer sampling1950; full2100, outer actual end2098. Latest activation requires now+1986<=outer end and actual activation+1983<=outer end. A60-second preparation plus1986 coverage is2046<2098, leaving52 absolute margin; guard overall+60=1900<1980 and observer normal release is independently required. Final queries/hash/kernel are bounded inside the original remainder gate. For all helper GPU queries retain15-second timeout each, conservatively30 seconds per two-query sample.

New business caps128/128 and fixed32 cap32 do not prove a decode rate. Retaining the original180-second request window is a bounded fail-closed proposal, not a promise that288 maximum generated tokens complete in that window. If independent review requires added decode allowance, specify a new complete<=2400 hard-budget table before admission rather than increasing a live deadline after failure. No cache drops, warmup or pagecache conditioning are proposed: host/NFS/model/kernel caches are **uncontrolled**. The sky request is third and warm relative to the two business checks; it is not cold first-business timing.

## Remaining admission and review gates

1. The three prompt literals are now frozen by exact source-gold comparison. Freeze the independent Kimi candidate ID/output path, new client/schema and actual rendered Docker args/body hashes; preserve all old AAB/gold/history.
2. Rebind qjhs000 paths/UUIDs/current CSV15 observer and approved physical ownership validation in commanddata; verify CPU actual-consumer positive/negative cases, new protocol predicates and no top_p field.
3. Confirm shared checkpoint identity/integrity through existing migration/source records; this audit read only small config/template/README files, not full checkpoint payload or its23.6MB index. Header/size alone must not be called payload integrity.
4. Independent review, fresh booking/resource/full-budget/guard/probe gate and user occupancy notice (8cards/purpose/maximum budget) before any execution. This preparation grants no GPU/model approval. Native final-stop failure remains retained; fixed32 SF preparation does not repair or rerun it.

Source-freeze coordination: another authorized agent is adding native backend trace_debug first-nonempty-token logging. SF itself mounts only the three pinned vLLM overlays and raw checkpoint, not the native backend; nonetheless final source/evidence commit labels must wait for the coordinator freeze and explicit pin verification. This read-only preparation does not overwrite any source or assume a clean worktree. No automatic retry, cache-state conditioning or request-budget enlargement is permitted.
