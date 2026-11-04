# Existing native DEBUG profile-memory evidence

Actual stopped ec5 installed sources independently read without imports or execution: v1/worker/gpu_worker.py SHAffbd4567a25e0a82ca5c714b75e86c7353cedb6fc5c2814b22f3c91afb1c88b1, utils/mem_utils.py SHA080206924a725b06312a975427217c6b6d4d712f302ec22b3de5fa2a98a21159 and logger.py SHA233720900b1207434e4824bbddcf86dba0dc8557a0bfe78a639545462a9e85b5. Runtime-v6 gpu_worker overlay only adds Store RPC and retains the original determine_available_memory profiling/logger behavior.

The existing logger can expose native profile computation without a new hook/probe: set only VLLM_LOGGING_LEVEL=DEBUG in the new full-save spec. logger._configure_vllm_root_logger applies that level to both vllm root logger and its handler. gpu_worker.determine_available_memory calls logger.debug(profile_result) directly after successful profiling; it is not logger.info_once/debug_once and has no rank-zero condition. Each TP worker invoking that method is expected to emit one row, including all8 when normal memory profiling completes. Actual logs must still establish8 unique owned worker PID/rank prefixes; source reachability is not runtime completeness. Keep existing512MiB bounded full logs and no new NCCL/GLOO/network variable. The inspected pre-final candidate had no VLLM_LOGGING_LEVEL set.

MemoryProfilingResult.__repr__ emits:
- profile_time with .2f seconds;
- non_kv_cache_memory;
- torch_peak_increase;
- non_torch_increase;
- weights_memory.
Each memory number is formatted as round(bytes/GiB,2), approximately +/-0.005GiB rounding uncertainty per displayed field. The result repr does NOT print absolute profile_torch_peak, before_profile.torch_peak, exact byte counts or reserved-byte peak. MemorySnapshot has its own repr including torch_peak/free/total/cuda/torch/nonTorch, all rounded, but the worker does not log its before/after snapshots individually here. Do not assume those fields will appear.

During normal profiling memory_profiling resets Torch peak counters, measures before_profile, runs MM encoder/text dummy/sampler, then measures after_profile. gpu_worker explicitly captures allocated_bytes.all.peak immediately after profile_run and uses its difference from before_profile.torch_peak as torch_peak_increase, avoiding later cudagraph double-counting. With eagerTrue graph estimation is skipped, not MM/text/sampler profiling. non_torch_increase is after-profile minus before-instance nonTorch memory, not the instantaneous maximum of all nonTorch allocations. weights_memory is passed from model_runner.model_memory_usage; nonKV sum is an internal KV-budget estimate, not a whole-device exact instantaneous peak.

Therefore DEBUG is a useful minimal supplement for the previously unprinted native profile phase and should be retained with these precision/phase limitations. It does not replace structured load-boundary precise counters, physical NVML observation, all8 ownership checks or cap/failure rules; do not label the rounded delta or nonKV estimate exact absolute GPU peak or import the prior SF observed peak as this save profile result. No extra CPU/GPU probe or source/logger modification is needed to turn on this existing diagnostic log.

No real Torch/vLLM import, source change, build, container create/start, GPU or Git mutation occurred in this read-only audit.
