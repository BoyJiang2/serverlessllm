# Kimi-K2.6 INT4 适配

- [当前接续](controls/kimi_store_v1/NEXT.md)
- [适配代码及CPU验证说明](controls/kimi_store_v1/README.md)
- [预约规划](controls/kimi_store_v1/RESERVATION-PLAN.md)
- [资源预算](controls/kimi_store_v1/MEMORY-BUDGET.md)
- [下载及完整性证据](evidence/followup-kimi-k26/)

代码位于controls/kimi_store_v1，模型专用文档/evidence/tools分别位于本目录下的对应子目录。kimi/tools/deploy_tree.py是原Git对象读取器的逐字节副本，仅用于本机源码打包，不调用其GLM部署入口；其固定SHA继续由source_bundle校验。

从仓库根运行CPU测试：`<CPU解释器> -X utf8 -m unittest discover -s kimi/controls/kimi_store_v1 -p "test_*.py"`。

235项CPU测试及旧候选证据是迁移前里程碑；迁移后的路径/打包验证另行登记。完整Store尚未生成，真实GPU/worker/守护仍待准入。96个官方文件已下载校验，不重复下载；服务器资产路径保持不变。
