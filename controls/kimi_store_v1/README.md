# Kimi INT4 Store CPU 适配候选

2026-10-29：本目录只有**导出前契约与字节分块规划**，不是可运行的Store导出器或vLLM loader，不提供启动入口。不修改GLM runtime/active。

已实现：

- 原字节校验官方Kimi config SHA及嵌套compressed-tensors量化位置，拒绝其他revision/TP/rank。
- 为候选vLLM0.19.1 / Torch2.10.0（cu129或无local suffix）记录ABI身份。这是计划输入约束，不是二进制镜像已验证声明。
- 对外部审计获得的每rank tensor记录检查shape、dtype、字节数和contiguous状态；只允许I32/BF16/F32，未知状态拒绝并等待审计，不自动转换dtype。
- 对原始storage字节按元素边界规划连续块，最大单块2GiB、默认64MiB；不解包INT4，不创建CPU tensor，不读取模型文件、不写Store。
- 输出明确保留payload_verified=false、tp_mapping_verified=false、store_created=false、runtime_admitted=false。

验证：`python -X utf8 -m unittest discover -s controls/kimi_store_v1 -p test_contract.py -v`。使用微型元数据夹具验证拒绝与分块边界，不使用假权重进行模型推理，也不声称完成八rank权重校验。

补充CPU测试共10项通过：增加packed整数/浮点特殊编码的逐字节与SHA保持、超过6GiB逻辑长度的分块边界（不分配大块内存）、标量/空张量/别名保守计费，以及全部64个官方分片文件头内**已保存样例**的shape/dtype/bytes一致性。样例检查不是208,550个张量全量权重检查，也不是TP8映射证明。另有下载器10项CPU回归通过；本轮没有服务器连接或GPU使用。`cpu-review.json`仍只绑定先前2f772a2版本的6项测试，不追认这次新增测试已获独立复审；生产contract.py未修改。

## 必须继续完成的接线

1. 从冻结backend实际初始化后的rank状态生成完整清单，区分checkpoint参数、必要buffer、别名与scratch。当前I32/BF16/F32集合来自官方checkpoint；实际backend若出现其他dtype，先审查来源再扩展，不能直接删掉。
2. 查明Kimi TP8融合专家、scale、weight_shape侧车映射。计划函数不根据名字猜TP切分，也不猜kernel；当前只校验已提供的记录，不能证明清单完整。
3. 实现GPU tensor逐块复制及同步写入SLLM Store。现有save_dict累积CPU字典或一次性写全tensor的接口，尚未证明支持本规划，不能把每一块当独立模型Store保存后声称可恢复。
4. 每块写完释放临时CPU引用，计算逐tensor流式SHA；同rank只允许一块在途。审计hash临时缓冲、GPU→CPU staging、writer对齐缓冲也占内存，因此2GiB仅是单块上限，**不是rank整体锁页/RSS上限**。进一步给整个进程加经测量的上限再批准运行。
5. 读取端恢复同一初始化前置状态，再严格执行一次backend权重后处理。保存官方配置/原始shape、完整rank映射、布局规则、ABI及派生状态hash。不要重复Marlin repack或将postprocess状态当preprocess。
6. 对I32 packed值、BF16 scales/权重、F32路由参数和所有必要元数据做八rank全量校验；建立新原生金标。现有GLM格式和金标不得借用。

本轮不连接服务器、不读595GB输入、不占GPU/Store池/页缓存；CPU通过后仍需镜像/完整适配审阅、有效资源窗口、独立守护与模型实际验证。先GLM实际验收及释放，再Kimi原生参考→Store正确性→完整服务。

## 原始分区写入候选（后续CPU工作）

`stream_writer.py`新增顺序CPU buffer写入及有界读回SHA校验。参考归档的`controls/candidate13e/evidence/runtime/source-v7/sllm_store/csrc/checkpoint/`中TensorWriter/AlignedBuffer：`tensor.data_N`，记录尾补齐8字节，分区尾补齐4096字节，默认10GiB阈值；与原实现一致，不跨分区拆一个大tensor。分块是同一记录的流式输入，不能对每一块重新加记录padding。

每个输入buffer至多8MiB；调用者仍须避免自己预先生成整份CPU字典，buffer限制不等于进程RSS上限。顺序返回逻辑offset、分区号、分区内offset、长度及逐记录SHA。现阶段不做alias去重，单独重复存储，只会增加预计字节数。新目录独占创建，出错保留不完整目录；没有写入任何native索引、ready标志或成功Store收据，调用者不得把失败目录发布给服务。

CPU测试覆盖记录/分区对齐、非整块输入、特殊原始字节、零长度与单记录超过阈值、SHA错误、截短/超长、重复名称、目标已存在、短写及读回损坏。测试目录使用微型字节夹具，不是模型权重；本轮未调用原生Store扩展或GPU loader，因此只证明此候选的CPU文件布局与字节往返，**不证明原生加载兼容性**。

当前共18项CPU测试通过。复审发现的负长度导致`read(-1)`风险已修复：读文件前要求长度、分区号和局部offset是非布尔非负整数，并用fstat拒绝超文件范围；回归覆盖拒绝发生在文件访问之前。候选把padding清零；native的记录padding未明确清零，因此仅承诺布局和有效payload一致，不能声称padding物理字节与native逐字节相同。

下一步需要核实固定SLLM Python `save_dict/load_dict`索引schema，接入shape/stride/dtype、别名与量化sidecar，然后用原生Store读回该候选。尚未实现上述清单第3项所要求的完整GPU逐块导出/写入连接，不得据此开始全量转换。

## 原生索引CPU闭环

`native_index.py`按固定SLLM commit `9f50241baa5386e06a9321c51f19a9ef5f964c2b`的`save_dict()`生成`tensor_index.json`：`name: [offset, storage_bytes, shape, stride, dtype]`。保留原始官方`config.json`（包括嵌套量化配置），用独立Kimi sidecar绑定rank计划、数据记录和索引SHA。只接受零storage_offset、独占完整storage、canonical contiguous stride；非完整view/重叠别名明确拒绝，不能从contiguous标志推断其拥有底层allocation。

先重算契约并读回数据SHA，检查物理分区和global/local偏移后发布。每个metadata文件使用独占partial+原子hardlink无覆盖发布，索引最后提交。hardlink成功即提交完成；后续临时文件清理失败通过返回值`cleanup_pending`报告，不把已发布误报成未发布。失败保留证据，不自动覆盖/重试。

`read_index()`要求外部可信的sidecar SHA，验证sidecar、官方config、索引和payload，防止仅修改自描述hash后通过。不把“数据与给定记录SHA一致”宣称为已证明来自官方checkpoint或TP映射；这些仍需八rank原生参考。

CPU测试实际从`fixtures/native_torch.py`提取并执行固定源文件的`save_dict()` AST，与候选索引比较。只替换其底层save_tensors回调和微型tensor元数据对象，未运行Torch/CUDA扩展，故**只是原始Python schema一致性，不是原生Store消费验证**。原文件完整保留Apache-2.0声明，SHA `a4fb54b68a3895bf6015cbefe3bc7a7fd51e0af4f37cda72d1b49c9fabce29e9`；来源：`https://raw.githubusercontent.com/ServerlessLLM/ServerlessLLM/9f50241baa5386e06a9321c51f19a9ef5f964c2b/sllm_store/sllm_store/torch.py`。

当前24项CPU测试通过，含索引/sidecar/config/payload损坏、遗漏张量、storage view、错误offset、禁止覆盖、提交后清理失败。原生Store扩展读取、vLLM接线、GPU导出仍未完成。暂不生成模型服务ready或运行批准。

## TP8融合形状源码分析

从已冻结SHA的vLLM v0.19.1 `CompressedTensorsWNA16MarlinMoEMethod.get_weight_shape()`提取纯方法fixture，CPU直接执行两分支。假定纯TP8、无EP、SwiGLU、384专家、hidden7168、每rank intermediate256、group32、packed factor8：

| 参数 | Marlin | Flashinfer |
|---|---|---|
| w13 packed | [384,896,512] | [384,512,896] |
| w2 packed | [384,32,7168] | [384,7168,32] |
| w13 scale | [384,224,512] | [384,512,224] |
| w2 scale | [384,8,7168] | [384,7168,8] |

在I32 packed和BF16 scale假设下，8rank×60MoE层字节和分别507343011840B、63417876480B，与官方checkpoint相应总量吻合；这只验证条件形状和字节守恒，不证明实际选中backend、专家编号/转置/切片顺序或原值映射。完整MoE模块源SHA `0d84c593350f59f3ff2d45beb3b3ef99dc1f155e6b5b17ffc806000e5857b70f`，纯方法fixture SHA `901a3061cdd00510ecd9998a84fa836286acecd30634df48fc502e3c6c4c23e2`。

源码还会创建融合后的weight_shape/g_idx等参数，不能以原始Safetensors名称数量代替初始化后的清单；其中weight_shape创建未显式指定dtype，实际取决于默认dtype上下文。需要实测记录，不能假定继续是原checkpoint的I32。下一步是接通真实完整rank清单与读写入口，完成固定backend的映射审计设计；原始checkpoint及配置继续完整保留。
