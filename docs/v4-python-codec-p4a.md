# P4A 实际 Python 保存入口与安装源码一致性

本批次没有算法改动，也未重跑此前299项CPU契约。服务器Git提交6a12b66与实际服务/Store v4pro.2 wheel内52个Python、JSON、proto及typing源码逐字节相等，当前venv中对应52个安装文件的SHA也全部相等；没有用本地源码shadow安装包。

在实际Torch2.11.0+cu130和真实_C扩展下调用sllm_store.torch.save_dict，仅输入tiny CPU张量。MXFP4打包I8、FP8 e4m3fn、UE8M0 e8m0fnu、BF16、mHC FP32、I64各256字节，外加精确存储别名；tensor_index.json中dtype/shape/stride/size正确，别名offset=0与原tensor相同。各有效载荷均与bytes(range(256))严格相等，保留包括特殊编码在内的原始位。

原生O_DIRECT生成的tensor.data_0实际4096字节，SHA a5d0f3ecf00a5104be264073b460387ba33acdc13874404e0df4de3aea857936，真实数据及索引一起归档。这是实际Python包装和C++保存链路及CPU读回，不是原生GPU恢复/IPC、完整Store转换、rank恢复或模型数值gold。

vLLM源码编译仍进行中；最终依赖锁、完整引擎/模型类CPU导入、ELF和tokenizer复验尚未完成。没有启动GPU、模型或服务，不消耗正式12个实验ID。
