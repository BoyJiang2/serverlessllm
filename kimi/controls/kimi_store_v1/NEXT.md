# Kimi CPU接续断点

2026-10-29。保持当前goal，不新建/重置阶段。原GLM40ID、金标、结果冻结；不启R24，不恢复取消的今晚探索脚本。

## 最新覆盖

- 新增source_schedule完整阶段预算与code/spec/plan CLI预检，246项CPU测试通过，复审见source-schedule-review.json。包含3721>3600拒绝、60秒最小观察+开销、每批自身guard/清理截止（不随11h预约拉长）、核验耗时计入且不重置；CLI时钟不可覆盖。仅时间配置预检，booking/guard/runtime均false，实际worker/独立守护仍待接齐。详见SOURCE-SCHEDULE.md。

- 仓库已按用户要求迁至GitHub BoyJiang2/serverlessllm，后续本机工作区D:/Documents/codex/2026-10-24/new-chat/serverlessllm，Kimi位于kimi/controls/kimi_store_v1，GLM位于glm/。重组代码57db396、复审0ba1c50：2861旧文件映射，2854字节未变，2605 GLM冻结文件逐字节一致，7处必要路径/属性修改；旧checkout和服务器资产未移动。235 Kimi CPU、10下载、14 GLM历史、6 C23回归通过；初迁移3个测试fixture缺失已修正留证。旧包/审阅不审批新运行。
- 新目录候选0ba1c50已实际成包169文件，真实code/run-spec门禁+标准entrypoint发现+固定registry源码在fresh CPU进程通过，缓存坏bundle SHA在runtime前拒绝；见source-startup-cpu-proof.json。runtime和模型factory明确mock，未执行vLLM真正general-plugin或模型，不能算GPU/完整worker通过。下一项source worker实际身份、完整预算、window/独立guard/probe/monitor及退出取证，仍不启动GPU或全量转换。

- source_plugin现强制在runtime/factory之前verify_source_run（实际插件目录+run固定manifest+外部bundle SHA），cached注册也重查；235项CPU测试通过，复审见source-startup-review.json。旧f152680包冻结，必须按新代码提交成包；下一项新整包CPU接线验证和worker入口/实际身份/预算/guard闭包。尚无GPU/完整Store。

- 实际源码候选已从代码提交f152680生成：165文件、1,094,014B，包内独立CPU校验通过，manifest SHA=b5c41c55d6c6cf7b7f7833c25b3277f340652fde189bd26c0322b0719df067e8。小证据已提交f12a7a3，见source-bundle-materialization.json。候选源码仍冻结f152680；仓库后续文档/证据提交不是新的运行gate。未绑定实际run spec、导入来源/worker/守护或GPU。下一项将verify_source_run接到source启动前并完成实际身份/窗口/预算/guard闭包。

- 新增source_bundle源码对象生成/完整文件校验及source spec/run布局绑定，233项CPU测试通过，初审扫描错误误通过已复现修复，复审见source-bundle-review.json。复用固定SHA的既有Git读取器，blob读取前检查大小；独立CPU CLI已测。尚未接source worker/插件启动，未验证真实整包或GPU；下一项实际候选存证、启动来源/guard/预算与退出闭包。详见SOURCE-BUNDLE.md。

- 新增source_plugin只做export独立注册及私有distribution metadata，220项CPU测试通过，初审读取上限竞态已复现修复，复审见source-plugin-review.json。新CPU子进程已测标准entrypoint发现及spec身份继承，尚未执行真实vLLM general-plugin/TP8 worker。下一项source worker完整manifest/镜像ABI/预算/guard/监控及退出取证，仍不启动GPU。详见SOURCE-PLUGIN.md和RESERVATION-PLAN.md。

- 已用冻结完整索引+64分片header实算memory_floors，211项CPU测试通过，复审见memory-floors-review.json。mixed raw≥71.557GiB/卡，8rank CPU文本≥565.438GiB、Store有效payload≥572.458GiB；不是峰值上限。两类全缓存+CPU目标+pool假设共驻留约2264.656GiB，普通页可回收，不能直接称OOM。旧预算停止保留；下一项source单模型worker/插件与完整资源/构建/守护闭包。详见MEMORY-BUDGET.md。

- 新增vllm_mixed_hook独立loader，把构造/aux/逐记录producer/install/显式CUDA后处理串入冻结Base私有namespace；207项CPU测试通过，初次fixture错误保留，复审见mixed-lifecycle-review.json。仍保留保守2×checkpoint预算停止，真实模型不能据此开跑；完整峰值/主机驻留预算、实际构建身份及worker准入仍待完成。详见MIXED-LIFECYCLE.md。

- 新增initialize_mixed_vllm_rank保留官方quant/config/reload初始化上下文，mixed_postprocess显式最终CUDA target并限制一次后处理；202项CPU测试通过，复审见mixed-context-review.json。初始化/后处理真实CUDA边界仍mock，未组合完整新factory、无GPU运行。下一项连接构造/aux/producer/install及受审全峰值预算与worker。详见MIXED-CONTEXT.md。

- 新增normalize_rank_mixed_auxiliary：CPU文本/官方CUDA视觉设备政策先核验，仅target-before-restore，复用60层全预检及CPU Marlin辅助规则；195项CPU测试通过，复审见mixed-auxiliary-review.json。未连接完整mixed factory、未运行真实CUDA；下一项构造/producer/install组合及Base后处理目标语义。详见MIXED-AUXILIARY.md。

- 新增显式device_map元数据及独立target_device_map安装边界，192项CPU测试通过，复审见mixed-state-review.json；默认CPU结果保持一致，隐藏Tensor仍扫描，地址alias加入device维度。真实混设备/CUDA绑定未验证，CPU Marlin规范化及Base后处理/新工厂接线仍待完成。详见MIXED-STATE.md。

- 新增mixed_device_init.py源绑定混设备构造候选；187项CPU测试通过，复审见mixed-initialization-review.json。只验证CPU上下文/拒绝边界与登记设备政策，未做真实CUDA构造，未接native工厂；完整混设备inventory/aux/install及Base后处理目标语义仍待闭合。详见MIXED-INITIALIZATION.md。

- 最新native_record_call.py已接逐记录allocator→多handle单次Store请求→确认→canonical恢复，183项CPU测试通过，当前复审见native-record-call-review.json。public入口绑定已编译补丁的build receipt和磁盘库身份，未编译ledger在全payload扫描前拒绝；预检计入同一deadline。成功core仅CPU allocator/IPC/RPC/restore回调，public真实ABI未通过，未接原工厂。MULTI-HANDLE-PRODUCER.md列明不能将磁盘hash当已映射/活跃Store证明。
- 下一项接CPU文本/官方CUDA视觉及mm_projector混设备初始化和producer/独立安装，再补实际库构建/worker/守护与释放probe；原两份GPU预算拒绝保留，不直接开跑。

- 新增独立native_patch/model.cpp关闭错误传播候选及实际ToGpu尾部的CPU C++编译故障测试，177项CPU测试通过，当前复审见ipc-close-review.json。旧片段在close/device故障仍报成功的控制已复现，新片段返回INTERRUPTED并在关闭尝试后通知。未构建完整Store、链接CUDA或部署；未改旧归档。下一项多handle producer需绑定该修订的实际库与RPC失败证据，再接混设备初始化及完整worker/guard；不能据Python SHA或布尔确认认定IPC已关闭。

- 新增record_allocations与独立payload/install组件，175项CPU测试通过，当前复审见record-allocation-review.json。查明仅CPU初始化不能覆盖whole native块被锚保留时的重排峰值；新候选按记录canonical独立包装、alias共享Storage owner，CPU weakref证明可逐记录释放而非锚住全raw state。旧single-allocation分支与预算拒绝保留，当前native工厂未改成新模式。
- 下一项补逐记录allocator/多IPC handle/RPC/完成及关闭证据producer，再接CPU文本/官方CUDA视觉和mm_projector的混设备初始化；需同时处理构造与重排寿命，不能只改单个device参数。ALLOCATION-LIFETIME.md列明源依据/未验证项；当前无GPU、守护或完整转换运行。历史exit137仍未知，不与本Kimi预算分析归因。

- 最新vllm_export_hook/loader_registry已接固定Base转换边界与独立格式注册，167项CPU测试通过，当前审阅见worker-preparation-review.json。发现native完整CUDA目标+native块的条件静态下界141.58GiB/卡超历史140.40GiB；新增工厂device_memory_budget_bytes强制初始化前拒绝不足预算，不能沿用旧签名/审阅。详见WORKER-PLAN.md及native-memory-lower-bound.json；不是实际OOM，不解释历史exit137。
- 下一项优先解决native双份驻留的初始化策略（CPU文本/官方CUDA视觉，或目标IPC写入，均须实际源码政策与CPU验证），再补独立worker/plugin传播、守护和监控闭包。当前仅准备了registry/factory，没有部署或启动。源转换/后端参考的一份模型路径仍可继续准备，不把native预算缺口扩写成所有CPU工作阻塞。

- 最新device_export.py/checkpoint_export.py接已初始化源rank的本地SF加载→aux规范化→有界host块写/核验/索引发布，159项CPU测试通过，当前修订审阅见checkpoint-export-review.json。补量化DefaultLoader不做完整目标检查的缺口：源名逐一覆盖、目标集合审计；仅本实例临时方法且finally恢复。完整源代码fixture已冻结；SF迭代器/mapper/全量manifest/target边界仍是小夹具，不是全模型转换，Store资产尚未生成。下一项：完整独立worker的身份/预算/窗口/守护/监控/失败退出取证及执行入口审阅；不自动GPU、不恢复旧窗口。

- 接续优先级澄清：Kimi完整Store尚未生成，不能先调用消费hook。下一轮先将device_state与有界主机块接入GPU源rank导出/原SF loader接线（仅源码与小CPU测试），再组合独立worker/当前窗口守护与取证。当前native factory只是消费候选，不是完整转换器或可运行服务。其已审实现不重写，原phase/目标不重置。

- 新增vllm_native_hook.py及六项CPU生命周期测试，150项CPU测试通过。候选已接构造前source预算/配置预检、CUDA rank准备、native调用与安装，再交固定Base后处理；native_call路径预检提取为共用helper防止两入口边界分叉。当前复审见native-lifecycle-review.json。成功测试明确mock CUDA/framework、rank/native/install边界；不是实际GPU/模型通过。下一项：受守护独立worker、当前run/window/monitor/失败清理完整闭包和实际运行审阅；不能把已有factory当service CLI或复用旧guard。

- 最新device_state.py及显式CUDA rank/Marlin辅助规范化候选已补齐，144项CPU测试通过；旧CPU入口/描述器保留，核心source规则和全60层先预检后变更共用。当前修订审阅见device-preparation-review.json。CPU parity/拒绝测试不是CUDA成功路径或真实模型验证；下一步连接固定vLLM Base生命周期与native_call/install，再补新worker及受控退出协议，不直接执行GPU。

- 最新native_call.py接固定nonblocking函数的私有调用环境，记录同次PyCapsule base/bytes/offsets/replica、带剩余deadline的RPC和显式完成响应；139项CPU测试通过，当前审阅见native-call-review.json。source client成功confirm返回None，候选直接核RPC响应并在restore包装前确认，不沿用旧真值判据。失败明确保留cleanup未验证并要求worker/context受控退出，不主动free潜在DMA地址。下一项：新worker隔离/失败回收协议和vLLM生命周期组合；不能把此夹具调用或旧守护当真实运行准入。

- 新增native_payload.py/native_install.py及小CPU测试，134项通过：已将可信侧车、角色/codec、目标预检、区间/实际字节摘要、引用锚和参数安装组合。测试仍为CPU frombuffer小状态，未调用native/CUDA/全模型；current修订复审见native-install-review.json。runtime_inventory新增显式indexed CUDA元数据设备匹配，默认CPU不变。下一项：固定native分配调用的同次allocation记录/完成确认与失败清理包装，再接已有vLLM生命周期；不能用CPU mock冒充真实调用或绕过新运行审阅。

- 2026-10-30：新增native_spans.py/test_native_spans.py，124项CPU测试通过。校验单次native分配的地址区间/alias、拒绝重叠和越界，并提供模型寿命的全部native Tensor引用保留。读C++后修正NATIVE-RESTORE.md：实际各tensor from_blob独立包装，只有一个offset0包装持cudaFree；不能误称统一大Storage或漏保留owner。当前仅CPU模拟地址/weakref，不是CUDA分配回收证明；未接真实native调用的allocation记录或安装入口。下一步接受信任的同次分配记录及有界payload验证，再组合角色/codec安装，仍需独立运行审阅和新守护。

- 原生协议接点已补充 `NATIVE-RESTORE.md` 与固定源码 AST 的三项 CPU 协议测试，共117项CPU测试通过。客户端/CUDA边界全为Mock，不是native实测；确认返回值被旧Python包装器忽略、CUDA backing storage不能套CPU全storage校验等限制已显式列出。用户已允许空闲借用GPU，但仍须新窗口/资源/独立审阅/守护；下一步是CUDA消费者的区间与别名预检及必要CPU测试，不直接启动尚未实现的模型入口。

- 2026-10-30：有界CPU文件恢复已接角色/complex原始位编码及唯一允许缺席的lazy视觉cache；先验证全部源payload，再分配必要cache，所有copy成功才登记非持久buffer。114项CPU测试通过，含已有alias复用、损坏写前拒绝、copy失败不登记及固定Base生命周期中已有/缺席cache。四文件当前修订复审状态见lazy-restore-review.json；旧审阅不得批准新hash。
- 下一项：依据固定native load_dict返回的CUDA物理张量协议，设计保留Parameter对象/角色/别名的消费者与有界逐tensor校验接点；先做本机小CPU接口测试，不直接把CPU loader改名成GPU loader。完整转换、native CUDA恢复、八rank、后处理payload与业务仍未验证。当前GPU空闲快照不构成整机独占或运行准入，须重新核验有效窗口、共享I/O、独立审阅和新守护。

- 本轮vllm_cpu_hook接固定Base/utility源码与CPU raw restore接点；107项CPU测试及10文件修订静态复审PASS，见vllm-cpu-hook-review.json，不重复派发。真实Base代码测试仍依赖微型框架/initializer与rank/name边界mock，未导入实际vLLM或运行Kimi。
- source与target auxiliary phase已分开；export写前只准source，恢复/安装显式接受target。初审生命周期及target-export晚拒绝两项已修复，失败终态/one-shot回归通过。CPU候选构造前强制CPU platform，qjhs003 CUDA平台不能用它启动模型。
- 下一项：将完整角色/编码/lazy buffer消费者接到文件恢复hook（避免完整state额外副本），并据固定native load_dict返回协议设计GPU候选的安装/分块校验接点；仅代码与小CPU测试，不调用native GPU loader、构造全模型或执行完整转换。实际平台、原生Store/服务和八rank仍未验证。

- 本轮state_bindings角色/持久性/grad清单已接写入/侧车/读取/CPU恢复；install_cpu_loaded_state消费完整CPU状态字典，decode后保留Parameter对象属性、登记buffer，仅特定lazy视觉cache可补登记。100项CPU测试通过，10文件修订复审PASS，见state-install-review.json，不重复派发。
- 初审buffer requires_grad误通过已修复；测试发现inventory递归闭包保留旧Tensor，finally断环并weakref无GC回归通过。旧无角色清单的layout组件不得用于install；编码旧侧车无角色清单将拒绝，不修改旧证据。
- 下一项：检查固定vLLM模型构造与load_weights/process_weights_after_loading调用链，并接最小原生Store恢复hook到现有CPU消费者；先写代码/小组件测试，不调用原生GPU loader或构造全模型。旧通用HF加载器有float统一cast/重建量化，不可直接沿用。实际八卡/完整checkpoint/业务验证未通过。

- 本轮complex64-int32-bits-v1 buffer codec已接CPU写/侧车/读回/指纹/rank包装；92项CPU测试通过。初审alias原始recipe交叉校验缺口已修复；7文件修订只读复审PASS并核验SHA，见buffer-codec-review.json，不重复派发。测试由主线程执行。旧“complex缓存完全不能编码”的历史断点被此更新覆盖；无显式codec仍拒绝。
- 原生load_dict仍返回物理INT32，decode_cpu_buffers仅CPU视图消费者。下一项需把完整buffer类型/登记/持久性及参数角色绑定到真实vLLM/Store恢复入口，确保decode发生在赋值/后处理前；未运行CUDA/native消费者、完整模型或业务，不可声称全适配。

- 本轮runtime_inventory已扫标准嵌套容器并拒绝container_tensor导出，循环/opaque/预算有证据；85CPU测试通过，5文件只读审阅已返回PASS并核验SHA，见vision-inventory-review.json；85项测试由主线程实际执行，不重复派发。
- 视觉真实源码小CPU测试确认lazy freqs_cis为complex64，原生归档C++ dtype表没有该类型，warm视觉缓存仍明确不支持；time_weight必须保留且实际dtype受外层cast影响。见VISION-STATE.md。不要添加Python标签冒充原生兼容或丢弃缓存。
- 下一项：实现可保留complex64完整原位模式的显式buffer编码/恢复侧车及消费者，或最小native dtype补丁，并用真实CPU位模式/别名/损坏负例验证；仍不执行全模型/完整转换。完整opaque政策与真实vLLM/native服务接线也未完。

- 本轮rank_export.py接入60层全预检再变更及CPU export/write/publish编排；78项CPU测试通过；初审canonical-stride预检缺口已修复，新增真实singleton stride负例。修订复审已返回PASS并核验三文件SHA，见rank-export-review.json；78项测试由主线程执行，不重复派发。新60层测试仅小控制流fixture，单层helper/目标边界有mock；真实冻结contract明确拒绝该fixture，不是全模型或转换通过。
- 下一项：梳理并实现视觉非持久buffer/opaque状态的必要性与处理政策，再把真实vLLM构造/加载入口接到CPU导出接口（仅写代码/小测试，不执行全模型）。已存在rank_export不等于已可运行的完整转换器，native load_dict/实际八卡仍待验证。

- 本轮auxiliary_manifest已绑定write_cpu_state/native publish/read与CPU restore来源/目标独立receipt；72项CPU测试通过。当前完整6文件静态审阅已返回PASS并核验SHA，见auxiliary-manifest-review.json；72项测试由主线程实际运行，不重复派发。旧“receipt未绑定”条目为历史断点。
- 下一项：实现整rank 60层的显式处理编排及完整checkpoint目标/状态清单接线，代码可先写、仅小CPU夹具验证；不要构造完整模型/转换。查清视觉必要buffer、opaque及实际vLLM/native接口，当前仍非完整转换器。

- 新增marlin_aux.py/test_marlin_aux.py和完整固定源码fixture：68项CPU测试通过，单层显式Marlin/no-actorder scratch规范化为空、记录before/after且不读旧值。初审方法身份绑定缺口已修复，精确code比对及instance override负例通过；修订复审PASS见marlin-aux-review.json，不重复派发。不是完整转换器，receipt未绑定native sidecar。
- 下一项应将规范化receipt绑定导出/恢复侧车与验证，防止无来源记录导出已改状态；完成60层编排再接真实vLLM接口。不得跳过opaque/视觉buffer政策，也不得把单层CPU源分支测试当实际backend证明。

- 新增cpu_restore.py/test_cpu_restore.py：61项CPU测试通过，有界readinto恢复现有CPU模型，写前全量校验、复制时SHA复核；中途失败destination作废，不回滚。初审发现共享pointer不同layout覆盖问题，已修复并加真实回归；修订复审已返回PASS，两文件SHA核验一致，见cpu-restore-review.json；不重复派发。61项测试由主线程实际运行。不是native load_dict或完整Kimi转换。
- 下一项转向scratch侧车/重建消费者与固定后端ABI接线；保持Flashinfer拒绝外推，所有未知状态不得静默省略。先明确真实构造/恢复各阶段可用的状态及native接口，不把微型CPU恢复当完整适配。

- 本轮新增state_audit.py与test_state_audit.py：55项真实CPU测试通过；分块SHA与同阶段读回比对，跨rank/phase/Torch上下文拒绝。AUX-POLICY.md是未实现的Marlin例外政策，原写入拒绝仍保留。当前只读审阅已返回PASS，三文件哈希核验一致，见state-audit-cpu-review.json；不重复派发。审阅未执行测试，55项测试由主线程实际运行。
- 下一项：真正的有界CPU恢复入口（不仅测试内手工拷贝），复用已验证native index与payload边界、保持alias/混合dtype并验证恢复后的state_audit；然后处理scratch消费者与真实vLLM接线。完整转换器尚未完成。

- 最新checkpoint_contract已接入可选写前检查：50真实CPU测试及只读复审PASS，见checkpoint-contract-cpu-review.json。完整source index重新派生目标artifact逐字节一致，1428条件目标名已硬绑定SHA；审阅已返回，不重复派发。
- 仍不是完整转换器。接下来推进明确的Marlin/no-actorder scratch重建政策与pre/post流式payload指纹；Flashinfer和opaque状态不能套用Marlin规则。权威名称清单不证明shape/数值/实际profile。
- 以下旧“尚未传targets”描述是历史断点：现在传checkpoint_contract时会传targets；不传仍仅组件模式且覆盖flag为false。

- 最新runtime_inventory元数据清单及写入前unsafe拒绝已实现，46真实CPU测试与只读审阅PASS，见runtime-inventory-cpu-review.json。审阅已返回，不重复派发。
- 接下来把权威checkpoint目标清单接入导出前检查（当前write_cpu_state尚未传入targets，不能声称完整性验证），并推进明确的scratch重建政策与后处理payload校验；opaque状态覆盖仍未完成。以下旧步骤为历史断点，以此覆盖为准。

- nonexpert只读审阅已返回PASS并逐hash核验，结果见nonexpert-cpu-review.json；不要重复派发该审阅。42项测试是主线程证据，仍非运行R1。
- 本聊天heartbeat `kimi-cpu-gpu` 已通过应用工具创建并view核对，ACTIVE，每小时仅推进本机CPU工作；不恢复任何GPU定时脚本。以下“尚未审阅”是此前断点，由本覆盖更新。
- 下一步直接推进第2项runtime buffer/scratch清单和后处理边界。

## 当前实际状态

- 上个已审/已push里程碑955dc5e：专家条件映射，38测试。
- 当前候选新增nonexpert_mapping.py、test_nonexpert_mapping.py、fixtures/vllm_linear_loaders.py、nonexpert-name-audit.json。本机42CPU测试通过（0.642秒）；1190个非专家名字全覆盖，条件为MLA、无sequence-parallel MoE、encoder TP data。还不是完整模型TP映射证明。
- 已向现有`/root/independent_readonly_review`发出一次这4个文件的只读审阅，优先收取已返回结果；若仍有活跃agent等待它，不重复派发。新turn若agent不可达，先检查持久review文件/对话返回，不能编造PASS。
- Kimi96文件/64分片已下载并校验完成，禁止重复下载。远端qjhs003资产根/data/brick1/boyjiang/kimi-k2.6/。CPU接续无需SSH。
- 专用测试解释器：`D:/Documents/codex/2026-10-24/new-chat/outputs/kimi-cpu-tests-env/Scripts/python.exe`，真实torch2.10.0+cpu；不修改GLM环境。命令：`<python> -X utf8 -m unittest discover -s controls/kimi_store_v1 -p 'test_*.py' -v`。

## 下一项可验证产出

1. 收尾nonexpert只读审阅，核实发现、必要时修复/加测试；保存准确source hash和结果，更新README。新候选未审前不能标运行ready。
2. 梳理checkpoint之外的runtime parameter/buffer。固定MoE源create_weights会torch.empty创建4个g_idx/sort_indices数组（无actorder时Marlin后处理换成空数组；Flashinfer分支提前return），不能未经政策直接序列化未初始化scratch。视觉源码有time_weight和按需freqs_cis非持久buffer。先建立metadata-only清单与角色/待审项，未知项不得悄悄丢弃。
3. 对接完整rank状态、后处理前后映射与CPU可验证的最小导出/读取入口。仍需原始shape侧车、必要buffer、ABI/布局状态和覆盖率；CPU片段通过不等于完整转换器。

## 固定边界

只做本机CPU源码/小测试/文档和审阅闭环。不启动模型、完整转换、大池、全量缓存或GPU，不争用Claude/alan，不操作全局设置/预约，不联系其他聊天。实际GPU运行另须独立审阅、有效预约、资源核验和新守护。定时任务不构成GPU授权。

保护已有变更，显式stage本任务文件；每个核验里程碑commit/push并ls-remote核验远端SHA。持续有变更30分钟安全提交，无变化不空提交。不要重写历史review：旧review只绑定旧hash。无进展不得只重复计划；如有真实阻塞保留证据，不无限重试。

用户因token不足要求定时接续：仅有意义进展/失败/完成/需决策时通知，无变化安静。调度器不能绕过账户额度、离线或客户端不可用，也不能替代远端实验守护。
