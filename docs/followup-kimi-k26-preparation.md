# Kimi-K2.6 后续实验准备

更新：2026-10-29，Asia/Shanghai。状态：**元数据与预算初查完成，未通过运行准入**。

本文保留首轮只读准备快照。后续下载授权、实际失败及服务器ModelScope逐字节等价传输修订见[kimi-k26-download-admission.md](kimi-k26-download-admission.md)；下文“未创建目录/未读取数据区”仅描述首轮准备时点，不是后续任务实时状态。

本轮只做本机轻量分析和 qjhs003 只读核验；未创建远端目录、下载完整权重、转换 Store、读取权重数据区、建池或启动模型。GLM Docker 重构仍由 Claude 继续，GLM 的 runtime/active、金标、40 个正式 ID、35 成功/5 失败及结果均不修改。先完成 GLM 实际验证，再依次做 Kimi 原生 vLLM 参考、Store 正确性、ServerlessLLM 原生完整服务。此文是后续实验准备，不是新测量协议或执行批准。

## 1. 模型和输入冻结

| 项目 | 核验结果 | 证据等级 |
|---|---|---|
| 官方模型 | `moonshotai/Kimi-K2.6`，MoE，官方标称总参数 1T、激活 32B | 官方声明，非参数实数重算 |
| 冻结 revision | `7eb5002f6aadc958aed6a9177b7ed26bb94011bb` | 官方 API 元数据 |
| config SHA256 | `85825ca6e18cbe539eb83ee09eedfb3f4222265929f06e9f535a6d9364f55899` | 实际下载 config 原字节计算 |
| Safetensors | **64 文件，595,177,988,208 B（595.18 GB，约 554.30 GiB）** | 官方文件尺寸；未校验完整 payload |
| 索引 | **208,550 张量**，payload 总计 **595,148,192,736 B** | 索引及全部 64 文件头交叉核对 |
| 索引 SHA256 | `00a982d005d6e39b25cfcb379a7e6d57f393fc0346e70892ea4271c259c0d1e3` | 下载索引原字节计算；23.60 MB 索引仅保留本机 outputs，不入 Git |
| 文件 LFS SHA256 | 64 项已记录 | 官方声明，**不是本地完整权重哈希验证** |

所有文件头仅用 HTTP 206 Range 读取前 8 字节和 JSON header，校验 Content-Range/文件总大小、索引归属、张量数量、连续 data_offsets 和末尾边界。全部头约 29.8 MB；未读取 tensor payload，未触碰远端模型页缓存。header hash 只证明所读元数据，不证明权重值正确。

官方依据：[冻结模型配置](https://huggingface.co/moonshotai/Kimi-K2.6/blob/7eb5002f6aadc958aed6a9177b7ed26bb94011bb/config.json)、[冻结模型卡](https://huggingface.co/moonshotai/Kimi-K2.6/blob/7eb5002f6aadc958aed6a9177b7ed26bb94011bb/README.md)、[冻结部署说明](https://huggingface.co/moonshotai/Kimi-K2.6/blob/7eb5002f6aadc958aed6a9177b7ed26bb94011bb/docs/deploy_guidance.md)。

## 2. INT4 与混合精度兼容性

量化配置位于 **`text_config.quantization_config`**，不是顶层。`compressed-tensors` / `pack-quantized`，4-bit signed integer、symmetric、group size 32、静态权重；输入/输出 activation 量化为空，`actorder=null`。保留这些设置及 tokenizer、处理器、chat template、视觉配置和自定义模型代码，不把量化字段丢在 Store 外。

索引包含 `weight_packed`、`weight_scale`、`weight_shape` 各 **69,120** 项，另有 weight 963、bias 167、路由修正 bias 60 项。全部文件头给出：

| dtype | 张量数 | payload 字节 |
|---|---:|---:|
| I32 | 138,240 | 507,343,564,800 |
| BF16 | 70,250 | 87,804,535,776 |
| F32 | 60 | 92,160 |

I32 包含 packed INT4 数据及原始 shape；**不能把 I32 当成普通整数权重，也不能因为 config.dtype=BF16 就全量转换**。样例：expert down_proj 的 packed 为 I32 `[7168,256]`，scale 为 BF16 `[7168,64]`，shape 为 I32 `[2]`。shape 的实际数值属于 payload，本轮未读。

配置明确忽略 attention、shared experts、dense MLP、lm_head、vision tower 和 mm_projector 的 INT4 量化；因此模型本来就是混合精度。初始参考建议文本单请求、TP8、短上下文、禁用 speculative/MTP、greedy；Instant 模式固定 `chat_template_kwargs.thinking=false`，将原始 32-token 与自然 EOS 业务测试分开。它是待审配置，不是沿用 GLM 金标；Kimi 自己重复原生冷启动稳定后才冻结新金标。视觉权重仍完整保留，文本通过不代表多模态通过。

**已经证实的源码缺口：**

1. 现有 `patches/sllm-integration-candidate-v5.patch` 的 loader 将 FORMAT 固定为 `sllm-glm51-fp8-raw-v1`，REVISION 固定为 GLM revision，并检查 FP8 E8M0 环境。它会拒绝 Kimi，不能直接复用旧 gate 或只换模型目录。
2. 现有导出会保存 named_parameters + named_buffers，但这不证明 Kimi TP8 的融合专家、packed 参数、shape 和非持久状态完整。需按 Kimi backend 初始化后的实际八 rank 参数清单审计，检查 TP 切分、融合、复制、别名和必要 buffer。
3. vLLM v0.19.1 的 `CompressedTensorsWNA16MarlinMoEMethod.process_weights_after_loading()` 存在 `gptq_marlin_moe_repack` 和 `marlin_moe_permute_scales`；另有 FlashInfer 分支。**实际 H20 选中哪条路径未知**，不能宣称后处理仅改布局、不改 scale，或复用 GLM 后处理哈希。固定 kernel、输入 dtype、backend 选择后，建立原生/恢复同阶段的全 rank 比较；必要时逆变换比较 packed 值和 scale，说明覆盖率。审计放在性能计时外。
4. 保持官方 INT4 及必要 BF16/F32 原值，不整模反量化、不换 FP8 checkpoint、不使用 dummy/random 权重。新适配器放独立 Kimi 源码/配置，保留旧 GLM 格式不变。

源码依据：[v0.19.1 MoE 实现](https://github.com/vllm-project/vllm/blob/v0.19.1/vllm/model_executor/layers/quantization/compressed_tensors/compressed_tensors_moe.py)，本机取回源码 SHA 见证据索引。

## 3. 独立 Docker 版本候选

官方冻结部署说明明确推荐经人工验证的 **vLLM 0.19.1**，给出 H200 单机 TP8 示例；这不是 H20 实测证明。采用以下**源码约束候选**，未构建镜像、未安装、未证明可运行：

| 依赖 | 候选/来源 | 当前状态 |
|---|---|---|
| vLLM | 0.19.1 stable，禁止 latest/nightly 漂移 | 官方推荐；待镜像 config/manifest digest 冻结 |
| CUDA / Python | v0.19.1 Dockerfile 默认 12.9.1 / 3.12 | 源码默认；不是已有二进制环境证明 |
| PyTorch / torchvision | 2.10.0 / 0.25.0 | v0.19.1 requirements/cuda.txt |
| Transformers | **4.57.1** 候选 | 满足官方 `>=4.57.1,<5.0.0`；PyPI 版本存在，未安装验证 |
| compressed-tensors | 0.15.0.1 | v0.19.1 requirements/common.txt |
| flashinfer-python / cubin | 0.6.6 / 0.6.6 | v0.19.1 requirements/cuda.txt；实际是否选用未知 |
| MoE kernel / Triton / NCCL / 完整 wheel 集 | 尚未冻结 | 镜像元数据、兼容性探针及运行证明缺项 |

GLM 已恢复镜像不是此候选镜像，不修改它的包。新镜像后续按平台 manifest/config digest 固定并保留备份；此时不拉镜像。vLLM 原生支持不等于 ServerlessLLM 支持。

依据：[CUDA 依赖](https://github.com/vllm-project/vllm/blob/v0.19.1/requirements/cuda.txt)、[公共依赖](https://github.com/vllm-project/vllm/blob/v0.19.1/requirements/common.txt)、[Dockerfile](https://github.com/vllm-project/vllm/blob/v0.19.1/docker/Dockerfile)。动态 recipe 仅作参考，不覆盖冻结官方指南。

## 4. 实际存储和内存预算

qjhs003 2026-10-29 **00:57:52+08:00** 只读快照：`/data/brick1` 是 `/dev/nvme1n1` 本地 XFS；boyjiang UID/GID 2147，父目录 `/data/brick1/boyjiang` 为 0700，Kimi 目录不存在。建议未来专属根 `/data/brick1/boyjiang/kimi-k2.6/`，下分 checkpoint、store、images、runs、evidence，保持原目录/权限，不在本轮创建。

| 资源 | 数值 | 等级/限制 |
|---|---:|---|
| brick1 总容量 | 3,838,880,616,448 B | 实测元数据 |
| 已使用 / 可用 | 1,678,270,988,288 / **2,160,609,628,160 B** | 实测；旧 GLM 资产已在 used 内，不能重复扣减 |
| checkpoint + 等大 Store | **1,190,355,976,416 B** | 估计；Store 并非已测等大 |
| 额外磁盘保留 | **300 GiB** | 本提案保守预留，非已批准配额 |
| 上两项后余量 | 约 **604 GiB** | 估计；还须容纳 TP 复制/对齐、镜像、日志、临时文件 |
| 主机总 / MemAvailable | 2,065,109,635,072 / **1,983,475,892,224 B** | 快照，不等于独占或可全用 |
| 一个等大 Store 池 | 约 **554.30 GiB** | 估计；实际锁页、TP复制及 allocator 开销未知 |
| Store 池 + checkpoint 页缓存 | 约 **1,108.61 GiB** | 估计，不含服务、源 CPU 副本、OS 和中间重排 |
| 再加一整份 CPU 源副本 | 三份共约 **1,662.91 GiB** | 估计；快照 MemAvailable 剩约 184 GiB，不满足本提案 300 GiB 余量 |
| 与 GLM 716 GiB 池同时驻留 | 超出上述安全预算 | 不允许；两模型大池错峰且核验释放 |
| GPU / CPU / shm / NUMA / 锁页限额 | 本轮未重新测量 | 未知；不得用历史 8 卡信息作为本轮准入 |
| 磁盘吞吐 / 共享 I/O / RDMA | 本轮未测 | 未知；不做 dd/fio 或整盘扫描干扰共享节点 |

**当前结论：磁盘初算可行，运行内存预算未闭合。** Store 预算必须来自实际 TP8 参数字节总和、别名/复制和对齐规则，不能默认等于 checkpoint。完整下载前先完成审阅及容量再核验；转换前再次确认两份加峰值临时文件与预留均可容纳。不在根盘/NFS凑空间，不建立额外下载副本。导出优先逐 rank/分块，禁止未经预算让源模型、整份 CPU 字典、池和热缓存同时驻留。无需常驻池的原生参考阶段仍须预算读取页缓存及临时重排。

## 5. 后续准入和交付顺序

1. **准备审阅**：复核本轮输入/预算，完成独立 INT4 适配设计和新 Docker 锁定清单。先解决 TP/重排语义，不碰 GLM 冻结包；当前没有运行批准。
2. **资产获取**：审阅通过且 fresh 容量满足后，按冻结 64 项清单单份下载，逐文件核验 LFS SHA、完整元数据及全部张量。不在正式计时期间下载/编译。
3. **原生参考**：GLM 实际验证及释放完成后，重新核验预约/实际八卡占用、内存/NUMA、共享 I/O，部署新 run 独立宿主截止守护并验证 probe；完整预算须覆盖初始化、失败清理、60 秒观察与取证。未给出经审预算前不指定可执行时长。重复原生冷启动、固定新金标，不能借用 GLM 结果。
4. **Store 组件正确性**：同 checkpoint/TP/推理设置，保存读回全部八 rank，packed 值、scale、shape、混合 dtype、配置完整；校验重排阶段，明确组件通过范围。转换/缓存成本单记。
5. **完整服务路径**：经过 ServerlessLLM 原生实例创建、业务推理、删除、再次拉起，分别验证自然 EOS 与固定 32 原始 token，删除后保留 60 秒观察并核验完整退出。
6. **正式比较另审**：Kimi 独立协议、预登记、账本、图表和失败分母；四种条件若某条只能组件实现，明确标识。不得把新的 Kimi 结果写入 GLM 原 40 ID，不能重试历史失败或跨模型合并。

本轮拟修改只有本文与 `evidence/followup-kimi-k26/` 的小型证据。没有改运行源码、调度/守护、正式 launcher、GLM STATUS 或历史结果；没有联系其他任务或新建定时任务。
