# 睿神，基线迁移已完成

今后唯一适配工作目录：`qjhs002:/data/brick1/boyjiang/kimi-new/ServerlessLLM`。这覆盖此前旧root规则。

- 适配源码仍 `00097eaafd60dbae262a954a29863b5c633aade9`，分支 `kimi-qjhs002-aligned-tail`，origin为 `https://github.com/BoyJiang2/serverlessllm.git`；13项源码/测试SHA及5个原生artifact/alias身份核验，迁移后四项SDK核心导入通过，CLI仍缺peft。
- 旧适配树在同文件系统rename至 `/data/brick1/boyjiang/kimi-k2.6/p0/baseline-relocation-20261103/retired-adapted-checkout`，保留build与历史，没有大拷贝或递归删除。
- `/data/brick1/boyjiang/ServerlessLLM` 已重建原版：官方main `9f50241baa5386e06a9321c51f19a9ef5f964c2b`，tracked/untracked clean，origin为官方 `https://github.com/ServerlessLLM/ServerlessLLM.git`，独立objects、无alternates。服务器直连GitHub clone两次TLS失败；最终从本机已验证原始Git对象执行local clone --no-hardlinks，不称服务器网络clone成功。Windows核验官方main同SHA。
- 用户确认此前为递归scp；[官方scp手册](https://man.openbsd.org/scp.1#r)说明-r跟随符号链接。target原1994B普通proto及元数据/diff先备份到服务器迁移证据目录，再仅target恢复Git120000链接 `../../proto/storage.proto`，内容SHA仍 `95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b`；旧历史不改。

迁移完成不等于整体适配完成：GPU FAILED_RETAINED/no-retry保持，tokenizer语义差异及其他未通过项保留。未继续实验、未触GPU/GLM、未改goal状态。权重、环境、build树和库二进制不纳入本次Git证据。
