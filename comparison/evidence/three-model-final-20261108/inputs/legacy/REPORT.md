# Kimi-K2.6 / GLM 最终实验报告与验收记录（整合版 V2）

本版整合已发布的正式比较、所有者L1功能接受、历史日志加载跨度、实际运行参数、物理资源及成本模型。没有新增GPU/容器/Store运行，也没有补跑GLM或修改原始实验/金标/失败。

**当前L1状态：所有者已接受既有证据完成L1功能验收。原始运行仍为DIAGNOSTIC_NOT_L1_NATIVE_OBSERVATION_RELEASE_COMPLETE，旧30秒FAIL保留；没有新执行的正式L1运行。** 所有者决定0fc1…单独保存。协调者较早接受、原运行分类、所有者后续接受是三份不同记录，未倒置其发生顺序或重写原始终态。

新证据补齐了38个原始合格样本的模型加载阶段日志跨度与76条实际导入记录。f7计时为getloader之前至loadmodel/可选LoRA、drafter、aux之后；不是纯ingestion、八rank最大值或纯CUDA传输。该范围满足原goal“权重摄入/加载阶段”中的加载阶段观测；仍未分离的纯分量按V2保留缺测。原成本v1及早期pending审计仍按其生成时间保存，本版给出当前解释。

源码版本分三条：GPU实际86fd/v8及835/v9；统计实际a43734f；只读日志collector源码fdc3643。main816d80fd的正式对比、add4659d的成本/审计、88d8fef5的所有者决定与命令、cd446bdf的实际日志/资源证据均已发布并核远端；本整合版以所在Git提交及服务器发布回执标识，不将本地提交先称已同步。

## 最终九项交付导航

| 项 | 当前交付与范围 |
|---|---|
|1 L1状态/决定|所有者明确接受既有功能证据；原NOT_L1/30FAIL不改。见owner acceptance与L1 evidence audit。|
|2 版本/补丁/ELF|两runtime版本、20生产路径patch、四ELF/构建/RO挂载、oldf7 lineage、实际76imports；更强未采每PID maps单列。|
|3 对齐表|本地NVMe资产精确字节、量化/架构、实际engine/template/request参数与省略/未知逐项区分。|
|4 正式入口|冻结实际四子条件消费者，57CPU及真实block身份；CPU不代替模型通过。|
|5 台账|原40 Kimi38/2/0，补跑1/1，历史5次1/4；GLM原40固定35/5；全部原始数组/引用/版本分层。|
|6 统计|Kimi8完整blocks/GLM6 C23，四比较、两个独立Holm4族、paired bootstrap/Wilson与独立复算。|
|7 成本|38个E阶段、模型加载日志C、已就绪D、转换controller范围、配置及观测资源；未知纯分量不填0。|
|8 局限|跨模型/容量/软件不同，非论文同协议复现，无warm service/迁移/吞吐/实测N复用/全payload相等承诺。|
|9 发布/复现|源和证据版本分开，完整SHA manifest、CPU重算/绘图命令、Git远端核验后发布回执。|

下文保留正式比较主体的数值与样本规则，随后给出新增加载、资源、参数和成本证据。历史报告中的“本轮”指其原交付阶段，当前状态以本页开头及新证据节为准。

## 结论
两模型各自的 Store 路径均有更低的注册后首业务等待时间。Kimi 在 8 个完整 block 上，Store 冷盘相对 SF 冷盘为 5.27× 加速，池命中为 8.02×；GLM 在 6 个完整 C23 block 上分别为 2.51×、7.12×。这些是各模型内部的均值之比，不能据此声称 Kimi 比 GLM 更快或实现更优。

加入实测准备成本后，Kimi 池命中相对 Store 冷盘的均值优势从 82.656 秒收窄为 17.579 秒（Prep+A）；GLM 的池命中 Prep+A 反而比 Store 冷盘高约 5.5 秒。GLM 准备阶段可能包含前一池卸载，Kimi 是目标条件构造与准入，不能将这个差异全归因于加载实现。

## 图 1：论文式主对比
![主对比](fig1_startup_means.png)

参照 [ServerlessLLM / OSDI 2024 Figure 6a](https://www.usenix.org/system/files/osdi24-fu.pdf#page=11) 的分组均值柱图形式；本图指标是注册到首道算术回复，论文 Figure 6a 是 checkpoint loading。仅借鉴形式，不复用其数值，不宣称同指标复现或 10–200× 系统结果。
柱为算术均值，黑点为各 block 的实际值；右图在模型内部除以自身 SF 冷盘均值。未画未经定义的跨模型配对检验。

| 条件 | Kimi 主集 n | Kimi 均值/秒 | GLM 主集 n | GLM 均值/秒 |
|---|---:|---:|---:|---:|
| SF 冷盘 | 8 | 1271.465883 | 6 | 448.496359 |
| SF 热缓存 | 8 | 441.607398 | 6 | 185.754312 |
| Store 冷盘 | 8 | 241.266401 | 6 | 178.399301 |
| Store 池命中 | 8 | 158.610811 | 6 | 63.012245 |

## 样本范围与失败
- Kimi 原计划 40 项全部启动：38 合格、2 失败、0 未启动。主完整 block 为 1/2/3/5/6/7/8/10，共 32 个样本；b04 和 b09 整块不进入任何主配对比较。
- b04 首次 SF 冷盘缓存准入失败、b09 SF 热缓存 worker 身份发现权限错误均保留。b04 额外 SF 冷盘 retry 成功单列，不替换原失败；b04 第四项池命中已执行成功，计入原 40 项的描述集。
- Kimi 额外补跑 1/1 合格；更早历史 5 次启动 1 合格/4 失败按旧标准保留。三类不混样本；累计形式上的 46 次/40 合格/6 失败也不冒充当期 40 项成功率。
- GLM 原 40 项保留 35 成功/5 失败；全量图标记不同协议。当前 C23 描述集 26 条（6/6/7/7），主完整 block 5–10 共 24 条；其余旧协议成功不混入主推断。
- 全量结果的 38/40 与 35/40 是描述性运行通过比例，试验边界、修复历史与故障机制不同，不能作为两模型可靠性的因果比较。

![全部原40项](appendix_original40_outcomes.png)

全量逐行表：[all-runs-ledger.csv](all-runs-ledger.csv)，共 Kimi 46 行（当期/补跑/历史明确分层）与 GLM 原 40 行。GLM 失败 A 为空，热图写 FAIL；Kimi 历史旧 schema 的 A 不重算，保留原始嵌套 metrics。

## 图 2：全部当前有效样本的分布
![ECDF](fig2_qualified_ecdf.png)

Kimi 当期描述集 38 条、各条件 n=9/9/10/10；GLM C23 描述集 26 条、各条件 n=6/6/7/7。部分 block 的成功只用于此描述图，不用于补成主配对。失败没有赋值为 0，也没有丢失其原始终态。

## 计时对齐与图 3：成本窗口
- A：实际 HTTP 客户端发起 /register 之前 → 首题 17+25 响应读完、JSON 解析及 context 退出完成的 received_ns，早于金标断言。独立注册/结束事件与 HTTP metrics 的时钟、requestID、client PID/CID、整数 ns 端点三方核对。父进程轮询、校验、打印和落盘不计入 A。
- GLM 179.112278228 秒是 cold_09 的该 A 值，恰为 C23 Store 冷盘 n=7 中位数；描述均值 180.874626875 秒。主完整 n=6 的均值为 178.399300519 秒，不与描述 n=7 混用。
- 请求顺序：算术 → cat 翻译 → 天空 fixed32。两模型使用相同文本、各自 tokenizer/chat template。Kimi thinking=false / preserve_thinking=false，正式温度 0、seed 20261024；Kimi 显式 top_p=1/top_k=0/min_p=0/n=1，GLM 未显式发送的参数不冒充已核实同值。
- 算术与翻译 max_tokens=128、ignore_eos=false，严格模型自身金标。天空 max_tokens=32、ignore_eos=true，按采样前选定的新标准要求实际 32 tokens/length，文本差异单列；旧逐 token 金标失败不改写。
- Prep 是实测目标缓存条件构造/准入；Gap 是准备结束到 HTTP 客户端发起注册；连续窗口 = Prep+Gap+A。Prep+A 是预定次指标，明确排除 Gap。公用环境与 640 GiB Store 池初始化、最终关停及释放都不在这些窗口内。

![准备与响应窗口](fig3_preparation_gap_startup.png)

| 模型 | 条件 | Prep/秒 | Gap/秒 | Prep+A/秒 | 连续窗口/秒 |
|---|---|---:|---:|---:|---:|
| Kimi-K2.6 | SF 冷盘 | 49.397869 | 0.094468 | 1320.863751 | 1320.958220 |
| Kimi-K2.6 | SF 热缓存 | 167.310532 | 0.097632 | 608.917930 | 609.015562 |
| Kimi-K2.6 | Store 冷盘 | 26.409528 | 0.098389 | 267.675929 | 267.774318 |
| Kimi-K2.6 | Store 池命中 | 91.486147 | 0.097849 | 250.096958 | 250.194807 |
| GLM-5.1-FP8 | SF 冷盘 | 39.818386 | 0.000239 | 488.314745 | 488.314985 |
| GLM-5.1-FP8 | SF 热缓存 | 323.429570 | 0.000250 | 509.183882 | 509.184132 |
| GLM-5.1-FP8 | Store 冷盘 | 11.863888 | 0.000240 | 190.263188 | 190.263429 |
| GLM-5.1-FP8 | Store 池命中 | 132.753840 | 0.000230 | 195.766086 | 195.766315 |

GLM Prep 在部分条件转换中包含前一池卸载（描述集 14/26）。上图是各自已测成本范围，不表示相同纯 I/O 工作。没有把相互嵌套的设备初始化、权重流水线、量化后处理阶段重复堆叠。

## Kimi 配对推断
原始 ns 重推、独立数学复算通过。每个比较用相同 8 个完整 block；双侧精确符号翻转 abs(mean delta)，枚举 256 个符号，四项一族 Holm。Bootstrap 为成对 block 重采样 10000 次、MT19937 seed=20261024，每个统计重置，95% marginal percentile（索引 249/9749）。不是同时置信区间。

| 左 − 右 | 均差/秒 | 均差95%区间 | 左/右均值比 | 原始 p | Holm4 p |
|---|---:|---|---:|---:|---:|
| store_disk_cold − safetensors_disk_cold | -1030.199482 | [-1040.699901, -1019.201711] | 0.189755 | 0.0078125 | 0.03125 |
| store_pool_hit − safetensors_disk_cold | -1112.855072 | [-1122.777463, -1102.429569] | 0.124746 | 0.0078125 | 0.03125 |
| store_pool_hit − store_disk_cold | -82.655590 | [-83.898422, -81.274364] | 0.657409 | 0.0078125 | 0.03125 |
| store_disk_cold − safetensors_disk_hot | -200.340997 | [-207.304970, -193.293451] | 0.546337 | 0.0078125 | 0.03125 |

四项 Kimi 主比较在该族 alpha=.05 下均拒绝零差异；Prep+A 的四项是独立第二族，也均 Holm=.03125，不声称八项联合 FWER 控制。GLM 已核验主族前 3 项 Holm=.125、第四项=.5625；Prep+A 四项 Holm=.125。在 n=6 的四项精确检验中，最小可达首位 Holm p=.125，因此不显著不等于没有效果。Kimi 与 GLM 的显著性差异也不是模型间效果差异的显著检验。
Bootstrap RNG 等实现细节是在统计前补充冻结，不虚称全部实现细节均在采样前预注册。完整原数组、版本分层与区间见 [actual analysis](kimi-four-condition-actual-analysis-v1-20261107.json)。

## fixed32 与数值差异
Kimi 主集四条件已就绪 fixed32 整个 HTTP 请求的均值分别为：SF 冷盘 3.686639s, SF 热缓存 3.652433s, Store 冷盘 3.608347s, Store 池命中 3.638195s。这不是纯 decode/TPOT，也不是首业务启动时间。
GLM 执行产物未保存 fixed32 独立开始/结束，留为缺测，不从文件 mtime、整体 elapsed 或算术 A 反推。Kimi backend 首个非空输出事件也不等同客户端流式 TTFT 或 GPU 第一次 sampling。
已观察 fixed32 文本/实际 token 差异，按新协议不作为性能失败；原因仍是未定位的数值差异，不擅称浮点误差、权重完全一致或路径无关。额外 top2 历史诊断保留其独立身份，不回填正式 logprobs。

## 版本、存储与复现
- GPU 运行源：完整 block 1/2/3 为 86fd23bff8c7a78735776d7085f7fcb2b0a4efad/v8；5/6/7/8/10 为 835e2b5e992dd4098f013ddfdf5de4415db373f1/v9。v9 增加缓存拒绝与 traceback 取证；可能引入微小准备落盘成本，不能抹掉版本差别。
- 统计源码：a43734f11e84a06577b3de3833d0e9895e8ab323，已发布并核验 GitHub 分支 kimi-qjhs002-aligned-tail。该提交只新增 analyzer 与其测试，不是重新部署 GPU 服务。
- Store ELF SHA256：95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd；其 manifest 59d70f4710ca19d95515e5ca99c5f14ddabd963b2051720908c748e766c2ee2d。
- Kimi qjhs003 本地 /dev/nvme1n1 XFS：复用 64 shard SF checkpoint；Store 一次本地化 617029596501 字节且完整校验。源码和本 evidence 在 NFS /home；不能称全栈本地 NVMe。GLM 也使用该节点本地 NVMe，但模型、量化、资源与实现配置不同，未做跨模型控制变量实验。
- Kimi 镜像本地 ID sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27（RepoDigest 缺失）；vLLM 0.19.1、Torch2.10.0+cu129、TP8/eager、GPU memory utilization .8、worker32CPU/1536GiB/SHM8GiB、Store640GiB/64MiB chunk/4threads。没有为了对齐盲调容量。
- 新正式 Store 正常关停窗口固定 60 秒，之后至少 60 秒释放观察；没有使用强杀或后台释放冒充正常退出。早期 30 秒失败保留；已接受 L1 功能成果不为本图重复加载。早期 raw 诊断的 formal L1 标签未被本图重写。
- 预算每条件7200秒、每block28920秒、原40项最大289200秒（80小时20分）；不是10–13小时的最大预算。新增一次SF补跑单列，不再开启第11个block。以上是准入上限，不是图中的实测性能。
- 只做 CPU 分析与绘图；没有新模型运行、GLM 补跑、Store 转换、R24 或新40项实验。已知源/小文件 SHA 不等于新做全量 SF 权重值比对。 GLM 部署接收仅覆盖既有17文件 gate 加 Store overlay；未重新核验整个部署、各 rank 实际机器码、GitHub 远端 ref 或大监控原始数据，详见只读接收回执。

单次转换控制器完整 678.128942504 秒包含退出和释放，不是纯转换 I/O；Store 本地化加目标校验 2019.017771686 秒也不是纯复制时间。本轮没有 N>1 保留池的实测、稳态吞吐、实时迁移或有效带宽上限数据，因此不画伪实测摊销/吞吐曲线，也不从本图声称论文全系统收益。

复现 CPU 统计（原始路径在服务器，输出需用新文件名以保留本次产物）：
```bash
python3 -B /home/boyjiang/kimi-new/ServerlessLLM/benchmarks/analyze_kimi_four_condition.py \
  /home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-four-analysis-input-snapshot-v1-20261107/input-manifest-v3.json \
  /absolute/new-output.json \
  --manifest-sha 4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801
```

绘图脚本在本目录；现有 Windows Python3.13.1 / Matplotlib3.10.8 只读取约380KB输入生成图，再上传服务器逐SHA校验。未安装新依赖/环境。PNG已人工视觉核对，PDF/SVG提供矢量导出。

输入快照459小文件（1,648,410字节），v1/v2/v3均保留。v3 SHA：4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801；真实统计输出 SHA：0959cf17761ec076a07711bd010e8ab4e0f061bb3707b9cd252963fe29758df1。GLM原文件只读。

## 可直接使用的论文图注（英文）
**Figure 1.** Time from the registration call to completion of the first arithmetic HTTP response under four storage states. Bars show arithmetic means and dots show observations from complete qualified blocks (Kimi: eight; GLM C23: six). The right panel normalizes each model to its own Safetensors disk-cold mean. The visual organization follows ServerlessLLM Figure 6a, but our metric includes service startup through the arithmetic response and is not checkpoint-only loading.

**Figure 3.** Observed contiguous preparation-to-response intervals, decomposed into target-condition preparation, the preparation-to-registration gap, and registration-to-arithmetic-response time. Preparation semantics differ: GLM may include unloading the preceding pool, whereas Kimi records target-condition construction and admission. Common initialization and final cleanup are excluded; these are not full lifecycle costs.

所有图、表与验证回执由 manifest.json 列出 SHA；历史原始目录未改写。


## 新增：实际模型加载阶段跨度与已就绪请求

![独立阶段观测](fig5_observed_stage_envelopes.png)

三种启动跨度分别画柱，不堆叠，也不通过相减声称固定开销。A为实际HTTP生产端，backendinit为对应actor观测，模型加载为原f7 perf_counter日志的已定义范围。日志记录没有恢复rank/PID，不能称完整TP8最大跨度。右图fixed32是整个HTTP请求，不是纯decode/TPOT。

| 条件 | 主完整块n | f7模型加载日志均值/秒 | 描述合格n | 描述均值/秒 |
|---|---:|---:|---:|---:|
| safetensors_disk_cold | 8 | 1121.366857 | 9 | 1118.357281 |
| safetensors_disk_hot | 8 | 284.510617 | 9 | 283.887790 |
| store_disk_cold | 8 | 87.859091 | 10 | 89.164936 |
| store_pool_hit | 8 | 9.998397 | 10 | 9.942815 |

原始日志仅在本任务已有38个run内一次有界读取，共32,819,174字节；public摘要3b0ab92e…与private导出SHA一致，实际执行exit0。约1.2MB公开摘要保存全部2946文件引用/哈希、38数值标记与76白名单导入；未提交32.8MB原始日志、密码或环境转储。新字段是对既有样本的补证，不是新性能样本/新检验族。

76条实际导入均观察到Torch2.10.0+cu129/vLLM distribution0.19.1/Transformers5.5.1/grpc1.76.0/Ray2.48.0等，6个模块origin与/source及site-packages对应。不同于只检查role代码意图；未包含的FlashInfer实际import版本仍不冒称已采。

## 新增：完整运行参数与差异

# Actual Kimi / GLM runtime parameter alignment

| Parameter | Kimi | GLM C23 | Evidence scope |
|---|---|---|---|
| Torch |2.10.0+cu129 |2.10.0+cu130 |76 actual Kimi imports / GLM head CPU preflight |
| vLLM |0.19.1 |0.19.1.dev1+g43a9b1afb |Same as above |
| CUDA runtime |Not recorded in selected actual Kimi import markers |13.0 |Do not infer runtime from cu129 label |
| TP / eager |8 /true |8 /true |Explicit engine config |
| GPU memory utilization |0.8 |0.9 |Explicit engine config |
| Max model length / sequences / batched tokens |2048 /1 /2048 |2048 /1 /2048 |Explicit engine config |
| Prefix caching |false |false |Explicit engine config |
| Store pool / chunk / threads |640GiB /64MiB /4 |716GiB /512MiB /4 |RPC assertion/restore contract and runner source |
| Pool lifecycle |Fresh percondition |Shared formal-batch pool |Not equivalent initialization/reuse geometry |
| Head CPU / memory / shm |8 /16GiB /1GiB |4 /8g /32g |Configured caps, not utilization |
| Worker CPU / memory / shm |32 /1536GiB /8GiB |32 /1700g /32g |Configured caps, not utilization |
| Request temperature / seed / logprobs |0 /20261024 /1 |0 /20261024 /1 |Explicit HTTP fields |
| Request top_p / top_k / min_p / n |1 /0 /0 /1 |All omitted |No effective defaults guessed |
| Engine seed |Omitted |20261024 |Request seed explicit both |
| max_tokens / ignore_eos in actual request order |128 false;128 false;32 true |Same |Arithmetic,translation,fixed32 |
| Token-ID observability |Trace; return_token_ids omitted |return_token_ids=true |Unsupported flag not passed to Kimi SamplingParams |
| Thinking/template |Frozen own template thinking=false,preserve_thinking=false,add_generation_prompt=true |No explicit switch found in selected gold/runner/prereg; unknown |Both HTTP calls use frozen input_token IDs; no runtime template flags |
| Extra engine fields |moe_backend and GPU block override omitted |deep_gemm; num_gpu_blocks_override256 |No implicit default inferred |

Kimi transformers5.5.1,grpcio1.76.0,ray2.48.0,msgpack1.1.2,GPUtil1.4.0,speedtest-cli2.1.3 observed in76imports. GLM equivalents are not in selected actual import receipt and remainnull. Kimi CUDA runtime remainsnull; CUDA build suffix is not an explicit runtime measurement.

JSON attaches source path+SHA+selector to every parameter family,38 exact version-matched Kimi specs, actual GLM head import scope, original engine config and runner mutations. Configured maxima do not establish utilization. No package imports, default guessing, GPU, Git or GLM changes.

JSON SHA256: 0dee219fe6e7af7ede4d436fea68d24fb8f281e360d64462d6c9c5bf73f96462


## 新增：成本与复位模型

# Kimi existing cost evidence audit

Scope: 38 qualified original samples, not a total study-cost bill. SHA-linked public exports only; original private R0 permissions unchanged.

| Condition | n | Common env | Ray join | Pool init | Target Prep | Backend init | Delete ack | API stop | Store stop | Stop to release |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| safetensors_disk_cold | 9 | 33.164 | 7.924 | 229.450 | 49.819 | 1242.655 | 5.974 | 2.538 | 33.126 | 102.589 |
| safetensors_disk_hot | 9 | 32.585 | 7.805 | 229.203 | 163.223 | 417.050 | 5.732 | 2.417 | 32.768 | 102.112 |
| store_disk_cold | 10 | 32.725 | 7.888 | 229.501 | 26.382 | 215.964 | 5.810 | 2.449 | 32.710 | 102.004 |
| store_pool_hit | 10 | 33.772 | 7.718 | 229.624 | 91.470 | 133.016 | 5.603 | 2.555 | 32.721 | 102.016 |

Values are mean seconds. Operation boundaries are controller-observed and include communication/polling/verification overhead. Backend init overlaps A; API/Store stop overlap the stop-to-release envelope. Do not add nested intervals.

Counts: 38 fresh environments and Store pools; 9 warm-file preparations, 19 target evictions, 10 poolhit prefill admissions / 80 confirmed rank CPU loads, 10 Storecold demand attempts, 114 correct HTTP requests, 38 delete/baseline/normal Store shutdown and release sequences. This scope excludes two failed original attempts, one supplemental success and five historical launches from cost aggregation; their main denominators remain unchanged.

**C weight ingestion and pure pool-fill are missing.** Registered load metadata has no ingestion begin/end. capture_seconds measures metadata capture, not weights. Poolhit Prep includes registration/load/admission, not isolated fill. Initial/deleted baseline observation and independent release quiet each have their own >=60 records, and are not summed together.

## N>1 projection parameters

Forced-reset online average is mean(Prep+Gap+A), flat with N. For Kimi Store conditions an explicitly separate historical conversion-controller proxy of678.128942504 seconds can be charged once as proxy/N. It includes initialization/conversion/validation/normal exit/60-second release; it is not pure conversion. The original command provides no saved tighter pure-conversion monotonic pair. Historical conversion node/storage differs from online qjhs003. Later localization-copy cost is outside that proxy.

Each hypothetical cycle repeats its fixed cache/pool preparation: no continuing resident-service reuse is measured. Common environment/pool initialization and deletion/final release are outside the online window, so the curve is not full lifecycle cost. GLM conversion is unknown: its including-conversion curve stays missing, not zero. No break-even is claimed.

GLM c23-costs.json and its existing P/N+H amortization.json are received by exact SHA. The old once-paid-preparation curve has different assumptions and is not silently reused for forced-resetN. Its26-trial prep includes state-dependent previous-pool unload. Underlying large monitors were not re-read.

Raw per-run boundary timestamps, clocks, source manifests, durations, summary ranges, projection parameters and all limitations are in kimi-final-cost-evidence-audit-20261107-v1.json.

JSON SHA256: 07a44e897eec5d8b6aab2c37cfaeaacedd87201939b37d8ebc308a5e2cbaf247


![N次复位成本条件模型](fig4_reset_reuse_projection.png)

N>1曲线是条件模型，不是新测量。每次假设恢复都重复对应页缓存/池构造；前两图仅Prep+Gap+A，第三仅Kimi完整controller elapsed（直接观测，不把嵌套列相加）。Store虚线摊入一次历史转换controller678.128942504秒/N，其节点/介质不同，包含验证/退出/释放且不含之后本地化。GLM未知转换曲线留空。没有确定回本次数、持续驻留或稳态复用结论。38合格样本成本不是包括失败/历史/补跑的总账单。

## 新增：实际物理资源与日志内存字段

# Existing observed physical-resource table

All values below are existing per-device NVML sampled-peak lower bounds. Host/cgroup numeric peaks were not present in reviewed small exports and remain null. Each run retains8device peaks, sample counts, gaps and SHA-bound baseline references in JSON. No new GPU sampling or memory-time integration.

|Run|Condition|Per-device sampled peak range MiB|Samples|Query gaps|Initial Store baseline MiB/device|
|---|---|---:|---:|---:|---:|
|kimi-four-v1-b01-safetensors_disk_hot-retry04|safetensors_disk_hot|118057–118171|1347|0|1615–1615|
|kimi-four-v1-b01-store_disk_cold-retry04|store_disk_cold|118045–118135|989|0|1615–1615|
|kimi-four-v1-b01-safetensors_disk_cold-retry04|safetensors_disk_cold|118057–118171|2194|0|1615–1615|
|kimi-four-v1-b01-store_pool_hit-retry04|store_pool_hit|118047–118135|971|0|1615–1615|
|kimi-four-v1-b02-store_pool_hit-retry02|store_pool_hit|118045–118135|974|0|1615–1615|
|kimi-four-v1-b02-safetensors_disk_cold-retry02|safetensors_disk_cold|118057–118171|2213|0|1615–1615|
|kimi-four-v1-b02-store_disk_cold-retry02|store_disk_cold|118047–118131|993|0|1615–1615|
|kimi-four-v1-b02-safetensors_disk_hot-retry02|safetensors_disk_hot|118057–118171|1549|0|1615–1615|
|kimi-four-v1-b03-store_disk_cold|store_disk_cold|118045–118133|996|0|1615–1615|
|kimi-four-v1-b03-store_pool_hit|store_pool_hit|118047–118135|987|0|1615–1615|
|kimi-four-v1-b03-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1388|0|1615–1615|
|kimi-four-v1-b03-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2282|0|1615–1615|
|kimi-four-v1-b04-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1325|0|1615–1615|
|kimi-four-v1-b04-store_disk_cold|store_disk_cold|118047–118139|999|0|1615–1615|
|kimi-four-v1-b04-store_pool_hit|store_pool_hit|118045–118135|974|0|1615–1615|
|kimi-four-v1-b05-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2211|0|1615–1615|
|kimi-four-v1-b05-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1421|0|1615–1615|
|kimi-four-v1-b05-store_pool_hit|store_pool_hit|118047–118131|986|0|1615–1615|
|kimi-four-v1-b05-store_disk_cold|store_disk_cold|118047–118139|996|0|1615–1615|
|kimi-four-v1-b06-store_pool_hit|store_pool_hit|118047–118135|976|0|1615–1615|
|kimi-four-v1-b06-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2176|0|1615–1615|
|kimi-four-v1-b06-store_disk_cold|store_disk_cold|118047–118137|993|0|1615–1615|
|kimi-four-v1-b06-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1496|0|1615–1615|
|kimi-four-v1-b07-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2243|0|1615–1615|
|kimi-four-v1-b07-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1309|0|1615–1615|
|kimi-four-v1-b07-store_pool_hit|store_pool_hit|118047–118131|978|0|1615–1615|
|kimi-four-v1-b07-store_disk_cold|store_disk_cold|118047–118137|993|0|1615–1615|
|kimi-four-v1-b08-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2212|0|1615–1615|
|kimi-four-v1-b08-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1320|0|1615–1615|
|kimi-four-v1-b08-store_pool_hit|store_pool_hit|118047–118135|979|0|1615–1615|
|kimi-four-v1-b08-store_disk_cold|store_disk_cold|118045–118135|999|0|1615–1615|
|kimi-four-v1-b09-store_pool_hit|store_pool_hit|118047–118137|976|0|1615–1615|
|kimi-four-v1-b09-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2222|0|1615–1615|
|kimi-four-v1-b09-store_disk_cold|store_disk_cold|118045–118133|988|0|1615–1615|
|kimi-four-v1-b10-store_disk_cold|store_disk_cold|118045–118135|993|0|1615–1615|
|kimi-four-v1-b10-store_pool_hit|store_pool_hit|118047–118135|976|0|1615–1615|
|kimi-four-v1-b10-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1326|0|1615–1615|
|kimi-four-v1-b10-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2213|0|1615–1615|

Per-device peaks need not be simultaneous. Their sum is deliberately not reported as an observed aggregate peak. GPU baselines are devicewide samples with authenticated StorePID, not a host pinned-memory count. Logger0.19GiB is an allocator delta and is not used as resource usage. PartialB04/B09 cases do not become paired primary blocks.

JSON `/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-final-observed-resource-cost-table-20261107.json` SHA256 `132e7344c863033630df84166c1f4cb3d7cb6170a0e4c6f2746ad13c985a69bd`.


# Model-loading memory log scope

The active f7 runner assigns `model_memory_usage = m.consumed_memory` after its DeviceMemoryProfiler context, then formats it in GiB at line4965. It is not a total device-memory measurement.

DeviceMemoryProfiler subtracts two endpoint calls. Its exact-commit upstream mem_utils bytes SHA080206… match the historical installed-image SHA. The CUDA platform function empties cache, resets peak statistics and immediately queries max_memory_allocated at EACH endpoint. Thus the printed value is an endpoint change in Torch allocator-tracked allocated memory; the reset discards the preceding peak. CUDA platform source here is exact upstream b1388b1fb, not a newly recovered installed-file hash.

Store uses direct CUDA allocation and wraps existing pointers with from_blob. Such external allocation is outside the Torch caching allocator accounting. Reported SF72.05GiB versus Store0.19GiB therefore cannot be interpreted as physical total memory, weight size, or NVML/model-load peak; Store0.19 does not mean the restored weights are almost memory-free. Existing physical NVML and authenticated rank ownership remain the relevant independent residency/peak chain.

The display rounds bytes/2**30 to two decimals. Keep this log only as allocator endpoint delta, separately from the model-loading duration and actual physical resource observation.

Source: [vLLM profiler](https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/utils/mem_utils.py), [CUDA platform](https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/platforms/cuda.py), [PyTorch allocator limitations](https://docs.pytorch.org/docs/2.10/torch_cuda_memory.html). No runtime/import/GPU or new memory measurement was performed.


**不能把Store日志0.19GiB解释为总权重显存或峰值。** 它是Torch allocator两端差值，直接CUDA/IPC外部分配不由其完整统计。显存结论取SHA绑定的逐卡NVML采样下界和rank物理所有权；不把8卡非同时峰相加，不把configured640GiB当实际主机峰，不把无数据记0。

## 新增：资产、版本补丁、入口及L1审计

详细证据见本目录：asset/config table、runtime-parameter alignment、version-patch identity v1/v2、20路径unified patch、formal-entry consumer audit、L1 evidence audit、owner acceptance及nine-deliverables literal audit。所有更早S1–S4/转换/恢复/原金标与失败证据通过其路径和SHA继续保留，没有为改变状态标签再跑模型。

本报告直接纳入了owner接受与最新日志证据，因此较早报告/审计中的“owner待确认”“privatehost日志未读”仅描述当时状态，不是当前阻塞。原始诊断标签和以前的失败仍是不可改写的历史事实。

本协议只包含已列出的文本请求与2048上下文设置；不主张图像/视频、长上下文、并发吞吐或持续驻留服务的正确性及性能。原生多模态架构/资产存在不等于这些工作负载已验证。

## 复算与数据位置

主统计可从已经发布的459份原始小元数据快照与v3 input-manifest复算；本目录保留统计结果、GLM只读输入、所有attempt表、加载跨度CSV、审计和各图来源SHA。完整快照继续位于此前final-kimi-glm-comparison-20261107/analysis-input，避免再次复制大资产。
图1/ECDF/准备图/全40图：plot_kimi_glm_comparison_20261107.py与plot_kimi_glm_all40_20261107.py；N模型：plot_kimi_glm_reset_cost_projection_20261107.py；阶段图：plot_kimi_observed_loading_spans_20261107.py。各脚本使用其冻结JSON输入及新输出目录，验证样本几何/公式/原始值并拒绝覆盖；无需再次root收集或运行模型。
所有新源码/审计/日志摘要先保留服务器，仅通过内存tar传输小型报告及Git证据；本轮没有本地GB级临时包，没有删除原Store、权重、镜像、唯一源码、历史证据或他人文件。

最终完成判定在本报告独立核验、manifest完整性与远端Git SHA核验后单独记录。这里的明确缺测和未支持范围遵循V2：不可分、缺测和失败均可作为正式结果，不虚构实测，也不为理想观测追加未经批准的GPU运行。

## 最终补充：逐事件互斥分解与离线复算

![五段互斥分解](fig6_exclusive_observed_stages.png)

本图将条件准备、准备至注册间隔、注册至路由、路由至后端首次非空输出、首次输出至算术HTTP完成分开。32个Kimi主样本逐条核同boot/timens/clockimpl、各producer PID/startticks/owner绑定、model/instance/requestID及事件顺序；尾三段严格和为A，五段和为连续窗口。GLM采用已核24个C23主样本，不混入描述集额外2条。共同环境/池初始化及最终清理在图外；prep语义差异保留。后端首次输出不是客户端流式TTFT，最后一段不是纯生成。小于主尺度的段见exclusive-small-intervals.csv，未缩放伪造其占比。

# Small mutually exclusive intervals

Mean seconds on primary complete blocks. These are observed operation spans, not pure IO/CUDA/generation.

| Model | Condition | n | Prep->register gap | Route->backend first output | Backend first->correct HTTP |
|---|---|---:|---:|---:|---:|
| Kimi-K2.6 | safetensors_disk_cold | 8 | 0.094468347 | 1.878032304 | 0.592469763 |
| Kimi-K2.6 | safetensors_disk_hot | 8 | 0.097631726 | 1.794219794 | 0.590644937 |
| Kimi-K2.6 | store_disk_cold | 8 | 0.098389168 | 1.849554454 | 0.581399260 |
| Kimi-K2.6 | store_pool_hit | 8 | 0.097849039 | 1.836714341 | 0.576331026 |
| GLM-5.1-FP8 | safetensors_disk_cold | 6 | 0.000239372 | 1.182353178 | 0.222864785 |
| GLM-5.1-FP8 | safetensors_disk_hot | 6 | 0.000249609 | 0.982716272 | 0.229067980 |
| GLM-5.1-FP8 | store_disk_cold | 6 | 0.000240298 | 0.990029940 | 0.239580781 |
| GLM-5.1-FP8 | store_pool_hit | 6 | 0.000229877 | 0.919944545 | 0.224149776 |

Data SHA256: 0593266d8e924e5fc21b98e32e48fe97eb7d2f16e9d6e4a31db1741bb6169a04
Kimi owner/startticks/CID/clock/request joins and all raw intervals are in the JSON. GLM arrays are direct references to prior verified plot input. No absolute clocks invented for GLM beyond that input.


已另在现有Windows Python3.13.1从Git交付元数据重定位467个SHA引用，实际运行原统计CLI，重得与Linux3.10原执行相同的cohorts/blocks/两族均差、比值、p、CI及描述统计（已声明浮点容差）。只是路径与provenance变化，不是重采样/新模型。详见[REPRODUCTION.md](REPRODUCTION.md)及delivery-portability-recompute-verification.json。
