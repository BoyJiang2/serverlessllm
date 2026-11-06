# GLM分析交接（由用户统一协调）

未与Kimi会话继续通信；不启动任何模型/池/探针。home副本已删除，收据见home-cleanup-receipt.json。

分析绝对路径：D:/Documents/codex/2026-10-24/new-chat/serverlessllm/glm/results/reanalysis-20261106

原始产物绝对路径：qjhs003:/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106

当前结论：40/40权威结果SHA匹配，35成功/5失败；完整C23主配对blocks5–10；n6/n7敏感性已独立复算。原16均值sanity最大差0.004755901秒，数值和26条事件时序gate通过。旧池卸载14/26条。第四项不含准备p=0.5625，不能称四项全部0.125。

部署声明补丁集已核验：17个v5目标文件三方SHA一致，C23单独Store服务覆盖与实际head CPU导入相符。deployment-gate限定PASS_FOR_DECLARED_PATCHSET，不能扩展为全仓库或逐rank映射机器码证明。

未解决：声明范围外全仓库差异、逐rank映射机器码、独立纯转换/填池成本、N>1真实复用；Kimi缺测。REPORT明确列界限，不生成确定回本曲线。Git发布提交以本会话实际push/ls-remote核验为准。

收到运行终态及释放结束通知，Git限制已解除。0949f33已推送并核远端SHA；最新补丁及阶段跨度交付另提交，后续以实际发布回执为准。保留并发Kimi提交链，不改写其他任务文件。

补充核验：所有记录阶段均覆盖八rank且跨度由起止时间独立重算；完整26条均通过。含准备配对统计沿用原preparation+command定义，遗漏的独立间隔在正文明确披露，互斥图已包含。检验的符号对称/独立与bootstrap可交换性假设明确列示，不宣称已由n6验证。

| 文件 | SHA256 |
|---|---|
| independent_audit.py | `e1e186398d54642d301209b85007fb835f8a89d9ba533c9856687b25e82d9c33` |
| event_audit.py | `03ad1f172afa048a80c3604511cae422c745f78c0e19cd85b1fef31bea98065f` |
| deployment_audit.py | `800adb19e88c2dca38fabb8882f51fe1196d11e43c81b01afc496e27ced45d7d` |
| plot_analysis.py | `038706374a76162fca4ca2dfda00cf8aaac8aa6e776c8d595298560c068b12bf` |
| independent-audit.json | `a0d3c29481327e1ec0887426b9a4594b956ac673831b02a50ab8f242e34dec86` |
| event-gate.json | `01e0bf4b6dcea514cd71c46c0bf78a3114804256bfe182feb2c3961695659f34` |
| deployment-gate.json | `7b06655df102d2d998149aa3caad2c0dd0bd27cb7dfc9ed242a8c73195f27c71` |
| REPORT.md | `f761a164078e74f20d02ff0f076c41c64892acbbcf0b68ec80a2af107112abf5` |
| raw-event-inputs.json | `c32f0e8c63fa50e4aa938673ee443c4b60f6feea19af58709b3ef2714b95f02d` |
| inputs/formal-ledger.json | `76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b` |
