# B2 smoke results (2026-11-11)

## Level 1 — PASSED (`B2-B2-smoke-r1-20261111T113723Z`)

Sequence `D → D` (V4 loaded natively, run, sleep L1, wake, run, sleep, shutdown).

| event | value |
|---|---|
| native load | 447.2 s |
| run 1 golds | pass |
| sleep L1 | 47.4 s, 8 ranks, `cpu_backup_bytes = 880,921,280,512` |
| wake L1 | same worker PID `813729`, `cpu_backup_bytes = 0` |
| run 2 golds | pass |
| sleep again | 880.9 GiB backup, same PID |
| PID continuity | **true** (one PID across load → sleep → wake → sleep) |
| final release | 59.9 s all-zero, `final_all_gpu_zero: true` |
| qualification | `passed: true` |

GPU memory went 107,321 MiB → 4,535 MiB after sleep and back, measured at 1 Hz.

Note: the first L1 attempt (`…T110231Z`) was OOM-killed because the worker container had a
512 GiB cgroup limit while the L1 backup needs 880.9 GiB; the limit is now 1400 GiB and the
controller refuses an L1 load when `MemAvailable < weights + 200 GiB` (recording
`ram_infeasible` instead of crashing).

## Level 2 — FAILED (`B2-B2-smoke-r1-20261111T115102Z`) — 未通过

- sleep L2 succeeded: 8 ranks, `cpu_backup_bytes = 0`, `sleeping = true`, golds before it passed.
- wake failed. Worker log shows, for every layer:

```
WARNING [layerwise.py:225] DeepseekV4Attention: Failed to load weights
WARNING [layerwise.py:225] DeepseekV4MLAAttention: Failed to load weights
WARNING [layerwise.py:225] LayerNorm: Failed to load weights
```

followed by

```
RuntimeError: CUDA Error: out of memory at /opt/src/vllm/csrc/cumem_allocator.cpp:139
  in vllm/v1/worker/gpu_worker.py:185 wake_up -> cumem.py:234 create_and_map
```

So on this model the layerwise weight reload (`collective_rpc('reload_weights',
is_checkpoint_format=True)`) does not restore V4's parameters, and the subsequent
`wake_up(tags=['kv_cache'])` runs out of device memory. Level 2 is therefore recorded as
**not passed** for DeepSeek-V4-Pro with the pinned engine; no numbers are synthesized for it.

Possible next attempts (not done): reload via `is_checkpoint_format=False`, or rebuild the
engine's weight iterator from the model loader and call `load_weights` directly.
