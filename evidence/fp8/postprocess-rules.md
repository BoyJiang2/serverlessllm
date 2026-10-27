# FP8处理前Store与处理后运行态：变换规则及覆盖

本记录补充交付报告的规则定位，不改变模型、loader、gold或正式ID。仅使用冻结镜像实际源和既有张量审计元数据；没有重新读取GPU载荷。完整数学正确性参考仍为C17，C23没有新增weightaudit。

## 源码与实际执行的绑定

[镜像源码索引](image-sources/index.json)记录每个文件的完整SHA、路径及镜像 `sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f`。同镜像仅create、永不start的元数据容器在复制后核验精确身份并删除，没有GPU请求、模型或Store。[采集器](image-sources/collector.py)与[提取记录](source-extraction-journal.json)保留机制和限界。原已停止容器的源码路径曾返回404，未推断原因。

镜像vLLM `__init__.py` SHA `fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346`与C23真实Store进程的backend_reference相同。其余文件是该不可变镜像的源；既有启动挂载只覆盖base_loader/gpu_worker等已登记文件，没有覆盖以下模块。这不冒充逐Ray-worker逐模块源导出。未使用本地0.19.0候选树替代实际源。

| 源码 | 规则入口 | 完整SHA256 |
|---|---|---|
| [MLA](image-sources/f63e821ccba8--mla_attention.py#L710) | MLAAttention.process_weights_after_loading，710–797 | a816328aaf1f6e6c5752eff9b27c736b46bb0b08edc5ced933b3acc89ef7ba39 |
| [逻辑权重](image-sources/1fc72e60e461--quant_utils.py#L342) | get_and_maybe_dequant_weights，342–391 | 012206124cc41350e2ddee3b975c4eafef72a5731abd38da9ca7220c718ab171 |
| [FP8方法](image-sources/fce7fa9e2b5a--fp8.py#L584) | Fp8MoEMethod初始化；749–832处理与_setup_kernel | f2612966aaefa1dd4596a50da361628f64cd590eb3e6bbf6e1b4cfa488334d7e |
| [MoE分支](image-sources/1aaa2b570b14--fp8.py#L413) | 显式backend选择；convert_to_fp8_moe_kernel_format | 537ae0a43a3979f437e76f27005084d77fad67fcf5554e3821c791802fb17892 |
| [DeepGEMM权重/scale](image-sources/5c809ecb2046--fp8_utils.py#L1341) | deepgemm_post_process_fp8_weight_block；1385–1405 prepare_fp8_moe_layer_for_deepgemm | 0792ca22632238dc24455f931e63f69ead0bb74480048b452e1331586ebc58c5 |
| [DeepGEMM包装](image-sources/05be247f4519--deep_gemm.py#L96) | is_deep_gemm_e8m0_used；247–253 scale布局接口 | f52297dc6e6b61832241c321e9fedda813468f6279aa6656a8de97fabf33212c |

## 保存、读取及调用时点

官方Safetensors经原生vLLM模型映射和TP分片后，在全局 `process_weights_after_loading` **之前**保存raw状态。元数据state为 `before_quantization_postprocessing`，冻结source revision、TP=8、FP8配置、Torch/vLLM版本和E8M0=0。

Store loader先建立同一模型骨架，验证完整key集合及每项shape/stride/dtype，恢复处理前GPU权重，再调用同一 `process_weights_after_loading(model,model_config,device)`。源见仓库 `patches/sllm-integration-candidate-v5.patch` 的save_raw_model/ServerlessLLMLoader及vLLM base-loader hook；最终实际文件哈希由受审manifest绑定。没有把后处理后的布局重新写入raw Store并再次处理。

## MLA的W_UV、W_UK_T

冻结MLA函数先执行 `get_and_maybe_dequant_weights(kv_b_proj,out_dtype=act_dtype).T`。helper返回逻辑 `[out,in]` 权重：未量化路径转换dtype；适用的非Marlin、非DeepGEMM普通FP8路径使用scale反量化；其他量化路径以identity矩阵调用原量化层取得逻辑权重。这里记录完整条件调用契约；既有审计未逐层保存use_deep_gemm/use_marlin布尔值，不把helper某个子分支说成已直接观察。

令L=kv_lora_rank、N=本rank头数、P=qk_nope_head_dim、V=v_head_dim：

1. 逻辑权重转置为 `(L,N*(P+V))`。
2. view为 `(L,N,P+V)`；末维split为 `UK:(L,N,P)`、`UV:(L,N,V)`。
3. 普通BMM分支：`W_UV=UV.transpose(0,1)`，shape `(N,L,V)`；`W_UK_T=UK.permute(1,2,0)`，shape `(N,P,L)`。

实际保留记录包含上述普通分支产生的W_UV/W_UK_T，不能借用ROCm AITER FP4/FP8 BMM分支公式。每rank78个活跃attention层；N=8、L=512、P=192、V=256。原FP8 `kv_b_proj.weight` 为 `[3584,512]`、stride `[512,1]`，scale为FP32 `[28,4]`、stride `[4,1]`。对应BF16逻辑临时矩阵转置/view后为 `[512,8,448]`：

| 实际输出 | shape | stride（元素） | dtype |
|---|---|---|---|
| W_UV | [8,512,256] | [448,3584,1] | torch.bfloat16 |
| W_UK_T | [8,192,512] | [448,1,3584] | torch.bfloat16 |

这两个BF16张量是官方后端为MLA BMM构建的派生运行态，并非将checkpoint或raw Store整体改存BF16。原FP8 KV权重与FP32 scale仍保留，选定记录在处理前后完全相同。helper内部具体取权子分支没有直接flag收据，属于该实现归属的证据边界；最终派生shape/stride/dtype/逻辑字节全量哈希已比较。

## 实际MoE分支与E8M0=0

C23各原始model-config明确 `moe_backend=deep_gemm`；冻结oracle将该值映射为DEEPGEMM，对显式请求执行supported检查或抛错，不自动回退其他backend（源码194、254–293）。当前单机mp/无EP批次使用该配置。DEEPGEMM与其batched变体在413–432都转入同一 `prepare_fp8_moe_layer_for_deepgemm`，分别处理w13及w2。

block量化采用weight_scale_inv，权重必须 `torch.float8_e4m3fn`，scale必须 `torch.float32`。本实验环境冻结 `VLLM_USE_DEEP_GEMM_E8M0=0`；因此跳过 `requant_weight_ue8m0_inplace`，Python后处理不重新量化权重值。二维权重仅临时unsqueeze，返回时squeeze；MoE三维权重不做该升降维。

scale接口为 `transform_sf_into_required_layout(sf,mn=wq.size(1),k=wq.size(2),recipe=(1,128,128),num_groups=wq.size(0),is_sfa=False)`；冻结包装传 `disable_ue8m0_cast=True`。底层C++的所有物理padding/隐藏排列不由这段Python展开推断，而用保留的可见输出shape/stride/dtype/逻辑字节SHA核验。每rank75个MoE层的实际输入与可见输出如下，selected记录的值、dtype和布局元数据处理前后均一致：

| 参数 | shape | stride（元素） | dtype | scale接口(mn,k,groups) |
|---|---|---|---|---|
| w13_weight | [256,512,6144] | [3145728,6144,1] | float8_e4m3fn | (512,6144,256) |
| w13_weight_scale_inv | [256,4,48] | [192,48,1] | float32 | 同w13 |
| w2_weight | [256,6144,256] | [1572864,256,1] | float8_e4m3fn | (6144,256,256) |
| w2_weight_scale_inv | [256,48,2] | [96,2,1] | float32 | 同w2 |

Fp8MoEMethod._setup_kernel将转换结果替换回相应Parameter并构建kernel；运行时内部临时scale副本或opaque allocation不包含在原nn.Module tensor枚举声明中。

## 校验方法和确切覆盖

[离线元数据复核](transform-tensor-coverage.json)和[读取器](../../tools/fp8_transform_evidence.py)使用已保留的A2 native_repeat7、native_repeat8和A3组件restoredraw3共24份rank审计JSON，以及冻结tar中8份raw_state。原GPU审计把每个逻辑张量contiguous后全字节SHA256，另外保存shape/stride/dtype。这里重新核对的是这些保留记录，没有重新计算GPU哈希。

每rank选定924个引用：768个处理前FP8/FP32参数引用（456 canonical＋312 alias）及156个派生MLA属性引用。FP8与scale各384引用；KV weight/scale各234（三重引用），W_UV/W_UK_T各78，MoE四参数各75。八rank全部满足：768个raw参数引用与原始Store对应值/布局相同；924个post状态引用在两次native及Store组件恢复记录中完全一致。别名另外验证指向的SHA，不把引用数当唯一张量数。

这只是变换相关的补充覆盖；完整G1的每rank5814个权重/scale/派生引用与DSA/KV排除在C17证据中单独记录。CPU读取器曾因未计alias数量、以及把alias元数据键当成tensor字段而拒绝；修正后两项回归用例和真实八rank输入检查通过，原始文件未改。没有声称覆盖storage padding、CUDA不透明分配或隐藏kernel缓存；这些限制不从性能样本中悄悄删除。
