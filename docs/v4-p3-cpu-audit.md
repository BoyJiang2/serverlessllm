> 历史快照：本文描述 d592066 时的状态，后续路线选择、grouped 实现和终态修复已推进。当前状态与验收矩阵请读 [P3 当前审计](v4-p3-readiness-audit.md)，不要按本文第5节重复询问已采纳的路线。

# SLLM × V4-Pro：P3 CPU状态总审计

2026-11-06。统一检查的源码HEAD为 `d592066addc2d97e1d6ead3de2801fd6cfff17c5`，独立分支 `sllm/v4-raw-store-p3-20261106`；基点 `89ced92416f8342a03c3df83670249cd626ac0d2`。本文随后作为文档/证据提交，不改变被测运行源码。当前状态是**CPU契约通过、核心缺口未闭合**，不是P3整体完成或GPU READY。

## 1 当前CPU证据

在qjhs002现有Python3.10.12与g++上，以一个unittest suite执行98项，98运行、0失败、0错误、0跳过。没有安装推理依赖、启动真实Ray/Store/model，或构建完整native扩展。包含实际源码AST、纯stdlib状态/哈希处理、固定上游源码函数及C++ CUDA调用替身；不包含真实float8运算、GPU IPC或物理显存证明。

| 用例 | 数量 | 证明范围 |
|---|---:|---|
| V4 allocation/owned-load | 1+5 | 部分分配回滚、owner先于dispatch建立、confirm后返回与未知失败quarantine控制逻辑 |
| V4 loader/state | 10+11 | 实际loader接点与一次后处理控制、manifest/alias/buffer/空项、8rank COMPLETE、容量拒绝等CPU路径 |
| V4 backend/router/startup | 14+9+10 | 编码调用与请求/取消/shutdown、并发槽位/计数、启动失败传播及ready发布CPU路径 |
| V4 evidence | 16 | 历史GLM全部16个metrics重算、固定目标EOS分支、业务与fixed32分离、60秒时间覆盖负例 |
| 既有blocking-confirm/error/event-loop/IPC-close | 7+3+2+1 | 旧接口顺序/异常、异步server等待、原生close尾部状态控制 |
| 既有native-ready lifecycle | 9 | FakeRay下Controller删除/再次注册、静默实例删除及失败owner保留 |

本轮首次汇总入口未把仓库sllm_store源码目录加入sys.path，出现4个导入错误，unittest只发现79项；补齐原本所需源码搜索路径后重新跑98项通过。保留initial日志/receipt，不将入口错误包装成产品缺陷或隐去。运行入口和全部日志/receipt见 `docs/evidence/v4-cpu-audit-p3/`，可以直接CPU重放。

98项通过**没有覆盖或解决**另外CPU探针已复现的late failed Confirm缺口。不能把一个集合通过解释为所有失败路径已闭合。真实V4全模型/原生服务/物理释放资格在receipt中明确为false。

## 2 原目标逐项判定

| 原要求 | 当前实际证据 | 判定/缺口 |
|---|---|---|
| 核查并复用Kimi/GLM、区分原始SLLM机制 | P1/P2三条谱系、实际GLM raw loader与C23源，Kimi基点/已有Controller修复复用 | 机制与设计已交付；不声称精确论文artifact复原，也未重跑其他模型GPU成果 |
| V4/H20/版本与必要修复 | 0.20.0与0.20.1固定源码比对、Hopper top-k绕行审计；目标B1=`132765e...` | 静态方案成立；没有目标环境/内核运行证据 |
| 固定权重/config/tokenizer/编码与功能 | model revision=`b5968e...`、15资产哈希、自然EOS业务和warm32分离、TP8/EP-off/2048/eager | 输入已冻结；真实目标BPE IDs、原生完整输出金标、实际所有kernel/ELF身份仍缺失 |
| MXFP4、FP8、UE8M0持久状态 | raw metadata/loader、dtype枚举和CPU检查；上游转换源码已审 | 未取得全模型真实反射与bitwise往返；不能以dtype字符串/受控tensor替身证明精度正确 |
| mHC/MLA/DSA/压缩cache重建 | 学习参数/构造/派生状态与冷cache分类、原生一次postprocess设计 | 没有全模型对象引用、cache绑定和forward证明 |
| aliases/buffers/quant_method/后处理顺序 | CPU expected状态检查、alias/empty/buffer、参数保留与原生后处理控制 | 真实跨组alias、非persistent/plain对象、kernel引用与异步stream寿命未验证 |
| 逐rank保存/恢复/完成 | CPU manifest、8rank COMPLETE与copy确认接点 | 没有全模型实际8rank manifest、设备UUID/局部专家归属及GPU故障注入 |
| 完整Store可恢复 | 单slab raw+Marlin专家必要下界191.40GiB/rank，已知H20容量140.40GiB | 当前表示明确不满足容量，guard拒绝；不是仅缺benchmark。分组/处理后/TP16待选择 |
| Store失败确认 | GpuReplica close状态已有CPU尾部检查；新探针复现FreeGpu删除失败条目后迟到Confirm等待 | 失败契约有实际缺口，已按AGENTS请求语义澄清；不得标完整失败清理通过 |
| 原生SLLM服务 | 实际backend/router/Controller函数CPU验证、V4编码接入 | 没有真正Ray注册→冷请求→全rank执行→正确响应；完整phase事件尚未迁入 |
| 卸载与再次拉起 | CPU静默删除与mock重新注册、shutdown失败owner保留 | 忙时删除/启动中断/watchdog、失败强制清理、worker/IPC/driver/HBM释放及再次GPU推理未闭合 |
| GLM指标/缓存/失败/释放对齐 | C23 raw事件重算匹配全部16metrics、12个V4 ID冻结、缓存和失败统计方案 | 数据定义已复核；没有V4正式样本/缓存准入实物、完整monitor/统计/cleanup runner |
| 至少60秒观察 | 历史GLM raw idle回放与覆盖/陈旧样本负例 | 只是历史回放和时间覆盖；没有新V4物理释放证据 |

## 3 当前补丁边界

相对选中Kimi基点，运行/集成只改9个文件：backend、roundrobin router、Store torch入口、checkpoint.cpp与checkpoint_py.cpp、Store setup、v4 loader/state/source pins；另有1个benchmarks/v4_evidence.py离线计算库。逐文件Git blob/SHA见本审计reviewed-source-manifest.json。现有Controller delete等待逻辑已在基点，不重复计为新增V4改动。没有修改目标vLLM模型计算源码。

source guard目前约束5个选中Python接点，不等于完整wheel、全部量化/内核源码或ELF ABI身份。native capability只是一项接口资格；旧ELF被拒绝不证明新ELF已构建。现有源码同步/发布、CPU编译替身和GPU运行三者保持区分。

## 4 已冻结的运行门槛

- A原生目标vLLM：先独立环境/实际ELF与kernel资格、真实tokenizer IDs、原生业务与fixed32金标；当前未执行。
- B小规模Store：CPU控制路径已有证据；真实FP8/UE8M0 bitwise、CUDA owner/IPC和全失败路径仍待运行及确认契约修复。
- C完整Store：必须先换掉已证明不满足容量的单slab表示，或重新选择拓扑；不能取消guard、缩模型、换精度或删专家绕过。
- D原生服务：必须真实走Controller/router/actor/backend/worker，完整phase事件、失败/取消与业务验收；独立LLM脚本不替代。
- E生命周期：两次真实恢复/业务，卸载后至少60秒、gap≤5秒，检查本实例所有进程/IPC/物理HBM；pool保留和最终Store销毁单列。

P3授权仍仅独立分支与CPU契约验证，不安装推理环境、不运行GPU。上述运行门槛没有因CPU总数增加而降低。

## 5 两项待答决策及后续动作

1. Store表示：推荐评审61专家层组+remainder的grouped raw（496个identifier），但97.27GiB/rank只为专家共存端点下界；另有非专家、临时、cache及pinned rounding预算。处理后Store或TP16同样需要重新冻结契约与代价，不能默选。
2. 失败回执：已提供“失败副本保留至Confirm完整终态”与“独立失败账本”两种语义选择。两者均须覆盖条目创建前失败、copy/IPC错误、server内部重试、迟到/重复/取消Confirm；回执不确定时不能删除失败状态或释放未知owner。

两项问题仍待用户回复，未把自动goal continuation当作选择或批准。依赖这些决策的实现保持未启动。路线明确后，在当前授权内继续对应CPU实现/负例；安装环境、完整原生构建或GPU运行需进入后续范围，不能将本审计视为已经获准或完成。
