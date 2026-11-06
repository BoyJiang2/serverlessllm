# 完整 V4-Pro 原生参考通过

第六次原生尝试 `native-reference-20261106T205752Z` 已在 qjhs2 的8×H20-3e上完成完整64分片加载、两类请求、正常退出和释放观察。原生参考输出已独立核对并固定为 [native-gold.json](evidence/v4-native-reference-success/successful-run/native-gold.json)，SHA256 `9c67cea181bdfc22f56a063e5b0ffcb3bba1105d178241e8e62c6bcdd59bb4c1`。这只通过原生参考门槛，整体SLLM迁移、完整Store恢复与服务生命周期仍未通过。

业务返回 `4`，token IDs `[22,1]`，自然EOS/stop；fixed32返回32个token，以length结束。两份原生RequestOutput的输入IDs分别与冻结30/43项逐项相等，输出与先保存的原值一致。没有以参考和自身比较来生成parity；未来恢复/服务须与本gold比较。

实际配置：固定vLLM0.20.1/132765e...、权重及tokenizer revision b5968e9...、native deepseek_v4_fp8、BF16激活、TP8/PP1/DP1/EPoff/MTPoff、2048/seq1/batch2048、eager/prefixoff、KV配置fp8、FP4 indexer cache关闭、seed20261105/mp、gpu_memory_utilization0.92。运行镜像和205包安装图沿用P4A；CUDA驱动实际为宿主580.95.05。没有重建引擎或重新完整验算权重SHA，前后文件元数据一致。

上游日志报告权重加载260.49秒，TP0模型加载整体268.19秒、102.42GiB；初始化/profile/KV/warmup报告257.34秒，含首次mHC/FlashInfer编译。八rank模型和请求均实际运行；这些日志子区间不按GLM阶段相加。离线generate调用业务19.09秒、fixed32约8.01秒；没有首token时刻，不能写成TTFT或GLM HTTP启动延迟。该次未执行正式冷/热缓存准入，也不消耗旧12个ID。

从准备到释放审计总耗时723.94秒，在1200秒预算内。容器和守护退出0，未触发硬超时；61个真实释放采样覆盖77.27秒，八卡均回到0MiB/无compute进程，捕获的原worker、控制器和守护身份均消失。GPU采样高水位每卡135295MiB，cgroup memory.current采样高水位251798241280字节；仅是采样下界，内核memory.peak不可用，不能当作精确或全宿主内存峰值。释放结论限本次原生单容器/GPU，不代替Store/IPC/两轮服务验收。

第五次失败也完整保留：新断言错误地将NVML UUID与进程中的NVIDIA_VISIBLE_DEVICES字面值比较，而同镜像运行时该值仍为void。只读NVML复现和第六次启动记录证明实际八卡UUID正确。初步bytes猜测已被实测否定，不作为根因；修正改用只读启动清单，先保存实际值再验证。资源身份以DeviceRequests、启动清单、实际UUID及进程证据为准，不能依赖环境字符串。

原始请求/配置、完整加载和JIT日志、两份响应、物理设备/进程观察、第五次失败及诊断均在 [证据目录](evidence/v4-native-reference-success)。原生JIT二进制留/data，记录SHA供后续同配置复用，避免重复编译；不把这些运行封装修正归为原SLLM或V4量化机制。后续按既定顺序核对完整Store转换/八rank恢复，再验证SLLM生命周期；正式四条件的统计设计与缓存门禁保持独立。
