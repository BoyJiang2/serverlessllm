# V4 服务首次注册的冻结配置契约

2026-11-06；父提交 `53304fbb16b87573bf5342889c9541fcc85f246f`。

注册绑定此前只保证第一份配置之后不变，不能保证第一份就是 P2 冻结配置。源码确认 backend 省略 enforce_eager 时默认为 False，省略 enable_prefix_caching 时默认为 True；router 的 max_instances 默认为 10。它们与冻结方案的 eager、prefix off、单 TP8 实例不符。其他任意 backend 参数还可能被 engine 字段过滤掉或改变模型行为。

复用现有 v4_service._validate_v4_topology，在 Controller 首次 await 前及 StoreManager worker/绑定操作之前，统一要求：

| 字段 | 请求契约 |
|---|---|
| torch_dtype | 显式字符串 bfloat16 |
| enforce_eager / enable_prefix_caching | 显式布尔 True / False |
| max_model_len / max_num_seqs / max_num_batched_tokens | 显式整数 2048 / 1 / 2048 |
| pretrained_model_name_or_path | 显式 /mnt/raid/models/DeepSeek-V4-Pro，沿用用户指定的原位目录 |
| auto_scaling_config | 精确整数 min_instances=0、max_instances=1，无额外字段 |
| GPU / TP / tokenizer | 沿用显式整数8 / 整数8 / deepseek_v4 |
| PP / DP / EP | 沿用1 / 1 / False，省略按已有请求契约解释；仍需实机核对有效值 |
| load_format | 仅 safetensors 或 sllm_v4_grouped_store |

backend_config 采用本实验的封闭字段集合：上述 backend 字段，以及 model_loader_extra_config、pipeline_parallel_size、data_parallel_size、enable_expert_parallel。其他键均明确报错，包含拼写错误、dtype/model/tokenizer 别名、hf_overrides、quantization、speculative_config、enable_lora/lora_adapters 等；即使显式填写某个额外键为默认/关闭值，也须删除该键后按此契约请求。这样 MTP、LoRA、显式量化覆盖及层数缩减等未纳入方案的功能不能进入此服务路径。需要新增参数时先审阅并修订实验配置，不静默扩展范围。

这是 V4 实验服务专用的请求限制，不修改 vLLM 的通用能力，也不约束独立原生/转换工具的参数接口。非 V4 注册保持原样。已有 Store extra 完整性校验、prepared grouped 前置要求及两种 loader 的共同配置绑定保留；native 仍须先完成 grouped 准备。变更不修复所有初始 B1 准入，也不证明环境、实际 GPU/rank、权重内容或后处理状态。

## 复现与验证

新增6项 CPU 测试：缺少冻结字段在 Controller 副作用前拒绝；错误值/数值类型在 StoreManager 绑定和 worker 前拒绝；未经审阅的覆盖/功能字段拒绝；两种 loader 正常复用496 ID；初始伸缩配置拒绝预启动/多实例/隐式默认及 bool/float；旧模型不受限制。

先针对原始源码运行5项：26个子例失败。第一部分修复后291项通过；补充伸缩红例运行6项，其中6个伸缩子例失败。最终修复后完整292/292通过、无跳过/错误/失败。未修改日志以隐藏中间验证过程；两份红例及最终完整日志已归档。成功服务fixture补充真实冻结字段，backend逻辑和假引擎没有改写以迎合校验。

最终源码diff（相对所述父提交，benchmarks/sllm/sllm_store/tests范围）SHA-256：`ae58e37cec00f4269282705d82dd828a2ac2ed2c30716c1df64791a81ef8f9d9`。累计29份生产源码与暂存Git blob逐一核对；证据文件原始字节有独立manifest。frozen-service-profile.json 是共同字段清单，不是可直接执行或已获准入的注册请求。

## 尚缺的实机证明

这里检查的是请求文本，不证明实际 vLLM 二进制/版本、有效engine配置、TP/PP/DP设备分布、HF配置内容与原生混合精度、mHC及量化后处理、缓存/JIT、Store/worker epoch或并发不可变性。指定目录路径也不证明完整64个权重文件SHA与冻结revision一致。

未安装推理环境、读取权重payload、执行真实HTTP/Ray、模型/GPU或正式编号。完整约805 GiB只读hash的范围确认仍待回复；GPU/真实转换恢复和生命周期尚不在当前CPU授权内。本批未调用这些动作。真实A/B后才能继续正式四条件及至少60秒物理释放观察。

远端活动代码/证据仍在 /home/boyjiang/sllm-v4-pro/{code,evidence}，模型原位 /mnt/raid/models/DeepSeek-V4-Pro；GLM参考和Store大数据继续留在既定/data目录。
