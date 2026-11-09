# Kimi 三阶段四条件补采：冻结交付与单次运行申请

状态：代码、CPU测试、真实源码准入和十份实际命令均已完成并通过独审；**未运行，新正式样本数0**。本交付不自动授予新300秒正常关停协议或GPU运行权限。

| 冻结项 | 可审阅结果 |
|---|---|
| 存储路线 |仅qjhs000(qjhs0)现有目录；原始权重只读`/mnt/raid/models/Kimi-K2.6`，复用既有Store。该节点为本地XFS RAID `/dev/md0`，不能称历史qjhs003 NVMe。SF控制池64MiB、Store条件640GiB，chunk64MiB/4线程；不新增模型副本或转换。 |
| 计时起止 |图5主值为八rank最早完整加载开始→最晚返回；同时记录每rank full/ingest/post端点、时长和最大值，三类跨度不相加、不称纯I/O、不新增CUDA同步。A主值为实际HTTP生产端/register前→精确JSON解析完成、断言前；另保留received口径供GLM历史边界比较，二者不冒称完全等价。Controller用同Invocation原生进程起止，不能用Prep+A替代。 |
| 版本pins |源码`87bc7ab2a3106eaea50ace11e2a000e228a0c9c5`已push并核远端；三overlay manifest `79e0584259faee50d6f62ff947f7ef98118da9abc0f6657f30e59e9ac4f434df`；沿用原镜像与Store ELF `95d14150…`、checkpoint ELF `9d629824…`。完整SHA、实际RO挂载、83项pins与十文件摘要见配套JSON及独审。 |
| 数值预算 |7200秒/条件；28920秒/整块（8小时2分）。原600秒有序停服段内包含候选Store300，另留120秒释放段（实际静默≥60）和180秒取证；执行前按真实GO与unit期限再次核覆盖。40项等价上界289200秒=80小时20分仅算数说明，不启动新40项，也不是开跑ETA。 |
| 通过/失败规则 |缓存准入、三请求正确、8rank三阶段/时钟/原文件前后SHA、服务及容器正常退出、≥60秒释放均须通过。样本有效性、条件终态、整组终态、清理结果分开；失败立即停止、后项NOT_STARTED，不重试、不换ID，不用强杀/后台释放冒正常PASS。 |
| 关停变更申请 |仅本新profile拟将正常Store退出从60改为300秒，包含IPC、诊断、finish及strictgone。依据新增136.173秒自然退出实测提出，不用217秒外推，也不保证未来必然300秒内完成。旧60秒FAIL和旧协议完全保留；上一轮“300秒额外观察”授权不复用为本次正式授权。 |
| 正式样本与资源缺项 |新块`kimi-load-stages-20261109-qjhs000-v1-b01`，四项均NOT_STARTED，n=0、数组空、均值/中位数null。当前API预约只覆盖到2026-11-09 23:00，不足完整8小时2分；23:00至次日07:00属其他预约，不抢占。仍需新协议/本块单次批准及完整窗口，随后重查资源/root/pins/未用claims/独立守护。 |

## 已完成验证及实际命令

- 仅四个源码文件修改：canonical vLLM补丁、既有loader接口测试、既有四条件生成器与测试；没有新部署框架、checkout、环境或编译库副本。
- 生产端39/39 CPU通过，独立39项复跑；消费者114/114通过，独审先112项及修正版13项针对性复测。旧93行为仅以SHA绑定的15b9原始输入重放，真实新源码拒绝旧pins也有反例，不称旧spec在新树可直接运行。
- 实际GitHub API/ref与本地committed blob准入通过，非mock CLI生成4 spec+4 command+bundle+root-command，共十文件。最终独审`a579cccd5af17faa984cf9dfca6de4ee956d55b2c399595bcf91c0d80a9096ef`核83项文件pins、实际字节和完整预算；没有执行其中argv。
- [最终根命令JSON](actual/kimi-load-stages-20261109-qjhs000-v1-b01-formal-v3-78beeac12a70-root-command.json)；SHA `65eb72256626c5a2c41319d513e0f546091714f82450fd1addd71c0b13d50b70`。只允许在新批准和新鲜准入后，将现有argv最后的预约expiry占位符替换为核验值，并用原O_CREAT|O_EXCL/0600 claim流程启动一次；不得复制改写另一套入口。
- [详细冻结表](FROZEN-STORAGE-TIMING-VERSIONS-BUDGET-v1.json)、[实际生成回执](actual-frozen-render-v1.json)、[最终独审](final-frozen-artifacts-independent-review-v1.json)、[GLM真实边界依据](GLM-ACTUAL-C23-HTTP-BOUNDARIES-v1.json)。GLM省略的采样字段不凭默认猜；Kimi自己的tokenizer/template/gold保留。

原Kimi40项38合格/2失败、主集8完整块、已接受L1、GLM35成功/5失败、历史f7与所有失败编号不变。新补采不回填旧主集。缓存准备、池初始化、请求与正常关停分别记录；未测数据不画零值或估计柱。
