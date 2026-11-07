# Model-loading memory log scope

The active f7 runner assigns `model_memory_usage = m.consumed_memory` after its DeviceMemoryProfiler context, then formats it in GiB at line4965. It is not a total device-memory measurement.

DeviceMemoryProfiler subtracts two endpoint calls. Its exact-commit upstream mem_utils bytes SHA080206… match the historical installed-image SHA. The CUDA platform function empties cache, resets peak statistics and immediately queries max_memory_allocated at EACH endpoint. Thus the printed value is an endpoint change in Torch allocator-tracked allocated memory; the reset discards the preceding peak. CUDA platform source here is exact upstream b1388b1fb, not a newly recovered installed-file hash.

Store uses direct CUDA allocation and wraps existing pointers with from_blob. Such external allocation is outside the Torch caching allocator accounting. Reported SF72.05GiB versus Store0.19GiB therefore cannot be interpreted as physical total memory, weight size, or NVML/model-load peak; Store0.19 does not mean the restored weights are almost memory-free. Existing physical NVML and authenticated rank ownership remain the relevant independent residency/peak chain.

The display rounds bytes/2**30 to two decimals. Keep this log only as allocator endpoint delta, separately from the model-loading duration and actual physical resource observation.

Source: [vLLM profiler](https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/utils/mem_utils.py), [CUDA platform](https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/platforms/cuda.py), [PyTorch allocator limitations](https://docs.pytorch.org/docs/2.10/torch_cuda_memory.html). No runtime/import/GPU or new memory measurement was performed.
