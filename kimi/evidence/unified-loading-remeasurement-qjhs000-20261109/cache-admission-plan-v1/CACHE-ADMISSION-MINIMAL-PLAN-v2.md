# Kimi 图5缓存准入：最小方案 v1（只读交付）

状态：源码仍为91fb2a10481b44c9d50a2cd963ecf0ac0d250c80；未改运行代码、未启动GPU/容器/Store、未重新读取权重。以下是设计提案，实际命令尚未实现、冻结或获准执行。原40项38合格/2失败、8完整块、已接受L1、全部历史失败和编号保持不变。

| 项目 | 已核验与最小方案 | 尚需通过的门槛 |
|---|---|---|
| 实际阻塞 | q0全部595204634151字节读完，但最终98275914/145313657页驻留；早读21分片不足。回收主体、NUMA/并发压力均未定位。 | 不降低all-hot标准，不用单次mincore或后验全局内存推断原因。 |
| SF真实依赖 | /register→Controller→StoreManager→八rank RegisterModel仍需活Store。Model.Initialize只access/stat分区；实际FCFS、memory_pool=[]、显式safetensors路径无Store模型缓冲区借用。DefaultLoader完成模型构造/权重赋值/必要量化布局；delete不走Store数据恢复。 | 见dependency-audit-v1.json的逐源码行号/SHA及实际95d ELF27个source pins；小池尚无运行通过。保留现成Store目录/元数据，缺失必须停，不能触发下载/转换。 |
| 条件资源 | SF冷/热候选64MiB池、64MiB chunk=1块；Store冷/池命中保留640GiB、同chunk。8rank payload按块取整需要9200块=575GiB，不能统一缩成控制池。 | SF64MiB由正容量/块对齐/零数据borrower推导，并非已验证最低运行阈值或缓存修复。若预取、storage-aware或serverless loader误启必须fail closed。 |
| 推荐诊断 | 复用现有native controller，在与未来SF64MiB相同head/worker/Ray/API/Store状态下观察缓存，停在register之前；不创建模型、不推理。小Store仍创建CUDA context/stream并cudaHostRegister，必须如实申请该资源。 | 不称无GPU纯CPU；先实现/测试/独审/发布并冻结实际命令，再另批一次。其通过不证明完整SF生命周期或L1。 |
| 可选D0 | 完全不启动Store/容器/GPU，只有CPU一次读+驻留观察；2400秒。CPUQuota4核、MemoryMax768GiB、High∞、SwapMax0；检查有效祖先剩余额度≥文件大小+64GiB、MemAvailable≥896GiB。 | 不是必跑前置；通过仅证明无大池时采样全热，不能替代正式SF小池/原640GiB资源状态验证。不自动读两遍595GB。 |
| 单次缓存协议 | 全量读一次，read cap1800秒；在实际producer read_end锚定0/30/60/90/120秒做5次全84文件mincore，每扫cap30秒；每文件resident==pages且身份未变才合格。任一失败sticky，后续仅按原计划只读观测，不rewarm/延时到通过。 | 记录实际读首/末时刻、逐文件查询区间；五次扫描不是原子全表或连续驻留证明，也不保证后续加载期间不回收。晚到/超时/缺测不能补0或补猜时间。 |
| 状态与观测 | 每次含wall+monotonic+boot/clock identity；每NUMA node meminfo/vmstat/允许节点；reader/controller/Store cgroup及所有祖先的max/high/current/numa_stat/events/stat/pressure与回收计数；Store配置/进程/CID/内存池状态；并发CPU/GPU/磁盘活动只读快照。 | 前/读中/读完/各固定复查点/终态均保留时间与计数差值。缺权限/有效限制不明，读前拒绝。并发状态不能冒称已隔离；不改NUMA/系统参数、不停他人任务。 |
| 匹配状态预算 | 沿用单case7200秒及现独立guard；阶段上限拟[300,300,1200,120,2400,0,0,0,720,180]，总5220。移除register/infer/delete相关阶段，缓存部分增加窗口；仍保留Store正常关停60秒及≥60秒释放。 | 最终实际命令必须复验原GO/ActiveEnter/尾槽断言，5220加总不是运行保证。缓存失败可作为诊断FAIL正常收尾，不能伪造gold/rank/正式PASS；技术/身份/guard错误仍严格停止。 |
| 图5 | weight_load_host_all_rank_span_s=(max(rank_end_ns)-min(rank_begin_ns))/1e9。 | 缓存/池初始化不计入加载端点；缺8rank身份/端点/时钟即null。新配置/q0RAID单列，旧结果不重标。 |

## 拟改现有文件（本次尚未修改）

- sllm_store/tests/prepare_kimi_four_condition.py及python/test_prepare_kimi_four_condition.py：仅在新的显式profile中绑定SF64MiB/Store640GiB，实际Store argv、root配置断言、store_config role三处一致；增加复用现有路径的cache-only诊断分支与真实消费者负例，不触历史分支/金标/loader/ELF/guard。
- sllm_store/tests/file_cache_state.py及python/test_file_cache_state.py：补真实producer读起止/字节计数、查询区间及有界观测所需小字段，复用现有warm/query和身份校验。不得增加循环rewarm、自动延时、mlock、全局dropcache或新部署框架。

## 测试与通过标准

1. mock实际SF控制链：8次元数据注册，无load_into_cpu/load_into_gpu/prefetch；Store条件仍按640GiB。验证CLI64MB精确解析为67108864，chunk不变、1块记账。
2. 实际生成器/消费者验证新profile argv/配置/节点/UUID/挂载一致；旧q3/q0正式分支不变。cache-only分支绝不调用/register、infer、delete模型或端点/gold验收，不伪造8rank通过。
3. 仅小型临时文件注入短读、身份漂移、部分驻留、晚到、读取/查询超时、祖先额度不足、缺遥测。失败不可被后续全热覆盖；全量read恰好一次，观察时点固定，所有结果有真实时戳。
4. 诊断分别记录观察是否完整、缓存是否合格、技术终态、正常退出及清理；正常/强杀分开。CPU任务结束只关闭自有FD和进程，不强制驱逐595GB页缓存。匹配状态保留现有独立守护、正常停服及完整释放。
5. 新源码/实际命令测试和独审→显式stage/commit/push→远端SHA核验→新鲜资源/完整预算→另行确认本次新诊断授权。旧一次q0运行授权已使用，预约续期不自动授权新权重读或CUDA诊断。当前不申请运行批准，因为实际命令尚未完成。

可追溯附件：dependency-audit-v1.json/md；store-capacity-arithmetic-readonly-v1.json；stopped-worker-vllm-loader-source-readonly-v1/RUNTIME-SOURCE-PROVENANCE-v2.json；cache-only-595GB-validation-readonly-protocol-draft-v1.json/md及design-addendum；匹配状态提案另列。原q0失败和65.95秒追加释放证据原样保留。

## 准入、版本与资源补充（v2）

- 匹配状态诊断和未来SF profile保留既有head8CPU/16GiB、worker32CPU/1536GiB、worker SHM8GiB、TP8及原GPU容量参数；本提案仅调整Store池容量和缓存观测流程。D0的4CPU/768GiB是另一个独立CPU-only profile，不偷偷套到旧SF对照。匹配状态的reader/root cgroup保持原配置（包括MemoryMax∞，以实际祖先有效限制为准），必须记录页缓存记账与余量，不能仅按Python RSS准入。原native启动1680GiB可用内存门槛不因本提案自动放宽。
- 修改file_cache_state.py会改变其SHA。旧冻结spec里的3340...不能自动准入新字节：只在新cohort/新spec中更新helper pin和全部实际传递链，历史spec/命令/代码证据保留、禁止复用为新配置运行。所谓旧分支不变是语义与历史记录不变，不承诺旧pins可以装载新源码。实施前重新检查共享源码的并发消费者，若冻结运行依赖这份helper则停止修改并报告；不另建checkout来绕过。
- MATCHED-STATE-DIAGNOSTIC-PROPOSAL-v1.json已完成只读预算审阅：阶段上限5220秒、完整上限7200秒。设计尚未成为可执行命令；实际源码实现、消费者测试、精确SHA、独审、发布和运行授权均尚未完成。D0草案也不被自动升级成已批准或必跑步骤。
