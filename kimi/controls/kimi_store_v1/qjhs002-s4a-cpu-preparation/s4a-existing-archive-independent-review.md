# S4a existing-archive independent source review

Verdict: PASS_SOURCE_REVIEW_FOR_CPU_PREPARATION; NOT_RUNTIME_APPROVAL. Reviewed stable candidates only; no component, test, SDK, Store, container, SSH, GPU, Git or sync execution was performed by this reviewer. Local source reads and review-file writes only.

Reviewed candidate SHA256:
- work/test_kimi_tensor_component.py: 9f06cd27bcb800b1c0f6b5703f10f1606b90e36757cecdd321239e431b18d232
- work/test_component_existing_archive.py: 538631948e284d54edaed89ddfff041bb20a73481d495158999c884a53959228
- outputs/s4a-cpu-save-only-receipt.json: 3817c2e7b942af18388478823c01063cf3ce2f969c6ff9107e36c79b78300ea5

Read both readback plans including final authoritative supplement. All eleven exact-source snapshots match source-map.json hashes, checked locally. No historical unresolved confirm/allocation-size inference was reused.

## Candidate findings

No blocking defect found in the existing-archive branch within the stated immutable/read-only archive mount envelope. Receipt fields are anchored to reviewed constants rather than trusting a caller-provided receipt alone: exact three tensor index entries, per-slice SHA, index/data SHA, payload/aligned/total extents. Constants match the retained save-only receipt. Exact file digests reject any metadata/stride/dtype/offset/data/padding alteration before torch.frombuffer; this makes a second generic extent validator unnecessary for this fixed archive. Byte reads are capped. Exact two regular non-symlink files, absolute storage root, traversal/outside-root rejection and ancestor symlink checks cover the intended Linux read-only mount. Mutable adversarial concurrent filesystem replacement is outside that envelope; mounts must remain read-only.

Existing mode calls neither save_component nor save_dict and does not inspect checkpoint shards. CPU expected tensors come only from verified small archive bytes, using whitelisted int32/BF16 frombuffer and reshape, without cast or dequantization. Returned CUDA tensors require exact device/dtype/shape/stride plus raw-bit equality and trusted SHA for each tensor. Numeric equality is additional, not the raw-bit acceptance criterion.

The original finally ordering remains: clear returned CUDA references, GC/synchronize, attempted-load CPU unload, then close channel; cleanup failures are recorded without replacing a primary exception. AST cleanup tests exercise this actual block. Focused CPU tests execute the candidate validation AST with fake tensors and synthetic payload hashes; they validate branch/rejection behavior, not real torch.frombuffer, CUDA, actual archive readback or native lifetime. Reviewer did not execute tests; writer-reported CPU results need the parent's receipt verification.

Candidate adds driver free-memory samples before load, while one packed alias remains after other returned tensors are dropped, and after the final alias is dropped, all before process/server teardown. It rehashes the retained packed alias and requires both retained allocation and final release deltas >=8,257,544 bytes. This is appropriate additional driver evidence for direct native cudaMalloc; Torch allocator peaks alone would miss it. Driver/NVML observations must still be correlated to the authorized GPU and owned processes, and server teardown cannot replace this in-process evidence. These assertions can fail conservatively from unrelated driver activity; they do not guarantee completion latency.

## Confirmed separate failure-ownership gap in exact official source

Official torch.py load_dict_non_blocking currently calls allocate_cuda_memory, get handles, UUID/offset calculations, client.load_into_gpu, checks its return, and only then restore_tensors. In exact client.py an RPC error returns False. Therefore an explicit RPC failure takes the raise path before RestoreTensors creates shared owners. Python holds only raw pointer integers; component restored is still empty, so its finally cannot eagerly cudaFree that allocation. This is a source-proven failure path, not a hypothetical kernel failure. Process exit may contain/release it, but cannot satisfy eager ownership proof. The successful final-alias check cannot demonstrate this failure path is fixed.

RestoreTensors first builds shared_ptr owners for supplied base allocations and then constructs tensor views. Its own exceptional unwind already owns/frees those bases; adding a blanket outer cudaFree would risk double free. A narrowly reviewed fix could calculate pure metadata/offsets before allocation, transfer allocation ownership to RestoreTensors before issuing the fallible GPU RPC, and preserve returned views until confirmation succeeds; all remaining raw-pointer operations before that transfer need explicit consideration. Do not claim that moving one call automatically resolves every pre-transfer exception. No source fix is made or approved here. A minimal CPU mock should execute actual load_dict_non_blocking with mocked allocate/handles/UUID/client/restore and inject RPC False, asserting restore/owner acquisition was not reached; it must not import CUDA/SDK or be reported as native release proof.

Exact client.confirm_model_loaded already raises RuntimeError from grpc.RpcError, including CANCELLED. No extra confirmation patch is justified. Native AllocateCudaMemory requests exactly 8,257,544 bytes here. 64MiB Store pool is host pinned memory, not GPU cap; driver granularity, CUDA contexts/streams/IPC are additional domains.

## Boundaries

S4a remains SAVE_ONLY_PASS_READBACK_NOT_RUN. Candidate deployment targets only the existing active root sllm_store/tests/python/test_kimi_tensor_component.py and focused CPU test; no new codec/loader/converter/framework. Any S5 ownership repair is a separately scoped exact-source change and needs failure-path verification.

No new GPU attempt is authorized. Keep GUARD938 GO25, cleanup65, release60, native140/full180 unchanged. Before a separately approved attempt, independent review must check concrete readiness/client/shutdown cutoffs within GO25, with official shutdown up to five seconds included; timeout is FAILED_RETAINED, not a reason to auto-extend or retry. Unknown completion latency does not itself demand an invented guaranteed-latency gate. Fresh resource/identity/observer admission and original-bit readback plus ownership/release evidence remain unproven.
