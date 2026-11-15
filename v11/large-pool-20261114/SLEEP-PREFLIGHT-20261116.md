# Sleep Mode L1/L2 无 GPU 前检

| 模式 | S1 合格轮 | S2 合格轮 | S3 合格轮 | 当前判断 |
| --- | ---: | ---: | ---: | --- |
| 大池 B1 | 3 | 3 | 3 | 历史已验收，见 READINESS.md |
| Sleep L1 | 0 | 0 | 0 | qjhs003 的严格三模型序列容量前检判为 ram_infeasible |
| Sleep L2 | 0 | 0 | 0 | 已有不同边界的先例，当前协议待实测 |

L1 必须让前序 Worker 原地睡眠，再唤醒目标 Worker。按现有先睡旧模型再唤目标的顺序，三个序列都需经过三份权重备份共存。Kimi 和 V4 的单份 L1 备份实测分别为 622,250,164,224 B 与 880,921,280,512 B。GLM 用已知 Store 权重体积 762,527,481,856 B 估算，GLM 完整 L1 备份尚无成功测量。组合约 **2110.10 GiB**，超过节点物理内存 **1923.28 GiB** 约 **186.81 GiB**，尚未计容器、运行时和新模型启动余量。即使 GLM 备份显著低于估算，历史 Kimi → GLM 在写第二份备份时曾把 MemAvailable 压到 **0.316 GiB**，据此保留严谨的不可行判定。节点无 swap，system.slice memory.max 为 max，仍受节点物理 RAM 限制。该判断避免启动已知高风险的 L1 轮次。单模型与中止记录单列。

L2 休眠权重无 CPU 备份。前置使用上一轮 Kimi 成功重载采样峰值 1630.47 GiB，对照原容器 1400 GiB 的 OOM 和成功轮的 1664 GiB 上限。实际入场先等在途 B1 释放，再核对可用内存、各容器上限、页缓存、八卡空闲和 GPU KV 配额。历史 B2 用每卡 16 GiB GPU KV 和 V4 overlay，尚未核验与本轮配置边界的等价性。历史 B2 结果独立保存。

首段使用对应模型的原生 SafeTensors 冷页准入。后续首次出现的目标原生 load，回访目标才从原 Worker wake。对 L2 独立记录旧 GPU KV 释放、权重映射与 checkpoint reload、GPU KV 重建、可服务回执和业务金标。L1 若迁移到具备容量的节点，另记备份与恢复的起止和有效字节。两者均无 LMCache，注册和 Host KV 清理为不适用。当双方端点齐全时比较墙钟跨度，原历史热度和显存配额差异单列。每机授权窗口内先各做 S1/S2/S3 一次，并在通过后补三次独立重复。

只读核对原生容器挂载及 checkpoint 索引，Kimi、GLM、V4 分别有 64、142、64 个分片，所列文件均存在。此检查只验证文件清单，冷页准入在首段放行前执行。Sleep Backend 的独立副本已在 qjhs003 编译，分别记录 sleep 调用边界、L2 权重映射、checkpoint 重载及 KV 恢复边界。原生 sleep 调用只提供整段接点，旧 GPU KV 释放细分仍待 allocator 打点。

隔离 runner 位于 qjhs003 的 /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-sleep-readiness-20261116。它接入固定 S1/S2/S3、现有冷页规则、窗口截止守护和逐段休眠后资源快照。候选配置采用 TP8 和每卡 16 GiB GPU KV，V4 沿用历史 sink/router overlay，Kimi 容器上限为 1664 GiB，另两模型沿用 1400 GiB。已通过实际服务器上的编译、参数入口和未开始窗口拒绝检查，真实性能和跨模型内存峰值待实际运行。

带 allocator 打点的隔离 runner 位于 qjhs003 的 `/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-sleep-readiness-20261116-allocator`。三个模型的 profile 使用同一镜像，生成前验证原 `cumem.py` 的 SHA-256。每次成功返回的解除映射与释放、创建与映射、CPU 备份拷贝和恢复拷贝分别记录操作标签、rank、字节与主机单调时钟起止。KV 映射结束和 Backend 可服务回执是两个接点，后者仍需单独记录。已通过编译和替身调用验证释放、按标签恢复及时间戳。无驱动容器无法导入依赖 `libcuda.so.1` 的原生扩展，实际八 rank 记录待 GPU 验证。

[Sleep Backend 、runner 和 allocator 副本生成脚本](prepare_sleep_timing.py)

[历史 L1 容量证据](../B2-L1-RAM-BOUNDARY.md) · [历史 B2 与 Kimi L2](../report.md#b2--原生-sleep--wake)
