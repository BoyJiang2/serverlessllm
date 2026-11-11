# V7 acceptance — unified vLLM 0.20.1 weight Store chain and independent Sleep L1

2026-11-10/11, qjhs003 (`h20/qjhs003`, 8×H20-3e). Scope is the V7 plan: weight / SLLM Store CPU
cache reuse on the mainline, vLLM Sleep Mode as a separate baseline. No CPU KV L2, no LMCache,
no cross-backend KV persistence, no W/K/G scan. GPU KV stays backend-local runtime state.

## 1. Pins

| item | value |
|---|---|
| vLLM | `0.20.1` @ `132765e3560659ff63ebd236203672e991b70e08`, Torch `2.11.0+cu130` |
| image | `sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d` |
| Store | one resident process, pool 1 TiB, block 32 MiB, host pid `655211` for the whole chain |
| sequence | Kimi → GLM → V4 → Kimi → Kimi (fixed five activations), same Store, same pool |
| inputs | `v7/candidates-v2/{kimi,glm,v4}-input.json` (frozen hashes inside the protocol) |
| protocols | `v7/protocol.json` sha256 `66ebda7f…a00`, `v7/sleep-protocol-v4.json` sha256 `80874a0b…1fc` |

## 2. Reused evidence and its limits

Reused unchanged: the 2026-11-09 shared-Store records (`SHARED-STORE-RESULTS.md`,
`COMPLETION-AUDIT.md`, fixed five steps and twelve directed switches), the historical Kimi
payload and golds, the existing V4 grouped payload, the native ledger / exact-CID guard /
observer / release machinery, and the three-model report at commit `6a8d44a9`.

Not reused as new acceptance: those runs used Kimi `0.19.1` and GLM `0.19.1.dev1`. The 0.20.1
chain below is the new unified-version evidence; earlier failures and payloads are untouched.

## 3. Mainline A0/A1/A2 — `v7-store-20261110T140421Z`

`fixed_chain_passed: true`, guards `store,00,01,02,03,04 = 0`, final release `74.8 s` with every
GPU at 0 MiB, observer exit 0, no cleanup errors.

| step | model | Store cache | payload read bytes | pool hit bytes | blocks alloc / freed | evicted paths | rank confirms | activation → correct |
|---|---|---|---|---|---|---|---|---|
| 00 | Kimi | full_miss | 617 000 763 392 | 0 | 18 392 / 0 | 0 | 8 | 248.6 s |
| 01 | GLM | full_miss | 762 527 481 856 | 0 | 22 728 / 9 196 | 4 | 8 | 505.0 s |
| 02 | V4 | full_miss | 877 729 513 472 | 0 | 26 552 / 26 242 | 10 | 496 | 243.0 s |
| 03 | Kimi | full_miss | 617 000 763 392 | 0 | 18 392 / 17 903 | 217 | 8 | 159.3 s |
| 04 | Kimi | full_hit | 0 | 617 000 763 392 | 0 / 0 | 0 | 8 | 83.9 s |

- **A0 cold read and pool hit.** Step 00 starts from an empty pool: all eight Kimi rank paths
  are read from disk and the native receipt shows `SLLM_CACHE_COMPLETE … resident=1` plus
  `file_read_bytes` equal to the sum of the eight `path_sizes` (617 000 763 392 B). Step 04
  reads 0 bytes and hits 617 000 763 392 B from the pool with 0 block allocations. Store-cold
  here means pool-cold; OS page cache was neither dropped nor claimed as cold.
- **A1 capacity, eviction, reuse.** GLM (762.5 GB) cannot be added to a pool already holding
  Kimi (617 GB): 9 196 blocks are freed and 4 Kimi rank paths are evicted. V4 then frees
  26 242 blocks and evicts 10 paths. Re-activating Kimi at step 03 requires a full re-read
  (617 000 763 392 B again) after 17 903 blocks are freed and 217 paths evicted, and the repeat
  at step 04 is a pure hit. Residency after step 04 is 1 090 355 757 056 B of a 1 099 511 627 776 B
  pool — the pool stays resident and near full; returning logical blocks is not an OS release.
- **A2 switching closure.** One Store process (pid 655211) serves all five activations; every
  backend container exits 0 and each rank process is proven dead before the next activation;
  between activations only the Store's own 2 387 MiB CUDA context remains on the eight GPUs
  (five idle windows of 136–140 s each, all samples). No KV or attention state crosses a
  backend boundary: each backend allocates its own KV (e.g. 234 GB for V4) and releases it with
  the worker.
- **Functional question.** `请只输出数字：17 + 25 = ?` returns `42` in all five activations
  (Kimi ` 42`, GLM `42`, V4 `42` tokens `[3180, 1]`). It is reported next to, not instead of,
  the frozen business golds and token alignment.

## 4. GLM on 0.20.1: rebuilt payload and version boundary

The historical GLM payload is the raw pre-postprocess state of `0.19.1.dev1` and is rejected by
the 0.20.1 constructor schema (retained failure `v7-store-20261110T054033Z`, and
`v7-store-20261110T122351Z` before the loader received the frozen identity). It was rebuilt
under the pinned image into a new directory, keeping the historical payload and golds:

- `v7-glm-rebuild-20261110T113709Z` exported `/data/brick3/boyjiang/glm5.1-fp8-store-v7-target-0201-20261110-v3`
  from the 0.20.1 native loader: all eight rank saves finished and the workers shut down at
  11:51:59Z, then the 1180 s `store-conversion` guard lease cut the in-container hash pass.
- `v7_glm_verify_store.py` re-established the facts offline: 8 ranks × 4 506 tensors,
  762 527 481 856 B, tensor-index/alias/byte-coverage checks, per-file digests,
  `COMPLETE.json` sha256 `3cfbb04e…819`.
- `v7-native-glm-20261110T135319Z` is the native (Store-free) 0.20.1 reference for the same
  token requests. Its `fixed32` output is token-identical to the Store-restored output
  (both `… 105860,103664,101070,101538,105047,3837,100604,…`), and both differ from the frozen
  `0.19.1.dev1` gold at token index 6 (`100310`). The two short golds match in both paths.
- The chain therefore accepts either the preserved historical gold or the explicit
  `expected_native_0201` reference and records which one matched; step 01 reports
  `matched_reference: native_0201`, `historical_gold_status: differs_on_this_engine_version`.
  The historical gold was not edited or removed.

## 5. Independent B1 — Sleep Level 1 → Wake (`v7-sleep-v4-20261110T153750Z`)

Native SafeTensors load, same workers, Store already destroyed (`own_store_pool_bytes_during_baseline: 0`,
so Store-plus-backup overlap is zero by construction). `qualified: true`, qualifier exit 0.

| measurement | value |
|---|---|
| model scope | DeepSeek-V4-Pro only; not generalised to Kimi/GLM |
| native load | 8 ranks, 117.3–119.3 s per rank |
| sleep | 42.86 s (`mode='wait'`), 75.1 s observation hold |
| wake | 3.32 s |
| CPU weight backup | 880 921 280 512 B, per-rank `cpu_backup_bytes` equal to tagged weight bytes |
| KV during sleep | 234 277 044 224 B tagged, `cpu_backup_bytes = 0` (discarded, not saved) |
| GPU per device | 138 471 MiB → 4 535 MiB while asleep; 132 486 MiB released per GPU (≥ 90 % rule) |
| host MemAvailable | 996.0 GiB during sleep, 1 878.6 GiB after shutdown |
| worker identity | identical PIDs and start ticks across before/sleep/wake/after |
| requests | all historical golds and the added question pass both before sleep and after wake |
| final release | 74.5 s, every GPU 0 MiB, container GPU request unchanged |
| scheduling | `other_model_schedulability: not tested` — a GPU memory drop is not a Ray quota |

## 6. Retained failures (original ids, no overwrite)

| id | outcome |
|---|---|
| `v7-store-20261110T044424Z` | 00-fixed-kimi failed on the earlier controller |
| `v7-store-20261110T051223Z` | coverage parser returned an empty Counter |
| `v7-store-20261110T054033Z` | 01-fixed-glm rejected the historical 0.19.1 raw payload |
| `v7-glm-rebuild-20261110T062606Z` | killed at 85 % of the rank save; host never OOM, container cgroup limit 1280 GiB |
| `v7-glm-rebuild-20261110T113029Z` | guard rejected a 2700 s lease (store-conversion caps at 1200 s) |
| `v7-glm-rebuild-20261110T113709Z` | saves and shutdown completed; in-container hash pass cut by the lease; verified offline |
| `v7-store-20261110T122351Z` | 00 passed; 01 failed because the backend config carried no loader identity |
| `v7-store-20261110T125209Z` | 00/01 ran; fixed32 differed from the 0.19.1 gold, which led to the native reference |
| `v7-native-glm-20261110T133356Z` | harness `NameError: record_only` |
| `v7-sleep-v4-20261110T145540Z` | `_CUuuid` has no `removeprefix` |
| `v7-sleep-v4-20261110T150731Z` | container removed before the guard's final inspect; guard exit 1 |
| `v7-sleep-v4-20261110T152353Z` | worker-exit sampled once and hit a shutdown race |

Each keeps its own `runs/<id>/` directory with protocol, container, guard, observer, release and
failure records.

## 7. Not covered

Kimi/GLM under Sleep (only V4 was measured); B2 Sleep L2 and explicit reload (not run, not
required by V7); Ray-level schedulability of another model after sleep; throughput, concurrency,
MTP/NextN, vision; physical SSD-cold reads. The Store pool is fixed-pool resident — its RSS is
not an OS release measurement.

## 8. Where the evidence lives

`/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7/runs/v7-store-20261110T140421Z/`,
`…/v7-glm-rebuild-20261110T113709Z/`, `…/v7-native-glm-20261110T135319Z/`,
`…/v7-sleep-v4-20261110T153750Z/`, plus `v7/glm-store-verification.json`,
`v7/native-reference-comparison.json`, `v7/protocol.json`, `v7/sleep-protocol-v4.json`,
`v7/events-sleep-*.jsonl`. Source: `vllm_glm20_loader.py`, `vllm_kimi20_loader.py`,
`v7_store_sequence.py`, `v7_backend.py`, `v7_sleep_once.py`, `v7_sleep_worker.py`,
`v7_qualify_sleep.py`, `v7_glm_convert*.py`, `v7_glm_verify_store.py`, `v7_native_ref.py`.

## 9. Second-node replication of B1 on qjhs000 — `v7-sleep-v4-qjhs000-20261111T010645Z`

Run 2026-11-11 09:06–09:31 +08 on `h20/qjhs000` (8×H20-3e, its own GPU UUID set), with a
separate frozen protocol and admission so nothing on qjhs003 was rewritten.

| item | qjhs000 replication | qjhs003 primary |
|---|---|---|
| qualified | **true** (guard 0, observer 0, no cleanup errors) | true |
| protocol | `sleep-protocol-v4-qjhs000.json` sha256 `9c08fd10…` | `sleep-protocol-v4.json` sha256 `80874a0b…` |
| admission | `user-20261111T010645Z`, `h20/qjhs000`, no booking, 63.9 s idle evidence | `user-20261110T153750Z` |
| image / checkpoint | same image digest; V4 config sha256 `5fe4568d…` (identical on both nodes) | same |
| sleep / wake | 289.88 s / 7.74 s | 42.86 s / 3.32 s |
| CPU weight backup | 880 921 280 512 B | 880 921 280 512 B |
| KV backup | 0 B (234 GB discarded) | 0 B |
| released per GPU | 132 486 MiB × 8 | 132 486 MiB × 8 |
| observation / final release | 72.9 s / 73.1 s | 72.9 s / 74.5 s |
| native load per rank | 213.8–214.1 s | 117.3–119.3 s |
| requests | historical golds matched before and after wake; added question `42` | same |

The timing difference is recorded as measured, not explained: sleep, wake and native load are
2.4–6.8× slower on qjhs000, whose checkpoint is served from a 97 %-full RAID0 (`/dev/md0`) and
whose host runs dozens of other containers. The acceptance gates and the mechanism are the same.

Scope limits: this is a replication of the independent baseline only. The mainline A0/A1/A2 was
**not** replicated on qjhs000: `/raid` had 472 GB free while the three Store payloads need
2.10 TiB (Kimi 575 GiB + GLM 762.5 GB + V4 818 GiB); a single payload already exceeds the free
space, so no Store sequence — not even a one-model one — can be staged there as-is.

## 10. Re-run of the fixed chain on the current tree — `v7-store-20261111T014941Z`

2026-11-11 09:49–10:40 +08, qjhs003, `rerun_of: v7-store-20261110T140421Z`, protocol sha256
`7525df12…` (previous `66ebda7f…`), admission `user-20261111T014941Z`, 63.1 s idle evidence.
Reason recorded in the protocol: the controller now takes `store_root` from the protocol
(default `/data`), and that edit had to be validated end to end rather than only by CPU tests.

| step | model | Store cache | read bytes | hit bytes | alloc / freed | evicted | confirms | activation → correct |
|---|---|---|---|---|---|---|---|---|
| 00 | Kimi | full_miss | 617 000 763 392 | 0 | 18 392 / 0 | 0 | 8 | 257.1 s |
| 01 | GLM | full_miss | 762 527 481 856 | 0 | 22 728 / 9 196 | 4 | 8 | 519.7 s |
| 02 | V4 | full_miss | 877 729 513 472 | 0 | 26 552 / 26 242 | 10 | 496 | 239.3 s |
| 03 | Kimi | full_miss | 617 000 763 392 | 0 | 18 392 / 17 903 | 217 | 8 | 162.6 s |
| 04 | Kimi | full_hit | 0 | 617 000 763 392 | 0 / 0 | 0 | 8 | 79.4 s |

`fixed_chain_passed: true`, six guards 0, final release 73.7 s with every GPU at 0 MiB,
observer exit 0. One Store process (pid 1869936) served all five activations.

The byte-level cache behaviour is identical to the 2026-11-10 run — same payload reads, same
block counts, same victim path counts, same pool hit — so the storage-root parameterization
changed nothing for the `/data` configuration. Both runs and the two Sleep baselines are
covered by `v7/acceptance-integrity.json` (`all_checks_pass: true`); the 2026-11-10 evidence is
unchanged and remains the primary record.
