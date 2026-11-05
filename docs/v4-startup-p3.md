# P3 冷启动失败与 readiness CPU 契约

2026-11-06。父提交 `dfe8527108406b02b05b1257107dc8d73e0212a9`；分支 `sllm/v4-raw-store-p3-20261106`。仅修改RoundRobinRouter的inference启动路径，增加stdlib CPU用例；不安装环境、构建native扩展或运行GPU/Ray。

## 具体问题和修复

原_start_instance在await init_backend之前标记InstanceHandle.ready=True，资源分配得到的node_id又直到actor创建成功后才保存；一旦创建RPC失败，原starting owner缺少已知的node信息。_create_instance的Task既未保留，也未消费异常，原日志可见“Task exception was never retrieved”；负载均衡器仍等待ready实例，排队业务不能得知本次启动已经失败。

本次保留启动Task并消费最终异常；成功时移除Task，失败/取消时记录模型级_startup_error。分配确认后立即记录node_id，直接保存start_instance返回的actor handle，减少按名字二次查询窗口。init_backend完成后才在实例池锁内同时标记ready并移入ready池。正常懒加载与请求计时边界保持：没有在Controller注册阶段预先构造模型。

启动失败后，已排队和新进入的推理请求均明确失败；计数finally不把带异常的分配Future当作实例ID。auto-scaler停止为该模型创建/回收实例，显式_create_instance拒绝重试。错误是模型级锁存状态，没有自动clear或重试开关，防止在资源状态未知时继续扩容。本修改作用于共用RoundRobinRouter推理路径；未宣称旧Kimi/GLM完整GPU回归通过。

## 各边界的所有权

| 边界 | 保留状态 | ready/请求/清理契约 |
|---|---|---|
| allocate RPC失败/取消 | instance_id与starting owner；node_id可能未知 | 不创建新actor、不自行退还资源；Task失败被观察，排队请求失败 |
| allocate确认后，actor创建RPC失败 | instance_id、已确认node_id；actor结果可能未知 | starting owner保留，ready=false；不得凭异常认为远端未创建actor |
| actor返回，init_backend失败 | instance_id、node_id、actor handle | starting owner保留，ready=false；Store容量拒绝也从此路径传播 |
| init_backend成功 | node_id、actor handle与ready池owner | ready=true，Task成功退休，原推理路径继续 |
| 失败后的shutdown | starting owner与错误 | 现有quiescent guard拒绝成功删除，Controller保留注册记录；不误报物理释放 |

没有实现未知RPC结果的自动rollback、actor终止确认、scheduler reconciliation或强制清理。仅删除starting字典项或调用deallocate可能把仍占用的GPU重新开放，因此本批明确保留失败owner，等待未来物理清理阶段处理。取消Task也不等于取消远端RPC或杀死vLLM子进程。

## CPU验证及证据

原8项启动用例在旧源码上1通过、7失败；before Windows日志包含旧未消费Task异常，不修改原始输出。最终10项启动CPU用例在本机及qjhs002均通过；另在qjhs002复测9项路由、9项已有native-ready lifecycle，全部通过。本轮是10+9+9=28项Linux CPU检查，分别运行并保留日志；不是一次与历史41项Store/loader/backend合并的全套运行。

测试执行实际router AST及asyncio，RPC/InstanceHandle受控。覆盖ready发布顺序、初始化与创建失败、分配未知结果、启动Task成功/失败/取消、排队请求失败与计数、停止自动重试，以及失败后shutdown不得丢弃owner。它不证明Ray调用真实传播、native backend资源回收或Store正确性。证据位于 `docs/evidence/v4-startup-p3/`。

## 后续门槛

实验控制层必须把本次启动失败保留为失败attempt，记录首次失败阶段与完整stderr；不得将排队异常当业务完成，或删除这次失败后重试并覆盖原run ID。业务成功/EOS、固定32token warm、60000ms以上释放观察等冻结规则未改变。本批未增加GLM phase trace、外部watchdog或实验runner。

完整V4-Pro单slab TP8 Store仍由容量guard拒绝；后续表示路线仍待用户选择。未知资源回收、启动卡住而非抛错的watchdog、Controller注册并发、真实Ray/worker/IPC/quarantine释放、完整Store恢复、再次拉起和GPU业务验收仍未完成。不得将这一轮失败显式化称为完整冷启动生命周期成功。
