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

状态：默认配置脚本整理中；Qwen GPU预检与正式实验、Llama cached30均尚未执行。预算依次为780、3780、1980秒（共6540秒，含每次至少60秒释放观察及清理/取证余量）。不在正式测量或释放观察期间进行Git/同步。
