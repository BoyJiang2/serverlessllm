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
