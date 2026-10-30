# ServerlessLLM 模型实验

按模型独立维护代码、协议、审阅及结果：

- [GLM-5.1 FP8](glm/README.md)：既有40个预登记ID及其35成功/5失败、金标、原始事件、报告和冻结运行证据。目录整理不改变任何实验结果，也不新增R24。
- [Kimi-K2.6](kimi/README.md)：官方冻结INT4适配、CPU校验、源码候选及后续八卡验证准备。尚未完成真实Store转换或GPU/原生服务验证。

两个目录的README和接续记录分别维护；历史记录中的旧路径、commit和哈希保留其原始含义，不批量改写。原本机checkout及服务器数据没有移动；本仓库不包含权重、Store数据、镜像或凭据。

Kimi当前接续：[NEXT](kimi/controls/kimi_store_v1/NEXT.md)。旧冻结候选仍使用其原清单与校验器；目录调整后的新候选须重新成包和审阅，不能沿用旧gate。
