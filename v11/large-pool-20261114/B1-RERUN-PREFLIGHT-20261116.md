# V11 B1 固定七段复跑前检 · 2026-11-16

历史 CPU 修订保存在 9481201。历史 B1 正式口径继续使用 052724Z（1 TiB 权重池、16 GiB Host KV，七段均走 Store）。本次要同时改变首段加载路径与两池容量，两处差异须进入正式回执。

## 已核实的复用条件

| 项目 | qjhs003 只读前检 |
| --- | --- |
| 固定顺序 | Kimi → GLM → V4 → GLM → Kimi → V4 → Kimi（S1）。先验收 S1，再按预约窗口考虑 GLM 首尾的 S2 和 V4 首尾的 S3。 |
| 正式目标容量 | 864 GiB 权重池和 813 GiB Host KV。现有 runner 的容器内存上限为 Store 1280 GiB、LMCache 877 GiB。上限与实际分配分列。 |
| 最大 Store 需求 | 历史 S1 V4 全未命中时读取 877,729,513,472 字节，分配 26,552 个 32 MiB 块，即 829.75 GiB。864 GiB 池相对该轮块需求余 34.25 GiB。 |
| 主机 RAM | MemTotal 2065109635072 B，MemAvailable 2017276002304 B，既有门槛为 2015413403648 B（两池加 200 GiB），当前只多 1.73 GiB。无 swap。 |
| cgroup 和锁页 | cgroup v2，system.slice 的 memory.max 为 max，读取时 shell memlock 为 unlimited。runner 为两个池容器显式传 memlock=-1:-1，实际限额和锁页结果在启动回执核实。 |
| 在途与服务 | V11 Active lock 可获取，V11 静态前检通过，八卡采样均为零、既有服务端口可用。开跑前重复准入。 |

前检原始回执见 [preflight-20261116.json](preflight-20261116.json)。服务器源在 /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-large-pool-20261113/preflight-20261116.2LTtbm/preflight.json。历史 runner 在同一目录下，v11_run.py SHA-256 为 4f0801143942b8e34ef804d96e5e2604bf79498ebc09e68f68edc63d56e882f6。

## 开跑门槛和测量

1. 核对用户预约、八卡、端口、独占锁和 RAM 余量。记录实际配额、镜像、LMCache overlay 和命令。新 run ID 沿用同一 runner 的 UTC 命名。
2. 首段保持 LMCache 配置，从原生 SafeTensors 启动。现有 helper 在放行前逐文件驱逐、核验驻留页为零，保存清单和位图，准入失败停止。首段 Store 仍空，第 2 段才首次访问 Store。
3. 复用 Host KV 与 Store 的初始化起止、八 rank GPU KV 分配端点、Store 查找/驱逐/完成事件、业务金标、填充请求、KV 清理以及逐阶段进程和 HBM 采样。初始化两段只有串行验证通过才相加，总准备时间独立取控制器准备起点至首段放行。
4. 既有 LMCache 日志有 STORE 完成时间和内部打印用时。实际开始、结束和请求的重叠需要同一时钟域的直接打点。现有 HBM 采样有设备总量及按 PID 的归属，权重和 GPU KV 拆分需要分配证据。缺失分项在报告留空，释放用原 60 秒门槛。
5. 完成 S1 后再排后续序列。B1 验证 Host KV 写入和清零，读取命中需要另设场景。

本文件记录 CPU 前检。新轮执行及其验收结果尚待用户提供预约窗口。
