# Kimi-K2.6 最终交付索引

- [最终报告与验收记录](REPORT.md)
- [CPU复算及绘图说明](REPRODUCTION.md)
- 文件完整性：manifest.json；原40项与历史/补跑分层见all-runs-ledger.csv；加载阶段见observed-loading-spans.csv。

L1功能验收已由所有者接受既有证据；原NOT_L1和旧30秒FAIL保留，没有新的正式L1运行。Kimi原40项全部启动，38合格/2失败/0未启动；GLM原40项仍35成功/5失败。额外及历史样本单列，不填回主8个完整配对块。

本目录7套PNG/PDF/SVG包含论文式均值对比、ECDF、准备分解、全40项、N复位成本模型、加载跨度与五段互斥事件分解。全部实际数值、区间、时钟、版本与源SHA均可追踪；N>1是条件推演，非新测量。

GPU实际源码86fd/v8和835/v9、统计实际a43734f、只读collector源码fdc3643分开固定。本轮只从既有日志补取证据和CPU复算，没有新GPU/Store/GLM实验。

最终报告已独审；生成前记录中的pending不回写，当前发布状态以所在Git commit及服务器发布回执为准。未知纯分量、每PID映射、FlashInfer实际import及GLM固定32独立时长均按定义保留缺测，不是0或已测。
