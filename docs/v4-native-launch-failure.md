# 单次原生参考启动失败与 CPU 修正

本次获用户明确指示直接使用 qjhs2 后，启动了唯一一次已授权容器尝试 `native-reference-20261106T184828Z`。原预约未修改、没有自动重试，也未使用正式四条件 ID。

容器于台北时间 2026-11-07 02:48:53 启动、02:48:54 退出，代码 1、非 OOM。根因是控制器漏掉覆盖环境快照继承的 `/bin/sleep` ENTRYPOINT，实际执行 `/bin/sleep /opt/sllm-v4/bin/python -u ...`，日志为 `sleep: invalid option -- 'u'`。GPU Python 前检、模型构造及请求均未执行，没有原生输出 gold。这是启动编排错误，不能据此判断 V4 模型、H20 内核或 Store 兼容性。

同时，守护在启动期一次 1 秒 Docker inspect 超时后判为 guard_error 并发出 KILL。控制器首次取得状态时容器已退出，因此缺少 pre-exit worker/cgroup 身份；该缺证据错误及原始日志都保留。观测超时不能作为目标终止证明，修正后记录超时原文并继续观察同一 ID，始终保留原绝对 deadline，其他失败路径与硬超时回收保持。

失败后完成 61 个真实采样点，跨度 75.924 秒，最大间隔 1.293 秒；八卡每次均为 0 MiB、compute apps 为空，容器始终 exited/Pid=0，原控制器及守护身份已消失。没有 worker 身份链，因此只报告 GPU/容器的原始退出观察，`physical_cleanup_verified=false`、`runtime_qualified=false`。容器保留供审阅，失败及已消耗的启动尝试不重置。

CPU 修正已具体验证：候选控制器显式指定 `/opt/sllm-v4/bin/python` ENTRYPOINT，调用方必须显式传入审阅后的 source HEAD；在同一不可变镜像的两个无设备 runc 容器中，实际 Python 入口、参数、正常退出和硬超时均通过。六项守护契约包含“单次查询超时不得终止目标或更改期限”。测试没有重建/重装环境，没有 GPU 重试。根 SLLM、vLLM 固定源码、模型配置、请求、精度和正式协议均未改动。

原始失败、执行时控制器/守护源码、完整释放样本及 CPU 修正证据见 [证据目录](evidence/v4-native-launch-failure)。本批只归为启动与记录辅助修正，不计入原 SLLM 或 V4 量化机制成果。继续 GPU 需依照此前“任一门槛失败立即停止，不自动重试”的约定取得下一次单次运行范围；当前没有自动再次执行。
