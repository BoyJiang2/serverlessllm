# V4 冻结 KV 配置修订评审

固定目标vLLM0.20.1/132765e...的DeepseekV4MLAAttention要求cache_dtype以fp8开头；当前config中没有KV算法字段，实际上游resolve函数保留auto。CPU执行实际源码中的赋值/assert片段得到：auto失败，fp8通过。这不是构造完整模型或GPU运行证明。

当前v4_convert.export_engine_config没有kv_cache_dtype；v4_service封闭profile接受缺省配置，却拒绝加入kv_cache_dtype=fp8。复现结果：qjhs2:/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106/kv-profile-contradiction.json。

建议本轮只修协议与CPU约束：转换器、服务首配置及loader一致性检查显式绑定kv_cache_dtype=fp8、attention_config.use_fp4_indexer_cache=false；记录上游模型构造后FP8格式到fp8_ds_mla的解析过程。保持BF16激活、原生MXFP4/FP8权重、UE8M0 scale、mHC F32、TP8/EPoff/2048及两份请求不变。旧协议和回执保留，新建运行profile revision，正式12个ID尚未消费。补异常配置CPU检查，再构建新版服务/Store wheel并闭合最终锁；仍不运行GPU或模型。当前仅为可评审提案，代码与冻结协议均未修改。

另一个后续覆盖边界：固定源码仅C4A层建立indexer，max_model_len按compress_ratio=4缩减，2048原始上下文对应最大512个压缩索引位置，小于index_topk=1024。因此不能用原始输入长于1024就声称覆盖非平凡top-k1024排序。P4B仍须记录实际索引长度和选中op；若要验证超过1024候选的排序，应另立受控GPU张量probe或新的上下文协议，不能悄悄改变当前正式请求/2048配置。这里只明确设计边界，未执行probe。

该决策只涉及冻结配置修订，原先P4A无GPU构建授权保持有效；独立vLLM源码编译不依赖此决策。

复现脚本、原始失败及固定上游resolver函数体归档于docs/evidence/v4-kv-review-p4a。这里另收录37个实际Store原生输入/Git一致性与glog/GoogleTest精确commit/tag证据，均不证明GPU恢复。当前KV修订仅为提案，等待用户选择；独立vLLM编译未因该问题停止，已批准的无GPU构建/导入检查继续。
