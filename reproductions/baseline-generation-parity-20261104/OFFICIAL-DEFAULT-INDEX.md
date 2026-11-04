# 官方仓库默认配置复现：工作索引

目标：Llama-3.1-8B-Instruct、Qwen3-8B，FP16；每模型每种格式random30与cached30。调用官方runner时不传num-replicas，并清除同名环境变量，使用runner默认值30。Fox输入、generate(max_new_tokens=50)、各自原始生成配置；Store32GB/4 IO线程/16MB chunk。当前运行是仓库加载微基准，不是论文Figure6的20副本/清系统缓存实验。

本地根目录：D:/Documents/codex/2026-11-03/kimi-k2-6-goal-qjhs002-data/outputs

| 目录 | 内容 |
|---|---|
| generation-config-parity | 已验证SDK修复、CPU测试、历史说明 |
| qwen3-official-fp16-probe | 单对FP16预检，独立于正式样本 |
| qwen3-official-random-cached | Qwen正式random30/cached30脚本与后续结果 |
| llama31-buffer-parity-final/results/random | 已通过的Llama random30原始证据，直接复用 |
| llama31-default-cached30 | Llama默认cached30补充运行 |
| qwen3-fox50-buffer-parity | 旧BF16 cold/warm补充实验，保留但不替代默认协议 |

服务器根目录：/data/brick1/boyjiang/ServerlessLLM/models/
对应任务目录带点号和日期后缀，例如.qwen3-official-random-cached-20261104/；脚本在scripts/，结果在results/，临时模型仅在专用storage/。唯一源码checkout、共享SDK、原始权重及Kimi源码均不改动。

状态：默认配置全部验收通过。两模型每种格式random30与cached30；共120对token全部匹配。Qwen两组与Llama cached为本轮执行，Llama random复用已有通过记录。每轮实际生成50新token。至少60秒释放观察通过，临时模型副本清理完毕。

服务端统一报告：/data/brick1/boyjiang/ServerlessLLM/models/.official-default-20261104/REPORT.md
服务端完整统计：同目录SUMMARY.json；每组原始数据的绝对路径、SHA256均在SUMMARY.json内。新预检、Qwen正式、Llama cached30的启动/退出/释放证据放在同目录metadata/中。
本地仅保留小型代码/索引/摘要，重复原始数据在服务端SHA核验后清理。
