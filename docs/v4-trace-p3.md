# SLLM × V4-Pro：GLM 对齐计时事件（P3 CPU）

2026-11-06；父提交 a1dc8f5eb2335103f15fa941ff4d3cd183fd1d6c。201 项统一 CPU 检查通过，无跳过。新增可选记录器、固定目标的七阶段接点，以及 backend/router/grouped loader 事件；仍未安装或运行真实 vLLM/Ray/Store/GPU，不构成缓存准入或实验 PASS。

## 核实并复用 GLM

重新只读读取 qjhs003:/data/brick1/boyjiang/glm5.1-fp8/code/ServerlessLLM/sllm_store/sllm_store/event_trace.py 和 code/Store-source-v7/sllm_store/event_trace.py，两份 SHA256 均为 e1ab869bc51cbc42f46430f2296d1a9e4a48fc45aec57cf7908d88ae565c6c6c。原始 68 行作为只读 fixture 固定，来源清单归档。

保留 record_event/traced 风格、monotonic_ns/container_hostname/pid、同名 begin/failed/end 和 backend 创建进程信息。扩展支持 async span，避免在 coroutine 尚未 await 完成时提前记录 end；没有迁入旧 GLM 的 callable 权重审计 RPC。新 hooks 是进程内装饰，不把 callable 发送到 worker，不开启 insecure serialization。

## 七阶段与业务边界

| GLM 字段/事件 | V4 0.20.1 接点与解释 |
|---|---|
| device_setup_including_communication | GPU Worker.init_device 包络，包含通信和设备/runner 准备 |
| communication_groups | init_worker_distributed_environment，嵌套于设备阶段 |
| weights_pipeline_including_allocation | Worker.load_model，包括模型构造、分配、加载和后处理 |
| weight_ingestion | DefaultModelLoader.load_weights；Store 为各自 load_weights，grouped 这里只覆盖初始化和 remainder，不能当作全专家 H2D |
| quantization_postprocess | BaseModelLoader 使用的 process_weights_after_loading，保持原生两遍顺序；grouped 内部含逐组 Store load |
| kv_cache_allocation | Worker.initialize_from_config，包括本版本的 KV connector/元数据等准备，非纯分配内核 |
| compile_graph_and_warmup | Worker.compile_or_warm_up_model；固定 eager profile 下仍是原生函数边界 |
| backend_engine_create_begin / backend_engine_ready | AsyncLLMEngine.from_engine_args 及状态切换的原边界；构造失败记录 create_failed，不伪造 ready |
| backend_first_token_observed | 生成循环首次观察非空 token_ids，每 request 一次；不是 HTTP 客户端首 token 或精确 GPU 生成时刻 |
| backend_generation_complete / failed | 生成循环/结果构造完成或异常；不能替代控制端收到正确完整业务响应的时间 |
| router_instance_published | 实例移入 ready pool、移出 starting pool 后记录；不是 Controller 初次冷路由注册 |
| backend_shutdown_begin / end / failed | 共享 shutdown task 的实际 await 边界，重复调用不重记；不是物理 GPU 释放证明 |

可选 general plugin 接点只在 SLLM_EVENT_DIR 开启时安装，并先核验既有 19 个固定源文件。按函数 marker 幂等安装，不改目标 vLLM 源文件字节，也不改变其后处理调用顺序。没有额外 CUDA synchronize；阶段都是 host 函数包络，嵌套跨度不可相加或称为内核耗时。

分组另记 grouped_store_copy（含 allocation/Confirm/binding 等函数工作）及 grouped_expert_postprocess_host（仅原生专家后处理调用）子事件，携带 rank/group_id。后者不包含随后既有的 alias 重绑/同步/owner 检查，不是 GPU 转换完成时间。总 quantization span 仍含专家 H2D，跨 GLM/V4 比较必须披露这一差异。旧 GLM 若实际启用了额外构造后权重 audit，其成本也在原 backend_construct 包络；正式对比须核对环境，不能猜测剔除。

router 发布证据写入失败时，在原 publication 锁内恢复非 ready/starting owner，供既有失败处理继续使用；不能留下“方法失败但 ready 池已有实例”的状态。此处与旧 GLM 解锁后记录相比增加了证据写入的临界区开销，需在配置差异中披露。

## 事件身份与读取契约

默认关闭时不做文件 I/O。开启要求绝对 SLLM_EVENT_DIR 和非空 SLLM_EVENT_RUN_ID；控制端须把两者配置到 actor/worker 环境，真实 Ray/MP 传播尚未验证。新 JSONL 保留原字段并增加 trace_schema、run_id、boot_id、time_namespace、pid_namespace、process_start_ticks、clock_ticks_per_second、clock_id 和写序号。文件名加入进程 start ticks，GLM 的 *.jsonl 扫描仍适用。

记录时禁止覆盖时钟/进程身份；线程锁保护完整追加和短写处理。fork 后重建锁/进程缓存。失败 span 只有 begin/failed，没有成功 end；写失败证据本身失败时保留原异常，并附加 sllm_trace_error。日志错误不能当作原操作成功。

read_event_window 使用控制端提供的 boot/time namespace/run/window，只接收同 CLOCK_MONOTONIC 域、完整进程身份且无 legacy (hostname,pid) 歧义的记录，拒绝混 run/boot/time namespace、损坏 JSON 和链接输入。当前仅支持同一可证实单机时钟域；没有跨主机/时间命名空间校准。该读取器验证身份/窗口，不验证缓存、资源、权重来源或全流程成功。

历史 GLM 缺少新逐行字段，继续通过既有离线库重算其原指标，不补造新身份字段。phase_metrics 现在也拒绝 failed 启动阶段，即使文件里另有 begin/end。业务失败统计仍须由控制端保留所有启动/准备/HTTP/超时结果，不能只统计日志中的 failed 行。

## CPU 证据

新增 19 项测试，涵盖 disabled 无 I/O、参考 SHA、真实 Linux 时钟/进程字段、线程短写、混时钟/损坏/进程键冲突、同步/async 失败与幂等 hook、八独立 CPU 进程七阶段配对、实际 backend/router AST 的首 token/失败/发布回退，以及 grouped loader 的 62 copy/61 postprocess 边界。首轮定向检查误把非 JSON 对象的 TypeError 预期成 ValueError，已修正测试预期，原日志保留；最终统一 201/201 通过。

额外 CPU 记录链路检查输出九个事件文件（控制进程加八 worker），用真实 /proc 身份及新读取器重算阶段包络。worker 方法和 source guard 是替身，控制事件手动发出；receipt 明确 is_formal_attempt=false、weights_loaded=false、gpu_execution=false、source_guard_is_stub=true，数值字段标记为 CPU fixture only。这不是模型性能数据，也没有验证实际 vLLM/Ray secure transport。所有原始事件、回执、脚本、日志与源码来源在 docs/evidence/v4-trace-p3/。

## 剩余工作

下一步实现严格缓存与计时准入控制，解决完整 payload 哈希、Controller 注册/router 启动/cache gate 的顺序，按已冻结 GLM 字段保留准备成本与失败分母。不能在事后缩短 command/trigger 边界，也不能拿当前日志齐全代替 pool-hit 或 attempt PASS。

实际环境安装/native ABI、完整权重 payload SHA、真实 secure worker RPC、原生 Store 全恢复/bitwise/gold、H20 容量/暂存峰值、服务卸载/再拉起与至少 60 秒物理释放均未验收。正式实验编号未使用。工作仍在 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}，权重按指定路径 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro 原位保留；GLM 原始参考、Store 数据及旧 /data 快照不改动。
