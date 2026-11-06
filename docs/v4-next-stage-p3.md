# SLLM × V4-Pro：P3 后的执行单与打包阻塞

2026-11-06，父提交966fae570d9353bac0d964c2b285f33f40b9f37a。此前完整权重64/64及运行输入16/16已经通过；本轮仅做源码约束和主机只读检查，没有环境安装、源码编译、Torch/vLLM导入、GPU查询/运行或模型读取。

## 已核实的下一项实际问题

| 现有安装入口 | 旧要求 | V4 固定目标 | 结论 |
|---|---|---|---|
| requirements-worker.txt | vllm==0.11.2 | vllm==0.20.1/132765e... | 不能同时成立 |
| sllm_store/requirements-build.txt | torch==2.9.1 | torch==2.11.0 | 不能同时成立，旧扩展不能据此获得新ABI资格 |
| requirements.txt | openai==1.52.0 | 目标common要求openai>=2.0.0 | 不能同时成立 |
| requirements.txt | pydantic==2.11.5 | 目标common要求pydantic>=2.12.0 | 不能同时成立 |

setup.py确实把requirements.txt写入服务包的install_requires，worker extras追加requirements-worker.txt；Store setup.py也使用自己的requirements.txt。仅换一份pip输入或--no-deps无法消除已安装包元数据中的矛盾。pydantic_core等传递依赖还须重新求解；本轮四个互斥检查不是完整依赖可解性证明，也不声称已经生成0.20.1锁文件。

Store pyproject的构建隔离torch>=2.7可能另取版本，旧builder是CUDA12.1.1/Python3.10；这不能作为目标Torch2.11/CUDA13/CPython3.12的新扩展身份。现有cached Kimi镜像仅可复用此前CPU编码结果，不能作为目标推理环境。

现有source requirement及目标0.20.1 common/cuda文件原始字节、SHA和packaging版本交集断言结果保存在evidence/v4-next-stage-p3/。保留旧模型入口，本轮不改全局依赖，不用忽略pip check或假装no-deps解决冲突。

## P4A 提案：隔离环境与无GPU构建验证

这是需要新范围确认的具体下一阶段，仍不启动模型或使用GPU。首选为V4专用构建/打包配置与可区分的wheel身份，使服务和Store的METADATA表达实际V4依赖，同时保留原入口供Kimi/GLM复用。实现前先确定这种独立包配置方式；不要直接升全仓依赖。

1. 固定V4包元数据入口，补CPU元数据测试，要求上述四个冲突消失且旧入口内容不变。重新解Linux x86_64/CPython3.12完整runtime/build依赖，选定准确wheel URL、build tag、hash、rootfs digest及Python patch身份；不能把旧vllm0.20.0锁改个版本字符串当新锁。
2. 以vLLM固定源码132765e3560659ff63ebd236203672e991b70e08为主路线；先固定完整源码/submodule闭包与toolchain，再构建。PyPI 0.20.1两个build的既有元数据可作备选，但单有版本字符串不证明对应源码/二进制身份。前序build1索引SHA保留，不冒称已经下载或验过主体。
3. Torch2.11.0及实际CUDA variant、FlashInfer0.6.8.post1、TileLang0.1.9、TVM-FFI0.1.9等按既定目标闭合；Transformers沿用P2候选5.6.0，重新解兼容依赖。此前CPU编码镜像5.5.1仅是独立输入证据，不自动改写目标环境。
4. 全部在无设备的独立构建容器执行，显式runc、无GPU device requests、NVIDIA_VISIBLE_DEVICES=void、CUDA_VISIBLE_DEVICES空，显式sm90编译目标，不以探测硬件决定架构。网络仅在受控软件获取阶段开放；不得挂载完整权重目录、启动LLM、分配KV或改变现有服务/驱动。Store/vLLM必须同Torch/CUDA/toolchain构建，防止PEP517隔离另装Torch；需要no-build-isolation时先完整固定构建依赖。
5. 验收真实wheel/METADATA、全量依赖锁和pip check；在同环境检查实际导入、ELF依赖/符号、Torch CXX ABI和CUDA库路径。只挂载两份tokenizer JSON做真实目标tokenizer CPU复验，业务30/fixed32 43输入IDs须与已归档候选完全一致。编译/导入不证明GPU内核或真实Store IPC通过。
6. 任一依赖冲突、浮动下载、源码/构建身份不明、错误Torch/架构、pip check失败、CPU导入需真实GPU或预算超限就停并保留日志；不得临时挂设备、回退旧引擎、升级未冻结依赖或启动模型来绕过。

拟定资源上限：构建4 CPU/48 GiB内存、NVCC并发1、单次8小时；下载/编译临时目录和缓存放/data/brick1/boyjiang/sllm-v4-pro-build，预算100 GiB。代码/脚本/证据保持/home/boyjiang/sllm-v4-pro，最终小型wheel/锁文件可归档/home；不把805 GiB权重复制进/home。Docker层预算60 GiB且brick0必须保留至少128 GiB空闲；预算不是预订或已分配资源，实施前重新核查。

## 只读主机条件与来源

qjhs2当前/proc记录驱动580.95.05，主机PATH未发现nvcc；Docker root为/data/brick0/docker。只读快照可用空间：brick0约284.7 GiB、/home NFS约431.6 GiB、brick1约2.19 TiB；主存可用约1.83 TiB。这些是瞬时proc/statvfs数据，不是预订、GPU容量/空闲或未来可用承诺。原始精确字节在host-readonly-preflight.json。

NVIDIA官方CUDA13.0资料列13.x小版本兼容驱动范围>=580；主机版本处于此范围。这仅是静态初筛，不证明选定toolkit/wheel/PTX及H20实际内核通过，也不要求本轮升级主机驱动。[NVIDIA CUDA13发布说明](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/)

目标版本官方安装文档说明PyTorch/CUDA组合与wheel不同时应源码构建；本执行单采用固定源码身份，不照搬文档里的latest/nightly或其他架构示例。[vLLM0.20.1安装说明](https://docs.vllm.ai/en/v0.20.1/getting_started/installation/gpu/)

Store CMake已有TORCH_CUDA_ARCH_LIST路径，经Torch/CMake flags提取后过滤支持集合，因此P4A先显式9.0并核对实际日志；发现新Torch不再生成现有解析所需-gencode时停止，不预先全局重写架构逻辑。

## GPU 阶段另行准入

P4B才是原生完整V4-Pro加载：经准入的8×H20/TP8、同设备身份、内存/干扰/容量及源码和二进制证明齐备后，运行冻结2048/seq1/BF16/eager/prefixoff/EPoff/MTPoff业务自然EOS和fixed32，各自形成输出gold。这里不分配正式12个实验ID。

P4C在原生通过后才做完整62组/rank raw转换、8rank成功/失败契约、496 ID准备与fresh恢复、逐rank raw/后处理覆盖及bitwise/gold/峰值；任一失败保留owner并否决COMPLETE。P4D再进入原生SLLM四条件、卸载、同配置再拉起及至少60秒物理释放，计时/缓存/失败分母与既有GLM定义一致。完整hash通过不能替代这些门槛。

## 当前范围边界

用户此前批准P3独立分支和CPU契约验证，并明确“暂不安装推理环境或运行GPU”；之后只新增了CPU串行完整权重I/O授权。环境安装/构建不是该I/O批准的隐含延伸。用户AGENTS还要求发现代码矛盾时暂停并澄清；因此本轮完成可审阅问题证据和执行单后，等待选择是进入P4A无GPU环境构建，还是先仅修V4包元数据/CPU检查而继续不安装。无回答不作为批准。
