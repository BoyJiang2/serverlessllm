# 历史 Controller 核验结论

本轮只读核验40条权威result的SHA，未重新扫描大型监控、读取完整权重或运行模型。原40项终态35成功/5失败、C23六块样本与所有原值不变。

**现有已核验记录不能补出同协议四条件的独立完整Controller。**

| 记录组 | 核验事实 | 为什么不能填逐条件完整Controller |
|---|---|---|
| C23，26项 | 两个公共parent端点及最终75.982432416秒释放记录匹配归档SHA；26项都位于这套parent内部 | 公共栈跨项驻留，不能按条件均摊；helper complete不是进程正常退出。head为受控停止、child_returncode=1，保留原义 |
| C21，4成功 | SF冷03、Store冷03、SF热04、SF冷04同属一套formal13；没有池命中成功项 | 不是四个独立部署生命周期，也没有四条件完整组 |
| C19，1失败 | store_pool_hit03，保留router_instance_published失败 | 不能以失败parent退出填正常Controller |
| 早期9项 | 原值按ledger逐条SHA核验；v7的4成功+热读失败共用formal3；v8的SF冷成功+SF热失败共用formal4，其他两项失败 | 缺逐项完整parent终点；成功helper不可代替整栈退出，更不能跨修订凑四条件 |

边界：这不是声称所有未登记探索日志都已穷尽。以上结论针对与原40项关联、目前可核验的结果和父进程档案；不将旧版本失败重判为成功，也不拼接C23六块。

可复算入口：`python glm/tools/historical_costs.py --c23-controller-audit`。`controller-history-audit.json`含C23逐条helper/Prep/A、公共parent与输入SHA；`controller-history-remote-verification.json`记录独立远端复核的31个小文件（26结果+5父进程/dispatch/释放文件）；`controller-legacy-results-audit.json`含另外14项按ledger.result_path读取并核SHA的结果摘要。没有把缺失Controller补零。

后续独立补测必须在同次运行记录Controller、Prep、A及既有加载阶段；Controller起点在宿主入口冻结，终点在正常受控退出、至少60秒全释放观察后。当前候选完整R1为BLOCKED，详见`../../controls/debug_stack_v1/controller-supplement-r1-review.json`；尚未产生新GPU样本。
