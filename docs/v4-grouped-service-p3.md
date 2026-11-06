# SLLM × V4-Pro：原生 StoreManager 分组注册（P3 CPU）

2026-11-06；父提交 491699faf223dcc27f710b383c622a10cf880399。本批接通已有 COMPLETE grouped Store 的服务注册路径，仍未运行真实 Ray、gRPC、Store、GPU 或安装推理环境。

## 行为和契约

StoreManager 对 `sllm_v4_grouped_store` 跳过旧模型下载/转换器。SllmLocalStore 在指定 `worker_id_<node>` 上运行命名 Ray task，通过该 worker 的 STORAGE_PATH 解析冻结的绝对 store_path，核验 TP8、身份、全部 COMPLETE/worker 完成标记、manifest 及所有 payload 哈希。不会假定控制进程可读取 worker 的 /data。

纯 stdlib 的 v4_service.grouped_registration_plan 生成每 rank 62 组、合计 496 个相对 Store ID。计费字节数来自 tensor.data_N 分片，不包含 JSON。原生 Model::Initialize 遇到分片编号缺口会停止读取，因此额外拒绝编号缺口、零字节组及非连续编号；已签入一致 manifest/COMPLETE 的缺口也不能准入。

控制侧使用现有 SllmStoreClient.register_model RPC，逐组在线程中调用，核对每组返回字节数与核验计划一致。全部成功后才发布 disk_models/model_storage_info/model_info；注册接收成功不是 CPU 权重驻留完成，也不是 MODEL_READY。重复注册仅在冻结 identity/store_path 一致时复用已注册大小。

负数、错误大小、bool、异常或取消均不发布可用模型。记录已注册 ID 和可能仍在途的 RPC，阻止同模型或同数据集换名重试。原生 Store 没有 unregister 事务，不能宣称失败会回滚 metadata；应隔离 Store 后重试。CPU 测试不证明取消能够中断真实 RPC 或线程。

## 预取与正式实验门槛

检查确认原有 load_into_cpu 仅等待异步请求接收，不能证明磁盘到 host 的全部权重已驻留。本批暂拒绝 grouped memory_pool placement 和直接 host prefetch，避免发布虚假的 pool-hit。后续需完成原生 CPU-copy 终态确认，并按每组分别 ceil(size/chunk_size) 累计 pinned pool，而非对总大小只取整一次；还要处理部分加载、失败、驱逐和再拉起。旧模型路径保持原行为，未泛化修改其已有生命周期。

worker 全 payload 哈希会读取全部 Store 数据并污染文件页缓存。它属于准入准备，不得混入原生 loader/H2D phase 或冒充冷缓存；正式 runner 必须证实该准备与冻结的 startup_command/trigger 边界关系，并在准入后重新执行对应缓存 gate。当前 Controller 注册与 router 启动相连，尚未证明能在两者之间执行严格缓存 gate，因此本批不是正式实验可运行证明。若原始命令边界包含该准入，应保留其真实耗时并披露差异，不能事后缩短 GLM 对齐边界。NFS 服务端缓存仍不受客户端 drop-cache 控制。

## CPU 证据与下一步

统一套件 142 项通过，无跳过。新增 14 项覆盖：真实小型 TP8 文件系统布局/全 payload 校验、损坏、分片缺口、目标 worker 路由、496 次注册、错大小/异常/取消、部分注册隔离与换名拒绝、幂等身份、旧 rank 注册回归、上层跳过旧 converter、失败不发布 placement 及预取门禁。执行实际 StoreManager AST；Ray 和客户端 RPC 使用替身。首次运行因测试命名空间漏 SllmStoreClient 类型名而失败，修复 harness 后通过，日志保留。

源码 CRLF 归一导致的全文件 diff 已修正为原始 LF；最终回执对应实际提交代码 diff。日志、回执和驱动归档于 docs/evidence/v4-grouped-service-p3/。

下一步先补 CPU-copy 终态/分组 pool 核算，再接八个 export worker 小字典返回值的实际协调、完整 recorder 与缓存准入控制。grouped_export_result 目前仍仅是 importable 候选接口；不启用 insecure serialization。ABI、完整权重 SHA、真实 bitwise/gold、容量准入、完整原生服务、卸载/再拉起和至少 60 秒物理释放均未验证，正式实验编号未使用。

活动目录仍为 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}；权重按用户指定原位读取 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro；Store 大数据留 /data/brick1/boyjiang，旧工作目录保持回退快照。
