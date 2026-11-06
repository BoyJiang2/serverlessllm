# 原生参考的容器外层守护：CPU 验证

`benchmarks/v4_container_guard.py` 为一次完整原生参考补上有具体缺口的外层回收，不启动容器、不分配 GPU、不创建正式实验 ID。它只接受完整 64 位容器 ID、未来不超过 1200 秒的绝对 monotonic deadline，以及独占的新证据目录。目标须尚未启动、带 `sllm.v4.guard=native-reference` 标签、禁止 restart 和 AutoRemove。先记录容器 ID/Created/Image、守护 PID/start_ticks/boot 与期限，再允许控制端继续；控制端须验证 armed 文件和守护进程仍活后才启动。

守护应以独立 session 运行，忽略 SIGHUP；控制端退出后仍生存。deadline 到达、收到退出信号或运行中 Docker 检查异常时向精确 ID 发送 KILL；Docker 调用各自有限超时，轮询 inspect 最长 1 秒，记录实际 kill 请求时刻和原始错误。最终再次检查容器状态、Pid=0 和 RestartCount=0；无法确认则明确失败。不得把 guard 的退出、docker 客户端超时、容器 stopped 或 owner 隔离等同物理释放，也不能据此自动重新准入。Docker daemon/内核异常仍可能阻止及时终止，结果保留为未知并停止后续运行。

已在 qjhs2 固定 CUDA 镜像上串行运行三个无设备、无挂载、runc/无网络的 CPU 容器：正常退出、带多个子进程且忽略 TERM 的硬超时、控制进程先退出且守护收到 SIGHUP 后的硬超时。三项均确认实际容器已退出、Pid=0、之前捕获的全部宿主 worker PID/start_ticks 不再存在；两次 KILL 请求均在各自期限后 1 秒内。仅删除这些已确认停止的精确 CPU 测试容器。另有 5 项异常路径测试。原始身份/状态、时间、日志和 SHA 在 [证据目录](evidence/v4-container-guard-cpu)。本结果不证明真实 GPU、Store、模型或至少 60 秒物理释放。

下一次原生参考仍须资源准入和明确的 GPU/完整模型运行授权。使用真实 64 分片只读模型、v4pro.4/已冻结 vLLM wheel 与 `20261105/mp` 等配置；容器外 1200 秒预算从准备开始，计划最多给模型进程 1080 秒，为回收与至少 60 秒观察留余量，超出或观察不足记失败。真实入口与释放采集接入后，才可称为已具备端到端有界执行能力。本脚本只约束该单容器内的 worker，不覆盖未来 Store daemon、Ray actor 或其他容器；这些生命周期仍分别验收。
