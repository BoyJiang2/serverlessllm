本次canonical2是已实际完整R1批准5b01d7d的受影响修订，请优先实际审阅本次差异，不重复已完成无关整理。上轮reviews/full-r1-canonical1/verdict.json为完整approved，但真实G1第一Ray head因Unix socket路径超过107字节失败，未启动Store/model，已完整清理/61样本75.82s释放。旧失败run证据和实际执行源码均保留，正式5成功4失败31未开始不变。
本修订只将同一个host run目录的容器内唯一可写挂载改为短别名/r，所有host源仍在canonical新根，readonly输入挂载/共享verify_input_layout与host-only guard保持。workspace.run_root在容器返回/r、host仍原新根下validation/controls/run。核对全部head/worker临时目录socket最大长度回归、缓存/事件/审计输出路径。没有第二个可写host资产、没有恢复旧长路径。新run_id，不重写旧控制失败或正式ID。
另绑定已实际确认的连续boyjiang10–18与18–21两段booking，新独立guard20:50；旧run17:50guard已清理inactive。不得用旧R1批准这些新字节。请返回gate=R1,scope=full-runtime,verdict=approved或changes_required，绑定本次实际commit与manifest。G1/R2尚未通过。下方保留完整审阅范围供引用。

请作为 DeepSeek Harness 只读 AI 审阅者完成当前单一 runtime/active 的**完整 R1**。仅 read/search，不运行 shell/SSH/资源命令，不改文件，不读取凭据，不派代理。调用会附实际 clean code commit 和 control-input-manifest SHA。不能用路径专项批准代替完整 R1，也不能把尚未运行的 G1/R2 说成通过。

先读当前 STATUS.md 尾部、protocol/CANONICAL-WORKSPACE.md、runtime/active/README.md、run-manifest.json、control-input-manifest.json。实际读取完整运行源码、配置、测试和必要外部引用；不要只审摘要。核对全部入口/传递依赖、预算、FP8/scale/混精引用、Store初始化、Ray创建、原生HTTP业务/删除/再拉起、缓存、监控和释放的组合正确性。模板的派生约束、镜像/源码/二进制与实际执行的绑定均在范围内。冻结1142源码/二进制清单与已存在的旧源代码证据可按需读取，不重新复制或下载模型。

已有路径专项实际结论：reviews/path-boundary-20261026/NEXT-STEPS.md、verdict-v2.json、receipt-v2.json。其源为bd25def/manifest951e80...，只批准路径/输入边界，当前实现完整R1仍待审。保留其共享 workspace.verify_input_layout、host-only historical_path/accept、8rank80part布局与哈希/软链拒绝、bootstrap输出创建前验收、严格 require_full_r1(gate=R1,scope=full-runtime)。PB02“bootstrap缺env”是已被不可变原始参数证伪并撤回的finding，不要机械恢复。旧长路径已经不存在，INDEX旧字段仅用于映射；不得要求恢复入口。现只用新canonical root及显式logical /experiment挂载。

上轮完整旧源码R1e在 reviews/verdicts/deepseek-r1-control13e-*，changes_required。当前受影响修复：
- require_helper_only 不再有 allow_engine；删除后61行每行都仅允许当前Store helper。host PID/NSpid/startticks/pidns/container/cgroup/UID/boot必须绑定；同run的其他engine也不能混入。秒0/59/60均有负向测试。保留连续原始监控。
- 新主机ready携带同一条原始NVML GPU五列/process字符串及sample_begin，nativeidle直接引用；accept解析PID、比较字符串，并独立关联原始主机监控记录。删除ack后最多5秒等待“采样开始晚于ack”，不是等待资源变成期望值；随后完整61行至少60秒，下一生命周期前也验收，不能掩盖监控空洞或失败。
- 每层swap值校验，自有slice必须0且SwapTotal0；根 cgroup 的 memory.max/high/swap.max 是官方non-root-only接口，允许仅根缺失，不能据此要求改全局或要求不存在的文件。参考官方 https://docs.kernel.org/admin-guide/cgroup-v2.html 及既有root反证，非根缺失仍拒绝。严格监控begin/end与gap、PSS独立45秒、root持久证据/最后timer冻结等均保留。
- 在现有Store启动入口建池前校验四个**实际已导入**模块路径/哈希，必须是canonical Store-source-v7，不能落到ServerlessLLM树内另一个不同扩展。记录当前Store进程的torch/vllm/CUDA版本。此回执不冒充每个Ray worker的导入证明；真实8rank权重/scale和业务审计仍要求G1。

审FP8时区分：engine torch_dtype=bfloat16用于官方混精非量化部分，并不等于把官方FP8权重反量化保存为BF16。模型/revision/FP8原值/scale/量化配置不变。weights审计引用仍为每rank5814weight/scale/derived references，排除234DSA scratch与固定KV运行态；派生W_UK/W_UV等后处理按既有weight_contract检查。G1两格式×两周期×8rank共32rank记录，每周期自然EOS业务与固定32token金标分开。验证不进入性能计时；Store组件、vLLM参考和完整原生服务结果不能混淆。

必须审完整时间/资源组合：Ray2×330秒，service总3420秒含watchdog25秒TERM/KILL reserve与bootstrap3300秒alarm；cache子任务600秒/8reader/64GiB NUMA余量/最多2repair/完整148输入初始0末次100% mincore及read-idle。pgsteal只解释。准入剩余时间>=整轮最大+180秒，保留每次删除60秒与独立最终释放。公共716GiB池同批保留，单机8H20-3e，TP8/utilization0.9/context2048/seq1/MTPoff/eager/256KVblocks不改。容器资源按本受审模板，不套旧2CPU/2GB到服务。init/cache/编译/失败/控制/闲置成本保留；选取的kernel cache初始化有逐文件hash与上限，不复制全环境/模型/Store。不得凭GPU空闲或旧金标跳过实际守护/监控。

当前元数据窗口为qjhs003连续boyjiang10–18与18–21两段预约；本次全新run的计划guard20:50，尚未安装。当前窗口和资源在执行前重新核验；旧失败run已经清理，不能复用其运行凭证。

请逐项核验发现的事实，给实际文件/函数/证据/影响/最小修正或验证。区分确定bug、推测和运行前置条件，避免以未执行G1/R2本身否决静态源码；但不能忽略真正的组合/预算/释放缺口。若需要额外信号探针，论证有界收益，不能建议全局Docker/BPF设置。完成最终结论，JSON至少包括 gate="R1", scope="full-runtime", verdict="approved"|"changes_required"|"blocked", code_commit, manifest_sha256, findings, runtime_conditions, limitations。不返回approved_for_path_boundary作为完整结论。

操作顺序不变：每个head启动后由操作者核验其仍Running且对应私有GCS TCP端口ready，再派发worker；只按已记录CID停止自有head。两个Ray控制分别成功并完成60s释放后才派发真实服务批次。任何失败立即停止新分配，清理本run并最终60s观察/root索引，不覆写失败或重试正式ID。
