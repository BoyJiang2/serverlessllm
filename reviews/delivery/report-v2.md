# GLM-5.1 FP8 扩展实验交付报告（结项审计草案）

本实验验证官方 GLM-5.1 FP8 在修改后的 ServerlessLLM 上的保存、恢复及原生服务生命周期。它属于扩展实验，不宣称复现论文原始模型或完成论文全部评测。正式测量已结束；全历史成本存在明确缺口，仓库推送尚待外发范围确认，整个 goal 尚未标记完成。

## 范围和真实状态

- 唯一正式计算入口 `qjhs003`，实际 hostname `qjhs3`。服务器规范根目录 `/data/brick1/boyjiang/glm5.1-fp8`；不恢复已移除的旧路径。
- 官方模型 `zai-org/GLM-5.1-FP8`，revision `f396cf805182f4ca10fa675e1a99815b3ca384db`。TP=8、上下文2048、并发1、贪心、MTP关闭、eager、256 KV blocks；E8M0重新量化关闭。
- 原40个ID全部终态：35成功、5失败、0未开始，无下一ID。原顺序、金标、历史失败均保留，未重试替换。见[总账](formal-ledger.json)及[导航复核](global-ledger-review.json)。
- C23实际运行代码 `f176e05607d04999ab28e8ea8cd49b41270cb528`，MF `3eac8c63cdd670a49d872922c11b821d07a0e252f2fbe80f790e169749091fb9`。报告工具的新提交不冒充运行代码。

## 资源预算与兼容性

| 项目 | 数值/结论 | 证据等级与边界 |
|---|---|---|
| GPU | 8×H20-3e，每卡143771MiB | 历史实际身份快照；C23采样峰值见成本账；不代表此刻整机可用 |
| 主机内存 | MemTotal约1923.283GiB | 实测容量；不是本实验独占额度 |
| 正式输入盘 | `/data/brick1`，本地XFS，`/dev/nvme1n1` | 已核验挂载；正式输入没有采用NFS |
| Checkpoint | 142 shards，756177645648B | 已有资产；配置/索引等另列；不重复下载 |
| TP8 Store | 80数据部件，762037043200B | 已有资产；不按每ID重复记新增容量；inactive MTP等附加资产另列 |
| 公共主机池 | 768799145984B=716GiB | 配置值并有实际整池/RPC证据；四种正式条件都保留 |
| Safetensors页缓存预算 | 约704.245GiB权重，加冻结清单中的元数据 | 估计/资产尺寸；实际驻留由完整mincore判定，不能把配置量当峰值 |
| C23部署内存峰值 | 1553848774656B | 自有cgroup采样峰值；kernel峰值未知；不是整机峰值 |
| 删除后的公共GPU基线 | 每卡1769MiB | 实测辅助进程，计入公共闲置成本；完整退出后另验收八卡归零 |

资源详细数值见[C23成本](c23-costs.json)及其[独立审阅](c23-costs-review.json)。worker闲置cgroup不包含head/宿主；RSS求和会重复共享映射，PSS为非原子单次快照，Linux Locked不能覆盖全部CUDA注册内存。池、源权重、目标页缓存和临时副本的同时驻留通过实际峰值及分条件证据记录，不能只把池尺寸作为部署成本。

[冻结版本证据](../evidence/fp8/runtime-versions.json)：vLLM `0.19.1.dev1+g43a9b1afb.cu130`，commit `43a9b1afbb7ed88e63ebeefeb55df8d445aa1992`；PyTorch `2.10.0+cu130`、CUDA13.0、Transformers5.4.0、Triton3.6.0。FlashInfer0.6.6来自同一不可变镜像的A1版本探针；C23未逐包重新证明其实际worker导入路径。Ray2.48.0来自实际挂载的serving-deps发行元数据；DeepGemm2.3.0为镜像源码版本字段。此前image源码中的FlashInfer未知值及grpc dev0属于fallback，不作为已安装版本。镜像ID及C23导入来源、二进制哈希另见实际审阅包。

ServerlessLLM基础commit为 `9f50241baa5386e06a9321c51f19a9ef5f964c2b`。原集成存在vLLM接口、FP8 dtype序列化、原始TP权重保存时点、恢复后处理及生命周期/路由事件兼容缺口；原生vLLM支持不等于原生ServerlessLLM已经支持。实际使用适配补丁及完整源码清单，另有Store服务单行SIGINT等待修复。补丁账在 `patches/`，最终实际身份以运行manifest、二进制哈希和当前R1/R2为准，不能用历史candidate名称替代。

Store本体经审查使用本地读取、CUDA Host注册、H2D和CUDA IPC；没有据主机mlx5设备推断Store使用RDMA。NCCL具体NET/IB及可归属MR覆盖仍有限，不宣称平台MR归零，也不把全局波动当作本实验资源释放证明。

## FP8保存、恢复与正确性

[源保存证据](../evidence/fp8/source-preservation.json)覆盖119028个官方源张量：117459个活跃张量，1569个关闭MTP张量单独保存、不装入GPU。官方配置、量化元数据和tokenizer文件有独立哈希。官方量化为动态activation、E4M3、128×128权重块；未量化模块使用官方混合精度，模型配置中的bfloat16不是将FP8权重整体反量化。

[Store磁盘核验](../evidence/fp8/raw-disk-verification.json)逐字节覆盖八rank每rank2560个canonical张量、2864个alias引用，并包含inactive源MTP；别名和对齐padding分别记录。Store保持处理前FP8值、scale、名称、shape、dtype和TP映射，保存发生在vLLM `process_weights_after_loading` 之前；恢复同一处理前状态后执行冻结vLLM相同后处理，避免对已变换权重二次后处理。

[后处理规则与确切源码](../evidence/fp8/postprocess-rules.md)给出W_UV/W_UK_T的split/transpose/permute、实际shape/dtype/stride、显式DeepGEMM及E8M0=0的权重/scale调用契约，并明确helper子分支与隐藏布局的覆盖边界；源文件逐个SHA绑定，不把处理前/后字节直接当作同一布局比较。验证方法是分别从官方Safetensors与Store构建引擎，再对同rank、同阶段的完整逻辑张量逐字节SHA256比较，同时检查shape/stride/dtype。该方法覆盖所有named parameters、buffers和各nn.Module直接tensor属性。每rank5814个权重/scale/派生引用；alias引用会重复计逻辑字节；234个DSA运行时索引引用和156个KV引用单列排除。opaque kernel allocation没有声称覆盖。

[两次真实冷启动参考](../evidence/reference/native-weight-contract.json)及金标已冻结。后续[C17完整G1](../reviews/actual-controls/sllm-glm51fp8-20261026T193424Z-685d6426/OBSERVATIONS.md)覆盖32份rank审计记录、两种格式各两次原生生命周期、12个正确响应和4次删除后观察。它属于非计时正确性证据；C23 weightaudit关闭，沿用经R2明确绑定的数学参考，未把C23业务成功说成新的全量权重审计。

## 组件与完整服务路径

- A2：官方Safetensors通过vLLM原生入口的两次稳定冷启动，是后端参考。
- A3：[整池Store恢复](../evidence/fp8/store-restore-validation.json)只证明组件，记录中的native_service_proven=false保留。
- A4/G1：经ServerlessLLM原生注册、路由、真实请求、删除及再次创建的完整生命周期有独立证据。
- B：C23完整R1→实际独立守护/身份/监控→normal资格验证→R2→正式gate后续跑26项。四条路径均走原生服务入口，未以组件时间冒充端到端。

原生启动指令定义为注册模型前的startup_command_ns；首token是后端事件，尚非客户端网络首字节。首个完整正确业务响应使用自然EOS算术请求，随后翻译请求和独立ignore_eos固定32-token测试。每个响应核验原始token、文本、finish reason与有限logprob。C23共78份请求响应，含26次固定32，其余为自然EOS业务；32-token内容与冻结金标完全一致。

## 四条路径的计时

以下仅为C23同一实际修订的全部成功样本；单位秒，每格“均值 / 中位数 / nearest-rank P90”。Store各7项、Safetensors各6项，不能称为同修订各10次。

| 条件 | n | 权重载入 | 启动到首token | 启动到完整正确业务 |
|---|---:|---|---|---|
| store_disk_cold | 7 | 121.455 / 121.368 / 121.783 | 180.633 / 178.866 / 195.473 | 180.875 / 179.112 / 195.727 |
| store_pool_hit | 7 | 5.819 / 5.780 / 6.103 | 62.713 / 62.375 / 64.660 | 62.936 / 62.607 / 64.861 |
| safetensors_disk_cold | 6 | 391.671 / 391.208 / 400.429 | 448.273 / 447.962 / 457.173 | 448.496 / 448.192 / 457.390 |
| safetensors_disk_hot | 6 | 129.215 / 140.474 / 145.085 | 185.525 / 197.011 / 201.400 | 185.754 / 197.255 / 201.611 |

[完整原始数值、阶段及区间](final-statistics.json)和[独立数值复核](final-statistics-review.json)按node/boot/code/MF/protocol/preparation/window及条件分为16层，不合并C21/C23。原始混合历史每条件10项：池命中8/10、Store冷读10/10、Safetensors冷读10/10、热读7/10；Wilson95在JSON中，这些是历史完成率，不能作为当前修订可靠性估计。

C23完整配对块仅5–10，六块共同重采样10000次（seed20261024、共享draw digest），Store额外block4只保留于描述和成本。区间为收集中补定的探索性、条件性、逐指标名义95%，并非完成原始10块预登记分析或提供四条件同时覆盖。六/七样本P90等于最大值，串行依赖和小样本限制仍在。

权重、进程创建、设备/通信组、量化后处理、KV池、预热与路由事件分开保存；嵌套区间不可相加，进程创建估计及worker复用边界见原始phase记录。C23正式审计覆盖104份缓存闭包、13312份完整mincore向量；19275634976为重复快照页位置，不是独立页数。冷条件逐输入零驻留，热条件逐输入全驻留，池命中有八rank严格ready证据。硬件/控制器、CUDA及编译缓存不声称全部清除；本批冻结JIT基线及后检查另列。

## 初始化、闲置与释放成本

C23公共初始化238.771219657秒；583个JIT seed文件18661429B复制耗时0.16921188秒，原C21编译创建成本保留。host准备开始到最终释放13253.930347667秒为含资格控制、审批等待、正式和清理的墙钟，不代表持续占GPU，不能与其内部区间相加。各次准备均单独计账，不能从业务启动时间中消失。

26次实例删除后各61点、至少60秒观察均通过，包含GPU进程/显存、worker cgroup、CPU/tasks、共享内存和PSS。公共716GiB池和约1769MiB/卡helper仍驻留。完整退出另有root清理与独立61点75.982432416秒观察；当时八卡0MiB且无GPU计算进程，自有tasks/anon/shmem归零，仍保留memory.current758745911296B，其中file756964155392B。普通页缓存和磁盘资产保留，不清全机cache。

本run截止守护清理边界仍为13:50，未随替换预约09:00–17:00延长；实际清理后timer inactive。源码经81文件/MF前后核验原子归档至本run executed-source-f176e05，canonical code/experiment不存在，旧ready/池凭证失效。相关收据与独立审阅在C23包内。

[历史成本盘点](historical-costs.json)及[范围复核](historical-costs-review.json)保留16个此前实际控制outcome、C21/C23成本、下载、A2两次参考、A3组件和八rank导出事件。下载首启动→全验证6094.860556秒，续跑3791.024317秒包含其中。导出派发→最晚预热403.180093388秒，缺少独立save起止，pure_save_s=null。C13原错误峰值0保留并指向776676474880B追加更正。不同boot的monotonic不拼接成总时间。

## 未通过、未知与下一步

- 五个历史正式失败保持原样：热读01因JIT变动；池命中01原始AssertionError信息不足；热读02/03缓存准入失败；池命中03缺router_instance_published，正确算术不足以构成该轮完整成功。原始错误和事件在总账链接中。
- 历史exit137上游来源仍未知，Ray/gRPC修复及后续成功不追认其原因。
- 纯转换时段、早期失败/编译/审批闲置的完整统一资源时间线与资源积分仍未闭合。total_cost=null；不能把已知阶段加成完整总费用或把未知设0。后续优先补查保留的确切原始进程/退出/编译收据，不重跑或替换原正式ID。
- GPU opaque allocations、完整MR归属、全包实际worker导入路径及kernel内存峰值的覆盖有限，保持已核验范围，不根据全局计数补结论。
- 最终报告及全需求验收还需独立复核；远端Git同步因整批证据外发自动审批拒绝而待用户确认。目前本地证据已提交，不声称远端SHA已更新。
