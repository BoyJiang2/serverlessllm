# Kimi 独立 worker：下一步准入清单

2026-10-30。源码准备草案，不是资源分配、预约、运行gate或可执行部署命令。只使用qjhs003；旧GLM模型、40个ID、金标、失败、runtime/active均冻结。

## 当前已接与尚未接

11/2 12:14现有pip26.1.1的dry-run解析3304fdcd/3f85133到原540秒后TimeoutExpired，业务失败、未安装/未产版本锁；不延长或重复服务器任务，不据此认定依赖无解或归因网络/回溯。运行中一次独立采样约86.75MiB/2GiB、CPU0.05%、3 PID（非峰值），无GPU；新SSH确认uv缺失及pip超时两run均容器空/timerinactive。实际暴露原超时异常分支未保存stdout/stderr，历史流无法追补，已明示缺口。现有body修正保留各32KiB原始base64/文本、总长度/截断标志及实际timeout，并在try前初始化计时避免早期pip --version超时掩盖原错；bodyed2d2190…的5项本机实际handler AST控制通过，旧失败/初版补丁均留history。未再远端执行；最新bodyed2d2190…限定超时诊断只读复审PASS，不批准重复远端解析。下一项评估本机已有UV0.11.28的Linux目标有界解析，不新装工具/环境；仍无兼容锁、安装预算/ABI或GPU准入。

11/2 11:50官方vLLM0.19.1 wheel资产准备通过：433132101B/完整SHA71a87f46cafab4489c69a5c5c83b870d0235e5694d8222303d460576293dc719，已落原qjhs003 downloads，未解包/安装。服务器253秒仅2.55MB（约10KB/s）后精确身份pidfd停止；本机180秒收到428670976B到期失败；一次有界仅余4461125B续传9.568秒，全SHA通过。远端原断点前缀一致后单次SFTP reput，独立SSH全SHA/UID/inode/ZIP目录验收再排他发布；随后核本地绝对路径及SHA删除433MB，两个本地大文件路径均不存在。完整失败/续传/清理证据留cpu-probe，不重写为一次成功。metadata控制356811b3已实际采集并释放：vLLM直接13缺失/11版本冲突，Torch自身另5冲突，不能直接相加当独立包数；Numba0.61.2还要求NumPy<2.3而现2.3.5，不是完整传递闭包。限定只读实际复审通过资产/清理与metadata范围。下一项固定完整兼容依赖及安装空间预算，再审现有差异层CPU安装/导入；当前环境/GPU/模型仍未准入，不重做native CPU探针，不另造包装或环境。


### 11月2日实际依赖缺口（固定53f661镜像，metadata控制356811b3）

| 范围 | 当前证据 | 后续要求 |
|---|---|---|
| 官方vLLM 0.19.1 wheel | x86_64/CPython abi3；433132101B，官方SHA71a87f46…；Range读取305135B，四个关键源文件与固定SHA一致 | 完整wheel433132101B/SHA已于11:50独立验收，本地中转删除；安装/导入仍未执行，目录声明解包1172403610B不是实测解包 |
| vLLM直接依赖 | 13项未安装、11项版本不符；vLLM本身也未安装 | 固定完整依赖闭包和下载/安装空间预算，不以单包安装完成称ready |
| 关键冲突 | Transformers5.3.0在排除范围；FlashInfer python/cubin为0.6.7.post2而需0.6.6；torchaudio/vision为旧Torch配套 | 在本任务差异层统一匹配，不修改共享基础镜像或他人容器 |
| Torch自身依赖 | CUDA bindings/cuDNN/NCCL/NVSHMEM/Triton共5项不符；Torch本体2.10cu129匹配 | 核完整依赖及动态库/实际ABI，不把之前native普通导入升级为完整环境通过 |
| 传递约束 | 官方numba0.61.2要求numpy<2.3,>=1.24及llvmlite<0.45,>=0.44.0dev0；现NumPy2.3.5不满足 | 此为新增已证传递冲突，尚未完成全部传递依赖/extras检查 |
| native辅助依赖 | accelerate1.12只读wheel辅助导入已实证；镜像内仍未安装 | 两种状态明确区分，不复用临时receipt充当运行证明 |

所有依赖计数按来源分组，不直接相加宣称独立包数。metadata采集成功不等于版本全部满足；本次无Torch/vLLM导入、无GPU/模型/Store。原完整编译、CPU保存和CPU身份绑定证据仍有效，但只覆盖各自范围。Numba元数据来源：https://pypi.org/pypi/numba/0.61.2/json 。


11/2 11:28真实CPU身份绑定通过：run44ef8ef8/code620543c，固定53f661镜像/994772cc及30132b6d两库，冻结wrapper及实际native_record_call._bound_runtime完成builtin归属、checkpoint映射inode和Store磁盘摘要核验；坏外部SHA、compiled=false及Store SHA三个负例实际拒绝。accelerate1.12.0仅从已核380935B只读wheel临时导入，未pip/改镜像或新环境；139359B必要源码仅容器/tmp临时存在，释放后消失。独立SSH核本次和首次缺accelerate失败run均容器空/timerinactive，8个GLM SDK文件及两库/wheel SHA不变。限定实际复审允许native_cpu_runtime_binding=true；mapped机器码/函数地址、完整功能ABI/liveStore、CUDA、原生恢复、八rank/完整模型/服务仍未验证。原失败保留，不复用本次临时receipt作为后续运行凭证。

11/2 11:14原生CPU保存对照实际通过：run9c6b6ae6/codeba5c16c，原_C.save_tensors与未改stream_writer对5个canonical+1alias的packed INT4/int32、BF16/FP16/FP32有效位及offset一致；源shape/stride/dtype未改，两文件均8192B，未比较未定义padding。独立SSH完整/逐payload哈希、uid2147/mode0600和自身容器空/timer inactive均通过，只留原downloads内8KiB证据。限定实际只读复审允许native_cpu_save_comparison=true，不证明native index、原生恢复、CUDA、八rank/完整模型/服务；CUDA未初始化。原始错误候选和NEEDS_FIX保留。原生writer申请1GiB虚拟buffer，Python单块上限8MiB；本次均未测RSS峰值。

11/2 11:00实际闭环：native补丁49b2已完整配置、编译及链接两个原target（code00c3de8/run5e597e33），以静态PIC glog修复首版临时动态依赖。固定53f661ba镜像、Torch2.10cu129/CUDA12.9、单并发1CPU8GiB；_C 395376B/SHA994772cc…，_checkpoint_store 823584B/SHA30132b6d…，详细原始哈希/33输入/日志见native_patch/cpu-probe.json。随后独立干净容器run0cd27f9d（codeb934e03）只挂两库，无源码或构建目录、无GPU/网络、1CPU2GiB，两个普通import均成功且CUDA未初始化；自身容器及timer独立复核已释放。实际只读复审允许ledger compiled_native_store=true及限定CPU导入声明。GPU、活跃Store、完整checkpoint、八rank和原生服务仍未验证，2×预算拒绝不变；依赖合同差异/vllm缺失未解除。旧失败和首版glog缺口完整保留。

以下构建方案和阻塞为历史记录，不覆盖上述实际结果。

### native完整编译的已核输入与最小执行方式（11月2日）

已有SDK的31个C++文件中30个按LF比对与冻结source-v7相同，唯一model.cpp须只读叠加候选49b2；raw及LF SHA全部在cpu-probe.existing_sdk_native_input_comparisons，不把换行归一化称原字节相同。已审21178B候选传到原downloads/kimi-native-model.cpp，校验49b2，不改GLM源码。两个原CMake必需依赖已取不可变commit存原downloads：glog b33e3bad…归档193526B/SHA a4cd355a…、googletest b796f7d…归档864010B/SHA2681de8c…；总解包4775586B，尚未解包，逐成员检查路径/大小/类型。没有旧build或_deps缓存可复用；不再复制SDK或重复下载这两份依赖。

拟复用已核53f661ba…镜像，原SDK目录及单文件补丁分开只读挂载到/src和/src/csrc/sllm_store/model.cpp，两个归档文件只读挂载；用已有/tmp作临时构建位置，无新host工作目录/checkout/venv。先解包校验过的小依赖到容器/tmp的临时子目录，然后原CMake -S/src -B/tmp -G Ninja -DSLLM_STORE_PYTHON_EXECUTABLE=<actual-sys.executable> -DBUILD_SLLM_TESTS=OFF -DFETCHCONTENT_FULLY_DISCONNECTED=ON -DFETCHCONTENT_SOURCE_DIR_GLOG=<实解包路径> -DFETCHCONTENT_SOURCE_DIR_GOOGLETEST=<实解包路径> -DCMAKE_BUILD_TYPE=Release，原Torch实际prefix由SDK函数取得，不用setup安装GLM插件。显式TORCH_CUDA_ARCH_LIST=9.0及四类CPU线程1；--build /tmp --target _C _checkpoint_store --parallel 1。必须保存真实配置/编译/链接日志、最终target架构、candidate输入SHA及库SHA，不能以CMake生成成功冒充完整编译。此为待执行约束，具体命令/宿主守护与预算仍需按同一执行body审阅，尚未运行CMake。

编译/普通extension import只用CPU、runc无GPU设备/网络，拟1CPU8GiB且临时产物增量预算2GiB（规划值，非已测峰值），成功保存必要.so与小证据后删除仅本次容器/tmp构建产物/停止运行，失败保留有界原日志。不得修改GLM原build或清他人文件，不强制GC/free未知CUDA指针。普通导入必须再核CUDA未初始化；缺少driver共享库或其他链接依赖时原样记录，不能调用CUDA来“验证”。当前nvshmem/cudnn/nccl/Triton等合同差异及vllm缺失依然存在，编译组件结果不作为全冻结环境或服务准入。

11月2日实际更新：目标Torch2.10cu129已装到原0202镜像的本任务差异层；普通import在线程1/无GPU设备下实际通过，CUDA未初始化。镜像53f661ba…独立核29原层共享、只增2.13GiB，自身临时容器/timer已释放。已有Torch安装阻塞已解除；native补丁仍未完整编译或导入，NVIDIA/Triton版本合同尚有差异/vllm缺失，不能宣全冻结环境ready。完整失败/成功/静态与实际只读复审在cpu-probe，NEXT首部为下一项；不重复下载或另建环境。首次139与后续成功分开，不追认历史故障根因。

11月1日新CPU/内存授权后的实际进展：已审一次性CPU metadata探针在已有0202镜像成功执行并释放，启动前独立user timer绑定同socket/完整CID，内60s外300s，实际目标约0.164s、exit0、无nvidia设备；独立SSH再核同label空、timer inactive及alan不变。实际复审只确认CPU版本/释放。远端确有nvcc12.9.86/CUDA头/CMake3.28.3，但Torch2.9.1+cu129且无vllm，不符合冻结2.10.0+cu129；并未执行ABI调用或编译。此前本机缺CUDA工具链的证据仍有效，但不再外推远端没有CUDA。原文/收据/源码SHA和新限定结论见cpu-probe；不得据版本probe声称完整build或GPU通过。

2026-10-31 23:14实际阻塞复核：连续4e881f9、e906053、本轮均缺可用完整native CUDA构建链。现有WSL3.10本轮实查Torch不可import、nvcc/ninja/CUDA_HOME为空、两标准CUDA头路径不存在，cmake/g++有；WSL单次固定原文读取Connection reset，未获得utils。所有新证据仅标准库metadata/网络小文件，没有安装或远端运行。已有五项只读审计没有新已确认本机CPU核心格式缺陷；native/GPU/完整模型验证缺口仍在，不以进一步通用启动封装代替证明。恢复需要现成匹配工具链/完整来源及其授权CPU内存范围，再审有界构建；构建无需先占八卡。目标保持未完成，原范围/拒绝/失败不放宽。

## 2026-10-31 原goal五项只读审计

审计基于5d2c450，整体结论INCOMPLETE，不是完整R1或运行批准。限定静态检查未找到一个已证实、可本地CPU复现的新增核心格式/映射错误；这不证明没有缺陷，也不应为制造进展继续扩写通用启动包装。

| 原目标 | 当前软件/CPU证据 | 必需的未证项 |
|---|---|---|
| 1 审阅收尾 | 修订SHA、原失败/回归与限定复审可查；mapped inode和runtime范围分开 | 当前完整运行包R1、实际构建/ABI/活跃Store及运行准入 |
| 2 packed INT4/scale/shape/混精度写读 | native_index、cpu_restore、native_payload及实际C++读片段保留位/布局/alias/配置 | 完整native库的实际O_DIRECT/CUDA IPC/Confirm，完整checkpoint和八rank |
| 3 分块与临时内存 | writer/reader/hash失败的自身引用清理，不GC或free不明DMA | 8MiB不等于RSS；源模型/CPU目标/池/缓存/重排/失败所有者的共同峰值 |
| 4 TP8融合专家及后处理 | 冻结208550源名到1428目标的条件映射、alias/buffer/Marlin辅助与一次后处理 | 真实八rank shape/value/scale、Marlin重排、混设备安装和最终CUDA状态 |
| 5 交付与八卡边界 | 源码/CPU测试/失败证据及未证项有记录；native plugin已有实际候选调用，source登记v2已绑输入 | vLLM实际spawn、parent/容器消费/守护、Kimi金标及原生服务/释放 |

CPU/meta不兼容不能用于断言CPU/CUDA.data必错：本机Torch2.10的TensorImpl.h:2070起明确Dense CPU/CUDA兼容；这仍不是实际CUDA安装/释放证明。mixed工厂的2×拒绝保持，逐记录CPU所有权设计不足以解除它。下一关键证据为匹配工具链的完整native构建和导入，再是最小真实CUDA/Store控制，最后一份源模型TP8完整导出、八rank审计及金标/服务；不占八卡等待编译或审批。

### 已钉住的少量构建输入

后续CPU控制直接从已钉setup.py的AST取出未修改的cmake_build_ext/remove_prefix，只替代build_ext基类/extension描述和subprocess，分别执行Ninja存在/不存在两支。MAX_JOBS=1时两支的两个目标仍传裸-j，Ninja分支仍传compile=8；四个build argv及两份configure argv留在cpu-probe.upstream_build_method_controls。没有执行CMake、编译器、顶层setup或Torch导入，不是实测编译并发。受预算的实际构建不能直接采用默认setup路径；拟从原CMake直接配置并以显式--parallel 1构建两个目标，ABI/依赖/架构/宿主硬预算仍需完整验证与审阅，当前不是可执行recipe。

本轮获取utils.cmake的目录API超时；一次独立raw回退SSL握手超时，web读取亦cache miss，未取得文件，不重试循环。失败追加cpu-probe.upstream_build_followups，先前四文件及原失败保留；未据函数名假定能在无可见GPU环境设置架构，也未冻结glog依赖、创建工程、安装工具链或连接远端计算。

通过冻结SLLM revision 9f50241…的目录API和Git blob API取得CMakeLists/setup.py/pyproject/requirements四文件共15367B，Git blob SHA与SHA256均核验，原文保存在已有native_patch/cpu-probe.json，没有新目录、源码checkout、依赖安装或编译。CMake要求3.20及CUDA/HIP、Torch/Python；该文件支持Python3.9–3.12，不能把Windows3.13 CPU测试环境当构建环境。

原pyproject与requirements只约束torch>=2.7.0，不固定本实验2.10.0+cu129。setup的CMake build传未带数字的-j，Ninja compile pool写8；不能假定MAX_JOBS已控制，实际方案须显式锁ABI/禁止默认重解依赖，并审查所选generator的并发与硬内存上限。CMake会FetchContent glog v0.6.0及googletest v1.13.0；后者已解析commit b796f7d44681514f58a683a3a71ff17c94edb0c1，glog ref解析本次超时未固定。cmake/utils.cmake及完整工程仍未补齐，不称已有完整可编译包，不产生compiled=true回执。

2026-10-31 source宿主登记升级v2：load_watchdog_inputs强制worker_input_sha256，重用worker输入读取器比对root/plan/原锚/完整schedule和source/target identity；旧source startup v1拒绝，旧target v1仅保留归属/清理描述，P0登记不变。CPU真实元数据正反例与77项原生WSL控制通过。实际source container Cmd/Env/resource profile绑定及parent创建/启动仍未完成，亦未安装guard；a581实际源码包CPU证明不审批这轮新guard修订。

2026-10-31 source参考worker内部入口增量：verify_source_worker_input冻结prompt/KV/plan SHA/声明的parent原锚，核代码/spec，_run_source_worker在验证来源目录和原剩余预算后调用既有body并持久化input SHA。七项原生CPU控制与完整404项测试已过，但父端实际创建命令、source guard target对新input SHA和anchor的绑定、完整资源/窗口/ABI/monitor准入仍缺。不能把新内部API当已部署model CLI，不借native完整编译缺口阻塞source必要准备，也不重复P0通用包装。

2026-10-31 host-reader兼容修订：已复现旧ToHost至多拼两分区且不续正短读/EINTR；独立model.cpp新循环通过writer四个4KiB真实分区及4/6/16KiB读取、短读/中断/截断CPU控制，原close尾部保留。当前CPP SHA49b2b7fd…已绑定ledger/native入口，旧c057close-only候选保留历史且不能批准新代码。源码只编CPU片段，真实O_DIRECT/线程/锁页池/完整CUDA构建仍缺；不得复用旧compiled receipt或当作历史exit137修复。下一项应接完整实际构建身份和峰值/运行准入，不重复故障已闭环的读循环。

2026-10-31库身份增量：native `_bound_runtime`现要求Linux self executable映射与同fd文件SHA/dev/inode/size/mtime/ctime一致，拒绝同字节未映射副本和旧库路径替换；原生WSL实际共享库控制通过。此为检查器的CPU证据，尚非实际Store checkpoint库或服务端身份通过；内存机器码/函数地址/live Store标志仍false。真实compiled receipt、活跃Store及全峰值/守护准入仍是下一项，不重复插件注册或凭磁盘SHA放行。

2026-10-31调用图补充：`source_plugin.register_native`现以独立native spec/manifest/env/private entrypoint接`register_deferred_mixed_loader`；登记只快照纯元数据，load_model才取实际TP8/PP1 rank/device，按rank选资产、device选预算并调原mixed factory；保留delegate失败状态及2×拒绝。export回调保持独立。新CPU子进程替身注册及小树真实文件清单验证不是实际vLLM spawn、完整worker或GPU准入；下一项实际构建身份和完整mixed峰值/运行预算仍须证据，不绕过旧预算停止或实际guard。

源转换入口现可由独立Base子类调用：固定Default loader只加载本地SF，rank导出发生在后处理之前，之后固定Base继续后处理，便于同一后端生成参考。`vllm_export_hook`默认读取已初始化的TP/PP组及当前CUDA设备；CPU测试的context callback是明确模拟。`loader_registry`复用固定官方注册函数，仅使用sllm_kimi_export_v1/sllm_kimi_native_v1独立名称；同类重复登记允许，其他类冲突拒绝，原safetensors不改。

官方general plugin会在process0、engine core及worker中加载；独立worker需要把本run配置与注册回调通过同一插件入口传到每个进程。当前已实现source私有distribution metadata、注册/完整source绑定与内部参考worker、watchdog/主动probe候选并做CPU控制；实际vLLM多进程插件传播、独立宿主服务与全模型仍未运行，不能只在父进程注册后假定spawn子进程也已注册。依据：https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/plugins/__init__.py 。

## 必须先修正 native 双份驻留

当前native候选先完整CUDA构造目标，再分配一整份native归档，二者在安装前同时存在。基于冻结配置/条件TP8映射，保守省略源shape元数据、假定其他权重理想八分，只计MLA A投影的必需复制，得到单rank权重下界76,009,523,580 B，双份152,019,047,160 B。历史卡容量150,754,820,096 B，尚未计视觉/路由额外复制、上下文、KV、scratch、后处理工作区即超出。完整输入、SHA、假设见native-memory-lower-bound.json；固定源码DeepSeekV2FusedQkvAProjLinear明确disable_tp=True。

这是条件静态预算结论，不是实际OOM或历史exit137的原因证据；历史事件仍原因未知。当前native工厂新增必须提供的device_memory_budget_bytes，在模型初始化前以归档中checkpoint-backed目标的双份字节下界拒绝不足预算。该字段必须来自新窗口实际资源证据，并扣除其他必要开销；不能直接填大值绕过，也不能把下界可容纳当作完整峰值可容纳。

下一项先评估CPU文本初始化/官方CUDA视觉支路或直接目标IPC写入两种方案。前者须验证混设备元数据、Parameter属性保留、Base后处理设备语义和主机峰值；后者须验证PyTorch缓存分配器的实际IPC base/offset、所有者、事件及释放语义。未经实现、测试和审阅不选择性跳过检查，不改TP/量化格式以求运行。源转换与backend参考只保留一份模型，不受此特定双份下界直接阻断，但仍须完整worker准入。

## worker 必需的闭包

2026-10-31核心修订：完成发布必须消费实际convert_checkpoint_rank返回的receipt['archive']，其中才包含native_index_sha256和cleanup_pending；不能让简化mock的扁平回执替代真实接口。已先用真实CPU转换器返回复现旧KeyError，再将同一微型CPU模型从固定Base初始化、固定Default/Kimi源加载、真实convert/write/index、固定Base后处理连到completion发布，并核packed与BF16原始位模式。设备、TP/name和framework配置为明确测试替身，非真实vLLM/CUDA/全模型。原归档partial拒绝与消费失败终态保持；全部Tensor所有权/峰值与八rank实际验证仍在计时之外完成，当前未开跑。

内部worker现已在既有run_root中独占预留source-reference-events.jsonl，不新建目录。预留文件前核run/output与checkpoint互不包含、run身份相符；文件存在即在engine factory之前拒绝，不覆盖旧证据。开始及唯一终态分别flush+fsync，单行最多64KiB、完整读回最多128KiB；开始保留prompt/KV，成功及失败路径都持久化prompt/实际engine配置、raw tokens/有限logprobs及已完成的business/rank元数据，traceback/error摘要截断有显式标志。使用unbuffered独占写，避免日志flush失败后的close二次刷新覆盖原业务异常；CPU回归实际以io.BufferedWriter+写入失败的RawIO故障控制复现旧路径两次flush覆盖，再验证新路径仅一次失败写且原异常保留。证据写入失败保留业务原异常及附加说明；成功业务后终态写入失败也整体失败，异常仍携带结果供宿主补取。read_reference_events拒绝partial最后一行、旧身份、重复/错序事件、非有限/倒退monotonic以及冒充释放或服务通过的结果。只有started的文件标记incomplete，不推断进程还活着或资源已释放。CPU测试实际拉起一个仅sleep的小子进程，由测试父进程通过自己持有的Popen句柄终止，读回仍为incomplete；仅证明日志中断语义，不是宿主deadline guard、真实容器身份或资源回收验证。

内部源参考worker主体已补在现有source_plugin._run_source_reference：工厂对应vLLM.LLM/SamplingParams，固定TP8/PP1/DP1/PCP1、Marlin、compressed-tensors、BF16非量化张量、无MTP；短文本max_model_len=1024、单请求、显式KV字节预算、无CPU offload/公共Store池，保留视觉与projector构造但跳过多模态profile。调用前后的loader真实profile硬检查仍保留，不能从EngineArgs直接设置派生use_sequence_parallel_moe字段。先收8rank完成元数据，然后分别生成固定32原始token（忽略EOS/min32）和自然EOS（最多128，length截断不算完成）。结果保留raw IDs/文本/每个选中token的有限logprob；非退化检查只是初始参考质量门槛，不是语义正确或稳定金标，gold_frozen=false。全部调用前后使用原批次墙钟/单调预算；finally通过llm_engine.engine_core.shutdown(timeout=剩余预算)尝试退出，即使已到截止也以timeout=0尝试。保留业务原异常、退出附加错误，以及已完成请求的raw tokens/rank/配置证据供未来宿主控制器落盘；构造未返回时明确要求清理可能的partial进程。

接口依据为固定v0.19.1官方源码：[LLM](https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/entrypoints/llm.py)、[EngineArgs](https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/engine/arg_utils.py)、[CoreClient.shutdown](https://raw.githubusercontent.com/vllm-project/vllm/v0.19.1/vllm/v1/engine/core_client.py)。候选无可直接启动的CLI，未接实际宿主准入；同步constructor/generate不能靠前后检查中断，实际独立guard必不可少。CPU测试的engine/sampling都是stand-in，真实vLLM多进程插件与完整模型尚未执行。shutdown返回也不证明GPU/RSS/cgroup释放；resources_released/full_service_verified仍false，60秒释放监控和完整host控制器继续待接。

CPU接线追加（2026-10-31）：source_plugin向原有export factory传入run_id/holder/code/spec/manifest身份。每个rank只在固定Base.load_model含后处理返回后，向既有rank目录发布source-complete.json；记录sidecar/index SHA与load_model耗时，不包含张量，也不重复导出。父端collect_source_rank_receipts要求8份完整且身份一致、device唯一、实际文件哈希一致；任一缺失/旧身份/损坏/partial拒绝。元数据读回使用nonblocking/no-follow（平台支持时）打开并fstat实际fd为普通文件，completion限4KiB、sidecar/index各16MiB，避免FIFO无界阻塞及全文件内存拷贝。源archive cleanup_pending或completion unlink失败使loader失败，partial保留供取证。metadata_verified仅是本次文件集合，不是payload/TP映射/native/GPU服务验证，相关flag仍false。独立宿主守护、真实UID/cgroup/镜像/预算与进程退出仍未完成；本次不创建运行目录或启动worker。

1. 新run_id/holder、UID2147、qjhs003与实际boot身份、代码SHA/完整文件清单、精确镜像及vLLM0.19.1/Torch2.10.0+cu129等实际ABI。不能沿用GLM的cu130镜像/旧gate当Kimi验证。checkpoint与输出挂载分别只读/独占新目录；先核磁盘容量和实际挂载来源，不重复下载。
2. 新有效授权窗口及不可延长的整体预算：镜像/ABI预检、分布式初始化、SF加载/转换、后处理、业务、60秒观察、模型/Store退出、取证和余量分别登记。各阶段上限尚待runtime方案冻结；未登记完整总和不得启动。所有预算包含编译和公共初始化，不只覆盖native_call timeout。
3. 独立宿主守护在计算前安装并probe，绑定本run的完整容器ID、holder与cgroup，不按模糊进程名清理。独立监控覆盖8卡、NUMA/主机RSS/PSS/锁页/共享内存、CPU及共享I/O。新scope不修改旧GLM守护或其他任务。
4. 模式分开：源转换/后端参考，重复冷启动稳定后冻结Kimi金标，再native Store正确性，最后原生服务路径。固定32原始token和自然EOS分别定义；所有8rank的packed/scale、角色与必要buffer在计时外核验，后处理状态另作覆盖说明。组件通过不冒充服务通过，不启R24。
5. 失败一律保留原始事件和partial输出；RPC超时不证明DMA停止。只受控退出本实验Store/worker/context，按精确身份核验释放并继续60秒观察，不自动重试或卸载共享Store。磁盘资产保留；源版本/transfer/source receipt及所有review/gate须绑定manifest。

本轮仍未安装守护、创建容器、注册插件、启动GPU或读取完整权重。下一次实际运行前需完整独立复审和新资源核验，不能仅凭164/167项CPU测试或注册API通过开跑。
