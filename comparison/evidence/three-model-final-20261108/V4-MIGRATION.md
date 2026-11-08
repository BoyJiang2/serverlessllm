# SLLM × DeepSeek-V4-Pro：迁移机制、状态契约与完成审计

本页绑定实际 GPU 执行源 `88cc71ec50c8668c26f26636d7d432c3351356fe`，而非报告发布提交。B1 的两个合格冷样本及两个原失败保留各自旧源；不将全部 40 行宣称为同源。

## 1. 先复用既有能力

SLLM 维护基线 `9f50241baa5386e06a9321c51f19a9ef5f964c2b` 对应 vLLM 0.11.2 的系统接点；更早公开候选 `6847927…` 为 0.5.0，不能混称精确论文 artifact。Kimi 已有修复、GLM raw loader/实验控制提供所有权、IPC、终态与计时的复用依据，不把这些全部计为 V4 发明。

GLM 来源为 `qjhs003:/data/brick1/boyjiang/glm5.1-fp8`，实际 loader SHA `36a40e20f8d2d6b2e7b3346ec5e1c54d5598359b1b1756348ef97723e34fa502`；其模型结构、E8M0-off 和旧引擎限制不能直接带到 V4。Kimi 文档中的 restore-v4 是该任务版本标签，不是 DeepSeek-V4 的验收。

| 层次 | 原机制或上游能力 | 本次必要接点 | 证据边界 |
|---|---|---|---|
| 原 SLLM | Store 分片 I/O、host pinned pool、CUDA copy/IPC、storage owner、完成确认、服务销毁 | 复用 native Store，不以 Python 权重经 Ray RPC 传输；保留 owner/错误传播 | 八 rank 恢复、496 ID、正常 Store 退出；不是 GDS |
| 0.11.2 → 0.20.1 | loader/worker API、async 引擎、注册和路由、请求/销毁接口 | `vllm_v4_loader.py`、`v4_export_worker.py`、`sllm/backends/vllm_backend.py`、Controller/StoreManager/V4 注册适配 | 使用目标 loader registry、`worker_extension_cls` 和命名 collective RPC，不原样重放旧七补丁 |
| V4 增量 | 上游已经有 V4 模型、mHC、原生混合量化、H20 Marlin、MLA/DSA/KV 构造 | 分组 raw、原生一次后处理、完整 state/layout/alias 身份检查、256B owned 子分配对齐 | 自定义格式 `sllm-v4-pro-grouped-raw-v2`，不是原论文不变格式 |
| 完成/失败硬化 | 既有 future、copy/IPC close 和 owner | C++ shared_future 按 model/replica UUID 保存终态；CPU I/O/dispatcher/consumer 失败传播 | CPU 注入验证失败分支；真实成功恢复与清理另有 GPU 证据，不声称所有硬件故障都实测 |
| 打包及实验控制 | wheel、依赖、事件、准入、守护与采样 | 专用 metadata/lock；revision/KV/seed/mp 闭合；原响应先保存；时序/cache lease | 不计作原 SLLM 模型加载机制，也不从 CPU 测试数量推定 GPU 通过 |

上游目标为 `vLLM 0.20.1@132765e3560659ff63ebd236203672e991b70e08`。初始候选 0.20.0 有 Hopper sparse-index top-k 分派风险；可选最小回补 #41605，但它与 0.20.1 全版本不是同一配置。2048 主场景不能仅凭 topk=1024 就断言命中 >32768 的协作 radix 缺陷。版本取舍及精确源 blob 见前置 P2 版本补充，实际使用 0.20.1 的真实 wheel 和模型证据闭合了本批目标，不宣称测试所有长度/内核分支。

## 2. 最小生产补丁职责

以下为职责组，不把历史开发提交数或文档数量当成最小补丁量。源码在本仓库实际执行提交中。

| 文件/接点 | 最小职责 |
|---|---|
| `sllm_store/sllm_store/vllm_v4_loader.py` | 导出前处理 raw；fresh skeleton 检查；逐专家层恢复及原生后处理；remainder owner/异常隔离 |
| `v4_state.py`、`v4_grouped_state.py`、`v4_source_pins.json` | identity、dtype/布局/alias/范围、文件与 rank hash、grouped COMPLETE、引擎源码和容量约束 |
| `v4_export_worker.py`、`v4_convert.py` | 真实 worker named RPC、CUDA 同步、原始 per-rank 成功回执和全 rank 原子发布；回传 primitive JSON ≤16KiB |
| `sllm_store/torch.py` 及 native checkpoint/Store 实现 | owned restore、256B 对齐、E4M3FN/E8M0 dtype、错误回滚、copy/IPC close 确认及失败 owner 保留 |
| `v4_service.py`、StoreManager/local store、backend/Controller/router | 固定 revision/TP8/EPoff、496 ID 注册、CPU copy 完成/整池驻留、async 服务、请求和正常销毁 |
| `setup.py`、专用构建/lock | 避免旧 vLLM/Torch/OpenAI/Pydantic metadata 冲突，固定实际 wheel/native ABI |
| `benchmarks/v4_*` | 仅实验：GLM 字段、时序、原 HTTP 响应、cache identity/mincore/lease、rank 事件、deadline、释放及原失败台账 |

未增加通用调度框架。Head 四个长期 CPU1 actor 为 Controller、StoreManager、FCFS scheduler、Router；worker backend CPU1/GPU8，另留 3 个逻辑 CPU 供瞬时工作，无 placement group。容器实际 quota4/4、Ray `num-cpus`4/4、actor 申请和节点约束分别实测；没有改 `num_cpus=0` 绕过资源不足。PIDs 上限4096，cpuset继承0–239，不声称独占物理核。

## 3. Store 状态契约

完整实际外格式 grouped-raw-v2、内格式 raw-v1。每 rank：remainder + 61 个**专家层组**，共 8×62=496 个 Store identifier；61 不是专家数。每组完整保留权重、scale、同 view alias 及原生遍历顺序。

| 状态 | 持久化 / 构造 / 恢复后重建 |
|---|---|
| MXFP4 专家 w13/w2 与 scale | 保存原生融合、TP 分片后但量化后处理前 raw 字节；UE8M0 不转 FP32 替代；每层完整恢复后调用一次对应 native quant 后处理 |
| FP8 非专家、mHC FP32 | remainder 持久化，包括参数子类、dtype/shape/stride、registered buffer 和 persistent 标志；owned raw storage 必须保持被引用期间存活 |
| 融合、重排及 kernel layout | 依固定引擎原始 load_weights 和原生 postprocess 顺序；最终 packed/layout 不与 pre-process raw 混存后重复转换 |
| quant_method/kernel/plain 派生 Tensor | fresh 模型构造及一次原生后处理生成；对象不 pickle 入 Store；检查实际引用，不能仅恢复 state_dict 就宣称完整 |
| alias / buffer | 完全相同 view 记录 alias；不同 offset/shape 的重叠 view、跨组 alias、非 contiguous、遗漏/重复键 fail closed |
| MLA/DSA、压缩 KV、request map、scratch/stream | fresh 引擎按实际 fp8 KV 配置重新初始化为空；不保存或迁移在途 KV/请求/graph runtime |

恢复顺序：验证 COMPLETE/身份/拓扑与 fresh 布局 → 释放 fresh 初始权重 storage → remainder restore 并保活 owner → 按保存的原生模块顺序逐层 expert raw restore → native postprocess 恰好一次 → CUDA sync/引用安全检查 → retire 该组 raw owner → 目标原生 attention 后处理、KV/profile/warmup → engine ready → 真正发布路由。失败后 `_sllm_v4_stage` 或部分层完成不得成为可用模型。

256B owned 子分配对齐解决真实恢复中暴露的 mHC TMA 对齐问题；最初失败、未完成 probe 和清理记录保留。之后完整八卡恢复与 gold 通过证明该配置可用，不将早期对齐候选直接说成已独立证明的唯一根因。Payload 未因此重新量化或更改。

## 4. 各 rank 的完成与失败

| 阶段 | 成功条件 | 失败契约 |
|---|---|---|
| 保存 | 每 rank 独占目录；组集合、alias、文件范围/hash 完整；真实 worker sync 后返回小 JSON receipt | 缺一 rank/组、显式 export failure、混 identity、错 UUID 均不发布 COMPLETE |
| 全局 COMPLETE | TP8/EPoff/PP1/DP1、唯一物理 GPU UUID；全部 rank metadata/bytes/hash 和完成标记通过，原子 fsync 发布 | 单 rank 或仅 metadata-only 不能代替完整数据验真；目录 fsync 失败撤销新标记 |
| CPU pool | 每组独立 ceil(bytes/32MiB) 求和；496 组真实 CPU-copy 终态确认、完整驻留 | RPC 接收成功不等于权重就绪；部分注册/在途操作隔离，不伪称 metadata 事务回滚 |
| GPU restore | 每 rank 所有 copy 与 IPC close 成功；CUDA 同步；owner 引用存活；每层一次 native 后处理 | 任一 copy/close/后处理异常保留 owner/quarantine，拒绝 ready；不是提前 cudaFree |
| Confirm / clear | 同 model/replica UUID shared_future，迟到/重复 Confirm 得到同终态 | 未知 UUID 失败、重复 submit 拒绝；active/failed ClearMem 拒绝；失败结果保留至 Store 退出 |
| 服务释放 | gold、同引擎事件链、delete ack、正常 Store/Ray/guard/container exit；61 个样本≥60秒，原 PID/IPC/GPU 无残留 | 隔离、强杀或守护回收只能算失败清理，不是正常生命周期成功；回收不确定不再准入 |

逐层释放避免单 slab 保存 raw+converted 专家权重的下界约191.40GiB/rank，超过 H20 每卡约140.40GiB。实际 Store payload877729513472B；496组按32MiB向上取整的 CPU pool890937278464B，不是对总字节只取一次整。独立完整 hash 在非计时准备阶段已完成一次；正式计时 metadata-only 不能检出同尺寸 payload 变更，资产只读和原完整验真是必要前提。

## 5. 缓存协议本次闭合

SF69 / Store1513 / 交集5。原先先锁 SF 再断言 Store1513 全冷有逻辑矛盾；现为：锁前驱逐并验证 Store1513 全冷 → 锁 SF69 → 强制验证 Store 独有1508仍冷 → 共享5单列 `hot_after_lock_by_construction` → 启动边界验证 SF69 全热。直到真正 HTTP delete ack 才解锁，VmLck归0且helper退出0。

Linux mincore 的权限降级可返回全1，故准入包含真实 owner/capability/visibility 检查，不能从无权限 all-ones 声称热缓存。本批 CPU 修复后先经独立非正式全模型 pilot，再修订/冻结65ee协议；pilot不占正式编号。用户指定原NFS完成批次，brick3本地化成果保留为独立未消费输入，没有改资产介质来补过缓存逻辑。

## 6. 实际执行矩阵与完成审计

| 门槛 | 实际证据 | 判定/限制 |
|---|---|---|
| P4A wheel、依赖及导入 | wheel179431059B，SHA f978d9bf907eebca3c682574b0e8461edd9133c7360614fb94a91c00d5351cf0；205包、pip check、14 ELF、九原生扩展；tokenizer30/43 | 完成；CPU compat 导入与后续真实宿主 driver GPU 运行分开记录 |
| P0/P2 revision/KV/seed/事件 | b596固定revision贯穿注册/backend/loader；fp8 KV、FP4 indexer off、seed20261105/mp；事件严格顺序、原响应先存 | 完成；旧协议和失败原件不改 |
| 原生 SafeTensors | native-reference-20261106T205752Z，完整64shards、八卡、自然EOS/fixed32、正常退出/77.27秒释放 | 完成；只提供 gold，不作四条件时间基线 |
| 完整 Store 数据 | store-conversion-20261107T020633Z；八真实worker RPC；独立CPU197.160720331秒完整验真；COMPLETE3333fe… | 数据完整性完成；原GPU转换deadline失败仍失败，不追认 |
| 八 rank 恢复 | store-recovery-20261107T063304Z，源655b88f，8×61后处理、owner/IPC、gold、daemon42.327秒正常退出/75.917秒释放 | 完成；非正式样本 |
| 原生服务两轮 | native-service-20261107T093848Z，源69f67ad；两轮register→gold→delete，轮间62.028秒观察；再次拉起；最终正常退出/82秒释放 | 完成；保留其版本，不冒称最终源重跑 |
| 四条件×10 | 40原编号终结，38独立合格+2原失败，B10四条件均通过，release81.67–84.38秒 | 完成冻结批次；缺测与版本分层保留，无替换 |
| 可复算交付 | 原40 replay、38原报告重审一致、原始档案SHA、统计/成本/资源/三模型报告 | CPU复核通过；发布是否同步以随后实际Git回执为准 |

前置逐阶段原始说明与证据仍在仓库 `docs/v4-*.md`、`docs/evidence/v4-*`；它们开头的“整体未完成”描述当时里程碑，当前状态以本完成审计及最终发布回执为准。外部源、原冻结协议和原失败不重写。

本验收不覆盖初始方案中更宽的长输入/取消故障注入等候选矩阵；后续实际冻结范围是2048/eager/seq1、两类短文本请求、TP8/EPoff、空KV新引擎和正常删除/再拉起。不能声称已验证 >1024 输入的所有 DSA 分支、在途取消或压缩KV迁移。该范围界限不阻止已授权正常机制与正式批次交付，也不靠额外GPU运行填满初始设想。
