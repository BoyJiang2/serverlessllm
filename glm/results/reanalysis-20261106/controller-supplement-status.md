# GLM Controller补测：首批停止，缺测不填成功值

原40项仍为35成功/5失败；C23 B5–B10六个完整块及全部金标不变。历史40项相关权威日志已核验，没有按条件独立完整Controller可恢复。新批独立登记四条件各一次，源码R1批准3f2e5697及清单6b5bff83…，实际root CPU守护probe通过后启动SF冷读。

当前新批：**0成功 / 1失败 / 3未开始**。run_id=`sllm-debug-20261109T105600Z-3d427ecc`。后3项未启动，不替换首失败，不与旧C23拼接。

首个算术HTTP响应匹配原gold；translation及fixed32未执行完成。随后严格phase汇总因`router_instance_published`缺失失败：head raw模板没有SLLM_EVENT_DIR，而正式派生给head/worker共享事件目录；本次9个实际事件文件router发布事件为0。保留断言，不把观测缺失解释为路由未发布或模型推理失败。

退出阶段还记录泛化monitor错误；因未保存原errno/filename，具体触发路径未知，不能直接定为Docker状态滞后。实际子进程返回RayCLI=1、Store=0、SLLM=15，不凭本次返回码放宽正常规则。

原程序自报释放60.236704707秒，原JSONL实际首末仅59.926809936秒，不能验收60秒。独立事后观察覆盖61.225920305秒，证明资源释放，不追认原尝试成功。八卡0MiB，无GPU进程；本run临时两个deadline timer及private slice已核为inactive，原失败unit/日志、磁盘checkpoint/Store资产均保留。原全局安全guard未修改。

## 同次失败组件观测（不是合格Controller数据）

|字段|秒|范围|
|---|---:|---|
|Prep|2.108004590|本次缓存准备，不含公用716GiB池创建|
|command到首算术正确HTTP响应|893.214198170|仅首题；新空JIT缓存，不能与旧C23成功均值拼接|
|trigger到首算术正确HTTP响应|883.011178862|HTTP非流式完成，不称最后token或客户端TTFT|
|失败controller进程历时|1323.152534000|kernel Main启动到exit1，计入失败成本；不是正常成功Controller|
|合格Controller|缺测|null，业务三题/完整正常退出未通过|

原生716GiB池实际RSS770023387136B，保留pool-ready收据；本次native-home编译缓存仅18059243B，留服务器，无GB级权重/镜像/环境副本。DeepGEMM首次预热包含在本次启动；未缩减预热或延长旧截止。

来源：现有evidence/cost-sources下controller-supplement-first-probe.json、first-failure.json、first-event-diagnosis.json、exit-runtime-sources.json；原始日志/事件/缓存向量/金标响应/父进程结果保留在服务器`/data/brick1/boyjiang/glm5.1-fp8/debug/sllm-debug-20261109T105600Z-3d427ecc`。controller-final.json明确success=false、metrics=null。代码/输入SHA见各收据及冻结R1清单。

下一步仅CPU最小整改和当前代码复审：共享head/worker事件目录并核实际Docker env；保留成员ENOENT/ESRCH显式消失、init/CG出生身份有界复核并留原始错误；按实际源码与主动信号时间线核受控退出；以首实际样本计60秒并让finalizer独立复核原JSONL跨度。不以资源已回收冒充服务正常完成。原队列不重置；新协议/后续批次须单列并独立审阅，不能沿用旧gate批准修改代码。


## 2026-11-09 21:25（+08）现场停止断点

原40终态35成功/5失败不变。三个独立补充cohort各为0成功/1失败/3未开始，均已停止后续；它们不是原ID重试，不与C23六块拼接。第1批首算术gold正确但事件缺失；第2批head实际Env准入拒绝，尚未创建worker；第3批监控采样校验失败，worker仅启动Ray CLI，Store/模型未启动。无可发布的新Controller成功值，原报告缺测格继续保留。

第3批原61秒级释放文件实际首末60.140834848秒；全部本run容器删除，八卡0/无GPU进程；独立exact timers和slice已停，原全局guard未改。随后一次CPU-only head25秒取证20样本0.263754516–0.283274189秒，无异常且容器删除，不能定位原慢采样或证明NVIDIA互斥根因。

当前取证源码beeea857已push核远端、上传90工具并逐项SHA核验（code/debug-stack-1f548274b575）。92项相关CPU检查通过，只读复审PASS_FOR_FORENSIC_SOURCE_CHANGE_ONLY，runtime_approval=false。新增原始拒绝snapshot/前时刻/错误/分段时钟取证，不放宽5秒、不改原生产入口、不改权重/金标。采集直接抛异常时snapshot=null、无法定位内部未完成子阶段，明确留作覆盖限制。

下一步不是第四批模型试跑：先冻结一次轻量GPU容器启动控制的完整命令/480秒级最大预算（最终以受审实际命令为准）/新身份/守护与释放判据，再完整审阅并现场准入；不启动Ray worker、Store池或模型。用新分段证据定位Docker、proc/cgroup、smaps或GPU查询的耗时。未复现则记录未复现，不连续盲试。不将源码取证修订通过当模型恢复通过。


## 2026-11-10 一次轻量GPU容器启动取证已完成

用户明确允许当前Azure预约bk-20261109T1403-6650c8d2（至10:00）中一次最多15分钟的无模型/无Store启动取证。新run为sllm-debug-20261109T163357Z-5a947f74，实际运行代码a9e17edb，source83/tree0201b7…；完整只读源码R1 PASS gate ad70d55d…，不沿用旧模型gate。新root故障probe通过，私有slice独立900秒timer实际目标在Main后866.90865秒、arm→Main余量33.094685953秒、Accuracy1秒；此前cfdf的905秒容差审阅阻断原样保留。

本次仅原CPU Ray head及GPU配置的sleep30 worker，未启动Ray worker应用、Store池、模型、业务请求或缓存控制。kernel Main正常exit0，总控制进程成本101.623486秒；不是完整模型Controller。77个监控样本最大0.492331029秒、最大间隔1.492782718秒。47个释放样本首末60.128680346秒、最大间隔1.563726192秒，worker自然exit0，head受控stop后exit1，均非OOM；本run容器已删除，临时timer/slices已独立核inactive，八卡0且无GPU进程。

主进程直接读取完整77/47原始JSONL校验SHA、时钟/原始跨度；后续CPU逐行补审复核八卡UUID/显存、5秒时序、53次init PID出生/cgroup身份及全部47行原释放判据。独立子代理只读核本地小摘要、冻结源及已执行verifier，结论PASS_STARTUP_COMPONENT_ONLY；它未直接读取远端原始JSONL，不能称两人均逐行复核。模型服务与完整GLM Controller仍未通过，报告缺测格不填。

原始证据目录：/data/brick1/boyjiang/glm5.1-fp8/debug/sllm-debug-20261109T163357Z-5a947f74。原monitor/release总约1.95MB留服务器，本地仅小型摘要/来源SHA和实际verifier源码，见glm/evidence/cost-sources/startup-monitor-control-result.json。控制源/池/缓存均未新增GB副本。独立guard故障probe、审阅、源码部署等公共初始化在主进程101.6秒之外，启动控制成本不能冒充包含这些步骤的总项目成本；root probe原始时刻和文件SHA已保留。

结论：此一次GPU容器创建/sleep控制未复现原慢采样，不证明根因已解决，也不能排除间歇性Docker/NVML或调度问题。原失败发生的worker应用/Python/Ray启动不在本次覆盖内，是下一待定位边界；任何更深入的现场控制须先有具体有界方案、独立审阅、资源授权与新守护，不能直接启第四模型cohort。当前没有持续占用GPU或大池。

原40仍35成功/5失败；三个补充cohort各0成功/1失败/3未开始，全部保留，不重试、不补入C23。下一阶段仍是证据驱动定位和必要修复，最终再进行受审四条件同次补测，goal未完成。


## 2026-11-10 Ray边界CPU准备与完整源码R1完成，现场未开始

source revision04fbc526（实现4904638c）复用原debug_service的Ray CLI、driver和双节点/8逻辑GPU核验，只在显式--startup-only --startup-stage ray后于Store前分支结束；普通Store/服务分支AST与a9e17edb一致。新scope glm-ray-startup-monitor-control-v1、预算180秒工作/900秒独立slice截止、原5秒监控及至少60秒释放不变。100项CPU检查通过（78+6+6+10），完整只读R1 PASS，manifest1cb69554…、source tree70a71e5d…、source-only approved gate35ed241f…；未获本次Ray阶段运行批准，不复用已完成sleep控制的资源授权。

真实CPU原生Ray退出组件（runc/UID2147/4CPU/8GiB/8192任务，0GPU、仅原三只读环境挂载，timeout130+kill5）约10.896936009秒通过，容器exit0、原生Ray受控exit1，无OOM/外层force/退出错误，已移除。执行函数AST与冻结源码相同；ray_control_pass=true而完整success=false，不是双节点GPU Ray阶段验证。证据ray-startup-cpu-control.json同时保留原始源码、日志及此前本地预检/夹具失败。另只读核当前Ray Node/Worker磁盘SHA仍与原信号来源一致，version源码为2.48.0/03491225d59a1ffde99c3628969ccf456be13efd；这是磁盘身份补证，不冒称本次活进程模块映射证明。

工具90文件已上传并SHA核验：code/debug-stack-51b31dd49b06；同commit源码83文件：code/source-debug-04fbc526546b-70a71e5dca77。无新环境、权重或GB级副本；当前本任务无GPU、模型、Store池或辅助CPU容器占用。

提案新run sllm-debug-20261109T170530Z-0cd21c3d尚未开始。已向用户提出具体的一次最多15分钟Ray阶段授权请求，当前至10:00的Azure窗口此前明确授权只覆盖sleep一次；本次未得到确认前不安装新运行守护或启动GPU控制。确认后仍须新root CPU故障probe、真实timer/Main/private slice及新鲜8卡/内存/共享I/O准入，失败保留且不自动重试。该控制仍不建Store池、不读权重或启动模型。

原40及三个失败cohort不改；未发布新的完整GLM Controller值，四条件补测和最终报告仍未完成。下一实际步骤是取得Ray阶段资源授权并执行受审有界控制，或在资源不允许时保留可接续证据，不能将CPU/R1通过改写为运行成功。


## 2026-11-10 Ray 启动控制通过

按用户单次授权，run sllm-debug-20261109T170530Z-0cd21c3d 在 qjhs003 完成双 Ray 节点启动、worker 8 个逻辑 GPU 注册及 30 秒观察。执行源码 04fbc526，R1 gate SHA 35ed241f8226ba26bb5397ce4771beedf2053facdeeb7517a7d576e5e5db4e87。实际独立 timer 目标为 Main 启动后 859.332922 秒。两容器退出符合受审判据，Ray CLI 主动终止后返回 1，宿主进程正常返回 0。宿主区间为 110.152246 秒。

82 个监控样本最长 0.491384466 秒，最大间隔 1.491998856 秒。46 个释放样本首末相距 60.915058211 秒。核验时八卡显存均为 0，无 GPU 计算进程，本 run 容器均已删除，临时 timer 和 slice 均已停止。原始日志和 JSONL 保留于服务器 /data/brick1/boyjiang/glm5.1-fp8/debug/sllm-debug-20261109T170530Z-0cd21c3d，摘要、复核源码和 SHA 见 glm/evidence/cost-sources/ray-startup-monitor-control-result.json。

历史慢采样在本次控制中未复现，原因仍未知。此控制的范围止于 Ray，四条件模型补测仍需完整模型 scope 的当前提交审阅及覆盖完整最大预算的有效窗口。本次控制的 110.15 秒只计入诊断成本。


## 第四批完整范围 R1 及整改

独立审阅对候选 df88e94a 清单 ecb515c4… 给出 BLOCKED。发布器此前可把两个仅满足长度的观察窗口和缺少八 rank 端点的结果判为成功。连续监控此前可能让下一健康样本覆盖加载期间记录的未归属 GPU 进程。冻结候选和旧测量回执保留。

发布器现在绑定删除确认、实例观察、容器移除、整栈释放观察及宿主退出的先后顺序，从同次端点复算 Prep、两种 A 和七阶段八 rank 跨度，并校验三请求顺序及缓存条件。监控在枚举到新 GPU PID 时用既有容器 cgroup 和 PID 出生身份复核。仍无法归属时保存拒绝样本并结束本轮采样。CPU 回归覆盖错位观察、缺指标、空端点及未归属样本后紧跟健康样本。第四批须重新绑定源码和清单复审，再按每条至少 3900 秒的剩余有效窗口逐条准入。

## 第四批已通过源码复审，待现场准入

完整范围复审已通过，运行代码为 `16f6c59a`，批准清单为 `glm/controls/debug_stack_v1/controller-supplement-r1-v4-approved.json`。服务器同名副本 SHA-256 为 `007c9a285cb4272acce1490fd5b6dc8cfc73de412cb3a0abdd41f08f58a8c1d6`。2026-11-10 02:40（UTC+8）核验首条的宿主 unit 尚不存在。第四批按清单顺序执行，每个条件各一次。

首条执行参数来自批准清单。确认模型和 716 GiB Store 池的授权范围后，启动前核验至少剩余 3900 秒，并检查八卡、主机及 NUMA 内存、锁页与共享内存、I/O、部署 SHA。使用以下路径和身份安装新探针，断开首次 SSH 后独立验证探针。

```text
host=qjhs003
run_id=sllm-debug-20261109T183328Z-71a4a759
holder=9746df9f20286e6092bca77a3c7a8e6c
condition=safetensors_disk_cold
tools=/data/brick1/boyjiang/glm5.1-fp8/code/debug-stack-5a91f175b86f/controls/debug_stack_v1
source_tree=/data/brick1/boyjiang/glm5.1-fp8/code/source-debug-16f6c59a2509-70a71e5dca77
review=/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/v2/controller-supplement-r1-v4-approved.json
probe=/run/sllm-debug-20261109T183328Z-71a4a759/guard-verification.json
unit=sllm-debug-20261109T183328Z-71a4a759-controller.service
slice=sllm-debug-20261109T183328Z-71a4a759.slice
probe_modes=--supplement-probe-start --supplement-probe-verify
measure_mode=measure
```

完整模型探针使用 3240 秒独立私有 slice 截止。宿主 controller unit 使用 3570 秒运行上限和 30 秒停止上限。释放完成且 unit 已退出后，在本地调用 `glm/tools/historical_costs.py --finalize-controller <run_id>`。以生成的 `controller-final.json` 中 `success=true` 作为继续下一条的必要条件。失败则保留该条证据与成本，后续未开始条目保持原状态。


## 2026-11-10 第四补充 cohort：两项已通过，后两项未开始

实际运行代码16f6c59a2509cdc966747e7084c176a906e254f9；完整批准gate SHA256 007c9a285cb4272acce1490fd5b6dc8cfc73de412cb3a0abdd41f08f58a8c1d6。两项均由独立后置finalizer验证kernel端点、同次业务金标、缓存、八rank阶段和两层至少60秒观察后发布；不是原C23样本。四条件保持716GiB公共Store池；各run独立native-home，实际编译/预热计入Controller，不能和历史共享编译缓存直接合并。

| 条件 | Prep/秒 | command→首个正确算术HTTP/秒 | Controller/秒 | 权重加载全rank包络/秒 |
|---|---:|---:|---:|---:|
| safetensors_disk_cold | 2.097072 | 843.981771 | 1274.926636 | 358.086166 |
| safetensors_disk_hot | 264.563567 | 625.085142 | 1301.317127 | 147.021788 |
| store_disk_cold | 未开始 | 未开始 | 未开始 | 未开始 |
| store_pool_hit | 未开始 | 未开始 | 未开始 | 未开始 |

Controller为实际宿主unit kernel启动到正常退出，覆盖完整退出及全资源释放观察；command A是第一条算术请求的正确HTTP响应，不是fixed32、客户端TTFT或纯generation。阶段包络可能嵌套，不相加。每条件当前n=1，仅描述观测，不发布推断或可靠性结论。root守护故障probe、源码部署和审阅等unit外初始化仍属于额外成本，表中Controller不包含这些unit外步骤。

SF冷读实例删除后观察60.095283099秒，全释放观察60.472522724秒；SF热读分别60.104987589秒和60.425782300秒。两项私有timer/slices已核inactive，所属容器已删除。SF热读最终回执kernel正常exit0；12:39:31起新出现weihaoni的八rank GPU进程，晚于本次正常退出，12:41:32复查仍占用八卡。后续Store启动暂缓，不能终止对方作业或以授权代替实际资源准入；未开始ID不消耗、不替换。新启动仍须完整3900秒余量、重新资源核验和每run独立守护。

本cohort当前2成功/0失败/2未开始；前三个cohort各0成功/1失败/3未开始不变；原40项35成功/5失败和C23六块不变。旧慢采样/exit137原因未知；这两次通过不能追认旧失败为成功或证明根因闭环。

小型来源回执（原始monitor/缓存/事件留qjhs003，路径及SHA见回执）：

- `glm/evidence/cost-sources/controller-supplement-v4-safetensors_disk_cold.json` SHA256 `6ddae26c627e40cc6c08d8fff4e97f73cf8f6d07a121e4bd5e20f199925c3c5a`。
- `glm/evidence/cost-sources/controller-supplement-v4-safetensors_disk_hot.json` SHA256 `fe8169d09a34c29f2e4255ce0676878b9c12621950c9af3c63b373fe77a3daf0`。


### 两项运行回执独立只读核验

独立只读子代理glm_controller_r1核对main 117ef3e0的两项本地小回执、冻结approved清单与实际finalizer，结论：未发现这两项限定发布的阻断；Controller由systemd单调kernel起止独立重算精确一致，finalizer源码SHA b96bb7c9…与批准清单匹配。两项unit正常exit0，worker exit0，head主动stop后exit1符合冻结规则。它没有亲读远端原始JSONL、请求响应或phase payload，因此Prep/A/phase/释放来源依赖主进程已执行finalizer的回执，不称第二人逐条独立复算，更不称完整四条件R2已通过。

后续Store冷读的只读启动准入尚未通过：先遇CPU/shared-IO gate（现场loadavg1 8.44，随后10.43，阈值严格<8；采样1秒nvme读sectors未变），稍后再遇GPU occupied。所有拒绝均发生在root probe/模型/大池启动之前；未创建本次Store run目录，未消耗其ID，无新模型失败可登记。等待成本与先前已成功的终态结果分开。不得将这些前置拒绝绕过或更改冻结阈值。


### 启动前资源拒绝的停止边界待用户裁定

独立只读复核发现冻结approved.full_path_required的“halt on first failed admission or attempt”未明确定义外层、尚未启动Main的资源预查例外。measure内部也先执行refuse_shared_state/supplement_resources后才mkdir/write-start，所以无run目录本身不足以证明不受停止条款约束。此前“等待后继续”尚未实际执行；现暂停后续启动并请求用户裁定，保留原条款及全部拒绝事实。两项not_started不改失败，不擅自默认继续；全部硬件、缓存、金标、守护和预算门槛保持原样。


### 用户裁定：外层只读占用预查可恢复等待

2026-11-10用户明确回复：“按可恢复的资源等待处理，空闲且原准入通过后继续”。该裁定只适用于未启动Main、未分配资源的外层只读占用预查；保留此前拒绝及冻结原条款，不修改运行失败停止条件，不重试终态ID，不放宽资源/缓存/金标/守护/预算门槛。原Store冷读和池命中两项可在完整原准入通过后按序继续。13:18:59现场负载3.58，但八卡仍各2045MiB且有GPU PID3523075，尚不符合八卡0/无compute进程要求，因此当前不启动。目标已由用户恢复active。


### Store冷读前置探针失败，正式attempt未启动

2026-11-10 13:39:49启动Store冷读的root CPU故障探针；宿主探针在约7.06秒后按故障结果结束，5秒slice deadline service也成功执行。但独立复核在13:42:17才运行，超过冻结的8–120秒验证窗口，批准verifier以RuntimeError拒绝，故不能声明root probe PASS。探针容器最终State没有在移除前保存，不能补写成已证实ExitCode137；此缺口保留。

全部CPU探针已退出；失败registration和回执保存在服务器既有v2目录，SHA分别bd4b78f7…和5f53fda…，本地小型来源摘要见glm/evidence/cost-sources/controller-supplement-v4-store-cold-probe-timeout.json。Controller Main、Store池、模型、权重读取均未启动，Store冷读ID不作为模型失败消费，仍保持not_started。外部sllm-v7 Store作业随后重新出现，八卡各约2387MiB、可用内存约773.5GiB，资源准入不通过。下一次即使资源释放，也必须先补做时限内独立探针；不得把本次超时验证当作通过。

资源等待追加核验：13:52外部Store容器及GPU进程出现；13:55该外部作业八个worker已各占约76–77GiB，loadavg1为25.60，主机可用约791.9GiB，另有约322MiB/卡的常驻Store进程。归入外部资源占用，不是本次attempt；未停止对方进程，Store冷读仍未启动。

失败探测资源清理：已移除仅属本run的CPU探针容器，停止其服务、子slice和空父slice；复查无populated cgroup路径。失败registration留在/run与既有v2持久副本，外部sllm-v7容器及GPU进程未动。

再次等待60秒后，外部八个worker已退出，但其sllm-v7 Store容器仍运行，每卡2387MiB且主机可用约775GiB；13:57确认仍是同一容器与PID。它不属于本attempt，资源等待仍未通过，未启动任何模型或Store池。

## 第四cohort在Store冷读失败后终止

新探针在时限内通过，Store 冷读 run sllm-debug-20261109T183328Z-b44dd6d7 实际启动。Store 日志记录开始创建 716 GiB 池（1432 × 512 MiB），未取得建池完成证据。监控拒绝回执记录 ESRCH（errno 3），阶段为 container init or cgroup read，filename 为 null。Docker 复查持续报告同一 PID 正在运行，具体失败操作和根因未知。宿主 unit 经过 172.041009 秒后 exit 1，内部计时为 172.026049133 秒。业务金标未执行，成功指标保留为空。

清理后本run容器全部移除，完整释放观察61.111785459秒，GPU八卡0且无计算进程；独立3240秒timer和空slice已另行停止。原始controller-final、monitor rejection、controller result、stack、release、Store/Ray日志及SHA见glm/evidence/cost-sources/controller-supplement-v4-store_disk_cold-failure.json。

第四cohort因此终态为2成功/1失败/1未开始，Store池命中未启动，失败ID不得重试。已证实行为缺口只允许在既有debug_stack.py修复：cgroup六个文件在ENOENT/ESRCH时最多2.5秒重试并记录，持续未知仍失败。新增回归加原78项debug、6项C23、6项final-cost、10项runtime-import共100项CPU检查全部通过。第五批候选已在 92979a42 冻结，四项均未开始，须完成该候选的独立 R1 及现场准入。

### 第五批候选独立 R1

只读审阅 glm_v5_frozen_review 对 92979a42 给出 BLOCKED。候选 SHA-256 为 19cd3e700b0f0113633960953a56a7ac7dde78932c07129eb42f1e9526af05fa。重建的 83 个源码文件（678252 字节）与冻结清单一致，8 个工具、8 个额外引用和 11 个证据引用的 Git blob SHA 均匹配。审阅方独立运行 100 项 CPU 检查通过，另用精确冻结函数复现了 2.49 秒故障、2.54 秒重试仍被接受的问题。

当前工作树整改将 2.5 秒重试预算按样本共享，并受原 5 秒样本和前次采样时钟约束。重试前和接受结果前均检查截止；保留失败读取路径、错误码、截止和事件时间，即使采样未返回也将重试记录写入拒绝回执。读取后复核进程 cgroup 路径和 inode，原 PID 出生检查保留。100 项 CPU 检查通过，其中监控回归覆盖 ENOENT、ESRCH、截止后成功拒绝、head/worker 共用截止及拒绝回执保留。冻结旧候选及原 CPU 回执仍保留；旧回执关于历史 ENOENT/短暂文件消失的解释由本页的原始证据复核更正。

剩余 R1 阻断：启动前须强制核验批准的 condition/run_id/holder、顺序和前一条独立成功回执，落实有效窗口及 3900 秒剩余预算校验。整改完成后须重新冻结完整清单并独立复审。第五批仍为 0 成功 / 0 失败 / 4 未开始。\x60n
### 第五批 R1 通过，现场准入待闭合

独立审阅对 9ff7c862 候选给出源码和设计 PASS。运行工具提交 e04de769，批准文件 SHA-256 为 25f755f4fc3e93a90860ba8fb65ed109fd97f3e16d9f74cfb3ded96cbd8b0cfd，批准记录已推送 b30d9c81。独立重建 83 个部署源码文件，核对 39 个 Git blob 引用并通过 102 项 CPU 检查。后续条件须将计时外独立复算的前序 final SHA 放入 root 窗口回执，Main 绑定前序 dispatch、内核退出与 start/result。第五批仍为 0 成功 / 0 失败 / 4 未开始。

首条是 safetensors_disk_cold，run sllm-debug-20261110T061909Z-cce53f64，holder a37467ebb11fcd623819f8607c749f68。由现有 deploy_debug_stack.py 清单重建 90 个文件（788344 字节），逐一核对 Git blob，批准工具哈希全部匹配。预期工具部署路径为 /data/brick1/boyjiang/glm5.1-fp8/code/debug-stack-2f7f1d35c6b4，批准文件目的地为 /data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/v2/controller-supplement-r1-v5-approved.json。现场只读检查确认两者尚未部署；原冻结 source-debug-16f6c59a2509-70a71e5dca77、精确 aa161e38 镜像及基础守护存在。首条 unit inactive，run 和窗口回执均不存在。

2026-11-10 19:48（UTC+8），qjhs003 八卡均约 96697 MiB，计算 PID 4112722–4112729，1 分钟负载 8.27。预约 API 此轮 TLS 读取失败，无法确认本实验可用截止。尚无独占资源、当前窗口或实际 per-run guard 证据。资源与授权通过后，按已有部署方式上传并独立核验新工具和批准文件，建立 root 窗口回执（首条 prior_final_sha256 为空对象），先 measure --preflight，再启动并在独立 SSH 中验证 CPU probe。Main 沿用已取证的 systemd 3570 秒运行上限、30 秒停止上限和私有 slice；在实际 dispatch 前保留至少 3900 秒有效窗口。按原顺序执行，计时及观察区间内停止同步。

2026-11-10 19:58（UTC+8），通过预约 API 核验 boyjiang 的整机预约 bk-20261110T1028-ec192ffd 为 19:00–21:00。65 分钟启动余量对应最晚 19:55，核验时仅余约 62 分钟，未进入任何 CPU probe 或 Main。19:54 和 19:55 的 NVML 查询均无 compute PID，但每卡 4 MiB，严格的 0 MiB 显存准入也未通过。保留第五批原顺序和 0/0/4 状态，等待足额授权窗口与实际资源重新核验。

2026-11-10 20:01（UTC+8），qjhs003 八卡均为 0 MiB、无计算进程，连续两次本地盘计数未变。预约 API 复核仍只到 21:00，剩余不足 3900 秒。已在现有授权窗口内仅同步 90 个受审工具文件（788344 字节）及批准文件。服务器逐文件 SHA 核验通过。第二条独立 SSH 使用部署后的原生校验函数重核冻结源码树、R1 工具和批准 SHA，基础守护活动且无身份异常。该回执仅证明部署与基础守护，尚未创建首条窗口回执、独立私有守护探针或 Main。第五批仍为 0/0/4。

2026-11-10 20:11（UTC+8），只读预约 API 确认 boyjiang 在 qjhs003 的新增整机窗口 bk-20261110T1210-c9868432 为 11 月 10 日 21:00 至 11 月 11 日 02:00，单窗 300 分钟。四条依次启动的最大规划预算为 260 分钟，每条启动前重新核算。20:15–20:17 按部署后的完整资源检查只读采样 5 次，首样通过，后 4 样因主机内存门拒绝。匿名内存首样约 2.96 GiB，末两样约 9.13 / 11.96 GiB，高于 8 GiB 上限；两个 NVMe 读取计数不变。八卡空闲不代表完整准入。未创建 run、窗口回执或独立探针，仍从原定 Safetensors 冷读接续。

20:20 两次只读采样定位到仍在消耗 CPU 的同 UID 进程 PID 39493，startticks 143680487，工作目录 /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7。该进程 RssAnon 约 8.06–9.41 GiB，同期主机 AnonPages 约 10.48–11.94 GiB。两者是独立观测值，进程 RSS 不作为全主机精确分摊。该 V4 进程不属于本 GLM 补测，未对其执行停止或变更。完整资源门继续拒绝，首条未启动。

2026-11-11 09:34（UTC+8），qjhs003 八卡均为 0 MiB，首条未登记 dispatch，独立探针与 Main 未启动。预约 API 只返回当前补充实验整机窗口 bk-20261110T1411-881646ab（02:00–10:00），剩余约 26 分钟，低于单轮 65 分钟和整批 260 分钟。新版 R1 批准文件 controller-supplement-r1-v5-admission-tests-approved.json 已部署至既有 results/reanalysis-20261106/v2，独立 SSH 使用原生验证函数核对冻结源码及实际运行工具，批准 SHA-256 为 2f01657b4de948e7a3ab5632472a98a5b0883fcf4b27d05dfcef05b5b7e8e078。旧 root 窗口回执仍保留作历史证据，下一有效窗口需要绑定新版批准 SHA。


### 第五批首条实际失败并完成释放

2026-11-11 11:04（UTC+8）Safetensors 冷读 sllm-debug-20261110T061909Z-cce53f64 经窗口、资源预查及独立探针后启动 Main。监控 cadence 实测 5.094687 秒，超过 5 秒门槛。被拒样本本身耗时 4.093970 秒，worker inspect 阶段占 3.812296 秒；该阶段内的根因未知，回执无 cgroup 重试。

宿主 systemd 记录 Controller 以状态 1 退出，失败经过时间 84.438015 秒。整栈释放观察 61.226168 秒，八卡归零、容器移除、私有 slice 与两只截止 timer 均已停止。独立 finalizer success=false、metrics=null。第五批终态为 0 成功 / 1 失败 / 3 未开始，停止后续。原始文件路径和 SHA 见第五批账本。该条不能填入 GLM Controller 成功数据列。


Docker events for the exact fifth-cohort worker show create at Unix 1794366276.285020825 and start at 1794366280.265654944. Using a later same-boot wall/monotonic conversion, worker inspect approximately spanned 1794366276.457586–1794366280.269882. The intervals overlap; this does not identify internal daemon locking or host scheduling as the cause. The rejected completion gap included approximately one second of fixed wait after the previous sample. Tool commit 38cd008f targets one second from sample start and retains the original 5-second gates. The cadence replay and all 102 CPU checks passed. A fresh sleep-only control and sixth model cohort are frozen separately. Both require independent R1, and the model cohort also requires a successful actual control.


## 第六批终态

三项通过独立 finalizer。Controller 从宿主 unit 启动计至正常退出，包含整栈释放观察。A 从启动指令计至第一条算术请求的正确 HTTP 响应。每个成功条件 n=1。

| 条件 | Controller（秒） | Prep（秒） | A（秒） | 八 rank 权重加载总跨度（秒） |
|---|---:|---:|---:|---:|
| Safetensors 冷读 | 1382.565 | 3.178 | 884.108 | 390.759 |
| Safetensors 热读 | 1296.046 | 263.556 | 615.469 | 139.082 |
| Store 冷读 | 1010.820 | 2.114 | 600.462 | 122.996 |
| Store 池命中 | 缺测（启动失败） | — | — | — |

Store 池命中 Main 于 14:42 左右启动。宿主监控拒绝 6.995553 秒样本，其中 worker inspect 为 6.706706 秒。精确容器的 Docker create 和 start 事件相隔约 6.930 秒。同一开机周期换算显示 inspect 几乎与该过渡重叠，调用内部原因未知。finalizer success=false，metrics=null。失败经历 90.520117 秒，整栈释放观察 60.023621 秒，八卡归零，所属容器移除、私有 slice 和两个 timer 停止。

第六批为 3 成功、1 失败。全部原始文件路径及 SHA 在第六批账本。后续分析应验证在保留容器身份和 5 秒门槛的前提下，采样是否可以避开 worker 启动过程中同步阻塞的 inspect 调用。

## 2026-11-13 独立轻量控制通过

qjhs003 的 sleep-only 控制 sllm-debug-20261113T074916Z-d4063bc2 使用当日有效预约及独立 R1。实际 root CPU 探针通过。118 个宿主监控样本中，第 17–22 行（零基）观察到启动中同一 worker 的已绑定 cgroup 及活进程，第 23 行取得同容器的 Docker running 观测。单样本最长 0.749673107 秒，相邻完成间隔最大 1.451767225 秒。Controller unit 正常退出，释放观察 60.234623152 秒，八卡 0 MiB，无所属容器，两个私有 timer 和 slice 均已停止。控制未执行模型业务或 Store 加载，未产生模型 Controller 指标。

原始目录 /data/brick1/boyjiang/glm5.1-fp8/debug/sllm-debug-20261113T074916Z-d4063bc2。SHA-256 如下。

- controller-result.json 7160a19ee77f4f92c8d0332d3f75836ff064ec630c9f71cc38ada43d02d4e636
- controller-monitor.jsonl 3787810f12f90e3a23346d4951d32a34abab808d44b3b2a74f901cd3fbcc82d4
- release60.jsonl 9be0fc433fe7f046bea9c16cf17e539131a30ed4a799a84ac8ba7edbc9e8876a

## 2026-11-13 第七批匹配轻量控制

运行 sllm-debug-20261113T083449Z-7f3bc467 经 R1 和独立守护探针后正常退出，102 个监控样本最长 1.17276746 秒，最大完成间隔 1.875024328 秒。释放观察 61.299190534 秒，八卡归零，无所属容器。采样未捕获带活进程的启动中 worker cgroup，故现场证据门未通过，不启动第七批模型。

原始文件位于 /data/brick1/boyjiang/glm5.1-fp8/debug/sllm-debug-20261113T083449Z-7f3bc467。

- controller-result.json 966869b5cc4a49ca3460bbb8bbc0a7ebecb04f133a1aefb3f6c71678696f08f9
- controller-monitor.jsonl 470745e60053cf2f0f2c65be30de8f16b66cd900f7c24bba9f66019e0471dae0
- release60.jsonl d18e082675d5f002d21c3e15ffc89c23a3b598ea32634bda041e2fb4d0124bc1
Docker events for worker 56551e4381a8a5a043c1c3f8696add658a670ff53f9016dbcad0bf74ea6172e1 (Docker wall-clock ns) record create 1794559047882625072, start 1794559048491928269, die 1794559078890252827, destroy 1794559079078353148. Create to start was 0.609303197 s. The first sampled worker state (zero-based monitor row 6) was already running; row 5 had no worker state. These clocks are reported separately. Docker events establish the transition occurred, but do not supply the missing live cgroup sample. A repeat should change the observation method under review instead of repeating the same sampling setup.

## Fast sleep control

Run sllm-debug-20261113T084332Z-29d1eb16 captured populated startup cgroups at zero-based samples 18 and 19 and Docker running for the same worker at sample 20. The controller nevertheless exited 1 with "supplement worker disappeared". Cleanup could not verify normal container exit. Whole-stack release observation lasted 60.309787564 seconds, all GPUs reported 0 MiB, and no owned container remained. Qualification remains pending. Root cause of disappearance is unverified.

- controller-result.json f4dbc643f1f840e2258aa72141657fdfd7cdfc251c00650d2e5bc58be31536ce
- controller-monitor.jsonl 010c606509bea81a4322fad459c90cb7e254fcb22c7ef5e257c910fbcb7ce5db
- release60.jsonl dfc82dfdf294296ff348103ec4c594ac7877eb503bb47abfcf216bc13eb6da52

Docker events for the fast-control worker 659a8aada2aebb4af3384c3f9878748a9f119f5475417594a40b61ab26a5625f record start at 1794559622705696598, die (exit 0) at 1794559652939632104 and destroy at 1794559652944766077 (wall-clock ns). Destroy followed die by 5.134 ms. The Docker event confirms process exit 0; the actor and cause of destruction remain unverified and the controller failure stands. Both owned timers and the private slice were stopped after the empty cgroup and absent owned containers were verified.

The fast worker die event records execDuration=30, exitCode=0 and the exact image, container name, run, holder, UID and purpose labels. Its declared deadline was Unix 1794560395, after the destroy event. This rules out expiration by the declared deadline at that instant, but does not identify the caller. The launcher does not pass --rm. HostConfig.AutoRemove has not been recovered for this container.

For a new sleep-only candidate, register an exact full-container-ID Docker Engine wait with condition=next-exit after verified creation and before start, with registration confirmed by HTTP response headers. A default docker wait can return on a created container. Capture bounded lifecycle events scoped to that ID and verify the label and image binding. If inspect loses the container, require matching wait status 0, a unique ordered start and die with exit 0, and no kill, stop, restart or OOM before die. Missing or contradictory evidence fails. Keep the original populated-cgroup transition and 60-second release checks. The model and Ray exit rules retain their existing inspect requirement. Review and test this candidate before any new control.

## 2026-11-13 匹配版退出控制现场通过

sllm-debug-20261113T090241Z-9e351ee4 使用批准提交 990b299b 的工具和冻结源码。实际独立守护探针通过。324 条样本中，第 19–20 条（零基）观测到启动中的同一 worker cgroup 带活进程，第 21 条 Docker 已确认 running。最长采样 0.878322832 秒，最大完成间隔 0.879230347 秒。预注册 next-exit 返回 0，正常 inspect 退出证据可用，未触发缺失容器事件回退。Controller 退出 0，释放观察 61.244267811 秒，八卡 0 MiB，无所属容器，两个 timer 与私有 slice 已停止。

原始目录 /data/brick1/boyjiang/glm5.1-fp8/debug/sllm-debug-20261113T090241Z-9e351ee4。

- controller-result.json 0ba5b06a3791aab7066c70806f6296592b64152f77411e233d86462a792468f9
- controller-monitor.jsonl 63efa3527e70bf143a3fa8eb976d957629f147331fe9c0fae18aa17471ff8ec8
- release60.jsonl 549bdb8fa7557bd4d8fc6b54777e110169d3582e855be21877fef17475f461fb
- sleep-exit-registration.json 5199b048fdd268beb7f9c1e78c9812ea4d4d3c9e18300accf98cb0cf5d129869
- sleep-exit-wait.json 3ff40d803ec9ed4885174d64e68e998cf110bd1590487f5f692939428b0cd2b6
