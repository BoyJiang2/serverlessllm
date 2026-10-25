# qjhs003 共享资源交接合同草案

协议提案：`qjhs003-coordination-draft-v1`。**SLLM单方草案；Node8尚未确认；未生效、未分配资源、未实现或部署锁。** 只有双方对同一版本/文件hash及权限/预算/维护者均明确接受后，才能提出下一阶段实现计划。本轮不直接联系其他任务，通过文件及用户转交互审。

## C01 范围与不可变条件

采用“并行轻量准备，按完整实验批次错峰运行，凭归属明确的释放证据交接”。重型批次对003的主机/NUMA内存、活跃锁页、CPU、共享内存、磁盘IO、网络及关联RDMA流量互斥；SLLM另要求八卡独占。不是只锁GPU。静态磁盘资产不归临时持有者独占或处置。

合同不授予预约、抢占、维护、杀对方进程、清平台MR/页缓存、改全局参数/权限或访问新节点的权力。SLLM唯一远端入口qjhs003；Node8的000/001关联信息由Node8在自己的授权范围采集并出具，不由SLLM跨节点核验。

保持两方各自模型、规模、TP、金标、正式ID、失败分母、缓存语义及公共基础设施。合同不批准缩小SLLM池或撤掉Safetensors条件的公共池。协调批准不替代SLLM R1→G1→R2→正式准入，亦不替代Node8自己的gate或DeepSeek审阅。

## C02 唯一目录、唯一实现、权限（未创建）

建议唯一规范目录：`/data/brick1/qjhs003-exp-coord/v1/`，使用已实测的本地XFS `/dev/nvme1n1`。禁止各自另建第二个“等价锁”、NFS备用锁或绕过入口。安装后双方须核验同一规范路径、挂载源/FS身份、lock文件device+inode、协议和实现SHA；任何不一致拒绝运行。重启后inode/锁文件变更不是自动放行理由。

提议由**SLLM主agent作为唯一实现维护者**，Node8只读审阅、各自维护client adapter；这是待Node8确认的分工，不是已获维护权限。唯一源码拟在SLLM仓库`tools/coordination/qjhs003_coordinator.py`维护，双方只使用同一审核SHA的安装物，禁止私改分叉。上述文件本轮不创建。

提议最小机制：**单一协调进程 + 一个稳定inode上的Linux flock排除第二协调进程 + 持久状态/append journal + 本地Unix socket API**。协调进程单写全局状态，双方client仅发请求并写自己的记录。此选择避免两个client各写一套状态，也避免控制器崩溃释放flock就被当作资源已释放。不是跨节点调度器，不控制实验进程，不自动杀进程。

建议权限布局，须实际UID确认、管理员分配后再实施：

| 项 | 提议所有者/模式 | 说明 |
|---|---|---|
| `/data/brick1/qjhs003-exp-coord`与`v1` | root:专用协调组，0750 | 现/data/brick1为root:root755，UID2147不可自行创建；不使用777，不更改现boyjiang700目录权限 |
| `v1/coordinator.lock` | 指定协调UID:专用组，0640；父目录不可由client写 | 提前创建，协调实现打开现有inode，不unlink/recreate/truncate；唯一daemon持有flock，client不直接取得/释放此FD |
| `runtime/` | 协调UID，0700 | 状态journal、release capability、协调进程身份；单写者，private内容不进Git |
| Unix socket | 协调UID:专用组，0660 | SO_PEERCRED校验实际执行UID；不监听TCP，不用共享全局口令 |
| `records/sllm/`、`records/node8/` | 各自实际UID:专用组，0750；证据文件0640 | 只写本方申请/释放/复核记录，另方只读；全局state引用hash，不互改实验源码/Git或对方记录 |
| 私有client capability | 本方0600或仅内存 | 不放公开申请/证据/日志/Git；公开holding_id不是释放凭据 |

当前仅证实SLLM UID/GID2147及docker/approachingai组；**Node8 UID/GID/组未知**，不能假定现有组适合共享。双方同UID时Unix权限不能提供项目级安全隔离，须明确采用可信协作模型：防误操作靠单一API、项目/holding_id/capability及日志校验，不能声称能抵御同UID恶意读写/调试。若双方要求OS级隔离，须另行管理员分配独立UID/受控broker权限；未确定前不部署。

协调进程拟有独立身份与小额限额（≤128MiB、≤0.1CPU为估计目标，未测试，不当现有配置），成本列为共享基础设施，不能隐藏在“零开销锁”中；不放进任一实验的待杀slice。确切权限和限额需下一阶段实测/审阅。不会因协调进程存在而要求平台其他服务退出。

## C03 原子申请、持有标识与记录

申请资格：本方代码/审阅已完成到拟申请批次要求；预算和完整清理有界；拥有明确有效预约/用户授权；不占大额资源等审批。资格不等于取得整机。

单一协调进程在持有唯一flock且已恢复持久状态后串行处理申请。原子grant执行“核验当前可释放状态及预期generation→持久写入新holding_id/epoch与HELD状态→fsync日志/文件及目录→返回grant”。写入或回复不确定时不得计算；重查同一request_id，不重复创建第二持有者。状态文件用原子替换，journal保留前后hash/顺序号。不得先返回grant再持久化。

公开`holding_id`为每次新授予的唯一随机标识；`coordinator_epoch`绑定boot+本次协调进程出生标识，`generation`单调推进。私有release capability仅发给该owner会话。API每次核对项目、实际UID、run_id、holding_id、generation及capability，错误持有者/旧token不能释放或改变当前lease。关闭任意client自己的FD不释放daemon的flock。明确这些是下一阶段待测试语义，不是假称已有实现。

grant之后、任何Ray/大池/cache/模型开始之前，本方**再次独立检查**实际资源、有效窗口、当前时间和自己的guard/probe；记录复核时间/hash及holding_id。检查失败则不启动；以零工作/实际已创建辅助项的真实证据走退出流程，不悄悄丢弃grant。启动清单和派生配置必须与该grant绑定，runtime凭证不得跨holding_id复用。

每份申请/持有记录至少包含：

- protocol version+contract SHA、节点machine-id/boot-id、resource_set（003主机/NUMA/IO/网络/RDMA；SLLM八卡UUID）；Node8另列linked_resources/flows覆盖000/001。
- experiment=`sllm`或`node8`、owner、实际UID/GID、run_id、request_id、holding_id、generation、coordinator epoch、用途control/formal。
- 有效预约ID/授权来源与起止、不可超过的截止、batch最大时长、预计释放时间、自己deadline guard脚本/unit/probe/hash。
- 心跳UTC+同boot monotonic、当前阶段、code commit、review/gate/manifest SHA、精确批次范围/正式ID列表、启动清单及派生约束。
- 每个完整CID、namespace、宿主PID/NSpid、UID、`/proc/PID/stat` start_ticks、完整cgroup路径/可用cgroup-id、boot；角色/父子关系/端口/挂载/设备FD/已确认MR归属。
- 更新清单的不可变版本/hash及最后时间；动态actor必须补登记，不能仅记主容器。
- 本方释放证据manifest位置/hash/大小/采集区间；接手方自己写的独立复核记录/hash；unknown和覆盖缺口明确保留。

## C04 六种状态与交接顺序

| 状态 | 语义与允许动作 |
|---|---|
| 申请 REQUESTED | 队列中、未分配；只能本地/不争用轻量准备，不能建池或预热 |
| 持有 HELD | 原子grant已持久化；本方复核和guard通过后可运行已审批次，另一方不启动重型负载 |
| 释放中 RELEASING | 本方停止自己的模型/Store/相关IO/辅助进程、取证；仍占用lease，不能让下一方启动 |
| 待核验 PENDING_VERIFY | 本方证据已提交且不可变；仍禁止新负载；等待接手方独立复核，不因锁FD消失/时间已到视为释放 |
| 已释放 RELEASED | 接手方明确确认本方资源退出证据且协调进程持久登记；无现持有者。下一申请仍需原子grant及自己的实际准入 |
| 异常 ANOMALY | 过期、失联、身份漂移、部分释放、证据缺失、SSH超时或持久状态不一致；冻结grant，不自动抢锁或清理对方 |

正常顺序：REQUESTED→HELD→RELEASING→PENDING_VERIFY→RELEASED→下一HELD。先实际清理、取证、复核，最后清除旧持有权；绝不“先放锁后清理”。状态转换仅由唯一实现执行，双方各自保留输入和回执，不伪造对方签收。

接手记录须区分`owner_release_verified`与`incoming_admission_passed`：例如Node8不用003GPU且其自身资源确已退出，SLLM不得要求Node8杀平台GPU进程；可确认Node8释放，但SLLM自己的八卡准入仍失败，不能取得可运行许可。对方释放成功不保证当前整机满足下一方条件。

## C05 失联、残留、重启与恢复

心跳建议30s，连续90s无新鲜心跳进入ANOMALY（提案值）；超过batch截止、SSH查询失败同样异常。**不把TTL过期变成RELEASED，不自动抢占，不自动杀owner。**

- 实验控制器退出而子进程仍活：daemon即使仍活也保持HELD/ANOMALY，独立guard只清该owner登记资源。持有者必须覆盖容器及非容器子进程，不能只看父PID。
- 协调进程/机器重启或flock意外丢失：下一协调进程取得同一inode只能进入恢复核验；从durable latch/journal重建旧owner，先ANOMALY。锁空并不等于节点空，首次初始化也需双方基线复核，不能以新建state文件绕过残留。
- PID复用：boot+PID+start_ticks+UID+cgroup共同匹配，CID使用完整ID。仅PID相同/不同或原容器名复用不形成退出证据。boot变化使旧grant/token/运行证据失效；保留历史，重新核验，不继承ready PASS。
- 部分释放/后台IO仍活/归属不清：继续RELEASING或ANOMALY，不grant。不能用全局内存/MR下降替代own退出。
- owner死亡但guard已经清完：仍不得伪造owner回执。提交可验证guard/进程证据，接手方复核；如缺本方必要记录或角色不清，交用户/管理员作具名恢复裁定并追加journal，不能提供无人审核force-unlock捷径。
- state/lock inode/协议/时钟异常：冻结grant，保留原始文件和最后有效hash；不在故障时创建第二命名空间。

guard与锁职责分离：锁只分配批次；guard独立于双方控制器、能按实际授权/预定batch最早截止清本实验资源。协调服务没有停止对方的权限。旧SLLM v6仅原run02:50；新run08:50守护未安装，未来还须批次最长持有边界，不能因共用owner账号借用旧guard。guard续期须新受审overlay/安装验证，不能编辑冻结旧时间或无缝留下无守护进程。

## C06 交出/接手共同验收

本方在取得资源前登记零点/平台基线及own身份；交出时逐项给采集时间和归属证据。共同验收是“own新增活跃资源结束+独立复核”，不是全机所有计数归零。

| 类别 | 本方交出证据 | 接手方复核与限制 |
|---|---|---|
| GPU | 本方CID/PID/start_ticks/UUID对应NVML compute及可用graphics进程、逐卡显存，释放观察 | SLLM独立要求8卡满足自身排他准入；Node8不用003GPU时只证明它自己的资源，不清别人的GPU |
| 匿名/锁页/共享内存 | own cgroup/pids、memory.current/stat/events、每PID RSS/PSS/VmLck/线程、CUDA注册池/共享段与设备FD生命周期 | CUDA注册不保证反映在VmLck；零pids/注册owner关闭与匿名/shmem回收等需联合。全局Mlocked≈224MiB等平台基线允许保留；cache/kernel记账单列 |
| Store/CPU/helper | 真实Store/Ray/监控/observer/导出/权重读取进程均结束；包含宿主非容器任务、子线程 | 全CID+boot/PID出生/cgroup核验，不能只凭docker ps无名项。协调daemon明确作为共享服务记成本，不伪装owner模型 |
| RDMA | 实际使用时记录可归属MR/PD/context、设备/owner进程、注册/关闭及helper证据；未用给source+runtime依据 | 不要求全局MR归零，不泄露rkey等敏感私有材料。全局计数变化不是owner证明；归属未知=证据不足。SLLM Store的CUDA IPC不自动等于RDMA；NCCL待实证 |
| IO/网络 | own /proc/io、FD、线程/队列、容器stats、连接/传输/导出结束；对可用own计数给连续观察 | 建议own CPU/读写/网络至少10s无新增后台权重流（待冻结阈值，不以全局≈0准入）。无owner就绪记录或活跃异步任务不能交接 |
| 端口/挂载 | 自己namespace/监听socket/inode/容器bind mounts退出；持久数据盘/静态目录列保留 | 不能卸共享XFS/NFS、停止平台端口；无权限看某字段则标unknown，不当0 |
| Node8关联资源 | 003及000/001本次关联进程、Store/MR/helper/共享IO停止或证明与交接无争用，并给同时间窗证据hash | SLLM只读Node8提交材料，不跨节点执行。仅003空闲不满足关联流量验收 |

SLLM每次原生实例删除后**原60s观察不变**；此时公共716GiB池可能仍在，不能交接。整个批次再完整退出并提供持续60s的退出证据/资源归属（实际新实现与R1仍待审核）。Node8自己定义并验证观察区间；不能套用SLLM60s代替Node8验收。共同10s静稳建议也不能取代任一更严格的观察。

普通可回收页缓存与活跃锁页池分开：保留checkpoint/Store磁盘资产及允许残留普通cache，不清全机cache。接手方按自己的受审输入清单/mincore处理本方缓存，记录影响与成本。若另一方仍后台读相同盘、网络或源节点，则未形成可测的独占环境；合法平台干扰只能等待/报告，不抢占。

owner提交release manifest后，其hash固定；后续补充只追加新版本，不能覆盖。原始大日志留本方专属盘，小型索引/摘要/hash可入Git。接手方写自己的verify记录，引用精确owner manifest hash、holding_id、boot、采集区间和pass/fail/unknown理由。缺项不签收。资源未释放前不能先关闭公共gate再删除证据。

## C07 完整批次与公平性

各批在申请前冻结用途、连续ID/控制范围、最大初始化/测量/观察/清理预算、截止和退出证据。计时中不做Git、审阅、编译、下载、压缩或大扫描；另一方准备移本地，不用别的003容器绕过共享入口。

初始顺序提案：按**双方准入已完成的申请到达序号**FIFO；同一仲裁轮同时就绪时，首次tie给予**Node8**，随后两方有就绪申请时严格交替。只有一方就绪可给其一批，不预占未来连续批；对方在当前批内就绪，下一批优先对方。记录ready_at/grant_at/release_verified_at、等待与实际持有时长，不能靠提前提交未准入申请插队。

SLLM提议槽：G1/control≤90min，历史block3剩余3ID formal≤120min，后续一完整4-ID block≤150min，详细保守分项见[sllm-proposal.md](sllm-proposal.md)。Node8须提出自己的完整批次预算；建议也在对应90/120/150min级别内，但**不强迫截断其完整试验来凑槽**。不接受时先集中协商最长持有/顺序，分歧由用户裁定，未一致不运行。槽不是已有授权，也不得超过真实预约或guard终点。

正式SLLM不逐个ID交给对方。正常按预定连续批次结束；故障导致partial/failed则立即按原停止条件退出，保留已消耗ID和未开始尾部，不补跑终态ID。续批重新申请；不得借失败重排/换ID。双方不得在已持有中临时加一批或延长截止。

最大预算表达：`T_work_max(含既有attempt内部60s) + T_extra_observation_reserve(60s) + T_release_forensics_max`，加必要初始化/控制开销，所有项在具体批次清单明示，入场剩余窗口须覆盖总和；表格已包含的项不重复计费。每次下一ID仍单独检查完整最大预算。不足则不分配该ID并退出，不用固定08:40代替判断。批次硬上限与安全停止点必须在后续源码/guard中兑现，否则只停留为草案。

公共池重建、缓存准备、等待、失败初始化、控制、闲置、退出分别记账。退出使旧运行态读回/ready/comm资源证明失效；后续只能复用核验过的磁盘资产。跨批公共池重建与cohort等价性由实验R2审，不用合同追认历史失败或将v5-v8混合成一个条件。

## C08 一致状态、互审与下一阶段门槛

当前：仅共同原则由用户明确；**没有对端提案/确认，不宣称双方已一致**。待Node8确认：真实UID/权限、预算和关联资源；规范目录/唯一实现维护者；状态/持久恢复/无自动抢占；观察与MR/IO归属；batch长度/FIFO和tie顺序。

三文档经用户转交给Node8主agent，其意见只写对方自己的review；SLLM收到Node8正式提案后只读并写本仓库review-of-node8.md。最多两轮集中收敛，结果分“已一致/仍分歧/需用户裁定”，互相接受必须绑定准确版本/commit/hash。未经双方一致，不实现、不部署、不启动共享003重负载。

一致后另提最小实现计划：唯一coordinator源码和测试、权限预检/管理员安装清单、双方申请/启动复核/释放/接手adapter、独立guard和归属采集、重启恢复流程。必要故障测试：同时申请仅一grant；错误/旧holding_id或capability释放被拒；owner/daemon失联；FD锁空而子进程仍活；PID复用/boot变化；部分释放/缺证据/IO仍活；错误namespace；持久写入或SSH失败；先放锁后清理被拒。先本地/小型无模型测试，再受审部署探针，不能直接用真实716GiB池试锁。

本轮拟改仅docs/coordination及STATUS。实现、权限、部署、实验执行均属下一阶段；本合同未给出任何可跳过实验审阅的运行命令。
