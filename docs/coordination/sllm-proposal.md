# ServerlessLLM：qjhs003 共享资源独占批次提案

版本：`qjhs003-coordination-draft-v1`。状态：**单方草案，未一致，未分配资源，未实现或部署共享锁**。本轮只读核验、方案与互审；不启动 Ray、Store、全量缓存或模型，不修改 Node8 文件或资源。所有时间为北京时间（UTC+8）。

## 1. 当前事实、证据等级与准入

证据索引：[evidence-index.json](evidence-index.json)，新快照为 [resource-snapshot-20261025T175542Z.json](evidence/resource-snapshot-20261025T175542Z.json)，采集时间 **2026-10-26 01:55:41**。仅 SSH `qjhs003`；当前 hostname=qjhs3，machine-id=000c69369afa00000001fc4f0d971280，boot-id=0145ca12-ac6e-40fc-9760-5d0639290553。身份与本实验当前记录吻合，不以 hostname 单独认定节点。

| 事实 | 数值 / 状态 | 等级与边界 |
|---|---|---|
| 主机内存 | MemTotal **1923.283GiB**，MemAvailable **1883.487GiB** | 本次实测；MemAvailable 包含可回收缓存，不是本项目保留额度 |
| GPU | 8×H20-3e，143771MiB/卡，8卡0MiB，无 compute-app PID | 本次实测；不是未来独占承诺，未额外证明所有 NVML graphics-process 接口覆盖 |
| 平台基线 | 全局 Mlocked=229832KiB，Shmem=4464KiB | 本次实测；不能要求平台计数归零，也不能用 VmLck 单独代表 CUDA host registration |
| 其他运行容器 | `miles-train` 运行；`share-mnt` restarting | 本次列表观察，不推断所有者或资源释放；未停止或修改 |
| SLLM 原 run | 无运行容器；实际嵌套 cgroup pids.current=0、anon=0、shmem=0、unevictable=0；memory.current=80875520B，其中 file=74182656B | 本次实测；保留的可回收页缓存/内核记账不冒充活跃 Store 池，也不称零内存成本 |
| 已规划 G1 身份 | 无运行容器，slice inactive，新截止 timer 不存在 | 本次实测；没有新 G1 计算或 guard/probe 通过 |
| 旧守护 | 原 run v6 timer 仍 active，02:50 截止 | 本次实测；不能覆盖其他 run 或续跑窗口，不能清理新 run / Node8 |
| 预约 | boyjiang 01:00–03:00、03:00–09:00；09:00 起另有 Azure 预约 | [本次 API 快照](evidence/booking-snapshot.json)。01–03 ID=bk-20261025T1730-97c53619；03–09 ID=bk-20261025T1538-fe2ffc58。当前提案不借用09:00后的预约 |
| 权限 | SLLM执行UID/GID=2147；组2147、docker(123)、approachingai(57272) | 本次 `id` 实测。Node8实际执行UID/组尚待其确认 |
| 当前审阅 | R1c=`changes_required`，绑定 code b5b48807fe04251777c324d52f9e015c5d05aad5、manifest2feefa1e9d74412084fd71cb5b412f9a9f95d6181ab983d95d5c29550e31eb56 | [实际DeepSeek结论](../../reviews/verdicts/deepseek-r1-control13c-verdict.json)。R1d为未封板本地草稿，未部署；本轮保护其已有改动 |

冻结实验为 `zai-org/GLM-5.1-FP8`，官方revision `f396cf805182f4ca10fa675e1a99815b3ca384db`，TP8、max_model_len2048、单请求、MTP关闭、eager；保留官方FP8/scale及混合精度元数据。金标SHA256 `9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0`，预登记SHA256 `90d4c91bf8b0c9495e0cb95376a55f84d17351308541414d58dd650d8e2dd37c`。

原40个正式ID仍为 **5成功、4失败、31未开始**，下一项 `store_pool_hit_03`；v5=0成功/1失败、v6=0/1、v7=4/1、v8=1/1，不合并为同一v8。金标和冻结官方revision不变。历史exit137仍失败：dockerd stop-timeout/force有机制记录，**上游请求者与原因未知**；Ray修复或协调合同不能解决该因果缺口。

准入缺项：完成新候选整改与CPU回归、commit/manifest绑定及DeepSeek R1复审；当前有效窗口和实际资源再核验；新run独立截止守护/probe/身份/监控实际通过；有界G1实际证据；必要normal-flags证据及R2完整gate/派生配置准入。**共享合同一致只增加申请资格，不替代任何实验gate。**

## 2. 已存在磁盘资产，不重复下载或预留第二套

本次 `findmnt/df/stat`：`/data/brick1` 为 **/dev/nvme1n1，本地XFS**，可用2172208365568B，约2.172TB；不是NFS。现有根目录为 `/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57`。

| 已有资产 | 本次元数据实测 | 计费/预算解释 |
|---|---|---|
| checkpoint/142官方shard | logical=756177645648B（704.245GiB）；allocated=756177850368B | 已占盘，不再次从“可用空间”扣一份；配置/索引等另有小额开销 |
| Store/8rank，80个tensor.data部件 | logical=762037043200B（709.702GiB）；allocated=762037534720B | 已占盘，非额外716GiB物理内存；80文件与142文件 inode 去重后无这两组重复扣算 |
| 两组载荷合计 | logical约1.518TB，1413.948GiB | 不含环境、日志与其他用户资产；不宣称是整个目录的完整du或全payload新hash |
| native-home及新证据 | 历史准备缓存约18.9MB，复制上限8GiB；监控文件上限4GiB | 前者为历史值/配置上限，后者非本次实测增量。新证据占盘须单独登记；本轮没有模型/Store复制 |

本次仅固定142+80文件 stat，不读模型内容，不做dd/fio、du全盘扫描或吞吐测量。**当前读写性能未知**；已有实测启动时间不能直接当磁盘带宽。性能探针若需要，须在之后的独占非计时准备批次单独审批，不在对方正式轮次运行。

## 3. 各阶段资源矩阵

单位GiB=2^30B。C=配置上限/代码设定，H=历史实测，E=估计/提案，U=未知。当前没有新负载峰值实测。配置上限不能相加后冒充峰值；子容器1700GiB同时受整个run的1700GiB slice上限约束。

| 阶段 | GPU | 主机内存、锁页、共享内存 | CPU、磁盘I/O和网络 | 持续时间及证据 |
|---|---|---|---|---|
| 静态轻量准备 | 0模型GPU分配（E边界）；只读NVML查询 | 本地优先；003元数据探针建议≤1CPU、≤1GiB RSS、单次≤60s（E，尚非实现限额），不预留大池/大文件cache | 小型清单/配置/哈希、有限stat；不全量权重hash、不压缩/复制大数据、不做导出/编译/网络灌流。对方计时时连这些003准备也移回本地 | 离线可并行，无资源持有权。没有用当前无OOM来推论安全并行 |
| Ray-only控制 | 广告8GPU、无模型/推理（C）；仍须八卡空闲 | 无716GiB池；head8GiB+worker8GiB+monitor2GiB上限（C）；每容器/dev/shm32GiB容量上限（C，非预分配），Ray object store每角色256MiB（C）；实际RSS/PSS/U | head4+worker4+monitor1核quota（C），两observer各≤1核（C）；Ray逻辑num_cpus=16不是额外物理16核。无权重磁盘流；同机bridge TCP控制面 | 每次330s外层设定、2次；另有观察/清理。R1c总预算一致性有缺陷，不能称当前已受审硬界 |
| 真实Store建池 | 不加载模型；CUDA设备/注册辅助显存峰值U | **716GiB** pool（C+H），1432×512MiB；aligned_alloc+cudaHostRegister（源码）；预期大额不可回收驻留。不是必须在VmLck中体现为716GiB。service1700GiB+head8GiB+mon2GiB子限额（C），整个slice1700GiB（C）；host实际峰值U | service32核、head4、monitor1、observer2，共约39核quota（C），另有host辅助开销U；池分配本身不读全权重。gRPC8073；无Store verbs调用证据 | pool-ready wait1800s（C，超时不等于成功）；历史近完成失败样本Store RSS约670GiB（H，非当前成功峰值） |
| Store池命中 | 独占8卡、TP8；每卡容量140.401GiB（实测硬件上限），实际峰值U | 716GiB池保持，模型有效载荷在池内，不能再加709.7GiB当额外匿名副本。checkpoint/Store残留页缓存与临时buffer另记（U）；GPU权重/KV/通信/重排另计，不当主机RAM | 先确认8rank全在池；当前协议仍有cache检查和metadata I/O，不能声称零磁盘I/O。本地CUDA IPC/H2D、Ray/HTTP/gRPC；NCCL实际transport待取证 | 1200s/正式ID（冻结配置，含准备与60s观察），不是纯加载时长 |
| Store本地冷读 | 同上 | 同一716GiB公共池不撤；数据读入池内。O_DIRECT成功时载荷页缓存预期较少；源码有fallback到buffered IO，实际命中路径与中间副本峰值U | 读取约709.7GiB Store载荷（E一遍量级；不保证只一遍），本地NVMe；全输入mincore冷判据、metadata、辅助检查另计。允许fallback不等于缓存条件自动通过 | 同上；失败/超时仍消耗该正式ID |
| Safetensors本地冷读 | 同上，官方FP8不改 | **公共716GiB池仍常驻**，即便此条件不从池取权重；约704.245GiB源文件可在读取中进入页缓存（E上界量级），非另加一份相同“源权重RAM”；CPU中间副本/解析峰值U | 读取官方142shard+6实际loader元数据；与Store路径争用同一NVMe/CPU/memory fabric，不允许Node8同时跑Store | 同上；不能因省内存删公共池或改TP |
| Safetensors本地热读 | 同上 | 公共716GiB池+约704.245GiB源页缓存同时驻留，约1420.245GiB（E最低量级）+服务/临时副本。预留64GiB工作余量是G1设定（C），不是已测峰值；另一资产缓存须实测/记录 | 全量暖读、完整mincore、最多2修复、8线程×8MiB buffer（C，约64MiB读缓冲，不是全模型副本）；helper总600s（C）。I/O全在准备/成本账 | 不放宽100%驻留阈值，不用mlock冒充普通hot，不以pgsteal≈0准入 |
| 单实例删除后观察 | 模型GPU应回收；辅助常驻显存实测U | **716GiB池与基础设施仍留存**，不算可交给Node8；缓存仍可能驻留。RSS/PSS、CUDA注册、shmem/CPU须继续记录 | 持续监控60s；可能还有析构、后台读取/网络，须实际核验，不把/delete返回等同退出 | 原协议60s不能缩短；观察失败留在正式失败分母 |
| 整个批次退出 | 停自己的模型/Store/Ray/monitor/aux后核验八卡 | 归属匿名内存、注册池和shmem释放；普通可回收cache保留并报告；不要求全局Mlocked/MR/内存归零 | 自己的读权重、导出、copy线程、网络/MR/端口/挂载须结束；磁盘资产保留 | 另有完整退出观察和取证；最大耗时尚需受审总批次封装保证，不能把原实例60s当全部交接 |

**不能并行的大额资源依据**：SLLM池716+热cache704.245+Node8暂报1024≈2444.245GiB，已超过本机1923.283GiB，尚未计任何服务开销。Node8的1024GiB仍是对方待核验历史预算，不是最终值。即使SLLM不在热条件，716+1024=1740GiB，也不能据剩余约183GiB就批准并行：source cache、临时副本、NUMA、实际锁页与峰值尚不闭合。因此采用**并行轻量准备，完整批次错峰，凭归属证据交接**。

页缓存必须与活跃池区分：Store内部有效数据占用716GiB池的一部分；磁盘上709.7GiB归档不是另一份必驻RAM；mmap虚拟地址不是再一份RSS。另一方面，残留Store文件缓存、Safetensors缓存、中间buffer、GPU副本可能同时存在，不能只算716GiB。入场需按受审预算验证整体/NUMA可用量与两组完整mincore，记录页归属与回收限制，不清全机cache。

## 4. RDMA与链路结论

[Store pool源码](../../controls/candidate13c/evidence/runtime/source-v7/sllm_store/csrc/sllm_store/pinned_memory_pool.cpp)使用cudaHostRegister/Unregister；[model.cpp](../../controls/candidate13c/evidence/runtime/source-v7/sllm_store/csrc/sllm_store/model.cpp)使用本地pread、O_DIRECT及fallback、cudaMemcpyHostToDevice；checkpoint_store.cpp使用cudaIpcOpenMemHandle。当前审查范围内无Store ibv/MR/远端RDMA传输调用，**不能把这个Store叫作RDMA Store**。主机有5个ACTIVE mlx5链路只是硬件状态，不是本实验实际注册MR的证据。

TP8的NCCL transport是否打开NET/IB、是否另有注册资源：**未知，不能据单机或GPU空闲断言没有**。后续实际批次须记录NCCL transport、own PID设备FD/注册上下文及可归属MR；确认未用时才登记N/A。不得为协调擅改NCCL/禁用IB或改变测量条件。Node8实际RDMA、MR/PD/helper及000/001关联流量须由其方案举证；SLLM不SSH其他节点。

## 5. 批次、最长持有与安全停止点（均为待双方/实验审阅的提案）

| 候选批次 | 精确范围 | 提议最长持有（不是现有保证） |
|---|---|---|
| SLLM-G1-control | 两次Ray-only、一次真实池、一次独立完整hot控制、两格式各两生命周期/8rank校验/每次60s、全部退出取证 | **90分钟**。330×2+3420=4080s基础控制设定，加观察/启动/退出/取证后提议封顶5400s；当前代码尚未证明整个批次封装有此上限，须R1复审与独立batch守护 |
| SLLM-normal-control | 若R2要求，当前normal-flags有限控制单独登记 | 暂提90分钟；精确范围和预算未冻结，不能隐藏并入G1或正式ID |
| SLLM-formal-first | 如总账仍不变，仅完成历史block3剩余三个连续ID：store_pool_hit_03→safetensors_disk_cold_03→store_disk_cold_03；hot_03历史失败保持 | **120分钟**提案：初始化≤2400s（待审预算）+3×1200s+额外60s观察保留+600s退出取证=6660s，预留540s。不是承诺初始化能在2400s完成 |
| 后续SLLM-formal-block | 以原预登记下一完整4-ID block为一批；次序以原清单为准 | **150分钟**提案：2400+4×1200+60+600=7860s，预留1140s。若受审实际预算不闭合，不申请；不得临时扩时/改ID或减池 |

这些外层总预算是**协调提议，不批准改变1200s attempt或600s helper原协议**。1200s内已含原60s删除观察；表中额外60s是保守窗口保留，不当两次成本重复相加。实际wall成本按重叠时间线记。释放600s和初始化2400s尚未实测为硬界；新封装/guard必须覆盖所有host采集者及子进程，失败也须有截止取证。

每次启动前：剩余时间≥该批受审完整最大预算+60s观察保留+清理取证余量，并满足批次预定终点、预约终点和独立守护中最早者。表格总数已含的项不再在资源成本中重复算；每个正式ID还需再次核验剩余预算。不使用08:40固定时刻替代判断。当前窗口最迟提议08:50完成清理，不借09:00之后预约。

正常交接只在完整预定批次退出后。故障/超时按原停止规则结束为failed/partial，保留终态ID与未开始尾部；不在推理/权重读取中为公平切换任意打断。安全点：未分配下一ID、所有当前测量/60s观察和原始落盘完成后停止自己基础设施。不能“实例删了但池还在”就交接。

G1结束必须退出大池再把资源交还；R2和文档审阅在本地做，不占716GiB等审批。下一批重新建同配置公共池，收取初始化/空闲成本，**旧pool-ready/读回/normal flags运行凭证全部失效**。磁盘资产可按哈希复用。跨批重建的等价性须R2审阅，不能自动合并历史cohort或改缓存条件。

公平规则和单一互斥机制见[共享合同草案](shared-contract-proposal.md)。当前Node8预算和完整批次尚未收到，不承诺两方已达成90/120/150分钟安排。

## 6. 轻量准备与退出边界

另一方计时时，SLLM只在本地Windows进行编辑、测试、审阅、Git；不在003压缩、编译、复制、全payload扫描、全量cache读取、网络传输或加载。少量紧急状态查询也需避免干扰并登记，不能将其称作零开销。当前只读探针不是节点独占许可。

交出：停止自己的全部模型、Store/Ray、辅助线程/导出/IO、monitor/observer；取得持续60s退出证据和归属记录，root/进程取证后提交manifest+hash，进入待核验。接手方独立复核后才交接。具体判据见合同，不停止平台或Node8，不删除checkpoint/Store。若可归属证据不足，则异常/待核验，不宣布释放。

## 7. 需要Node8确认及拟改文件

1. 003执行UID/GID/补充组、协调目录访问方式；是否与SLLM同UID，是否接受其不能提供OS级项目隔离的边界。
2. 003真实Store分片/总池、容器与祖先上限、实际峰值匿名/锁页/缓存/共享内存/CPU、GPU需求；1024GiB是否仍成立。
3. RDMA MR/PD/context/后台helper归属，000/001关联进程与共享IO的完整退出证据。不要只发003空闲截图。
4. 一个完整控制/正式批次的准确范围、最长预算、现有守护与退出耗时；能否接受90/120/150分钟槽，不能则提交完整预算而非截断试验。
5. 是否接受单一XFS命名空间、单一实现维护者、状态语义、接手复核和无自动抢占；公平顺序见合同。
6. 审阅本三文档的准确commit与证据hash，回复逐条同意/修改/阻断，最多两轮集中收敛。

本轮拟修改仅三份提案、只读证据及索引、STATUS。Node8提案通过用户转交后才新增review-of-node8.md；当前**未收到其正式提案，未互审、未一致**。本轮不修改运行代码，不实现锁，不部署目录/组/ACL，不触碰已有candidate13d。后续可能修改的协调单一实现、各自申请/释放/核验adapter、G1总预算/guard、R2 gate均属于下一阶段，需另行最小实现计划与故障测试。
