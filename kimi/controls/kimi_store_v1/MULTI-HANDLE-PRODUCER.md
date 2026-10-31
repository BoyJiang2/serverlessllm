# 逐记录多handle加载候选

2026-10-30。`native_record_call.py` 连接已审分配计划、Store客户端、逐记录恢复与独立安装。这里只做本机CPU协议测试，未构建完整原生库、运行GPU或形成可启动worker。

## 调用链与边界

public入口先检查显式rank/device/时间及字节预算、目录边界和侧车SHA，再检查外部绑定的build receipt，随后执行既有索引/完整payload自校验。未编译的native_patch/ledger.json会在payload扫描前被拒绝，不能拿源码ledger当已部署证明。整个public调用共用绝对monotonic deadline，预检/文件校验耗时计入预算；Python不能中止文件或native阻塞，宿主独立守护仍必需。

build receipt要求schema=kimi-independent-native-build-v1、实际compiled_native_store和当前model_source_sha256，记录checkpoint库SHA、Store库路径/SHA；receipt本身须由调用方给出外部SHA绑定。检查器核对固定Python source/code、四个native函数来自同一已导入extension对象及磁盘库hash。它不证明已映射字节、活跃Store进程或窗口/守护；mapped_library_bytes_verified、live_store_process_verified、runtime_admitted均保持false。当前没有可通过该生产绑定的真实build receipt。

core先向本实验拥有的loopback Store请求host加载；每个非空canonical记录调用原生allocator一次，记录真实PyCapsule地址，检查对齐/重叠，再由既有scalar get_cuda_memory_handles获得64字节IPC handle。零字节scratch不分配。所有handle装入一次load_into_gpu请求，copy chunk保留源文件offset、目标offset为0、handle_idx指向对应分配；alias不重复写或分配。

显式ConfirmModel响应通过后，按每个canonical单独恢复owner，alias共享同一Tensor，返回IndependentRecordState及同次分配收据。后续安装器可验证全部摘要并依赖各StorageImpl，而非全raw模型锚。该路径直接复用原生Store低层API，**没有调用原load_dict入口**，receipt明确load_dict_entry_used=false；不能冒充旧single-allocation路径或宣称等价服务时延。

请求预算包括host请求、分配、IPC、GPU请求、确认及恢复；所有RPC均透传剩余deadline。失败保留阶段、分配尝试/地址/字节、replica、已构造state、Store与worker各自清理义务，不cudaFree或卸载共享Store。RPC超时不证明服务端DMA停止，关闭错误传播需要实际补丁库；失败worker/context须由本run守护受控退出并留证。字节cap只约束请求payload，不含原生分配粒度、上下文、工作区或全主机RSS。

## 本轮验证

新增六项CPU测试，总计183项通过。固定客户端代码在小CPU buffer上执行真实protobuf形状的序列化夹具：4个独立真实PyCapsule、4个handle、一次确认后4次canonical包装，随后真实独立payload/install验证packed/BF16位。allocator/IPC/transport/restore是明确CPU回调，不是实际CUDA或gRPC服务。

覆盖CPU请求、部分分配、重叠、GPU请求、错误确认、模拟补丁关闭错误RPC和包装失败，均保留清理义务且确认前不构造owner；预算/非loopback拒绝发生在任何native请求前。public入口单独验证未编译ledger不能授权、预检超时不进入core。成功的core夹具没有绕过public二进制检查去宣称生产入口通过；public正向真实ABI仍待验证。

## 尚未闭合

新producer尚未接入vllm_native_hook，原native工厂的双份预算拒绝继续保留。接下来须核验/实现CPU文本与官方CUDA视觉/mm_projector混设备初始化、实际库构建/独立审阅/活跃Store身份、释放与峰值probe及完整worker/guard。旧GLM正式数据、金标与40ID不变；不启动R24或更改任何模型/量化/TP来绕开资源问题。

## 2026-10-31 收尾预算修正

旧core在try内return后才finally关闭客户端通道，关闭异常仅记日志，且关闭耗时未计入成功回执。故障回归先在旧源码复现两项未抛异常，现改为通道关闭后统一判定；关闭抛错、超过或恰好达到原绝对deadline均失败，已有RPC/恢复错误保留原stage和cause，关闭错误另记，已构造state仍留给失败清理。public返回边界也检查原总预算。没有主动free、卸载Store或自动重试。

当前九项producer CPU测试通过；全套302项中301通过、1项POSIX FIFO因Windows跳过（29.475秒）。两文件独立静态复审PASS，精确hash和旧失败追加native-record-call-review.json。同步close依然不能由本函数强制中断；此修正不是实际CUDA/IPC释放、原生ABI或宿主守护通过。逐记录producer已有独立mixed-loader组合候选，但未注册切换旧native工厂，也无完整worker准入。

构建回执读取追加硬上限：外部SHA格式先验证，实际fd以nonblocking/no-follow（平台支持时）打开并fstat确认普通文件；最多read(65537)，超过65536字节在JSON/native核验前拒绝，finally关闭fd。64KiB精确边界、非法SHA不open、非普通fd拒绝/实际关闭均有CPU回归；Linux无writer FIFO用例在Windows明确跳过。旧源码超限解析负例已先失败，当前全套306项中304通过、2项平台跳过（27.885秒），当前两文件静态复审PASS。此上限只约束回执读取，不是整个进程RSS或阻塞I/O截止保证；其他侧车/旧入口仍有整文件metadata读取，不能宣称所有元数据已统一有界。

## 最小单卡P0a主体（2026-10-31）

原native_record_call.py已增加run_cuda_ipc_component_probe内部body，独立kimi-p0 run_id/holder/预先绑定GPU UUID，严格Torch2.10.0+cu129/CUDA12.9且只暴露一张卡，没有CPU回退。固定40字节packed I32/BF16/F32特殊位、同对象alias与空张量只作组件控制，不是checkpoint、模型权重或替代金标。spawn消费者经Torch注册的IPC reducer接收CUDA张量，先校原始bits，再写一个packed测试值；父端只有在实际child exitcode=0后读到同一修改才通过，不从子端拷回张量或只信其boolean。生产者保持至消费者退出。

输入同一monotonic原截止、最多300秒worker预算并预留3秒清理，只对自己创建的Process对象有限terminate/kill/join；失败保留owner/receipt交宿主guard处理，不手动free未知地址，不卸载Store。回执含worker命名空间的producer/consumer PID和启动尝试，不能当作宿主NVML PID归属证明。载荷上限最多1MiB只是字节预算，CUDA上下文、allocator缓存和RSS须实际监控；resources_released/runtime_admitted/native_loader_verified/model/Store均保持false，未消费正式ID。

最终343项CPU测试中339通过、4Windows FIFO跳过（31.344秒），两文件当前静态复审PASS。正向父进程使用明确CPU设备/传输替身；真实CPU验证位模式，不能冒称CUDA IPC。此body没有CLI或完整P0 guard scope/监控/60秒观察，不得直接调用绕开准入；旧source/GLM gate也不批准kimi-p0。下一步只补这些最小执行连接和可复用运行时，不等待整模source worker或native完整编译来运行纯CUDA P0a，不将P0a当native P0b通过。

## 本机WSL执行范围

复用现有Ubuntu-22.04 WSL2和同一/mnt/d仓库，无克隆/新环境/权重复制。Linux Python3.10.12无Torch/vLLM/Transformers，故不迁移依赖；已实测从WSL执行现有Windows CPU Python2.10+cpu及同一仓库cwd，相关19项测试18通过1平台跳过（0.674秒），仍是Windows解释器测试而非Linux验证。Linux ssh缺qjhs003配置，现有ssh.exe -G别名可用；WSL原生git status在DrvFS超过10秒，暂用WSL中的现有Windows git.exe写同一index，禁止并发两端写入，不改换行/权限或Git配置。

WSL另报告boyjiang用户systemd会话启动失败，当前仅确认shell、同仓库编辑、Windows工具桥接可用，不据此批准任何WSL服务或将其与qjhs003守护混同；不为此修改WSL全局配置。Git本地写操作可从WSL调用现有git.exe，推送仍保留已验证的Windows凭据路径。


## P0a 内部 worker 命令（2026-10-31，尚非可开跑入口）

现有native_record_call.py增加 `python -B <run>/code/native_record_call.py p0a --registration <run>/registration.json --registration-sha <SHA>`。这只供未来已完成准入的宿主控制器调用，不得手动借旧source/GLM gate启动。先复用专属P0 spec/manifest/window验证，并绑定当前脚本确实位于已核run/code；拒source-model registration。输入验证前记wall/mono，使用原guard fire剩余时间与spec component上限的更短值，验证、证据写入及返回都不重新计时。

仅在既有run根独占创建p0-component-events.jsonl，记录inputs_verified及failed/component_returned元数据；无模型、Store或权重路径。失败日志write/fsync错误记入原异常p0_evidence_error，保留原p0_receipt/p0_owners；成功组件结果在返回超时或终态证据故障时也保留。component_returned只表示组件当时返回，之后证据/进程仍可能失败，未来收集端必须同时核真实worker exit0与独立释放证据，不能按单条日志追认成功。打印的guard_verified/runtime_admitted/resources_released仍false。

21项相关CPU测试20过1Windows跳过，正向CLI的verifier/body是明确mock；最终全套351项347通过4跳过41.230秒。初审自文件绑定/错误覆盖/成功终态fsync缺口先用负例复现并修复，最终两文件只读复审PASS与hash、所有失败追加native-record-call-review.json。一次完整suite在旧Windows orphan-child夹具删除child.err时WinError32，实际进程列表已无Python，后续完整suite通过，旧失败保留。以上不是真实CUDA、独立宿主守护、监控或60秒释放通过。


### P0 worker 创建前输入覆盖（2026-10-31）

上节host registration参数已被新worker-input接口替代，原因是registration包含创建后CID，不能预先放进Docker create Cmd。当前命令只读p0-worker-input.json及外部SHA，schema为kimi-p0-worker-input-v1，四字段包含原plan SHA、原batch anchor及完整环境SHA。实际code/spec/window仍全部核验，自文件/原预算/错误owner及receipt保存行为保持。host target v2另核同输入SHA、plan、anchor和ENV；纯创建argv不引用CID，不执行Docker。详见SOURCE-SCHEDULE.md最新覆盖。原生Store代码与保守2x预算停止不变，没有实际CUDA/Store/完整Kimi通过。
