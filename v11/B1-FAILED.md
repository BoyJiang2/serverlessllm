# B1 — 未通过（结论与完整证据链）

**结论**：在固定镜像（vLLM 0.20.1 @132765e）上，B1（SLLM Store + GPU KV L1 + LMCache MP Host KV 层）
无法跑通。KV 注册握手失败，engine 起不来，因此 **0 次激活可记录**，没有任何 B1 数字进入对比。

## 直接根因（原生日志）

客户端注册请求到达 server，但 server 拒绝：

```
ERROR: Error handling operation register_kv_cache  (mq.py:798)
  File ".../lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py", line 508, in register_kv_cache
    self._ctx.layout_desc_registry.register(
  File ".../lmcache/v1/multiprocess/engine_context.py", line 88, in register
    attn_desc = replace(attn_desc, world_size=world_size)
  File ".../lmcache/v1/distributed/api.py", line 386, in __post_init__
ValueError: AttnWindowDesc: world_size must be >= 1, got 0
```

server 版本与客户端同为 0.5.6（overlay）；`world_size` 来自客户端的
`ParallelStrategy.kv_world_size = vllm_world_size // n_servers`，本应等于 TP8，实际为 0。

## 六次尝试与逐层排除

| run | 现象 | 根因 | 处理/结论 |
|---|---|---|---|
| `…T092923Z` | 作废 | 遗留探针容器占 9100/5555，KV 写进外来 server（2 GiB tier） | 端口预检 + 校验 tier 大小 + 容器 Running；该 run 标 `INVALID.md` |
| `…T094324Z` | register 超时 | server 容器无 GPU 设备，无法接收 CUDA IPC handle | server 容器加 `--gpus`/`NVIDIA_VISIBLE_DEVICES` |
| `…T100307Z` | register 超时 | server 端 `ModuleNotFoundError: cupy` | 装 `cupy-cuda13x==14.2.0` |
| `…T102728Z` | register 拒绝 | `world_size=0`（默认 `kv_parallel_size=1`） | 判定为客户端并行几何未注入 |
| `…T132035Z` | register 拒绝 | 仍 `world_size=0`，加了 `kv_parallel_size=8` 与显式 `server_urls` 也无效 | 升级/配置均无法绕过 |
| 版本对照 | — | 0.5.7rc4（独立 overlay `lmcache057-site`）**保留同一校验** | 换版本不能解决 |

## 已确认可用的部分（预验收第 1 部分）

`b1-preacceptance-part1.json`：

- `GET /status` → 200，`is_healthy: true`，`memory_used_bytes` 可读；
- `POST /cache/clear {"tier":"l1"}` → `{"status":"ok","cleared":{"tier":"l1"}}`；
- `POST /cache/clear {"tier":"l2"}` → 400（符合"只做两层"）；
- `num_l2_adapters: 0`，`eviction_policy: LRU`，tier 16 GiB 被控制器校验通过。

也就是说 Host 层本身可控、可清理、可观测，唯一缺口是 **KV 注册握手**。

## 复现命令

```bash
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11
python3 v11_run.py --group B1 --models K --rep 1   # 20 分钟内复现同样的 ValueError
```

## 说明

按协议"未跑通即记未通过"，B1 不参与 A vs B1 的数值对比；对比章节只保留 A 与 B2-L1 的实测数据，
并明确写出 B1 的失败原因与本文件路径，避免读者把"没跑"误读成"跑输了"。

## 2026-11-12 补充：客户端并行几何是错的（新增原生证据）

给 LMCache 客户端打了**只加日志**的补丁（llm_multi_process_adapter.py 的 world_size 属性），
在 B1-B1-smoke-r1-20261112T013808Z 里抓到 worker 实际收到的几何：

`
v11-debug-worker strategy=ParallelStrategy(
    mla_only=True, vllm_world_size=1, vllm_worker_id=0,
    tp_size=8, pp_size=3, n_servers=8, dcp_size=1) kv_world_size=0
`

对照正确值应为 llm_world_size=8, n_servers=1, pp_size=1，于是
kv_world_size = vllm_world_size // n_servers = 1 // 8 = 0 —— 正是 server 拒绝的 world_size=0。
pp_size 在三个 rank 上分别是 3/1/4（同一引擎不可能），说明这是
**vLLM 0.20.1 的 connector 与 LMCache 0.5.6 的 adapter 初始化参数形状错位**（位置参数被当成别的字段），
不是配置项能修好的问题。

结论不变：B1 记**未通过**；若要修，需要改 vLLM 侧 connector 或 LMCache 侧 adapter 的入参对齐，
那会引入一个"打过补丁的 LMCache"，不再是冻结基线，必须在报告里单独标注。
## 2026-11-12（第二轮）更新：0.5.5 修好了握手，B1 的卡点换成 V4 的显存滞留

上一节（`world_size=0`）的结论**已被 LMCache 0.5.5 推翻**：0.5.5 自带的
`lmcache/integration/vllm/lmcache_mp_connector_0201.py`（PR #4135）把并行几何对上了，
B1 现在能真正激活模型并给出正确响应。上面那段保留，是为了留下"当时为什么判定不可修"的记录。

### 1. 激活已经成立，但"清空前 Host 字节"不是验收证据

`B1-B1-smoke-r1-20261112T013808Z` 里 Kimi 与 GLM 都到达 engine ready 且金标通过；Kimi 的激活窗口内
Host 层观测到 35,979,264 B / 2 objects。而 GLM、V4 的 `kv-clear.json.before` 读到 **0**。

原因在读取时机：clear 发生在 backend 退出并排空显存**之后**（GLM 245 s、V4 240–725 s），
那时 Host 层可能已经空了，而模型存活期内其实写过。因此验收口径改为：

1. 激活窗口内 1 Hz 采样峰值 `kv_peak_bytes` > 0（序列落在 `kv-write-window.json`）；
2. 写入时刻早于 clear 时刻（`kv_write_before_clear`）；
3. clear 返回 200 且记账归零（`kv_clear_ok`）。

`qualified.passed` 现在对 B1 额外要求每一段 `b1_host_kv.steps[].closed` 为真——
"7 段都跑完"不再等于 B1 闭环。

### 2. V4 退出后 30,607 MiB/卡 不归还（本组真正的卡点）

| 退出的模型 | 退出后残留（MiB/卡） | 等待 | 8/8 rank 都调了 unregister |
|---|---|---|---|
| Kimi | 2,875 | 245 s 回到驱动基线 | 是 |
| GLM | 4,869 | 245 s | 是 |
| **V4** | **30,607** | 240 s、725 s 均不动 | 是 |

也就是说这不是"没注销"，而是注销之后显存仍不归还，且只发生在 V4 上（V4 是 hybrid
KV：`DEEPSEEK_SPARSE_SWA`，block 256）。`POST /cache/clear` 之后依然滞留，紧随其后的 GLM 因此在
`ValueError: Free memory on device cuda:0 (108.16/139.8 GiB) ... desired GPU memory utilization (0.9, 125.82 GiB)`
处失败。

### 3. 把 util 降到 0.75 会直接杀死 V4

`ValueError: To serve at least one request ... (0.91 GiB KV cache is needed, which is larger than the available KV cache memory (0.68 GiB)`：
V4 每卡权重 102.2 GiB，0.75 × 143,771 MiB 的配额只剩 0.68 GiB 做 KV，比单条 max_model_len=2048
序列需要的 0.91 GiB 还小。所以 B1 不能对三家共用一个 util，现在按模型取：
K 0.90 / G 0.90 / **D 0.76**（0.76 下 V4 仍有约 2.5 GiB KV，是最低需求的 2.4 倍；
A 组仍统一 0.90，偏差写在 `qualification.gpu_memory_utilization_deviation`）。

### 4. 已加进流程的两道闸

- **`preflight.py`**（开跑前，控制器强制调用，写 `preflight.json`）：从历史 backend.log 重新抽取
  三家 block size（K 16 / G 64 / D 256）并断言 `256 % block == 0` 且三者 `lcm == 256`；
  再查 `kv_fill` 几何（512 = 2×256）、每卡"权重 + 最小 KV ≤ util×HBM"、宿主 RAM 预算
  （1024 GiB 池 + 16 GiB tier + 200 GiB 余量）、端口、遗留容器、显存基线。
  全部在花 12 分钟加载之前判掉。
- **`preflight.vram_gate`**（每段创建容器前）：每卡 `free ≥ util×HBM + 1024 MiB`，最多重试 120 s；
  失败则在 `<step>/vram-gate-fail/` 落 `nvidia-smi`、compute PID 的 `/proc/<pid>/cmdline` 与 RSS、
  `docker ps`、`meminfo`，并写 `vram-gate.json`。失败不再只剩一个 traceback。

### 5. 当前状态

**仍未通过。** 下一步用 `--models DG` 单独验证"V4 之后能否起 GLM"——这是 7 段序列里唯一
未知的边界；在它通过之前，不宣称 B1 闭环，也不把 B1 纳入 A/B1 数值对比。

### 2026-11-12 20:30 最终结果：7/7 激活，3/7 闭环

`B1-seq-7-agents-r1-20261112T110659Z`（`K → G → D → G → K → D → K`，预检全过）：

- **7/7 段激活成功**，全部 `exit=0`、金标全过、每段显存门槛一次通过（`free/need` 见各段 `vram-gate.json`）；
- 收尾 `release=59.635 s`，8 卡全零；
- **Host KV 闭环 3/7**：`00-K`、`04-K`、`06-K` 各 35,979,264 B，clear 前 35,979,264 B → clear 后 0（`clear=200`）；
- **4 段未闭环**：`01-G`、`03-G`、`02-D`、`05-D`，激活窗口峰值 0，clear 前后都是 0。

`qualification.json` 的分项因此是：

```
complete=true  no_failure=true  exit_zero=true  requests=true  b1_host_kv_closed=false
→ passed=false
```

即"模型全部答对"与"KV 层闭环"已经彻底分开，`passed` 不再被前四项掩盖。

未闭环的直接原因（每段只有一次 store 尝试，全部在 server 端失败）：

| 时刻 | 段 | 结果 |
|---|---|---|
| 11:19:27 | 00-K | clear `objects=2`（成功） |
| 11:29:02 | 01-G | `ValueError: zip() argument 2 is longer than argument 1` |
| 11:38:52 | 02-D | 同上 |
| 11:48:30 | 03-G | 同上 |
| 11:53:58 | 04-K | clear `objects=2`（成功） |
| 12:03:38 | 05-D | 同上 |

栈位置固定为
`lmcache_driven_transfer.py:1124 in store` → `zip(gpu_block_ids, blocks_per_chunk, strict=True)`。

**根因**：vLLM 0.20.1 在设置 `--kv-transfer-config` 时关闭 hybrid KV cache manager，
hybrid 模型（GLM 的 `DEEPSEEK_V32_INDEXER`、V4 的 `DEEPSEEK_SPARSE_SWA`）被折叠成
单张 64-token block 表，connector 只发 1 组 block id；LMCache 侧按注册张量分出
7 个 kernel group（`tokens_per_block` = 256 / 2 / 256 / 64 / 256 / 64 / 256），
`store()` 的 `strict=True` 立马抛错并按设计 fail-closed 丢弃整笔写入。
即使只放宽 zip 也不够：`tokens_per_block=2` 的组一个 256-token chunk 需要 128 个
block id，而引擎一张 64-token 表只能给出 4 个，仍会被下一道 underflow 守卫拒绝。
**信息在 vLLM 侧就已经丢失，改 LMCache 补不回来。**

尝试过并排除的替代路径：`--supported-transfer-mode engine_driven`
（`B1-B1-smoke-r1-20261112T101536Z`）——0.5.5 的 engine-driven 模式没有实现
`REGISTER_KV_CACHE`，worker 等满 300 s 后 engine init 失败。

结论：在该冻结栈（vLLM 0.20.1 + LMCache 0.5.5，无补丁）上，B1 的 Host KV 层
只对非 hybrid 模型（Kimi）成立；GLM / V4 的 4 段不可能闭环。B1 记 **未通过**，
但"激活"与"闭环"分开记录：**7/7 vs 3/7**。

## 旧 Kimi smoke 的验收边界

`B1-B1-smoke-r1-20261112T032145Z` 原运行器标记通过，覆盖 1/1 激活和正确响应。原始 `switch-events.csv` 记录 Host KV 清理前后均为 0、clear HTTP 200，且 `qualification.json` 无逐段 Host KV 写入或零命中验收字段，因此这轮不能证明 Host KV 闭环。后续三模型 7/7 闭环由 `B1-seq-7-agents-r1-20261112T144041Z` 独立提供。
