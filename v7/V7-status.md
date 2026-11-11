# V7 active execution scope

V7 supersedes the dual-cache proposal for new work. Old plans, code, raw data and failures remain unchanged. No CPU KV L2 / LMCache / cross-backend KV persistence / W-K-G scans will be implemented. GPU KV is backend-local runtime state. B2 is optional.

## Evidence and remaining gates

- Historical baseline: `../SHARED-STORE-RESULTS-AUDITED.md`, `../COMPLETION-AUDIT.md`; fixed report commit `6a8d44a99f3270b20fcc2408ab481efacadd40d3` read. Fixed five activations and 12 directed switches cover the historical mechanisms; 46 protocol passes include 44 strict gold matches. They are not unified-engine validation.
- Target: vLLM 0.20.1 @ 132765e3560659ff63ebd236203672e991b70e08; reuse existing V4 image (7d6a368950d1...), Torch 2.11.0+cu130. Historical Kimi/GLM versions remain separately labelled.
- Kimi: historical payload is postprocessed; Marlin repack/scale processing source compared with target. Restore must skip repack, reconstruct workspace and check dense quantization state, generated buffers, aliases and all eight ranks. The candidate preserves its 240 empty tensors per rank and canonical-key rules; real target-image CPU tests pass. Runtime compatibility remains unproven.
- GLM: historical payload is raw/pre-postprocess. New `sllm_glm51_raw_0201` candidate retains exact source identity checks, validates against the target constructor tensor schema and alias graph, uses the existing owned IPC restore, then runs target postprocessing once. The combined 25 CPU tests for both loaders, the backend/Sleep receipt gates and full-load trace hooks pass; all six Store/native-Sleep candidate EngineArgs/LoadConfig constructions pass in the real target image without CUDA initialization. This does not qualify payload compatibility or inference.
- V4: existing target image, grouped payload and loader remain unchanged; new sequence requires runtime proof under its own ID.

## Next bounded execution

1. Finish Kimi/GLM candidate checks using target source and immutable metadata, no payload conversion unless a real layout gap requires it.
2. Freeze the new per-model identities/configs and existing golds plus the exact added question `请只输出数字：17 + 25 = ?` (42); no historical gold replacement.
3. A0-A2: fresh 1 TiB / 32 MiB Store and full TP8 backends, Kimi -> GLM -> V4 -> Kimi -> Kimi. Reuse native ledger/guard/observation machinery. Native lookup/read bytes, allocation/free/victims, all-rank receipts, responses and normal shutdown are required. Store-retained observation >=60 seconds between backends; destroy Store and observe full release >=60 seconds at end. Store-cold does not imply OS page-cache cold.
4. B1 independently: native SafeTensors full-model load, Sleep L1 -> Wake -> old golds and new question; retain the same worker identities, CPU backup / GPU / scheduling observations. Run after Store shutdown so Store-plus-backup overlap is zero by construction; report this limitation. Model coverage must be explicit. Sleep results remain separate from Store results.

## Current resource boundary

GPU use is authorized directly by the user; a reservation is not a launch prerequisite. The controller supports a fresh `user_authorized` admission record with a bounded execution window. Actual GPU occupancy, available host memory, the exclusive controller lock and source identity gates still apply. At 2026-11-10 12:41 +08, all eight GPUs were occupied by another user’s processes; no V7 model run had started. Do not interrupt those processes.

Completion requires actual unified three-model runtime plus A0/A1/A2 and B1, not CPU checks/imports. Current goal remains active.

Fresh five-step Store controller and independent Sleep backend runner are implemented candidates. The controller reuses the old ledger, exact-CID guard and release flow; it requires a fresh authorized admission receipt, a frozen protocol, zero initial GPU use and an exclusive V7 controller lock. Its five CPU cohort/parser checks pass. End-to-end GPU acceptance remains outstanding.


The next candidate adds the independent native V4 Sleep L1 runner, host observer, and evidence replay. It verifies the same eight workers, per-device physical memory changes, CPU weight backup, zero KV backup, native loader call spans, both sets of gold responses, retained container constraints during sleep, and a final zero-GPU window. The minimum physical release check is 90% of tagged allocation bytes per GPU. The Store controller compares inter-backend GPU memory with its measured empty-pool baseline plus 256 MiB per GPU.

On qjhs003, candidate-cpu-v8 passed 35 target-image CPU tests. The prior candidate-cpu-v7 discovery failure is retained. cpu-observer-v1 captured three valid samples from a real CPU-only cgroup and its container exited with code 0. The run directories contain no V7 GPU attempts.

Current server protocol drafts are `protocol-user-authorized-draft.json` and `sleep-protocol-v4-user-authorized-draft.json` under the existing V7 directory. Freeze each with the matching direct admission ID and a fresh `user_authorized` admission record before launch. First run the fixed five-step Store sequence. After the Store exits and final release passes, run the independent native V4 Sleep L1 baseline. The Sleep result applies to the V4 configuration only.


## 2026-11-10 19:16-19:40 +08 (V7 continuation round)

- GLM 0.20.1 Store rebuild attempt `v7-glm-rebuild-20261110T062606Z` ended with container exit 137 at 85% of the rank save. Host `MemAvailable` never fell below 1147 GiB and `OOMKilled=false`; the container cgroup limit was 1280 GiB while it held eight 94 GiB anonymous TP-shard copies, checkpoint page cache and dirty Store pages. The failed store directory `/data/brick3/boyjiang/glm5.1-fp8-store-v7-target-0201-20261110-v2` is retained.
- Export controls added to `vllm_glm20_loader.py`: 1600 GiB container limit, an eight-rank barrier before any read cache is released, `posix_fadvise(DONTNEED)` on the checkpoint `*.safetensors` files after native loading, and `fsync` + `DONTNEED` on each written `tensor.data_*`. `candidate-cpu-v10` runs the full target-image CPU cohort, 38 tests, OK, including three new export-memory tests; no GPU was used.
- `v7-glm-rebuild-20261110T113029Z` failed before creating a container: `sequence_guard.py` caps `store-conversion` leases at 1200 s and the controller asked for 2700 s. The lease is restored to 1180 s and the failed attempt is retained.
- Admission baseline amended and recorded in both protocols: after the previous owner engine exited, all eight GPUs kept 4 MiB with zero compute apps, so admission requires zero compute apps, unchanged UUID order and at most a 16 MiB driver baseline. Per-model release checks and the empty-Store idle window (baseline + 256 MiB tolerance) are unchanged.
- `v7_freeze_store_then_launch.py` and `v7_freeze_sleep_then_launch.py` are prepared: 60 s idle evidence, new `user_authorized` admission id, rebuilt GLM payload metadata (`path_sizes`, eight rank directories, `raw_state.json` digests), updated `glm-input.json` path, refreshed runtime hashes, superseded protocol hash retained, then launch.
- Node contention: `h20/qjhs003` is booked by boyjiang 19:00-21:00 for the separate Prism P8/P9 native baselines, and that session's V4 native run occupied all eight GPUs from 19:23. V7 does not interrupt it. `v7_glm_autolaunch.py` observes `nvidia-smi`, and only when the node is idle does it freeze a fresh admission and launch the conversion; the freeze step re-samples 60 s of idle evidence before any container is created.
- Still outstanding: the GLM v3 rebuild itself, the A0/A1/A2 five-step chain on the rebuilt payload, and B1. No V7 GPU attempt has consumed the rebuilt payload yet.

## 2026-11-10 19:16-19:40 +08 (V7 continuation round)

- GLM 0.20.1 Store rebuild attempt `v7-glm-rebuild-20261110T062606Z` ended with container exit 137 at 85% of the rank save. Host `MemAvailable` never fell below 1147 GiB and `OOMKilled=false`; the container cgroup limit was 1280 GiB while it held eight 94 GiB anonymous TP-shard copies, checkpoint page cache and dirty Store pages. The failed store directory `/data/brick3/boyjiang/glm5.1-fp8-store-v7-target-0201-20261110-v2` is retained.
- Export controls added to `vllm_glm20_loader.py`: 1600 GiB container limit, an eight-rank barrier before any read cache is released, `posix_fadvise(DONTNEED)` on the checkpoint `*.safetensors` files after native loading, and `fsync` + `DONTNEED` on each written `tensor.data_*`. `candidate-cpu-v10` runs the full target-image CPU cohort, 38 tests, OK, including three new export-memory tests; no GPU was used.
- `v7-glm-rebuild-20261110T113029Z` failed before creating a container: `sequence_guard.py` caps `store-conversion` leases at 1200 s and the controller asked for 2700 s. The lease is restored to 1180 s and the failed attempt is retained.
- Admission baseline amended and recorded in both protocols: after the previous owner engine exited, all eight GPUs kept 4 MiB with zero compute apps, so admission requires zero compute apps, unchanged UUID order and at most a 16 MiB driver baseline. Per-model release checks and the empty-Store idle window (baseline + 256 MiB tolerance) are unchanged.
- `v7_freeze_store_then_launch.py` and `v7_freeze_sleep_then_launch.py` are prepared: 60 s idle evidence, new `user_authorized` admission id, rebuilt GLM payload metadata (`path_sizes`, eight rank directories, `raw_state.json` digests), updated `glm-input.json` path, refreshed runtime hashes, superseded protocol hash retained, then launch.
- Node contention: `h20/qjhs003` is booked by boyjiang 19:00-21:00 for the separate Prism P8/P9 native baselines, and that session's V4 native run occupied all eight GPUs from 19:23. V7 does not interrupt it. `v7_glm_autolaunch.py` observes `nvidia-smi`, and only when the node is idle does it freeze a fresh admission and launch the conversion; the freeze step re-samples 60 s of idle evidence before any container is created.
- Still outstanding: the GLM v3 rebuild itself, the A0/A1/A2 five-step chain on the rebuilt payload, and B1. No V7 GPU attempt has consumed the rebuilt payload yet.

## 2026-11-10 23:55 +08 — V7 mainline and B1 completed

- The unified 0.20.1 fixed chain passed: `v7-store-20261110T140421Z`, one Store process (pid 655211), steps Kimi/GLM/V4/Kimi(full_miss)/Kimi(full_hit), 617 000 763 392 B cold read, 762 527 481 856 B GLM read with 9 196 blocks freed and 4 evicted paths, 877 729 513 472 B V4 read with 26 242 freed, 617 000 763 392 B re-read after 17 903 freed and 217 evicted, then a 617 000 763 392 B pool hit with zero reads. Final release 74.8 s, all GPUs zero.
- GLM 0.20.1 payload rebuilt in `glm5.1-fp8-store-v7-target-0201-20261110-v3` (8 ranks × 4 506 tensors, 762 527 481 856 B, `COMPLETE.json` sha256 `3cfbb04e…`), verified offline after the in-container hash pass was cut by the guard lease.
- `v7-native-glm-20261110T135319Z` native reference: identical token ids to the Store-restored `fixed32`; both differ from the preserved 0.19.1.dev1 gold at one token. The chain now accepts the historical gold or the recorded native reference and reports which matched.
- B1 qualified: `v7-sleep-v4-20261110T153750Z`, sleep 42.86 s, wake 3.32 s, 880 921 280 512 B CPU weight backup, KV 234 277 044 224 B discarded with zero backup, 132 486 MiB released per GPU, host available 996.0 GiB asleep and 1 878.6 GiB after shutdown, same worker PIDs across sleep/wake, all golds and the added question pass before and after.
- Full acceptance record: `V7-ACCEPTANCE.md` in this directory.

## 2026-11-11 09:35 +08 — B1 在第二台机器 qjhs000 上复现通过

- `v7-sleep-v4-qjhs000-20261111T010645Z`：`qualified: true`，guard/observer 均 0，无清理错误。使用独立协议 `sleep-protocol-v4-qjhs000.json`（sha `9c08fd10…`）与独立准入 `user-20261111T010645Z`（`h20/qjhs000`，当时无预约，63.9 s 空闲证据），qjhs003 的协议与准入未被改写。
- 数据：sleep 289.88 s、wake 7.74 s、CPU 权重备份 880,921,280,512 B、KV 备份 0、每卡释放 132,486 MiB、观测 72.9 s、最终释放 73.1 s；唤醒前后历史金标全部命中，功能题返回 42。
- 与 qjhs003（sleep 42.86 s / wake 3.32 s / 原生加载 117–119 s）相比，qjhs000 的 sleep、wake 和原生加载慢 2.4–6.8 倍（本地加载 213.8–214.1 s）；该项按实测记录，原因未在本轮定论（候选因素：`/dev/md0` RAID0 已 97% 满、宿主常驻数十个容器）。
- 主线 A0/A1/A2 未在 qjhs000 复现：`/raid` 仅剩 472 GB，而三模型 Store payload 需要 2.10 TiB（Kimi 575 GiB + GLM 762.5 GB + V4 818 GiB），单个 payload 已超过可用空间。GLM-5.3 的 Store payload 同量级（约 710–770 GiB），换模型不改变该结论。
- 结论不变：统一 0.20.1 主线与 B1 的验收均成立，qjhs003 为主记录，qjhs000 为独立基线复现。详见 `V7-ACCEPTANCE.md` §9。

## 2026-11-11 09:45 +08 — qjhs000 主线迁移前置：控制面已参数化，容量结论已固化

- `v7_store_sequence.py` 增加 `store_root`（默认 `/data`，向后兼容）：容器 `--root`、`STORAGE_PATH`、`/data` 挂载以及 `verify_legacy_metadata` 的相对路径基准全部改为读取该值。这样迁移到别的存储根不需要再改代码。
  注意：`v7-store-20261110T140421Z` 验收时协议里记录的 `v7_store_sequence.py` 哈希是改动前的版本；该 run 目录保存了自己的 `protocol.json` 副本，验收证据不受影响，但**用旧协议文件重跑会被哈希门禁拒绝**（这正是门禁的设计）。任何后续运行都应先重新冻结协议。
- 新增 `v7_migrate_qjhs000.py`：`--plan` 在数据所在节点（qjhs3）从已验收协议生成迁移计划，`--check` 在目标节点比对可用空间。产物：
  - `migration-plan-qjhs000.json`（sha `5e53336e…`）：按模型列出源根、payload 路径数、字节数、是否齐全；三模型源数据齐全。
  - `migration-capacity-qjhs000.json`：qjhs000 上实测 `/raid` 可用 `505,897,918,464 B`，需要 `2,324,975,491,481 B`（含 3% 余量），**缺口 `1,819,077,573,017 B`（≈1.65 TiB）**；连最小的单个 payload（Kimi 617,000,763,392 B）也放不下。
- 目标镜像内的 CPU 队列 `candidate-cpu-v11`：38 项全部通过（含 v7_store_sequence 覆盖测试），确认控制面改动没有破坏导入与既有覆盖逻辑。
- 结论：qjhs000 主线仍被存储容量硬性阻塞，需要外加 ≥1.7 TiB（全量三模型）或 ≥0.9 TiB（Kimi+V4 两模型，属缩减版验收）才能开始；在此之前不再重复尝试。

## 2026-11-11 09:42 +08 — 验收证据复核 + 一处比较口径修正

- 新增 `verify_acceptance.py` 并落盘 `acceptance-integrity.json`：18 项布尔检查全部为 true，5 步明细与验收表一致（00 Kimi 617,000,763,392 B 冷读、01 GLM 762,527,481,856 B + 9,196 块释放 + 4 条驱逐、02 V4 877,729,513,472 B + 26,242 块释放、03 Kimi 重读 617,000,763,392 B + 17,903 块释放 + 217 条驱逐、04 Kimi 纯命中 617,000,763,392 B 且零读取）；保留的失败 run 共 16 个。
- 修正 `native-reference-comparison.json` 的口径：原 `all_tokens_equal=False` 把“测量 run 未执行到的 added question”也计入了比较。现改为只对两条路径都产生的请求比较，结果 `all_shared_requests_equal=True`（business_arithmetic / business_translation / fixed32 三个请求 token 级一致）；added question 单独引用通过链中的结果（Store 路径 42、原生路径 42）。结论不变，只是去掉了会误导的布尔值。
- qjhs000 容量再次实测未变：`/raid` 可用 505,897,918,464 B，所需 2,324,975,491,481 B，缺口 1,819,077,573,017 B；主线仍无法在该节点开始，需外加存储或缩减版授权。

## 2026-11-11 10:45 +08 — 在 qjhs003 上按当前代码重跑固定五步链并通过

- 目标改为 qjhs003 后，用当前工作树重新冻结协议并重跑：`v7-store-20261111T014941Z`，`fixed_chain_passed: true`，6 个 guard 全 0，最终释放 73.7 s、8 卡全 0、observer 0；单 Store 进程 pid 1869936 贯穿五次激活。
- 字节级行为与 10-10 的验收 run 完全一致：00 Kimi 617,000,763,392 B 冷读、01 GLM 762,527,481,856 B（9,196 块释放 / 4 条驱逐）、02 V4 877,729,513,472 B（26,242 块释放 / 10 条驱逐 / 496 确认）、03 Kimi 重读 617,000,763,392 B（17,903 块释放 / 217 条驱逐）、04 Kimi 纯命中 617,000,763,392 B 且零读取。
- 该 run 的用途是验证控制面改动（`store_root` 参数，默认 `/data`）端到端可用，并作为第二个完整链数据点；`rerun_of` 已在协议中记录，10-10 的验收证据未被改动。
- `acceptance-integrity.json` 已扩展为同时校验两次链运行与两次 Sleep 基线：17 项检查全为 true（`all_checks_pass True`）。协议前后哈希：`66ebda7f…` → `7525df12…`。
