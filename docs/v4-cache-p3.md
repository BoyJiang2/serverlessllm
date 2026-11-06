# V4 缓存证据与计时边界（P3 CPU）

本批仅增加 benchmarks/v4_evidence.py 的离线一致性检查。没有安装环境、读取模型 payload、运行 mincore、清理缓存、调用真实 Store/Ray 或执行 GPU。权重继续原位使用 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro，代码和证据在 /home/boyjiang/sllm-v4-pro，Store 大数据仍在 /data。

## 参考与比较范围

只读复核 GLM C23 cache_state_formal.py 与 formal_trials.py，原字节及服务器 SHA 在 evidence/v4-cache-p3/reference-manifest.json。GLM 是先检查另一资产，再对目标资产准备和完整扫描，随后 preparation_complete、startup_command、HTTP /register、业务触发。cold 要求每个文件 resident_pages=0，hot 要求每个文件全部驻留；不能换成全局百分比阈值。pool_hit 的文件扫描是 check，不要求文件页驻留；pool 命中须另有完整原生确认。

V4 条件保持已冻结的 safetensors_disk_cold、safetensors_disk_hot、store_disk_cold、store_pool_hit；原 12 个 ID、3 配对块、1200 秒（含每次准备）、失败分母、至少 60 秒释放观察均不变。新字段是 V4 控制器证据要求，不能补造进历史 GLM。GLM 与 V4 可比较相同边界的延迟、准备成本和各类失败率；模型大小、量化、TP、group 数、介质与缓存层差异必须同时披露。NFS 客户端 mincore 为冷不代表 NFS 服务端/硬件缓存为冷。

## 最小顺序，无哈希旁路

当前首次 grouped_registration_plan 会 verify_payload=True，读取全部 Store 数据。若放在缓存检查之后的定时 /register 中，cold 会被哈希读热。现有 StoreManager.register 和 SllmLocalStore 已支持存活进程内幂等注册，可利用现有入口，不新增略过校验的 token 或接口。

1. 批次准备：冻结模型名、worker、Store root、完整 identity、配置和文件清单。在 StoreManager 直接 register 同一 grouped 配置，local_disk 指定目标 worker、memory_pool=[]；等全量校验和 496 次 native 注册全部成功。此入口不创建 Controller router/backend。保留校验起止、完整 SHA/注册回执及单独 bootstrap 成本；它不等于已有完整原模型 payload SHA。
2. 每次 attempt 从 preparation_begin 开始计入 1200 秒。任何批次校验若在该 attempt 内执行也计入，不重置时钟。依据条件显式准备 pool 状态；非 pool-hit 必须确认目标不在池中。检查另一资产，再完成目标准备和最后完整 mincore 扫描。所有共享元数据读取必须先于目标最终扫描。
3. pool-hit 在最终文件扫描后再次调用 confirm_grouped_host(node, model)，将真实 await 的起止及布尔结果留档；内部应覆盖全部 496 ID。确认是观察，不是驻留租约；禁止外部/并发原生 LRU、unload 或读取干扰。无法保证时不准入。
4. preparation_complete 后记录 startup_command，调用同一冻结配置的 HTTP /register，保留 min_instances=0，然后触发业务请求。原有 StoreManager 内存记录应命中，不能再次全量哈希；每次请求、worker、进程启动身份和预注册状态必须由未来执行器实查，不能只填写 store_registration_reused=true。Controller 首次注册、router、backend、分组 copy/quant、业务首 token 的原开销均留在原计时内。

StoreManager/Store 重启、名字/路径/identity/placement/config 改变、文件变更、失败/部分注册或无法证明数据封闭性时停止，重新校验后重新准备缓存；不得借幂等缓存掩盖变化。上层 StoreManager 按名字复用，不自动比较完整配置，未来执行器必须先核实完全相同配置；本批没有修改生产注册路径，也没有运行上述操作。

## 新离线函数

cache_scan_checks(scan, expected_files, vectors)：expected_files 是独立冻结、覆盖所有实际加载输入（payload、index、模型/量化/编码元数据）的绝对 source_path->bytes 清单，不能从待验 summary 自己生成。vectors 是按 vector 字段索引的解压后原始 mincore bytes。函数核对文件和向量完整集合、重复、byte/page/驻留计数、原向量 SHA、低位驻留标志、严格冷热条件及准备/最终扫描顺序。不会访问或 mmap 文件。check 模式仅核对观测，不赋予冷热 PASS。

cache_timing_checks(record, target, other, condition)：仅在两个 scan 都通过前函数且 run/boot/time namespace 相同后调用。验证另一资产先于目标准备、完整缓存证据先于 preparation_complete/startup_command、总 attempt deadline 不超过 preparation_begin+1200s，以及 Store 校验早于缓存准备和定时注册复用声明。批次校验可早于本次 attempt，但 bootstrap 成本须保留。pool-hit 还检查完整池确认声明在最终扫描后、准备完成前。函数仅检查结构与时间关系，不验证这些声明来自真实 RPC。

它们是离线计算器，没有连接执行器，也不输出正式 attempt PASS。仍需原生采样器的 syscall/文件身份/完整清单/逐文件稳定性证据、输入 SHA 与不可并发写入约束、单机时钟域、run/protocol/source 身份、准备后到启动间的干扰控制、资源准入、完整 pool 收据和所有已启动/准备失败的分母。不能凭几个自填字段产生正式结论。

## CPU 验证与下一步

新增 13 项合成异常用例，覆盖全向量、严格阈值、缺失/重复/额外输入、checksum/计数、扫描顺序、四个冻结名称、晚哈希/重新注册、准备超时、pool 确认时间和批次校验复用。首轮 212 项通过后修正条件名并补充两例；最终统一回归结果见同目录 cache-suite-receipt.json。所有时间值和向量均为合成，未使用正式 ID。

下一步在 CPU 契约范围内实现控制器状态机和不可变 attempt 记录，把这两个检查与已验证的身份/事件、准备失败分母连接起来；真实缓存采集/准备、权重全 SHA、容量、模型 gold、服务卸载/再拉起和释放观察仍待后续运行授权与环境验收。

后续接口修复见 [P3 当前审计](v4-p3-readiness-audit.md)：StoreManager 已固定 common 配置及 Store identity/path，活跃 Controller 路由也核对完整配置。四条件允许删除旧路由后在两种既定 loader 间切换；native SLLM 注册需先完成 grouped Store 准备，避免旧 converter。进程/worker epoch与输入内容仍须独立实查，配置绑定不是驻留租约。
