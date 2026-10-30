# 原生 Store 消费接点：实现前边界

2026-10-30。以下基于冻结 SLLM commit `9f50241baa5386e06a9321c51f19a9ef5f964c2b` 的 `fixtures/native_torch.py`，SHA `a4fb54b68a3895bf6015cbefe3bc7a7fd51e0af4f37cda72d1b49c9fabce29e9`。这是接口分析，不是已完成的 CUDA 消费者或运行准入。

## 已核验的 Python 协议

`load_dict_non_blocking` 先请求 Store `load_into_cpu(model_path)`，读取索引，展开 device map，分配 CUDA backing storage/IPC handles，再请求 `load_into_gpu`。`restore_tensors` 用物理 shape/stride/dtype、CUDA 地址和每张量 offset 构造返回字典；返回 `(replica_uuid, state_dict)`，自身不调用完成确认。

`load_dict` 调用以上函数后，向 Store 调用 `confirm_model_loaded(model_path, replica_uuid)`，再返回字典。固定 Python 包装器没有判断确认返回值。不能仅凭返回字典或此调用已发生，就声称异步数据已验证、模型就绪或业务通过；后续消费者必须核验真实确认/同步语义及逐张量值。

`test_native_load_protocol.py` 执行固定源函数 AST，以 Mock 替换客户端、设备规划和 CUDA 边界：检查物理索引到恢复函数的参数、replica/IPC 传递、两种返回形式，以及 CPU/GPU 请求失败时不执行恢复。还明确保留“确认返回 false 仍返回字典”的现有行为证据。测试没有访问服务器、导入 native 扩展或构造 CUDA 张量。

## 接入前必须补齐

1. 独立绑定完整侧车 SHA、配置、rank、角色、codec 和后处理阶段，在 native 调用/分配之前拒绝不匹配。当前 `read_index` 的 CPU 全payload验证属于正确性成本，不混入加载计时。
2. 每个 TP rank 只消费自己的归档，device map 约束为该 rank 的单一实际设备。固定 `native_utils.py` 的去重字典按 `(源offset, size)` 全局维护，不能未经分析将同一 alias 分配到多个设备；已有 CPU alias/offset 测试只证明同设备路径。
3. native 返回的是物理编码张量。complex cache 必须在登记前从 INT32 原位视图解码；CPU decoder 的通过不能替代 CUDA dtype-view、offset、storage 生命周期和同步验证。
4. 保留目标 Parameter 对象、子类及 loader 属性，只替换存储；buffer 按完整 manifest 恢复持久性、grad 及允许的缺席名称。不要对 packed INT4/scale 做统一 float cast。
5. 进一步核对固定 C++ `checkpoint.cpp`（SHA `eb731fab981dae01d752e0cd3a4539cbffeae415d50eabb030d0acb9f3de8099`）后修正原说明：各张量以各自偏移地址 `from_blob` 包装，并非统一暴露整块 PyTorch storage。底层仍是共同 allocation；同地址 alias 也可有不同 Storage 对象，所以要检查实际区间/地址，不能只比 Storage 身份。仅首个偏移为零的包装器持有 `cudaFree(base)` deleter，其他包装器的 deleter 为空。若后处理替换拥有者，其他张量可提前失效；必须保留整个 native 返回字典中所有 Tensor 的强引用，直到模型销毁且无外部借用。此项是源码风险分析，尚未做 CUDA 实测。
6. 以有界 D2H 块在计时之外验证每 rank 的全部 packed/scale/必要 buffer，保留同步及内存峰值证据；后处理前后分开，不能把 Marlin 浮点 scale 变化视为单纯重排。失败目标整体作废。
7. Store 分配、目标初始化、后处理额外显存可能同时驻留，须预算并验证。完成数据消费、一次后处理和独立八rank/业务正确性之后，才可进入原生服务验收。

## 区间及寿命候选

`native_spans.validate_native_spans` 仅元数据检查：显式单设备、同次分配的 base/bytes/offsets、全部名称/shape/stride/dtype、实际 data_ptr、越界/对齐、显式 alias 与未声明重叠。allocation及offset记录必须来自同次实际 native 调用；当前原生公共接口不返回这些记录，仍需受审接线，不能用任意声明替代。零字节张量没有可读取地址，不据其 data_ptr 认定区间。receipt 的 payload、ownership、native、runtime 标志均为 false。

`retain_native_allocation` 在模型私有属性上保留所有返回 Tensor 的引用元组，不复制 payload，也不只挑选推测的 owner。CPU weakref 测试验证外部字典清空和 Parameter 替换后仍保留 Tensor，模型销毁后释放。它尚未接入真实 native 消费者，不证明 C++ deleter/CUDA 回收；不得提前删除锚或让外部张量借用活过模型。该私有容器将被现有 inventory 视为额外 Tensor 状态；恢复态再次导出仍应拒绝，直到有单独受审政策，不能静默跳过。

本轮七项小 CPU 测试，总计124项通过；bytearray/frombuffer 夹具只模拟共同 allocation 上的不同 Storage 地址，未运行 native 扩展或 GPU。

## 返回张量的校验与安装候选

`native_payload.py` 对已返回、静止的张量做原始uint8视图，再以最多8MiB的块同步复制到主机并计算逐tensor SHA。即使是CPU夹具也走独立有界副本；不解包、不数值cast、不累积完整CPU状态，alias按已验证的共同区间去重复制但保留全部名称的摘要。消费者不得保留块；块上限不是RSS或CUDA allocation总上限。CPU weakref测试验证前块在下一次yield前释放；实际D2H同步/显存和锁页峰值仍待GPU验证。

`native_install.install_native_state` 组合外部侧车SHA、原有配置/索引/payload校验、rank/阶段、完整角色、原始buffer编码、目标清单与aux receipt、native区间及实际返回值摘要。全部完成并预先构造解码视图后才建立模型引用锚和修改参数/buffer；保留Parameter对象/属性，按manifest恢复非持久buffer，仅允许特定lazy缓存补登记。修改失败目标整体作废，不回滚。默认无checkpoint contract时仍是组件覆盖；传入冻结完整contract才校验其名称范围。

inventory默认CPU行为保留，新增显式indexed CUDA设备的元数据匹配入口，拒绝混设备；未执行GPU分支。原生allocation记录仍必须来自同次调用且经过调用方验证，源码/实际ABI、完成确认及生命周期入口尚未接齐。当前安装器不调用native loader，不执行后处理，也不发布路由。`native_loader_verified`、`runtime_admitted`与opaque policy均保持false。

本轮五项payload与五项组合安装CPU测试，总计134项通过，覆盖混合位模式、alias、独立frombuffer包装、参数属性、缺席complex缓存、错误rank/角色/隐藏状态/损坏payload写前拒绝及持有锚禁止覆盖。不是完整checkpoint转换、CUDA模型或服务证据。

## 同次 native 调用记录候选

`native_call.load_native_return` 绑定固定 `load_dict_non_blocking`、device-map、native utils和client源码/函数code。使用私有globals执行固定函数，替换的仅是本次client工厂、分配/restore记录器及已认证索引读取；不改原模块globals、其他client或原生内核。记录从同一次实际allocator返回的PyCapsule取得base，保留原capsule传给native接口，绑定本次bytes/offsets及replica。先核验路径、侧车、rank/阶段、完整角色与allocation上限，RPC带剩余预算。

新增完整client原件 `fixtures/native_client.py` SHA `e33fb8ca82969dada13ea4e2905530ec6e9741f83dfc6fc33ca067d750ab04c4`；device-map原件 SHA `d15e8888b8c04230f958d71c2f731acdaed97ea4d78dc1a2c7b0f4ec7238c4de`，均从本地Git对象的固定commit提取，保留许可证。client成功确认返回None，失败False，不能把 `if not client.confirm_model_loaded(...)` 当成功判断。候选直接检查有deadline的 `ConfirmModel` RPC响应model_path，在确认后才调用native restore构造deleter拥有者；不修改共享client实现。

这与旧包装器“先restore后confirm”的顺序不同，是需实际验证的新修订。分配入口返回 `void*` 的pybind接口，CPU Torch捆绑header的caster返回PyCapsule；本轮用真实CPython capsule模拟该包装，不把整数假接口当真实ABI。实际远端pybind/ABI、同步与memoryhandles仍未验证。

失败抛出 `NativeCallFailure`，保留阶段、raw分配信息、replica、已构造Tensor引用及cleanup未验证状态；**没有自动cudaFree或清除Store池**。尤其原native allocator后、restore前失败时没有Tensor owner，需受守护worker/context与本实验Store的受控退出和取证。RPC超时不证明服务端DMA停止，不能立即释放地址或复用失败进程。该候选不提供进程隔离/守护或服务端取消，尚不能作为完整运行入口。自有RPC channel会关闭，关闭错误保留并要求worker清理；不伪造释放成功。

本轮五项协议/边界CPU测试，总计139项通过，执行固定Python代码、真实capsule和小CPU frombuffer，client stub、allocator、IPC、restore均为夹具；确认先于包装、失败不伪造cleanup、预检预算拒绝和原globals不变有证据。尚未将此调用与安装器接入实际vLLM/GPU生命周期。

清理标志分别登记：`worker_cleanup_required`描述分配/worker风险，`store_request_attempted`与`store_cleanup_required`在发出首个加载RPC前置真。即使CPU请求失败、尚无GPU分配，Store也可能已有副作用；不能把worker标志为false解释为Store没有加载或资源已释放。调用方需隔离并核验本实验Store的退出，不对共享/他人Store执行卸载。

## CUDA rank 准备的源代码候选

接生命周期前发现原 `normalize_rank_cpu_auxiliary` / `_prepare_cpu_auxiliary` 必须拒绝CUDA，不能直接复用为GPU入口。保留CPU公开入口及其描述器，提取全60层预检/变更编排与固定Marlin源码分支；另增 `normalize_rank_cuda_auxiliary` / `_prepare_cuda_auxiliary`，要求显式 `cuda:N`，每个张量元数据必须匹配同设备。仍仅支持冻结Marlin/no-actorder及固定profile，不外推Flashinfer。

`device_state.py` 描述初始化前置状态的全owned storage、canonical stride、原始dtype和显式complex编码；只有零复制dtype视图，没有D2H、数值cast或完整CPU副本。它不用于native返回的任意区间，不能替代native_spans。CPU路径继续用旧encode_cpu_state/tensor_layout，既有139项测试保留；新增五项CPU测试验证新描述器与CPU旧描述器的原位地址/metadata一致、拒绝不支持view/dtype/codec，并验证CUDA入口遇CPU张量时在任何GPU分配之前拒绝且不改绑定。

共144项CPU测试通过，CUDA成功分支及GPU空aux创建尚未执行。不能以拒绝测试或CPU同构测试声称真实GPU准备已验证。下一步将这些新入口与native_call/native_install及固定vLLM Base生命周期组合；worker隔离、失败退出和运行准入仍待完成。

## 固定 vLLM 生命周期组合

`vllm_native_hook.make_native_store_loader` 先验证配置、完整目标契约、外部侧车SHA、rank/阶段和源归档分配预算，再构造独立Base子类，不注册插件或修改global。模型构造前检查显式CUDA设备与平台、TP8/PP1、禁EP/sequence-parallel MoE、MLA、encoder TP=data、compressed-tensors/BF16及关闭MTP；实际通信组和CUDA ABI仍须新worker准入核验。

weight阶段检查全部已初始化张量设备，调用新的CUDA rank规范化、native_call，再将同次分配记录传给native_install。安装完释放loader自身的状态字典引用，模型锚继续保留原owner；之后由固定Base/utility执行一次量化与Attention/MLA后处理。每个loader只能尝试一次，初始化/加载/后处理异常均进入失败生命周期；native失败的receipt和已有Tensor引用保留在loader供受控退出，失败后禁止重试同实例。

该工厂仍不是可运行worker或服务：无守护/window授权验证、进程隔离、Store停止/证明、运行gate或路由发布。`discard_worker_required`从进入生命周期开始保持true，返回模型也不表示资源已释放或整个部署通过；load_native的timeout仅覆盖其调用预算，模型初始化和后处理须纳入外部整体截止。拒绝项不得通过关闭检查赶进度。

六项新增CPU测试，总计150项通过。成功控制测试执行固定Base/utility源码，但明确Mock模型设备/peak APIs、名称边界、rank准备、native调用和安装；只证明调用顺序、一次后处理与生命周期状态，不证明真实CUDA数据消费。独立CPU组件测试覆盖安装/字节，实际设备拒绝测试未绕检查。待新worker完整审阅后才能进行真实八rank验证。

## 源 rank 的有界导出及 SF 接线

`device_export.write_device_state` 将已加载、静止的完整owned rank状态编码为原始视图，按最多8MiB主机块写入既有stream_writer；不累积完整CPU权重字典。alias只写一次，保留原shape/dtype/stride、角色及complex codec。`export_rank_device` 先检查phase/ABI/块预算/目标已存在，按设备执行已审aux规范化，随后写入、读回核SHA并最后发布索引；失败留partial目录，不覆盖或自动重试。仅收集元数据和Tensor引用，不对整份权重做CPU克隆；调用方必须保证源静止并验证实际CUDA同步。

`checkpoint_export.convert_checkpoint_rank` 接受由独立worker初始化的真实模型和DefaultModelLoader，执行本地官方SF加载、全rank规范化、流式导出及发布；自身不构造模型、不执行后处理/推理、不启动worker。转换预算/元数据先检；成功/失败一次性状态保留在模型属性，失败目标不可复用。

固定DefaultModelLoader对量化模型不检查完整loaded_weights，故源加载桥接显式跟踪全部208550个源名与冻结目标契约。默认SF loader和Kimi load_weights仍用原函数，临时截获仅本实例方法并在finally恢复；不改global/backend，不解包或重新量化。全部官方源名须恰好消费一次，返回集合须覆盖目标；仅允许单线程lazy Safetensors、固定revision和主源，metadata准备只返回已认证index列出的64个本地分片，不调用下载fallback或次级模型源。

完整源原件 `fixtures/vllm_default_loader.py` SHA `e7252bc755341559c568bcc933225bd8ca855124fb553059b430000174a9feaa` 和 `fixtures/vllm_kimi_k25.py` SHA `72541f4debfb3d9642884d4e4d86dccecbec0ddf187727d7e96a4639c74f69ba` 来自固定vLLM v0.19.1，保留许可证。config/index原字节身份受冻结SHA约束；本桥接不重新hash全部源权重文件，`checkpoint_payload_verified=false`，来源完整性仍需已有下载证据及实际运行时冻结/核验。文件系统是否本地盘、当前占用和权限仍由worker准入验证，不从“路径本地”推断物理I/O来源。

原始buffer编码不增加整份CPU模型副本，但名字集合/模型元数据、迭代器/页缓存、writer和GPU源权重都另占资源；8MiB仅是一个host payload块的上限，不是rank或整机RSS上限。返回的transfer/source版本与覆盖证据必须由worker保存并绑定manifest；不能仅保存原生索引而丢掉来源证据。

新增九项CPU测试，总计159项通过：小型混合dtype状态实际流式写/发布/恢复，codec/alias、块引用释放、失败无索引；固定Default/Kimi函数执行但SF迭代器、AutoWeightsLoader、官方manifest/目标边界为明确fixture，验证原始packed/BF16位、遗漏/重复/错误target拒绝、方法恢复和无后处理的一次组件转换。不是595GB转换或真实vLLM/八rank通过。下一步是完整独立worker/守护/监控/预算和失败退出取证方案，再经审阅及实际资源核验运行。

## worker 接线与实际预算缺口

最新 `vllm_export_hook.py` 将转换接到固定Base weight边界，再由Base执行一次后处理；`loader_registry.py`复用官方注册API，使用独立export/native格式且拒绝外来覆盖。构造前默认获取已建立的TP/PP上下文及当前CUDA设备；转换只复制一份load config供原Default loader使用，保持engine原自定义格式不变。CPU成功控制仍mock设备/context/转换，实际多进程插件传播尚未部署。

同时发现当前完整CUDA目标再native分配的双份驻留存在静态预算缺口；native工厂新增必需的device_memory_budget_bytes，并在初始化前用checkpoint-backed归档字节的双份下界拒绝超限。基于冻结配置、header统计及MLA复制源码的条件下界已超过历史H20容量，详见WORKER-PLAN.md及native-memory-lower-bound.json。CUDA native初始化策略必须先整改，不能以旧factory测试通过宣称可在该卡运行；这不改变或解释历史GLM失败。

共167项CPU测试通过，包括导出/后处理顺序、rank上下文拒绝、注册冲突、预算写前拒绝及源码MLA复制分支。实际backend构造、CUDA峰值和完整worker准入均未通过。

## 逐记录寿命整改的组件步骤

CPU初始化不足以证明旧single-allocation在重排期间的峰值；新增record_allocations与独立payload/install组件，按canonical记录分别包装owner，alias共享同一StorageImpl。旧路径仍保留模型全state锚，新路径只在同次typed结果/索引/address/alias验证后依赖各绑定的Storage持有者，不增加全state锚。175项CPU测试通过，当前复审见record-allocation-review.json。

目前没有逐记录native allocator/RPC producer或混设备工厂，不能据此运行Kimi或声称显存问题已实际解决。相关C++传输区间已与固定Git原件比较，归档host补丁与IPC关闭错误语义已列明，详见ALLOCATION-LIFETIME.md及independent-allocation-source.json。原native工厂预算拒绝与全部历史证据保持。

## 当前执行限制

用户已允许空闲时借用 GPU，不再就是否开跑重复询问。2026-10-30 02:22 快照八卡为 0 MiB，但共享磁盘仍有读取；本轮预约查询 50 秒超时，没有核实新窗口。当前技术缺项仍是 CUDA 消费者及完整运行方案。实施、源码/预算复审、实际资源与授权窗口核验、新独立守护及 probe 均通过后，才启动有界非正式验证。旧 GLM 40 ID、金标及失败不变。

## 2026-10-31 Store元数据读取边界

在原native_index.py内统一read_metadata：仅接受config.json（64KiB）、tensor_index.json和kimi_raw_state.json（各16MiB），以实际fd的普通文件类型核验和平台支持的NONBLOCK/NOFOLLOW打开，最多读取上限加1字节后拒绝超限；finally关闭fd。native_index、CPU恢复/安装、原单块native调用与安装、逐记录调用、native/mixed工厂均复用该读取，原摘要、内容和量化校验保留。发布器在任何config/sidecar/index发布之前检查三个序列化结果同一上限，超限不产生元数据或commit marker；已写载荷保留供取证，不自动删除。

旧read_index在有效JSON侧车附加空白至16MiB+1后仍接受，已先用真实小归档回归复现，再修正为payload核验前拒绝。测试还记录每次read的上限加1字节、精确边界/超限、真实NUL设备fd拒绝及关闭、未知文件名不open、各元数据超限写前拒绝。全套312项中309通过、3个POSIX FIFO在Windows跳过（29.040秒）；当前源码静态审阅及完整哈希见native-record-call-review.json最新追加记录。

这是raw metadata读取上限，不是JSON对象/序列化、源模型、Store池、CUDA或整个进程的峰值上限。read_index在解析后释放原始字节临时引用，但调用方仍持有必要索引/侧车；未消除所有重复解析。此处不读写真实权重，也不授权运行。source_plugin/checkpoint_export的源checkpoint config读取仍需单独检查；Linux FIFO/no-follow、完整模型元数据规模、实际库与峰值须后续运行验证。
