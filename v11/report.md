# V11 三模型三路径切换实验报告

本报告由 `make_report.py` 从各 run 目录的 `qualification.json` 与 `switch-events.csv` 生成，数字全部来自原生证据，未通过项照实列出。

| 组 | 状态 | 结论来源 |
|---|---|---|
| A | 通过（3/3 个 run 通过） | `runs/*/qualification.json` |
| B1 | 七段正式轮通过（7/7 段 Host KV 写入并清零） | `runs/*/qualification.json` |
| B2 | 部分通过（2/4 个 run 通过） | `runs/*/qualification.json` |

## A 组结果（SLLM Store）

### 序列汇总

| run | 序列 | 段数 | full_hit | partial_hit | full_miss | 平均切换(不含观察) | 最终释放 |
|---|---|---|---|---|---|---|---|
| `A-seq-10-1-r1-20261111T142600Z` | seq-10-1 | 11 | 2 | 0 | 9 | 389.1 s | 59.4 s |
| `A-seq-7-1-r1-20261111T081622Z` | seq-7-1 | 8 | 1 | 1 | 6 | 408.6 s | 59.7 s |
| `A-seq-8-1-r1-20261111T120359Z` | seq-8-1 | 9 | 2 | 0 | 7 | 347.8 s | 59.5 s |

说明：A 的"激活→正确"包含新进程从 Store 恢复权重（1 TiB 池、32 MiB 块）+ 8 卡引擎初始化 + 答题，所以即使 full_hit 也是百秒量级；命中只省掉磁盘读取，不省进程与引擎拉起。

campaign 覆盖（3 个通过的 A run 合计）：`D→G` 3、`D→K` 3、`G→D` 4、`G→K` 3、`K→D` 3、`K→G` 4；自切换 8 次单独统计 —— 最小非自方向计数 3。

### `A-seq-10-1-r1-20261111T142600Z` — 通过

序列 `seq-10-1`，11/11 段完成，protocol_hash `d6b76f45627bcdd5032ad4a579fa5abf032de44fb60d5b04eaeb202d6f59b5f7`（暂定），最终释放 59.4 s、8 卡全 0 = True。

| step | 迁移 | 缓存 | 读取 | 命中 | 块分配/释放 | 驱逐 | 激活→正确 (s) | 首 token (s) | 切换不含观察 (s) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | ∅→K | full_miss | 574.6 GiB | 0 | 18392/0 | 0 | 273.8 | 269.6 | — |
| 1 | K→K | full_hit | 0 | 574.6 GiB | 0/0 | 0 | 146.0 | 142.0 | 165.7 |
| 2 | K→G | full_miss | 710.2 GiB | 0 | 22728/9196 | 4 | 505.5 | 493.2 | 529.4 |
| 3 | G→D | full_miss | 817.4 GiB | 0 | 26552/26242 | 10 | 481.3 | 469.3 | 498.1 |
| 4 | D→K | full_miss | 574.6 GiB | 0 | 18392/17903 | 217 | 239.7 | 235.7 | 254.7 |
| 5 | K→K | full_hit | 0 | 574.6 GiB | 0/0 | 0 | 142.4 | 138.5 | 156.9 |
| 6 | K→D | full_miss | 817.4 GiB | 0 | 26552/28125 | 287 | 478.0 | 465.9 | 496.5 |
| 7 | D→G | full_miss | 710.2 GiB | 0 | 22728/21154 | 302 | 501.9 | 489.8 | 515.4 |
| 8 | G→K | full_miss | 574.6 GiB | 0 | 18392/18519 | 199 | 236.1 | 232.0 | 253.0 |
| 9 | K→G | full_miss | 710.2 GiB | 0 | 22728/23401 | 9 | 498.7 | 486.3 | 522.8 |
| 10 | G→D | full_miss | 817.4 GiB | 0 | 26552/26242 | 10 | 481.4 | 469.3 | 498.8 |

Store 进程: 1885780（同一进程贯穿全部激活）。

### `A-seq-7-1-r1-20261111T081622Z` — 通过

序列 `seq-7-1`，8/8 段完成，protocol_hash `d6b76f45627bcdd5032ad4a579fa5abf032de44fb60d5b04eaeb202d6f59b5f7`（暂定），最终释放 59.7 s、8 卡全 0 = True。

| step | 迁移 | 缓存 | 读取 | 命中 | 块分配/释放 | 驱逐 | 激活→正确 (s) | 首 token (s) | 切换不含观察 (s) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | ∅→K | full_miss | 574.6 GiB | 0 | 18392/0 | 0 | 229.7 | 225.6 | — |
| 1 | K→G | full_miss | 710.2 GiB | 0 | 22728/9196 | 4 | 495.5 | 483.3 | 510.6 |
| 2 | G→D | full_miss | 817.4 GiB | 0 | 26552/26242 | 10 | 474.4 | 462.3 | 489.0 |
| 3 | D→G | full_miss | 710.2 GiB | 0 | 22728/22238 | 302 | 489.7 | 477.4 | 502.0 |
| 4 | G→G | full_hit | 0 | 710.2 GiB | 0/0 | 0 | 382.5 | 370.5 | 396.9 |
| 5 | G→K | full_miss | 574.6 GiB | 0 | 18392/18519 | 199 | 227.1 | 223.2 | 241.7 |
| 6 | K→D | full_miss | 817.4 GiB | 0 | 26552/27999 | 11 | 475.9 | 463.9 | 490.4 |
| 7 | D→K | partial_hit | 502.8 GiB | 71.8 GiB | 16093/14520 | 216 | 217.7 | 213.9 | 229.9 |

Store 进程: 3543615（同一进程贯穿全部激活）。

### `A-seq-8-1-r1-20261111T120359Z` — 通过

序列 `seq-8-1`，9/9 段完成，protocol_hash `d6b76f45627bcdd5032ad4a579fa5abf032de44fb60d5b04eaeb202d6f59b5f7`（暂定），最终释放 59.5 s、8 卡全 0 = True。

| step | 迁移 | 缓存 | 读取 | 命中 | 块分配/释放 | 驱逐 | 激活→正确 (s) | 首 token (s) | 切换不含观察 (s) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | ∅→D | full_miss | 817.4 GiB | 0 | 26552/0 | 0 | 472.2 | 460.2 | — |
| 1 | D→G | full_miss | 710.2 GiB | 0 | 22728/16556 | 300 | 488.7 | 476.5 | 501.4 |
| 2 | G→D | full_miss | 817.4 GiB | 0 | 26552/27042 | 202 | 471.7 | 459.6 | 486.0 |
| 3 | D→K | full_miss | 574.6 GiB | 0 | 18392/17903 | 217 | 226.5 | 222.5 | 239.5 |
| 4 | K→G | full_miss | 710.2 GiB | 0 | 22728/23527 | 285 | 491.1 | 478.9 | 507.1 |
| 5 | G→K | full_miss | 574.6 GiB | 0 | 18392/17719 | 7 | 228.3 | 224.4 | 243.6 |
| 6 | K→K | full_hit | 0 | 574.6 GiB | 0/0 | 0 | 141.4 | 137.4 | 157.1 |
| 7 | K→K | full_hit | 0 | 574.6 GiB | 0/0 | 0 | 146.0 | 142.1 | 161.7 |
| 8 | K→D | full_miss | 817.4 GiB | 0 | 26552/27999 | 11 | 471.1 | 459.2 | 486.3 |

Store 进程: 889517（同一进程贯穿全部激活）。

## B1 组结果（Host KV 层）

**未通过。** 在固定镜像上，vLLM 0.20.1 的 `LMCacheMPConnector` 与 LMCache 0.5.6/0.5.7rc4
的 MP server 无法完成 `register_kv_caches` 握手：server 端 `LayoutDescRegistry.register`
收到 `world_size=0` 并拒绝（`ValueError: AttnWindowDesc: world_size must be >= 1, got 0`），
worker 等满 300 s 超时后 engine init 失败，因此没有任何一次激活可以记录。

逐次失败与修复记录：

| run | 现象 | 根因 | 处理 |
|---|---|---|---|
| `…T092923Z` | 作废 | 遗留探针占用 9100/5555，KV 被写进外来 server | 端口预检 + 校验 tier 大小 |
| `…T094324Z` | register 超时 | server 无 GPU 设备，无法接收 CUDA IPC handle | server 容器加 `--gpus` |
| `…T100307Z` | register 超时 | `ModuleNotFoundError: cupy`（`lmcache_driven_transfer.py:483`） | 装 `cupy-cuda13x==14.2.0` |
| `…T102728Z` | register 拒绝 | `AttnWindowDesc: world_size must be >= 1, got 0` | 未解决（协议错配） |

预验收第 1 部分（`b1-preacceptance-part1.json`）已通过：`GET /status` 200、`POST /cache/clear {"tier":"l1"}` 200、`tier=l2` 400、`num_l2_adapters=0`、`memory_used_bytes` 可直接读取。也就是说 Host 层本身可控，缺的是 KV 注册握手。

### `B1-seq-7-agents-r1-20261112T144041Z` — 通过（Host KV 闭环 7/7 段）

| step | 模型 | 读取 | Host 写入峰值 | 写入时刻 | clear 前 | clear 后 | clear 状态 | 闭环 |
|---|---|---|---|---|---|---|---|---|
| 0 | K | 574.6 GiB | 34.3 MiB | 激活后 269.2 s | 34.3 MiB | 0 | 200 | 是 |
| 1 | G | 710.2 GiB | 48.9 MiB | 激活后 509.8 s | 48.9 MiB | 0 | 200 | 是 |
| 2 | D | 817.4 GiB | 47.1 MiB | 激活后 504.4 s | 47.1 MiB | 0 | 200 | 是 |
| 3 | G | 621.4 GiB | 48.9 MiB | 激活后 493.5 s | 48.9 MiB | 0 | 200 | 是 |
| 4 | K | 574.6 GiB | 34.3 MiB | 激活后 246.2 s | 34.3 MiB | 0 | 200 | 是 |
| 5 | D | 817.4 GiB | 47.1 MiB | 激活后 511.8 s | 47.1 MiB | 0 | 200 | 是 |
| 6 | K | 574.6 GiB | 34.3 MiB | 激活后 237.9 s | 34.3 MiB | 0 | 200 | 是 |

正式轮使用 vLLM 0.20.1 @132765e 与带 HMA 补丁的 LMCache 0.5.5 @05a013b。V4 GPU memory utilization 为 0.76，Kimi 和 GLM 为 0.90。最终八卡显存归零，释放观测记录为 59.504 秒。

[验收回执](runs/B1-seq-7-agents-r1-20261112T144041Z/qualification.json) · [切换事件与 t1/t2](runs/B1-seq-7-agents-r1-20261112T144041Z/switch-events.csv) · [补丁及运行身份](runs/B1-seq-7-agents-r1-20261112T144041Z/lmcache-patch-identity.json) · [HMA 补丁](runs/B1-seq-7-agents-r1-20261112T144041Z/lmcache-hma.patch)

## B2 组结果（原生 Sleep，L1/L2 同组）

### `B2-B2-smoke-r1-20261111T110231Z` — 未通过

序列 `B2-smoke`，level [1]，最终释放 59.9 s、8 卡全 0 = True。

失败记录：

```
AssertionError('worker exited before ack')
```

### `B2-B2-smoke-r1-20261111T112107Z` — 通过

序列 `B2-smoke`，level [1]，最终释放 42.3 s、8 卡全 0 = False。

| level | step | 模型 | 阶段 | sleep (s) | 权重 CPU 备份 | 权重 | KV | Worker PID |
|---|---|---|---|---|---|---|---|---|
| 1 | 0 | D | load | 72.8 | 820.4 GiB | 820.4 GiB | 218.2 GiB | 813729 |
| 1 | 1 | D | wake | 3.6 | 820.4 GiB | 820.4 GiB | 218.2 GiB | 813729 |

level 1: PID 连续性 = True，金标 = True，PID {'D': [813729]}

### `B2-B2-smoke-r1-20261111T113723Z` — 通过

序列 `B2-smoke`，level [1]，最终释放 59.9 s、8 卡全 0 = True。

| level | step | 模型 | 阶段 | sleep (s) | 权重 CPU 备份 | 权重 | KV | Worker PID |
|---|---|---|---|---|---|---|---|---|
| 1 | 0 | D | load | 47.4 | 820.4 GiB | 820.4 GiB | 218.2 GiB | 840589 |
| 1 | 1 | D | wake | 4.1 | 820.4 GiB | 820.4 GiB | 218.2 GiB | 840589 |

level 1: PID 连续性 = True，金标 = True，PID {'D': [840589]}

### `B2-B2-smoke-r1-20261111T115102Z` — 未通过

序列 `B2-smoke`，level [2]，最终释放 59.7 s、8 卡全 0 = True。

失败记录：

```
AssertionError('worker exited before ack')
```

## 两个对比

### 对比一：A（Store 常驻） vs B1（Store + Host KV 层）

B1 已完成七段 Host KV 写入与清零验收。A 组同一固定序列尚无完成回执，因此暂不计算同序列的 A/B1 时延差。

### 对比二：A（Store 常驻） vs B2（原生 Sleep，L1 / L2 同组）

| 路径 | 同一模型再次可服务所需时间 | 证据 |
|---|---|---|
| A：Store 命中 | 191.7 s（5 次 full_hit 均值） | `runs/A-*/switch-events.csv` |
| A：Store 未命中（冷读/重读） | 398.1 s（23 次 full_miss/partial_hit 均值） | 同上 |
| B2-L1：Sleep → Wake | Sleep 32.0 s 均值，Wake 3.7 s 均值，唤醒后同一 PID 继续答题 | `runs/B2-*/L1/level-result.json` |
| B2-L2 | **未通过**：逐层 `reload_weights` 对 V4 无效（每层 Failed to load weights），随后 `wake_up(kv_cache)` CUDA OOM | `runs/B2-…T115102Z/DIAGNOSIS.md` |

**口径差异（必须一起读）**：A 的计时是"新进程要从 Store 恢复权重 + 引擎初始化 + 答题"，B2 的 sleep/wake 是"同一常驻进程的内存/显存搬移"，两者不是同一件事；报告把两边都列出来，但不把它们的差值包装成单一"加速比"。

### 容量与可行性边界（实测）

- Store 池 1 TiB：同时只放得下一份模型权重；换模型时必然发生真实驱逐（见 A 组驱逐列）。
- B2-L1：1.9 TiB 主机上只能容纳"一份权重备份 + 当前活动模型"；两份备份会把 `MemAvailable` 压到 0.3 GiB（实测），三模型 L1 campaign 在本机不可行，见 `B2-L1-RAM-BOUNDARY.md`。
- B2-L2：不占 CPU 备份，但重载路径在固定镜像上对 V4 无效（见上）。

