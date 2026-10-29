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
5. native backing storage 可由多个 Tensor 共享整块 allocation。CPU helper 要求每个 tensor 拥有完整 storage，不能直接用于该布局；CUDA 消费者需明确验证每个实际区间、重叠、别名和所有者，不能删掉检查以迁就它。
6. 以有界 D2H 块在计时之外验证每 rank 的全部 packed/scale/必要 buffer，保留同步及内存峰值证据；后处理前后分开，不能把 Marlin 浮点 scale 变化视为单纯重排。失败目标整体作废。
7. Store 分配、目标初始化、后处理额外显存可能同时驻留，须预算并验证。完成数据消费、一次后处理和独立八rank/业务正确性之后，才可进入原生服务验收。

## 当前执行限制

用户已允许空闲时借用 GPU，不再就是否开跑重复询问。2026-10-30 02:22 快照八卡为 0 MiB，但共享磁盘仍有读取；本轮预约查询 50 秒超时，没有核实新窗口。当前技术缺项仍是 CUDA 消费者及完整运行方案。实施、源码/预算复审、实际资源与授权窗口核验、新独立守护及 probe 均通过后，才启动有界非正式验证。旧 GLM 40 ID、金标及失败不变。
