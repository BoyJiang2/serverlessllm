# 图5完整加载总跨度：历史边界核验

**结论C：严格共同边界与逐rank进程身份链尚未闭合，不能判A进入统一主图。** 24项的TP8时间端点可重算，不等于24项已通过统一功能边界。

## 实际源码与范围

运行代码f176e05607d04999ab28e8ea8cd49b41270cb528；vLLM基线43a9b1afbb7ed88e63ebeefeb55df8d445aa1992。worker替换文件由C23冻结launch挂载到镜像vLLM路径；本次取executed-source-f176e05，不用已不同的当前launch文件。来源字节、挂载源码及SHA见loading-boundary-sources.json。

| 源码 | 位置（1起） | SHA256 |
|---|---|---|
| gpu_worker.py | 320 decorator；321–326 load_model | 86f5606fa76ab71b78699e22b70319cc9c85ead31761dba2e0c1187e984c8182 |
| event_trace.py | 55发begin；57调用函数；63发end；11–43写时间/进程 | e1ab869bc51cbc42f46430f2296d1a9e4a48fc45aec57cf7908d88ae565c6c6c |
| base_loader.py | 44–94 load_model，57构造、91后处理 | d26f6561e4a3480591f63d1b653b0efd170ae09c03ee18397996289e35285ab7 |
| Store vllm_loader.py | 127–168 load_model，144构造、166后处理 | 36a40e20f8d2d6b2e7b3346ec5e1c54d5598359b1b1756348ef97723e34fa502 |

begin由traced包装器在调用GPUWorker.load_model前发出；end在该函数成功返回后发出，异常路径发_failed而非_end。范围包含weights内存池/config上下文及model_runner.load_model，begin记录写文件本身也处于该包络内；它不是只围住load_weights。end证明host调用返回，不等于独立记录了所有异步CUDA工作完成。
SF的Base loader与Store loader源码均在其内部创建模型并执行加载及必要后处理；Store还有骨架storage释放/恢复工作。192个实际rank区间均验证device_setup end≤pipeline begin≤quantization begin≤quantization end≤pipeline end≤KV begin，因此这些已记录的设备/NCCL初始化与KV阶段在外，后处理在内。不能把通信buffer等所有通信相关工作也据此称为在外。
runner对象本身在gpu_worker.py 297–312的init_device内先行构造，pipeline从其后才开始。Store的fresh骨架分配在本段内有直接事件；SF有Base loader构造源码。要严格排除实际runner构造时更早的权重storage分配，还缺实际runner构造函数版本/映射证明，不能仅凭pipeline字段名宣告fresh边界已闭合。
GPUModelRunner实际worker映射文件SHA及V1/V2实际分支缺少历史证明。固定43a的V1官方源码只作参考：4730–4887含内存记账、可选LoRA/drafter/aux、4826–4831模型专属通信buffer、4856–4885可选compile/graph包装、4887 offloader收尾。MTP配置null不能代替所有这些条件分支的逐worker证据。192个已记录compile/warmup begin均在pipeline end后；Worker的内存profile是另一入口，不能据此把runner内所有条件编译/初始化也说成完全没有额外工作。

## 192行逐rank证据与缺口

loading-boundary-endpoints.csv逐行列attempt/rank/PID、startticks、begin/end、时钟上下文、原始进程文件路径与SHA、端点行号及phases来源。每个非空配对均在同一PID/容器、唯一源文件记录及同一个backend create→ready窗口内；每项8个不同PID、rank0–7。逐rank声明与上下文证明分开。
在本轮读取的事件与小型身份归档中：rank startticks缺失192/192，rank直接boot标记缺失192/192。actor的PID/startticks/boot另列，绝不填给GPU worker。没有重新扫描大监控来补这些值，也不以请求时间窗推造startticks；不声称未读取的大监控中同样不存在身份信息。CLOCK_MONOTONIC来源由固定event_trace.py确定，boot绑定来自actor/容器，不声称每rank独立时钟身份已取证。

## 逐attempt公式与原6项均值

`weight_load_host_all_rank_span_s = (max(rank_end_ns)-min(rank_begin_ns))/1e9`；另列max_rank_host_duration_s，不替代此值。缺失端点返回null。下面是完整端点包络的复算，不是严格共同边界已验收的均值。

| 条件 | 端点有效n/原n | 包络均值/s | 严格统一有效n | 严格统一均值 |
|---|---:|---:|---:|---|
| safetensors_disk_cold | 6/6 | 393.624686710 | 0 | null |
| safetensors_disk_hot | 6/6 | 131.216365887 | 0 | null |
| store_disk_cold | 6/6 | 123.572060839 | 0 | null |
| store_pool_hit | 6/6 | 7.889164785 | 0 | null |

24行逐attempt数值在loading-boundary-attempts.csv。四个均值复核匹配393.624687 / 131.216366 / 123.572061 / 7.889165秒；原ID、B5–B10与四条件未变。

本轮不重画图5：当前证据只能保留观测包络，不能将其升级成统一主比较。ingestion/postprocess只作专项说明，不与V4同名阶段直接横比；其专家copy所在遍历与GLM工作范围未证相同。图4/helper及资源补充不修改。

复算：`python glm/tools/historical_costs.py --c23-loading-boundary`。来源SHA清单为loading-boundary-inputs.json；无监控重扫、无模型/容器/Store运行。官方参考：[固定43a GPUModelRunner](https://raw.githubusercontent.com/vllm-project/vllm/43a9b1afbb7ed88e63ebeefeb55df8d445aa1992/vllm/v1/worker/gpu_model_runner.py)，不是实际映射文件证明。
