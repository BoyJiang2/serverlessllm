# SLLM × V4-Pro：CPU 读取终态与分组 host pool（P3 CPU）

2026-11-06；父提交 0a79e2504c6a68c2c36f58961d52e02ec1724748。统一 CPU 契约 159 项通过，无跳过。未安装推理环境、构建完整 native 扩展或启动真实 Ray/gRPC/Store/GPU。本批不是 pinned-memory、容量、缓存命中或物理释放的运行证明。

## Native / RPC 完成契约

原 LoadModelFromDiskAsync 只把 future 放入队列，CPU Confirm 不支持；而 Model::ToHost 在已有读取 LOADING 时也可返回 0。因此重复提交的返回值不能证明实际磁盘读取已结束。

现在所有 CPU 提交（含旧客户端不带 ID 的提交）进入按 Store ID 保存的 shared_future 记录。并发请求关联已有实际读取，不另造一个提前返回的完成任务。确认型请求带唯一 ID，重复 ID 拒绝；失败或异常保留为终态，后续不自动重试。正常完成后新请求可再次读取/命中当前 host，适用于驱逐后的再次加载。旧无 ID API 仍可提交，但不能用空 ID 获取确认。

WaitModelInCpu 先在元数据锁外等待对应 future，再检查当前 Model 是否为 LOADED 且仍有 pinned chunks；未知 ID、失败、异常、缺模型或已驱逐均失败。历史成功不能单独作为当前驻留证据。ClearMem 对未完成/失败的 CPU 任务拒绝清理；UnloadModelFromHost 同时拒绝未完成/失败的 CPU 或 GPU 任务，保留状态供进程隔离处理。

复用现有 protobuf replica_uuid 字段传递 CPU submission ID，无 schema 修改。server 的 CPU Confirm 通过 asyncio.to_thread 等待；CPU wait 和 host unload 的 pybind 接口释放 GIL。新增 client.load_cpu_confirmed 返回已确认 ID，client.confirm_cpu_model_loaded 重查同一 ID。请求或响应 Store ID 不匹配不能成功；传输异常直接传播。新 server/client/native 必须一起升级重建，当前并未安装或完成 ABI 验证。

## 分组 pool 与生命周期

每个组的 native tensor.data_N 字节数已在前批核验。现在 reservation 按 sum(ceil(group_bytes/chunk_size)) 计算；不能对全模型 sum(bytes) 只取整一次。完整 cohort 超过 pool 时，在任何 CPU load 前拒绝。

原生服务分组 memory_pool placement 已解除临时拒绝。load_to_host 在现有队列中等待全部组 load + CPU Confirm，再重查全组驻留后才发布 pinned_memory_pool。StoreManager 预取使用已注册的 model_name，而非 HF 源路径。重复预取会重新确认驻留；队列请求取消不取消底层完成记录。

在开始分组加载前保留整个 cohort 的 rounded reservation。部分读取失败、确认失败、底层调度取消或部分卸载失败时，不发布 pool-hit，不归还计数，保存 receipts/inflight/unloaded 和 failed 状态，要求隔离 Store 后重试。取消 await 不证明真实 RPC 或线程已经停止。计数是保守管理记录，不是物理 pinned byte 测量。

StoreManager 暴露 confirm_grouped_host(node_id, model_name) 和 unload_grouped_host(node_id, model_name)。后者拒绝排队或未发布的 cohort；显式卸载和 LRU 共用逐组卸载 RPC/响应 Store ID 核验。全部成功后才删除 pool marker、归还完整 rounded reservation 并移除完成记录，允许下一次使用新 submission ID 拉起。部分失败保留完整 reservation，即使一部分物理数据已释放。

## 缓存与计时边界

这些确认是当前驻留观察，不是 lease；并发 native LRU、外部 Store 请求或清理仍可使后续失效。正式 pool-hit 准入必须控制这些活动，检查完整 cohort 的新鲜确认并绑定本次 Store/进程身份；不能只拿 get_store_info 的历史 marker 当准入证据。旧模型 pool 计数/预取行为没有泛化重写，混合旧模型的资源账本不能据此声称已修复。

完整 payload 哈希仍属于前置准入，会读取文件并污染页缓存。本批 CPU Confirm 只查任务/当前状态，不重新哈希；它不解决 Controller 注册、router 启动与 cold-cache gate 的时序。正式 runner 仍需落实原 GLM command/trigger/first-token/业务响应边界，不能为排除准备成本事后缩短边界。分组 H2D 与原生后处理交错，quantization span 不能称为纯转换耗时。

## 验证与剩余工作

新增 17 项用例：直接编译实际 C++ 提交/等待/ClearMem/卸载和 IsHostLoaded 方法，以可控读任务验证 pending join、重复/迟到确认、异常、失败保持、metadata 锁可用、驱逐后确认失败和再次加载；执行实际 Python client/server/StoreManager AST，覆盖 CPU ID 传递、事件循环响应、分组取整、完整等待、部分失败、取消、显式/LRU 卸载及再次拉起。C++ 磁盘 worker/内存对象及 RPC 均为替身，小 cohort 生命周期测试不能替代完整 TP8；完整 496 ID 的元数据/注册覆盖继续在统一套件中运行。

首次统一 159 项有一个旧 IPC-close 夹具编译失败：其源码截取范围把新增 IsHostLoaded 方法也包含进去。仅修正该夹具边界，随后 159/159 通过；初次失败、最终日志/回执与驱动归档于 docs/evidence/v4-host-contract-p3/。源码换行按 Git 父提交恢复，最终回执的 diff SHA 对应提交代码。

下一步是实际八 export worker 返回值协调、完整 phase/event recorder 和严格缓存准入控制。真实磁盘读取/异常传播到 GPU 消费者、完整 native ABI、全权重 payload SHA、真实 bitwise/gold、容量与暂存峰值、原生服务卸载/再拉起及至少 60 秒物理释放均未验证。未消耗正式实验编号，不启用 insecure serialization。

工作路径 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}；原位权重 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro；Store 大数据仍在 /data/brick1/boyjiang。GLM 原始参考和旧 /data 工作快照未改动。
