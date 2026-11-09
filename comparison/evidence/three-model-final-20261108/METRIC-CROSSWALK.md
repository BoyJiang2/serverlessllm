# 三模型指标口径与证据索引

2026-11-09，CPU复核。统一的是定义、资格规则和展示方式；没有把缺端点或功能不一致的历史值改名为统一值。原40项、主集与失败完全保留。四条件顺序均为SF冷、SF热、Store冷、Store池。下文简写glm/、kimi-source/、kimi-formal64/、kimi-earlier/、v4/均相对inputs/harmonized/。

## A：激活／注册到首个正确业务完整响应

| 模型/有效样本 | 实际起点→终点 | 纳入范围与实现差异 | 原始来源/本地索引 |
|---|---|---|---|
| Kimi主展示各n=8；历史有效9/9/10/10 | HTTP register前的registration command主机时钟→算术HTTP读取/解析、context退出后received_ns；金标断言在后 | 自己的tokenizer/template生成并冻结输入IDs，thinking=false、preserve_thinking=false；17+25，含路由/引擎启动、请求及完整响应传输。算术/翻译严格，fixed32按原length_only规则另列 | inputs/legacy/kimi-four-condition-actual-analysis-v1-20261107.json、kimi-glm-mutually-exclusive-stage-input-20261107.json；原件根/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/，逐run/source SHA见同目录实际分析与新controller-attempts.json |
| GLM C23主展示各n=6；描述6/6/7/7 | formal_trials.py中startup_command_ns（调用http('/register')前）→http业务返回后立即捕获的correct_business_response_ns | 自己冻结的input_tokens；17+25，非运行时重新套chat template；所选金标未显式固定thinking/template开关，不能推默认等同Kimi。三请求严格token/text/finish和有限logprob | inputs/harmonized/v4/report-boundaries-20261109.json的glm[ID].result和code中的C23 formal_trials.py；原件/data/brick1/boyjiang/glm5.1-fp8/validation/controls/sllm-glm51fp8-20261027T011726Z-28c92313/formal-trials/ID/result.json |
| V4展示各n=9；正式10/9/10/9 | startup_command_ns→business received_ns，响应原文先保存再校验 | 自己的revision/tokenizer、messages路径及原生gold；2+2自然EOS，输入30token；fixed32输入43token。包含注册、调度、引擎、业务生成/完整响应；不是纯加载 | inputs/v4/results/raw-replay/ID/result.json、qualified-rows.json；原件/home/boyjiang/sllm-v4-pro/evidence/ID/output/trial-startup.json、trial-business.raw.json、trial-http-complete.json |

A具有相近用户可见语义，存在请求/编码/实现差异，只作有配置说明的描述性比较。后端first-token事件不是客户端流式TTFT；主机received也不是GPU末token时刻。

## 完整权重加载：共同定义与历史资格

共同定义：在每次尝试中，匹配八个预期rank，进入完整`model_loader.load_model`前（fresh模型构造、storage分配之前）到正常返回后（必要恢复/后处理完成、可选runner工作之前）。主指标为`(max(end_ns)-min(begin_ns))/1e9`；逐rank为`(end_ns-begin_ns)/1e9`，另报其最大值。两者不混用，也不从总A倒推。

| 模型 | 实际历史观测边界 | 共同主指标有效n | 原因与证据 |
|---|---|---|---|
| Kimi历史 | f7局部perf_counter在get_model_loader前→load_model及可选LoRA/drafter/aux后；单条日志缺rank/PID与绝对端点 | 四条件均0，mean/median=null | 不是八rank共同包络；inputs/kimi-unified/condition-summaries.json、inputs/harmonized/kimi-source/source-boundary.json及history-endpoint-audit.json |
| Kimi独立新补采 | f919 overlay在完整loader调用前后取CLOCK_MONOTONIC；实际Base/Default和Store loader包含fresh构造/storage与必要后处理 | 两个独立小批完整块均0；各自SF热单条件合格n=1，Store冷为失败观测n=1，另两条件未开始 | 逐rankPID/startticks、设备owner、实例、boot/time namespace见kimi-formal64/*-endpoints.json及kimi-earlier/results-v1.json；不回填历史n=8，不合并q3NVMe和q0RAID |
| GLM历史 | traced(GPUWorker.load_model)：进入weights pool/config context之前→model_runner.load_model正常返回之后 | 严格共同四条件均0；原Worker阶段时间端点各n=6有效 | 包含更宽runner工作；实际GPUModelRunner映射/V1或V2分支、构造分配及可选路径排除未闭合。192条rank缺直接startticks/boot，actor身份不能移植给worker。见glm/loading-boundary-note.md、sources.json、endpoints.csv；保留C判定 |
| V4历史 | v4_trace.py包装Worker.load_model；上游gpu_worker.py 318–323→GPUModelRunner.load_model，后者含完整loader及额外runner收尾 | 严格共同四条件均0；Worker阶段展示各n=9，正式有效38项有端点 | 有八rank过程身份，但没有直接包住完整loader调用的历史事件；不能从外包络扣除未布点的额外工作。见report-boundaries JSON的code与events；Worker包络不得仅改名成为共同loader包络 |

GLM官方参考V1代码不能代替实际映射文件证明。V4核查的是固定132765e上游源码与冻结88cc71e hooks，不以当前分支的新代码代替运行源。V4 Runner 4792–4795为loader工厂/调用；随后可选LoRA/drafter/aux、通信buffer/包装及offloader工作仍处在Worker函数范围。没有从“配置通常关闭”推导两个函数端点相等。

## 摄入和后处理：模型内阶段观测

| 模型 | 实际接点/功能 | 样本与可比较性 |
|---|---|---|
| Kimi | 历史无可匹配的摄入/后处理分项端点；新补采只围完整loader调用 | 分项n=0，null；f7或完整调用不能拆分代替 |
| GLM | load_weights/原Store恢复方法阶段；process_weights_after_loading阶段；外层Worker阶段另列 | 原主展示各n=6；功能不同于V4分组恢复，分模型画图，不横向同色并列归因 |
| V4 SF | DefaultModelLoader.load_weights与base_loader.process_weights_after_loading包装 | 保留原定义，不改变历史同步行为 |
| V4 grouped Store | V4GroupedStoreLoader.load_weights→session.begin：安装hooks并恢复remainder；后续61专家组在process_weights_after_loading遍历包装器中逐组恢复、H2D并处理 | weight_ingestion不覆盖全部专家权重传输；quantization_postprocess包含组恢复、H2D及处理，不是纯量化计算。展示各n=9，正式各10/9/10/9另存；任何包络都不能相加或均值相减成为纯成本 |

全部为主机函数包络：异步设备工作与主机调用可以交错；end证明host正常返回，不保证未同步CUDA任务全部完成。本轮没有加CUDA同步改历史定义。1552条逐rank/缺测槽位见derived/rank_durations.csv；每attempt包络与max见phase_attempts.csv，各条件均值见phase_summary.csv。

## Prep：不等价成本不堆叠

| 模型 | 原Prep起止/范围 | 已有端点可拆什么 | 保持null什么 |
|---|---|---|---|
| Kimi | 原target condition construction→cache/pool admission complete；公共环境、Ray、池初始化及初始baseline在其外 | controller-attempts.json另给common_environment begin/end、store_pool_initialization begin/end；这些是独立外部区间，不加回原Prep | 权重注册纯时长、目标构造与准入各自纯时长；原Prep只作为合计 |
| GLM | preparation_begin_ns→preparation_complete_ns；目标cache/pool准备、必要旧pool卸载和文件级准入；公共栈先已启动且跨26次共享 | 原Prep合计，来源formal_trials.py与各result；共享成本不按条件均摊 | 每条件环境、建池、纯注册、独立cache/admission分量 |
| V4 | 宿主preparation origin→startup_command_ns；含环境/服务、池、注册验证、cache构造/准入 | 可精确拆原始里程碑：origin→store-configuration-checked；→trial-cache-complete；→startup。三段逐attempt相加等于原Prep | 里程碑段不等于纯建池、注册、IO或准入；这些纯分量仍null |

derived/preparation_components.csv逐ID记录可拆值与null；v4_preparation_milestones.csv是有范围名称的里程碑，不是估算填项。互相嵌套或重叠的分量不加总。

## Controller：起止、退出与释放

| 模型 | 实际原边界/纳入 | 排除与缺口 | 有效n |
|---|---|---|---|
| Kimi历史 | systemd outercontroller ActiveEnterTimestampMonotonic→成功结果字典中elapsed求值；含环境/池、初始baseline60、三请求验证、delete baseline60、正常Store/API/Ray/容器退出、guard/observer quiet≥60、checkfiles/network删除及资格检查 | 不是Python入口→进程退出。elapsed求值后的unitprops、结果序列化/原子写、finally辅助client/journal及解释器退出不含；选定收据绝对起止未记录，不能从mtime倒推 | 主展示每条件8，原elapsed浮点保留 |
| GLM历史 | helper preparation_begin_ns→complete_ns：含目标准备/三请求/delete/每实例idle≥60 | 不是部署Controller。共享父栈跨26次才退出；head controlled_stop且child rc1保留，不能称父子正常exit0。shared release75.982秒不按trial分摊 | 每条件Controller 0/null；helper主展示各6，描述6/6/7/7 |
| V4历史 | 宿主preparation origin→正常退出、release_roles和journal release后、candidate.json字典中total_elapsed_seconds求值 | 不含随后candidate写入、controller解释器退出和独立review；包含最终head/worker各61点81.67–84.38秒释放与删除后观察 | 主展示各9，正式10/9/10/9 |

Kimi精确边界及32项正常退出核验见kimi-source/controller-boundary.json；GLM当前历史恢复结论见glm/controller-r1-1ced918f-controller-history-audit.json（controller_recoverable_by_condition=false）。不同边界只列分模型表，禁止横向总成本排名或强行拼接新批Controller与旧加载。

## 独立随机切换与缓存粒度

共享Store随机轮为另一个cohort：qjhs003、1TiB池、32MiB chunk，直接VllmBackend激活marker→宿主解析正确业务响应；不是上述HTTP注册A。六方向各2次、12次切换；固定5步另列。原始server证据根/home/boyjiang/sllm-v4-pro/cache-competition-20261109/cache-sequence-directed-v1，索引及SHA在inputs/harmonized/random/evidence-manifest.json。

缓存路径为Store管理/淘汰单位；tensor.data_*是路径内部数据文件；chunk是固定池分配单位。Kimi/GLM为8条rank路径，V4为8×(61专家组+1remainder)=496路径，不是496次实验或496个文件。新随机表优先报命中payload字节、重读字节、分配/归还块；路径数仅核查。历史三模型chunk为64/512/32MiB，与随机统一32MiB不可混为相同块容量。分组粒度可能影响重读/调用/淘汰，但没有控制变量实验量化独立贡献，不把跨模型延迟主要归因于路径数量。
