# P3 原生路由 CPU 契约修复

2026-11-06，承接 `855af8033978c94039920d0c9f4d0bfcff4dde38`，独立分支 `sllm/v4-raw-store-p3-20261106`。未安装环境、运行GPU或真实Ray服务。

## 实现与复用

当前源码已包含Kimi原生静默删除修复：Controller等待router.shutdown成功才kill和移除注册，router取消auto-scaler/load-balancer后删除ready实例，backend shutdown/kill/资源退还任一步失败均保留owner。此次复用且通过原CPU测试重新验证，没有重复改写Controller。

此次修改RoundRobinRouter.inference及_load_balancer_loop：请求计数的准入与running检查在同一临界区；所有返回、异常与取消进入finally，退回已分配实例的并发槽位并减少请求计数。取消发生在Future已完成但请求尚未继续执行时，仍从ready/deleting池找到原实例并退回槽位；尚未分配则取消Future。负载均衡器跳过已完成/取消Future；等待槽位时发生取消则回滚刚取得的槽位，不再对取消Future调用set_result导致循环退出。generate/encode结果和backend异常保留原语义。

没有更改自动扩缩容策略、懒加载、request触发model load的计时边界、Store格式、backend或模型配置。MigrationRouter仍有已知旧接口缺口，本冻结路线enable_migration=false；未借此修复在途物理迁移或为它宣称兼容。

## 验证

- 原代码7项路由用例：1通过、4失败、2错误。保留Windows原始失败日志。
- 最终9项路由CPU用例在本机通过，并在qjhs002 Python3重新通过；覆盖backend失败、排队取消、运行中取消、分配后尚未恢复时取消、等待槽位锁时取消、LoRA提前失败、停止后拒绝和正常返回。
- 9项已有native-ready lifecycle CPU用例在qjhs002重新通过：覆盖Controller delete/re-register、router静默删除、3个失败边界owner保留、非静默拒绝及旧backend行为。这是FakeRay/engine和实际源码AST测试，不是真实Ray/资源退出证明。
- 本轮发现旧fixture只抽取shutdown而未抽取上一提交新增的_shutdown_engine，也未注入inspect，旧断言仍要求debug stop超时。已补齐实际方法并将断言更新为：重复shutdown等待同一失败，完成的debug history不阻挡stop且仍保留history。修改前失败日志保留；不隐瞒上轮未运行该fixture的覆盖缺口。

本批合计18项Linux CPU用例通过。此前41项V4 Store/loader/backend合跑日志保持历史身份，本轮未重复执行，也不将两批拼作一次59项合跑。日志位于 `docs/evidence/v4-router-p3/`。

## 仍未完成

冷启动资源分配/actor初始化失败时的starting owner回收、启动中断/watchdog、Controller并发注册/删除、在途请求强制删除、worker/IPC/quarantine物理清理与至少60秒释放观察仍未验证或实现。本轮只保证所覆盖的请求计数与分配槽位CPU路径；远程Ray调用取消的真实传播和GPU任务停止没有证据。

完整V4-Pro单slab TP8恢复仍因容量guard被拒绝；分组raw Store、处理后Store或TP16路线仍待用户选择。不得以CPU通过替代完整Store恢复、业务正确完成、再次拉起或GPU验收。
