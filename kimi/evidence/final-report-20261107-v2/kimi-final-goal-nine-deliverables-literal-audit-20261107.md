# 原goal最终九项逐字要求审计

依据原goal SHA51926e7c…及V2 SHAa31bfbcf…全文，逐条核现有实际证据。不宣告goal完成；当前真正剩余为报告整合与最终发布校验，不默认追加GPU。

|项|原文短引语|当前证据结论|
|---|---|---|
|11.1|无歧义的L1状态、证据覆盖与单独验收决定|EVIDENCED_OWNER_ACCEPTED_PENDING_CURRENT_REPORT_INTEGRATION；将owner0fc决定纳入当前最终报告/状态，不需要为标签重跑。|
|11.2|版本、补丁、实际ELF、构建与运行身份清单|EVIDENCED_WITH_EXPLICIT_IDENTITY_LIMITS；已有证据交付，保留所列未证明范围|
|11.3|存储路线和GLM/Kimi参数、缓存、计时边界对齐表|EVIDENCED_WITH_DECLARED_DIFFERENCES；新model-loading跨度与import76应补当前表/报告；无需重新迁移或GPU。|
|11.4|冻结且实际可执行的正式实验入口|EVIDENCED_CPU_AND_REAL_EXECUTION_SEPARATE；已有证据交付，保留所列未证明范围|
|11.5|所有实际样本、失败、未执行项和完整原始台账|EVIDENCED_ALL_ATTEMPTS_STRATIFIED；已有证据交付，保留所列未证明范围|
|11.6|分条件统计、成功率及适当的不确定性|EVIDENCED_REPRODUCIBLE_ANALYSIS；已有证据交付，保留所列未证明范围|
|11.7|初始化、转换/准备、缓存、加载、推理、删除、关停成本|EVIDENCED_OBSERVED_COSTS_AND_FORMAL_MISSING_RESULTS_PENDING_INTEGRATION；按已验证scope整合38model-loading与缺测表/非嵌套总成本；不能沿用旧README“privatehostlog尚未读/无C观测”当当前状态。|
|11.8|可比较范围、硬件/模型/精度/节点/介质差异及未支持范围|EVIDENCED_BOUNDARIES_MUST_REMAIN；已有证据交付，保留所列未证明范围|
|11.9|代码与证据的远端SHA和复现说明|PRIOR_RELEASE_PROVEN_CURRENT_INTEGRATION_AND_PUBLICATION_PENDING；最终集成、文件SHAmanifest、明确source/evidence版本并发布/核远端；这是真实剩余文档交付工作，无GPU。|

## 关键原文约束

> goal§8：C. 权重摄入/加载阶段

38个实际f7日志跨度覆盖加载阶段，不是纯ingestion；按源边界报告即可，不能偷换成GLM同义C。

> V2§10：缺测或未验收部分留空，不填预计数值。

> V2§12：验收以证据完整、定义一致、结果可复算为准；不可分、缺测和失败均是正式结果。

所以纯fill、纯conversion、GiB·h积分或everyPID完整maps等更强理想观测，不能在原文没有明定时追加成必须再跑GPU的门槛。原文已有资源GiB、字节、观测峰值、配置和成本范围需如实分列；未知保持null。

57CPU中test_real_http_producer_and_parent_delays_preserve_endpoint第141–142行已注入time_namespace不匹配并让真实parent消费者拒绝，时钟异常测试存在；不应将“未逐个键全部负例”误写为完全没有clock负例。

用户owner0fc已单独接受L1功能且保留rawNOT_L1/30FAIL，不制造新formalrun。原40/失败/历史/补跑和GLM冻结不改。

当前须完成：报告更新owner决定与38model-load/76imports，明确嵌套/缺测，生成最新SHAmanifest并显式发布核远端，再作整体完成复核。此审阅没有缩目标、没有自行宣布完成，也没有授权新实验。

每项路径、完整SHA和原文短引语详见同名JSON。
