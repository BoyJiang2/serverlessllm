# 第二次原生启动：监控计数器错误

用户已明确授权修正后再启动一次。独立尝试 `native-reference-20261106T185715Z` 沿用固定配置和镜像，Python ENTRYPOINT 配置正确；但宿主控制器把 `memory.peak` 当作必读文件，而 qjhs2 的 Linux 5.15 不提供它。实际 traceback 指向该读取；控制器异常清理发送 KILL，容器以 137 退出，Docker 的 OOMKilled=false。不能将此错误归为模型 OOM、H20 内核或 vLLM 兼容性失败。

GPU 前检未完成，原生入口和模型构造均未开始，没有模型输出或 gold。本次失败和已消耗的第二次启动记录保留，未自动第三次启动，正式四条件 ID 未使用。

退出后 61 点真实释放观察覆盖 75.831 秒，捕获的原 worker 身份均消失，选定八卡显存每次为 0 MiB、compute apps 每次为空，绑定容器 exited/Pid=0；原控制器及守护也已消失。报告为本次 GPU/单容器释放观察通过，physical_cleanup_verified=false、runtime_qualified=false；不扩展为 SLLM Store/IPC 或完整生命周期验收。

最小修正：共用宿主 cgroup 采集函数保留必需的 memory.current/memory.events/pids.current；可选 memory.peak 缺失明确记录 null，不能填零，也不能把采样最大值冒充内核精确峰值。控制器异常同时保存完整 traceback 和错误路径。该函数已在 qjhs2 同一不可变镜像的真实无设备 CPU 容器 cgroup 上验证，另有六项释放契约通过；不是再次用 GPU 试修。

原始 traceback、释放样本、执行时控制器、CPU 修正及候选控制器见 [证据目录](evidence/v4-kernel-telemetry-failure)。本批属于启动监控辅助功能修正，目标 vLLM、Store、模型、请求与既有协议均未改动。后续完整模型、输出和精确峰值仍未验证。
