# V4 Store P3 CPU 契约里程碑

2026-11-06。用户授权独立分支实现与CPU契约验证，不安装推理环境、不运行GPU。基点为Kimi工作树的tracked commit `89ced92416f8342a03c3df83670249cd626ac0d2`；原工作树的未跟踪文件和现存ELF未复制。独立服务器目录为 `qjhs002:/data/brick1/boyjiang/sllm-v4-p3-20261106`，分支 `sllm/v4-raw-store-p3-20261106`。

**本里程碑不能执行完整V4-Pro的单slab TP8恢复。** 容量复核证明该表示不适配已知H20容量，代码已在模型构造前加入必要下界拒绝。后续表示路线等待用户选择：按专家层分组raw Store、处理后Store，或TP16。没有通过缩小模型、改变精度或删掉专家来绕过目标。

## 1 已实现的接点

| 文件 | 行为与来源 |
|---|---|
| `sllm_store/sllm_store/vllm_v4_loader.py` | 复用GLM后处理前raw导出、参数子类保留、buffer/空张量/alias校验思路。`V4ExportLoader`委托目标DefaultModelLoader加载safetensors，再于原生后处理前保存；`V4StoreLoader`恢复raw后由原生BaseModelLoader执行一次后处理。没有新增core/client/worker专名RPC |
| `v4_state.py` | 纯stdlib identity、tensor/index/文件范围/哈希校验、8 rank汇总和原子COMPLETE发布；拒绝混合conversion、缺scale、坏dtype、非法alias、重复GPU UUID、链接和短写。目录fsync失败撤销新标记。增加单slab容量下界检查 |
| `v4_source_pins.json` 与 setup.py | 绑定目标0.20.1五个关键Python文件的实际SHA，新增两个独立load format和general-plugin入口；不覆盖旧serverless_llm loader。源码指纹不是整个wheel/native ABI验证 |
| `sllm_store/torch.py` | 新增独立 `load_dict_owned`，旧load_dict/nonblocking默认语义保留。V4先建立native storage owner，再暴露IPC；等待确认后才返回state。派发后的未知失败保留owner到quarantine，不提前cudaFree |
| `csrc/checkpoint/checkpoint.cpp` | 增加E4M3FN/E8M0 dtype映射；检查设备选择与分配错误，对部分成功分配执行回滚。existing shared owner RestoreTensors沿用基点修复 |
| `checkpoint_py.cpp` | 新native capability `v4_raw_restore_contract=1`。V4 owned路径拒绝没有此标记的旧ELF，防止把Python新代码误配旧扩展；该标记不是ABI或GPU通过证明 |

GLM参考loader实际文件SHA256为 `36a40e20f8d2d6b2e7b3346ec5e1c54d5598359b1b1756348ef97723e34fa502`，来自qjhs003规范GLM目录。它原先限定GLM/TP8/E8M0-off；这里使用V4独立入口，保留原专家FP4、非专家FP8与UE8M0，未复用该E8M0-off限制。

## 2 固定边界与可复用接口

当前adapter只接受原始Pro配置文件SHA `5fe4568daee51c208cb8a79538eaeda090ae011ade1dee2c386aa95f569c810e`、固定revision `b5968e9190ef611bbf34a7229255be88a0e937c1`、H20/SM90、TP8/EP-off/PP1/DP1、2048上下文、eager单请求、MTP/prefix-cache off。Torch基版本2.11.0与真实local版本字符串须匹配；FI python/cubin0.6.8.post1、TileLang0.1.9、TVM FFI0.1.9也在profile中检查。HF overrides被拒绝。

这不等于完整权重来源已核验；64个权重payload仍须在原生阶段按冻结资产manifest验真。实际Python/native ABI、kernel构建和库解析仍是独立准入项。默认build requirements中的Torch2.9.1不能拿来声称对目标2.11构建；本轮不修改其他模型的全局构建pin，也没有执行安装或CUDA扩展构建。

load format分别为 `sllm_v4_export`、`sllm_v4_store`，extra config仅接受 `store_path` 和 `identity`。原始model参数仍指向同revision的本地HF资产目录，Store根由store_path单独指定。若设置了VLLM_PLUGINS白名单，必须包含 `sllm_v4_store`；没有安装插件/运行真实worker，本轮不声称原生spawn发现已通过。

raw导出的rank目录独占创建。所有export workers成功返回后，协调者才能以同一identity调用 `publish_complete(root, identity)`；它校验全部rank、payload/hash和坐标再发布COMPLETE。正式冷缓存准备前调用 `validate_complete(..., verify_payload=True)`；计时路径只读metadata和文件大小，不重新读取全payload，以免污染冷缓存。**metadata-only不能检出同尺寸payload篡改**，测试明确保留这一边界；后续部署必须用已验证的只读资产mount和原缓存准入控制。

raw tensor归档保持contiguous layout，完全相同view以alias记录；不同offset/shape的重叠view显式拒绝。包含registered parameters与全部registered buffers，并记录buffer是否persistent。plain派生Tensor、quant method/kernel对象由fresh构造和一次原生后处理生成；本轮没有真实全模型反射清单，不能称所有V4对象已经动态覆盖。

`_sllm_v4_stage=postprocessed`只表示该loader返回前完成了后处理，不等于cache/预热/服务已经ready。`_sllm_storage_owners`保活raw分配，正是当前单slab表示容量矛盾的一部分；不能为了通过显存预算直接删掉所有权而忽略native引用。grouped表示须同时解决分组释放和引用生命周期。

## 3 已证实的容量矛盾

固定配置E=384、H=7168、I_local=3072/8=384，共61个主层；源码每个decoder构造DeepseekV4MoE。Marlin默认BF16激活下，raw MXFP4权重及scales和转换后的packed专家参数具有相同字节计数。每rank专家raw：

`61 × 384 × [2×384×(7168/2) + 7168×(384/2) + 2×384×(7168/32) + 7168×(384/32)]`

=102,756,777,984 bytes=95.699707GiB。保留raw slab且构造转换后专家参数时，**仅专家两份**下界为205,513,555,968 bytes=191.399414GiB/rank。既有硬件清单143,771MiB=140.401367GiB/卡；八rank下界1531.195312GiB已超过八卡名义1123.210938GiB。非专家、mHC、缓存、通信、workspace及转换临时都未计入，不能用49B激活参数量估算驻留。

这是源码/配置/表示的条件式下界，不是GPU OOM实测。`check_monolithic_capacity`读取实际raw manifest中canonical bytes和实际Mxfp4MoEMethod/Marlin四参数计数，超过实际device total_memory即拒绝，发生在BaseModelLoader模型构造前；小于整卡总量只是不触发这个必要下界，绝不代表空闲容量足够。CPU小fixture能通过不意味着完整模型能通过。

推荐后续按专家层分组raw allocation：只让一层的raw专家状态在该层后处理完成后具备独立释放条件，其他权重组独立持有。代价是更多Store标识、IPC与confirm调用，必须重新冻结pool-hit完整驻留集合及分组计时。处理后Store可免在线重排，但需拆解对象重建；TP16须另核跨机拓扑。上述路线尚未实施，也没有自动切换。

## 4 失败所有权与停止条件

V4 owned路径先要求所选native capability；native分配成功后以RestoreTensors建立shared ownership，再取handles/派发。在派发前失败时，没有服务端GPU借用；派发后RPC false/raise、confirm false/raise不能证明IPC关闭，保留state owners至quarantine并传播异常，禁止发布模型就绪。`owned_load_quarantine()`提供未决replica信息，不提供盲目清空/free入口。

quarantine意味着实例必须隔离并由本任务两端进程/IPC清理协议处理，不能长期当作可用服务复用。当前没有实现服务端取消与可证明的quarantine排空，也没有60秒物理释放证据；它是fail-closed的CPU契约，不是完整恢复/卸载成功。旧默认load_dict路径的历史协议保持原样，不能将V4新路径语义泛化给所有现存调用者。

## 5 本轮验证

服务器使用现有Python3.10.12和现有C++编译器。未安装包、启动容器/Store/Ray/model、导入真实Torch/vLLM/native扩展或调用GPU。C++测试只编译提取出的AllocateCudaMemory函数，链接的是CPU stand-ins，未构建CUDA扩展。

| 验证 | 实际结果 | 证明边界 |
|---|---|---|
| 新增V4测试 | 27/27 | 实际metadata代码、adapter和固定0.20.1 BaseModelLoader/registry函数；Torch、device、传输用替身 |
| 部分分配失败C++测试 | 包含在27项中，通过 | 提取真实函数，注入第二次malloc/set-device失败与正常返回；不是CUDA allocator运行 |
| 旧blocking confirm | 7/7 | 旧公共blocking/nonblocking时序未改变 |
| 旧confirm error | 3/3 | 现有错误传播 |
| 旧event loop | 2/2 | 真实handler的CPU线程/event-loop行为，无grpc/Torch/native导入 |

合计39项CPU检查，不是39项模型或GPU验收。原始日志在 `docs/evidence/v4-raw-store-p3/`。此前实际失败分别发现dtype映射缺失、部分分配不回滚和目录fsync后残留COMPLETE，修正后通过；这些初次失败输出在聊天工具记录中，本文只做摘要，不将重放结果冒称原始日志。

目标BaseModelLoader/registry fixture的body SHA与固定Git commit官方blob核验后的源码一致，测试使用真实函数AST，隔离第三方import。新增native dtype的C++枚举名另对照官方 [Torch2.11 ScalarType](https://github.com/pytorch/pytorch/blob/v2.11.0/c10/core/ScalarType.h) 与 [Torch2.7 ScalarType](https://github.com/pytorch/pytorch/blob/v2.7.0/c10/core/ScalarType.h) 头文件；该源码存在性不替代完整扩展编译。

## 6 尚未完成

- 解决完整Pro在TP8下的Store表示与释放粒度，继续实现用户选择的路线；当前单slab被拒绝。
- 真实全模型parameters/buffers/plain状态、backend选中日志与布局/alias覆盖，及原生token/业务金标。
- 原生SLLM backend的V4编码/取消/shutdown、Controller/router协议和请求排空；本里程碑未改这些文件。
- 目标native构建/ABI、真实spawn/plugin/RPC、完整Store转换与恢复、pool驻留准入、卸载/再次拉起及至少60秒释放观察。

用户仍限制不安装推理环境、不运行GPU。本里程碑完成可复核的CPU源码工作，核心迁移和生命周期目标保持未完成。
