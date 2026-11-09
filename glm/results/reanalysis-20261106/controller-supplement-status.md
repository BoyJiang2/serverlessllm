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
