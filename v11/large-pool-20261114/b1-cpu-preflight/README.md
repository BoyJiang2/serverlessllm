# 大池 B1 补测 CPU 前检

2026-11-16 在 qjhs003 使用正式 B1 镜像 `sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d`。运行容器使用 runc、无 GPU 设备，检查结束时 PyTorch CUDA 未初始化。

- Worker 用镜像内的 vLLM 源码与正式运行使用的 LMCache 0.5.6 HMA adapter 生成 overlay。CPU 测试覆盖 8 rank、64 条替身事件。固定镜像实际导入四份 overlay，校验文件 SHA-256，并确认 HMA connector 使用该 adapter。[Worker 清单](worker-trace-manifest.json)
- Store 使用镜像内源码和既有 observer 生成打点，Linux C++ 用例覆盖两条读取路径、错误处理与拷贝计数。固定镜像构建 `_checkpoint_store`，实际导入后核对二进制 SHA-256 和 `CheckpointStore` 符号。[构建回执](build-receipt.json)
- 控制器用正式脚本的固定 SHA-256 生成，校验两个 overlay 的摘要并编译。Store 时间日志、逐 rank 权重与 KV 采样、LMCache 注册和清理的端点仍需在实际补测中逐段核验。

qjhs003 工件目录为 `/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b1-cpu-preflight-20261116`，包含 Worker overlay、Store 扩展和构建日志。
