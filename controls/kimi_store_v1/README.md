# Kimi INT4 Store CPU 适配候选

当前断点见文末最新章节；早期各节保留当时实现范围与审阅边界，不能当作全局实时状态。当前仍无完整转换或GPU运行准入。

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

## 真实PyTorch CPU字节入口（最新测试：32项）

`cpu_tensor_io.write_cpu_state()`现可枚举真实CPU模型的`named_parameters(remove_duplicate=False)`及全部`named_buffers(remove_duplicate=False)`（含nonpersistent），提取实际shape/stride/dtype和storage范围，连接分块writer。拒绝非CPU、未知dtype、非连续/非完整storage、lazy-negative/conjugate以及不同layout共用storage；非法输入在目录创建前拒绝。此入口不初始化模型、不把CUDA tensor拷到CPU，不能拿它直接运行整模转换。

I32/BF16/F32原始storage经ctypes只读使用约定暴露为memoryview，不做数值转换或整tensor clone。ctypes对象持有源tensor，保留的memoryview可在generator关闭后继续保活storage；writer消费后删除块引用，再请求下一块。调用者必须保证源不被并发写入或resize。每块8MiB只是交给writer的buffer限制，不是模型常驻、总RSS、锁页或GPU staging的峰值承诺。

相同layout共享storage的参数别名已识别并返回`alias_of`，当前仍分别序列化，**没有恢复共享storage语义**；这一项仍是后续接线工作，不能把字节相同当成别名恢复已通过。所有buffers先保留，不按名称擅自删除scratch；哪些是必要状态仍需真实后端清单审计。

本轮在仓库外独立环境`../outputs/kimi-cpu-tests-env`安装官方CPU版Torch2.10.0+cpu，`torch.version.cuda is None`；没有改变GLM环境。完整环境版本见`cpu-test-requirements.txt`。最新测试命令（PowerShell，从仓库根执行）：

```powershell
& '..\outputs\kimi-cpu-tests-env\Scripts\python.exe' -X utf8 -m unittest discover -s controls/kimi_store_v1 -p 'test_*.py' -v
```

32项通过，包含真实BF16/I32/F32逐bit保存读回、参数别名及非持久buffer枚举、view与lazy-negative拒绝、引用保活/释放；测试模型均为微型CPU夹具，不是Kimi权重或真实推理金标。没有NumPy依赖；Torch导入时可能提示缺NumPy，不影响这条ctypes字节路径。旧的schema AST测试仍是元数据单元测试，新增真实tensor测试也没有调用原生Store扩展。

未完成：别名共享storage恢复、固定后端真实rank清单/参数映射、GPU有界staging、原生Store扩展消费验证、vLLM导出/恢复及业务金标。目标仍未完成，后续GPU步骤继续受预约/独立审阅/守护约束。本轮无远端连接、无模型下载、无完整转换。

## 完整同布局别名CPU闭环（最新：35项测试）

本节更新上一阶段“别名分别序列化”的状态。`write_cpu_state()`现在只写canonical参数一次，并为具有同一storage、完整且相同shape/stride/dtype的别名返回相同record。不同layout、部分view和lazy-negative/conjugate继续拒绝。

`publish_index(..., aliases=info['alias_of'])`明确接收别名声明，验证canonical存在、不自指、不成链，layout及完整记录一致；只有声明的alias可共享范围，未声明重叠仍拒绝。索引里别名采用相同offset/size；sidecar记录alias_of及aliases_deduplicated。`planned_transfer_bytes`保留逻辑计划的保守计重复估计，`written_payload_bytes`仅为去重有效payload，不包含8字节记录padding和4096字节分区padding，不等于实际磁盘占用。

从固定SLLM commit的`utils.py`提取并执行原始`calculate_device_memory()`和`calculate_tensor_device_offsets()` AST：单rank、单device图中，alias只分配一次、copy chunk只出现一次，设备offset相同。微型CPU buffer回放该copy metadata后，用真实torch.frombuffer重建两个同offset视图，修改一个可被另一个观察到。这验证CPU共享关系与原生映射函数的元数据语义，**没有调用CUDA恢复或原生Store服务**。

源fixture：`fixtures/native_utils.py`，SHA `c21d9a8e30121c17ddd96bbcf6a5f6be59f3208a75e86a430194eee611276217`，完整保留Apache-2.0许可；来源 `https://raw.githubusercontent.com/ServerlessLLM/ServerlessLLM/9f50241baa5386e06a9321c51f19a9ef5f964c2b/sllm_store/sllm_store/utils.py`。注意原函数的去重字典跨device共享，本测试绝不推广为跨device别名正确性；后续每rank索引仅映射到一个设备，其他安排需另审。

最新35项真实Torch CPU测试及独立只读复审通过，见`alias-cpu-review.json`。此前review均仅绑定各自旧版本，不自动批准本次或未来GPU运行。仍待完成：固定后端真实rank清单与TP8参数/必要buffer映射、有界GPU staging、原生Store/CUDA消费及vLLM接线、真实模型金标。目标保持未完成；本轮没有远端连接、GPU、完整模型转换或下载。

## TP8专家切片/融合方法CPU验证（最新：38项）

`expert_mapping.py`新增冻结模型专家名解析及checkpoint期望切片view，只作为CPU审计规则，不作为另一套模型loader。gate/up沿原始输出维分为8份，down沿原始输入维分为8份；Marlin分支转置，Flashinfer分支不转置。返回借用view，无解包、重数量化或clone；明确拒绝未知条件/dtype/lazy view。返回view可能非contiguous，不能直接交给只接受完整owned storage的写入器。

测试fixture直接提取vLLM v0.19.1 FusedMoE原始`weight_loader`及四个helper方法和量化枚举，完整layer.py源SHA `b47fe17d1f760f184b95140fc474259520fc60ff2ed4d68bb558eb6e23b1328a`。`fixtures/vllm_expert_loader.py`经过AST结构复核，保留许可。测试仅设置quant_method接口名称、无EP identity expert map等分支条件；没有构建真实vLLM/FusedMoE对象或选择实际GPU kernel。

在微型真实CPU张量上，逐8rank核对：gate的w1放在融合w13前半，up的w3在后半，down的w2分片，其他expert槽保持；拼回全部rank还原原始source bits。BF16 NaN payload/负零及I32符号位也逐字节保持。另验证weight_shape复制到指定F32夹具时原shape值不变；这不保证任意I32能无损转F32，官方原始I32 shape侧车仍必须保留。

对本机已有、SHA固定的原始索引做完整**名字**扫描：208550项中207360个专家条目（packed/scale/shape各69120），无重复expert/shard槽；映射到360个融合目标参数名，每个w13目标有768个gate/up源项、w2目标384个down源项。其余1190个非专家参数未在这一步映射，见`expert-name-audit.json`。全量名字扫描不证明初始化后的参数/buffer完整性或真实payload映射。

38项CPU测试及独立只读复审通过，见`expert-mapping-cpu-review.json`。仍待：实际backend/拓扑和必要buffer全清单、非专家参数、weight_shape原始值侧车与映射、后处理重排前后审计、原生Store/CUDA恢复及vLLM接线。当前目标继续保持未完成。本轮无远端连接、GPU、模型运行、完整转换或重复权重下载。

## 非专家规则与运行态清单（最新：46项CPU测试）

非专家条件规则已通过独立只读复审，见`nonexpert-cpu-review.json`。固定索引1190个剩余名字全部归类为norm245、router120、MLA A122/B122、attention output61、shared MLP180/dense MLP3、vocabulary2、projector6、vision329；融合后为1068个目标名字。`nonexpert_mapping.py`明确限制MLA、无sequence-parallel MoE和encoder TP data，不代表实际运行配置已选中。`mm_projector.proj.0/2`映射到linear_1/2；MLA q_a与kv_a合并为复制式fused_qkv_a_proj，不能误当TP切片矩阵。

测试直接执行固定vLLM linear/vocab loader方法AST，验证条件下row/column、gate_up融合、复制MLA A以及词表补零。仅用真实BF16 Parameter；未选的量化参数类guard以占位类表示，不能用于声称其他量化分支通过。词表shard_indices是测试指定的条件输入，不证明真实构造器分区正确。源SHA见`nonexpert-name-audit.json`，fixture保留许可。

`runtime_inventory.py`新增真实CPU模型的metadata-only清单：包含parameters、全部buffers（含nonpersistent）及直接挂在module上的未注册Tensor属性。收集shape/stride/dtype、logical bytes、storage范围、alias、lazy flags及持久性；不读取payload或序列化指针。按调用者提供的checkpoint目标名区分源权重与运行态待审项，缺失目标单列。

固定MoE源创建的w13/w2 weight_g_idx和g_idx_sort_indices来自torch.empty；源码未证明它们在无actorder的preprocess阶段被填值，因此精确匹配的非空项标为uninitialized_aux_candidate。Marlin无actorder分支在后处理中重建为空；Flashinfer分支提前返回，不能把Marlin规则外推。`write_cpu_state()`已在文件创建前拦住这些非空候选和直接未注册Tensor，避免无政策时复制其内容。此举不构成已实现scratch省略/重建策略，真实转换仍被阻断。

其他runtime parameter/buffer不自动排除，保留requires_policy_review；CPU helper的inventory_policy_verified仍为false。视觉源码的time_weight是非持久buffer，freqs_cis可能按需注册，不能仅凭checkpoint清单认定状态完整。清单当前不遍历opaque容器里的Tensor，明确opaque_container_audit_complete=false；不得当作全对象状态审计。

`compare_inventory()`报告后处理前后added/removed/metadata-changed，只接受正确阶段顺序；不比较权重值、不认定后处理正确。新增真实CPU测试覆盖非持久buffer、直属未注册缓存、缺失源目标、alias、阻止未初始化辅助导出及空辅助重建的metadata差异，累计46项通过。仍待：明确初始化/重建政策、opaque状态审计、固定后端实际rank快照与pre/post payload校验、原生Store与vLLM接线。未启动任何远端任务或GPU。

## 全索引来源绑定与写前名称完整性（最新：50项CPU测试）

`checkpoint_contract.py`从精确SHA绑定的完整原始索引和config生成`checkpoint-targets.json`：208550源条目全部进入既有专家/非专家条件规则，得到1428个目标名及每目标源条目数，并记录revision、config/index SHA、mapper SHA和条件profile（TP8/PP1/MLA/no EP/no SP/encoder data）。重新派生得到逐字节相同的artifact，收据见`checkpoint-contract-derivation.json`。

`load_targets()`硬绑定artifact SHA `52a4262b0f607f3dbb975a2408bafa0cfda1cb5a813a6992ae61e25daddc42ae`，拒绝被修改或不受审的来源清单。`write_cpu_state(..., checkpoint_contract=raw_bytes)`现将其目标名传入metadata inventory，missing checkpoint target在创建目录前拒绝。50项CPU测试包含artifact/源索引变更拒绝、完整条目计数、真实不完整CPU模型写前拒绝。

目标清单是**来源固定、条件性的名称预期**，不是已观测的实际rank布局。尚未验证shape/dtype、选中参数类、运行态profile或已正确加载的值；实际初始化若不同，必须调查并修订/审阅，不能删除missing项凑通过。未提供contract的旧组件调用仍允许微型CPU测试，但明确checkpoint_name_coverage_verified=false、inventory_policy_verified=false、runtime_admitted=false，不能用于宣称完整Kimi导出。提供contract且无missing也只说明名称覆盖，其他flag继续false。

仍待：明确的scratch初始化/重建政策、opaque缓存与必要buffer覆盖、pre/post实际payload校验、原生Store消费与vLLM导出恢复接线。当前仍不启动完整转换或GPU，不宣称目标完成。
