# V4 首次注册的拓扑与编码一致性检查

2026-11-06。父源码4cf40aeeddd23728d746a7b8a2adab1fa090ce25，运行差异63191be553e5d08d42ff0f313c9f69852acde4318b20ebdb4f444967885eae07。本批新增7项CPU用例，统一281/281通过、无跳过；未安装推理环境、运行GPU或启动805GiB完整权重读取。

## 复现的实际接口问题

Controller.register用model_config.num_gpus决定Ray资源需求，省略时默认0；backend使用backend_config.tensor_parallel_size决定vLLM分布式拓扑。此前注册绑定只拒绝后续改配置，不能拒绝首份num_gpus=1而TP=8的配置，也不能拒绝省略num_gpus。两个负例在修改前均出现ValueError not raised，且可能触及下层注册/路由创建。原始红例和精确父版本共享helper重放均归档。

新v4_service._validate_v4_topology由已有freeze_registration_config（Controller入口）和bind_v4_registration（StoreManager入口）共用，在Store绑定、worker/RPC或router创建之前检查：

- num_gpus必须显式整数8，tensor_parallel_size必须显式整数8；拒绝缺失、bool、字符串、浮点及其他数值。
- pipeline_parallel_size、data_parallel_size的请求值必须是整数1；请求未给出时按本契约的1处理。正式运行仍须回读实际engine/rank配置，不能靠这项静态默认推断环境已生效。
- enable_expert_parallel必须是布尔False，省略按False；不把数值0当作布尔配置。
- 已声明V4服务必须显式tokenizer_mode=deepseek_v4，避免grouped loader配auto时backend走旧role-prefix消息路径。

V4识别仍基于deepseek_v4 tokenizer或sllm_v4_* loader声明，以及既有Store绑定；它不凭模型目录名猜架构，也不是通用模型识别器。下层纯worker Store元数据检查仍可单独使用，无需假装取得GPU资源。三项既有StoreManager测试现显式给出GPU8/编码模式，符合真实服务请求结构；没有修改原始冻结协议、请求、模型权重或并行选择。

## 验证结果与证据边界

7项新测试包含多组异常GPU值/类型、TP/PP/DP/EP坐标、缺失TP/编码模式、合法默认和显式坐标、两loader复用。直接执行实际Controller/StoreManager源函数，证明无效输入在worker/绑定/actor前失败；合法输入传给router的GPU数仍为8，native/grouped切换仍只注册496次且不再次检查Store。Ray、actor、native客户端为替身，不是实机资源分配证明。

首轮green脚本误把嵌套fixture的inspect放在错误层，造成1个AttributeError；修正测试访问路径后通过，保留topology-green-first.log，不将它包装成产品缺陷。精确父版本helper重放确认原2个回归仍失败、无其他错误。最终统一281项回归包含此前注册/事件/结果/Store完整C++源CPU用例；source_files清单29份SHA与当前index相符。

这些检查不是资源预订、物理GPU/CPU容量许可，也不是完整B1 validator。权重完整SHA、真实tokenizer BPE/native gold、dtype/完整配置与有效环境、kernel/ELF/driver ABI、并发注册/删除、Store/worker epoch、物理释放和真实服务均仍有独立门槛。改变了请求不等于改变或获准使用设备，不能据此启动GPU或称实验PASS。

代码仍在qjhs002:/home/boyjiang/sllm-v4-pro/code；脚本/证据在同根evidence。源文件、日志、重放脚本、回执见docs/evidence/v4-topology-p3/。完整权重大IO范围问题此前已发出，未回复前不执行；不把自动goal continuation或静态检查通过当作扩大授权。
