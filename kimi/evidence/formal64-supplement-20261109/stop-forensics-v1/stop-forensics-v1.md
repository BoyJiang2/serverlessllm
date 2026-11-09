# Storecold exit60 forensics

The new block remains failed: SFhot qualifiedPASS; Storecold failedexit60; later2NOT_STARTED; completeblockn0. No retry was performed.

## What is proved

The same Store process was captured at both points: hostPID1495474/startticks802293651, namespacePID1238. The10.507s snapshot has the main threadR/syscallrunning; the20.423s snapshot hasD and an NVIDIA os_acquire_rwlock_read -> rmapiLockAcquire -> serverFreeResourceTree -> ioctl stack. These are sequential, bounded proc reads, not atomic samples or a permanent-deadlock proof. Other thread futex/poll waits do not identify the rootcause.

Eight rank borrowers each returned1150chunks (9200total); Allmodelsunloaded appears before poolreleasebegin10240. The observed slow phase is therefore inside the native pool-release loop after borrower return. Same-thread progress continues through elapsed59859ms: attempted2880/failed0/total10240. No whole-loop completion line appears.

**attempted is not completed pool release.** failed0 does not certify all10240unregister calls or normalexit. No constant-rate completion prediction is justified. Between10/20 snapshots main user/system counters increase andRSS falls; this supports changing process state, not CUDA-time attribution.

## Source and ELF mapping

The actual654888-byte native ELF hashes95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd. Buildmanifest59d70f... matches the frozen current and historicalspecs; all27sourcepins match. Python gRPCshutdown -> ReleaseMemoryPool -> DrainAsyncTasks -> ClearMem -> pool.Release. The loop holds its ownmutex, invokes cudaHostUnregister, then successfree/erase, increments attempted and logs every64. It has no per-call timings.

The20s kernel stack cannot distinguish unregister from memory-free/driver interactions without a userspace unwind/call bracket. No historical StorePID maps/fd-link proof was captured; verifiedsource/deploymentpins are not a full loaded-module map. The release binding lacks an explicitGILrelease, but no evidence makes this the cause.

## Historical33.139s remains valid within its scope

The acceptedq3run used the same95ELF,640GiB/10240buffers/64MiBchunks. Its160progress records reach attempted10240/failed0, completionline elapsed31318ms, and actualnormalexit0+strictgone at33.139352623s. Those raw files were rehashed against its originalindependentreview.

It differs in node/boot/GPUUUIDs, lifecyclecount(2vs1), resourcehistory and controller/modelsettings. Driver/kernel/pageplacement/runtime-lockstate equivalence is not proved. One completedq3run never established a stableq0bound. OwneracceptedL1 and oldDIAGNOSTIC_NOT_L1/30FAIL labels are unchanged.

## Next step is a proposal only

First separate bounded observation of true normalexit duration from optimization. The existing stop-observe helper is fixedlegacy30/cap300/oldIDs/twolifecycles, so its CLI must not be launched as a newcase. A newexplicitdiagnostic would preserve this fixed60failure and could only observe inside a separatelyreviewed finitecap and unchangedguards/fullbudget; no extraoldL1reload.

Realoptimization needs per-call unregister/free/erase timing evidence first. Minimalpotentialinstrumentation belongs inexisting pinned_memory_pool.cpp plus test_pinned_pool_shutdown_logging.py, with defaultbehavior/APIorder/errorretention unchanged. No parallelism, NUMA/chunksize change or deadline widening is justified now.

All126rawcopies (669682bytes) passed SHA/size checks. Existing7CPUstubtests passed; they do not exercise CUDA or prove shutdownspeed. Guards/observerquiet>60 and safetyrelease afterforced137 remain separate from normalStopFAIL. Exactreferences, all45progressrecords and explicitmissinginformation are in the JSON.

No source/Git/config/budget changes, GPU/Store/model starts, weight/cache operations or retry occurred.
