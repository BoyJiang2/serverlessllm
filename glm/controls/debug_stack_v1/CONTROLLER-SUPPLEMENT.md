# GLM 四条件 controller 补充批次：准备中，不是运行批准

目的：独立测量完整部署生命周期，不修改原 40 项（35 成功/5 失败）、C23 主样本或历史 helper 时长。新批次每条件一次，n=1，不给出重复性 CI，不将新 controller 与旧 Prep/A 拼接成同一观测。

## 已核实与缺口

2026-11-09 15:48（+08:00）只读快照：qjhs003 实际主机 qjhs3，UID 2147；8×H20-3e 各 143771 MiB、0 MiB 占用、无计算进程；主机约 1923 GiB，可用约 1880 GiB；原镜像 aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f 存在，checkpoint 与 rank_0…rank_7 Store 资产存在。未重新读取权重或建立副本。其他合法容器存在，未操作它们。快照不代表后续准入。

15:56:46 只读核验：/data/brick1 是 /dev/nvme1n1 上的本地 XFS，剩余 861.9G；两 NUMA 节点。现有 sllm-debug-guard.timer active，ExecStart 为 /usr/bin/python3 -I /usr/local/lib/sllm-debug-guard/debug_guard.py；脚本 root:644、4228 字节，SHA256 3d70fc4ad03f54ff474aa3a492b664c9bddae341b780b78f9e561a263141f342；root 心跳 unix=1794210994.3972726、actions=0。这不是新批次截止 probe，也不批准运行。

用户本轮明确整机独占；预约 API 请求超时，实际起止时间仍未取得。不得由“随便用”推导无限窗口，不重用旧预约。当前没有启动容器、模型、Store 池或缓存控制。

## 冻结条件与计量边界

保持官方 GLM-5.1-FP8 revision f396cf805182f4ca10fa675e1a99815b3ca384db、原镜像/依赖、TP8、上下文2048、并发1、eager、MTP 关闭、官方 FP8 与原金标。顺序为 safetensors_disk_cold、safetensors_disk_hot、store_disk_cold、store_pool_hit；每条新身份独立计入成功/失败，失败不伪装成功，不消费旧正式 ID。

四条均保留 768799145984 字节 Store 池（716 GiB）；SF 不能沿用调试默认 64 MiB 池。池命中须逐 rank 原生确认，SF 热读须冻结完整输入列表及完整 mincore 全驻留，冷读须目标列表全部页面零驻留。复用 C23 的文件级控制与 NUMA 放置；不能清全机缓存。元数据身份读取必须在最终驱逐/扫描前完成。

主区间：宿主 controller 入口（资源核验、代码/镜像恢复、部署之前）到本实验所有容器/辅助进程退出、释放核验和证据落盘完成；同一宿主 boot ID、CLOCK_MONOTONIC，记录 PID/startticks、精确容器 ID/cgroup 与 run/holder。另列部署到首正确算术响应、实例生命周期和完整退出。注册后的请求顺序 arithmetic→translation→fixed32；逐 token/文本/finish_reason/有限 logprob 校验。非流式 HTTP 完成不称最后 token。

删除模型后至少观察 60 秒，再完整退出 Store、Ray、head/worker、监控及本实验辅助进程；记录 GPU、匿名内存、锁页、共享内存和后台 I/O 的归属证据。不得以全局 GPU 清空或 Docker 消失单独认定全部释放。额外释放观察计入 controller 总成本。不得将现有 debug controller_elapsed_s 直接当上述完整指标。

新批次每条从零初始化公共栈，旧 C23 为跨 trial 共用栈；因此是新的补充协议层，不能追填 C23 controller 栏。报告增列新批次及清晰边界；原栏仍保留缺测。

## 预算与停止

建议预约 4 小时仅为规划，不是已证明可执行的硬预算。每条拟采用单一绝对工作截止 1800 秒（包含代码检查、可选镜像恢复、建池、缓存控制、加载和三请求，子步骤不得重置累计截止），之后预算删除180秒、实例观察60秒、worker退出320秒、head退出70秒、全退出观察60秒、证据120秒，共2610秒/条。四条10440秒（2小时54分），另留守护安装/probe/资源核验与窗口末尾清理余量。实现及 R1 必须证明所有操作有界、守护最坏扫描/停止时间也能在授权截止前完成；未证明不能靠本段估计启动。

现有 stack 的镜像恢复发生在容器创建前，现有容器 timeout 不能保护这段；必须落实宿主绝对截止，不能仅靠旧 debug timer。异常保留原始证据并停止下一条，无新增证据不重复同类失败。实际剩余窗口不足完整单条最大预算＋取证/守护余量时不启动。

## 剩余实现与验收

1. 已补现有 debug_stack.py 的宿主入口/退出单调端点，缺失或跨 boot 时 null，42项既有及新增 CPU 测试通过；没有 GPU 数据。
2. 在已有 debug_service.py / debug_stack.py 复用 C23 原语接入四条件完整缓存准入、统一池配置、三金标请求、删除后观察、资源归属监控、finally 释放与总截止。不得复制旧 formal gate 冒充新批准，不能只更改 purpose 标签。
3. 冻结当前代码 commit/完整源码与配置 manifest，完整独立 R1；核验实际窗口、资源/共享 I/O、宿主守护身份和截止/probe。未实现、未审阅或证据不足就停在 CPU 准备。
4. 获准后四条按顺序各运行一次，保留失败和未开始计数。新增数据单列，不重跑原40项，不宣称论文原样复现。运行及释放观察期间不 Git/同步。

当前状态：CPU 计量缺口修复完成；完整补充入口、R1、实际截止与 probe 尚未完成，未开跑。
