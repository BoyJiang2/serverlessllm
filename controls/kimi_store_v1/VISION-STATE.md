# 视觉状态源码核验与当前缺口

固定vLLM v0.19.1视觉源码已保存为fixtures/vllm_vision.py，SHA256
`2d4ff4817698cda5a8e3c9845c6c5e3e97fee8a78c7f1b4a5b05fb8ddd8c5a37`。
以下是源码及小CPU组件证据，不是实际Kimi模型/视觉业务验证。

| 状态 | 来源与必要性 | 当前处理 |
| --- | --- | --- |
| vision_tower.patch_embed.pos_emb.time_weight | 构造时由NumPy位置函数生成、注册persistent=False，多帧位置编码会读取；Kimi外层随后对整个视觉塔执行to(device,dtype=model_config.dtype) | 必须保留实际dtype和原始值；不能因不在state_dict或文本请求暂时不用而丢弃。现有CPU写入枚举全部buffers，支持实际BF16/F32；尚未实际构造NumPy原buffer或验证多帧输出。 |
| vision_tower.encoder.rope_2d.freqs_cis | 首次get_freqs_cis时注册persistent=False；使用torch.polar生成complex64，后续调用复用缓存 | 缓存不存在时没有字节可保存，但须记录缺席/生成阶段；存在时当前写入在任何文件创建前拒绝。不丢弃、不把complex转实数，也不将其标为已支持。 |

Rope源码在encoder中使用max_height=max_width=512、head_dim=1152/16=72。
因此完整缓存按源码/config推导为[512,512,36] complex64，约72MiB/副本；
若八rank各持有一份即576MiB。这是推导预算，未观测实际驻留，也不代表所有部署都同时生成缓存。

固定SLLM归档C++ dtype表不含complex64（checkpoint.cpp SHA256
`eb731fab981dae01d752e0cd3a4539cbffeae415d50eabb030d0acb9f3de8099`），
所以仅给Python格式添加complex64标签不能解决原生RestoreTensors支持问题。
后续必须选择并审阅保留完整complex位模式的编码/恢复规则或原生dtype补丁，
验证实际消费者后才支持已有缓存状态；不能以简单cast/删缓存绕过。

CPU测试执行固定Rope类的原始源码，使用dim8、2x3小网格：确认首次注册、complex64、
nonpersistent、二次复用及写前拒绝；不构造512x512缓存，不调用GPU。
time_weight仅测试原注册语法及Torch buffer dtype转换语义，没有仿造NumPy构造数值。

`runtime_inventory`另扫描Module属性中的标准dict/OrderedDict/list/tuple/set/frozenset，
发现其中Tensor时作为container_tensor记录并阻止导出；记录循环路径、未展开自定义对象类型，
不读取Tensor值或输出字典键内容。仅标准容器扫描完成不等于完整opaque状态审计。
递归深度32、节点100000超限直接拒绝，不把截断结果当成功清单。

## 原始位编码进展（当前CPU候选）

新增显式complex64-int32-bits-v1及布局侧车，保留complex完整位模式和相同布局alias；可完成CPU小buffer写入/读回/decode，不能把该成果当作C++新增complex支持。默认无codec仍拒绝现有缓存，真实CUDA消费者与视觉业务仍待接线验证。见README最新章节。
