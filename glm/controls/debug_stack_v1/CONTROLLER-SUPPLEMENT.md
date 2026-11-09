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


## 785a完整R1与后续最小修订

完整只读R1对785a3ee4及清单6de3e6f133c91192772d99eff3e1d32c38d769aa8062699366675db265a2adde给出BLOCKED；独立结论转录于controller-supplement-r1-785a-review.json。除现场准入外还剩两项源码阻断：正常退出采样PID/cgroup消失竞态，及Docker子进程OOM未纳入成功判定。该提交、远端90文件副本及旧冻结清单不变。

下一修订仅在已有实现/测试中修复：ENOENT/ESRCH发生后重新inspect同一CID，只有确认退出/移除才保留明确missing/race并继续；身份复用/仍活且不明/权限错误仍失败。私有slice在Main存活期间持续保留层级memory.events，oom/oom_kill非零或缺失均失败；子Docker cgroup消失后仍能保留计数，不把OOMKilled=false当无OOM。实际准入核cgroup2且未启用memory_localevents；qjhs003只读挂载核验为cgroup2 rw,nosuid,nodev,noexec,relatime，未修改挂载。监控和释放收据保存原始OOM字段。去除up中重复同值cgroup-parent，复用原docker_args既有参数。

CPU故障回归在785a冻结源码上重现失败、在修订上通过；仅证明故障注入判据，未制造真实OOM、未运行模型。完整新R1及实际root CPU probe仍待完成，四条件补测仍0启动。


4f26完整复审关闭上述两项，但新增确认R1-4f26-01：首个监控样本失败仍可能先进入up，且worker在大池建立后才等host snapshot。独立完整审阅转录保留在controller-supplement-r1-4f26-review.json，仍为BLOCKED，原候选不改。

后续必要修订只加两个既有路径的握手：Main在up之前有界等待本run/boot首个真实健康host收据，错误/超时/失配不调用up；worker在launch Store之前复用fresh_host_snapshot的建池前分支，要求本run/boot/holder、stack精确workerCID及自身Docker hostname、新鲜健康host收据，不要求尚未存在的Store helper；建池后原helper绑定不变。等待计入原1800秒绝对工作截止，不增加或重置预算。CPU故障测试执行实际Main工作片段和实际建池片段，确认拒绝时up/Store launch未调用；另覆盖健康、错误CID及不健康收据，仍不当作真实监控或模型通过。当前86项相关CPU检查通过，待新commit/manifest完整R1及现场probe。


## 首次实际补测失败与当前必要修订（不追认旧数据）

3f2e5697源码R1及实际root CPU guard probe通过后，独立SF冷读run sllm-debug-20261109T105600Z-3d427ecc已运行。716GiB池实际RSS770023387136B，完整cold cache准入通过，官方FP8/TP8首算术gold正确；但phase汇总随后因router_instance_published=0失败，translation/fixed32未完成。首批终态0成功/1失败/3未开始，后3未启动；原40仍35/5。失败进程成本1323.152534秒单列，Controller成功值null。

缺失事件的源码/配置依据已闭合：正式materialize_control13对head/worker派生共享/r/events-<label>，补充plan此前只用raw head模板（无SLLM_EVENT_DIR），而实际router/controller actor在head。新修订只在补充plan给head/worker共享worker既有目录，并核实际Docker env，保留严格router唯一事件断言；不更改原生产源码/模型，也不跳过校验。

退出监控旧泛化错误未保留errno/filename，具体触发路径未知；不能直接认定Docker状态滞后已证实。修订将成员ENOENT/ESRCH显式记录为该成员消失；init/CG错误只在原5秒sample预算内最多2秒同CID复核，继续检查PID/startticks/CG inode。确认Exited/Absent才接受missing，活身份不明/权限/复用仍失败，保存原errno/filename/阶段与每次State。缺失不填零。

原程序自报60.236704707秒，原release文件首末仅59.926809936秒，原失败不改。事后独立48样本61.225920305秒仅证明清理完成；GPU均0、无进程/容器，两临时deadline timer与private slice已核inactive，原全局安全guard未动。修订运行循环按首实际row.monotonic_ns起计；finalizer独立读取并SHA登记idle/release JSONL，复算首末>=60、相邻最大5秒及自报一致。不能用自报时长覆盖原始文件缺口。

受控退出采用同一次Popen出生/主动信号时间线与最终日志：SLLM仅一次SIGINT，先有界等待HTTP完整shutdown+匹配PID+返回0；这不宣称调用了controller.shutdown。随后Store SIGINT，其240秒grace从实际请求时点固定；Ray CLI SIGTERM后先等自身退出，再调用非force ray stop，成功还要求“没有活跃Ray进程”原生输出，避免仅以未带--force推断没有内部强杀。Ray CLI的受控exit1仅在主动SIGTERM、信号前活、出生一致、退出在后、完整同次CLI日志无unexpected-deceased证据时成立；不普遍允许1或SLLM正15。其源码依据为实际Ray node.py/worker.py及精确原镜像Uvicorn server.py的SHA。超时/强杀/OOM/身份不明仍失败。

退出工作软截止从stop入口固定290秒，各wait/subprocess取剩余预算；Store240秒不在Ray等待之后重置。worker进入finally时另装默认SIGALRM的300秒硬终止，超时不能发布正常成功；给日志/结果写入留10秒。原默认debug路径和C23生产入口不改变，严格补充路径才采用该规则。

实际无GPU退出控制保留4次尝试：2GiB容器报OOM；两次loopback/512任务配置未能使驱动加入；匹配既有head4CPU/8GiB、私有bridge/实际IP、8192任务上限的控制通过，HTTP0、Ray受控1、无force/无残留/无OOM且容器已删除。成功前快照661任务、memory.current3405623296B（含文件缓存，不是RSS峰值）。多变量配置纠正不当作网络或历史exit137的根因证明。CPU控制不加载模型/Store池，record.success仍false，单列component_pass，不能算GLM服务PASS。原始控制失败和成本保留在controller-supplement-cpu-signal-probe.json。

当前90项相关CPU检查通过；实际模型补测仍须新commit/清单完整R1、新身份/独立guard probe与新鲜资源准入。首批不重置/替换；如新修订继续测量，另列独立cohort及新ID、代码/协议修订，不把旧失败或C23六块合并。


第二批首条39ef784c在worker/Store/模型启动前被实际Docker Env检查拒绝。784195源码及R1漏查up的head_plan调用未传condition/start_sha；plan函数自身正确不代表实际调用接齐。原批终态0/1/3不变，原失败不重试。已有up调用片段CPU回归修复前四条件均失败、修复后通过，默认debug事件目录原值保持。仅在原调用补传与worker相同的格式/query/condition/start_sha，不改生产代码或新增包装层。独立事后观察49样本60.467971145秒、八卡0/无GPU进程/无本run容器，临时timer/slice已核停止；仍非成功Controller。完整独立R1须重新绑定当前commit。Ray无外层force证据不代表其内部所有子进程信号已逐个审计。


## 第三批停止与最小取证修订

fb854930完整R1通过并实际新guard/probe/资源准入后，第三批首条0270da51因监控slow or inconsistent sample停止；0成功/1失败/3未开始，停止后续。四个保留样本分别0.2583/0.3614/0.2825/0.2840秒；异常样本在校验后写盘的旧顺序下丢失，原始三时刻不足，具体耗时操作未知。worker仅开始Ray CLI（空日志，后受控TERM），Store和模型未启动。主unit失败81.618091秒，原释放观察60.140834848秒；两容器移除、八卡0/无GPU进程、exact timers/slices均停止。Controller成功值继续null，不能填写报告缺测格。

独立CPU-only head启动采样25秒得到20样本约0.26–0.28秒，无异常并已删除容器；它不覆盖GPU worker/NVIDIA初始化，不能认定NVML是根因，也不证明原故障已修复。取证修订仅在现有supplement_sample记录Docker inspect、init/cgroup、成员/proc、smaps_rollup、两次GPU查询及剩余读取的分段monotonic时间；monitor在失败时保留原snapshot、上一时刻、错误和collect/validate阶段，失败不发布健康ready。原5秒判据不改。注入6秒样本和采集异常的实际monitor函数CPU回归通过，当前92项相关检查；该修订是可定位下一控制的取证准备，不是慢采样修复或运行批准。

下一现场控制只允许一次有界GPU容器启动取证（不是模型重试）：复用既有head及worker派生/挂载，worker轻量sleep入口，不启动Ray worker/Store/模型；将涉及NVIDIA运行时，须先冻结完整实际命令/身份/最长预算及独立守护、完整只读R1和现场准入，取得分段/失败证据后再判断下一步，未复现不能无限重复。当前不启动第四补测cohort或再占大池。


## 一次轻量GPU容器启动控制（待本修订完整R1与现场准入）

实际缺口是第三批返回后被5秒校验拒绝的样本未落盘；NVML只是候选，不能将CPU head未复现当修复。因此只在既有measure增加--startup-only，沿用原身份、资源检查、源文件SHA、宿主监控、私有slice与两角色清理，不新增框架/环境/权重副本。

冻结执行形态：root systemd controller启动当前冻结debug_stack.py measure --startup-only --condition safetensors_disk_cold --source-tree <同commit的已核验source-debug目录> --review <scope=glm-startup-monitor-control-v1的本修订PASS清单> --guard-probe /run/<新run>/guard-verification.json --controller-unit <新run>-controller.service。该condition只选原SF模板，完全不做缓存控制，不是SF冷读样本。新run唯一身份由清单记录，不使用任何旧终态ID。

head仍是既有CPU Ray head；worker沿用原GPU/NVIDIA容器配置、挂载、环境和身份，但argv确定改为现有lifetime_wrapper(300, worker)+[/bin/sleep,30]。不会执行debug_service.py或任何Ray worker/Store/模型入口；配置收据pool_bytes=0。保留模型只读挂载不等于读取权重。成功只表示该次容器启动与监控控制通过，不能发布Controller/Prep/A模型测量，不运行历史finalizer。

预算：工作原点仍由root unit内核ExecMainStartTimestampMonotonic冻结；绝对工作180秒，容器300秒timeout；清理沿用worker320秒+head70秒等待与现有Docker命令期限，释放观察原始首末至少60秒。总硬截止900秒由私有slice外独立root timer绑定精确run执行SIGKILL，覆盖controller和Docker兄弟cgroup；超时一律控制失败，不能保证超时后仍留有完整观察。原3570+30秒unit只作额外兜底，不能代替900秒slice timer。180+320+70+60=630秒，其余270秒为Docker/取证预算；若串行命令未在该硬上限内完成则失败，不续期。

守护先执行现有--supplement-probe-start，新SSH核验执行新增显式--startup-probe-verify；模式仅将新run独立timer改为900秒，旧默认3240秒不变，不触碰全局guard安装。现场校验root只读proof的delay=900、run/holder/boot/代码SHA、真实timer下一monotonic时刻落在Main原点+660..900秒、exact slice/出生身份及实际CPU故障probe，不能复用旧probe或旧R1。660下界与proof最多120秒旧化及安装/启动余量配合，上界确保不超过总预算；不能据此将正常模型补测改用900秒。

控制前必须完整源码/manifest独立只读R1，且新鲜8卡/NUMA/匿名/锁页/共享内存/本地I/O与授权准入通过。运行中原5秒门不变，异常原snapshot及分段时刻保留，立即失败并进入清理；仅一次，不因为未复现重复分配。完成或失败均保存unit退出、容器State、OOM/cgroup及至少60秒释放证据，硬截止/失败观察不足明确保留。执行与观察期间不Git/同步。

CPU验证：73调试测试+6成本+6C23参数+10导入=95。新增实际head/worker调用传播、仅sleep argv与默认路径对照、scope/180/900审阅隔离及root900拒绝旧3240校验；最初测试fixture漏state导致5个NameError已保留为整改过程，补fixture后通过，不改实际up逻辑以迎合fixture。此处不是GPU运行批准。


完整startup R1对cfdf0650及清单e36063d2给出BLOCKED，仅STARTUP-R1-01：905秒准入上界不能证明900秒预算。新修订只收紧startup上界900并补901秒拒绝，旧3245不改。systemd AccuracySec=1及调度延迟不是物理无延迟保证，现场需记录实际NextElapse、arm→Main余量及最终触发/退出；不能将容差作为额外授权时间。旧候选与BLOCKED结论保留。
