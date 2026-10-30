# SIGKILL/137 历史现场联合调查

investigation_id: sigkill-ro-20261025T163752Z-221fa2。只读 qjhs003 上本实验既有文件，远端读取已结束；未启动模型/Store/探针、未用 sudo、未修改主代码或 Git。新增源数据读取少于 27 MiB（限额 2 GiB）。本报告不批准 R1/R2 或正式计时。

## 结论

历史 formal6 CID `8ebd672e388d52410269e3c0de99a040274ecd0fa5268a9b26f86481eaf9ac24` 于 2026-10-25T04:58:20.052203220Z 启动、05:03:16.289423094Z 结束，296.237219874 s，Docker exit137/OOMKilled=false。事件发生在 Store 716 GiB pinned pool 分配尚无完成标记时；模型权重加载、推理与正式 trial 尚未到达。退出码与 SIGKILL 相容，缺直接信号/wait-status/API 调用证据；根因保持 `unresolved / undetermined`。

本轮补齐了先前未读取的真实 identity、Store 日志、170 条正确窗口监控及 Ray 内部日志，收窄阶段并发现监控非原子间隙；没有找到杀死目标的发送者。不能点名平台、人员、Docker API 或 Ray 为原因。

## 已证实事实与绑定

|事实|原始/派生证据|边界|
|---|---|---|
|CID→host bootstrap PID1481458→docker scope→boot0145ca12-ac6e-40fc-9760-5d0639290553；监控 NSpid[1481458,1]、start_ticks2861870|focused-small-evidence.json 的 formal6-identity/dispatch；monitor-boundary-rows.json|identity 本身无 start_ticks，由正确树的监控补充；不能用同名 PID 替代绑定|
|Store PID1482888、NSpid1124、PPid1481458、start_ticks2862253|monitor-boundary-rows.json；store.log 的 glog1124|是 bootstrap 直接子进程，未发现 PID 重用|
|768799145984B=716GiB=1432×512MiB，已开始 pool 创建，目标日志结束时无完成/启动gRPC标记|service-bootstrap-formal6__store.log|日志缺标记与阶段证据一致，不独立证明库崩溃|
|确有 `WARNING: Logging before InitGoogleLogging() is written to STDERR` 原始行|同 store.log|反证“默认 INFO 一定不进stderr所以完成标记不可见”；须结合实际初始化时序，不能一概推断|
|Ray worker 04:58:24.560 ready，快照 head与worker alive，worker GPU8|service-bootstrap-formal6__ray-worker.log / ray-nodes.json|不是05:03:16持续健康证明|
|170个唯一监控时间点，两个源前12651851bytes SHA相同|monitor-window-summary.json / monitor-focused.json|不是340个独立样本|
|正确run-root的 memory.events 和local在170行中全0；最后正常行05:03:13 host MemAvailable1181.098GiB，root current676.110GiB，SwapTotal0|monitor-boundary-rows.json / monitor-binding.json|不是历史leaf/所有祖先计数；全局/父层OOM不得仅凭OOMKilled=false排除|
|跨退出点的最大监控间隔33.798147357s|monitor-window-summary.json / resource_monitor_v11.py|代码先读current和逐PID的status/stat/smaps，再打时间戳、读events/host/NVML，行内是非原子采样|
|05:03:46.994799混合行仍带旧PID/start状态，但host已释放，下一行目标树空|monitor-boundary-rows.json|不能声称目标在46秒仍活；不能按内存增量精确反推早读字段在16.2秒；具体慢read未计时，smaps仅候选|
|cleanup-v5-main在20:00+08才开始，打印先前exit137状态|cleanup-v5-main.log|晚近7小时，不能倒推为13:03信号发送者|

OOM判断依据：Linux v5.15 文档定义 memory.events 默认对子树聚合，oom_kill记录任意OOM killer杀死成员；local只当前层。当前同boot 5.15.0-91-generic cgroup2挂载未带memory_localevents，使正确run-root全零成为常规OOM解释的重要反证。历史mount连续性、leaf/其他祖先以及瞬时kernel事件未独立覆盖，仍保留缺口。参见 [Linux v5.15官方文档](https://www.kernel.org/doc/html/v5.15/admin-guide/cgroup-v2.html) 与 [当前文档对memory_localevents的定义](https://docs.kernel.org/admin-guide/cgroup-v2.html)。不把“未覆盖”写成“不存在”。

## Ray 聚焦补取

17个本实验自身日志，569378B，全文件散列和源行号在 ray-focused.json；精简原始事件在 ray-critical-lines.json，哈希简表 ray-source-hashes.json。ray-formal6不存在，实际挂载 session_2026-10-25_04-57-59_397139_1 位于 ray-hformal6；未读取其他session内容。

- worker raylet.1.out:33 于04:58:24.472初始化MemoryMonitor，阈值1734093045760B，total1825361100800B。head raylet.out:33 的8GiB是另一个容器，不能套给目标Store。
- driver日志:61 于04:58:24.649为 INTENDED_USER_EXIT / Shutdown by ray.shutdown()；head raylet:206–209记录graceful disconnect。它在Store池分配前，不能冒充05:03的异常driver死亡。
- GCS:1131/1133于05:03:29.519记目标worker node8bfa… missed heartbeats/dead，晚于Docker退出13.23s。此为失联观测，缺少先于退出的发信号因果链。
- 所选时间/关键词窗口未找到实际memory-pressure worker-kill事件；state-dump的MemoryMonitor.CheckIsMemoryUsageAboveThreshold / kill_idle_workers函数计数不是发生杀死的证明。日志覆盖/关键词筛选并不等于绝对排除用户态策略，特别worker raylet日志止于05:02:24附近。

## 可排除/条件反证

1. 当前冻结bootstrap源码在validate_full_model=1时走min(7200,budget>3600)，Storewait1800；不能用非模型300秒分支解释296.237秒。正式trial尚未执行。
2. 更早e60c… boot-id exit1与后续4b34… Ray exit1是另两次事件，不串因。
3. 模型推理/FP8加载阶段不是本次已观察到的失败起点。
4. 冻结launcher通过cgroup escape检查后才写identity；存在正确scope的identity对“启动即escape kill”分支构成条件反证，前提是对应执行字节和收据可信。manifest/源码SHA匹配不足以证明所有后续外部操作不存在。
5. StopTimeout=null来自缺字段的脱敏表示，不支持构造默认10秒stop时间线。Docker历史事件环仅256条，空回放不能排除旧事件。

## 仍缺的因果链与最小下一步

缺杀死目标的直接signal/wait-status、Docker/systemd/API调用方、目标leaf/祖先历史OOM、逐读时间戳和kernel瞬时记录。本轮不再扩张扫描，不为了把unknown改成确定而重跑完整模型。

主流程可修正监控：从fast heartbeat移除同步大进程smaps_rollup，独立有界采PSS并记read begin/end；这是已发现观测缺口的修正，不是旧137根因已解。

若后续主agent另行审阅认为确需调查新事件，只提出有界、target TGID/cgroup过滤的signal_generate与Docker事件、leaf/祖先events采集，采集器独立deadline，避免全局audit或无限复现。signal_generate中的runtime/shim执行上下文仍不等于最上游APIcaller；未取得直接链条继续unknown。本调查没有部署该方案。

## DeepSeek 独立复核与纠偏

真实安装的DSH headless，只读当前隔离目录，shell/网络/子代理禁用；首轮exit0，session_id=session-c9f476c7-de27-4ad8-b6a9-1bf6e143c983。原始NDJSON在deepseek.jsonl，提取全文DEEPSEEK-FINAL.md；真实工具读取收据见RECEIPT.json。

首轮独立结论同为undetermined，并同意PID/Store绑定、内部300s不适用与混合采样。其“早读字段来自约05:03:16.2/最后pre-kill点”是超证据时间外推，本报告明确收回；只有per-read时间戳才可确认。首轮未来调查建议未实施，也不自动授权。新增Ray证据已作一次同会话聚焦复核，结果另附DEEPSEEK-RAY-ADDENDUM.md。

所有源SHA、时间、输出哈希见 input-manifest.json（首轮冻结输入）、ray-source-hashes.json（Ray源文件）、final-manifest.json（本地最终交付文件；不含自身）。只修改本调查新目录，没有commit或碰主Git；主agent可统一提交已审核成果。
### Codex 对 addendum 的最终边界校正

DSH 已真实读取新增Ray原始行，并明确收回16.2s推断，最终仍unknown。其“GCS node-dead是死亡后果”应读作与下游失联一致，晚记日志并不证明底层心跳故障开始时间；只是该日志不能独立提供退出前的发信号证据。其“threshold远高于用量所以进一步排除”也只作削弱候选：run-root current不等于Ray MemoryMonitor的完整历史主机用量、采样有间隙；不作严格排除。worker raylet末尾约05:02:24与退出尚差约52s，覆盖缺口明确保留。输入里raw JSON单行在DSH read界面可能被截断，关键原始事件另有pretty格式ray-critical-lines，已真实读取；不宣称它逐字审完所有17个文件。
