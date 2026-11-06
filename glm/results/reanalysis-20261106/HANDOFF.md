# GLM分析交接（由用户统一协调）

未与Kimi会话继续通信；不启动任何模型/池/探针。home副本已删除，收据见home-cleanup-receipt.json。

分析绝对路径：D:/Documents/codex/2026-10-24/new-chat/serverlessllm/glm/results/reanalysis-20261106

原始产物绝对路径：qjhs003:/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106

当前结论：40/40权威结果SHA匹配，35成功/5失败；完整C23主配对blocks5–10；n6/n7敏感性已独立复算。原16均值sanity最大差0.004755901秒，数值和26条事件时序gate通过。旧池卸载14/26条。第四项不含准备p=0.5625，不能称四项全部0.125。

未解决：完整部署相对官方的逐文件差异、独立纯转换/填池成本、N>1真实复用；Kimi缺测。REPORT明确列界限，不生成确定回本曲线。Git发布提交以本会话实际push/ls-remote核验为准。

| 文件 | SHA256 |
|---|---|
| independent_audit.py | `b939cfc2e091b5302f7f81d4a43262149b2973cc08561eb7eaa5147e1cf63448` |
| event_audit.py | `c3df20e6831c274a6e0227b548fc538755c49ceee4e388f5cf06b9d9bb0fe5e7` |
| plot_analysis.py | `ce80e032902571a29745c1107b7b26b4b96e9acd88cc2681aa55d675c71a7c8c` |
| independent-audit.json | `c7a47982e636f9157b7d307f87f64f6ec21c9e90075696eda294b3a85e7ec451` |
| event-gate.json | `71e1c8a3f21173aa3a7f5d52ae936aa2b1ac5d0e830b4f232739f8ac3cd52e03` |
| REPORT.md | `a7a4e15c1f7afeb1ee7fe08d6ea07faa1d7a3a493106d4db318ebd8fd7ea3fab` |
| raw-event-inputs.json | `c32f0e8c63fa50e4aa938673ee443c4b60f6feea19af58709b3ef2714b95f02d` |
| inputs/formal-ledger.json | `76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b` |
