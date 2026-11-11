# SLLM 多模型切换实验方案 V11（2026-11-11 修订）

> **修订重点**：基线 1 和主方案使用同一个持续运行的 **SLLM CPU 权重 Store**；基线 1 增加 **vLLM GPU KV L1 + 独立 LMCache MP Server 的 CPU Host KV 层**，但在模型切换边界清理旧模型 KV。基线 2 单独使用 **vLLM Sleep Mode Level 1 / Level 2**，关注保留 Worker 的显存释放、CPU 备份与唤醒成本。重点对比 **切换时间与 CPU/GPU 资源代价**。
>
> **本版修订（相对上一稿）**：三条路线、四组实验（A / B1 / B2-L1 / B2-L2）。B1 定位为 **KV 层的纯开销对照**——不复用、同一模型存活期内关闭 prefix caching、不配置 LMCache 的 L2 adapter，primary 指标改为 KV 层真实代价。B2 重新定位为 **原生 vLLM Memory Saver 能否替代 SLLM 的权重 / KV 管理能力**——不接 SLLM Store，重载源只用原生 checkpoint。

![三条实验路径架构图](SLLM_Three_Paths_V11.svg)

> 图沿用上一稿：A 的"共享 Store / 权重池"、B1 的"KV 按模型切换边界清理、不跨模型保留"、B2 的"多 Worker 依次 Sleep / Wake"三个框与本版定位一致，无需重绘。

## 0. 本版变更（相对上一稿）

| 编号 | 变更 | 原因 |
|---|---|---|
| C1 | B1 增加"纯开销"条款：同模型存活期内关闭 vLLM prefix caching，KV 命中必须为 0；命中即需记录并在结论中剔除 | B1 的意义是量"多一层 KV 管理要付多少代价"，命中会污染 A/B1 可比性 |
| C2 | B1 明确**只做两层**：不配置 LMCache 的 L2 adapter（fs / S3 等），Host 层严格等于 LMCache MP 的 DRAM L1 | 配了 L2 adapter 就变三层，`/cache/clear` 也清不到它，"占用归零"不成立 |
| C3 | B1 的 primary 改为 KV 层真实代价（写入字节、clear 耗时与确定性、Host 层峰值、MP Server RSS），端到端时延降为 secondary | 不复用意味着时延只剩开销，它本身就是被测量对象 |
| C4 | B2 重新定位：从"独立基线 / 比切换成本"改为"原生 Memory Saver 能否替代 SLLM 的权重与 KV 管理能力"，结论项是**能力等价性 + 代价** | 验证的是管理能力，不是跑分 |
| C5 | B2 不接 SLLM Store；B2-L2 的权重重载源只用 vLLM 原生 checkpoint | "不要有 SLLM 的多级缓存"必须在数据上成立 |
| C6 | 新增 §2.1 四组在"缓存管理层数"轴上的位置，并声明 B2-L1 的 CPU 权重备份是**独占**缓存，不是多级缓存 | 否则"B2 没有多级缓存"会被 B2-L1 自己的数据反驳 |

## 1. 研究目标与共同实验协议

研究对象为 Kimi-K2.6、GLM-5.1-FP8、DeepSeek-V4-Pro 在同一组 GPU 上的串行切换（TP8），固定序列：

```text
K → G → D → G → K → D
其中 K、G、D 分别代表 Kimi-K2.6、GLM-5.1-FP8、DeepSeek-V4-Pro。
```

三条路线、四组实验（A / B1 / B2-L1 / B2-L2）都必须输出正确结果，并报告完整的切换耗时、模型后端 / Store / 缓存服务的生命周期，以及 CPU RAM、GPU HBM 的实际资源变化。

**共同约束**：模型 checkpoint revision、量化配置、上下文长度、输入与正确性协议固定；目标 vLLM 版本为 **0.20.1**。此前 Kimi/GLM 的已验收版本为 0.19.1 系列，升级或适配至 0.20.1 后必须重新验证原有 loader 与正确性。已有证据不能自动外推到新版本。

**本版新增约束**：

1. **LMCache 版本必须锁定**（tag 或 commit），不能只锁 vLLM。MP 的 HTTP 面在 0.4 / 0.5 线之间变动过，"清空 CPU 层"的接口形态与参数要以锁定版本为准。
2. 进入正式切换序列前，先在锁定版本上通过**三项预验收**：`GET /status` 能看到已注册的 KV 上下文；`POST /cache/clear`（等价于 `lmcache kvcache clear`）返回符合预期；clear 前后 Host 层记账字节确实归零。

### 既有工作：可直接继承的权重 Store 实验

- 同一个 SLLM Store 采用 **1 TiB 固定锁页 CPU 池、32 MiB 块**，贯穿 **17 次 Backend 激活**，期间 Store 未重启。
- 在固定序列 `Kimi → GLM → V4 → Kimi → Kimi` 中，V4 之后的 Kimi 需要重新读取约 **617.001 GB**；紧接着再激活 Kimi 时 **8/8 权重路径命中、文件重读为 0**。
- 旧模型权重缓存块可驱逐、被其他模型重新使用；旧 Backend 正常退出后 GPU 权重 / KV 释放，**Store CPU 缓存历史继续存在**。
- 以上是既有实验结果，并非新基线已经复现通过的结果。

参考：[现有三模型完成报告](https://github.com/BoyJiang2/serverlessllm/blob/d3c10bda437b29d821b4845ca6a79c39b00fb975/comparison/evidence/three-model-final-20261108/REPORT.md)、`SHARED-STORE-RESULTS.md`、`COMPLETION-AUDIT.md`。

## 2. 三条复现路径

### 2.1 四组在"缓存管理层数"轴上的位置

| 组 | 权重缓存 | KV 层 | 管理层数 | 本组要回答的问题 |
|---|---|---|---|---|
| **A** | 共享 CPU 池 W，可驱逐、可跨模型命中 | 无（随 Backend 退出消亡） | 1 | 只靠共享权重缓存，一次切换要付多少 |
| **B1** | 与 A **完全一致**，不得改动 | GPU KV L1 + Host KV 层，**写入即弃、每次切换清空** | 2 | 多一层"真实工作"的 KV 管理，额外代价是多少 |
| **B2-L1** | 每模型**独占**一份 CPU 备份（不共享、不驱逐、不计命中率） | 丢弃，wake 后重建 | 1（独占） | 不靠共享缓存，只靠常驻进程 + CPU 备份能否管住资源 |
| **B2-L2** | **无缓存**，wake 后从原生 checkpoint 冷重载 | 丢弃，wake 后重建 | 0 | 完全不缓存时，管理能力的下限在哪里 |

**口径声明（必须写进报告）**：B2-L1 的 CPU 权重备份也是缓存，但它是**每模型独占、无跨模型共享、无驱逐策略、无命中率**的单份副本，因此不构成 SLLM 意义上的多级缓存；B2-L2 才是真正的零缓存端点。四组按管理层数从多到少排列，任何"谁更优"的结论都必须落在这条轴上解释。

### A：主方案——共享 SLLM 权重 Store 复用

```text
SLLM Store 持续运行，权重 CPU Pool W 不重建
       ↓                       ↓
vLLM A 加载/推理 → A 正常退出 → vLLM B / C 加载/推理
       ↑                                         ↓
       └──────── 返回 A：同一 Store 命中 / 重读 ──┘
```

**机制**：复用一个常驻 Store、一个 CPU 权重池，以及未被淘汰的本模型权重；发生容量竞争时进行 Store 块驱逐和空间再分配。GPU KV 只在模型 Backend 生命周期内存在，关闭后丢弃，不作跨模型持久化。

**指标**：权重命中路径、Store 重读字节、victim / 分配 / 归还块、激活至正确响应、切换端到端时延、CPU / GPU 资源占用。

### B1：共享权重 Store + KV 两层，**不复用**的纯开销对照

**与主方案共用同一套 SLLM 权重 Store 机制**（权重侧不做任何改动），只额外接入 LMCache MP Server 作为 Host KV 层。

```text
              共享 SLLM Store（CPU 权重缓存 W，常驻）
                        │                   │
                     权重恢复             权重恢复
                        ▼                   ▼
vLLM Model A                       vLLM Model B（新建）
  GPU KV L1                            新的 GPU KV L1
     │
     └── KV Store ──→ LMCache MP Server：CPU Host KV 层
                                │
                          A 请求完成、A 退出
                                │
                          清理旧模型 KV
                                │
                   服务进程不退出，Host 层占用归零或可核查释放
```

**必须满足：**

1. Model A 的 GPU KV L1 和 LMCache CPU Host 层都真实运行，Host 层发生实际 KV Store、可观测其内存占用；不是仅配置参数。
2. A 的请求结束后等待所有在途 KV Store / 注册完成，正常关闭 A Backend。确认没有活跃读写，再清空 LMCache 的 CPU KV 层；不能把新模型 B 的数据写到 A 的身份空间。已验证：`POST /cache/clear` 与 `lmcache kvcache clear` 是同一个调用（CLI 内部即 `POST /cache/clear {"tier":"l1"}`），该接口只接受 `tier=l1`、其它值返回 400，且**没有模型维度过滤**——即只能整层清空，不存在"按模型选择性清理"。
3. **保持同一个 SLLM Store 常驻**：Model B 仍可从共享权重池加载（通过已有 SLLM Loader 接入 vLLM）；不允许把普通 SafeTensors 磁盘加载误称为 Store 命中。
4. 旧模型 KV 数据 **不跨模型保留、不参与 B 的推理**。B 重新分配自己的 GPU KV，并存储 B 自己的 KV。
5. 记录 MP Server PID、Host 层写入 / 清理前后字节数、进程 RSS / cgroup、GPU KV L1 占用和 Model A→B 切换耗时。
6. **纯开销条款（C1）**：本组的 KV 层不得作为性能收益呈现。同一模型存活期内**关闭 vLLM prefix caching**，KV 命中必须为 0；若因实现限制无法关闭，则逐次记录命中数并在结论中剔除。本组不追求 KV 命中率，也不评价跨模型 KV 复用性能。
7. **只做两层（C2）**：不配置 LMCache 的 L2 adapter（fs / S3 等）。Host 层严格等于 LMCache MP 的 DRAM L1，以便"占用归零"是一个可核查的命题，而不是部分成立。

**术语说明**：LMCache MP 的 CPU 层在其自身术语中称为 *L1*；本报告统一以整体层级 **GPU KV L1 / Host KV 层** 命名。两层分属两个所有者，边界不可混：**GPU KV L1 归 vLLM 的 paged KV 池，只有该 Worker 退出才释放，LMCache 无权分配、驱逐或清空**；**Host 层归 MP Server，是 `/cache/clear` 唯一能触碰的层**。

**指标（C3，primary 已调整）**：

- **primary**：KV 层写入字节与 STORE 耗时、clear 的耗时与返回体、clear 前后 Host 层记账字节、MP Server RSS / cgroup、Host 层峰值。
- **secondary**：权重命中路径、Store 重读字节、端到端切换耗时与各阶段耗时。
- 报告须显式区分 `t1`（A 退出、NVML 显存回落）与 `t2`（clear 完成）：前者是进程退出的副作用，后者才是 LMCache 的动作，二者不可合并计账。

参考：[LMCache MP HTTP 管理 API](https://docs.lmcache.ai/mp/http_api.html)、[LMCache MP 配置](https://docs.lmcache.ai/mp/configuration.html)。

### B2：原生 vLLM Memory Saver——不带 SLLM 多级缓存的权重 / KV 管理

**本组要验证的不是"比谁快"，而是**：不引入 SLLM Store 的共享 / 多级权重缓存，仅靠 vLLM 原生 Sleep L1 / L2，能否完成 **"权重下放—恢复"** 与 **"KV 丢弃—重建"** 这两项管理，代价是多少。

**本组不接 SLLM Store。** L2 的重载源必须是 **vLLM 原生 checkpoint 源**，重新加载的字节数与耗时单独记录——这项数据正是与 A 的"Store 命中"真正对齐的对照项。任何"用 SLLM Store 给 B2 喂权重"的做法已排除（见附录 A）。

按每个模型保留独立 vLLM Worker 的方式尝试串行切换：

```text
Model A：Running → Sleep(L1 / L2)
Model B：Running → Sleep(L1 / L2)
Model C：Running → Sleep(L1 / L2)
                              ↓
返回 Model A：Wake → 恢复权重 / 分配 GPU KV → 正确答题
返回 Model B：Wake → 恢复权重 / 分配 GPU KV → 正确答题
返回 Model C：Wake → 恢复权重 / 分配 GPU KV → 正确答题
```

**必须将 Level 1 和 Level 2 分为两组实验，不允许混作同一种机制。**

| 项目 | Sleep Level 1 | Sleep Level 2 |
|---|---|---|
| GPU 权重 | 释放 GPU 内存，权重备份到 CPU RAM | 释放 GPU 内存；原权重数据丢弃 |
| GPU KV | 内容丢弃 | 内容丢弃 |
| CPU RAM | 增加权重备份副本（**每模型独占**） | 不备份完整参数；保留部分非参数状态 |
| Wake | 从 CPU 备份恢复权重，重新分配 KV | 先分配权重显存、**显式重新加载权重**，再分配 KV |
| Worker | 保留进程，不能等同于退出容器 | 保留进程，不能等同于退出容器 |
| 关键约束 | 多个超大模型权重备份总量可能超过可用 RAM | 重载源固定为原生 checkpoint；加载字节 / 耗时必须可测 |

官方 vLLM 0.20.1 的 L2 示例为 `sleep(level=2) → wake_up(tags=["weights"]) → reload_weights → wake_up(tags=["kv_cache"])`。B2 不叠加 SLLM Store，因此不存在"原位 SLLM Loader 适配"这一项；若将来要做，属于另一个实验，不在本方案范围内。参考：[vLLM 0.20.1 Sleep Mode](https://docs.vllm.ai/en/v0.20.1/features/sleep_mode/)。

**可行性门槛**：Sleep 释放 HBM 并不必然释放 Ray GPU 资源声明、CUDA Context、NCCL 占用。验证 A 睡眠以后 B 是否真的能在相同 GPU 上成功启动；若无法共置，记录该配置为 **不满足多模型切换要求**，而非通过强杀或退回进程重建将其伪装为 Sleep 成功。该门槛是**前置门槛**，应在跑满 6 段序列之前先做单次 smoke test。

**能力差异（必须写进报告）**：A 的 KV 要等 **Backend 进程退出**才释放；B2 的 KV 在 **Sleep 时**就释放，而 Worker 进程仍存活。这是 B2 相对 A 唯一明确更强的一点，代价是常驻进程与 CPU 侧资源。因此资源表必须单列"进程是否存活"字段，否则这条差异看不出来。

## 3. 容量设置

| 资源池 | 变量 | 候选实验值 | 说明 |
|---|---|---|---|
| SLLM Checkpoint CPU 池 | W | 已有 1024 GiB；可扫 896 / 960 / 1024 GiB | 每档独立初始化 Store，档内不重启；仅 A / B1 |
| LMCache CPU Host 层（仅 B1） | K | 8 / 16 / 32 GiB | 必须连同 SLLM 锁页池计算宿主 RAM 余量 |
| GPU KV L1 | G | 每款模型实测默认值及缩减值 | 由 vLLM 的显存配额决定，LMCache 无权调整；不将缓存容量与 GPU 已分配物理内存混同 |
| Sleep L1 的 CPU 权重副本 | S | 按模型实测 | 非固定 K，可能接近每模型权重字节量；每模型独占一份 |

B1 的 W、K 是两个彼此独立的 CPU 分配器，不可假定其中一方的空闲块直接供另一方使用。**B2 不接 SLLM Store**，其内存预算 = S（每模型独占备份）+ 常驻 Worker 的系统开销，不存在 W / S 叠加，也不存在 Store 驱逐。

## 4. 切换时延和资源采样

### 事件时间线

| 时间戳 | 主方案 A | B1：LMCache | B2：Sleep |
|---|---|---|---|
| `t0` | 旧模型停止接收请求 | 旧模型停止接收请求 | A 开始 Sleep |
| `t1` | 旧 Backend 正常退出 | A 结束 KV 写入并退出 | Sleep 完成确认 |
| `t2` | 新模型开始激活 | Host 层清理确认；B 开始激活 | B 开始启动或 A 开始 Wake |
| `t3` | 新模型 engine ready | 新模型 engine ready | 新模型 ready / A Wake 完成 |
| `t4` | 新模型正确业务响应 | 新模型正确业务响应 | 新模型正确业务响应 |

- **切换时间**：从旧模型开始执行切换动作到新模型完整且正确的业务响应；各项动作含在该定义之内。**A 以此为主要结论；B1 只作 secondary，B2 只作 secondary（其 primary 是能力是否成立与资源代价）。**
- **阶段耗时**：A 用 `teardown / Store lookup-load / engine ready / inference`；B1 增加 `KV store completion / cleanup`（且 `t1` 与 `t2` 分开报）；B2 增加 `Sleep(L1/L2) / Wake / weight restoration`（含重载字节数）。
- **实验等待/观察**：若延续已验证的每轮 60 秒资源观察，应单列 `T_observation`，同时给出含观察和不含观察的结果；不得混作算法切换时延。
- **内存采样**：切换前、Sleep / Host 层清理后、新模型就绪后、正确响应后四个稳定点必须采样；动作期间持续采样（例如 1 秒间隔），保留不可用采样为缺失。

### 必须采集的资源字段

- **GPU**：8 卡每卡 NVML `memory.used` / 活跃计算进程、模型 Weight / KV 可归因字节（如有原生事件）、Sleep 前后释放差值、进入下一模型时的 GPU 剩余量。
- **CPU RAM**：SLLM Store 锁页池总量 / 有效 payload / 空闲块，LMCache MP 占用和 CPU Host KV 字节，Sleep CPU 备份量、RSS、cgroup `memory.current` 和可用内存。
- **进程与生命周期**：每个 Backend PID、Store CID / PID / start_ticks、LMCache PID、Sleep 状态、**进程是否存活**、GPU / Ray 资源声明和容器退出码。
- **正确性**：固定问题"请只输出数字：17 + 25 = ?"期望 42，同时维持各模型已经冻结的金标检查；保留原始响应和异常记录。

**数据处理**：KV 命中率与跨模型 KV 复用性能不属于本次结论；但 B1 必须证明 Host 层的实际写入与清理。资源峰值按观测到的采样峰值表达，不能宣称为完整内核真实峰值；CPU RSS 与 cgroup 不相加。

## 5. 建议交付

- `architecture.svg`：本图的可编辑矢量版（现有图可直接沿用）。
- `switch-events.csv`：每次 A/B/C 切换的阶段时间戳、缓存事件、`t1`/`t2` 分离记录与正确响应时间。
- `resource-samples.csv`：时间、host / cgroup RSS、8 卡显存、Store / LMCache / Sleep 备份字节、进程存活标记。
- `qualification.json`：每次 model、version（含 LMCache 版本）、TP8、金标、异常、是否正常退出。
- `report.md`：A、B1、B2-L1、B2-L2 四组对应结果及能力边界，结论必须回落到 §2.1 的"管理层数"轴。

**最终结论目标**：在相同切换负载与可对齐的资源口径下，回答"多一层 KV 管理值不值"（A→B1）、"共享缓存换成独占备份会怎样"（A→B2-L1）、"完全不缓存的下限在哪"（B2-L2）。哪一种方案适合何种预算，应由实测数据支持，而不是提前把任何一组判为劣势。

## 6. 未决事项（不改变本版实验定义）

1. **重复次数与统计口径**：每个模型切换点建议 ≥3 次重复，报中位数与极差；轮次顺序需固定或随机化并记录。
2. **`engine ready` 的判定**：建议定义为 health 200 + 一次空跑通过，而不是"进程起来"。
3. **`T_observation` 是否固定为 60 秒**：需确认后再写入 runbook。

## 附录 A：已排除项

1. **跨模型 KV 复用**（含 B1 内的任何 KV 命中）：与"不复用"的设计目标冲突。
2. **B1 配置 LMCache 的 L2 adapter（fs / S3 等）**：会使"两层"变三层，且 `/cache/clear` 清不到这一层。
3. **用 SLLM Store 作为 B2 的权重重载源**：与"不要 SLLM 多级缓存"冲突，且需额外验证原位 SLLM Loader 适配，属另一个实验。
4. **把 Sleep 通过与进程重建 / 强杀混同**：已在 §2 B2 的可行性门槛中禁止。
