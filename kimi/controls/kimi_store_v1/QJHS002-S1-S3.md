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
