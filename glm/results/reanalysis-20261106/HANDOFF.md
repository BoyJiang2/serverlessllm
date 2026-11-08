# GLM分析交接（由用户统一协调）

未与Kimi会话继续通信；不启动任何模型/池/探针。home副本已删除，收据见home-cleanup-receipt.json。

分析绝对路径：D:/Documents/codex/2026-10-24/new-chat/serverlessllm/glm/results/reanalysis-20261106

原始产物绝对路径：qjhs003:/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106

当前结论：40/40权威结果SHA匹配，35成功/5失败；完整C23主配对blocks5–10；n6/n7敏感性已独立复算。原16均值sanity最大差0.004755901秒，数值和26条事件时序gate通过。旧池卸载14/26条。第四项不含准备p=0.5625，不能称四项全部0.125。

部署声明补丁集已核验：17个v5目标文件三方SHA一致，C23单独Store服务覆盖与实际head CPU导入相符。deployment-gate限定PASS_FOR_DECLARED_PATCHSET，不能扩展为全仓库或逐rank映射机器码证明。

未解决：声明范围外全仓库差异、逐rank映射机器码、独立纯转换/填池成本、N>1真实复用；Kimi缺测。REPORT明确列界限，不生成确定回本曲线。Git发布提交以本会话实际push/ls-remote核验为准。

已解除运行/释放观察Git限制。分析提交106df5bf615b4d205541cbc8fd80e778dc9663ff已推送，独立ls-remote核验同一SHA。推送时远端已是da56d58，保留其父链，仅快进发布GLM后续提交；未修改并发Kimi文件。发布回执见git-publication-pending.json（文件名沿用历史）。

补充核验：所有记录阶段均覆盖八rank且跨度由起止时间独立重算；完整26条均通过。含准备配对统计沿用原preparation+command定义，遗漏的独立间隔在正文明确披露，互斥图已包含。检验的符号对称/独立与bootstrap可交换性假设明确列示，不宣称已由n6验证。

自包含输入：两份冻结补丁与成本汇总已保留精确小副本在inputs，分析脚本不再依赖目录外文件。服务器交付目录为qjhs003:/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/v2；独立于原产物，原始MANIFEST与recompute不覆盖。自包含代码提交80df240b2b019cd99450530a4945b2509b57faec已推送并核远端SHA；三个脚本在服务器实际复算exit0，全部数值/事件/部署输出SHA仍相同。同步回执见server-sync-receipt.json。

| 文件 | SHA256 |
|---|---|
| independent_audit.py | `e1e186398d54642d301209b85007fb835f8a89d9ba533c9856687b25e82d9c33` |
| event_audit.py | `03ad1f172afa048a80c3604511cae422c745f78c0e19cd85b1fef31bea98065f` |
| deployment_audit.py | `bc28de89e79dbe47f35b8980fcc44b70df5dc24beb19f7f15e9eb9707e020922` |
| plot_analysis.py | `fd40504fc33a9026fc81f7dd60c8225d3e74c92197c6550eb10471de16221712` |
| independent-audit.json | `a0d3c29481327e1ec0887426b9a4594b956ac673831b02a50ab8f242e34dec86` |
| event-gate.json | `01e0bf4b6dcea514cd71c46c0bf78a3114804256bfe182feb2c3961695659f34` |
| deployment-gate.json | `214e0b546d4bcf1947151dc907f90dc6b2c5632557d41c94a436b7071510b0c9` |
| REPORT.md | `c4d45ff6325aee73cc888d2435fb21a367b0a0dbc988eabc9ee2895e1d8b1ddc` |
| raw-event-inputs.json | `c32f0e8c63fa50e4aa938673ee443c4b60f6feea19af58709b3ef2714b95f02d` |
| inputs/formal-ledger.json | `76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b` |

## 历史日志CPU补充（2026-11-08）

先看historical-supplement.md及JSON：重新流式读取1070633372字节历史监控，原文件留服务器；9647条有效采样与原审计一致。新增52个按原始端点定义的资源窗口、26条helper记录区间、批共享supervisor区间、505.467441929秒转换容器观测；不冒充完整逐次controller或纯save。缺客户端fixed32计时、同定义f7、纯保存/填池、N>1，另列待GPU方案，不启动补跑。inputs中三份tools源码为字节留档，不是独立部署入口；复算命令在Git仓库根执行。
