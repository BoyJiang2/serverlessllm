# SLLM × V4-Pro：P3 当前接口与验收审计

2026-11-06。本次被测源码为父提交 92f4380f19a3448b251c59c3cb522a0c25212f20 加工作差异 dcb2eb518221eb934a1b87cb8b14a78ee9e4c6ef688756941b158b10224da6ba。新增 17 项注册契约用例，统一 274/274 CPU 回归通过、无跳过；实际 /home NFS TMPDIR 中运行。不是完整 native 扩展构建、模型运行或正式实验资格。

活动代码/证据：qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}。模型按用户指定原位使用 /mnt/raid/models/DeepSeek-V4-Pro；Store 数据在 /data/brick1/boyjiang；GLM 原参考在 qjhs003:/data/brick1/boyjiang/glm5.1-fp8。旧 /data 代码和证据为 b89f2f1 快照，不再执行。此前 grouped raw 与失败状态保留的路线选择已落实，无须重复询问旧 v4-p3-cpu-audit.md 第5节的问题。

## 本次实际缺陷与最小修复

先用未修改源码复现两个失败：StoreManager 已注册同名模型后，换 conversion_id 仍返回；Controller 活跃路由换 max_model_len 也直接返回。两者跳过 LocalStore 的 identity/path 检查，因而底层已有校验不能保证公开注册入口复用的是原配置。registration-red.log 保留两项 AssertionError，修复后转绿。

v4_service.py 增加纯 stdlib 配置快照和绑定；StoreManager 在现有 metadata_lock 内保留进程内绑定，Controller 在首次 await 前复制 V4 输入并核对活跃路由配置。规范 JSON 比较区分整数与浮点、拒绝 NaN，忽略字典键顺序；等待 worker 时调用方改原 dict 不影响本次注册。改动仅针对 tokenizer_mode=deepseek_v4 或 sllm_v4_* loader 所声明的 V4 服务，旧模型 converter/重复注册/停止状态返回行为保持原样。

四条件不能被“同名只能永远一种 loader”破坏：共同 model_config（除 backend_config.load_format 和 model_loader_extra_config 外）固定；grouped Store 的 identity/path 单独固定。仅允许 safetensors 与 sllm_v4_grouped_store 两种模式。native safetensors 不携带 grouped extra，必须先有成功的 grouped Store 准备；这样不走旧 downloader/max_model_len=1 转换。standalone 原生 vLLM A 阶段不受此 SLLM 前置条件影响。

活跃 Controller 路由必须完整配置相同才能重复注册；切换加载模式要先成功删除旧路由。StoreManager 的准备绑定跨路由删除保留，故同一批次可以 grouped→safetensors→grouped，CPU 用例确认仍仅一次 worker 检查和496次 native register，没有重新 payload 哈希或旧转换器调用。Store 准备失败时 native 模式被拒绝；同名改路径/identity/精度/TP/上下文/放置/数值类型被拒绝。V4 Controller 未运行、未知 local_disk/memory_pool worker 现在显式抛错，避免 HTTP 层把静默 return 包成 ok。

这些是配置一致性检查，不是输入文件不可变或 worker 驻留租约。绑定在首次尝试前保留；失败后不提供改配置/清空绑定的自动重试接口。Store/worker 重启、进程 epoch 或数据内容变化仍须由未来执行器识别并停止，不能相信内存 model_info 本身。并发同配置注册/删除、动态 /update/LoRA、失败强制清理也没有因此获得完整资格。首份输入是否满足全部 B1 profile 仍须独立准入；此补丁不是完整配置验证器。

## 原目标逐项审计

| 目标/机制 | 可核对实现与当前证据 | 尚不能宣称的内容 |
|---|---|---|
| 复用 Kimi/GLM 而非重复造实现 | Kimi 基点 89ced924；原 Controller 静默删除、GLM raw 导出/后处理、C23 原事件/指标/缓存/失败源；P1/P2 与各批 docs | 不等于精确论文 artifact 重现；未重跑其他模型 GPU 成果 |
| 目标引擎与上游能力 | 原候选0.20.0与最终0.20.1/132765e 的固定源码比对；Hopper top-k1024 修复；19文件局部门禁 | vLLM 原生已实现 V4、量化/attention/runtime 的源码不是已运行内核证明；完整 wheel/ELF/driver ABI 仍缺 |
| 权重与编码冻结 | 指定模型目录的64 shard大小/header、145116 key、15小资产及index哈希；目标 revision b5968e...、config 5fe4568...；原生编码源码路径与请求文本 | ModelScope master 标记不证明整个805GiB payload等于目标revision；完整文件SHA、目标实际BPE IDs与 native gold 缺失 |
| MXFP4/FP8/UE8M0/mHC | v4_state、grouped_state、loader 保留原生 raw 状态与dtype/scale/parameter subclass；mHC学习态F32，原生后处理生成派生态 | fixture仅小张量/受控Tensor，不能证明真实全模型每一位或Marlin/DeepGEMM布局正确 |
| alias/buffer/量化状态与顺序 | registered parameter/buffer与空项、同view别名、拒绝重叠/跨组引用、fresh quant_method/source签名、原生一次后处理；grouped owner/同步/可见引用扫描 | 普通闭包、C++ opaque指针、所有CUDA stream与真实全模型plain tensor覆盖未证明 |
| MLA/DSA/压缩cache运行态 | 不把缓存或stream对象当持久权重；构造/加载后重建策略，cache/prefix/MTP/eager功能范围固定 | 真实 cache绑定、压缩状态、forward和graph兼容未验证 |
| TP8/EP-off逐rank完成/失败 | 62组/rank、496 ID、8 worker命名RPC、8rank/独立设备UUID/完整回执才发布COMPLETE；失败标记否决、失败owner保留 | 真实MP/Ray传输、设备UUID与rank实物、IPC、CUDA异常/超时及full-store实际导出恢复缺失 |
| Store原生异步机制 | 完整 Model C++ CPU构建配假 pinned/CUDA，真实临时 pread；初始化/producer/dispatcher/copy失败传播、queue cancel、终态 Confirm/pool | 没有 native扩展/ABI、O_DIRECT/pinned/IPC/GPU执行证明；host确认是观察，不是租约 |
| 容量可行性 | 保留单slab拒绝；grouped逐组恢复已实现，chunk rounding计入pool | 历史H20容量快照不是新许可；97.27GiB/rank只是端点下界，不是含非专家/临时/KV的峰值 |
| 原生 SLLM 服务 | plugin四种loader、实际 backend/router/Controller/StoreManager CPU源码；本批补配置绑定、显式错误与两loader重用 | 没有真实 HTTP→Ray→scheduler→worker→业务响应；独立LLM/CPU AST不能替代 |
| 卸载/再次拉起 | 原Controller等待shutdown再kill、router starting owner/失败/计数、backend取消/close/shutdown；本批实际delete方法接到注册链 | 未运行两次真实恢复与推理；启动卡住watchdog、忙时/失败强清理、所有worker/IPC/HBM释放不完整 |
| GLM计量与失败分母 | 七阶段/首非空token/完整HTTP响应，原16指标历史重算；缓存向量、1200秒含准备、12ID顺序、journal与派生结果 | 没有新V4正式样本；缺真实cache采集、资源/干扰/JIT隔离、native gold来源和完整控制执行器 |
| 至少60秒释放观察 | release_coverage要求61样本、跨度≥60s、gap≤5s及fresh post-ack时间；合成/历史负例 | 无新V4物理释放；不能把合成时间或CPU sleep替代实测 |

累计运行源清单及实际字节SHA在 evidence/v4-registration-p3/reviewed-source-manifest.json；controller 本批是新的V4改动，不能继续引用早期“Controller未改/9文件/5pins”作为当前状态。没有修改目标vLLM计算源码，也没有加入自写GPU量化/attention内核。

## 必须按顺序进行的真实验收矩阵

| 阶段 | 最小动作与通过标准 | 资源与停止条件 |
|---|---|---|
| A 原生目标vLLM | 重新核实环境/实际ELF和kernel，取得真实目标tokenizer IDs；完整本模型加载，两类冻结请求各自native gold、自然EOS与32长度分别过关 | 经准入的8×H20/TP8、主存与IO预算；实际权重revision/ABI/内核/请求编码不明即停，不换小模型或跳专家 |
| B Store转换及恢复 | 全量原生 raw load→62组/rank导出→8个命名RPC成功→shutdown→全payload SHA→COMPLETE；fresh skeleton恢复，逐rank raw/postprocess状态覆盖与bitwise/gold | 完整Store空间、按manifest和496组rounding计算pinned需求；任一rank缺失/失败、owner不确定或容量超限即停；不撤单slabguard |
| C 批次准备与缓存 | 先注册完整grouped配置、不建backend；保留bootstrap哈希/注册成本；另一资产检查后目标全文件最后mincore；pool_hit最终全cohort Confirm；同一冻结common profile | 输入/Store不可写、Store/worker epoch不变、外部eviction受控；冷每文件0页/热全页；NFS客户端冷不代表服务端冷。无法证明就不准入 |
| D 原生SLLM四条件 | 正式command从HTTP /register前计时，min_instances=0；每次新路由仅切既定两loader；真实controller/router/actor/worker事件、首token、完整业务、fixed32分别验收 | 冻结12ID/3配对块；1200秒从preparation_begin起；准备/transport/超时/缺证据均保留原ID，不能扣除新RPC/fsync开销或用历史PASS |
| E 生命周期与再拉起 | 删除确认后采≥60s，逐进程/IPC/driver/HBM核对；同配置再次完整恢复并做业务/32 parity；host pool保留与最终销毁单列 | 相同设备和资源窗口；gap>5s、残留/未知owner或失败清理不明即停。逻辑删除/kill回执不等于物理释放 |

每一步的失败状态和原始证据必须保留。只有A/B后才能谈真实D/E；现有CPU通过不会缩短这些门槛。所有真实安装、推理/GPU与正式测量仍在后续范围，本次未执行。

## 下一项可独立推进的输入工作

核对用户指定权重目录与冻结目标revision的文件级官方manifest，先做只读元数据对照，明确哪些已验证、哪些仍需全payload IO；优先把“目标revision声明”与“本地实际权重内容证明”分开。随后才能安排完整fingerprint、真实目标编码/native gold及环境准入。当前没有用本审计自发启动上述大IO或GPU动作。
