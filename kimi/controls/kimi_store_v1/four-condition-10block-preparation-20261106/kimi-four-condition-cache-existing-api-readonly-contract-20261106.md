# Four-condition cache admission: read-only existing-source contract draft

Read-only preparation at 2026-11-06T11:27:08.691582+00:00. No Store/GPU/container start, cache advice/drop, payload read, installation, Git operation or source edit. This is a draft executable evidence contract, not a frozen sample protocol or authorization. Metadata-only directory listing confirmed rank_0 through rank_7 in the existing archive.

## Reuse existing APIs, do not equate async ACK with residency

client.py39-54 load_into_cpu submits LoadModelAsync with target CPU. server.py68-69 calls load_model_from_disk_async; CheckpointStore.cpp214-220 schedules std::async and returns0. StoreManager.loading_loop166-177 immediately records its logical pinned_memory_pool after these ACKs. Neither that dictionary, load_to_host's queued=True response, estimated_completion_time, nor the Python success log proves completed disk reads. ConfirmModel130-133 explicitly rejects CPU; its existing GPU path is a later GPU-copy confirmation, not a CPU preload barrier. Do not allocate a GPU model just to manufacture a pool-hit condition.

Existing per-rank native terminal log is sufficient under an explicit exclusive/serial ownership contract, so a new CPU-confirm RPC is not required for the first minimal implementation. Model::ToHost212-219 gets every read future;221-224 closes descriptors;226 reacquires the model lock;error227-246 does not print success. Success249-252 sets model state LOADED, prints `Finished loading model <exact path> from disk`, then returns0. The current model state is private; checked production .cpp files provide no setter assigning Model::state_=CANCELLED. The worker's cancellation return0 at168-171 is therefore not reachable through current official host-state API. GPU-replica cancellation is separate. Exceptions/negative pread/short reads do not reach this success log. This is a source-path conclusion, not a general guarantee under arbitrary memory corruption or future code changes.

A same-process log certificate must bind:
1. Exact authenticated Store host PID/startticks/fullCID cgroup and current source/ELF identity; record raw log inode/offset before preload, and parse only the new interval.
2. The exact8 registered model paths, derived from actual StoreManager._get_model_path and its rank_0..rank_7 list; no guessed substring match. RegisterModel validates each archive size. Issue existing load_into_cpu for those exact paths, require grpc OK plus response.model_path equality, but treat that as admission ACK only.
3. Within bounded preparation, require one fresh terminal native success line for every exact path (no missing/duplicate paths), with the emitting Store identity. Reject read errors, cancellation/error outcomes, log truncation/rotation or ambiguous interleaving. These short native lines do not have the former multi-megabyte JSON issue.
4. From those successes through the timed trigger: same Store process/pool, no Unload/ClearMem/Release/eviction/additional unrelated load or memory pressure that violates the protection contract. Private owned service topology and strictly serial callers must enforce this, not merely claim absence from one log. Require expected pool capacity and all-domain resource admission. Record this as pinned-host model residency, not Linux file-page-cache residency or a physical GPU cache hit.
5. The measured inference must still pass its own8rank restore/physical ownership and own Kimi correctness criteria. The later successful GPU confirms corroborate the selected path; they do not retroactively turn the preload ACK into a barrier or include preparation in the measured startup interval.

Model::ToHost76-78 returns0 immediately when already LOADING or LOADED, so calling the synchronous routine again would not itself form a barrier. Model::WaitInHost396-410 exists, but the current successful ToHost transition does not notify its cv; it is not exposed by the Store binding/RPC. This unused helper limitation is recorded only; no change is needed if the log certificate above is implemented correctly.

## Logical empty pool and store_disk_cold

Reuse UnloadModel(CPU) for each exact rank (client.unload_from_cpu), checking grpc status and returned path, or existing stub.ClearMem(ClearMemRequest()) for all models. The Python wrapper has no ClearMem convenience function, but the generated proto already has the RPC. Do not call release_memory_pool: that terminally destroys registered buffers and changes the experiment.

The source chain is UnloadModel→UnloadModelFromHost→Model::FreeHost, or ClearMem316-327→FreeHost for every map entry→map.clear. FreeHost479-495 waits LOADING host/replicas,500 resets pinned_mem_, and PinnedMemory's destructor calls pool.Deallocate, returning chunks to the existing registered free list. Thus an empty model cache means no model-owned buffers, not640GiB of memory disappeared or was zeroed. RSS and remaining Store CUDA contexts are not counterproof: the empty pool allocation intentionally remains.

For a trustworthy empty certificate, first ensure all pending load jobs/replica operations are already finished and no new load can enter. ClearMem itself does not drain the retained std::async queue and returns0 even after logging an individual FreeHost error. Prefer the8 successful UnloadModel responses plus fresh per-model deallocation logs, no error, and known total borrowed chunks returned under the exclusive8-model scope; then ClearMem may remove metadata. A fresh initialized Store with no model loads is the cleanest alternative when restarting it is already part of the declared condition, but its long initialization is preparation, never hidden measured work. Existing APIs expose no free-chunk count or per-model host-state snapshot: a direct numeric allocator snapshot would be a future optional narrow addition only if the exclusive source/log proof cannot be established. No new schema is presumed necessary.

## Four draft condition certificates

| Condition | Required evidence before a trial |
|---|---|
| safetensors_disk_cold | No ready model/old model GPU actors; exact SF files/geometry and filesystem identity; cold residency certificate for relevant client page cache and, on NFS, a separately justified server-cache scope. No warming before trigger. |
| safetensors_disk_hot | Same ready-model absence; intentional full-file warming outside the measured interval with complete budget/protection, followed by actual all-file residency evidence immediately before trigger. A warmup model run or one shard is not a full595GB file-cache certificate. |
| store_disk_cold | Same no-ready-model condition; verified no model-owned pool buffers as above plus declared Store-file cache domain/IO behavior and cold evidence. Pool initialized but empty is distinct from pool destroyed. |
| store_pool_hit | Fresh8rank completed ToHost certificate, same protected Store/pool and no intervening eviction; then create a fresh model instance from the native archive. Linux file cache state does not define this pinned-pool condition. |

These predicates are deliberately independent of sample counts, order, cutoffs and effect tests. They must be frozen later with complete budgets and failure handling; no existing historical run is being relabeled into one of these conditions.

## Current controller and filesystem limits

A bounded source inventory found no mincore, posix_fadvise, POSIX_FADV_DONTNEED or drop_caches implementation in current sllm_store/tests Python, benchmarks Python or tests Python. A readable small JSON/Markdown evidence inventory likewise found no existing Kimi inline cache-operation implementation. Thus existing Kimi lifecycle controls do not yet deliver cold/hot certificates. Historical SF states remain uncontrolled. No GLM controller or agent was imported/contacted.

A minimal future addition belongs in the existing Kimi preparation/controller's condition-preparation and admission phase, not a new launcher: enumerate frozen file manifest, per-file read-only fd/stat identity, aligned whole-file cache advice if authorized, and bounded non-populating mappings for residency snapshots; report every syscall result/coverage and per-file totals before/after. The advisory return alone is insufficient; a full warm read is large real work requiring separate admission/budget. Validate that the residency probe can report trustworthy results for the file ownership/permissions/kernel configuration; never treat missing/unsupported results as zero/hot. Do not globally drop shared server caches or modify others' data.

Linux documentation: [posix_fadvise(2)](https://man7.org/linux/man-pages/man2/posix_fadvise.2.html) describes advice as nonbinding; DONTNEED attempts discarding and ignores partial-page requests. [mincore(2)](https://man7.org/linux/man-pages/man2/mincore.2.html) gives a local residency snapshot that can become stale. [open(2)](https://man7.org/linux/man-pages/man2/open.2.html) explicitly states NFS O_DIRECT bypasses client page cache, while the server may still cache IO. Consequently client-only fadvise/mincore cannot establish q0 server-disk cold for q3's shared NFS path.

Actual Store model.cpp96 uses O_DIRECT|O_RDONLY, with O_RDONLY fallback only on EINVAL99-104. Therefore pool-empty Store loading and SF mmap/page-cache hot loading are different official IO paths; do not claim both hit the same OS-cache mechanism or silently disable O_DIRECT to force agreement. Actual fallback must be recorded. qjhs003 /home is NFS from qjhs000172.31.0.5; qjhs000's backing /home is local XFS. q3 client residency or natural source reuse cannot stand in for q0 page cache, storage-controller cache, remote readahead or physical-disk cold. A future protocol must either control/observe the required server domain under authorization, or accurately name a client-cold/server-uncontrolled condition. No cross-node speed comparison follows from the current evidence.

Pinned source identities:
- sllm/store_manager.py SHA256 29eac0acabcfcf821e43da0fb226c4451007e34559d8ae9212e2f875d55db2a5
- sllm_store/sllm_store/client.py SHA256 203f22054e1cb2661f3d02d63dcfa7697b8f1d37d7a057ad6a514c43995d205f
- sllm_store/sllm_store/server.py SHA256 ae2636383bfd4c89365a03b6772655a6846b2234767250fc5c2d518a8aea7d52
- sllm_store/sllm_store/proto/storage.proto SHA256 95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b
- sllm_store/csrc/sllm_store/checkpoint_store.cpp SHA256 b6a75571631e6901a50263ee96603945526d0ede6cc1288f8253688ef04dbea3
- sllm_store/csrc/sllm_store/checkpoint_store_py.cpp SHA256 9947ed2618493e12918a8223230942edfe8ff8689e43719d633cf4f12478230d
- sllm_store/csrc/sllm_store/model.cpp SHA256 6f8b64645763a1e336b77f27c8a301bb45e84b545e38858e8374f80091a84509
- sllm_store/csrc/sllm_store/model.h SHA256 8644ef5aaa2c50d2958a9fb0864948bf766dd73bdf85fac4d1de582eee9aae83
- sllm_store/csrc/sllm_store/pinned_memory.cpp SHA256 e821c99d9a8f75c57adfea81f803ef307b8c67794f746274bcc0ecd0403d7a2c
- sllm_store/csrc/sllm_store/pinned_memory_pool.cpp SHA256 058b84f43d6ee6fd02665db19a71b93ddc1612532eb3f7bc03351293b9dced1d
