# SLLM × V4-Pro：分组转换与八 worker 完成协调（P3 CPU）

2026-11-06；父提交 db1fd953d2ea1e04409cee6f4bc00bd5302c10f8。177 项统一 CPU 检查通过，无跳过；未安装推理环境、完整构建 native 扩展或运行真实 Ray/gRPC/vLLM/Store/GPU。

## 接点和最小改动

新增 importable V4ExportWorkerExtension，通过固定 vLLM 的 worker_extension_cls 注入；控制端只调用字符串方法 sllm_v4_export_receipt。使用 worker.get_model() 解包原生 wrapper，复用既有 grouped_export_result；不传 Python callable，不启用 VLLM_ALLOW_INSECURE_SERIALIZATION。

worker 校验 grouped-export load_format、identity、rank、回执键集和 primitive JSON 大小（至多 16 KiB），随后同步本 rank CUDA device，再返回小字典。Python 后处理返回不能替代 CUDA 完成；同步异常会使 RPC 失败，控制端不可用落盘文件替代返回值。权重及 Tensor 不经过此 RPC。

新增 v4_convert 协调入口，保持旧 downloader 不变；后者原先的 max_model_len=1/旧导出方法不适用于本配置。转换入口固定 TP8/EP-off/PP1/DP1、BF16/native FP4-FP8、2048 上下文、eager、seq1/batch2048、MTP/prefix off、CPU offload0、mp executor、指定扩展类和 grouped-export loader。GPU memory utilization 显式采用目标 LLM 构造器默认 0.92；这是离线转换准备配置，尚未通过 H20 容量准入，也不能据此替换服务/正式请求配置。seed20261105，模型/tokenizer revision 均固定为 b5968e9190ef611bbf34a7229255be88a0e937c1，tokenizer_mode=deepseek_v4。

## 完成与失败契约

| 阶段 | 成功条件 | 失败行为 |
|---|---|---|
| 前置检查 | 原生 config SHA、identity、安全序列化设置、绝对路径及 STORAGE_PATH 边界有效 | 不创建转换目录、不构造引擎 |
| 领取目录 | 新目录独占创建，写 CONVERSION_STARTED.json，绑定完整 engine config | 不覆盖旧成功/部分数据；要求新转换 |
| 构造 | 目标 LLM 完成原生加载及初始化 | 记录 constructing 失败；构造未返回时无法取得引擎句柄，外层仍需隔离可能残留进程 |
| 收集 | 命名 collective_rpc 返回八个唯一 rank 且身份一致；每个 worker 完成同步 | 缺失、重复、异身份、异常或超时均失败，不从 export_done 文件补齐响应 |
| 停止引擎 | engine_core.shutdown 返回成功 | 不发布 COMPLETE，保留引擎强引用；不盲目重试 shutdown |
| 发布 | 校验全部 rank manifest、export_done、worker 回执、唯一 GPU UUID 及完整 payload 哈希 | 任一损坏均不发布；只有全部通过才原子写 COMPLETE.json |

RPC 超时固定 1200 秒，shutdown 参数 60 秒；二者不覆盖 LLM 构造阶段的全程超时，也不代表至少 60 秒物理释放观察。外层进程控制、实际 worker 消失和资源观察仍须独立验证。

失败写 CONVERSION_FAILED.json，记录阶段、异常类型、shutdown 次级错误与引擎引用保留状态。若写失败证据本身失败，export_contexts 仍保留 record_error，不掩盖原始异常。COMPLETE 落盘后、调用返回前也可能中断，造成完成/失败标记并存；此时 CONVERSION_FAILED 是准入否决条件，发布、服务注册与 loader 恢复统一拒绝，不因 COMPLETE 存在而继续。硬终止来不及写失败标记时，外层控制仍须按缺失成功回执统计失败，不能把 Store 数据提交当作实验成功。Unix 时间字段明确命名为 *_unix_ns，仅用于控制审计，不与 GLM monotonic 计时相减。不删除失败权重，也不自动复用目录。

## 源码与 CPU 证据

本批从官方固定提交 132765e3560659ff63ebd236203672e991b70e08 获取 10 个额外 RPC/worker/engine API 文件，逐个核对 Git tree blob 与 SHA。局部源码门禁由 9 个扩展到 19 个文件，不代表完整包或 ABI 验证。原始上游 fixture 保留完整字节供离线 AST 回归；大量 fixture 行是上游证据，不是修改 vLLM 引擎的补丁。源清单见 docs/evidence/v4-export-coordinator-p3/source-manifest.json。

新增 18 项用例包含：固定上游 WorkerWrapperBase 的实际扩展注入、GPUWorker/GPUModelRunner 的 get_model/解包，以及 LLM→LLMEngine→SyncMPClient→EngineCore→MultiprocExecutor→WorkerProc 字符串分发和实际 FutureWrapper 聚合。消息队列、进程、native/CUDA 同步和构造/shutdown 均为受控替身；实际 msgpack/ZMQ/共享内存/安全传输尚未运行。异常用例涵盖部分/重复/异身份回执、构造失败、RPC 超时、延迟 CUDA 错误、shutdown 失败、payload 损坏、证据写失败，以及旧目录不可覆盖。

在 qjhs2 的真实 /mnt/raid/models/DeepSeek-V4-Pro 上额外执行 --plan，原生 config SHA 匹配；回执确认没有导入 torch/vllm/ray/native Store 模块，也没有创建目标 Store 目录。该计划 identity 明确为 CPU_PLAN_ONLY_NOT_FOR_RUNTIME，Torch 版本字符串为不合格占位符，不能当成已安装环境或实际转换准入。计划、回执、验证脚本和 177 项日志一并归档；此前 174/175 项通过日志保留在服务器 evidence；增加同步门禁和中断窗口否决用例后重新验证最终源码。

## 剩余门槛与数据对齐

入口依赖未来安装后的 sllm_store general plugin 在父进程/全部 worker 可见；本次没有安装。完整源权重 payload SHA、真实 secure RPC/worker 启动与终止、原生量化/bitwise/gold、完整 HBM/host/pool 容量和转换峰值、Store 恢复及原生服务生命周期、至少 60 秒物理释放观察均未验证。模型小资产/index/64 分片大小/header 核对不能替代完整 payload 或固定 revision 证明。

下一步接完整 phase/event recorder 和严格缓存/计时准入，并继续检查 native 磁盘失败到 GPU 消费者的传播。全 payload 哈希属于准备成本且会污染缓存；正式 runner 尚需落实 Controller 注册、router 启动与 cache gate 的时序。逐组 H2D 在原生 quantization 内执行，两者 span 不可当作独立纯耗时相加。历史 GLM 结果不作为 V4 PASS，正式实验编号未使用。

代码和证据仍在 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}；权重原位使用 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro；Store 大数据留 /data/brick1/boyjiang，GLM 原始参考及旧 /data 工作快照未改动。
