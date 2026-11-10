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

新复跑探针在时限内通过，随后Store冷读run sllm-debug-20261109T183328Z-b44dd6d7实际启动。716GiB公共Store池开始创建（1432×512MiB）后，worker仍被Docker报告为running且PID/startticks未变，但监控在一次样本读取其cgroup时得到ENOENT；约2秒重查仍未消失，正确拒绝将缺失当作零内存并终止。Controller 172.026049133秒后exit1，业务金标未执行，无Controller/Prep/A可发布。

清理后本run容器全部移除，完整释放观察61.111785459秒，GPU八卡0且无计算进程；独立3240秒timer和空slice已另行停止。原始controller-final、monitor rejection、controller result、stack、release、Store/Ray日志及SHA见glm/evidence/cost-sources/controller-supplement-v4-store_disk_cold-failure.json。

第四cohort因此终态为2成功/1失败/1未开始，Store池命中未启动，失败ID不得重试。已证实行为缺口只允许在既有debug_stack.py修复：cgroup六个文件在ENOENT/ESRCH时最多2.5秒重试并记录，持续未知仍失败。新增回归加原78项debug、6项C23、6项final-cost、10项runtime-import共100项CPU检查全部通过。新commit、完整manifest和R1尚未完成，故代码未部署、未重跑；后续必须创建新的cohort身份。
