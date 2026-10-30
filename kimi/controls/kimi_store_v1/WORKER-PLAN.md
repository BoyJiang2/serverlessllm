# Kimi 独立 worker：下一步准入清单

2026-10-30。源码准备草案，不是资源分配、预约、运行gate或可执行部署命令。只使用qjhs003；旧GLM模型、40个ID、金标、失败、runtime/active均冻结。

## 当前已接与尚未接

源转换入口现可由独立Base子类调用：固定Default loader只加载本地SF，rank导出发生在后处理之前，之后固定Base继续后处理，便于同一后端生成参考。`vllm_export_hook`默认读取已初始化的TP/PP组及当前CUDA设备；CPU测试的context callback是明确模拟。`loader_registry`复用固定官方注册函数，仅使用sllm_kimi_export_v1/sllm_kimi_native_v1独立名称；同类重复登记允许，其他类冲突拒绝，原safetensors不改。

官方general plugin会在process0、engine core及worker中加载；独立worker需要把本run配置与注册回调通过同一插件入口传到每个进程。当前已实现source私有distribution metadata、注册/完整source绑定与内部参考worker、watchdog/主动probe候选并做CPU控制；实际vLLM多进程插件传播、独立宿主服务与全模型仍未运行，不能只在父进程注册后假定spawn子进程也已注册。依据：https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/plugins/__init__.py 。

## 必须先修正 native 双份驻留

当前native候选先完整CUDA构造目标，再分配一整份native归档，二者在安装前同时存在。基于冻结配置/条件TP8映射，保守省略源shape元数据、假定其他权重理想八分，只计MLA A投影的必需复制，得到单rank权重下界76,009,523,580 B，双份152,019,047,160 B。历史卡容量150,754,820,096 B，尚未计视觉/路由额外复制、上下文、KV、scratch、后处理工作区即超出。完整输入、SHA、假设见native-memory-lower-bound.json；固定源码DeepSeekV2FusedQkvAProjLinear明确disable_tp=True。

这是条件静态预算结论，不是实际OOM或历史exit137的原因证据；历史事件仍原因未知。当前native工厂新增必须提供的device_memory_budget_bytes，在模型初始化前以归档中checkpoint-backed目标的双份字节下界拒绝不足预算。该字段必须来自新窗口实际资源证据，并扣除其他必要开销；不能直接填大值绕过，也不能把下界可容纳当作完整峰值可容纳。

下一项先评估CPU文本初始化/官方CUDA视觉支路或直接目标IPC写入两种方案。前者须验证混设备元数据、Parameter属性保留、Base后处理设备语义和主机峰值；后者须验证PyTorch缓存分配器的实际IPC base/offset、所有者、事件及释放语义。未经实现、测试和审阅不选择性跳过检查，不改TP/量化格式以求运行。源转换与backend参考只保留一份模型，不受此特定双份下界直接阻断，但仍须完整worker准入。

## worker 必需的闭包

2026-10-31核心修订：完成发布必须消费实际convert_checkpoint_rank返回的receipt['archive']，其中才包含native_index_sha256和cleanup_pending；不能让简化mock的扁平回执替代真实接口。已先用真实CPU转换器返回复现旧KeyError，再将同一微型CPU模型从固定Base初始化、固定Default/Kimi源加载、真实convert/write/index、固定Base后处理连到completion发布，并核packed与BF16原始位模式。设备、TP/name和framework配置为明确测试替身，非真实vLLM/CUDA/全模型。原归档partial拒绝与消费失败终态保持；全部Tensor所有权/峰值与八rank实际验证仍在计时之外完成，当前未开跑。

内部worker现已在既有run_root中独占预留source-reference-events.jsonl，不新建目录。预留文件前核run/output与checkpoint互不包含、run身份相符；文件存在即在engine factory之前拒绝，不覆盖旧证据。开始及唯一终态分别flush+fsync，单行最多64KiB、完整读回最多128KiB；开始保留prompt/KV，成功及失败路径都持久化prompt/实际engine配置、raw tokens/有限logprobs及已完成的business/rank元数据，traceback/error摘要截断有显式标志。使用unbuffered独占写，避免日志flush失败后的close二次刷新覆盖原业务异常；CPU回归实际以io.BufferedWriter+写入失败的RawIO故障控制复现旧路径两次flush覆盖，再验证新路径仅一次失败写且原异常保留。证据写入失败保留业务原异常及附加说明；成功业务后终态写入失败也整体失败，异常仍携带结果供宿主补取。read_reference_events拒绝partial最后一行、旧身份、重复/错序事件、非有限/倒退monotonic以及冒充释放或服务通过的结果。只有started的文件标记incomplete，不推断进程还活着或资源已释放。CPU测试实际拉起一个仅sleep的小子进程，由测试父进程通过自己持有的Popen句柄终止，读回仍为incomplete；仅证明日志中断语义，不是宿主deadline guard、真实容器身份或资源回收验证。

内部源参考worker主体已补在现有source_plugin._run_source_reference：工厂对应vLLM.LLM/SamplingParams，固定TP8/PP1/DP1/PCP1、Marlin、compressed-tensors、BF16非量化张量、无MTP；短文本max_model_len=1024、单请求、显式KV字节预算、无CPU offload/公共Store池，保留视觉与projector构造但跳过多模态profile。调用前后的loader真实profile硬检查仍保留，不能从EngineArgs直接设置派生use_sequence_parallel_moe字段。先收8rank完成元数据，然后分别生成固定32原始token（忽略EOS/min32）和自然EOS（最多128，length截断不算完成）。结果保留raw IDs/文本/每个选中token的有限logprob；非退化检查只是初始参考质量门槛，不是语义正确或稳定金标，gold_frozen=false。全部调用前后使用原批次墙钟/单调预算；finally通过llm_engine.engine_core.shutdown(timeout=剩余预算)尝试退出，即使已到截止也以timeout=0尝试。保留业务原异常、退出附加错误，以及已完成请求的raw tokens/rank/配置证据供未来宿主控制器落盘；构造未返回时明确要求清理可能的partial进程。

接口依据为固定v0.19.1官方源码：[LLM](https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/entrypoints/llm.py)、[EngineArgs](https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/engine/arg_utils.py)、[CoreClient.shutdown](https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/v1/engine/core_client.py)。候选无可直接启动的CLI，未接实际宿主准入；同步constructor/generate不能靠前后检查中断，实际独立guard必不可少。CPU测试的engine/sampling都是stand-in，真实vLLM多进程插件与完整模型尚未执行。shutdown返回也不证明GPU/RSS/cgroup释放；resources_released/full_service_verified仍false，60秒释放监控和完整host控制器继续待接。

CPU接线追加（2026-10-31）：source_plugin向原有export factory传入run_id/holder/code/spec/manifest身份。每个rank只在固定Base.load_model含后处理返回后，向既有rank目录发布source-complete.json；记录sidecar/index SHA与load_model耗时，不包含张量，也不重复导出。父端collect_source_rank_receipts要求8份完整且身份一致、device唯一、实际文件哈希一致；任一缺失/旧身份/损坏/partial拒绝。元数据读回使用nonblocking/no-follow（平台支持时）打开并fstat实际fd为普通文件，completion限4KiB、sidecar/index各16MiB，避免FIFO无界阻塞及全文件内存拷贝。源archive cleanup_pending或completion unlink失败使loader失败，partial保留供取证。metadata_verified仅是本次文件集合，不是payload/TP映射/native/GPU服务验证，相关flag仍false。独立宿主守护、真实UID/cgroup/镜像/预算与进程退出仍未完成；本次不创建运行目录或启动worker。

1. 新run_id/holder、UID2147、qjhs003与实际boot身份、代码SHA/完整文件清单、精确镜像及vLLM0.19.1/Torch2.10.0+cu129等实际ABI。不能沿用GLM的cu130镜像/旧gate当Kimi验证。checkpoint与输出挂载分别只读/独占新目录；先核磁盘容量和实际挂载来源，不重复下载。
2. 新有效授权窗口及不可延长的整体预算：镜像/ABI预检、分布式初始化、SF加载/转换、后处理、业务、60秒观察、模型/Store退出、取证和余量分别登记。各阶段上限尚待runtime方案冻结；未登记完整总和不得启动。所有预算包含编译和公共初始化，不只覆盖native_call timeout。
3. 独立宿主守护在计算前安装并probe，绑定本run的完整容器ID、holder与cgroup，不按模糊进程名清理。独立监控覆盖8卡、NUMA/主机RSS/PSS/锁页/共享内存、CPU及共享I/O。新scope不修改旧GLM守护或其他任务。
4. 模式分开：源转换/后端参考，重复冷启动稳定后冻结Kimi金标，再native Store正确性，最后原生服务路径。固定32原始token和自然EOS分别定义；所有8rank的packed/scale、角色与必要buffer在计时外核验，后处理状态另作覆盖说明。组件通过不冒充服务通过，不启R24。
5. 失败一律保留原始事件和partial输出；RPC超时不证明DMA停止。只受控退出本实验Store/worker/context，按精确身份核验释放并继续60秒观察，不自动重试或卸载共享Store。磁盘资产保留；源版本/transfer/source receipt及所有review/gate须绑定manifest。

本轮仍未安装守护、创建容器、注册插件、启动GPU或读取完整权重。下一次实际运行前需完整独立复审和新资源核验，不能仅凭164/167项CPU测试或注册API通过开跑。
