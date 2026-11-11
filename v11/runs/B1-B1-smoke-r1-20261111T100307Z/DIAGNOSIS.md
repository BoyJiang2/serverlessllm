# B1 smoke attempts — diagnosis (2026-11-11, updated 19:05)

Five B1 attempts were made. Directories are kept; only an attempt with a passing
`qualification.json` counts as evidence. So far **no B1 attempt has passed**; the current
blocker is a register-schema mismatch between vLLM 0.20.1's MP connector and LMCache 0.5.6.

## `B1-B1-smoke-r1-20261111T092923Z` — INVALID (foreign server)
A leftover probe container held ports 9100/5555; the run's own LMCache server exited and the
Kimi worker registered against the probe server (2 GiB tier). Marked in `INVALID.md`.
The controller now refuses to start when 9100/5555 are busy and asserts the started server is
Running with the configured tier size.

## `B1-B1-smoke-r1-20261111T094324Z` — failed: register timeout, no GPU in the server
The LMCache server ran without GPU device access; the register payload carries CUDA IPC handles.
Fix: server container now gets `--gpus` + `CUDA_VISIBLE_DEVICES`/`NVIDIA_VISIBLE_DEVICES`
(it needs only a CUDA context, not weight memory).

## `B1-B1-smoke-r1-20261111T100307Z` — failed: `ModuleNotFoundError: cupy`
With a GPU-visible server the client's request did arrive, and the server rejected it:

```
ERROR: Error handling operation register_kv_cache
  File ".../lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py", line 483
ModuleNotFoundError: No module named 'cupy'
```

The error path did not answer the client, so the worker waited out the 300 s timeout.
Fix: `cupy-cuda13x==14.2.0` installed into the overlay (now ~300 MB).

## `B1-B1-smoke-r1-20261111T102728Z` — failed: register schema mismatch
With cupy present the server accepted the wire call but rejected the registration body:

```
ValueError: AttnWindowDesc: world_size must be >= 1, got 0
  at lmcache/v1/multiprocess/engine_context.py:88 (LayoutDescRegistry.register)
```

The client is vLLM 0.20.1's bundled adapter
(`vllm/distributed/kv_transfer/kv_connector/v1/lmcache_integration/`, plus lmcache's
`lmcache/integration/vllm/vllm_multi_process_adapter.py` from the 0.5.6 overlay), which sends
`engine_group_infos` carrying `world_size = 0`; the 0.5.6 server refuses that value. The
worker then times out after 300 s and engine init fails, so no activation is recorded
(`activations: 0`, `complete: false`, `passed: false`).

Server log retained: `lmcache/lmcache.log` (3 637 lines, 64 error/warning hits).

## Next actions (not yet done)
1. Try a newer LMCache release whose register schema matches vLLM 0.20.1 (0.5.7rc4 is on pypi),
   or pin the exact lmcache commit vLLM's integration was written against; re-run this smoke.
2. If no version matches, record B1 as **未通过** with this evidence and keep A / B2 results
   independent (B2 does not use LMCache at all).
