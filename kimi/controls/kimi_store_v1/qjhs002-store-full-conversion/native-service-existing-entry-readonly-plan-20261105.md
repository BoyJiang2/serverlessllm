# Native ServerlessLLM existing-entry read-only plan

Recorded UTC: 2026-11-04T22:05:15.824189+00:00

Status: preparation only; parent identifies source publication `6de7240`. Current byte identities below were read from the active adaptation tree; no Git command was run. Full Store conversion and eight-rank restore have not passed, so no native service run is admitted by this report. Existing SF AAB and mixed-component passes remain separate milestones. Native blockers do not block the ongoing official MP save/restore work.

## Existing formal entry points

| Action | Actual current source path | Meaning / limit |
|---|---|---|
| Head/API start | `setup.py:68–71` console `sllm = sllm.cli.clic:cli`; `sllm start` → `_cli_utils.start_server` → Ray controller + `create_app` + uvicorn | Existing official flow. This does not create a GPU worker by itself. |
| Create | `sllm deploy --config <reviewed existing-style JSON>` → `POST /register` → `SllmController.register:98–177` → StoreManager register + named router | Register HTTP200 does not prove engine ready: router.start.remote is submitted without awaiting initialization. |
| Infer | `POST /v1/chat/completions` → router.inference → detached backend.generate | Existing native endpoint, not a new vLLM HTTP server. |
| Delete | `sllm delete kimi-k2.6-sf-reference` → `POST /delete` → controller.delete:257–275 | Present source drops router metadata/handle; explicit router.shutdown is commented out. Not a GPU release barrier. |
| Start again | Repeat the same official deploy/config after verified deletion | Reuse accepted Store directory; do not let native downloader trigger another conversion/download. |
| Status | `sllm status` / `GET /v1/models`, plus actual backend readiness | Registry presence alone is not inference proof. |

The documented Compose file is an existing topology reference only: it contains `build`, an unpinned latest image, fixed shared container names, one-GPU assignment and a 4GB pool. Do not execute it verbatim. Future adaptation must reuse existing assets and official processes, with task-owned names/ports/CIDs and independently reviewed containment; no second launcher/loader-management framework.

## Confirmed compatibility and lifecycle gaps

| Fact proved from current source / immutable stopped image | Smallest candidate, not implemented |
|---|---|
| Worker requirement pins `ray==2.48.0`, `vllm==0.11.2`; single-machine Markdown only claims patch testing at0.11.2. Actual Kimi image has vLLM0.19.1. | Treat old documentation as provenance only; validate actual0.19.1 APIs below. No downgrade. |
| `vllm_backend.py` imports `Counter` from `vllm.utils`, whose actual0.19.1 __init__ has no Counter. Existing `vllm.utils.counter.Counter` is present. | Change only that import to its actual official module; no invented counter or framework. |
| Backend forcibly inserts `task=self.task` after filtering EngineArgs fields. Actual EngineArgs/AsyncEngineArgs have no task field; supported field is runner. | Remove obsolete forced task argument for this generation path, preserve supported filtered options. Set supported runner only if a tested need exists; do not blindly rename unrelated task values. |
| Actual AsyncLLMEngine is an alias of v1 AsyncLLM. `from_engine_args`, `generate`, async `abort`, synchronous `get_tokenizer`, synchronous `shutdown(timeout)` exist. Backend currently deletes its engine instead of explicitly invoking shutdown. | Reuse existing explicit shutdown API and existing router lifecycle, with bounded waits and physical evidence. No alternate engine wrapper. |
| Backend generates a role:content string; it does not apply the Kimi chat template. Remaining request body is passed straight to SamplingParams. Actual SamplingParams has no stream, return_token_ids or chat_template_kwargs fields. | Existing input_tokens→TokensPrompt can reuse frozen canonical SF prompt IDs, already encoding Instant mode. For ordinary messages, any later Kimi-aware template fix belongs in this backend. Do not send SF HTTP-only fields to SamplingParams. |
| Native process_output omits prompt/completion token IDs although RequestOutput contains them. | If token-level acceptance is required, add a narrow optional response flag/fields in the same native backend, consuming that flag before SamplingParams. Default response semantics stay unchanged. |
| `sllm.backends.__init__` eagerly imports TransformersBackend; that file imports peft/datasets/sllm_store.transformers at module scope. PEFT is absent in the checked ec5 path. | Native backend dispatch has an additional optional-backend import barrier despite the already fixed Store CLI. Lazy corresponding existing package import is a candidate after focused reproduction/review, not a dependency installation. |
| Backend actors are created with `lifetime="detached"` in inference_instance:49–54. Controller.delete does not call the existing router.shutdown / _shutdown_instance path. | Restore a real awaited lifecycle using existing router/backend/actor shutdown APIs. Just deleting the handle is not evidence of releasing detached actors. |
| Router.shutdown only visits ready instances; background auto-scaler/load-balancer loops are whileTrue and are not cancelled there. Controller.shutdown references `_delete`, which is not defined in that class. | Review ready/starting instances and background tasks together before a minimal controller/router fix; never claim that uncommenting one line solves all ownership cases. |

## Existing asset search — bounded, read-only

- Stopped source CID `4001f3d943717ec521afe25cbe127116cacdf10c9d3697eba295e24245bf328a`, image `sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27`: correctly URL-encoded Docker archive HEAD reports404 for `/usr/local/bin/sllm`, `/usr/bin/sllm`, `/usr/local/bin/ray`, `/usr/bin/ray`, canonical dist-packages `ray/__init__.py` and `sllm/__init__.py`, site-packages alternatives, `/opt/venv`, and queried0.8.0/2.48.0 distribution metadata. PATH contains standard system/CUDA paths; no VIRTUAL_ENV/CONDA_PREFIX/PYTHONPATH override was present. This is bounded filesystem evidence, not package import proof or an exhaustive whole-image scan.
- qjhs002 host `/usr/bin/python3` (3.10 site paths): `importlib.util.find_spec("ray")` returned None without importing ray. Known `/opt/venv`, active/official baseline .venv, kimi-k2.6 venv/.venv, and boyjiang miniconda/anaconda env directories were absent. User/site/dist-package paths were inspected only as metadata.
- Existing downloads bounded recursive `*ray*.whl` filename search found none. No wheel contents or model payload was read.
- Docker image metadata filters `*sllm*`, `*ray*`, `*vllm*` found only the existing ec5 Kimi image; `docker ps -a --filter name=sllm` returned none. No other users environments or unrelated image filesystems were scanned; no qjhs003 access, migration, install, image build, new environment or container was performed.

Conclusion: no usable native Ray/service environment was located within these approved existing locations. Do not silently install Ray or claim ec5 ready for native serving. A later existing-asset decision is required; official MP save and restore can proceed independently.

## Ownership/topology facts that must enter final admission

- `get_worker_nodes` excludes nodes advertising control_node. Official topology separates head (control_node,0GPU) and worker (worker_node/worker_id_0,8GPU). One Ray node carrying both labels would be excluded as a worker by current source. Do not accidentally connect to a shared pre-existing Ray cluster.
- Router._start_instance:414–463 requests num_gpus8 and worker-specific resources; inference_instance creates the detached backend actor with that reservation. Backend configuration must also specify tensor_parallel_size8 and supported distributed_executor_backend=mp, and actual Ray CUDA ordinals must be reconciled with all eight physical UUIDs/PIDs. Ray accounting is not a GPU-memory or cgroup ownership proof.
- Local Store registration already loops rank_0…rank_7 using tensor_parallel_size. `model_downloader.download_vllm_model:179–181` returns when the archive model directory exists. Before native registration, mount only the fully accepted immutable Store output and gate all8indices/files; directory existence alone is insufficient. Do not invoke native conversion as a substitute for the preceding official Store stage.
- StoreManager.initialize_cluster runs collect_all_info: the existing collector writes/reads a100MiB temporary file five times and labels the result disk bandwidth. If /tmp is tmpfs this measures that tmpfs/cache path, not model storage. Include this actual work in resources/budget and do not use it as measured617GB throughput.
- Future native deployment needs Ray/head↔worker/Store RPC, so current isolated single-container networknone profile is not an automatically reusable cluster topology. Reuse official process roles with task-contained networking and explicit ports; do not make a new cluster/launcher framework.
- Containment budget must include head/API, raylet/GCS/object-store workers, Store pinned pool, eight model ranks, compilation/attention workspaces and all descendant processes. Ray object-store memory must be explicit/bounded rather than assumed free. Preserve all8GPU owner identity, per-scope MemoryMax/TasksMax and host locked-memory bounds.
- Loader cache limitation remains recorded: CPUempty0/del/gc proves reference release, not driver release; runtime-v6 has no empty_cache before native cudaMalloc. Constructor mostlyCPU, vision/projector explicitlyCUDA; no measured old77GB reserve or OOM claim. Current native AllocateCudaMemory also does not check cudaMalloc status. No fix to either is included here.

## Reuse SF gold and final acceptance gates

Frozen public SF AAB artifact: `/data/brick1/boyjiang/kimi-k2.6/p0/kimi-sf-tp8-reference-optional-mempeak-20261105-once-public/api-client.stdout`, SHA256 `ffbfa50c28e3af345eeddf469c84d1a27a7da8e3ee14f5b8a45bc6a8b4396ba8`. SF receipt SHA `b261e2b2ecf8140dbf3e597594b0be459ba90e54fea81be53024b7da8ad71c3a`.

Reuse its exact A,A,B content and canonical prompt IDs; temperature0, seed0, top_p1, n1, max_tokens32. Frozen SF requests used thinking:false/preserve_thinking:false (Instant); input_tokens should use those exact already-rendered prompts, not role:content concatenation. Native request fields for the existing TokensPrompt route are model/input_tokens plus supported SamplingParams fields only. Require exact expected text/finish_reason/usage and, after a narrowly reviewed existing-backend output change if necessary, prompt/completion token-ID equality. Do not claim ordinary native MM-message API correctness from these text-only gold requests; retain the native multimodal model and configuration.

Sequence after full Store conversion and eight-rank restore acceptance only:

1. Recheck booking, complete maximum budget, all8physicalGPU ownership, actual native environment/dependency identity, source/review/publication SHAs and independent guards. Do not infer budget from the micro or CPU passes.
2. Start only the existing official head/worker/Store/API processes in reviewed containment. Deploy using official sllm config: backend=vllm, num_gpus8, one instance (min=max1), BF16/trust_remote_code/mm_encoder_tp_mode=data/TP8 and accepted Store model path. Exact resource knobs follow measured restore/compile peaks, not guesses.
3. Wait for actual engine readiness and8rank metadata, then perform frozen AAB through the native endpoint and compare gold. HTTP register200/status alone is insufficient.
4. Delete through the official endpoint, require awaited model/actor/process teardown, no recreation, no orphan TP/NCCL processes and actual driver-zero/baseline for at least60seconds. Store CPU cache lifetime is a separately identified owner; GPU-zero does not imply its pinned pool was released.
5. Re-deploy the same accepted Store archive and repeat native gold without conversion/download. Then stop task-owned API/Ray/Store resources, verify all owned scopes/PIDs gone, native statuses/exit codes, memory release and at least60seconds final physical observation. Failures stop; no automatic retries or historical relabeling.

No launcher, loader, codec, source patch, environment, service or runtime probe was created by this audit.

## Source identities

| Active source file | SHA256 |
|---|---|
| `docs/getting_started.md` | `180d2fb4a9b473478af36d1449efd42d2b5b7b42521a3102d7130c9ca6d394ee` |
| `docs/deployment/single_machine.md` | `337f522b97a48622868a9ae46905e119520a97ea7485dbfd3a46aa4f6dc00282` |
| `docs/api/cli.md` | `7519ea868dd3c40957fe4f0e75189da7ec202ac1c81d81e0ff49cbc009fa433b` |
| `setup.py` | `c74f5a195bcfc6dc095da63a96b0d49fc1efccd6399171bfafc5de666316d264` |
| `requirements-worker.txt` | `423bda059c984f74d4b0ff02c30ce88d01a6ad6c1ede8728fc72ab9f8bb8d069` |
| `examples/docker/docker-compose.yml` | `b7b5b65cca9fecb71521a0796e05293e11d023ac93a8587b9a4bdff9aefdc3af` |
| `sllm/cli/clic.py` | `9b309d2025a604d2372be5ecee0ac4778e9dd262e5913573ff907b109e49d3d1` |
| `sllm/cli/_cli_utils.py` | `9b82b1bf3c747c136d8f6668f4b3f8fa0277ec6017762f25f1fccfc88326c91b` |
| `sllm/app_lib.py` | `462476744209740470fc32de10cc0fa825a69d64803045982e7f80ae7234fd9d` |
| `sllm/controller.py` | `8acf9c062daf244be4e58686f1bd69bb04c8d69d9a83eec7bcfd4b7847075e34` |
| `sllm/inference_instance.py` | `7a47e0579ded1eec5ad77f9e0cc7164242129739d066c54e5ac722d2f7837e5a` |
| `sllm/routers/roundrobin_router.py` | `b594f8c288df615f98ba2cb35b151e8ef8c95e25c4e06c953b5d7468e21fe7ca` |
| `sllm/backends/__init__.py` | `ea910272e33475c3f3e5cfaaad2b6031e5d504966207ba05f06cf1e834fe095d` |
| `sllm/backends/vllm_backend.py` | `daedf53b07dc4e20393f53cdc8a35d77941ec0cb238a1a32ceffcf9ca74f9ff6` |
| `sllm/backends/transformers_backend.py` | `1fa2c5c16ca8b22a94e487d4df1c9a01ed21f70f3f98aa6f6bb7535fb5c66501` |
| `sllm/schedulers/fcfs_scheduler.py` | `6e7f56dc217a98691e2e01fe2190cc70398e9ded83937b7e0491bd4d655abbf6` |
| `sllm/store_manager.py` | `29eac0acabcfcf821e43da0fb226c4451007e34559d8ae9212e2f875d55db2a5` |
| `sllm/model_downloader.py` | `353649e8bda84450491ba7724e336c54746cb561ea61f6a1073e5c73bf4701a9` |
| `sllm/utils.py` | `e3ebd803ee06f6665aaa51e586911e1049a5f2c118b612d750a25deff7d008fc` |
| `sllm/hardware_info_collector.py` | `370f73ce4a8e545cb3c1b6a82d66e8b27c298dafc71606386b06449814828545` |

Actual stopped-image vLLM identities (bounded tar-to-memory text/AST, no imports):

| Module | SHA256 / relevant interface |
|---|---|
| `vllm/__init__.py` | `fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346`; exports AsyncEngineArgs/AsyncLLMEngine/RequestOutput/EmbeddingRequestOutput |
| `vllm/utils/__init__.py` | `ee8c13f5e4963ff6535a2cd1ca39e4fa904d1592f940df9b079858019f8ae7ba`; Counter absent |
| `vllm/utils/counter.py` | `e976b45a31decb62f0c8f26419f61adfb9a03ac41f707245f64472a4871a1c34`; official Counter exists |
| `vllm/engine/arg_utils.py` | `b576b4e3156e7669857e2ed6cbd9a4ac50062472501bc39a80d3642991b5f362`; EngineArgs373–2246, AsyncEngineArgs2250–2275; task absent |
| `vllm/engine/async_llm_engine.py` | `3f751c61135ff53e463bb8fe363d342d5b3639829bce5d22bdabc620b412db5b`; v1 alias |
| `vllm/v1/engine/async_llm.py` | `b101a3cd5ea4b7d0cf1852824d8fc96ec8c6a83c0396aad54ad9c1c8d69442c3`; from_engine_args240–262/shutdown267–279/generate529–638/abort712–724/get_tokenizer864–865 |
| `vllm/sampling_params.py` | `8512e73c45a6d2f8015f2ca8d94c9fc9a05ca390d6206e2b387ad6550e9b014a`; HTTP template/token-return/stream options absent |
