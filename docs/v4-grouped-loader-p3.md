# SLLM × V4-Pro：分组 Store loader（P3，CPU 阶段）

2026-11-06；本批次在 b89f2f1 基础上实现实际逐组导出/恢复。统一 CPU 契约检查 128/128 通过，无跳过。未安装推理环境、运行 GPU 或构建完整 native 扩展；这不是模型推理或容量验收。

## 实现与状态契约

注册 `sllm_v4_grouped_export` 和 `sllm_v4_grouped_store`，保留原两种 loader 及单 slab 容量拒绝。使用固定 vLLM 0.20.1（132765e3560659ff63ebd236203672e991b70e08）的 BaseModelLoader 和原生两遍后处理顺序；9 个相关 Python 源文件哈希作为局部门禁，不代表 wheel/native ABI 验证。

每个 TP rank 保存 remainder 加 61 个专家组，共 62 组、TP8 共 496 个 Store 标识。专家组只含原生 w13/w2 权重及 scale 和同模块同视图别名；其他 buffer 留在 remainder。按原生模块顺序逐组导出，避免整 rank CPU 副本同时驻留；拒绝跨组别名、不同重叠视图及 canonical 文件区间重叠。

外层格式升级为 `sllm-v4-pro-grouped-raw-v2`，内层仍是原 raw-v1 权重契约。此前 v1 仅为原型元数据，必须重新生成；没有实际 Store 数据需要迁移。导出仅在原生后处理成功后写 rank/export_done.json。发布 COMPLETE 要求全部八个 rank 的小型 worker 返回值，与落盘完成标记、manifest 哈希、身份和唯一 GPU UUID 对齐，且校验完整 payload；目录齐全不能替代 worker 成功。

恢复先核对全局 COMPLETE、原生拓扑、量化方法、别名、dtype/shape/stride 和设备要求，再清空 fresh CUDA skeleton 的原始存储。remainder 的 owner 保留至模型生命周期结束；逐个专家组加载、绑定、执行一次原生后处理、同步当前设备，再核查 Python 可见引用并退休本组 owner。最后必须完成全部 61 组，才能标记 postprocessed；该状态不是 MODEL_READY 或业务请求成功。

引用检查覆盖参数/buffer、字典键值、容器、对象字段、继承 slots 和空 view 的底层存储。失败保留模型及当前 owner 的强引用，恢复临时方法，并禁止同 conversion/rank 再次构造模型；需要 rank/process 隔离后重试。此检查不证明任意闭包或 C++ opaque 引用消失，也不证明物理 cudaFree。共享专家 quant_method、CPU/UVA offload、重复或漏执行后处理均拒绝。

## 证据和限制

`docs/evidence/v4-grouped-loader-p3/` 保存最终 128 项日志、回执、CPU 驱动和上游源清单。测试执行实际固定上游 BaseModelLoader/两遍后处理源码；Torch、GPU、Store/RPC 部分使用 CPU 替身。最终测试所用父提交与代码 diff SHA 在回执中绑定。首次统一测试的跨模块别名失败也保留，修复后覆盖异常用例。此前导出替身缺少 CPU API 的调试日志保留在服务器 evidence 中。

容量检查仅是必要下界；不覆盖 allocator/pool 对齐、转换临时量、通信、KV/cache 等。Marlin 输入精度还需冻结实际环境 getter，方法字段可能为 None。mHC/FP8/UE8M0 的布局契约检查不等于数值或 bitwise 验证。

恢复在原生 quantization 后处理内部逐组复制，因此该 span 包含 Store H2D；不能称为纯转换耗时，也不能与其他 span 相加。完整管线/业务计时边界沿用 GLM 定义；分组子事件及完整运行时 recorder 尚待接入。历史 GLM 数据不构成本次 V4 的 PASS。

## 当前路径与后续门槛

代码：qjhs002:/home/boyjiang/sllm-v4-pro/code；脚本和证据：同根 evidence。权重按用户指定原位读取 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro。15 个小资产、index、64 个分片大小和 header 覆盖已核对；未计算完整 tensor payload SHA，ModelScope master 标记不能独自证明固定 HF revision。

Store 大数据继续放 /data/brick1/boyjiang；GLM 原始参考保留 qjhs003:/data/brick1/boyjiang/glm5.1-fp8。/home 与 /mnt/raid 是共享 NFS，未来须披露权重与 Store 的介质差异，客户端清缓存不等于 NFS 服务端冷缓存。旧 /data 代码/证据保留 b89f2f1 回退快照，后续只在 /home 工作。

下一步在现有服务路径接入 496 个组的 StoreManager 注册和八个 worker 返回值协调。`grouped_export_result(model)` 仅提供小字典候选接口，实际 apply_model/collective RPC 传输、序列化尚未验证；不启用 insecure serialization。还需真实完整转换/恢复、ABI、容量准入、gold/bitwise、原生 SLLM 服务、卸载及再次拉起和至少 60 秒物理释放观察。当前授权仍仅 CPU 实现，不消耗正式实验编号。
