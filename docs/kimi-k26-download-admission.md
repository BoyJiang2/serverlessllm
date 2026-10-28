# Kimi-K2.6：INT4 审查与下载准入

2026-10-29。用户已明确要求完成检查后开始权重下载。本准入只覆盖资产获取，不批准 Store 转换、建池或模型启动。

## 兼容性审查结论

| 层次 | 结论 | 下一项实际验证 |
|---|---|---|
| 官方序列化 | 64 文件/208,550 张量文件头与索引相符；I32 packed/shape、BF16 scale/其他权重、F32路由bias | 下载后逐文件官方 LFS SHA256 |
| Store 基本 dtype | 现有 C++ dtype 表包含 int32、bfloat16、float32；不需要把 INT4 解成 BF16 才能存储其容器 | 新适配器全量逐 tensor 保存读回 |
| 现有 GLM loader | FORMAT/revision/FP8检查固定，**直接替换模型不可用** | 独立 Kimi 格式与 revision 校验，旧路径不变 |
| TP8 分片 | hidden7168、专家intermediate2048、group32均可整除相关TP分片；384 experts可整除8 | 实际 fused w13/w2 的rank布局、scale切分和复制规则；可整除不是运行证明 |
| 后处理 | v0.19.1存在 Marlin repack/scale permutation与FlashInfer分支 | 固定实际backend和输入dtype；对比原生/恢复相同阶段的八rank全量状态 |
| 原始 weight_shape | 索引含69,120项，加载时未必仍以同名参数存在 | checkpoint原件不丢；Store sidecar保留原shape、映射及完整量化配置，不能仅靠named_parameters覆盖率 |
| 配置/多模态 | 量化位于text_config；视觉等BF16资产是正式模型一部分 | 全部配置、tokenizer、processor、自定义代码原样下载但不执行；文本参考不冒充视觉验证 |

**结论：官方输入适合按原格式获取；完整 ServerlessLLM 兼容性尚未成立。** 下载不依赖于声称新的 loader 已通过。后续继续原生参考→独立Store正确性→原生完整服务，不绕过门槛。

## 字节预算及峰值约束

从配置维度和完整文件头总量交叉计算，专家 packed 为 `60×384×3×2048×7168÷2 = 507343011840 B`；专家scale为 `60×384×3×2048×7168÷32×2 = 63417876480 B`；shape为 `69120×2×4 = 552960 B`。其余payload为24386751456 B。

假设专家packed/scale跨TP8总量不扩张、其余张量及shape最保守复制八份，投影总量约765.86GB（约713.26GiB）。这是**源码设计估计，不是可证明的Store上界**：融合参数、别名、对齐、空g_idx、视觉复制和kernel状态仍需实例清单验证。因此暂留 **800GiB Store磁盘额度**，并不表示允许创建800GiB池。

2026-10-29 01:04:34+08:00现场：brick1可用2160609628160 B，MemAvailable1983643049984 B。可见其他用户容器；八卡0MiB仅为当时快照，**不是独占证明**。未操作其他容器，不占GPU。只做低优先级单流下载，不做带宽测试/全量缓存控制。

下载输入为全仓库96项（64权重+32小资产），权重595177988208 B，加元数据约27MB。要求可用空间至少覆盖本次全部输入 + 未来Store800GiB + 安全余量300GiB；预计约1.776TB，现场2.161TB满足。每文件前复查下一文件+1100GiB保留，不清理他人数据，失败保留partial。

运行态设计另行约束：800GiB池预算 + 554.30GiB新checkpoint页缓存 + 每rank至多2GiB导出块（八rank合16GiB）+ 服务64GiB + 300GiB余量，合约1734.30GiB。当前MemAvailable约1847GiB，纸面可容纳，但 **分块导出尚未实现和测峰值，800GiB池也不是实测**。原GLM exporter会累积CPU字典，不能据此直接执行；不允许与GLM大池同时驻留。这一项仍阻断转换/模型运行，不阻断低驻留流式资产下载。

## 下载实现边界

- 唯一远端qjhs003；专属根`/data/brick1/boyjiang/kimi-k2.6`，不变更现有权限/数据。checkpoint放冻结revision子目录，运行记录放downloads/唯一run_id。
- `tools/download_kimi_weights.py`只用Python标准库顺序下载，不安装包、不执行模型代码、不使用Docker/GPU/RDMA。
- 单流32MiB/s应用限速，nice19、ionice idle；读块8MiB。每256MiB fsync后对自己文件做POSIX_FADV_DONTNEED建议，降低本任务页缓存；不清全机缓存，不宣称内核保证零驻留。
- 官方固定revision；所有文件核对HTTP Content-Length/实际长度，64权重额外核对官方LFS SHA。config及index校验已冻结SHA，其余小文件记录实际SHA。
- 文件先`.partial`，通过才rename；已存在目标或partial拒绝覆盖。无自动重试/续传；失败留证后单独判断，不重置登记。
- 外层GNU timeout 21600秒，TERM后最多30秒KILL；网络单次阻塞30秒。运行登记记录UTC截止、PID、boot_id、starttime和源码commit/hash。即使SSH断开，timeout仍约束下载进程；不创建定时任务。
- 退出码、完成/失败文件及JSONL事件保留。32MiB/s理想传输约4.93小时，6小时是硬预算，不保证网络能完成；超时不隐式延长。

## 本地验证

`python -X utf8 -m unittest discover -s tools -p test_download_kimi_weights.py -v`：5项通过，覆盖SHA成功发布、SHA错误保留partial、短响应、禁止覆盖成品、禁止覆盖partial。测试为下载I/O单元测试，不是模型权重正确性或服务通过证明。

新源码、审阅与部署身份入证据；GLM runtime/active、历史40ID和金标不修改。下载完成后仍需独立复核远端64文件的收据和哈希覆盖，才能进入下一阶段。
