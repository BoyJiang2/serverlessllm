# GLM 四条件 controller 补充批次：准备中，不是运行批准

目的：独立测量完整部署生命周期，不修改原 40 项（35 成功/5 失败）、C23 主样本或历史 helper 时长。新批次每条件一次，n=1，不给出重复性 CI，不将新 controller 与旧 Prep/A 拼接成同一观测。

## 已核实与缺口

2026-11-09 15:48（+08:00）只读快照：qjhs003 实际主机 qjhs3，UID 2147；8×H20-3e 各 143771 MiB、0 MiB 占用、无计算进程；主机约 1923 GiB，可用约 1880 GiB；原镜像 aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f 存在，checkpoint 与 rank_0…rank_7 Store 资产存在。未重新读取权重或建立副本。其他合法容器存在，未操作它们。快照不代表后续准入。

15:56:46 只读核验：/data/brick1 是 /dev/nvme1n1 上的本地 XFS，剩余 861.9G；两 NUMA 节点。现有 sllm-debug-guard.timer active，ExecStart 为 /usr/bin/python3 -I /usr/local/lib/sllm-debug-guard/debug_guard.py；脚本 root:644、4228 字节，SHA256 3d70fc4ad03f54ff474aa3a492b664c9bddae341b780b78f9e561a263141f342；root 心跳 unix=1794210994.3972726、actions=0。这不是新批次截止 probe，也不批准运行。

用户本轮明确整机独占，随后明确“直接用，目前机器没人用”，按新的有界短借用授权处理，不再要求再次确认预约；预约 API 超时不冒充已查到预约。不能由此推导无限窗口，不重用旧预约。开跑前须冻结本实验短借用的绝对工作/清理截止，核验所有资源及独立守护/probe；他人资源重新出现时停止启动下一条。当前没有启动容器、模型、Store 池或缓存控制。

## 冻结条件与计量边界

保持官方 GLM-5.1-FP8 revision f396cf805182f4ca10fa675e1a99815b3ca384db、原镜像/依赖、TP8、上下文2048、并发1、eager、MTP 关闭、官方 FP8 与原金标。顺序为 safetensors_disk_cold、safetensors_disk_hot、store_disk_cold、store_pool_hit；每条新身份独立计入成功/失败，失败不伪装成功，不消费旧正式 ID。

四条均保留 768799145984 字节 Store 池（716 GiB）；SF 不能沿用调试默认 64 MiB 池。池命中须逐 rank 原生确认，SF 热读须冻结完整输入列表及完整 mincore 全驻留，冷读须目标列表全部页面零驻留。复用 C23 的文件级控制与 NUMA 放置；不能清全机缓存。元数据身份读取必须在最终驱逐/扫描前完成。

冻结主区间：新root管理的systemd瞬态controller service之ExecMainStartTimestampMonotonic，到该Main进程正常退出的ExecMainExitTimestampMonotonic（微秒精度，转换为ns）；Main退出之前必须完成本实验栈正常受控退出及全退出后至少60秒释放观察、证据落盘；同一宿主 boot ID、CLOCK_MONOTONIC，记录 PID/startticks、精确容器 ID/cgroup 与 run/holder。证据最终收据自身的写入不递归计时，终点之后不能再做实验清理；异常退出单列失败。另列部署到首正确算术响应、实例生命周期和完整退出。注册后的请求顺序 arithmetic→translation→fixed32；逐 token/文本/finish_reason/有限 logprob 校验。非流式 HTTP 完成不称最后 token。

每条统一输出：Controller=上述完整宿主区间；Prep=本次 preparation_complete_ns−preparation_begin_ns；A=本次首算术 HTTP 正确响应完成−注册命令发出（另列 trigger 起点）；已有 phase_* 使用同一请求窗口中八 rank 最早 begin→最晚 end，保留原始事件和SHA，不相加成总耗时。每条只有一个新 run/attempt 身份，统一 manifest 绑定配置、缓存证据、原始 token、阶段文件及结果。任何缺失指标为 null/失败，禁止借用旧六块或其他尝试的字段拼成一行。

删除模型后至少观察 60 秒，再完整退出 Store、Ray、head/worker、监控及本实验辅助进程；记录 GPU、匿名内存、锁页、共享内存和后台 I/O 的归属证据。不得以全局 GPU 清空或 Docker 消失单独认定全部释放。额外释放观察计入 controller 总成本。不得将现有 debug controller_elapsed_s 直接当上述完整指标。

新批次每条从零初始化公共栈，旧 C23 为跨 trial 共用栈；因此是新的补充协议层，不能追填 C23 controller 栏。报告增列新批次及清晰边界；原栏仍保留缺测。

## 预算与停止

此前建议4小时是粗规划，不是已批准硬预算。当前候选每条采用单一绝对工作截止1800秒（包含源码检查、建池、缓存控制、加载、三请求、删除及实例观察，不能逐步骤重置），完整宿主候选预算3600秒。四条最多4小时，另留实际守护probe、资源核验及异常取证余量，整体按约4.5–5小时规划，而非保证4小时内完成。独立审阅仍需证明所有最坏退出路径（包括重复清理、监控join和宿主自身退出）有界；仅在结束后发现超时不是硬截止。现有实现尚未满足此项，不得开跑。

镜像恢复可能留下客户端超时但daemon仍工作的歧义，所以本补充入口只接受原镜像已经存在，create前再次检查，缺失则失败停止；原有按需恢复路径仍保留，但在补充计量之外执行并另记成本。不能靠缺失时自动load重置时钟。异常保留原始证据并停止下一条，无新增证据不重复同类失败。实际剩余窗口不足完整单条最大预算＋取证/守护余量时不启动。

## 剩余实现与验收

1. 已补现有 debug_stack.py 的宿主入口/退出单调端点，缺失或跨 boot 时 null，42项既有及新增 CPU 测试通过；没有 GPU 数据。
2. 在已有 debug_service.py / debug_stack.py 复用 C23 原语接入四条件完整缓存准入、统一池配置、三金标请求、删除后观察、资源归属监控、finally 释放与总截止。不得复制旧 formal gate 冒充新批准，不能只更改 purpose 标签。
3. 冻结当前代码 commit/完整源码与配置 manifest，完整独立 R1；核验实际窗口、资源/共享 I/O、宿主守护身份和截止/probe。未实现、未审阅或证据不足就停在 CPU 准备。
4. 获准后四条按顺序各运行一次，保留失败和未开始计数。新增数据单列，不重跑原40项，不宣称论文原样复现。运行及释放观察期间不 Git/同步。

当前状态：历史父进程/释放记录和26条结果已由 historical_costs.py --c23-controller-audit 核验，controller-history-audit.json 保留SHA与不可恢复依据；头进程受控停止 child_returncode=1 明确保留，不称正常 exit0。debug_service.py 已增加复用C23原语的缓存准入、同次三金标/Prep/A/phase 与删除后60秒观察候选。debug_stack.py 的 measure 子命令候选接入当前源码/完整source tree的R1哈希绑定、宿主连续采样、工作绝对截止和退出后至少60秒观察，但完整总截止、强制/正常退出判据、probe实际证据验证及释放归属仍须完整独立审阅；候选不能据此标ready，未开跑。默认up/status/down行为与C23原正式入口保持隔离。

## 首次完整R1与整改断点

独立只读R1对代码1ced918fc374068b3ae0f9c486f1f0ecaa86ee01及清单48c85e4a204b9b2115a3e2e241c93d36828ae78378d4bc842bc96c4c6684cd30给出BLOCKED，9项问题及核验证据保留在controller-supplement-r1-review.json；旧候选和旧清单在Git中保留，不能改其结论批准新代码。

后续修订已修正B1的跨平台路径键并补回归；针对B4复用listener、audit_store_threads及实际RPC/RSS/日志检查，拒绝TCP已通但配置或驻留不足，输出Store出生身份；B6已开始保存rm前最终容器State并识别exit137等非正常退出，首个角色清理异常不再跳过第二个角色。51项调试CPU测试及5项历史成本测试通过，仅证明这些CPU检查；B4真实池正向和完整R1复审尚未进行。B2宿主总硬截止、B3辅助GPU基线身份、B5实际probe来源、B6剩余子进程/嵌套finally、B7完整释放归属、B8实际运行依赖/资产绑定、B9完整资源与IO准入仍须闭合。不得部署或补测当前修订。

已额外按ledger逐条远端核验其余14项result SHA；与C23的26项合计覆盖原40项结果，来源和范围见../../results/reanalysis-20261106/controller-history-audit.md。不把历史记录不充分改写成零，也不宣称所有探索日志均已穷尽。

第二次CPU整改断点（仍非运行批准）：B3已直接接回gpu_identity13的实际Store namespace/PID/startticks/cgroup绑定及accept_control13.validate_native_idle；辅助基线来自本次真实池就绪、模型尚未注册的快照，不抄1769MiB。B6记录所有子进程退出与清理异常，Ray stop超时不跳过后续清理/失败回执。B7保留实际cgroup路径/inode，检查完整8卡显存及进程、cgroup消失或空且匿名/共享/unevictable归零、退出后归属IO不再增长。B9补八卡型号/容量/显存、CPU/NUMA/锁页/共享内存、双次本地IO静默和XFS准入。以上均是候选代码和CPU故障回归，不是现场验证。

B8已通过SSH只读复核57个小型运行源码/native/配置资产，共2007325字节。服务器源码副本没有.git，因此没有clone或补复制；改用冻结官方提交9f50241baa5386e06a9321c51f19a9ef5f964c2b的31个Git blob核对：3处已知补丁按原历史SHA匹配，其余28处仅CRLF部署差异。只做只读换行比较，服务器原字节不改，运行准入仍钉住实际raw SHA；gold/restore不做换行归一化。源依据、文件清单和旧native副本/overlay区别保留在../../evidence/cost-sources/controller-runtime-reference-verification.json；首次预检失败单列保留。真正Store进程在创建大池前复用原verify_store_imports/verify_event_trace并核实际Python/Torch/vLLM/CUDA及导入路径，不能称所有rank机器码证明。

78项相关CPU检查通过（57调试候选+5历史成本+6C23参数回归+10旧native导入校验），不代表完整measure链或GPU通过。**当前仍缺B2独立exact-slice总截止与实际CPU故障probe、B5可信root probe来源及对应完整复审；宿主独立unit身份检查只是部分接线，不能当守护已部署。** Controller最终端点还需绑定实际正常退出证据，不能将自身写result前的内层时间冒称完整进程退出时刻。原四条件补测全部未开始，GPU/Store池占用为零（本任务未启动，不代表最新全机快照）。


## 当前独立截止与最终端点修订（待完整R1）

实际阻塞是宿主controller及Docker子树的独立硬截止、可信故障probe和内层终点不覆盖进程退出。仅修改现有debug_stack.py、debug_service.py、install_debug_guard.sh、historical_costs.py及对应既有测试；不新增运行框架、镜像、环境或权重副本。

- 每条使用唯一run/holder，root管理的私有`<run>.slice`，controller与两个Docker容器必须实际处于该slice。新slice上限1700GiB、Swap0、Tasks8192。默认debug与C23模板不变；补充容器增加同一私有cgroup-parent。
- 独立root瞬态controller service：User/Group2147，RuntimeMaxSec3570、TimeoutStopSec30、KillModecontrol-group、SendSIGKILLyes、Restartno、RemainAfterExityes，不使用--collect。保留退出元数据供后置CPU复算。工作阶段仍只允许原点起1800秒，不因步骤重置。
- 另一条root timer位于私有slice外，probe核验后3240秒对精确私有slice执行systemctl kill --kill-who=all --signal=SIGKILL；本次Main启动时再次核对实际下一次monotonic触发时刻，使其落在Main原点后3000–3245秒。只杀本run，不操作其他资源。该强杀用于故障兜底；触发后本条失败，不能称正常退出。
- 既有安装脚本新增两个显式CPU probe模式；不调用其默认全局安装模式。先启动有TERM-ignore/setsid子进程的有界CPU服务、32MiB无网络/runc/noGPU小容器、同slice哨兵及另一slice对照；断开SSH后独立核验实际超时、精确slice清空、137非OOM及对照存活，再删除本次小容器和对照。root只读收据存/run/<run>，含PID出生、boot、源码SHA、unit、timer、实际触发时刻；controller拒绝用户可写、过期或失配收据。CPU probe不是GPU/模型验证。
- 退出路径只由measure清理一次；up失败不重复先清理再被外层清理。每角色保留rm前最终State；强杀、137、OOM或证据缺失均失败。Store进程有240秒grace，嵌套finally保留错误。3240秒slice兜底与3570+30秒宿主上限仍需实际CPU验证；不靠事后时间比较当守护。
- 内层controller-result.json仅组件结果，标记controller_metric_provisional；不是主Controller。Main退出后historical_costs.py --finalize-controller <run>独立读取systemd原点/退出、InvocationID、同boot/run/holder及同次worker/cache/三金标/phases的SHA。正常退出且两次60秒观察均通过才发布Controller；否则metrics=null并保留失败。不得将内层预退出时间或旧helper填入主栏。
- 独立守护安装/probe、源码部署、镜像恢复及闲置slice等公共准备成本另账，不因放在主Controller外消失。主Controller包含本次资源/代码检查、建池、缓存控制、部署/请求及两层释放观察。镜像必须已存在，缺失则停止，不在计时里发起异步load。

当前只完成CPU源码检查，不存在本修订实际root probe、四条件运行或批准。18:23(+08)只读快照八卡0MiB，主机MemAvailable2018325405696字节；快照不授予后续准入。下一步完整源码/预算R1，之后实际CPU守护probe及新鲜资源检查，通过才按固定顺序各一次；异常停止后续，不与C23六块拼接。
