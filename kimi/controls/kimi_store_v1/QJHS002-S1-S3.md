# qjhs002 S1–S3 接续核验（2026-11-03，未闭环）

保留现有阶段、金标、失败和审阅。唯一构建/服务基线为 /data/brick1/boyjiang/ServerlessLLM，资产复用 /data/brick1/boyjiang/kimi-k2.6。未启动模型、转换或服务，未安装、回滚或修改远端源码。

|阶段|本轮实际核验|尚缺证据|
|---|---|---|
|S1|官方 HEAD 9f50241baa5386e06a9321c51f19a9ef5f964c2b；requirements-worker.txt 固定 vllm==0.11.2；默认 /usr/bin/python3 的 pip show 未找到 vllm/torch/transformers|既有 Kimi 实际解释器、包路径、commit、依赖与模型支持；旧0.9.0.1文档定位。不能据此降级|
|S2|git status 唯一所见差异为 storage.proto 类型 T；Git 模式120000，原内容 ../../proto/storage.proto；现为1994字节普通文件；现文件和目标均 SHA256 95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b|类型变化原因尚未证明，可能与迁移复制有关；资产完整性、补丁与编译库身份尚待核验；未恢复链接|
|S3|已读 patch.sh；正向dry-run失败一律打印 already applied，存在误报；已读补丁头，涉及 model_loader 注册 serverless_llm 与新增 ServerlessLLMLoader|尚未定位实际安装树，未完成正反干跑及接口比对；未应用/已应用/冲突/不兼容均不能先行判定|

## 当前最小补丁清单

零项已获证据支持的生产修改。先补齐上述证据，再确定官方对应文件的最小修复。patch.sh 的失败分支是已确认的诊断缺口，不能替代实际双向干跑或作为模型适配成功证据。当前未修改此脚本。

## p0 与窗口

已只读读取远端 p0/p0-lifecycle-receipt.json：docker start 10秒超时，inspect 清理502.196739秒超时；torch_cuda_ipc_verified/full_model_verified/native_loader_verified/store_created 均false。此为历史失败收据，不能覆盖后续释放关账记录。

当前迁移来的 p0/p0-spec.json 仍绑定 node=qjhs003、run=kimi-p0-20261102T201641Z-99679319、code_commit=844e56736f0a7f5775ed51a8c878f20cc0b64f4c。不能在002原样复用旧gate、GPU UUID、窗口或批准。

本轮使用 gputime-booking-agent 官方 helper 的只读 snapshot，复用 Windows 既有 token（WSL 默认无token）；确认 bk-20261103T0429-3ecd2dc4，用户boyjiang，用途sllm kimi，h20/qjhs002，2026-11-03 13:00–20:00 +08:00，8 H20，CPU lane disabled。用户已明确允许002继续适配。预约不替代完整预算、资源核验、独立审阅及新守护。

远端多次短SSH出现超时/断连，未取到的输出不记成功。未运行新的40项或R24，GLM原35成功/5失败保持。下一步继续只读定位迁移环境/官方转换加载源码与编译产物，再双向干跑；S1–S3完成前不全量转换。
## 16:04–本轮补充核验（保留前述初始断点）

- GitHub refs/heads/main 已实际返回 6294fb582459b315d862b95c58fd74182a907c53，补齐上轮TLS失败后的远端核验。
- 用户明确权重根为 /data/brick1/boyjiang/kimi-new/checkpoint；实际snapshot为其下7eb5002f6aadc958aed6a9177b7ed26bb94011bb。唯一构建/服务基线不变。旧kimi-k2.6资产根仍用于既有wheel、编译库和失败记录。
- 只读全部64个safetensors头（每头上限16MiB），208550张量与index逐项归属一致，无缺片；文件共595177988208B，index metadata total_size=595148192736。未读全量payload/未做595GB哈希，不将元数据完整性当字节完整性。config SHA256 85825ca6e18cbe539eb83ee09eedfb3f4222265929f06e9f535a6d9364f55899；KimiK25ForConditionalGeneration/kimi_k25；text_config量化为compressed-tensors pack-quantized、4bit、group_size32；packed权重I32。
- 已读该snapshot官方README及docs/deploy_guidance.md：K2.6复用K2.5架构，明确推荐人工验证的vLLM0.19.1；README同时要求transformers>=4.57.1,<5.0.0，与旧候选锁5.5.1存在待解差异。当前官方基线quickstart明确补丁测试版本0.11.2；本提交树Markdown未检到0.9.0.1文本，不能将旧说明当当前依赖。
- 既有vllm wheel _version.py给出0.19.1 / gb1388b1fb；METADATA要求torch2.10.0、compressed-tensors0.15.0.1、transformers>=4.56.0且排除5.0–5.4及5.5.0。这是wheel身份，不是实际执行版本。系统默认python无vLLM；Docker精确inspect旧P0镜像ec5b4131…返回No such image。本轮未找到既有Kimi可执行环境，已询问用户路径/镜像，不新建环境。
- 已读官方save_vllm_model.py、load_vllm_model.py、torch.save_dict/load_dict和完整sllm_load.patch。转换器实际先构造完整LLM再逐rank导出；不会拿前两个分片冒充完整转换。save_dict保留dtype/shape/stride，load_dict依赖本地RPC Store/CUDA，不能将CPU解析头部当真实读回。
- S3有界干跑：仅将wheel中补丁所需的522767B源码临时展开，退出自动清理，未解包环境、未改wheel/安装树。patch SHA256 0d5f9e4e1ea8901538b7a75bf3ac5caa706519bc9e2bbcb91d99aa8ce022f357。默认正向rc0但两worker依赖fuzz2；--forward --fuzz=0 rc1，两worker hunk失败；--force -R --fuzz=0 rc1全部失败，sllm_loader.py不存在。分类：wheel未应用；严格文本冲突在两个worker；是否运行兼容尚未证明；实际安装树状态未知。最初--batch -R会自动反转方向并rc0，不能用该返回码证明已应用，原输出保留。
- 0.19.1 loader调用传vllm_config/model_config/prefix，官方补丁override通过**kwargs接收但忽略后两者；BaseModelLoader标准路径执行process_weights_after_loading，补丁override未调用。需辨别归档是postprocess还是原始权重后才决定修复，不能盲目二次后处理。原生恢复的参数/buffer所有权与显存峰值仍未证明。
- 旧5e597e33编译库实际SHA与旧receipt一致：_C=994772cc3ca6c874d995b5aa9a846400b4b253b0a786628a2832dccc0f813864，_checkpoint_store=30132b6d6034759d3b5b961cd4d22bd0d8da1ca2754d8e80d7769e35cfe21eb5。33项源码原始SHA与当前基线均不同，其中26项可由LF/CRLF解释，另7项仍实质不同（aligned_buffer.cpp/checkpoint.cpp/tensor_writer.cpp/checkpoint_store.cpp/checkpoint_store.h/checkpoint_store_py.cpp/model.cpp）。旧receipt的import/CPU构建成功不能追认为002当前基线ABI成功。

最小生产补丁清单仍未冻结：不新增codec/loader框架，不降级、不覆盖链接/编译库。下一步定位既有环境；查七项源码差异及Transformers约束；用0.19.1实际接口确定官方patch最小移植范围，然后完成真实有界张量save/read验证。运行前须新资源、预算、审阅和守护。

## 本轮七项差异定位与官方缺口复现

已逐行比较当前官方基线与旧构建对应冻结源码（model.cpp用Kimi既有候选），仅读取GLM冻结文件，没有修改。完整差异见qjhs002-native-source-diff.txt：
- aligned_buffer.cpp：旧版对尾部按4096补零并检查写入/截断，官方析构直接pwrite未对齐buf_pos_且忽略返回。
- tensor_writer.cpp：旧版在切分区时把offset_补到4096边界，官方未计物理分区尾部padding。
- checkpoint.cpp：旧版增加float8_e4m3fn，当前官方原本已有I32/BF16等；不能因INT4就预设需要新codec或FP8补丁。
- checkpoint_store.cpp/.h/_py.cpp：旧版额外WaitModelInHost入口、等待GPU时释放GIL。不是当前官方库的既有能力。
- model.cpp：旧Kimi候选包含跨多分片/EINTR/短读处理，以及IPC关闭错误传播；官方当前实现只补读下一分片且通知早于IPC close。

已在002直接编译当前官方aligned_buffer.cpp（未修改源码），用3字节输入实际写/读。g++编译rc0，writeData返回3，但析构后文件size=0、payload=0,0,0，probe rc5；完整输出见qjhs002-official-tail-repro.txt。小CPU控制限制AS1536MiB、CPU5秒、wall8秒；官方固定1GiB虚拟buffer仅触碰小尾部，未启动Torch/GPU/Store。临时目录/可执行文件/3字节payload由TemporaryDirectory在退出后清除。这证明官方未对齐尾部写入会静默丢失，尚非Kimi真实张量组件验证。

最小修复范围已收敛为官方aligned_buffer.cpp与tensor_writer.cpp的尾部/物理分片偏移一致性，先设计覆盖非整页尾部、已有文件截断、分片偏移的有界回归再改；不能只修其中一处，不直接搬入七文件旧GLM补丁。分片边界小fixture若缩小常量必须明示，不能当10GiB完整性能验证。原生ToHost多分片读取和IPC关闭错误传播另列待验证必要缺口。当前生产文件未修改，旧收据/失败/金标保持原样。

补充环境核验：002系统python为3.10；旧native构建依赖Python3.12/Torch2.10/CUDA12.9，其构建镜像53f661ba…精确inspect也返回No such image，和旧P0运行镜像均缺失。不能将wheel或迁移库当已安装的运行环境。用户关于既有环境位置的问题仍待回复；依照禁止新建环境要求，未安装或创建容器环境。S4a和GPU路径仍未通过。

## 官方最小修复已实际提交；既有环境迁移进行中

用户确认运行环境仍在qjhs003 Docker内，明确授权尝试迁移既有runtime210-cu129镜像。003实际image ID=sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27，size=34184928331B；已取RootFS全部层摘要用于迁移后比对。无registry digest不等于没有可核验内容身份，需核完整image ID/配置/层摘要。002 DockerRootDir=/data/brick0/docker，所在NVMe可用约1123874398208B；003系统盘可用约820563099648B。

迁移复用既有WSL，通过003 docker save|gzip -1流到002 gzip -d|docker load，未生成本地/共享盘tar，无新镜像构建、容器、GPU或模型。外层timeout1800秒。当前exec session_id=39425，仍运行；最近独立核源端docker save PID3484138/gzip3484139及目标gzip4025990/docker load4025991均存活。尚未有归档字节流到目标，不宣称导入成功。继续轮询同一handle，不能因观察超时重启迁移。

已修复官方对应模块且仅显式stage本任务三文件：
- sllm_store/csrc/checkpoint/aligned_buffer.cpp：尾部补零到4096并写入；检查pwrite/ftruncate，拒绝静默成功。
- sllm_store/csrc/checkpoint/tensor_writer.cpp：下一物理分片起点纳入padding。
- sllm_store/tests/cpp/test_tensor_writer.py：stdlib unittest直接编译现有C++；覆盖3/4096/4101字节、较长旧文件截断、零padding、分片偏移及两分片payload。分片阈值只在临时头文件夹具降为8192，生产常量未改，不是10GiB性能/完整模型证明。

在002执行回归1 test OK（0.873秒）；git diff --check通过。服务器源码commit=ab3eb90ffe197002d75aae5e0057a235db60282d，父基线9f50241；proto类型T仍未纳入提交，未回滚。当前未重编译扩展、未部署运行或独立GPU审阅。小型源码patch已保存official-aligned-tail-ab3eb90.patch。

本地从002现有仓库fetch单个提交用于发布（对象包约6.13MiB，无新checkout）。多次将源码commit发布到用户GitHub refs/heads/kimi-qjhs002-aligned-tail返回HTTP408，远端未返回该ref；源码分支推送尚未核验成功，不能称已同步。原审计main的2e4e43247a6c3f890b9b3b0fe17547c0695b4ff6随后push及ls-remote均已核验。此次保留服务器/本地源码提交及可复查patch，后续继续发布，不force-push。

## S3三态修复、独立审阅与整buffer覆盖补缺

已读最新用户指定AGENTS.md并原样复制到当前工作目录AGENTS.md，双端SHA256一致，未修改源仓库中用户的未提交AGENTS.md。

旧版本证据已从官方Git历史定位：7b3afd7为“Update vllm version to 0.9.0.1 and support v1 engine (#263)”，旧docs/stable/store/rocm_quickstart.md含0.9.0.1运行日志；ce354fd4d3a2f6117ffffc49dffe3cca11d6bbde将官方补丁升级0.11.2；9f50241当前quickstart明确测试0.11.2。Kimi snapshot指南另明确推荐0.19.1，因此不据旧说明降级。

官方patch.sh现已修正：正向--forward --fuzz=0干跑成功才实际应用同样严格补丁；反向--force -R --fuzz=0干跑成功才报已应用；两者失败明确冲突退出1。commit398058769c697e7930a7768eca17067481eeef89。新tests/python/test_vllm_patch_status.py用真实patch(1)+小文本夹具验证未应用/已应用/冲突三态，1test OK 0.017s；只对fixture伪造包发现，不导入/修改环境。真实0.19.1 wheel源码子集上新script rc1、source_unchanged=true、false_already_applied=false，输出见qjhs002-strict-script-wheel-result.json。尚未修改实际vLLM安装树或宣称runtime兼容。

用户明确允许多subagent后启动三个只读任务：官方修复独立审阅、0.19.1接口核对、S4a官方组件路径。writer首审NEEDS_FIX：恰好写满buffer时buf_pos_=0使旧长文件未截断。保持原失败不抹去，用临时kBufferSize=8192夹具先实际复现SIGABRT（见qjhs002-writer-full-buffer-negative.txt），再在官方AlignedBuffer constructor加O_TRUNC。唯一生产调用为TensorWriter完整重写分片，未发现append消费者。新增case8192及0，测试移入既有tests/python，不变生产常量。主线程实际unittest discover 1test OK 0.831s、diffcheck通过；新commit bb315443216a7b2d1931a23851203ec5674b8aa3。独立agent review_official_fixes限定复审PASS_SOURCE_ONLY，绑定该SHA，未执行测试/未授GPU批准。旧NEEDS_FIX保留。源码三提交系列保存在official-kimi-fixes-bb31544.patch，不含权重/环境。

官方CLI早已通过llm_engine.engine_core保存，现有test_save_vllm_model.py集成测试也是此路径；只有examples/save_vllm_model.py仍访问model_executor。选择复用CLI，不另修示例或新建转换入口；尚未运行GPU集成测试或完整模型。

用户再次明确授权002 GPU计算，16:31 fresh API核boyjiang预约仍到20:00+08；nvidia-smi显示8×H20-3e各143771MiB，均0占用/0利用率、无compute-apps；主机available约2013882548224B。此为当时资源快照，非后续运行gate。

镜像迁移exec39425仍存活、尚未目标归档字节；源Docker导出进程和目标load进程可见，源磁盘用量增长不单独证明本任务进度。保留同一会话，未重启迁移。Github审计main d1bda621906c9312c958124463cc03efe70f1cca已实际核验。源码分支发布仍HTTP408（一次--progress记录4.39MiB pack、84–369KiB/s后断连）；未把失败称成功，服务器/本地源码提交及小型patch均保留。S1实际执行环境、S2符号链接变化原因、S3运行兼容、S4a官方读回与全部S4b仍未闭环。

## 最新工作方式与迁移终态覆盖（不重写旧记录）

用户明确调整为“我准备代码/命令，用户负责运行”。此后不再自行启动迁移、容器、GPU、模型或Store。已提供用户分步docker save压缩落盘、scp和docker load命令，等待实际回传，不能把用户意图视为已运行或成功。

旧exec39425已明确terminal exit1：源003 SSH报“Timeout, server 115.190.191.164 not responding”，目标gzip报unexpected end of file，docker load报缺repositories。随后独立只读ps确认源3484138/3484139、目标4025990/4025991均不在；002精确镜像inspect仍未找到目标镜像。没有仍活跃的迁移handle，不重复启动。审计main a461beed0f8380cb837e73736c33f56f338b14ae已实际ls-remote核验。

独立vllm_compatibility只读核验发现必须处理的实际合同缺口：
- 0.19.1 compressed_tensors_moe.py:1369–1411 fresh创建4个g_idx/sort_indices；Marlin且actorder非group时:1478–1494改为[E,0] int32参数。官方_filter_subtensors丢弃numel=0，当前恢复fresh模型却仍索引这些非空key，会缺key。仅可对实际确认Marlin/no-actorder分支用官方语义重建，不能用于group actorder或未核分支。
- :1552的workspace是普通tensor属性，不在state_dict；apply:1664需要。marlin_utils.py:257–265官方marlin_make_workspace_new(device,4)创建全零int workspace，大小为num_compute_units(device.index)*4。可独立重建，但不能因此宣称布局/完整恢复已验证。
- :1496–1550会repack/permute packed/scale。Store保存LLM加载后的状态，恢复不可再跑完整后处理；必须先绑定归档backend/layout身份及源参考实际分支。
- Flashinfer :1417–1439提前返回，不具有同一Marlin workspace/空参数问题；不能套Marlin规则，亦不能由此称完整兼容。
- get_model传model_config/prefix，旧补丁忽略；CPU state_dict引用保留使placeholder替换不立即释放原CPU权重；shape/dtype/backend/layout严格校验与CUDA backing ownership仍未证明。
- 视觉time_weight及lazy complex64 freqs_cis均为persistent=False，不能预设需新增codec。

当前最小代码修复方向限定官方既有sllm_load.patch/ServerlessLLMLoader：先锁实际backend，绑定布局，再处理必要scratch及原生API合同；没有新增loader管理框架或启动系统。现阶段不盲目把Marlin政策套到未知profile。官方writer与patch状态修复已源码复审通过，但真实组件/完整GPU/原生服务尚未通过。

已准备给用户的下一条CPU环境核验命令（outputs/qjhs002-runtime-inventory-command.txt）：先核精确image ID，再用既有镜像runc、无GPU/网络、1CPU/4GiB/128pids，只导入并报告模块路径/版本、vLLM commit和依赖、Torch编译CUDA/ABI。Python入口45秒alarm，不加载模型/Store或分配CUDA；尚未由用户执行。alarm仅约束Python入口，不证明Docker start物理可取消；旧Docker启动失败历史保留。S4b必须另有新守护/预算/资源及审阅。

CPU命令经review_official_fixes只读复审后已按建议修订：运行完整expected image ID，避免tag检查/使用竞态；逐模块starting/result即时flush，导入崩溃/超时也保留先前已完成项；额外收集已安装distribution版本。声明requirements与已安装版本均只作证据，不自动宣称约束/ABI满足。该命令不是S4b guard或probe。用户尚未执行，不宣称导入成功。

## RestoreTensors所有权候选（用户执行，未应用/编译）

独立源码复审确认当前官方RestoreTensors的实际缺口：各from_blob创建独立Storage，只有一个offset0 Storage拥有cudaFree；非零offset/另一个offset0 Storage不会保活该allocation。删掉拥有者而保留其他Storage会悬空；没有offset0则没有释放者。此结论是源码合同证据，不是GPU复现实测。

已准备official-restore-ownership-candidate.patch，只改官方csrc/checkpoint/checkpoint.cpp并在既有tests/python增加回归，不新增loader/部署框架：
- 对输入base建立shared_ptr<void> owner表，所有对应from_blob deleter捕获同一owner；保留原cudaFree操作，去掉offset0特殊分支。
- 明确一次调用消费输入allocation，unused allocation在返回时释放；不支持caller之后手动free或把同一allocation再次交入另一调用。
- 测试提取实际RestoreTensors函数，使用真实Torch C++ from_blob，仅替换设备为CPU、CUDA free为计数的test_cuda_free；覆盖offset0先删、多个offset0别名、无offset0、unused allocation、缺metadata异常。缺metadata case先检查生产释放次数再进入测试兜底，不能以兜底冒充通过。
- CPU Torch扩展编译限制MAX_JOBS=1并使用临时build目录；需要既有Torch/compiler/Ninja，无安装。尚未编译/运行，不能称CPU测试通过，更不能称CUDA/Store通过。
- 任意OOM中尚未被owner表接管的后续allocation、跨调用重复移交、跨设备及IPC生命周期仍需后续验证，不声称全部异常无泄漏。

首版patch的Windows CRLF使002 git apply --check失败，未应用任何源码；已改用明确LF字节重新生成。最终SHA256=185d68216d46e24357268a8b14b5925a9e54f40a5596bdb9ee31bf3c241a4aee，已上传002:/data/brick1/boyjiang/kimi-k2.6/p0/restore-ownership-candidate-185d68216d46.patch，并实际git apply --check通过。002官方HEAD仍bb315443216a7b2d1931a23851203ec5674b8aa3，工作树仍只有原proto T；checkpoint.cpp SHA256=7ec80ad1d2104e0743874888c3d1f0dde03a95778f576f299e299199af84c2a6，与9f50241源对象相同。候选未部署，源码commit未改变。review_official_fixes独立只读复审给出限定可交用户编译验证、异常case无新增阻塞；未执行测试。root只做Python AST与补丁干跑。

S4a只读结论补充：官方save_dict可以CPU原始保存；已查load_dict/RestoreTensors路径依赖Store RPC与CUDA，不能自行解析tensor.data冒充官方CPU读回。真实最小组件建议一个专家packed I32+对应scale+必要shape元数据，总payload<=16MiB；必须通过官方保存/读回并核原始位模式。单GPU、单Store、1thread/1MiB chunk/64MiB pinned pool仅是待验证有界候选，不是已证明的最小可运行配置。现有source_schedule/watchdog旧gate仍绑定003，未给002新守护发通过。镜像/ABI核验先行，所有计算由用户运行。

迁移接续：用户手工导出后，17:03只读确认003已生成kimi-runtime-ec5b4131.tar.gz（18061247234B）及117B sha256文件，导出/压缩/校验/传输进程当时均未见；002当时未见这两个文件。已给用户只重试scp的命令，禁止重复导出。用户报告remote -v已OK，涉及其kimi-new/ServerlessLLM checkout；不改变唯一构建/服务基线。后续传输/导入/CPU inventory输出尚待用户回传，不推断已运行成功。

## 用户等待传输期间：0.19.1官方补丁接口候选

用户明确传输未完成并要求继续后续工作。未代其启动计算或迁移；并行准备了不依赖镜像运行的官方源码候选。新增official-vllm0191-interface-candidate.patch只修改官方既有sllm_store/vllm_patch/sllm_load.patch并在既有tests/python新增参数转发回归：
- 将上下文按0.19.1 wheel源重新生成，LoadConfig导入改为vllm.config.load。
- ServerlessLLMLoader.load_model显式接收vllm_config/model_config/prefix，不再通过**kwargs静默丢弃；目录、dtype取传入model_config，initialize_model转发三参数，符合0.19.1 BaseModelLoader合同。
- test_vllm_loader_interface.py无需导入Torch/vLLM；执行实际补丁内load_model方法，到mock initializer抛异常即止。用不同且不可访问的fallback model_config验证选择正确，断言dtype、rank目录(selected.model,rank_0)及prefix转发，并拒绝Store调用。只做过AST语法检查，测试尚未运行；不覆盖模块级真实导入/模型构造或GPU。
- 生成时仅在临时wheel源码子集借原补丁fuzz应用重建意图；重新生成后的候选在干净0.19.1源码子集上，以--batch --fuzz=0 -p2完成正向干跑、实际fixture应用、严格反向干跑，三者rc0。完整argv与临时路径/输出见qjhs002-vllm0191-interface-static-checks.json。不是实际安装树应用。
- 独立AST核GPUModelRunner与Worker各恰有一个save_serverless_llm_state方法；LoadConfig定义与导入路径AST核验通过。不是Python包导入/ABI验证。

最终outer候选SHA256=e33be14d0d0cc74d9e264596e951f8c8345f94307028b3359dc3b251e1f506a0；已上传002:/data/brick1/boyjiang/kimi-k2.6/p0/vllm0191-interface-candidate-e33be14d0d0c.patch，实际官方工作树git apply --check通过，未应用。inner补丁SHA256=4d6271fa7cce8e47ccbc10a39812075d3e806426b8cf0a83b42632e807110f84。vllm_compatibility独立只读核对scope/API/fixture通过；其要求的完整argv、worker AST结果与rank目录断言均已补齐。测试仍由用户运行。

更精确backend证据：同一个CompressedTensorsWNA16MarlinMoEMethod类以kernel_backend字段区分Flashinfer/Marlin（1172–1179），不能按类名判分支。actorder来自weight_quant.actorder；E与device从layer.w13_weight_g_idx取，TP rank不等于E。四空参数/workspace重建必须绑定实际Marlin/no-group profile及归档layout。当前候选仍不解决这些布局/scratch、alias、所有权或显存峰值缺口，不是完整转换或运行准入；不能仅因严格文本补丁可应用就启动模型。

用户可在传输期间对官方源码应用该outer候选并运行纯stdlib接口回归；这不调用patch.sh、不修改实际vLLM环境、不导入Torch或启动模型/Store。需回传实际测试结果后才能记为回归通过。

## IPC关闭状态与确认错误传播候选（镜像传输期间源码准备）

已复用既有Kimi native_patch/model.cpp中经历史CPU夹具验证的关闭尾部，提取为当前官方model.cpp的独立候选；没有搬入旧ToHost/FP8/WaitModelInHost等无关差异。仅在ToGpu尾部保留复制失败，记录cudaSetDevice/cudaIpcCloseMemHandle失败，失败设备不在错误context继续关闭，并继续其他可处理句柄，最终INTERRUPTED后通知。复用旧test_ipc_close_patch.py的PREFIX/MAIN编译当前官方实际tail，新增tests/python/test_ipc_close_status.py覆盖success/close_failure/device_failure/copy_failure、有效关闭数量及notify最后。未执行此新测试，只做AST和提取标记检查。必须保留限制：原尾部持有mutex_，此前同锁等待者不能在解锁前观察；不能把通知位置调整单独称为并发竞态已修复。

继续沿失败链读得ConfirmModel服务wait失败返回gRPC INTERNAL；客户端confirm_model_loaded却捕获RPC错误返回False，而官方torch.py一次、transformers.py三次和utils.py一次调用全部忽略该返回值。候选只在共享client.py方法把已返回的RPC错误统一raise RuntimeError(...) from e，成功仍返回None；使五处调用均不能继续返回未确认结果，不批量改五个调用方。新增tests/python/test_confirm_model_error.py使用实际AST提取函数：成功契约、cancelled/internal保留原cause、实际load_dict不返回未确认state，三条纯stdlib回归；未运行。

review_official_fixes独立只读复审对IPC尾部及共享客户端传播均给出限定可交用户验证、无新增源码阻塞；没有执行测试或授运行准入。失败句柄残留、consumer异常时CUDA allocation所有权、Store停止/释放顺序仍待守护与实际GPU验证。RPC阻塞/超时也未由这个“已返回错误传播”改动解决，不能宣称完整失败清理。

最终候选outputs/official-ipc-close-candidate.patch SHA256=eeb75df57417f64ee98bb2c5be85b25ee3d3e7b80e52430f122d8b128fb1195e。已上传002:/data/brick1/boyjiang/kimi-k2.6/p0/ipc-close-candidate-eeb75df57417.patch，官方工作树git apply --check通过、SHA与本地一致；未应用，工作树检查仍只有原storage.proto类型T。准备给用户在传输期间运行这两项CPU回归，不启动容器/GPU/Store。旧候选5b3163仅包含尾部，保留为历史草稿，不冒充最终版本。

## 用户实际CPU回归通过，源码已提交并发布（最新覆盖）

用户回传qjhs002原始终端结果：test_vllm_loader_interface.py 1test/0.003s OK；test_confirm_model_error.py 3tests/0.008s OK；test_ipc_close_status.py 1test/0.442s OK。共5项CPU测试，包含实际g++编译的CUDA-stub关闭控制，执行者为用户，agent未代跑。详见qjhs002-user-cpu-validation-d9eee8d.json。不能将这些结果外推为真实GPU/Store/IPC资源清理通过。

root只读核对六个源/测试文件及SHA，普通源码diff --check通过；仅显式stage这六个文件，于服务器提交d9eee8de70badb1570127ca551f22b66ec4a5b30。原storage.proto类型T保持未提交，未回滚或纳管。用户报告的空白警告对应内嵌unified patch三处单空格上下文标记（261/284/331），直接trim会改变补丁语法，因此原样保留，不为消除警告破坏补丁。完整四提交源码系列保存在official-source-through-d9eee8d.patch。

这次源码分支推送成功：GitHub BoyJiang2/serverlessllm refs/heads/kimi-qjhs002-aligned-tail实际返回d9eee8de70badb1570127ca551f22b66ec4a5b30。此前HTTP408失败保留，本次不是force-push，没有合并main。此处覆盖“源码分支尚未同步”的旧断点，不能覆盖运行准入边界。

17:48:35只读确认002压缩包9794322432B，未找到runtime210-cu129镜像；不推断传输/导入已完成。下一步由用户完成导入后运行已交付e0bcf2e CPU inventory命令，核实际解释器/版本/commit/路径/依赖。ownership候选185d6821仍未应用/编译/执行，当前原生库未按新源码重建，S4a、S4b仍未通过。收到CPU回归结果解决该部分等待，不自动赋予新的GPU运行gate。

## 用户已导入运行镜像，迁移身份核验通过（覆盖旧等待）

用户报告镜像导入完成后，root只读分别inspect qjhs003和qjhs002的runtime210-cu129：完整ID均为sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27；Size均34184928331B；RootFS 31个层摘要及顺序逐项相同。镜像ID绑定配置文档；本轮未启动容器、导入模块或运行GPU。完整双端身份收据见qjhs002-runtime-image-migration-verified.json。这解除“目标运行镜像缺失”，不证明实际依赖/ABI、当前官方源码已编译安装或GPU运行通过。

下一步仍由用户在002执行现有/p0/qjhs002-runtime-inventory-e0bcf2e.txt：CPU导入核验Python/module路径/version、vLLM commit/声明依赖/已安装distribution、Torch编译CUDA/ABI、Store扩展是否可导入。metadata命令不是S4b guard。当前ownership候选尚未应用/编译/测试，当前源码未重建原生库；S4a/TP8/完整转换/八rank/原生服务仍未通过。压缩包在完整后续验收前保留，未擅自清理。

## 002 CPU ownership、原生库与 SDK 导入里程碑（2026-11-03，覆盖旧等待）

已核源码分支 `kimi-qjhs002-aligned-tail` 的远端 SHA 为 `00097eaafd60dbae262a954a29863b5c633aade9`。本节只保存已有 CPU 证据，不在整理时重新执行容器、编译、模型、Store 或 GPU；源码分支发布不等于 main 合并或运行准入。原始小日志、收据与文件 SHA 清单见 `qjhs002-cpu-00097ea/`。

- ownership 首次因 `/tmp` noexec 导致扩展映射失败，0 tests/11.091s，保留为失败；改为可执行目录后的 retry 为 1 test、5 subcases、11.079s OK。测试使用真实 Torch C++ `from_blob`，设备与 free 为 CPU/stub，仅证明相应 CPU 生命周期回归，不证明 CUDA allocation/IPC 清理。
- 静态构建原生 `_C`、`_checkpoint_store` 通过且扩展可导入；SDK `torch` 可导入，`server`/`cli` 因缺 `libglog.so` 失败，不能将该构建收据的 `passed` 当作整个 SDK 通过。
- shared 构建并修正包内 glog 后，`_C`、`_checkpoint_store`、`torch`、`server` 四项 CPU 导入通过；CLI 仍因 `No module named 'peft'` 失败。两次构建收据的全部 `source_before`/`source_after` 哈希逐项一致。
- 原 shared build 收据错误记录 SONAME 为字符串 `SONAME`，原始文件保持不改。`sdk-shared-install-00097ea-receipt.json` 附更正：实际 SONAME 为 `libglog.so.1`，文件为 `libglog.so.0.6.0`，SHA256 `d76e3fafb8b0b0176a1b5a0ccc9d5db11c5118046b8f31d724731640740da9e6`；错误来自正则捕获 tag 而非方括号值，二进制未变。

用户回传的运行环境原始输出另存 `runtime-inventory-user-output.txt`：Python 3.12.3、Torch 2.10.0+cu129（compiled CUDA 12.9、CXX11 ABI=true）、vLLM 0.19.1 commit `gb1388b1fb`、Transformers 5.5.1、compressed-tensors 0.15.0.1。初始 inventory 未安装 `sllm_store`，须与后续包内导入结果分开看。SDK 声明依赖中仍缺 bitsandbytes==0.46.0、grpcio-tools==1.76.0、peft>=0.16.0；本次证据整理未重跑依赖解析器，不由版本清单推断完整兼容。

S4a/S4b、完整模型转换、真实 Store 读回、GPU/TP8/八 rank 及原生服务均未通过；本节不能解除这些门槛。未归档二进制、权重、大日志或凭据；原始失败及 SONAME 错误收据均保留。

## 002 CPU deadline guard 跨 SSH 探针（2026-11-03）

已有 CPU 探针原始证据、冻结 guard 和启动计划存于 `qjhs002-guard-cpu-20261103-2034/`；本轮整理不重跑。负例故意改错 Created，身份检查拒绝且无 ready，错误清理也未获身份授权。正例 ready PID=1263200；root 从本轮工具原始输出确认原 SSH session 4790 已 Connection closed/exit0，新 SSH 于20:31:00仍见同 PID active。这两项当时未单独落 raw 文件，明确作为事后 root 观察叙述保存。

正例在20:31:08期限到达后实际 `killed`→`stopped`，容器 exit137，最终20:32:31观察时 RestartCount=0，unit inactive/Result=success/ExecMainStatus=0，停止后约83秒未重启。该正例为 runc CPU sleep 容器，无 GPU DeviceRequests、无 privileged、无挂载和额外 devices；仅证明这一具体跨 SSH 主动 kill 探针通过。

冻结 guard SHA 与运行目录原 `guard.sha256` 一致。两个运行 manifest 的 SHA 及所有小证据的 SHA/大小是在观察结束后补建，保存为 `post-observation-manifest.json`；不能冒称运行前已持久化 manifest SHA。原始 stdout/JSON、最终观察、启动计划、root 叙述均分别保留。

该 CPU 结果不授予 GPU/模型/Store 运行权限，不证明 GPU 资源释放、逃逸子进程覆盖、GPU 独立预算及释放观察。S4a/S4b 的完整准入、独立审阅和实际资源/窗口核验仍独立要求。

## 002 下一步探针准备里程碑（2026-11-03，无 GPU 执行）

`qjhs002-probe-preparation/` 冻结六个现有小脚本及审阅范围。这里的 GPU-aware guard 与已归档 CPU 旧 guard 分开：start 前查 GPU XML 全部进程及 cgroup，再由同一进程 start；异常后固定清理截止覆盖 late start。2GiB 仅 sampling stop，绝非硬显存分配上限。guard_recipe_review 最终静态通过仅限1MiB CUDA 微探针，仍需 fresh booking/resource/budget/server hash；本次未启动容器或 GPU。

归档脚本上的纯 stdlib 回归实际 rc0：guard 四例（超阈值、外来 graphics PID、查询超时、start timeout 后延迟启动）；组件 cleanup 为1 test/10 subcases。完整小 stdout/stderr 保留。review_s4a_component 对实际组件 provenance/header/cleanup 复审通过；CPU stub/AST 回归不证明 GPU release、Store 完整读回或模型通过。

准备脚本仍固定 nonce/UUID/源码及镜像，且要求两个源文件置于 p0 根目录、probe 文件名以 cuda-micro-probe- 开头；本里程碑归档路径不等于部署路径，不自动满足该准备合同。具体限制见 REVIEW-SCOPE.md；任何 GPU 微探针或 S4a 仍须独立准入、完整释放观察，不复用此提交作运行批准。

## 002 CUDA 微探针 FAILED_RETAINED（2026-11-03，停止 GPU 升级）

归档 `qjhs002-cuda-micro-failed-retained-20261103/` 保留该正式失败及释放观察，绝不称 GPU 通过、不以重试替换证据。180秒完整计划预算已超限，未进入 Python，CUDA compute/model/Store 均未通过。

| 时间/阶段（+08） | 实际结果 |
| --- | --- |
| 20:49:06 | guard ready；GPU 0MiB、无进程 |
| start/cleanup | start 4秒超时，随后 inspect 反复4秒超时，Docker guard 未能在完整预算内完成释放 |
| root 精确清理 | 仅身份 pin 的 runc PID1368025/startticks25212033，pidfd，TERM 后 KILL |
| 20:54:07 | inspect 解锁，created/Running=false/Pid0/exit128/RestartCount0，未进入 Python |
| 20:56:28–20:57:31 | 13 samples/62.939545秒，均非运行/Pid0/Restart0/GPU0/无GPU PID；精确 owned runc/shim/logs CLI absent |

hook #4 报 Go startup pthread_create EAGAIN；128 PID 限制仅候选，实际 OCI/cgroup post-cleanup 已 gone，未证实因果。只保留512字符以内错误摘录及完整错误/原 inspect SHA，不提交 full inspect、Config.Env 或172k错误栈。FAILED_RETAINED_RELEASE_VERIFIED 仅表示失败保留且所列释放观察完成；停止 GPU 升级、不重试，未来任何 GPU 工作须新诊断/审阅与完整 gate。

同批保留既有 CPU installed-vLLM 补丁干跑：同不可变镜像、runc/noGPU/只读树，安装路径 `/usr/local/lib/python3.12/dist-packages/vllm`，inner patch `4d6271fa7cce8e47ccbc10a39812075d3e806426b8cf0a83b42632e807110f84`；forward `--batch --fuzz=0 --dry-run -p2` rc0、forced reverse rc1、全部 target before==after，classification=unapplied。未应用实际环境，不称运行兼容；本次归档未重新运行。

## S4a 真实组件 CPU SAVE_ONLY 半程通过（2026-11-03）

`qjhs002-s4a-save-only/` 保留已有实际 CPU 日志及最后 JSON receipt。官方 save_dict 保存一个真实 packed component，并对 archive 的原始位逐项比对通过：I32 packed[7168,256]、BF16 scale[7168,64]、I32 shape[2]，shape 实值[7168,2048]；payload=8257544B、tensor.data_0=8261632B、含索引总计8261992B。容器 exited0，后验8 GPU使用量均0。

archive 仅保留于002 `p0/s4a-saveonly-20261103-2103/packed-component`，未下载/纳管payload；tensor.data_0 SHA256=`4920e225718ab1a21e9dd771438c7f1c3c86277b76adfc5c86f91954569a80d8`。实际源码仍00097ea；SDK _C=41d2409…、_checkpoint_store=f757a8e…（完整SHA见验证日志），proto类型T保留且原因未知。versioned libglog.so.0.6.0/.1作为未跟踪编译artifact保留，不stage .so、不改ignore。

这只是 S4a 保存半程 PASS：archive raw-bit 比对不是官方 load_dict 读回，不是完整转换。真实 Store/CUDA 读回、GPU资源释放及完整模型/TP8/原生服务仍未通过；GPU FAILED_RETAINED、预算超限和 no-retry 边界不变。当前CDI fallback即使另有枚举，也不是失败当次实际OCI证据，不据此给hook#4定因。此轮仅归档已有证据，无新计算。

## CPU hook 机制诊断：仅 pids128 version 成功（2026-11-03）

`qjhs002-hook-cpu-diag/` 保留一次 CPU runc `/hook --version` 小证据。host binary SHA256=`fb9e22c288346e9a99cfbef446a8495932073608aa9982628a750cacb129925c`，rc0，非 pthread EAGAIN；before/after 实际 pids.max=128、pids.events max0、memory.events全部0。容器exited0，OS guard inactive/Result=success，后验8 GPU全0。

本次实际 OCI 仅 libnetwork prestart，无 NVIDIA hook；因此只证明128 CPU version成功，不能证明原GPU startup/hook#4失败根因。512条件未满足、未启动；GPU FAILED_RETAINED/no-retry停止边界不解除。当前CDI fallback枚举另存，但不是原失败当次OCI证据；原runtime-diagnostic-fields已在失败目录保留，不重复或覆盖。未保存full container-before/Config.Env，未新增计算。

## S1/S2 与 runtime 只读源码证据补齐（2026-11-03）

`qjhs002-readonly-runtime-provenance/` 持久化依赖求值输入、报告及只读采集/求值脚本。root实际重跑audit rc0，closure SHA256=`c10bbdc522df0c03c9c193f4f97199a9f3ef894fcf28220250e70c8fe756089c`。历史inventory的88条vLLM Requires-Dist按目标Linux/x86_64/CPython3.12.3/default extra求值：65条默认直接distribution版本满足，23条optional extras排除；不是transitive/extras递归闭包。实际SDK requirements13项=10满足/3缺（bitsandbytes0.46.0、grpcio-tools1.76.0、peft>=0.16.0）。wheel metadata允许TF5.5.1并有原生KimiK25/v4-v5分支，但README `<5` 与actual5.5.1差异仍未消除，不证明模型兼容。

hook ELF内联buildinfo实际Go1.26.0/CGO_ENABLED=1，区别runc自身Go1.25.9；host glibc2.35。当前daemon及可读祖先pids无上限、events max0，不追认失败时不存在PID限制；缺失字段记unavailable，不当0。runc createContainer在应用UID/rlimits/seccomp设置前，应用参数不能直接解释hook；Go源码行号与保留栈仍有精确对应缺口。EAGAIN有多种原因，仍无当次OCI/hook限额证据，不重试GPU/不绕过hook。

S2双端只读：003原HEAD9f50241的storage.proto仍symlink `../../proto/storage.proto`，mtime/birth 11:32:16.945592703；002普通文件birth12:51:06、mtime11:32:16截秒，target也相近复制式birth。root补充README/Git目录12:50复制式birth。这支持迁移解引用推断，未找到具体复制命令，用户方式尚待确认；不恢复或纳管proto。源码仍00097ea且未修改，未纳管libs/环境/凭据。此轮仅归档既有只读证据，无新GPU/模型/Store/容器计算，所有GPU停止边界保持。

## 原生 Kimi config CPU 实际通过，初次 UID 缓存失败保留（2026-11-03）

`qjhs002-native-config-cpu/` 保存来源审阅、同脚本两次CPU输出和回执。首次在Torch默认Inductor缓存选路径时因镜像无UID2147账户，`getpwuid` KeyError、exit1，尚未进入Kimi配置；失败完整保留。读取官方cache_dir_utils.py确认支持后，仅本次Docker env设 `TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor`，第二次exit0；未改系统账户、源码或依赖。

实际类 `vllm.transformers_utils.configs.kimi_k25.KimiK25Config`，安装包文件SHA256=`c7b79b5b0369ba3f4f756fede58c552cbc452a8447322c927405638af1fbc984`与wheel源相同；text class=DeepseekV3Config，hidden/moe/expert/layer/head/vocab=7168/2048/384/61/64/163840。torch.cuda.is_initialized=false，trust_remote_code=false、config_format=hf、local_files_only=true。仅bind config.json与probe，不挂权重/tokenizer/SDK；root记录runc/noGPU/netnone/只读/user2147/cpu1/mem4g/pids256/tmp256m，离线HF缓存位于tmp，PID1 timeout60/kill10、host timeout90/kill5。两容器均停止、后验8 GPU全0。仅原生配置CPU路径通过，不是完整模型、tokenizer或GPU兼容，不豁免README TF<5与实际5.5.1差异。

追加双端proto内容pin：002 target与普通proto均SHA256=`95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b`；003对应target同SHA，Git链接文本仍`../../proto/storage.proto`。内容相同不解决类型变化来源；复制方式已问用户未答，不恢复/纳管proto。GPU FAILED_RETAINED/no-retry边界保持，无新源码修改或goal状态更新。

## Native tokenizer CPU smoke 通过，但语义差异明确（2026-11-03）

`qjhs002-native-tokenizer-smoke/` 保留实际stdout/stderr、postcheck、receipt及只读regex/特殊ID审计。HF5.5.1 native fallback构造和单样本roundtrip成功、容器exit0、CUDA未初始化、后验8 GPU全0；仅挂5资产2809638B、无权重/remote Python/SDK/Store，offline/noGPU/只读镜像、UID2147与显式tmp缓存。来源准入引用既有 `qjhs002-native-config-cpu/runtime-native-config-admission.json`，不重复。

不记semantic PASS：实际PAD=163606，而官方配置PAD=163839、163606对应`<think>`；len163607/base163584只有23 added tokens，官方明确保留256槽至163839。Mistral警告来自宽泛检测，不是Kimi专属regex比较，不盲设fix_mistral_regex=True。官方Kimi Han/大小写分支pattern与TF generic不同；实际runtime pretokenizer及精确text IDs需下一独立CPU reference probe，当前只读计划未执行。receipt保持semantic_parity_verified=false、regex_warning_resolved=false。

仅source/smoke证据，不证明S1模型兼容，README TF<5差异仍在；不修改生产代码、依赖或目标版本。GPU FAILED_RETAINED/no-retry边界不变，无新GPU计算。只读tokenizer源码小证据已归档，不挂载或执行checkpoint Python。

## 用户最新规则覆盖：适配基线迁移完成（2026-11-03）

今后仅修改 qjhs002 `/data/brick1/boyjiang/kimi-new/ServerlessLLM`，覆盖历史唯一旧root要求；source仍00097ea、kimi-qjhs002-aligned-tail、origin BoyJiang2/serverlessllm.git。迁移收据核13源码/测试SHA与5artifact/alias；迁移后4core导入成功、CLI已知缺peft。证据见 `qjhs002-baseline-relocation/`，authority见ACTIVE-BASELINE.json。

旧适配树same-FS rename至 `p0/baseline-relocation-20261103/retired-adapted-checkout`，保留build/history无大拷贝。旧root `/data/brick1/boyjiang/ServerlessLLM` 重建官方main9f50241，独立objects/无alternates、tracked/untracked clean、origin官方.git。服务器GitHub直clone两次TLS失败后，local clone --no-hardlinks复用已核原始Gitcommit，Windows上游main同9f；不称服务器网络clone成功。

用户确认recursive scp；官方scp手册-r跟随symlink，proto类型变化因果关账。target普通1994B文件/元数据/diff先在服务器迁移目录保全，再仅target恢复Git120000 `../../proto/storage.proto`，内容95547af…不变；旧历史不改。未修改生产源码commit、未stage库/权重/环境，原repo用户dirty AGENTS不stage。迁移完成不等于适配完成，GPUstop/FAILED_RETAINED/no-retry及GLM冻结不变；不继续实验、不改goal。

## 已完成 CPU tokenizer core divergence 证据补存（2026-11-03）

`qjhs002-tokenizer-core-divergence/` 保留迁移前已完成的CPU探针，receipt逐字段等于原stdout最后JSON，postcheck容器exited0、后验8 GPU全0。结果确认：18个named ID错误、233个reserved槽缺失、12个文本用例中1个ID序列不同（`I'm WE'RE Don't CAN'T`）；两侧decode均可往返，不能将roundtrip当ID语义相同。

参考只从官方源码AST提取literal pattern/reserved常量，使用既有tiktoken及2.8MB本地资产；未import checkpoint Python，不是模型/GPU/chat-template验证。运行时native pretokenizer已捕获，generic pattern与官方Han/大小写分支的实际差异证据保留，不设fix_mistral_regex、不改依赖/生产。

同存只读下一路径选择：优先复用既有vLLM `tokenizer_cls` 与官方class进行独立CPU验证，尚未执行，需显式可信源scope；TF4.57.1候选metadata显示hub<1与当前1.14冲突，不能盲降级。当前唯一适配目录仍 `/data/brick1/boyjiang/kimi-new/ServerlessLLM`；旧root pristine9f不动，source00097ea不变。GPU停止/no-retry与goal状态均不变，本轮仅小证据归档。

## Official tokenizer CPU core/cache/pickle 通过；生产 factory 尚未接入（2026-11-03）

`qjhs002-official-tokenizer-cpu/` 保存实际已有输出、booking、脚本和回执。`--allow-reviewed-local-code` 限定root原任务授权及两次完整源码独立审阅的scope，现有get_tokenizer显式传入official tokenizer_cls；7资产<3MiB、sandbox runc/noGPU/netnone/只读/user2147，HF_MODULES/cache仅tmp。未将任意checkpoint Python授权扩展到其他代码；stderr原encode警告原样保留。

实际original/cached/pickle恢复三个对象均匹配23 named ID、256 reserved槽和12 gold语料精确IDs；PAD163839/UNK163838/vocab163840，torch CUDA未初始化。pickle6733495B仅在内存，无序列化payload文件归档；postcheck容器exited0、8 GPU全0。receipt逐字段等于原stdout。

这是直接class CPU路径验证，不是生产修复：native CLI/LLM factory仍未接入，生产修改候选仅work且未apply；`vllm-tokenizer-production-api-review.md`明确真实factory位置及调用边界。既有支持路径选择引用 `qjhs002-tokenizer-core-divergence/kimi-tokenizer-support-path-selection.md`。不证明chat/tools/model/GPU、S4a官方读回或原生服务；TF README版本差异和所有GPUstop/no-retry保持。唯一适配新root `/data/brick1/boyjiang/kimi-new/ServerlessLLM`、source00097ea不变，无新生产/依赖/goal改动。

## S5 真实 canonical tokenizer factory CPU 通过；源码 dd4b60b 已发布（2026-11-04）

`qjhs002-canonical-tokenizer/` 保留已完成unit/真实factory CPU/安装树干跑及源码发布证据。newroot只提交官方 `sllm_store/vllm_patch/sllm_load.patch` 与 `tests/python/test_vllm_kimi_tokenizer.py`，source=`dd4b60bb9b19183fc45896d5b7fd778a9f98ad44`，non-force推至kimi-qjhs002-aligned-tail且tracking已CAS更新。此前7个ServerlessLLM生产路径计数不增，测试变7；runtime patched target增加hf.py hunk属于同一inner patch，不是新codec/框架。

服务器实际6factory unit/0.017s与1loader/0.003s通过；local6unit通过。normal get_tokenizer未传tokenizer_cls，真实HF factory通过candidate只读overlay运行，23 named/256 reserved/12 gold、kwargs encode/decode、cached/pickle、真实HF nested subfolder同gold均通过，PAD163839/vocab163840/CUDAfalse。标准encoder/cache tail保留。仅TF5.5.1+strict trust=True+local Kimi config+exact mapper选择官方class，失败不generic回退，其他profile/None子目录/参数由unit覆盖。

原inner前12057B保留；new hf candidate SHA=`0d620af05b7c8b86e5c7d3c34db180ad058c4564694dea35911e476ad31df480`，whole inner SHA=`a734b0864c0bff21357db8b83ab1a46955abb381e136bc506f4c752067acab53`。candidate-manifest是最初静态阶段历史收据，其“未测试”状态由本节实际证据覆盖，原文件不改。whole inner实际安装树strict forward0/reverse1、全部target unchanged，classification=unapplied：不可变镜像未安装完整patch，sync不是部署批准。

唯一适配根仍 `/data/brick1/boyjiang/kimi-new/ServerlessLLM`；旧root HEAD仍9f，本轮未触；source-final小快照有untracked models/，不据此称当前全树clean或擅自清理。仅CPU factory限定通过，不证明chat/tools/model/Store/GPU/S4a官方读回，TF README差异与GPUstop/no-retry、GLM35/5及goal状态均保持。

## OS 原生 guard CPU 机制 proof 通过（2026-11-04）

`qjhs002-os-guard-cpu/` 保留已有CPU proof与仅四个远端指定小文件。实际Type=exec、Restart=no、DelegateControllers=cpu memory pids、RuntimeMax10s/Stop3s、KillMode=control-group/FinalSIG9。ready时unit root procs为空；supervisor与container两个descendant cgroup共3角色，均忽略TERM。最终Result=timeout、ExecMainCode2/Status9，StartMono267382437077→Exit267395520626=13.083549s。13次释放观察实际跨度>=60s，全部精确owned PID+starttick身份不存在、unit cgroup removed。

限制必须保留：交互launch后properties捕获命令被TTY截断，报`roperties.txt not found`；fresh SSH权威show已完整恢复，属性以final文本为准（Restart=no另见fresh live文本），不能伪装launch CLI全程无错误。此proof仅CPU睡眠/OS清理机制PASS，未跑runc/hook/GPU/模型/Store，不证明原GPU失败根因或GPU授权，GPUstop不解除。source dd4b60b、新根唯一、GLM35/5、goal不变，本轮仅证据归档。

## Canonical CPU chat 三个有界用例通过，首次fixture失败保留（2026-11-04）

`qjhs002-canonical-chat-cpu/` 保存原/修正脚本、两次stdout/stderr、源码审阅及postcheck。首次容器exit1：参考工具声明fixture漏终端LF；按官方tool_declaration_ts.py末尾 `}\n` 源码证据，仅修正参考tool_text末尾LF，不修改生产、断言、flag、用例或gold。原脚本SHA14151988…与修正fb9029dc…分别保留；correction-review为修正前审阅，原“未执行”状态由本次实际corrected结果覆盖，历史不改。

修正容器exit0，stdout最后JSON=`CANONICAL_KIMI_TOKENIZER_CPU_CHAT_CASES_PASS`；default thinking、thinking=False和tool declaration三个有界纯文本用例的官方template渲染及core BPE IDs均对独立参考一致，控制token恰一次；tool仅声明不执行。CUDA未初始化，后验8 GPU全0。只读factory overlay仍不是原镜像完整patch部署。

仅上述CPU chat cases PASS，不覆盖default AutoProcessor解析、HTTP服务、多模态、工具执行、模型/权重/Store/GPU或S4a官方读回。source仍dd4b60b、新根唯一，GPUstop/GLM35/5/goal边界不变；本轮只归档，不新运行。

## Scratch runc CPU hook 实测完成（2026-11-04）

`qjhs002-scratch-hook-cpu/` 保存指定小证据；trace证实nvidia-cdi-hook --version exec成功、打印1.19.0并exit0。leaf曾实际128 PID/2GiB/cpu1、pids.events max0；删除后字段为missing/errno2，不补0。runc create1为预期缺 `/__cpu_probe_no_app__`（hook之后），wrapper0/delete0，不称应用或模型通过。

7个实际PID+starttick身份均在13样本、60.104323秒跨度观察中absent，unit cgroup removed。unit runtime310ms来源root的交互TTY，不从已collected unit-final的0单调时间伪算。raw strace168118B仅留远端原run目录，SHA256=`788e44ccba51e416aa9d3840949a76af04fea3dc411c6d52e042688799303e40`，本地只保存trace-analysis首行摘要，无raw下载。

这是CPU instrumented hook成功控制；原GPU故障未复现、根因仍未知、GPUstop不解除。source dd4b60b/新根唯一、GLM35/5/goal保持，本轮只归档已有结果，不启动新fixture。

## 原失败 CID 的 PID-controller 拒绝已确认（2026-11-04，只读历史证据）

`qjhs002-original-pids-rejection/` 保留20:48–20:55 journal筛选（374 records）及原kernel entry/cursor/bootID、独立源码审阅。原CID c6ae802…在20:49:07.951685出现 `cgroup: fork rejected by pids controller in /system.slice/docker-c6ae802…scope`；原始bootID与root报告当前boot相同。新增结论 `ORIGINAL_PID_CONTROLLER_REJECTION_CONFIRMED`，不改历史cuda-micro-failed-retained.json或正式失败终态。

该路径是initial charging task/目标cgroup，不识别实际limiting ancestor；首个event打印一次不等于只拒绝一次。与同CID启动窗口hook pthread EAGAIN强关联，但kernel无PID/TID，具体hookthread、local128还是ancestor拒绝、任务数和压力来源仍未知；upstream5.15与Ubuntu补丁差异未核，不由当前CPU控制或ancestor快照追认历史。至少一次原始PID-controller拒绝已确证，不等于完整GPU故障根因闭环，更不授权调限或GPU重试。

source dd4b60b、新根唯一、GPU FAILED_RETAINED/no-retry、GLM35/5/goal不变。本轮只归档五个指定小文件，不打包大booking或其他并发证据，不运行新计算。

## 用户批准现有 prepare 入口迁入 active root（2026-11-04，源码已发布）

`qjhs002-prepare-entry-migration/` 保留最小迁移、4项offline回归及独立source-scope PASS。active新root仅stage并提交 `sllm_store/tests/prepare_cuda_micro.py` 与 `sllm_store/tests/python/test_prepare_cuda_micro.py`，source=`e4da57ec70f1259f4f5a5aa2e13670f801c44f8f`（parent dd4b60b），GitHub sourcebranch已non-force推送并核SHA，服务器tracking ref以CAS同步。两项glog未stage，本地证据repo用户AGENTS不stage。

entry SHA=f99229c338a240b7a5f78dd1d635004038414c4763f082ad73938280ab2e1c29，test SHA=adb6fa18f07425776d6f5e3bec78c158e737a2c4856e54ee06c1f5c0deac3274；本机取得已提交对象再次核相同。最小改动为单PID参数(default128/显式512)、新root/actual source/newnonce/inspect PID一致性；4offline tests/0.012s通过，未创建容器。历史入口/prepared/FAILED_RETAINED原样并记录SHA，不因本次迁移覆盖。

native-scope deadline review仅source-only附录，181KB输入仅记SHA不重复打包；预算/guard字段是声明，不等于guard已通过或运行准入。当前唯一新根、原版旧根/GPUstop/no-retry/GLM35/5/goal保持；未新跑GPU/runc fixture或修改依赖。

## 当前 guard/probe source 候选已提交，GitHub 发布待网络恢复（2026-11-04）

active仅新增并stage三个任务文件：`sllm_store/tests/guard_cuda_micro.py`、`sllm_store/tests/cuda_micro_probe.py`、`sllm_store/tests/python/test_guard_cuda_micro.py`；source=`b1f14fef05b31516e5ff05adaa09aeb4bec2379f`、parent e4da57e。glog未stage。服务器commit与本机fetch对象成功；GitHub默认proxy127.0.0.1两次拒绝，本次direct443也连接失败，故不称已公开，server origin tracking仍e4da；持久proxy配置未改。

`qjhs002-guard-source-candidate/` 保留4offline PASS、旧2scope缺陷EXPECTED2FAIL、guard独审允许单次bounded CPU fault scope、normal-exit probe独审及CPU recipe。recipe状态NOT_EXECUTED；offline不验证kernel cgroup/pidfd，声明时限/预算不算guard实测或GPU准入。旧75ba rejected源码不是当前候选，不替换历史；probe仅marker及去掉hold支持正常退出，原GPU payload不改。

本轮无容器/probe运行，正式CPU注入尚未执行，GPUstop/GLM35/5/goal均保持。源码与证据commit本地/服务器保留，待GitHub网络恢复后普通push并核SHA，不force。

## CPU1 FAILED_RETAINED 与日志有界修复源码发布（2026-11-04）

`qjhs002-cpu1-failed-bounded-log/` 保留CPU1小证据；终态FAILED_RETAINED，失败event_log_cap。scopekill25.0196s、runc KILL28.0258s、quiet60.009s仅组件事实，不等于overall PASS。原8MB event log与raw OCI仅远端root保护保留，未下载。

source a54102a4559ab4ecbf510ea9df30f66519b0ff00（parent b1f14fe）仅提交guard938、test887、实际executed driver504三文件。先capture/stage504后通知root，本轮不覆盖后续CPU2的RUN/guardSHA修改；guard日志修复5offline PASS+独立source-scope PASS不等于新kernel实测。direct GitHub push成功，source及证据旧pending b1/ac6均已公开并核SHA；server source tracking以CAS e4da→a541，此前pending历史原收据保留，此处覆盖其当前发布状态。

新root唯一，glog/用户AGENTS不stage，无新容器或probe；GPUstop/GLM35/5/goal不变。

## CPU2正式守护机制验证通过（2026-11-04）

qjhs002-cpu2-guard-pass保留10个小证据含完整17542B guard-events及fresh booking；raw OCI0600仅远端保护不下载。source870a25ef22dd8fb80f1cea4cab0a5c34b0e47a42（parenta541）只改实际driver208b的RUN/guardSHA两个常量，AST归一化完全相同，GitHub/source tracking已核。

终态KERNEL_CLEANUP_RELEASE_PASS，failures空；scopekill GO25.0168160、runc KILL28.0187818早于native40，quiet60.020760858；root确认driver返回prompt、guard正常Code1/Status0/success后专属unit已stop。CPU1保持FAILED_RETAINED/event_log_cap，不覆盖。仅CPU守护机制通过，不等于CUDA/GPU准入或原故障完整根因；GPUstop/GLM35/5/goal保持，本轮不新运行。

## GPU512 source-only安全边界发布（2026-11-04）

qjhs002-gpu512-source-only保留独审、计划、stopped profile及源码。active仅新增controller一文件，source b8682b2a9ce4857564a088a3efcf4290cfab7476，SHA7e8e2f04e22eaa0af78418b0d14e3fca78ae08d0509466d007a03796f482f2a7，GitHub/source tracking核验。唯一CID9fe3e1129a2d700c3acb9d36d37945d937906e4d680ff8b2a0766941fba6a1e9已create未start，实际Pids512，其余旧profile字段及ENVhash逐项相同。

独审SOURCE_REVIEW_PASS_CONDITIONAL_ON_FRESH_ADMISSION；未有CUDA PASS，source同步不等于GPU准入。root须安全边界停止Git后fresh booking/resource/full budget及ARM；本轮不start、不改controller，历史失败/GLM/goal不变。

## 唯一GPU512正式终态 FAILED_RETAINED，不再重试（2026-11-04）

qjhs002-gpu512-failed-retained保留已导出小证据。controller断言容器ExitCode失败，Stateexited/exit1/Pid0/Restart0/noOOM；root观察Python marker后torch.cuda.device_count()==1断言失败，无CUDA marker。guard终态abort_requested/quiet60.027080，observer peak0/quiet60.316816；root确认二者MainPID0且已stop专属unit。仅释放观察/组件事实，不称CUDA PASS。

ConfigEnv实际NVIDIA_VISIBLE_DEVICES=void、CUDA_VISIBLE_DEVICES空；首版OCI同空且重复NVVISIBLE UUID。guard发生两次OCI选择；raw仅首版dd3c…，第二73b3…仅安全摘要，不能将首版raw称最终完整OCI。实际scope pids512且捕获events max0，不等于完整PID因素解除；exactCID kernel query rc1/no entries不是内核证明无PID拒绝。完整root rawOCI不下载，精确任务身份postcheck及Torch mask静态解读待另补。

source仍b8682b2，无代码修改；原始GPU/CPU1历史失败不改，本次不再start/retry，模型/Store/TP8/goal/GLM35/5保持边界。本轮仅归档，未启动计算。

## 可审阅 visible-env 修复提案已发布，仅 source-only（2026-11-04）

qjhs002-visible-env-proposal保存3文件候选/patch/hash、独审PASS_NO_EXECUTION_AUTHORIZATION、remote4offline PASS、实际Torch静态解读与GPU512精确身份postcheck及失败probe日志。source cc3fd227227c45f6934aba98ca35408294ba1a59（parentb868），仅prepare/test/controller三文件，GitHub/source tracking已核。两visibility ENV显式指定同UUID、新nonce cuda-micro-pids512-visible-20261104/controller BASE两unit对应；PID仍显式512，其余保持。

安装Torch源码确认空CUDA_VISIBLE_DEVICES解析为空可见设备；不等于证明物理device injection/accessibility，第二OCI只有安全摘要限制不消除。原9fe3一次额度已执行FAILED_RETAINED，下次create/start/GPU须用户另行批准。本轮没有新create/start或运行controller，源码同步不授新执行权限。原失败/原始root OCI不改不下载，模型/Store/GLM/goal保持。

visible-env路径说明：candidate布局 `tests/test_prepare_cuda_micro.py` 的真实active source路径为 `sllm_store/tests/python/test_prepare_cuda_micro.py`；另外两项真实路径为 `sllm_store/tests/prepare_cuda_micro.py`、`sllm_store/tests/gpu512_once_controller.py`。候选布局不是部署测试路径。

## 用户批准唯一修正版微探针 PASS（2026-11-04）

qjhs002-visible-gpu-micro-pass保存controller终态GPU512_MICRO_NORMAL_EXIT_RELEASE_PASS及实际PYTHON_ENTERED/CUDA_MICRO_COMPUTE_PASS；容器normal exit0/Pid0/Restart0，H20-3e，payload/allocator peak1MiB、reserved2MiB。guard exit0无failures/quiet60.022521385，observer exit0/quiet60.591078338/采样peak21MiB，elapsed85.794381s。exact shim1812212/runc1812224 startticks身份均absent、scope removed；两unit MainPID0/Code1Status0/success捕获后root stop专属units。

成功配置是PID512加两visibility ENV同UUID override，不宣称单PID唯一根因或最小阈值。首raw OCI19bb仅remote0600保留，guard selected可能多版本重写，首raw不当最终完整OCI；kernelqueryrc1/records0不排除PID因素。原128fail、9fe3maskfail、CPU1eventcap均原样。仅1MiB CUDA微探针及释放通过，不证明模型/Store/TP8/S4a读回；sourcecc3/controller6597/guard938不变，无空sourcecommit，goal不complete。

## S4a 下一步只读源码与身份核验（2026-11-04）

qjhs002-s4a-readback-readonly保留READONLY未run计划、11文件SHA映射及archive/index/SDK/glog新hash（匹配既有收据），不重复源文本或下载payload。当前官方serve绕CLI peft无需新增依赖；64MiB仅host pool，Ctor无CudaMemoryPool；calculate_device_memory/native Allocate请求8257544B；RestoreTensors共享base owner；confirm RPC失败已raise，无新patch理由。

Torch allocator peak不含native CUDA，后续须driver/NVML及保留alias/最终view生命周期证据，不能仅进程退出称所有权释放。未知耗时不另立硬gate；readiness/client/shutdown cutoffs应拟合GO25/cleanup65/release60/full180，超时failclosed，不盲改guard。现有draft重save/读checkpoint，最小existing-archive+receipt分支和CPU bad-input tests仍未实现，不新codec/loader/framework。本轮无Store/新GPU、无源码改动，sourcecc3不空commit，gold/history/GLM/goal保持，READBACK_NOT_RUN。

## S4a CPU准备源码公开；SDK失败清理候选拒绝（2026-11-04）

qjhs002-s4a-cpu-preparation保留获审3组件测试源码、stdlib3focused+1cleanup PASS（含Linuxsymlink）、既有8MB真实archive三tensor raw/index/gold CPU验证PASS。Tensor构造为stub，无Torch/SDK/GPU/Store/readback；新gold小receipt仅运行assetcopy，历史gold不改。source838bfe7309506370aa9ad8ab70988a4c7b42706d（parentcc3），只stage3tests，CRLF源码原字节哈希保留、CR-aware diff check通过，GitHub/source tracking已核。

S5 c947本地草稿独审REJECT_C947_CANDIDATE_FOR_DEPLOYMENT：RPCerror可能server仍借IPC，owner.clear可能UAF；旧2FAIL/新6mockPASS不证明IPC安全，不可部署。官方torch.py仍原a4fb54…，root未采纳SDK改动，草稿不作为生产stage。14源SHAmap保留而不重复全文，无新codec/loader/framework。

CPU准备PASS不是运行授权，readback未run，无模型/Store/TP8/GPU运行或goalcomplete；原fail/gold保持，glog/AGENTS不stage。

## Blocking确认先于RestoreTensors最小顺序修复（2026-11-04，source-only）

qjhs002-blocking-confirm-order保存独审含packaging附录、旧realAST2FAIL/新7mockPASS、active实际14stdlib PASS（7ordering+3confirm+3archive+1cleanup）。source ae49c965d64ded15af639e31838b4ddf0b6bb452（parent838）仅torch.py/ordering test/confirm error test三文件。SDK SHA1dd5eb…、两test SHA f8e51d…/bf4ab5…核实际提交对象一致，GitHub/source tracking已核；glog/AGENTS保护。

load_dict传private kw-only True并删除重复confirm，default nonblocking False不变；submitTrue先成功Confirm（copy完成+IPC全close）再RestoreTensors，避免blocking callee异常时提前释放已交付owner。不是c947提前clear/free方案，c947拒绝仍保持。实际路径包装及真实client confirm原cause未削弱，mock14PASS不是native IPC/GPU证明。

RPC/confirm失败时rawclient allocation仍需未来component server-before-client明确barrier；nonblocking/S5 globalfailurepending，不称全面ownership或真实GPUfree。next-plan最终选择blocking-only；proto fence仅备用，不新建active-cancel框架或作为窄S4a前置。无新C++编译、Store/GPU/权重copy或模型/TP8/goalcomplete，原fail/gold保持。

## 窄 cooperative barrier CPU准备源码发布（2026-11-04）

qjhs002-s4a-cooperative-barrier保留独审source-only PASS及active20tests PASS（6barrier+1cleanup+3archive+7blocking+3confirm）。source c0a89dab91a11438530c7c8ca4c9cb80262fd9d0（parentae49）仅4获审组件runner/test文件，逐对象SHA匹配，GitHub/source tracking已核。

父进程stdlib-only，official serve/client同container；absolute GO+8/16/21余4，client异常保异常/owner等server PID/startticks verified reap ACK才clear；server不能reap不ACK、不先杀client，park交nativeguard且FAIL。ACK_SENT不等client完成barrier，clientcrash=NO_BARRIER；成功alias eagerfree须先于serverstop。-I显式SDK路径/LD prepend保留image原path。

这仅cooperative CPU准备，不是运行授权或S5全面修复；仍缺root controller写0644 component-go metadata/mounts/identity/runtime核验。无Store/GPU/新container运行，旧officialroot及goal不改。ACTIVE当前UTC时间/scope已纠正：获批correctedmicro组合配置已PASS，历史fail保留；S4a/fullmodel/Storeconversion/TP8未run，下一runtime仍需独立gates，不将历史stop误写所有futureGPU绝对禁止。

## S4a success正常服务exit0条件tiny修复（2026-11-04）

qjhs002-s4a-graceful-success保留独审附录及active23CPU PASS；source8c2696513dced00f9d403acda37c089dc1895b1e（parentc0a）仅runner b5e8与barriertest68c3两文件，GitHub/servertracking核验。success path核owned PID/startticks后SIGINT，利用官方asyncio serve cancel/stop5，原GO21内wait必须rc0+verifiedreap；结果记录server/clientcodes、stopkind、forced。非0/timeout整体FAIL走原server-firstTERM/KILL，failure path不改；forced=True仅表示进入fallback，不伪称必发KILL。

这是source-only/23mock CPU准备，不是实际signal/Store/GPU/runtime通过，不承诺stop延迟。此前SIGTERM/KILL不能称正常exit0的歧义已修；其他barrier局限/下一controller准入仍保持。无新Store/GPU、旧root/历史/AGENTS/goal不动。
