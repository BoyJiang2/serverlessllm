# Docker daemon journal：历史终止机制补充证据

此为新增证据附录；不修改原调查报告或旧manifest。仅复制并分析主agent提供的既有只读journal结果，本子代理没有新增SSH、计算或探针。原formal失败/分母不改，不构成R1/R2或计时审批。

## 分层结论更新

- **机制层新增直接证据**：正确boot/CID的dockerd日志明确记录目标在signal15后1m30s仍未退出，随后“using the force”。因此先前“完全没有终止机制记录”已过时；现在有Docker daemon执行停止等待、超时后升级强制终止的直接日志，与exit137/FinishedAt高度吻合。
- **信号递送边界**：该行直接证明daemon报告了停止超时及强制升级，不等于独立捕获最终SIGKILL系统调用、目标wait-status或证明没有并发终止原因。可以报告Docker强制停止机制已有直接证据，避免仅从137推SIGKILL，也避免把机制证据写成所有因果链已齐。
- **请求发起者与起因仍未知**：未提供最初stop API请求、客户端身份、自动清理/人工/平台记录。不能点名具体人员或平台；dockerd PID6247是logger/执行层，不是APIcaller身份。

## 原始绑定核对

输入：target-journal-20261025T0456-0505.json。

全部6条目标记录CID为`8ebd672e388d52410269e3c0de99a040274ecd0fa5268a9b26f86481eaf9ac24`，不是更早e60c或之后4b34。全部`_BOOT_ID=0145ca12ac6e40fc97605d0639290553`，规范化后与原identity boot0145ca12-ac6e-40fc-9760-5d0639290553一致。

|记录|MESSAGE UTC|logger字段|解释|
|---|---|---|---|
|connecting to shim|04:58:20.057721468Z|containerd.service / containerd / PID5515|与Docker启动04:58:20.052203220Z相邻|
|signal15后1m30s未退出，using the force|05:03:15.681605979Z|docker.service / dockerd / PID6247|完全相同CID的强制升级记录|
|shim disconnected|05:03:16.302539394Z|containerd.service / containerd / PID5515|晚于FinishedAt05:03:16.289423094Z约0.013116300s|
|cleaning up after shim disconnected / dead shim|05:03:16.302583676Z / .302591763Z|同containerd|生命周期收尾，不证明最初stop发起者|
|ignoring /tasks/delete|05:03:16.302716229Z|docker.service / dockerd / PID6247|退出后删除事件处理，不是发起者证据|

强制升级日志早于FinishedAt0.607817115s。`__REALTIME_TIMESTAMP`分别提供journal微秒实时时间，和MESSAGE中的纳秒时刻仅差几十微秒；粒度不同，不混作独立同精度测量。

由05:03:15.681605979Z减90s得到约05:01:45.6816Z，仅为等待时长的算术回推，不是观测到的SIGTERM发送/原始API请求时间，也没有建立“启动后205秒的某策略”事实。

## 覆盖与反证边界

services查询exit0：保留62条、筛选目标6条；raw stdout66533B、SHA43e2898c58a4542105ec404690557a37d595bae2d6a8f74038b3647152bf6eff。输入保留目标记录而不是完整stdout，完整性只能按收集scope描述，不能把未入选内容当不存在。

kernel查询exit1且标明read failed，无权作“没有OOM”的负结论。pressure_indicator_records_count=0只描述筛选结果，不绝对排除kernel/并发其他原因。原监控root计数全0和大量MemAvailable仍是独立反证；本新增证据不把这些覆盖缺口消除。

Ray node-dead仍为退出后日志；新daemon记录提供了此前没有的近端机制证据。Store池未完成、正式模型未进入的阶段判定保持。

## 最小后续

将历史事件标签更新为：`Docker daemon stop timeout / force escalation observed; upstream stop requester and reason undetermined`。不再泛称机制完全未知，也不宣称顶层根因定位。若主agent已有同窗口daemon生命周期上下文，可只读核对最初请求/daemon shutdown链；没有则冻结上游unknown，不额外复现模型。

## 独立复核

真实DSH在原只读session-c9f476c7-de27-4ad8-b6a9-1bf6e143c983进行一次聚焦复核；原始输出与提取结论另存本supplement。只读输入及会话工具收据、哈希见本目录manifest/receipt。
DSH聚焦复核已exit0，实际读取本JSON（工具收据见RECEIPT.json）。同意机制层更新，原文见DEEPSEEK-FINAL.md；其SIGTERM收到表述按daemon报告理解，不升级为独立投递追踪。
