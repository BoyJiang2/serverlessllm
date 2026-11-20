# GLM-5.3 联合 B2 Sleep L2 交接

**截至 11 月 20 日，机器操作按用户指示暂停，当前只做本地整理。** 正式 B2 为 0/3 轮，B1 补跑为 0/6 轮。已验收的 B1 r1 三条序列均为 7/7 段。[B1 r1 原报告](REPORT.md)

当前正式候选为 `formal-nostore-813-reference/`，只启用 **813 GiB LMCache Host KV**，LMCache 容器上限 877 GiB，开池预算 880 GiB，开轮门槛 1,000 GiB。三份计划和容量检查已做 CPU 核验。恢复机器使用后，`start_joint_b2.ps1 -Sequence Sx -PreflightOnly` 核对当前哈希和预约。历史交接窗口 `bk-20261119T1450-de16ef1f` 的原回执随对应轮次保留。[当前前检](derived/b2-nostore-813-reference-preflight.json) · [准入适配](diagnostics/joint-b2-authorized-handoff.patch)

## 本地进度与恢复入口

| 轮次 | 配置 | 完成段数 | 停止点 | 八卡释放 | 本地证据 |
| --- | --- | ---: | --- | --- | --- |
| S1 `043421Z` | 384 GiB，独立先行 | 6/7 | 末段 Kimi 回读探针逐 token 分歧 | 61.134 s，Worker 退出异常 | [复算](derived/independent-384/B2-GLM53-S1-20261120T043421Z-recomputed.json) · [索引](derived/independent-384/B2-GLM53-S1-20261120T043421Z-index.json) |
| S1 `061704Z` | 813 GiB，原探针 | 4/7 | Kimi 回读匹配，删键后零命中重算出现分歧 | 61.303 s，Worker 清理异常 | [复算](derived/failed-813/B2-GLM53-S1-20261120T061704Z-recomputed.json) · [索引](derived/failed-813/B2-GLM53-S1-20261120T061704Z-index.json) |
| S1 `074802Z` | 813 GiB，参考探针 | 1/7 | Kimi 通过，独立 SGLang 进程触发 GLM-5.3 首载前显存门槛 | GPU2 持续占用，未通过 | [复算](derived/failed-813/B2-GLM53-S1-20261120T074802Z-recomputed.json) · [索引](derived/failed-813/B2-GLM53-S1-20261120T074802Z-index.json) |

`061704Z` 已实际跑完 GLM-5.3 首载，141 个分片通过零驻留页准入。激活至首个正确完整响应为 **852.265 秒**，Host KV 池增加 **26.344 MiB**。同轮回访已在原八 rank 上实际回读 512 token，每 rank 覆盖 21 层 indexer 和 78 层 MLA，删键后零命中且金标通过。这些数据仅作为单段证据。

两次 813 GiB 尝试均观察到整池锁页注册失败，384 GiB 先行轮确认整池注册成功。`074802Z` 在准入时的最低可用显存为 124,606 MiB，门槛为 130,417 MiB。进程归属、显存快照和逐次释放采样可从上表索引找到。

恢复后先核查现场预约、八卡空闲和 20 个冻结文件，再以新 run ID 从参考探针版 S1 开始。S1、S2、S3 三轮各自完成 7/7 及释放验收后，按 r2、r3 各跑 S1、S2、S3。三次失败轮不计入正式样本。

正式 B2 的前三段均为原生 SafeTensors 冷载，后四段均从 checkpoint 唤醒原 Worker。K 为 Kimi-K2.6，G 为 GLM-5.3，D 为 DeepSeek-V4-Pro。

| 序列 | 首次冷载 `00–02` | 回访唤醒 `03–06` |
| --- | --- | --- |
| S1 | K → G → D | G → K → D → K |
| S2 | G → D → K | D → G → K → G |
| S3 | D → K → G | K → D → G → D |

三轮都复用 Kimi 已验收的 560-token 参考探针。前 512 token 走实测 Host KV 回读，后 48 token 要求复述前缀中的编号。验收要求自然停止、逐 token 匹配及删键后零命中。业务金标、GLM 和 V4 输入均保留。[参考探针](diagnostics/kimi-reference-probe.json) · [适配补丁](diagnostics/joint-b2-kimi-reference.patch)

下文保留 2026-11-20 的历史双池回执及当时的验收步骤。历史 S1 最好一次 2/7，S2 尚无正式运行，S3 最好一次 6/7。当前正式候选使用上段单池配置。历史 S1 的 V4 Store 首载与 S2 的 Kimi Store 驱逐门槛仅用于解释旧轮次。

## GLM-5.1-FP8 通过与 GLM-5.3 本轮未通过的原因

| 对照项 | 历史 GLM-5.1-FP8 原生 B2-L2 | 历史 GLM-5.3 联合双池 B2-L2 |
| --- | --- | --- |
| 加载及回访 | 三个模型首次均原生加载，首段做 SafeTensors 冷页准入。回访从 checkpoint 唤醒原 Worker。 | 首段 SafeTensors 冷页加载，另外两个模型首次由同一 Store 原位恢复。回访从 checkpoint 唤醒原 Worker，再注册 LMCache 并回读同模型的 Host KV。 |
| 常驻组件 | 运行清单 `weight_store=false`、`lmcache=false`。 | 常驻 **864 GiB 权重 Store + 813 GiB Host KV**。逐段按双池和休眠 Worker 同时驻留时的实际 `MemAvailable` 检查准入。 |
| 通过范围 | Kimi WNA16 与 V4 Marlin scale 修复后的 S1、S2、S3 各 3 轮，**63/63 段金标、36 次原 Worker 回访**。 | S3 已验证三个模型各一次真实 Host KV 回读。最后一次 V4 在唤醒前因主机 RAM 准入停止，整轮未通过。 |

历史通过证明原生 Sleep L2 的权重重载、原 Worker 身份与金标。本次还要求 Store 首载、常驻 Host KV 的注册和实际传输、跨模型隔离及轮末清理。最新 S3 停在 `06-D-v4`，可用 **66.937222 GiB**，距 **67 GiB** 门槛差 **64.285 MiB**。此前 `05-G-glm53` 已在原 Worker 的八个 rank 上回读 512 token，每个 rank 均有 78 层 MLA 与 21 层 indexer 的实际 H2D 和金标回执。GLM-5.3 的独立 1 GiB Host KV 单模型对照也通过了两组回读、金标及 61.163 秒八卡释放观察。当前能确认的失败点是末段 V4 的主机 RAM 准入。GLM 模型和引擎版本各自的影响仍缺相同双池、加载路径和准入规则下的配对证据。[历史报告](../report.md#kimi-wna16-修复后的三模型-sleep-l2-复验) · [历史实际配置](../large-pool-20261114/sleep-wna16-sequences/B2-WNA16-S3-r1-20261117T062729Z/campaign-input.json) · [GLM-5.3 单模型复算](derived/glm53-l2-memory.json) · [本轮准入原件](raw/B2-GLM53-S3-20261119T231013Z/06-D-v4/host-admission.json)

## 补丁及验证状态

| 项目 | 补丁或源码 | 实测覆盖与剩余验证 |
| --- | --- | --- |
| V4 唤醒前 RAM 复查 | [复查补丁](diagnostics/joint-b2-v4-ram-settle.patch)，`b2_sequence.py` | **仅 CPU 验证。** V4 checkpoint 唤醒首次读数满足 `55 GiB ≤ RAM < 67 GiB` 时，每 2 秒复查、最长 30 秒，连续 3 次达到 67 GiB 才继续。低于 55 GiB 或到期走停止清理，唤醒前仍按 67 GiB 即时复核。记录 `host-admission-settle.json`，使用原执行截止和清理余量。旧轮首次回到 67 GiB 以上发生在清理信号之后。 |
| V4 KV 布局 | [物理视图补丁](diagnostics/joint-b2-v4-view.patch) · [八组几何](diagnostics/v4-historical-kv-geometry.json) | **已实测 S3 SafeTensors 首载注册及一次 V4 回访。** 严格核对三维 `(blocks, slots, width)`、block stride、dtype 及每 rank 八个传输组。S1 的 V4 Store 首载后注册及回访仍待实机串起来。 |
| S2 Kimi Store 首载 | [Store 驻留检查](diagnostics/joint-b2-s2-store-entry.patch) · [S2 容量配置](derived/b2-phase-capacity-s2.json) | **S2 只有 CPU 验证和计划。** 86 GiB 门槛只适用于 GLM、V4 都在休眠且 Store 完整驻留 V4 的阶段。依据来自 S3 空 Store 下 Kimi 原位首载的实测下降 21.313 GiB，另计驱逐余量。S2 的实际驱逐、读取和 RAM 低点待测。[S3 原始采样复算](derived/s3-kimi-store-admission-evidence.json) |
| 跨模型 KV 查询 | [时序补丁](diagnostics/joint-b2-namespace-order.patch) | **实测。** 在旧模型注销前验证同前缀 512 命中，在目标模型注册后验证 0 命中。S1 `214730Z` 已验证 Kimi → GLM，S3 `231013Z` 已取得五对；末段 V4 未启动，因此第六对待验收。 |
| 服务与容器启动 | `run_three_model_b2.py`、`b2_lmcache_launch.py` · [Docker 等待补丁](diagnostics/joint-b2-container-start-timeout.patch) | **S3 实测双池就绪且三模型启动。** 守护器按预约截止和四小时上限取较早值，LMCache 设置 `nofile=8192:8192` 并检查就绪前退出，`docker start` 最多等 90 秒且受本轮截止约束。 |
| Kimi WNA16 + meta、V4 Marlin scale | [Kimi meta 计数补丁](diagnostics/kimi-l2/wna16-count-meta-only.patch) · [TP 前切片候选](diagnostics/kimi-l2/localize-wna16-transpose.patch) · [V4 Marlin rebind 生成器](../large-pool-20261114/prepare_v4_marlin_rebind_candidate.py) | **Kimi 单模型 GPU 对照、联合 S3 Kimi 与 V4 各一次回访已通过。** 联合配置沿用 WNA16 + meta，TP 前切片候选留作独立对照。正式启动要核对实际镜像、挂载和逐 rank 导入 SHA，参照[单模型对照](derived/kimi-l2.json)及[S3 Kimi](raw/B2-GLM53-S3-20261119T231013Z/workers/worker-K/worker-imports.json)、[S3 V4](raw/B2-GLM53-S3-20261119T231013Z/workers/worker-D/worker-imports.json) 的实际导入回执。 |

## 历史双池 B2 的 S1、S2、S3 验收顺序

段号从 `00` 开始。K 为 Kimi-K2.6，G 为 GLM-5.3，D 为 DeepSeek-V4-Pro。固定 qjhs003、8×H20、TP8、每 rank 16 GiB GPU KV、原权重和金标请求。G 使用 vLLM 0.30.0，K 与 D 使用 0.20.1。先初始化 813 GiB Host KV，再初始化 864 GiB 权重 Store。

| 序列 | `00` 冷页首载 | `01–02` Store 原位首载 | `03–06` 原 Worker checkpoint 唤醒 | 必查点 |
| --- | --- | --- | --- | --- |
| S1 | K | G → D | G → K → D → K | `02-D-v4` 核对全部 496 条 Store 分组路径及八 rank 权重就绪，再核对三维 KV 注册与金标；`05-D-v4` 核对原 Worker 的八组回读。历史最好 2/7，正式验收从 `00` 重新跑。 |
| S2 | G | D → K | D → G → K → G | `02-K-kimi` 先采 `store-entry.json`，须对应 `01-D-v4` 的 Store 快照，且完整 V4 为唯一驻留工件、G 和 D 都已休眠。通过 86 GiB 后记录实际驱逐路径、释放及分配块、读入字节、RAM 最低点和金标。`04`、`06` 均验证 GLM 两组回读。 |
| S3 | D | K → G | K → D → G → D | `01-K-kimi` 核对空 Store 与 87 GiB 准入。`04-D-v4`、`05-G-glm53`、`06-D-v4` 分别取回访证据，重点保存末段主机 RAM 原读数、复查采样、最后即时准入和轮末清理。历史 6/7 归档，正式验收从 `00` 重新跑。 |

### 历史双池 B2 的每轮检查

1. **开池前**核对有效预约、节点端口、无在途任务、八卡初始占用和主机 RAM。沿用最低 1871 GiB 的开池准入与逐段 RAM、显存准入。冻结模型工件、容器镜像、挂载、补丁及 SHA-256，并记录 `b2-plan.json`、`controller-source.json`、`live-admission.json`、`pool-init-timing.json`。脚本和既有快照在下文列出。
2. **首载**保存 `00-*/sf-admission/summary.json` 的冷页回执。Store 两段保存实际读入、命中、驱逐及块分配。传输证据要有实际 `STORE` 完成、Host 对象提交及金标，记录各段 `NN-first-*` 和 `step-result.json`。记录旧模型休眠前、目标注册后的同前缀配对查询，并检查八 rank 的身份及休眠回执。
3. **每次回访**按 PID、启动标识及 KV 注册实例确认原八 rank。保存 `wake.json`、`registered.json` 和 `NN-wake-{lookup,transfers,kv-result}.json`。把 `MP_RETRIEVE_END` 与真实 H2D 分组和字节按请求、传输键、rank 对齐。预期 512 token、八 rank，K/D/G 的每 rank 传输组数分别为 **1/8/2**。G 分别核对 21 层 indexer 与 78 层 MLA。金标通过后按对象键 DELETE，再用同前缀确认零命中和结果一致。该对照请求会重新写入键，所以还需验证轮末全池清理。
4. **轮末**保存最后一段的 `last-use-clear.json`，按仍有对象的模型保存 `final-worker-*-clear.json`。核对最终 Host KV 有效字节、对象数和注册均为零，保存 `lmcache-final-empty.json` 或由原始删除回执对齐的清零证据。三组 Worker 及两服务均正常退出，保存关停 ACK 和 `*-exit.json`。从原始 `release.jsonl` 核对八卡同一组 UUID 均为 0 MiB、无计算进程、连续至少 60 秒。最后比对 `joint-sequence.json`、七个 `step-result.json` 与 `result.json`，要求 **7/7、`qualified=true`、无运行和清理错误**。
5. **失败即保留原次。** 记录首个失败接点及实际读数、控制器和 Worker 日志、LMCache MP 回执及释放采样。GPU 已归零的失败轮单独报告释放结果。新运行使用新 run ID，三轮分别验收，历史片段不拼接成一轮。

## 历史双池快照与当前复算入口

- 历史双池快照父目录为 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/`。S1 `formal-ram-S1.VWIupndC/`，S2 `formal-ram-S2.Apr9Wlvn/`，S3 `formal-ram-S3.kSgbpiJf/`，各自的 `cpu-plan/b2-plan.json` 为 CPU 计划。[S1 容量配置](derived/b2-phase-capacity.json) · [S2 容量配置](derived/b2-phase-capacity-s2.json) · [S3 容量配置](derived/b2-phase-capacity-s3.json)
- 当前正式启动器使用 `formal-nostore-813-reference/`，按首段说明校验三份计划和预约。观测器结束后使用 [重算、索引与报告命令](README.md#从原始回执重算联合-b2) 复算该轮原始证据，传入同轮的主机采样及 `worker-K-final.log`。索引核对复算所用的同轮采样和 Kimi 日志的路径、大小及 SHA-256。待三轮 813 GiB 全部通过再生成正式报告。
- 首次加载与回访分开统计，分别列权重准备、新 GPU KV、旧 GPU KV 开始释放至新 KV 就绪、至可服务、首个正确完整响应及全套金标。GLM-5.3 目前缺首载八 rank 独立 GPU KV 分配起止和旧 `kv_cache` 逐 rank 解除映射起点，保留 `null`，已有回访 KV 唤醒端点照常使用。

## 原始日志与失败回执

正式运行原件父目录为 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b2-runs/`。下表链接为本地已归档回执或带服务器绝对路径、字节数和 SHA-256 的索引。

| Run ID | 通过段数与停止点 | 原始日志或索引 |
| --- | --- | --- |
| `B2-GLM53-S1-20261119T205243Z` | 0/7，守护器截止校验 | [终态](raw/B2-GLM53-S1-20261119T205243Z/result.json) · [LMCache 守护器日志](raw/B2-GLM53-S1-20261119T205243Z/lmcache-guard.log) |
| `B2-GLM53-S1-20261119T205548Z` | 0/7，LMCache FD 上限 | [LMCache 原日志](raw/B2-GLM53-S1-20261119T205548Z/external-cleanup/lmcache-final.log) · [外部清理与释放](raw/B2-GLM53-S1-20261119T205548Z/external-cleanup/result.json) |
| `B2-GLM53-S1-20261119T210129Z` | 1/7，旧模型注销后查询 `no_gpu_context`，解锁 RPC 超时 | [终态](raw/B2-GLM53-S1-20261119T210129Z/result.json) · [LMCache、Worker 与查询日志索引](raw/B2-GLM53-S1-20261119T210129Z/evidence-index.json) |
| `B2-GLM53-S1-20261119T214730Z` | 2/7，V4 Store 权重加载后，KV 物理视图校验停止 | [终态](raw/B2-GLM53-S1-20261119T214730Z/result.json) · [Store、Worker 和注册证据索引](raw/B2-GLM53-S1-20261119T214730Z/evidence-index.json) |
| `B2-GLM53-S3-20261119T224044Z` | 1/7，Kimi `docker start` 超时 | [终态](raw/B2-GLM53-S3-20261119T224044Z/result.json) · [Docker 事件](raw/B2-GLM53-S3-20261119T224044Z/docker-events-worker-K.jsonl) · [索引](raw/B2-GLM53-S3-20261119T224044Z/evidence-index.json) |
| `B2-GLM53-S3-20261119T231013Z` | 6/7，最后一次 V4 RAM 准入不足；八卡持续归零 60.959 秒，保留三个 Worker 清理错误 | [终态](raw/B2-GLM53-S3-20261119T231013Z/result.json) · [逐段复算](raw/B2-GLM53-S3-20261119T231013Z/recomputed.json) · [释放原件](raw/B2-GLM53-S3-20261119T231013Z/release.jsonl) · [完整索引](raw/B2-GLM53-S3-20261119T231013Z/evidence-index.json) |

最新 S3 在运行原件目录下保存 `lmcache-final.log`、`store-final.log`、`worker-{K,G,D}-final.log` 和 `lmcache/` 的原始事件。`06-D-v4/host-admission.json` 是停止点，轮末清零回执缺失。控制器与主机采样另存于 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/formal-s3-start.A6G9E6F0/`，文件为 `controller-B2-GLM53-S3-20261119T231013Z.log` 与 `B2-GLM53-S3-20261119T231013Z-host-memory.jsonl`。采样共 13,282,515 B，SHA-256 `5a678c2bf400621d7111b910a063df24ffe1082523848261d2b15ca171e42a17`。分组原件可从 [GLM 回访传输](raw/B2-GLM53-S3-20261119T231013Z/05-G-glm53/05-wake-transfers.json) 和 [V4 回访传输](raw/B2-GLM53-S3-20261119T231013Z/04-D-v4/04-wake-transfers.json) 查起。
