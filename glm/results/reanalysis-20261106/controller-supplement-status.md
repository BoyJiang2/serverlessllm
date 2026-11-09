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
