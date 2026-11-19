# 联合 B2 Sleep L2 容量前检

联合 B2 固定 qjhs003、8×H20 TP8、864 GiB 权重 Store 和 813 GiB LMCache Host KV 池。首模型通过 SafeTensors 冷页准入后加载，另两个模型首次从 Store 原位写入 CuMem 权重。后四段保留各自原 Worker，从原 checkpoint 唤醒，再重注册和回读同模型的 Host KV。这组设计独立于历史原生 Sleep L2。

| 已核对的资源 | 观测值 | 证据 |
| --- | ---: | --- |
| 双池就绪后可用 RAM | 128.332–130.406 GiB | 当前 B1 S1 前五段 `host-admission.json` |
| 历史原生 L2 最大主机 RAM 压力 | 128.711 GiB | 七个合格轮中，同轮启动至最低 `MemAvailable` 的最大变化 |
| 保留 50 GiB 时的双池后准入 | 179 GiB | 将历史最大变化与余量相加并向上取整 |
| qjhs003 物理 RAM | 1923.283 GiB | 节点 `/proc/meminfo` |

179 GiB 来自历史原生 L2 的整轮最大下降。当前已取得 Kimi meta 补丁和 GLM-5.3 的分阶段实测，正式 S1 采用开池与逐段两级准入。B1 同容量池证据提供开池预算，原生 L2 及单模型结果用于逐段估计。准入、正式结果与代理观测分别留证。

历史原生 L2 的 S1 第 5 轮在第三次休眠后，主机可用内存较开轮低约 59.622 GiB。分阶段准入须在实际操作前计入全部休眠 Worker，记录两池和三个 Worker 的 cgroup，与首次加载、休眠、唤醒端点对齐。采样程序已用 qjhs003 的在途单模型容器验证 cgroup 读取，联合模式的记录周期为 1 秒。

已用实机工件重生成 S1、S2、S3 计划，逐项核对冻结序列、Load/Wake 路径和两池容量。Kimi 固定挂载单模型实测重载更短、RAM 更低的 meta 计数补丁。TP 切片前移的候选计划和结果继续保留。V4 要求八 rank 实测 KV 映射状态。跨模型探针用同一前缀分别验证旧模型仍命中 512 token、当前模型命中 0。普通 KV 探针按请求 ID 对齐查询完成事件与实际拷贝。本次通过 8 项控制器测试和固定镜像中的 6 项 V4 CPU 测试。[CPU 前检工件和哈希](derived/joint-b2-preflight.json)

B1 S1 的 L1 状态回执报告 813 GiB 容量，LMCache cgroup 的匿名内存实测约 813.516 GiB。该轮 LMCache 的一次整池 CUDA pin 请求失败，日志报告 872,952,102,912 B。七段实际 KV 写入、清理和金标均通过。两池退出后，同机 `SUnreclaim` 从约 71 GiB 回落到约 4.7 GiB。现有普通用户采样无法确定各类 slab 的归属。[S1 LMCache 原始日志](raw/B1-GLM53-S1-20261119T141542Z/lmcache-final.log)

S1 先退出 Store，S2 先启动 LMCache。两条时间线都采到了 LMCache 匿名内存仍达到配置的 99%、Store 匿名内存小于等于 512 MiB 的间隔。将这些点与双池匿名内存各自达到配置 99% 的点比较，得到以下主机 `SUnreclaim` 中位数。

| 序列与方向 | LMCache 仍驻留（样本数） | 双池已驻留（样本数） | 差值 |
| --- | ---: | ---: | ---: |
| S1 退出 | 4.706 GiB（3） | 71.240 GiB（324） | 66.535 GiB |
| S2 启动 | 4.676 GiB（19） | 71.056 GiB（51） | 66.379 GiB |

增量随 Store 的建立和释放出现。样本为主机 slab 总量，包含同一时点的其他进程负载。S2 只取截至所列单调时钟端点的采样，正式 B1 验收仍以该轮最终回执为准。[采样范围、逐组统计与原始前缀 SHA-256](derived/store-memory-lifecycle.json)

S2 的同次 cgroup 读取记录，Store 匿名内存 **865.166 GiB** 中有 **24.182 GiB** 记为 `anon_thp`（2.80%）。LMCache 对应为 **813.438 GiB** 和 **659.623 GiB**（81.09%）。独立 CPU 小测用同节点每次四个 32 MiB 缓冲，原 `aligned_alloc(4096)` 在 CPU 首写后达到 120/128 MiB THP，2 MiB 对齐并提示 `MADV_HUGEPAGE` 的映射达到 128/128 MiB，两种模式各三次。下一步对照 CUDA 注册前后的 THP、slab 和实际传输，以判定是否能追回容量余量。CPU 小测只覆盖分配和首次写入。[S2 同次原始采样](raw/host-memory/B1-GLM53-S2-20261119T154121Z-thp-20261119T155646Z.jsonl) · [六份 CPU 原始回执](diagnostics/store-thp-cpu/cpu-results.jsonl)

单卡探针 `store-pin-20261119T194925Z` 的容器启动在 `runc create` 超时，实际 CUDA 注册和 32 MiB 搬运均缺测。保留初始启动错误、清理错误、原始释放和仅针对该容器的恢复回执。准入仍沿用上表。原始记录位于 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-three-model-20261119/store-thp-cpu.Rm2TbbLo/pin-v2/store-pin-20261119T194925Z/`。

修正后的单卡探针 `store-pin-20261119T202734Z` 通过了选定 UUID 的可见性检查。九次独立进程均注册四块 32 MiB 缓冲，完整拷贝一块并核对回读 SHA-256，八卡归零观察 61.024 秒。

| 方式 | 有效 n | 主机 `SUnreclaim` 增量中位数（KiB） | 范围（KiB） | 四次注册调用总耗时中位数（ms） |
| --- | ---: | ---: | ---: | ---: |
| 现有直接注册 | 3 | 2836 | 2772–2836 | 24.623 |
| 先触页 | 3 | 2780 | 2660–3176 | 4.152 |
| `MADV_HUGEPAGE` 后先触页 | 3 | 2776 | 2776–2836 | 4.167 |

主机增量从 CUDA 初始化后的基线测到第四块注册完成。注册调用耗时排除先触页。三组增量范围重叠，尚无依据将差异外推到 864 GiB。`smaps` 的 THP 字段涵盖与目标相交的整个 VMA，有些样本合计超过 128 MiB，因此保留原始值供读回。[单卡原始汇总](raw/store-pin-20261119T202734Z/result.json) · [逐次日志索引](derived/store-pin-evidence.json)

## Kimi 单模型 Sleep L2 对照

三组各 1 次，使用同一镜像、checkpoint、金标和 1 GiB Host KV 池。原八 rank 唤醒后均实际回读 512 token，三个探针的命中依次为 0、512、0 token，金标一致。重算还在每组核对了八 rank 的 60 个专家层完成事件。

| 配置与 run ID 后缀 | checkpoint 重载（秒） | 最后专家层完成（自重载起，秒） | 重载匿名内存峰值（GiB） | 重载不可回收估计峰值（GiB） | 八卡归零观察（秒） |
| --- | ---: | ---: | ---: | ---: | ---: |
| 基线 `20261119T131128Z` | 750.764 | 749.960 | 75.405 | 77.540 | 61.026 |
| TP 切片前移 `20261119T192513Z` | 520.032 | 519.467 | 68.579 | 70.687 | 61.039 |
| meta 计数 `20261119T195718Z` | 320.904 | 320.285 | 20.332 | 22.360 | 61.113 |

相对基线，meta 计数的 checkpoint 重载缩短 429.861 秒（57.3%），重载匿名内存峰值低 55.073 GiB。三组首次休眠的不可回收估计峰值为 22.067、22.104、22.105 GiB，均缺第二次休眠的驻留数据。两种补丁分别验收，联合候选选用 meta 计数。两个补丁组在各自 checkpoint 重载采样窗口内 `workingset_refault_file` 增量均为 0 页，基线无该计数。独立单模型数据用于评估补丁与容量，联合双池准入仍按实机水位判定。[重算结果](derived/kimi-l2.json) · [服务器证据索引](derived/kimi-l2-evidence.json)

## GLM-5.3 单模型 RAM 补测

`glm53-sleep-L2-memory-20261119T203024Z` 在 qjhs003 上使用 TP8、固定 16 GiB/rank GPU KV 和独立 1 GiB Host KV。原八 rank 的 indexer 21 层和 MLA 78 层各实际回读 512 token，三个探针命中依次为 0、512、0，金标逐 token 一致，八卡归零观察 61.163 秒。样本为单次。

| 采样区间 | 时间（秒） | 采样点 | 主机可用 RAM 最大采样降幅（GiB） | cgroup 匿名内存峰值（GiB） |
| --- | ---: | ---: | ---: | ---: |
| 八 rank 权重加载 | 477.28 | 477 | 5.47 | 17.04 |
| 完成休眠至发起唤醒 | 75.10 | 75 | 0.81 | 19.95 |
| checkpoint 重载 | 143.29 | 143 | 0.00 | 19.27 |
| 开始唤醒至重新注册 | 145.56 | 146 | 0.05 | 19.95 |

降幅以各区间前 10 秒样本的中位数为基线。容器启动前至首次休眠完成的最大采样降幅另计为 24.009 GiB。唤醒至重新注册的 `workingset_refault_file` 增量为 73,082,340 页，计数可包含同一文件页的重复 refault。[重算结果](derived/glm53-l2-memory.json) · [逐 rank H2D](raw/glm53-sleep-L2-memory-20261119T203024Z/glm53-sf-cold/after-wake-h2d.json) · [原始证据索引](derived/glm53-l2-memory-evidence.json)

## 正式 S1 准入与运行

开池前要求至少 1871 GiB 可用 RAM，沿用 B1 的 1750 GiB 双池预算。运行每段前读取包含所有休眠 Worker 在内的实际 `MemAvailable`，以 50 GiB 保留量、观测上界向上取整和 8 GiB 操作缓冲计算该段门槛。操作期间轮询读数低于 55 GiB 会进入清理。V4 `gpu_memory_utilization` 显式设为同节点 B1 验证过的 0.76，继续执行逐卡显存准入并保留 16 GiB/rank 固定 KV。[准入配置及各模式依据](derived/b2-phase-capacity.json) · [B1 双池阶段采样](derived/b1-host-capacity.json)

| 阶段最低可用 RAM（GiB） | Kimi-K2.6 | GLM-5.3 | DeepSeek-V4-Pro |
| --- | ---: | ---: | ---: |
| SafeTensors 首载 | 81 | 83 | 84 |
| Store 原位首载 | 129 | 83 | 83 |
| checkpoint 回访 | 59 | 61 | 67 |

S1 首次执行 `B2-GLM53-S1-20261119T205243Z` 在守护器校验截止时间时停止。LMCache 容器处于 `created`，执行 0/7 段，清理后八卡归零 61.069 秒。控制器改为取预约截止与守护器四小时上限的较早值。[首轮失败回执](raw/B2-GLM53-S1-20261119T205243Z/result.json) · [守护器原始日志](raw/B2-GLM53-S1-20261119T205243Z/lmcache-guard.log)

第二次 `B2-GLM53-S1-20261119T205548Z` 通过守护器校验，LMCache 因 FD 接收器的 `RLIMIT_NOFILE` 不足退出。该轮执行 0/7 段，独立清理后八卡归零 61.055 秒。LMCache 容器现在显式使用 `nofile=8192:8192`，控制器也检查服务是否在就绪前退出。[LMCache 原始日志](raw/B2-GLM53-S1-20261119T205548Z/external-cleanup/lmcache-final.log) · [清理回执](raw/B2-GLM53-S1-20261119T205548Z/external-cleanup/result.json)

第三次 `B2-GLM53-S1-20261119T210129Z` 完成 1/7 段。Kimi 从冷页启动并通过金标，Host KV 实写 34.312 MiB。GLM-5.3 八 rank 从 Store 读取的路径总量为 757,596,880,896 B，包含后处理的加载调用跨度为 125.566 秒。旧模型注销后，LMCache 查询返回 `no_gpu_context`，解锁 RPC 随后超时，GLM-5.3 本段尚未执行金标。原始回执保留两项 Worker 清理错误，八卡连续归零 60.927 秒。[独立复核](raw/B2-GLM53-S1-20261119T210129Z/verification.json) · [原始文件索引](raw/B2-GLM53-S1-20261119T210129Z/evidence-index.json)

对照查询移至两个模型实际注册的窗口。先在旧模型注销前确认同一 512-token 前缀命中，再在新模型注册后验证该前缀零命中。检查切换前后的池内对象数和字节数。后续回访以原 Worker 八 rank 的实际 H2D 及金标验证保留的数据。[控制器变更](diagnostics/joint-b2-namespace-order.patch)

第四次 `B2-GLM53-S1-20261119T214730Z` 通过 2/7 段。修正后的 Kimi → GLM-5.3 查询为 512 → 0 token，期间均保留 35,979,264 B、2 个 Host KV 对象。V4 随后完成全部 496 条 Store 路径读取及八 rank 后处理，权重准备跨度 153.111 秒。KV 注册在 `model.layers.0.attn` 物理视图校验停止，V4 该段未执行金标。保留三项 Worker 清理错误和八卡连续归零 60.990 秒的采样。[独立复核](raw/B2-GLM53-S1-20261119T214730Z/verification.json) · [原始文件索引](raw/B2-GLM53-S1-20261119T214730Z/evidence-index.json)

V4 的历史注册日志和固定 vLLM 后端均使用 `(blocks, slots, width)` 三维物理 KV 视图。联合 B2 修正为严格校验这套形状、block 步幅和 dtype，沿用八组映射、地址签名和原 IPC 传输。旧错误回执未包含张量元组，修正后会在拒绝时输出实际与预期值。固定镜像中的 13 项 CPU 测试通过，包含真实八组历史几何、原消息编解码以及退出和再注册检查。[历史几何证据](diagnostics/v4-historical-kv-geometry.json) · [控制器和 V4 修正](diagnostics/joint-b2-v4-view.patch)

S3 的 Kimi 首载从空 Store 原位恢复，前序 V4 为 SafeTensors 首载。该段采用暂定 87 GiB 准入，即 55 GiB 运行停止线、向上取整的 24 GiB 代理压力及 8 GiB 余量。代理参考 Kimi 历史加载阶段与同双池下 V4 的 23.412 GiB 实测下降。Kimi 原位首载发出前仍需读取 V4 休眠后的实际 RAM。S2 的 Kimi 前序有 V4 已驻留权重，需要单独核对驱逐压力。[仅 S3 使用的准入配置](derived/b2-phase-capacity-s3.json)

## 验收要求

- 各轮三次首次加载和四次原 Worker checkpoint 唤醒。固定每 rank 16 GiB GPU KV 配置，记录实际分配和显存准入。
- 对照相同模型休眠前与唤醒后的业务及冷、热 KV 探针。验证 Host 提交、实际 H2D、原 Worker 身份及模型级清理。用相同前缀向下一模型查询，验证零跨模型命中。
- GLM-5.3 的 21 层 indexer 与 78 层 MLA 分别验证八 rank。DeepSeek-V4-Pro 按八个物理传输组的有效滑窗检查。
- 每轮结束三组 Worker 和两服务均正常退出，八卡归零持续至少 60 秒。

## 原始证据路径

- B1 两池 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b1-runs/B1-GLM53-S1-20261119T141542Z/`。
- 历史最坏 L2 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117/runs/B2-WNA16-S1-r5-20261119T021015Z/resource-samples.jsonl`。
- 联合计划 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/cpu-finalplan-{S1,S2,S3}/b2-plan.json`。
