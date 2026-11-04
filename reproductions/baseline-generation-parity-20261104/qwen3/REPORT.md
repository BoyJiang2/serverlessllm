# Qwen3-8B：生成配置与 SafeTensors 基线对齐

状态：实际GPU重跑通过，12次正式测量与1次预热。Prompt为“The quick brown fox jumps over the lazy dog.”，调用generate(max_new_tokens=50)。BF16、GPU7 H20、4 CPU、32GiB Store、1 IO线程/32MiB chunk；原cold/warm各3次协议与加载同步计时保留，生成不计入加载时间。生成前按1000+trial重置随机种子，各种子在SF/SLLM及cold/warm之间配对。

SLLM加载入口已在返回模型前恢复checkpoint的generation_config.json，恢复IO计入加载计时；不覆盖SF默认配置、不强制greedy、不修改基线temperature/top_k/top_p。实际所有配置与SF原checkpoint逐字段相同：do_sample=true,temperature=0.6,top_k=20,top_p=0.95，特殊token设置相同。所有参数完整BF16字节SHA与原实验一致（68dc53d65a3487ee16ef93f4efb1de37125fe7cc5d323d2d80b1ba9c8e8c935a）。

3种子产生3种序列；每个种子对应的两后端与cold/warm共4轮，输入与输出token逐一一致，每轮实际生成50 tokens。只证明这些输入/种子的匹配，不宣称一般准确率或任意输入等价。

| 场景 | SF加载中位数秒 | SLLM加载中位数秒 | 加载加速 | SF生成中位数秒 | SLLM生成中位数秒 |
|---|---:|---:|---:|---:|---:|
| cold | 9.222960 | 2.653447 | 3.476x | 1.309586 | 1.360782 |
| warm | 2.760967 | 0.532067 | 5.189x | 1.357236 | 1.317220 |

完整13个小JSON与verified-summary.json保留。结束后60秒释放观察通过。原Hello/single-forward实验与本次之前未对齐实验保留，不覆盖。未使用Llama的random/cached数据作为Qwen cold/warm对照。共享SDK、原始权重、既有Store均只读；修正以本任务容器文件挂载生效，未修改Kimi或官方checkout的跟踪文件。
