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