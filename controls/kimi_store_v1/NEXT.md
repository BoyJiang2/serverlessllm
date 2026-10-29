# Kimi CPU接续断点

2026-10-29。保持当前goal，不新建/重置阶段。原GLM40ID、金标、结果冻结；不启R24，不恢复取消的今晚探索脚本。

## 最新覆盖

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
