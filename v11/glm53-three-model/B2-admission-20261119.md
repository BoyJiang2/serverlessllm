# 联合 B2 Sleep L2 容量前检

联合 B2 固定 qjhs003、8×H20 TP8、864 GiB 权重 Store 和 813 GiB LMCache Host KV 池。首模型通过 SafeTensors 冷页准入后加载，另两个模型首次从 Store 原位写入 CuMem 权重。后四段保留各自原 Worker，从原 checkpoint 唤醒，再重注册和回读同模型的 Host KV。这组设计独立于历史原生 Sleep L2。

| 已核对的资源 | 观测值 | 证据 |
| --- | ---: | --- |
| 双池就绪后可用 RAM | 128.332–130.406 GiB | 当前 B1 S1 前五段 `host-admission.json` |
| 历史原生 L2 最大主机 RAM 压力 | 128.711 GiB | 七个合格轮中，同轮启动至最低 `MemAvailable` 的最大变化 |
| 保留 50 GiB 时的双池后准入 | 179 GiB | 将历史最大变化与余量相加并向上取整 |
| qjhs003 物理 RAM | 1923.283 GiB | 节点 `/proc/meminfo` |

两池实际就绪后的余量尚不足以按现有保守门槛启动联合 B2。历史原生 L2 和当前 B1 的压力来自各自独立运行。联合 B2 已完成三条序列的 CPU 启动计划验证，尚无联合运行的 OOM 或同 Worker 回读结果。GLM-5.3 单模型此前已通过两组 Host KV 回读，尚需带分阶段 cgroup 采样复跑。

历史原生 L2 的 S1 第 5 轮在第三次休眠后，主机可用内存较开轮低约 59.622 GiB。分阶段准入须在实际操作前计入全部休眠 Worker，记录两池和三个 Worker 的 cgroup，与首次加载、休眠、唤醒端点对齐。采样程序已用 qjhs003 的在途单模型容器验证 cgroup 读取，联合模式的记录周期为 1 秒。

B1 S1 的 L1 状态回执报告 813 GiB 容量，LMCache cgroup 的匿名内存实测约 813.516 GiB。该轮 LMCache 的一次整池 CUDA pin 请求失败，日志报告 872,952,102,912 B。七段实际 KV 写入、清理和金标均通过。两池退出后，同机 `SUnreclaim` 从约 71 GiB 回落到约 4.7 GiB。现有普通用户采样无法确定各类 slab 的归属。[S1 LMCache 原始日志](raw/B1-GLM53-S1-20261119T141542Z/lmcache-final.log)

S1 先退出 Store，S2 先启动 LMCache。两条时间线都采到了 LMCache 匿名内存仍达到配置的 99%、Store 匿名内存小于等于 512 MiB 的间隔。将这些点与双池匿名内存各自达到配置 99% 的点比较，得到以下主机 `SUnreclaim` 中位数。

| 序列与方向 | LMCache 仍驻留（样本数） | 双池已驻留（样本数） | 差值 |
| --- | ---: | ---: | ---: |
| S1 退出 | 4.706 GiB（3） | 71.240 GiB（324） | 66.535 GiB |
| S2 启动 | 4.676 GiB（19） | 71.056 GiB（51） | 66.379 GiB |

增量随 Store 的建立和释放出现。样本为主机 slab 总量，包含同一时点的其他进程负载。S2 只取截至所列单调时钟端点的采样，正式 B1 验收仍以该轮最终回执为准。[采样范围、逐组统计与原始前缀 SHA-256](derived/store-memory-lifecycle.json)

S2 的同次 cgroup 读取记录，Store 匿名内存 **865.166 GiB** 中有 **24.182 GiB** 记为 `anon_thp`（2.80%）。LMCache 对应为 **813.438 GiB** 和 **659.623 GiB**（81.09%）。独立 CPU 小测用同节点每次四个 32 MiB 缓冲，原 `aligned_alloc(4096)` 在 CPU 首写后达到 120/128 MiB THP，2 MiB 对齐并提示 `MADV_HUGEPAGE` 的映射达到 128/128 MiB，两种模式各三次。下一步对照 CUDA 注册前后的 THP、slab 和实际传输，以判定是否能追回容量余量。CPU 小测只覆盖分配和首次写入。[S2 同次原始采样](raw/host-memory/B1-GLM53-S2-20261119T154121Z-thp-20261119T155646Z.jsonl) · [六份 CPU 原始回执](diagnostics/store-thp-cpu/cpu-results.jsonl)

单卡探针 `store-pin-20261119T194925Z` 的容器启动在 `runc create` 超时，实际 CUDA 注册和 32 MiB 搬运均缺测。保留初始启动错误、清理错误、原始释放和仅针对该容器的恢复回执。准入仍沿用上表。原始记录位于 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-three-model-20261119/store-thp-cpu.Rm2TbbLo/pin-v2/store-pin-20261119T194925Z/`。

## Kimi 单模型 Sleep L2 对照

两组各 1 次，使用同一镜像、checkpoint、金标和 1 GiB Host KV 池。原八 rank 唤醒后均实际回读 512 token，三个探针的命中依次为 0、512、0 token，金标一致。重算还在每组核对了八 rank 的 60 个专家层完成事件。

| 配置与 run ID 后缀 | checkpoint 重载（秒） | 最后专家层完成（自重载起，秒） | 重载匿名内存峰值（GiB） | 重载不可回收估计峰值（GiB） | 八卡归零观察（秒） |
| --- | ---: | ---: | ---: | ---: | ---: |
| 基线 `20261119T131128Z` | 750.764 | 749.960 | 75.405 | 77.540 | 61.026 |
| TP 切片前移 `20261119T192513Z` | 520.032 | 519.467 | 68.579 | 70.687 | 61.039 |

本次配对观察到 checkpoint 重载缩短 230.732 秒（30.7%），重载匿名内存峰值低 6.826 GiB。首次休眠的不可回收估计峰值为 22.067 和 22.104 GiB，两个运行均没有第二次休眠的驻留数据。meta 计数候选的 GPU 对照仍待验收。独立单模型数据用于评估补丁与容量，联合双池准入仍按实机水位判定。[重算结果](derived/kimi-l2.json) · [服务器证据索引](derived/kimi-l2-evidence.json)

## 验收要求

- 各轮三次首次加载和四次原 Worker checkpoint 唤醒。固定每 rank 16 GiB GPU KV 配置，记录实际分配和显存准入。
- 对照相同模型休眠前与唤醒后的业务及冷、热 KV 探针。验证 Host 提交、实际 H2D、原 Worker 身份及模型级清理。用相同前缀向下一模型查询，验证零跨模型命中。
- GLM-5.3 的 21 层 indexer 与 78 层 MLA 分别验证八 rank。DeepSeek-V4-Pro 按八个物理传输组的有效滑窗检查。
- 每轮结束三组 Worker 和两服务均正常退出，八卡归零持续至少 60 秒。

## 原始证据路径

- B1 两池 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b1-runs/B1-GLM53-S1-20261119T141542Z/`。
- 历史最坏 L2 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117/runs/B2-WNA16-S1-r5-20261119T021015Z/resource-samples.jsonl`。
- 联合计划 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/cpu-finalplan-{S1,S2,S3}/b2-plan.json`。
