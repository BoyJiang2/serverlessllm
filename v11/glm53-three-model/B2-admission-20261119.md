# 联合 B2 Sleep L2 容量前检

联合 B2 固定 qjhs003、8×H20 TP8、864 GiB 权重 Store 和 813 GiB LMCache Host KV 池。首模型通过 SafeTensors 冷页准入后加载，另两个模型首次从 Store 原位写入 CuMem 权重。后四段保留各自原 Worker，从原 checkpoint 唤醒，再重注册和回读同模型的 Host KV。这组设计独立于历史原生 Sleep L2。

| 已核对的资源 | 观测值 | 证据 |
| --- | ---: | --- |
| 双池就绪后可用 RAM | 128.332–130.406 GiB | 当前 B1 S1 前五段 `host-admission.json` |
| 历史原生 L2 最大主机 RAM 压力 | 128.711 GiB | 七个合格轮中，同轮启动至最低 `MemAvailable` 的最大变化 |
| 保留 50 GiB 时的双池后准入 | 179 GiB | 将历史最大变化与余量相加并向上取整 |
| qjhs003 物理 RAM | 1923.283 GiB | 节点 `/proc/meminfo` |

两池实际就绪后的余量尚不足以按现有保守门槛启动联合 B2。历史原生 L2 和当前 B1 的压力来自各自独立运行。联合 B2 已完成三条序列的 CPU 启动计划验证，尚无联合运行的 OOM 或同 Worker 回读结果。GLM-5.3 单模型此前已通过两组 Host KV 回读，尚需带分阶段 cgroup 采样复跑。Kimi 的两项 WNA16 优化待同配置 GPU 对照，之后复核联合准入。

B1 S1 的 L1 状态回执报告 813 GiB 容量，LMCache cgroup 的匿名内存实测约 813.516 GiB。该轮 LMCache 的一次整池 CUDA pin 请求失败，日志报告 872,952,102,912 B。七段实际 KV 写入、清理和金标均通过。两池退出后，同机 `SUnreclaim` 从约 71 GiB 回落到约 4.7 GiB。现有普通用户采样无法确定各类 slab 的归属。[S1 LMCache 原始日志](raw/B1-GLM53-S1-20261119T141542Z/lmcache-final.log)

## 验收要求

- 各轮三次首次加载和四次原 Worker checkpoint 唤醒。固定每 rank 16 GiB GPU KV 配置，记录实际分配和显存准入。
- 对照相同模型休眠前与唤醒后的业务及冷、热 KV 探针。验证 Host 提交、实际 H2D、原 Worker 身份及模型级清理。用相同前缀向下一模型查询，验证零跨模型命中。
- GLM-5.3 的 21 层 indexer 与 78 层 MLA 分别验证八 rank。DeepSeek-V4-Pro 按八个物理传输组的有效滑窗检查。
- 每轮结束三组 Worker 和两服务均正常退出，八卡归零持续至少 60 秒。

## 原始证据路径

- B1 两池 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b1-runs/B1-GLM53-S1-20261119T141542Z/`。
- 历史最坏 L2 `qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117/runs/B2-WNA16-S1-r5-20261119T021015Z/resource-samples.jsonl`。
- 联合计划 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/cpu-finalplan-{S1,S2,S3}/b2-plan.json`。
