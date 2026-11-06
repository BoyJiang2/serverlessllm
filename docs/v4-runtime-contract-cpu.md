# V4 配置与证据安全修改批次

P4A 的固定源码 vLLM 0.20.1 wheel 已真实构建、安装并完成 CPU 原生导入验收。本批复用该 wheel 和既有 Store 原生二进制，不重编译、不重做 Store 序列化或完整权重校验；旧 P4A v4pro.2 回执保留。本批服务和 Store 为 `0.8.0+v4pro.4`，205 包最终安装图精确匹配新锁，pip check 通过。新锁 SHA256 为 `597fd6743f2d298e9765bf6124efd1949e294c169ef2df1c1efea7fed6b41805`。

注册白名单、转换器及恢复 loader 现在要求固定权重/tokenizer revision `b5968e9190ef611bbf34a7229255be88a0e937c1`、显式 FP8 KV，并关闭 FP4 indexer cache。按用户确认，服务、转换器及下一次原生参考统一 `seed=20261105`、`distributed_executor_backend=mp`。旧协议文件、正式 ID、既有失败记录不改写；本记录只批准这些配置修订，不新增 GPU/模型运行范围，也不冻结新的统计协议。

实际已安装 backend → AsyncEngineArgs → ModelConfig，以及实际 ParallelConfig/CacheConfig 的 CPU 探针通过：TP8/PP1/DP1/EPoff、BF16、上下文 2048、seed/mp、KV `fp8`，原生有效量化名称为 `deepseek_v4_fp8`。转换器请求 `quantization=fp8`、服务由模型配置推导，两者解析一致；gpu_memory_utilization 均为 0.92。loader 据此修正原来的量化名称判断，并允许运行时原生 KV 格式名 `fp8_ds_mla`；后者未由本 CPU 探针证明实际分配。探针私有模型路径只有配置/tokenizer 和 `CONFIG_ONLY_NOT_RUNTIME_WEIGHTS` 标记，没有完整权重，也没有构造 NN 模型、引擎或初始化 CUDA。

本批同时校验引擎构造开始 → ready → 实例发布 → 首 token → generation complete → 收到业务响应的顺序和唯一性。业务与 fixed32 原响应及参考响应在语义验证前分别原子持久化，journal/导出绑定其字节 SHA，并检查内容一致，失败回放保留异常输入。历史 journal 不改写；缺失旧 completion 事件明确列为 legacy 缺证，不能获得运行 PASS。针对本批改动的 116 项 CPU 测试通过；新增逻辑包含失败输入和篡改反例。证据为 JSON 响应对象快照，不声称保留原始 HTTP wire bytes。

原始反例、实际配置、wheel 重打包的二进制逐字节复用证明、最终锁/安装回执及测试日志见 [证据目录](evidence/v4-runtime-contract-cpu)。配置/证据和打包记录属于本批改动；原 SLLM Store/IPC/生命周期机制、0.11.2→0.20.1 接口迁移、V4 量化和分组状态增量仍分别归因，不把既有 Kimi/GLM 修复或自定义分组格式称为 V4 新增的原论文机制。

下一次有界原生参考：先完成资源重新准入、宿主 driver/H20 兼容性及完整模型容量门槛，并在占用 GPU 前告知。使用真实 `/mnt/raid/models/DeepSeek-V4-Pro` 只读权重，拒绝 config-only 标记或缺少 64 分片的目录；沿用上述配置、eager/prefixoff、业务自然 EOS 后再 fixed32，首次正确输出形成参考 gold。只执行一次完整 SafeTensors 参考，不做 Store 转换或正式四条件，不消耗旧 12 个 ID。

真实运行前仍须落实 1200 秒外层硬超时、独立守护及精确容器身份回收；超时约束覆盖容器内全部 mp worker，不能只超时 docker 客户端。工作预算必须为退出及至少 60 秒释放观察留余量。OOM、输出不符、超时、owner 或进程回收不确定立即停止；隔离失败 owner 不等于物理清理，清理证据不全不得再准入。当前无 GPU/模型/完整 Store/服务生命周期或物理释放验收结论。
