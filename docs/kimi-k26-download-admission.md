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

`python -X utf8 -m unittest discover -s tools -p test_download_kimi_weights.py -v`：9项通过，覆盖SHA成功发布、SHA错误保留partial、短响应、禁止覆盖成品、禁止覆盖partial、supervisor的截止/退出码记录及源SHA拒绝、重复登记拒绝、身份发布失败回收自身进程组。测试为下载I/O单元测试，不是模型权重正确性或服务通过证明。

首次启动包装器因内联字符串转义错误在Python解析阶段失败，未创建下载子进程、registration或checkpoint，原run与日志保留。这不是传输失败后自动重试。修正为受版本控制的独立`tools/supervise_kimi_download.py`，部署前先实际py_compile与CPU测试，通过后才创建下载进程；失败记录单独入账。

新源码、审阅与部署身份入证据；GLM runtime/active、历史40ID和金标不修改。下载完成后仍需独立复核远端64文件的收据和哈希覆盖，才能进入下一阶段。

## 实际启动记录

修正后的任务 `kimi-download-20261028T171334Z` 于2026-10-29 01:13:36+08:00开始，运行代码commit `6f28f68611e8824113ce7018e75d3fde79deb7fa`；远端实际编译与9项CPU测试通过，timeout PID3735195及boot/starttime已独立核验。硬截止07:13:36+08:00，最晚KILL07:14:06；无自动延期/重启。资产目录为`/data/brick1/boyjiang/kimi-k2.6/checkpoint/7eb5002f6aadc958aed6a9177b7ed26bb94011bb`。

首份进度仅证明进程已启动、正在请求首个小文件，尚无完成文件，不代表权重传输或完整性成功。最新原始状态以远端downloads目录registration、events、exit/failure/complete为准；本机证据快照明确带采集时间。

### 服务器直下载传输修订

上述HF任务在首个请求30秒后因URLError退出1：0文件、0权重、0partial；两个登记进程均已退出，失败计入资产获取记录，不覆盖或重新标成功。远端HF、hf-mirror具体配置文件IPv4 HEAD也失败。用户随后授权必要时本机透传，但要求优先服务器下载。

服务器可直连官方ModelScope仓库。核对其API文件清单：64权重尺寸与官方HF LFS SHA全部一致；另外32小文件实际从冻结HF版本取回计算SHA，31项与ModelScope一致，仅`.gitattributes`不同。新增传输清单固定95项的各自ModelScope文件commit与HF内容SHA；不使用master动态下载，不获取ModelScope额外文件。`.gitattributes`仅1643B从冻结HF原件经SSH部署，校验SHA后写入。

这保持同一个冻结HF checkpoint的全部96项字节身份，不是换模型或换权重修订。每项完整SHA才算成功。服务器已实际GET正确config（5416B，原SHA一致）、GET权重首1MiB范围并丢弃，以及HEAD完整首分片（200，995001888B，cdn-lfs-cn-1.modelscope.cn）；样本只验证传输入口，不计为完整分片验证。本机未建立大权重透传或保存权重文件。

新任务沿用单流32MiB/s、低优先级、6小时硬预算、失败无自动重试；新run登记新代码，不续旧截止。旧空checkpoint树先核实仅为空目录、无文件/symlink，再rename入旧run保存；不删除资产。传输清单联合篡改官方与mirror SHA拒绝测试已加入，当前10项CPU测试通过。模型运行和Store门槛不变。

**ModelScope实际执行：**`kimi-ms-download-20261028T172311Z`，代码`d654132a41f890335daf4bc1936cd83f9d0a0db3`，2026-10-29 01:23:13+08:00启动。远端10测试和编译通过；timeout PID3756630，boot/starttime复核一致。独立截止07:23:13，最晚KILL07:23:43。首次采样01:23:26已有26个小文件、24,129,303B通过完整SHA，权重分片当时尚未开始；后续以带时间的进度证据为准。本机未建透传、无大权重副本。
