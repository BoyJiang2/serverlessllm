# Kimi INT4 Store CPU 适配候选

最新逐记录producer已连通多handle协议及独立安装CPU链，183项CPU测试通过，复审见native-record-call-review.json；public真实二进制绑定/活跃Store及CUDA仍未验证。详见MULTI-HANDLE-PRODUCER.md。未接混设备工厂，原GPU预算拒绝继续生效，没有实际GPU或完整转换。

最新IPC错误传播候选：native_patch/model.cpp及实际源尾部的CPU编译故障控制已通过，总计177项CPU测试，复审见ipc-close-review.json。只是独立源码，未编译/部署完整Store或执行CUDA；下一步多handle加载必须核验实际库修订，不能沿用旧确认语义。

最新逐记录寿命候选：record_allocations与独立payload/install分支已有175项CPU测试，复审见record-allocation-review.json。CPU小buffer证明alias最后使用后可释放该记录、其他记录仍活跃；未运行CUDA，未接多handle native producer或混设备工厂。详见ALLOCATION-LIFETIME.md，不能宣称显存问题已实际解决。

最新worker准备：导出factory/独立loader注册已接，167项CPU测试通过，复审见worker-preparation-review.json。native完整CUDA目标+native分配双份驻留的条件下界超过历史单卡容量，新增工厂设备预算拒绝；必须先改初始化策略，不能将旧CPU PASS当作可运行。详见WORKER-PLAN.md。当前无守护部署、插件多进程传播或GPU执行。

最新 `checkpoint_export.convert_checkpoint_rank` 接固定本地官方SF加载到device_export流式写入：原始INT4/scale位、shape/角色/alias/codec保留，源名称及目标覆盖显式检查；159项CPU测试通过，审阅见checkpoint-export-review.json。完整manifest/mapper/SF迭代器仅fixture，未执行完整模型或实际GPU转换；此函数需要worker供应已初始化模型与loader，不是可直接运行的服务/转换CLI。

最新组合 `vllm_native_hook.py` 已连接源预算/配置预检、CUDA rank准备、native_call/install和固定Base后处理，一次生命周期/失败状态有CPU控制测试；150项CPU测试通过，复审见native-lifecycle-review.json。成功控制有明确设备/rank/native/install mocks；完整worker、守护、实际八rank与服务仍未验证，不提供可直接开跑的CLI。

最新显式CUDA准备候选：新增device_state元数据/视图及CUDA rank/Marlin辅助入口，共用已绑定源分支和60层预检编排，旧CPU入口保留。144项CPU测试通过，审阅见device-preparation-review.json；CUDA成功分支未运行。当前仍没有经实际GPU验收的vLLM原生入口或完整转换器。

最新 `native_call.py` 包装固定native nonblocking函数，在私有环境记录本次分配与offsets、显式检查完成RPC后才构造Tensor包装。139项CPU测试通过，修订审阅见native-call-review.json。真实native执行、worker隔离、失败context/Store退出及vLLM GPU生命周期仍未通过；未提供可直接启动的服务入口。

最新组合候选：`native_install.py`已经连接可信元数据、逐块原字节校验、角色/codec恢复及引用锚，`native_payload.py`以至多8MiB主机块审计已返回张量。134项CPU测试通过，当前修订静态审阅见`native-install-review.json`。仍未接真实native分配/完成确认和vLLM GPU入口；CPU夹具不能作为运行准入，详见`NATIVE-RESTORE.md`。

最新native接点：`native_spans.py`提供单次分配区间/实际地址/alias预检和模型寿命的Tensor引用锚，124项CPU测试通过，修订复审见`native-spans-review.json`。C++逐tensor from_blob只有一个offset0包装器负责cudaFree；此源码寿命风险及原大Storage说明修正详见`NATIVE-RESTORE.md`。尚未接真实分配记录、CUDA消费者及payload验证，不能据此启动或宣称模型恢复成功。

2026-10-30最新补充：CPU文件恢复支持显式codec还原非持久complex64视觉cache，保留原始位与存储别名；允许唯一已审名称在目标尚未登记时补建。全部源payload验证发生在新cache分配及任何目标字节写入之前，登记发生在全部copy成功之后；中途失败仍令整个目标作废，不回滚。直接恢复已有参数及有界读视图，不另建完整state副本。

本轮114项CPU测试通过；固定vLLM Base/utility代码的小夹具验证已有及缺席cache都在后处理之前完成恢复。名称覆盖和rank准备边界仍有mock；codec转发测试不证明真实60层模型已执行。只读复审绑定文件SHA见lazy-restore-review.json，不代表native load_dict、CUDA、完整Kimi或服务准入。未启动GPU，也未修改GLM历史结果。

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


## 同阶段CPU字节指纹（55项测试及独立只读复审PASS）

`state_audit.fingerprint_state()`逐个真实CPU参数与全部buffer，以最多8MiB视图计算原始SHA256，不转dtype、不克隆整张量。记录rank、phase、Torch版本、layout与metadata inventory；可传入冻结checkpoint contract。未初始化辅助项、直接未注册Tensor及缺失目标仍在读取payload前拒绝。调用者必须暂停状态写入；该函数不是并发一致的模型快照。

`compare_restored_state()`仅比较同rank、同phase、同Torch版本及同名称覆盖模式的指纹，报告增加/缺失/改变的张量与inventory差异。pre/post跨阶段被拒绝；GPU后处理另需明确变换政策，见`AUX-POLICY.md`。普通opaque容器覆盖仍未完成，结果永远不授予runtime准入。

新增测试使用既有真实CPU混合dtype夹具，八个rank标签分别从raw partition读回实际字节，验证NaN payload、负零、I32 packed值、alias与非持久buffer；同形状packed/scale/shape/router字节翻转会失败。这里的八rank是微型CPU夹具参数化，不是实际八卡权重核验；读回是测试内显式字节拷贝，不是原生load_dict。

8MiB只是单次视图上限，不是进程RSS上限。完整模型本体/恢复目的张量、Torch allocator、元数据与其他驻留仍占内存；指纹不创建整模型副本，不证明真实峰值。尚需真正的有界恢复入口、辅助状态消费者、完整模型rank构造与原生Store/vLLM接线。


## 有界CPU恢复入口（61项测试及修订复审PASS）

`cpu_restore.restore_cpu_state(directory, model, expected_sidecar_sha256, rank=..., checkpoint_contract=..., chunk_bytes=...)`接收已构造、暂停写入的CPU推理模型及外部可信侧车SHA，不根据文件中的任意shape分配模型。它复用冻结config/plan、native index校验、完整源payload验证；在任何目标写入前要求名称、dtype/shape/stride、完整storage、alias拓扑、TP8 rank和before-postprocess状态匹配。可选官方名称契约继续仅证明条件名称覆盖。

实际恢复用无缓冲文件readinto直接填充最多8MiB的CPU storage视图，支持短读，按目标dtype元素边界分块；不反量化、不cast、不克隆完整张量，alias只写canonical一次。复制期间再次算SHA防止初验后payload变化，成功才返回cpu_payload_restored。源目录和目标模型须保持独占静止；不是原子恢复，也不支持训练autograd语义。初验失败不改目标；复制开始后的任何失败都必须丢弃整个目标，不得继续推理，不能把异常前部分写入算成功。

61项CPU测试覆盖真实INT32 packed/BF16 scale/INT32 shape/F32 buffer与空buffer、alias、八个rank标签的微型读回、错rank/shape/alias/块上限拒绝、后部损坏在写前拒绝、校验与复制之间变化拒绝、短读与EOF。首次测试断言误把夹具唯一payload写为48B，实为44B，已修正；这是测试预期错误，不涉及真实模型数据。

分块上限不等于RSS上限：目标完整模型已经驻留；元数据、Torch allocator和源页缓存另占成本。入口先完整校验后再复制，存在双遍I/O，当前仅正确性组件，不是性能测量协议。尚未调用原生load_dict/CUDA、构造完整Kimi TP状态或实施scratch重建消费者；native_loader_verified和runtime_admitted始终为false。

本轮独立初审发现：目标不同dtype/shape若重解释同一完整storage，原alias键不会把它们标为alias，逐项SHA可能通过但后写覆盖前写。已在任何复制前拒绝非空共享pointer但layout不同的目标，并增加I32[2]/BF16[4]真实共享存储负例，证明copy未调用且目标未变。旧初审changes_required保留，不以新测试抹去发现。


## 显式Marlin辅助状态规范化（68项CPU测试及修订复审PASS）

`marlin_aux.normalize_cpu_auxiliary()`是只针对单个官方routed expert层的CPU步骤：必须在checkpoint加载结束、原生后处理之前显式调用。它要求冻结config、TP8/PP1/noEP等条件、vLLM0.19.1、实际quant method类名/模块及其process方法所在文件完整SHA，且该对象backend=Marlin、actorder=None。四个scratch必须是注册的、requires_grad=False的完整CPU INT32存储，形状分别为[384,7168]或[384,256]；同其他注册参数/buffer共享storage时拒绝。

完成所有预检后才替换四项为[384,0] INT32参数，返回原始layout、新layout、rank、层名、来源身份及阶段receipt。不会读取未初始化scratch字节，不改packed或scale，也不把非空零填充伪装成原始值。空参数的处理与固定原生后处理的no-actorder else分支一致；这里只将这一确定子步骤前移，不执行其余GPU repack或scale变换。重复规范化拒绝，避免无记录地接受已改写的输入。

新增fixture是官方v0.19.1完整源文件（96755B，SHA在代码中冻结，保留许可）；测试仅AST隔离原方法并构造属性，另直接执行其空参数else分支比对metadata，未import/初始化实际vLLM。单层CPU scratch约21.75MiB、torch.empty不读内容；未构造完整模型。覆盖错误backend、group actorder、未知类、来源变化、错phase/profile/rank/ABI、payload别名和最后一项layout不符时写前拒绝。68项测试通过不代表实际backend或完整TP映射通过。

receipt尚未绑定native index侧车，完整60层/八rank的编排及恢复消费者尚未接入，因此不能用此helper宣称完整Store转换。默认写入器继续拒绝未规范化的非空辅助项。opaque状态、视觉buffer、实际GPU后处理及原生服务仍需后续工作。

本轮初审changes_required指出仅class/module/sourcefile SHA不能绑定实际方法；已从完整冻结源码compile提取精确方法code，与当前类方法code比较，并拒绝instance override。新增同路径改写方法及实例覆盖负例，68项测试通过；静态复审PASS见marlin-aux-review.json，旧发现及旧hash保留。


## 辅助状态receipt绑定写入与恢复（72项CPU测试及只读复审PASS）

`auxiliary_manifest.validate_auxiliary_receipts()`按冻结来源约束逐层核验：实际layout中每个完整FQN辅助参数必须被一个、且仅一个recipe覆盖，after必须为官方[384,0] INT32完整存储；before必须是固定来源的[384,7168]/[384,256]。rank、config/source SHA、method、backend、actorder、profile、阶段及保守flag均按精确JSON比对。深拷贝JSON防止调用者在校验后改写原receipt对象。实际layout缺项、额外/重复/错误receipt均拒绝。

`write_cpu_state(..., auxiliary_receipts=[receipt], rank=rank)`在创建目录前校验；返回info携带隔离的receipt。发布时必须显式传`publish_index(..., auxiliary_receipts=info['auxiliary_receipts'])`，记录进入受外部SHA约束的kimi_raw_state.json。`read_index`按索引layout复核recipe，删掉receipt后即使重新提供侧车SHA也不能通过。无官方FQN辅助项的旧微型CPU组件仍可用空receipt；有这些项而无receipt的旧文件会被拒绝。

恢复目标必须事先独立执行显式规范化，调用`restore_cpu_state(..., destination_auxiliary_receipts=[target_receipt])`；来源侧车和目标receipt均按目标layout及rank验证，然后才可能写入。恢复函数不代替目标构造/规范化，不把来源记录冒充目标执行证据。

新增单层CPU集成测试走normalize→有界写→native-schema发布→CPU恢复→原始指纹比较；仍不调用native load_dict、不构造完整60层模型、不触碰GPU。72项测试另覆盖缺失receipt写前拒绝、目标无receipt不变、重复/错rank/被改recipe拒绝、侧车缺receipt拒绝及拷贝隔离。

receipt校验只证明记录与冻结规则、布局及外部SHA一致，不独立认证函数实际执行、phase真实性或backend初始化；这些必须由后续运行证据补齐。all_layers_verified、actual_backend_initialized、runtime_admitted继续false。尚需60层编排、完整对象状态/视觉buffer政策和真正vLLM/原生Store恢复入口。


## 整rank预检与导出编排（78项CPU测试及修订复审PASS）

`rank_export.normalize_rank_cpu_auxiliary()`先加载完整冻结目标清单，要求1–60层routed expert模块存在且各自独立；全部checkpoint目标缺失、直属未知Tensor、非CPU/非完整storage/未知dtype以及辅助存储跨状态共享会在任何绑定变更前拒绝。单层规范化已拆为非变更的_prepare与原public执行步骤，整rank先完成全部60层源码/布局预检并验证投影后的receipt清单，最后才统一替换240个辅助参数。此处的全层代码遍历不把实际backend初始化或完整模型正确性flag改成true。

`export_rank_cpu()`仅接受调用方已构造并加载checkpoint的CPU模型，连接整rank准备→现有有界写→plan→native-schema发布，保留来源/目标限制，不构造模型、不下载、不加载CUDA、不绕过未知状态。接口实现不代表已授权执行完整转换。预检阶段的source_inputs只含元数据，临时named张量列表及时释放，避免替换前scratch引用跨越后续文件I/O。模型本体、规范化前scratch、allocator和其他驻留仍须另算RSS；这不是全模型内存峰值证明。

新增6项测试使用约240个单元素参数的60模块控制流夹具，并mock已另测的单层source/helper和目标清单边界；只验证全部预检先于首个变更、最后层失败保持所有绑定、缺层/共用模块/跨状态共享拒绝、无效预算写前拒绝、预检列表不延长旧张量寿命。未mock的冻结完整contract明确拒绝该夹具，测试不输出假完整Store、不运行全模型，也不把mock过的来源/shape当实际权重证明。原有真实单层及分块组件测试继续运行，共78项。

预检失败保证不变更；执行替换或I/O期间出现其他异常时，整个模型/输出目录作废，保留失败证据，不承诺事务回滚或自动重试。尚需完整对象/视觉buffer政策、真实模型构造与载入接线、原生load_dict和实际八rank后处理/业务验证。

本轮初审发现size-1维的非canonical stride虽可is_contiguous/full-owned，但原发布器会拒绝，若不预检会在修改scratch后才失败。已把与publish_index一致的canonical stride检验前置，并补真实empty_strided((1,),(2,))负例，证明尚未调用单层prepare且所有原绑定未变；初审记录保留。


## 嵌套运行态清单与视觉buffer证据（85项CPU测试及只读复审PASS）

metadata inventory现扫描Module属性中的标准dict/OrderedDict/list/tuple/set/frozenset，包含字典Tensor键；不重复遍历已单独枚举的_parameters/_buffers/_modules。发现嵌套Tensor即记录container_tensor、layout、alias并阻止写入，避免静默掉失。字典路径使用索引，不记录键的实际内容；循环路径可审计，未展开自定义对象只列类型/路径。深度32、节点100000超限拒绝；builtin_container_audit_complete不代表opaque_container_audit_complete，后者仍false。自定义对象、Tensor自身附加状态等仍需专门政策。

新增真实CPU测试涵盖嵌套Tensor与注册参数alias、写前拒绝、Tensor字典键、循环、自定义opaque对象及深度/节点预算。不会读取Tensor数值或改变原模型。旧清单仅识别直属Tensor的历史断点已被此更新覆盖。

视觉状态证据见VISION-STATE.md：固定原始Rope类在2x3小网格实际生成lazy nonpersistent complex64缓存，现有dtype清单及归档native C++表均不支持它；存在该缓存时当前写入在文件创建前拒绝。不能仅给Python添加dtype名来冒称原生支持。time_weight虽nonpersistent仍须保留，外层视觉塔dtype转换会改变实际dtype；此次未运行NumPy构造或实际视觉业务。

共85项CPU测试通过，未构造完整模型、512x512缓存或转换，也未访问远端/GPU。下一项应实现并审阅保留complex缓存完整位模式的编码/恢复规则及消费者，或适配原生dtype；还需明确opaque对象的必要状态，不能靠丢缓存/改模型来解决。


## complex64 buffer原始位编码（92项CPU测试及修订复审PASS）

`buffer_codec.py`新增显式`complex64-int32-bits-v1`：仅调用方指定的已注册CPU complex64 buffer可编码，必须canonical/full-owned且无lazy conjugate/negative。`reshape(-1).view(torch.int32)`借用同一storage，将完整real/imag位模式写入原生已支持的INT32物理记录，不做数值转换、不重算频率、不触碰官方packed参数或scale。侧车同时记录原complex dtype/shape/stride/storage bytes与平坦INT32布局。标量/空状态仍需同一layout规则；不支持一般view或跨dtype存储共享。

调用方用`buffer_codecs={full_buffer_name: CODEC}`接入CPU写入、指纹、整rank导出与恢复；真实alias只有全部编码且原layout相同时可保留。publish必须接info['buffer_encodings']，read_index校验侧车recipe。固定视觉cache完整FQN出现时强制要求manifest，不能删record后把INT32当原状态。恢复比对来源及目标实际编码描述；原模型buffer绑定仍是complex64。`decode_cpu_buffers(state, encodings)`可将已读回的CPU INT32状态映射为共享storage的complex视图；标准原生load_dict本身仍只会给出物理INT32，实际CUDA消费者尚未接线/验证，不能跳过decode。

92项测试含NaN payload、正负零及任意real/imag word的逐位保留、buffer alias、零复制decode、缺codec/参数codec/未知codec/partial/conjugate/crossdtype alias拒绝、侧车删改及payload损坏拒绝，以及固定原始Rope类的小网格生成→编码→CPU恢复。旧默认路径仍拒绝未声明complex buffer。没有生成512x512缓存、完整模型或Store池，没有GPU工作。

这是CPU编码与恢复组件，不是原生complex dtype新增支持。原生GPU视图重建、目标buffer登记/持久性、完整vLLM消费和视觉输出验证仍待完成；runtime_admitted/native_loader_verified始终false。编码视图不克隆整个buffer，临时字节块上限仍8MiB；源/目的完整状态及缓存驻留成本依旧存在。

本轮初审发现publisher/read缺alias两端原始recipe交叉校验：等字节但不同complex原shape或一端不编码可能被接受。已在发布/读取交叉核验，并让decode根据实际storage识别alias后核验；新增三入口拒绝负例。原发现与旧hash保留在buffer-codec-review.json，修订静态复审PASS。


## CPU状态角色与安装消费者（100项测试及修订复审PASS）

`state_bindings.py`枚举参数/buffer槽位，包含persistent与requires_grad；CPU writer写前采集，rank准备变更前也检查，侧车发布/读取绑定该清单。编码buffer必须有完整角色清单，避免INT32物理记录丢失原buffer语义。旧无角色清单的普通layout组件仍可读取，但不能用于新安装入口；旧编码试验侧车若无角色清单将被拒绝，不自动追认或篡改旧证据。自定义buffer Python属性尚无保存政策，源/目标在任何写入或变更前拒绝；参数加载属性由既有构造对象保留。

`install_state.install_cpu_loaded_state(directory, model, state, trusted_sha, rank=...)`检查外部侧车SHA、索引、原文件payload、传入CPU状态的layout/实际字节/alias，decode complex buffer后，完整预检目标角色、布局、原始codec形状、共享对象约束及辅助receipt，再安装。参数使用原Parameter对象.data赋值，保留子类/weight_loader等属性；buffer按声明persistent重新register_buffer，使用detach共享视图恢复requires_grad。不做浮点统一cast，不调用load_state_dict忽略非持久项，也不调用hf_quantizer重新量化。

只有固定视觉cache FQN缺席、模块路径已存在、来源明确为encoded nonpersistent无grad buffer时，才允许新登记lazy cache；不创建模块、不替换其他同名属性、不补造权重。其余缺项/多项拒绝。模型和传入状态必须静止，且仅CPU；预检失败不修改目标，安装阶段异常使整个目标作废、不回滚。此入口不调用原生load_dict、CUDA、quant后处理或路由发布，不能据返回值宣称原生加载/服务通过。

新增7项安装测试用真实小CPU状态字典，未调用native loader：混合位模式、Parameter子类和加载marker、alias、旧complex buffer引用释放、lazy cache非持久登记、错误角色/persistent/payload/tied对象/自定义buffer属性、缺角色清单及True/False共享存储grad标记。初审发现grad标记误通过后修复，旧发现/hash保留于state-install-review.json。

安装释放测试另发现上轮inventory递归函数闭包自环会保留named Tensor列表直到GC，已用finally删除递归函数绑定；新增不调用gc.collect的weakref回归，成功路径即时释放引用，预算失败路径也断环。首次97项运行因此有一项旧buffer释放失败，已保留原因与修复记录。全套100项通过，未启动模型或完整转换。

下一步仍需固定vLLM构造/加载生命周期及原生Store接口接线，尤其避免旧通用HF加载器的统一float cast/重新量化路径。完整模型、实际八rank后处理与业务gold仍未验证，所有运行准入flag继续false。


## 固定vLLM生命周期接点（107项CPU测试及修订复审PASS）

固定vLLM0.19.1 BaseModelLoader.load_model顺序为initialize_model→self.load_weights→可选online finalize→统一process_weights_after_loading→eval。utility先遍历quant methods，再处理Attention/MLA的后载入初始化。Store已保存TP融合preprocess参数时，应接load_weights槽，不再把状态送入模型原始checkpoint load_weights二次切TP，也不在hook里重复执行后处理。旧通用HF加载器的float统一cast/重新量化路径不适合直接接本格式。

`vllm_cpu_hook.make_cpu_store_loader()`生成独立CPU候选子类：验证Base及后处理utility完整文件SHA和实际方法code，保留原生命周期，只覆盖weight hook连接已审CPU rank准备及有界文件恢复。未注册到vLLM全局loader表、未覆盖原serverless_llm入口。构造模型前要求CPU platform和CPU device；仅load_config.device=cpu不够，因为Kimi构造器还会显式使用current_platform设备。候选不会在qjhs003 CUDA平台初始化模型，它不是可部署GPU服务loader。

新增独立lifecycle_status在初始化前消费一次尝试，任意阶段异常记failed，成功仅returned；weights_status=installed只指权重步骤，不代表后处理/服务成功。不允许失败后自动重试或二次初始化，不自动下载。postprocess仍由Base调用，hook不将其执行两遍；online uses_meta_device方案拒绝。

辅助receipt现区分source checkpoint_loaded_before_postprocess与target target_initialized_before_restore，后者只说明构造后准备接收权重，不能谎称目标已加载来源checkpoint。默认写入/发布仍只接受source阶段；恢复/安装可显式接受两者。整rank export入口先拒绝target阶段，避免修改绑定后才被writer拒绝。任意新阶段字符串仍拒绝。

新增7项控制测试执行完整固定源码编译出的Base类和utility函数（fixture保留许可），但框架依赖、initializer和quant/attention方法为微型控制标记，全rank准备与名称边界在正例中mock；CPU raw文件恢复实际执行。验证restore先于quant/attention且各一次、损坏时不进入后处理、设备/平台及重入在初始化前拒绝、初始化/后处理失败终态，以及target记录不可发布成source。不是实际quant kernel或完整Kimi测试。测试夹具首次类code执行/AST重编译不一致问题已用完整源码编译的class code与__build_class__解决，未放宽代码身份检查。

当前hook仅接现有布局的CPU raw恢复，不调用原生SllmStoreClient/load_dict或CUDA；编码/lazy视觉buffer的完整生命周期连接、GPU状态校验、实际模型构造及服务验证仍待完成。之前CPU installer具备角色/解码组件并不等于此hook已经消费所有状态。独立复审发现的生命周期/phase问题与旧hash见vllm-cpu-hook-review.json；107项通过仍不授予运行准入。

- 新增mixed_device_init.py源绑定混设备构造候选；187项CPU测试通过，复审见mixed-initialization-review.json。只验证CPU上下文/拒绝边界与登记设备政策，未做真实CUDA构造，未接native工厂；完整混设备inventory/aux/install及Base后处理目标语义仍待闭合。详见MIXED-INITIALIZATION.md。

- 新增显式device_map元数据及独立target_device_map安装边界，192项CPU测试通过，复审见mixed-state-review.json；默认CPU结果保持一致，隐藏Tensor仍扫描，地址alias加入device维度。真实混设备/CUDA绑定未验证，CPU Marlin规范化及Base后处理/新工厂接线仍待完成。详见MIXED-STATE.md。

- 新增normalize_rank_mixed_auxiliary：CPU文本/官方CUDA视觉设备政策先核验，仅target-before-restore，复用60层全预检及CPU Marlin辅助规则；195项CPU测试通过，复审见mixed-auxiliary-review.json。未连接完整mixed factory、未运行真实CUDA；下一项构造/producer/install组合及Base后处理目标语义。详见MIXED-AUXILIARY.md。

- 新增initialize_mixed_vllm_rank保留官方quant/config/reload初始化上下文，mixed_postprocess显式最终CUDA target并限制一次后处理；202项CPU测试通过，复审见mixed-context-review.json。初始化/后处理真实CUDA边界仍mock，未组合完整新factory、无GPU运行。下一项连接构造/aux/producer/install及受审全峰值预算与worker。详见MIXED-CONTEXT.md。

- 新增vllm_mixed_hook独立loader，把构造/aux/逐记录producer/install/显式CUDA后处理串入冻结Base私有namespace；207项CPU测试通过，初次fixture错误保留，复审见mixed-lifecycle-review.json。仍保留保守2×checkpoint预算停止，真实模型不能据此开跑；完整峰值/主机驻留预算、实际构建身份及worker准入仍待完成。详见MIXED-LIFECYCLE.md。

- 已用冻结完整索引+64分片header实算memory_floors，211项CPU测试通过，复审见memory-floors-review.json。mixed raw≥71.557GiB/卡，8rank CPU文本≥565.438GiB、Store有效payload≥572.458GiB；不是峰值上限。两类全缓存+CPU目标+pool假设共驻留约2264.656GiB，普通页可回收，不能直接称OOM。旧预算停止保留；下一项source单模型worker/插件与完整资源/构建/守护闭包。详见MEMORY-BUDGET.md。

- 新增source_plugin只做export独立注册及私有distribution metadata，220项CPU测试通过，初审读取上限竞态已复现修复，复审见source-plugin-review.json。新CPU子进程已测标准entrypoint发现及spec身份继承，尚未执行真实vLLM general-plugin/TP8 worker。下一项source worker完整manifest/镜像ABI/预算/guard/监控及退出取证，仍不启动GPU。详见SOURCE-PLUGIN.md和RESERVATION-PLAN.md。

- 新增source_bundle源码对象生成/完整文件校验及source spec/run布局绑定，233项CPU测试通过，初审扫描错误误通过已复现修复，复审见source-bundle-review.json。复用固定SHA的既有Git读取器，blob读取前检查大小；独立CPU CLI已测。尚未接source worker/插件启动，未验证真实整包或GPU；下一项实际候选存证、启动来源/guard/预算与退出闭包。详见SOURCE-BUNDLE.md。

- source_plugin现强制在runtime/factory之前verify_source_run（实际插件目录+run固定manifest+外部bundle SHA），cached注册也重查；235项CPU测试通过，复审见source-startup-review.json。旧f152680包冻结，必须按新代码提交成包；下一项新整包CPU接线验证和worker入口/实际身份/预算/guard闭包。尚无GPU/完整Store。
