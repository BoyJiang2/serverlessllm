# SF Store-pool dependency audit

**Source conclusion:** this exact SF native-service chain needs a live Store for GetServerConfig and8rank model registration, but no model-sized Store pool borrower was found. The frozen protocol nevertheless creates640GiB immediately at Store startup. Reducing it is a new protocol proposal, not an observed runtime success or a cache-failure fix.

| Actual stage | Store pool allocation | CPU payload load / GPU restore |
|---|---|---|
| Store constructor before API | Allocates entire configured pool; CUDA contexts/streams and cudaHostRegister | No model payload yet |
| GetServerConfig / StoreManager cluster discovery | Bookkeeping only | No |
| /register -> StoreManager -> local8rank RegisterModel | Model metadata only: access/stat tensor.data_N, stateUNALLOCATED | No |
| Registration preload | placement.memory_pool=[] bypasses it | No |
| Instance scheduling | Actual start argv omits storage-aware flag ->False ->FCFS | No prefetch |
| Explicit safetensors backend | DefaultModelLoader reads localSF via vLLM/Torch | No Store load_dict/CPUload/GPUrestore |
| SF delete / engine shutdown | Torch/engine teardown and scheduler resource release | No Store payload action |
| Final Store stop | Drain/ClearMem/unregister/free configured pool | No absent SF borrower to wait for |

The alternative storage-aware scheduler has automatic load_to_host, but is not selected: CLI and start_server passFalse and Controller selectsFcfsScheduler. Checking placement.memory_pool=[] alone would not establish this.

The converted directory remains required: VllmModelDownloader returns when/store/vllm/model exists; otherwise conversion/download code exists and must remain excluded. RegisterModelInfo calls Model.Initialize, which access/stat partition files only; it does not read payload or tensor_index or borrow the pool.

## SF quant layout is still real work

The actual stopped-image BaseModelLoader andDefaultModelLoader were exported read-only without starting/executing the container. Runtime mount provenance distinguishes them from active bound __init__/utils. Safetensors selectsDefaultModelLoader; inheritedBaseModelLoader calls load_weights then activeutils.process_weights_after_loading. That invokes quant methods, including packed-weight/scale Marlin repack and runtime workspace construction, then attention postprocessing.

Store instead callsServerlessLLMLoader.load_model ->load_dict ->CPUload/GPUrestore/confirm and binds saved converted tensors; it runs prepare_runtime_for_restored_weights plus attention processing, without full repack again. A small SF Store-control pool does not remove SF quant processing or imply weight equality.

## Derived minimum, not an arbitrary default

Zero is rejected. With unchanged64MiB chunks, constructor allocates ceil(pool/chunk) full chunks and logs non-alignment; Python bookkeeping usesfloor(pool/chunk). The smallest positive **chunk-aligned** configuration is therefore64MiB, allocating one64MiB chunk and reporting onechunk. A1byte configured pool would still allocate64MiB physically and report0chunks, so it is not a useful smaller configuration.4GiB examples do not establish an SF minimum.

General Store documentation says pool>=largestmodel for payload loading. This metadata-only SF exception is inferred from the actual call chain, not claimed as that general recommendation. Both Store conditions retain640GiB.

Even onechunk still calls CUDA APIs and cudaHostRegister across a Store service with8GPUstreams. Testing it is not a CPU-only/no-GPU test. It requires a distinct approved cohort, exact generated CLI/GetServerConfig checks, full budgets/guards and real lifecycle validation. No reduction is guaranteed to make all-hot residency pass; the earlier cache failure cause remains unresolved.

## Evidence and limits

Every path/line range/fileSHA and ordered call is recorded in the JSON. Frozen native source pins were checked, and all27native build source pins match the95ELF build manifest. This binds the C++stat-only registration and immediate-pool constructor to the frozen artifact provenance without importing it.

Current q0SFhot failed before any load endpoints; no small-pool lifecycle was run. Existing metadata does not prove full tensor-value equality. No Git, synchronization, runtime source changes, model/container/Store start, cache operation or weight read was performed.
