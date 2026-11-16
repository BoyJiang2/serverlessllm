# 大池 B1 的 S1 补测

本轮使用 TP8、864 GiB 权重 Store 和 813 GiB Host KV。首段 Kimi 通过 SafeTensors 冷读准入，后六段使用常驻 Store。七段金标与 Host KV 写入、清零和零命中均通过。整轮结束后，49 个采样点覆盖 61.078730650 秒，八卡均为零显存占用且无计算进程。本轮单列，正式统计的样本仍为原九轮。

| 段 | 模型 | 权重来源 | Store 重读 GiB | Store 命中 GiB | 驱逐路径数 |
| ---: | --- | --- | ---: | ---: | ---: |
| 1 | Kimi | SafeTensors 冷读 | — | — | — |
| 2 | GLM | Store 全未命中 | 710.16 | 0 | 0 |
| 3 | V4 | Store 全未命中 | 817.45 | 0 | 8 |
| 4 | GLM | Store 全未命中 | 710.16 | 0 | 400 |
| 5 | Kimi | Store 全未命中 | 574.63 | 0 | 101 |
| 6 | V4 | Store 全未命中 | 817.45 | 0 | 11 |
| 7 | Kimi | Store 全未命中 | 574.63 | 0 | 315 |

表格由 [逐段回执](switch-events.csv) 的原始字节除以 2³⁰ 得出。首次 SafeTensors I/O 在 Store 字段中不适用。权重命中与 Host KV 请求命中来自各自的计数。

[整轮验收](qualification.json) · [释放采样](release-samples.jsonl) · [初始化原始端点](pool-init-timing.json) · [qjhs003 证据索引](evidence-index.csv)

## 仍需采集的 GPU KV 端点

用独立 B1 短序列复用本轮前两段，首段 Kimi 按 SafeTensors 冷读准入，随后通过常驻 Store 恢复 GLM。保留 TP8、双池大小、Host KV 写入及清理、业务金标和至少 60 秒的最终释放观察。新轮次独立编号。

在固定 vLLM 镜像的 `GPUModelRunner.shutdown()` 中，为已有的 `_cleanup_profiling_kv_cache()` 调用记录八 rank 的主机单调时钟起止。该调用清除 KV 引用并执行原有缓存清理。它的运行区间可用于比较清理操作至新 GPU KV 和 LMCache 注册就绪的跨度。原有分配器同时清理其他缓存，物理 GPU KV 释放起止仍需按实际 KV 分配追踪。rank 缺失、进程身份不符或强制退出时，相应测量留空。

Store 缺块路径的加载到主机跨度已从历史日志恢复。若后续需要细分到文件读取系统调用，可在 `Model::ToHost` 两处已有 `pread` 分支记录单调时钟端点，按读取线程和分区汇总。现有跨基线对照使用已核验的加载阶段跨度。
