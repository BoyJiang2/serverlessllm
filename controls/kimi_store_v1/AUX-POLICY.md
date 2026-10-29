# Marlin/no-actorder 辅助状态政策（单层CPU步骤已实现，未接完整转换器）

依据：vLLM v0.19.1 `compressed_tensors_moe.py` 的
`CompressedTensorsWNA16MarlinMoEMethod.process_weights_after_loading`，本地固定源码
`vllm-w4a16-moe.py` SHA256
`0d84c593350f59f3ff2d45beb3b3ef99dc1f155e6b5b17ffc806000e5857b70f`。
官方冻结配置 actorder=None；实际运行 backend 尚未证明。

## 可以实施的条件与待实现动作

仅当完整配置/来源哈希、vLLM ABI、实际 quant method、Marlin backend、
actorder=None、TP8/PP1/无EP profile 均有运行证据时，才允许为以下四项制定重建例外：
`w13_weight_g_idx`、`w2_weight_g_idx`、`w13_g_idx_sort_indices`、`w2_g_idx_sort_indices`。
必须登记全部60层、八rank的原始元数据及实际参数绑定，不能仅根据名字推断初始化状态。

源代码在非group分支将四项替换为 int32 `[num_experts, 0]`、requires_grad=False，
device继承原w13 g_idx；只读取原shape/device，不使用旧scratch数值。
拟采用明确侧车记录这些项的重建身份、shape/device规则及来源证据，恢复时在正确阶段重建，
不读未初始化payload，不把这些项冒充原checkpoint张量，不把零填充非空数组当作等价替代。
恢复消费者与后处理调用顺序尚未实现，因此当前写入器/指纹入口继续拒绝非空辅助项。

## 不适用范围

Flashinfer分支在此之前转换权重/scale并提前返回，不能套用上述规则。
group actorder、其他quant method、其他版本或profile一律拒绝该例外。
未知buffer、视觉time_weight/freqs_cis、未注册或opaque容器中的状态必须另作政策，不能静默丢弃。

Marlin输入为float8时，后处理还原地改写packed值并将scale乘512，随后执行GPU repack；
所以不能一概称为纯重排。`state_audit.py`只在同rank、同phase、同Torch上下文比较
恢复字节及已收集元数据；pre/post之间必须另建明确变换验证，不能比较SHA相等。

## 准入缺项

完整模型构造、所有必要状态清单、侧车消费者、原生load_dict、八rank实际kernel前后验证，
以及原生服务金标均未通过。本政策草案和CPU测试不授予运行准入。

## CPU实现更新

`marlin_aux.py`现可在上述显式阶段对单层进行全部预检后，将四项前移规范化为空，返回来源与before/after receipt；不会读旧值。测试对照原source else分支。此实现尚未绑定导出侧车、整模型编排或恢复消费者；旧的非空项拒绝继续有效。详见README最新章节，不能把helper成功当作运行政策准入。
