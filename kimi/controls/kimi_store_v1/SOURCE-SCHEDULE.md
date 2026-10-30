# 源转换批次：完整预算与窗口预检

`source_schedule.py`只做本机CPU配置/时间检查，不启动任何模型或进程，不安装守护，不改预约。输入是外部SHA绑定的≤64KiB计划、已有source spec与源码清单。计划的run_id/holder/code_commit/spec SHA/源码manifest SHA必须与verify_source_run结果一致；固定资源h20/qjhs003。booking_id只是声明，返回booking_verified=false，不能据配置文件自授授权。

固定预算序列是preflight、image_restore、engine_initialize_and_export、fixed_token_business、natural_eos_business、worker_shutdown、release_observe、cleanup_evidence、safety_margin，全部正整数且有限。engine阶段包含TP/通信组初始化、SF加载、导出、后处理/KV/必要预热；preflight包含代码/输入与实际资源/守护检查的预算，镜像恢复及可能的编译/校验也必须明确归入相应上限，不从总成本里消失。这里只定义字段，未冻结生产秒数，测试内数值不作模型耗时估计。

观察的最低持续时间固定60秒；release_observe预算上限可更长，例如90秒用于采样/收尾开销，不能把60秒观察压成更短时间。所有阶段最大预算相加后才能判断是否能开启本批。3600秒外层空间不能容纳3721秒总预算；每一秒边界纳入校验。

## 截止关系

- 预约起止、窗口清理终点及当前时间必须带时区，统一到UTC计算；未到窗口开始即拒绝，不因GPU空闲提前开始。
- 窗口清理终点最多是预约结束前10分钟。now+全批最大预算超过该点即拒绝，不能用固定“几点停止开新任务”替代。
- 本批清理终点是now+全批最大预算，而不是整段预约的末尾。因此长预约不延长一个小批次的寿命。
- 独立守护最迟触发点是本批清理终点减去完整worker_shutdown+release_observe+cleanup_evidence+safety_margin尾部预算；不等到预约结束才停模型。
- 各阶段绝对deadline由同一批次开始时间累计推导；没有运行时延长旧plan/guard的接口。实际执行仍须用单调时钟、独立守护和精确身份执行这些期限，当前模块没有完成这些执行器。

CLI先做真实code/spec检查再读plan，但开始时间在检查之前采集；核验耗时用monotonic计入preflight，不在读完配置后重新起算。核验耗尽preflight、墙钟倒退或已越过preflight deadline都会拒绝。CLI没有--now等改钟入口。输出timing_fits只说明时间配置可容纳，runtime_admitted和guard_verified始终false。

CLI形态：

```text
python source_schedule.py --code <run/code> --manifest <run/source-code-manifest.json> --manifest-sha <SHA> --spec <source-spec.json> --spec-sha <SHA> --plan <source-plan.json> --plan-sha <SHA>
```

## 验证及剩余工作

新增11项CPU测试，总计246项通过；包含全预算精确边界/差1秒、3721对3600、过早/过期/时区/身份/缺阶段/bool/观察不足拒绝、观察预留开销、守护尾部、长预约不延长小批次、外部哈希/读取上限，以及CLI核验耗时不能重置。两个fresh CPU CLI用小Git/metadata夹具实际连接code→spec→plan；时间不足/窗口未到拒绝，时间可容纳仍返回runtime_admitted=false。夹具不是实际节点授权或模型，未运行vLLM/八卡。测试导入重名的初次失败保留source-schedule-tests.json。

下一项是把此预检接进实际source worker/宿主控制器，核验真实UID/boot/容器/cgroup/授权、安装独立guard并probe、连续监控、实际ABI和完整内存峰值预算及退出取证。没有这些证据仍不启动计算。已预约时段仍按RESERVATION-PLAN.md提前评估，不占模型或大池等审批；本模块不批准P0/P1或任何GPU运行。GLM历史与40ID不变。
