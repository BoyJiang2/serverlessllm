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

## 当前执行限制

用户已允许空闲时借用 GPU，不再就是否开跑重复询问。2026-10-30 02:22 快照八卡为 0 MiB，但共享磁盘仍有读取；本轮预约查询 50 秒超时，没有核实新窗口。当前技术缺项仍是 CUDA 消费者及完整运行方案。实施、源码/预算复审、实际资源与授权窗口核验、新独立守护及 probe 均通过后，才启动有界非正式验证。旧 GLM 40 ID、金标及失败不变。
