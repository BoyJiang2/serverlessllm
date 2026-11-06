# SLLM × V4-Pro：Store 流水线失败传播（P3 CPU）

2026-11-06；父提交 9d1d86e01fc587317ed1ac62f3476989837d9b5c。修复 host 数据尚未就绪及磁盘读取失败时 GPU 消费者的崩溃/等待路径，保留已读 chunk 与后续磁盘读取重叠的机制。最终统一 182 项 CPU 检查通过，无跳过；没有安装推理环境、构建完整 native 扩展或运行真实 Store/GPU。

## 修改前复现

使用完整实际 Model.cpp / model.h、实际 ConcurrentVector/Queue 和 CheckpointStore 的读取/提交/CPU Confirm 方法，在独立 CPU 子进程编译执行。只有 pinned allocator、CUDA 调用与日志头为替身；临时文件使用真实 buffered pread，测试钩子去掉 O_DIRECT 并注入错误。不是完整扩展或驱动运行。

初始四项测试出现六个失败子场景：未提交 CPU 读取、分配失败、pool 不足、open 失败共四个 SIGSEGV；短读和读取异常各有一次在有界观察内无法结束，子进程报 pipeline did not terminate。正常重叠复制和普通 copy/device 错误控制则可结束。原始 before 日志及父提交五个相关源文件 SHA 收录于 docs/evidence/v4-store-pipeline-p3/；初始 fixture 后续扩展为五项测试，不能把两次用例集合视为完全相同。

根因：ToGpu 的 dispatcher 可在 host vector 尚未初始化时访问它；短读时 ToHost 等 GPU 结束，而 dispatcher/consumer 又等待永远不会生产的 chunk；dispatcher future 的返回值原先被忽略。旧的部分方法/替身测试没有覆盖这条完整并发链路。

## 机制改动与运行时契约

| 接点 | 改动与必要边界 |
|---|---|
| LoadModelFromDiskAsync | 同步标记读取 pending，再启动/关联原读取 future；正常返回、读取异常或任务创建异常都通知 Model 完成状态。失败不自动重试 |
| ToGpu | 只等待 host 数据结构可用或 producer 失败，不等待完整权重读完；没有读取且无可用 host 时失败。注册 replica 后统一走完成/IPC-close 路径 |
| FinishHostLoad | 失败时锁存 INTERRUPTED，关闭 host chunk vector，取消/唤醒进行中的 GPU queues；保留 raw storage，不与消费者相互等待 |
| ConcurrentVector/Queue | 增加终态 close，唤醒所有等待者，已入队项目可排空，空且关闭返回零大小 sentinel；关闭后不再接收新项目。每个新 replica 使用新队列 |
| 分发与复制 future | 分发结果/异常及消费者异常共同决定失败；退出时关闭 queues。分发或复制线程失败不能因其他线程正常退出而变成成功 |
| raw owner | dispatcher/consumer 捕获对应 host memory/vector 的 shared owner；消费者停止前不会依赖一个可被替换的全局指针 |
| 状态与确认 | 跨线程 host/replica 状态改为 atomic；CANCELLED 也算失败，不能落入成功确认；既有 Store shared_future 终态/迟到 Confirm 契约保留 |
| 释放与重试 | 失败 host/replica 拒绝 FreeHost/TryFreeHost；ClearMem 遇到释放失败不再清空 metadata。正常已卸载 host 的重复释放幂等成功 |

二次分配仍返回正的缺口时直接失败。移除读取错误后立即 FreeHost 的路径，避免在消费者尚未停止时释放 raw；失败进程仍需隔离。ClearMem 不是事务，可能已有健康模型被释放再遇到失败，调用方不能由 metadata 仍在推断物理缓存还在。

本批没有改持久化张量、dtype、别名或后处理格式；raw-v1/grouped-v2 保持不变。pending、关闭队列、failed replica 与 owner 是进程运行时状态，不进入 Store 权重表示。旧 loaded_size 累计目前未用于输出，按精准修改要求保留，没有顺带清理相邻旧代码。

## CPU 验证范围

新增五项完整源码测试，覆盖十二个具体场景：缺 CPU 读取、正常重叠、分配失败、pool 不足、open 失败、短读、读取异常、CUDA-copy 返回失败、device 返回失败、dispatcher 异常、消费者异常，以及 CPU 失败而 copy 尚未返回。另保留全部既有 grouped loader、注册/pool、转换协调、IPC-close、请求/生命周期回归，总计 182 项。

重叠测试阻塞最后一次 pread，先观察首个 chunk 已调用 copy，再解除读取阻塞，最终逐字节校验 8192 字节输出。未用整模型 host barrier 消除故障。copy-pending 测试在复制调用仍阻塞时确认取消状态不成功，FreeHost/TryFreeHost 均不能释放 owner；解除后确认持续失败。失败时完整源码 ClearMem 保留模型，成功路径可清理。

测试使用 CPU std::memcpy/CUDA-return-code 替身、普通内存分配及临时文件，不能证明真实 CUDA copy、IPC-close、pinned pool、O_DIRECT/NFS、805 GiB 权重或数值正确性。g++ 编译完整 Model 源码也不能代替 native extension/Torch/CUDA ABI 验证；本次修改包含 native 结构和逻辑，未来必须一起重建。

所有原始失败、修复后定向日志、最终 182 项日志/回执、CPU 驱动与复现来源清单均归档。回执绑定父提交及实际代码 diff SHA；没有复用历史 GLM PASS、缩短正式超时或占用正式实验编号。

## 下一步与数据对齐

下一步迁入完整 phase/event recorder 和缓存准入控制，再按授权范围开展后续资格验证。真实 I/O 或 CUDA 调用本身长期阻塞时仍需外层超时/进程隔离；CPU 用例不是故障后的硬件回收证明，也没有执行至少 60 秒释放观察。

GLM 的 command/trigger、backend 首 token、正确完整业务响应、嵌套 phase span 和失败分母定义继续保持。新的等待只覆盖初始化，流水线仍重叠；不能将 CPU 修复耗时当作 V4 性能结果。分组 H2D 与 quantization 交错、全 payload 准入哈希污染缓存、Controller/router/cache gate 时序仍需正式控制落实。

活动代码/证据 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}；现有权重 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro 原位保留；Store 大数据仍在 /data/brick1/boyjiang。GLM 原始参考与旧 /data 回退快照未改动。
