# 源转换批次：完整预算与窗口预检

`source_schedule.py`默认入口只做配置/时间检查；新增guard子命令为独立宿主watchdog候选，可在完整绑定后调用Docker清理指定本实验容器。当前仅本地CPU/替身测试，未远端部署、启动模型、安装守护或改预约。输入是外部SHA绑定的≤64KiB计划、已有source spec与源码清单。计划的run_id/holder/code_commit/spec SHA/源码manifest SHA必须与verify_source_run结果一致；固定资源h20/qjhs003。booking_id只是声明，返回booking_verified=false，不能据配置文件自授授权。

固定预算序列是preflight、image_restore、engine_initialize_and_export、fixed_token_business、natural_eos_business、worker_shutdown、release_observe、cleanup_evidence、safety_margin，全部正整数且有限。engine阶段包含TP/通信组初始化、SF加载、导出、后处理/KV/必要预热；preflight包含代码/输入与实际资源/守护检查的预算，镜像恢复及可能的编译/校验也必须明确归入相应上限，不从总成本里消失。这里只定义字段，未冻结生产秒数，测试内数值不作模型耗时估计。

观察的最低持续时间固定60秒；release_observe预算上限可更长，例如90秒用于采样/收尾开销，不能把60秒观察压成更短时间。所有阶段最大预算相加后才能判断是否能开启本批。3600秒外层空间不能容纳3721秒总预算；每一秒边界纳入校验。

## 截止关系

- 预约起止、窗口清理终点及当前时间必须带时区，统一到UTC计算；未到窗口开始即拒绝，不因GPU空闲提前开始。
- 窗口清理终点最多是预约结束前10分钟。now+全批最大预算超过该点即拒绝，不能用固定“几点停止开新任务”替代。
- 本批清理终点是now+全批最大预算，而不是整段预约的末尾。因此长预约不延长一个小批次的寿命。
- 独立守护最迟触发点是本批清理终点减去完整worker_shutdown+release_observe+cleanup_evidence+safety_margin尾部预算；不等到预约结束才停模型。
- 各阶段绝对deadline由同一批次开始时间累计推导；没有运行时延长旧plan/guard的接口。实际执行仍须用单调时钟、独立守护和精确身份执行这些期限，当前模块没有完成这些执行器。

CLI先做真实code/spec检查再读plan，但开始时间在检查之前采集；核验耗时用monotonic计入preflight，不在读完配置后重新起算。核验耗尽preflight、墙钟倒退或已越过preflight deadline都会拒绝。CLI没有--now等改钟入口。输出timing_fits只说明时间配置可容纳，runtime_admitted和guard_verified始终false。

CLI形态：

```text
python source_schedule.py --code <run/code> --manifest <run/source-code-manifest.json> --manifest-sha <SHA> --spec <source-spec.json> --spec-sha <SHA> --plan <source-plan.json> --plan-sha <SHA>
```

## 验证及剩余工作

2026-10-31追加主动就绪探针：guard实际CLI在自己的armed循环创建Linux abstract AF_UNIX监听器，地址由本run随机nonce派生，不新建目录或socket文件。`probe`子命令使用与guard相同的registration/plan/code/spec验证后，先要求事件仅有inputs_verified+ready两条完整记录，再做新随机challenge往返；旧日志、已进入cleanup或没有主动端点均拒绝。请求≤4KiB、响应≤8KiB，connect/收发/实际PID核验共用最多2秒的单一绝对deadline，并检查未到guard fire。服务端单客户端≤0.25秒，只在armed且尚未到期时响应；坏/慢/错UID/错nonce客户端不取消或延长守护。正常与异常收尾都先尝试关闭listener，再执行owned cleanup，关闭错误不跳过清理或覆盖原错误。

协议依据为[Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)：abstract socket的权限不由文件chmod保护，接口因此保留SO_PEERCRED的UID/PID核验；其凭据来自connect/listen/socketpair时的peer。client还必须把内核peer PID、ready PID/启动tick、fresh /proc双读启动tick、boot、PID namespace、cgroup，以及nonce/challenge/完整target身份/截止全部交叉核验。探针只返回probe_exchange_verified，independent_service_verified、guard_deployed、runtime_admitted和resources_released仍false：它不证明独立systemd/服务单元、预约、整机资源或完整服务准入。

CLI形态为`python -B <run>/code/source_schedule.py probe --registration <run>/source-guard-registration.json --registration-sha <SHA>`，不覆盖或创建guard事件日志，也不启动模型。当前Windows测试使用真实socketpair及有界线程验证流协议，但Linux peer凭据、/proc和abstract endpoint仍为替身/未实测；不能据CPU结果宣称qjhs003 guard/probe实际通过。后续须接独立宿主服务启动/单元归属证明、完整preflight准入、监控及60秒释放验证。

2026-10-31追加清理内核候选（尚无watchdog/部署CLI）：guard_target绑定完整64hex container ID、精确image ID/Created、run/holder/code/spec/manifest标签、UID2147、boot身份参数及禁止自动重启。cleanup_guard_target先验证，再inspect/proc核验，按单个累计剩余预算执行stop→再次inspect已停→rm（无force/volume/image）→本ID过滤ps核验absent；不能按名称或PID发信号，不处理其他目标。guard_process双读/proc/stat field22起始tick，核UID与含完整Docker ID的cgroup，拒绝PID复用/错误归属。boot/UID必须由未来独立守护实际采集；本机测试传入的模拟值不构成节点证明。

Docker适配器固定/usr/bin/docker与本地unix:///var/run/docker.sock，在子进程环境副本中移除DOCKER_HOST/CONTEXT/TLS相关覆盖，不改全局配置。POSIX selector按4KiB读取，每个stdout/stderr保留≤64KiB，超限只终止自己持有的Docker CLI子进程；不把CLI超时/结束当daemon取消。该输出缓冲上限不是Python、Docker进程或模型的RSS峰值。selector初始化失败也必须清理CLI及双pipe，关闭错误不覆盖原错误；这些故障已用CPU替身测试，未在Linux/Docker实际执行。返回container_absence_verified只代表该ID在一次可达daemon查询中不存在，resources_released/release_observation_verified/guard_deployed/runtime_admitted均false。

watchdog候选已接内核，独立guard CLI形态为`python -B <run>/code/source_schedule.py guard --registration <run>/source-guard-registration.json --registration-sha <SHA>`。它不启动模型。必须从该run的已核源码bundle执行、禁止优化和写pycache，核外部registration/plan SHA、完整code/spec及同run身份，随后才查询宿主和本地Docker。registration为精确schema kimi-source-watchdog-registration-v1，字段target（已有guard-target-v1）、plan_sha256、batch_entered_utc、host_pid_namespace、nonce；原plan固定在既有run/source-plan.json，batch_entered_utc不能晚于实际guard开始。UID2147、boot、实际/proc/self PID namespace/start ticks与外部登记相符，且guard cgroup不能在目标容器里；实际部署还须证明该源进程位于独立宿主服务，不依赖SSH或模型生命周期。

guard在既有run目录独占创建source-watchdog-events.jsonl，单事件≤8KiB且无缓冲flush/fsync；记录registration/plan SHA、nonce、宿主PID/启动tick/namespace、目标完整ID/image/Created、ready或late_start以及清理结果/失败说明。fire始终是原批次累计截止，墙钟/单调时钟取更早；shutdown预算及最终完成判据也同时核两种时钟。迟到即使仍在shutdown尾部也整体失败，不发布ready。目标身份已验证后的ready/日志/等待异常仍尝试一次精确清理，未验证/foreign目标不动；清理已尝试不无限重试，原异常和清理错误一并留证。截止已耗尽时只额外尝试一次最多5秒的本实验紧急清理，记录emergency_cleanup_budget_seconds和失败，不能授予继续计算或追认准时完成。

真实本地CPU两层进程控制验证：启动watchdog的launcher退出后，watchdog子进程仍按自己的2秒截止触发FakeDocker清理；子进程另有8秒测试自限并关闭证据文件后才清理临时目录。这仅证明CPU控制独立性，host/namespace/Docker仍为替身，不证明Linux宿主守护已安装或获得GPU准入。下一项实际安装/启动方案、就绪proof验收、连续监控、60秒释放观察和完整启动前R1仍待完成；单个ready日志不能替代这些条件。未安装或运行任何远端守护。

CPU追加修订：phase_remaining_seconds复用批次最初的entered与累计monotonic elapsed，返回墙钟和单调时钟中较小的剩余秒数；恰好到期也拒绝启动下一操作。CLI核验使用该检查；NaN/Inf/bool、负耗时、无时区及墙钟倒退拒绝。后续worker必须沿用同一批次锚点，不能每阶段重新采集entered；此函数不强制终止子进程，也不是独立守护。4项新增测试覆盖时钟分歧、等值/超时、跨阶段累计及非法输入，全套250项CPU测试通过（28.437秒）。直接修改已有模块、测试和记录，未新增目录或模型副本。

新增11项CPU测试，总计246项通过；包含全预算精确边界/差1秒、3721对3600、过早/过期/时区/身份/缺阶段/bool/观察不足拒绝、观察预留开销、守护尾部、长预约不延长小批次、外部哈希/读取上限，以及CLI核验耗时不能重置。两个fresh CPU CLI用小Git/metadata夹具实际连接code→spec→plan；时间不足/窗口未到拒绝，时间可容纳仍返回runtime_admitted=false。夹具不是实际节点授权或模型，未运行vLLM/八卡。测试导入重名的初次失败保留source-schedule-tests.json。

下一项是把此预检接进实际source worker/宿主控制器，核验真实UID/boot/容器/cgroup/授权、安装独立guard并probe、连续监控、实际ABI和完整内存峰值预算及退出取证。没有这些证据仍不启动计算。已预约时段仍按RESERVATION-PLAN.md提前评估，不占模型或大池等审批；本模块不批准P0/P1或任何GPU运行。GLM历史与40ID不变。

## 独立宿主服务部署清单候选（2026-10-31）

在原模块新增service-plan子命令，参数与guard相同：`python -B <run>/code/source_schedule.py service-plan --registration <run>/registration.json --registration-sha <SHA>`。先复用同run/code位置、registration/plan/code/spec验证，再只输出JSON内的systemd-run argv；不创建unit、目录或日志，不调用systemd，不获得计算准入。实际调用此入口仍须从该run的已核源码树执行，普通checkout不代替部署包。

候选使用system manager的独立service，unit包含完整run_id与holder，User=2147、Type=exec、system.slice、Restart=no、KillMode=control-group；固定256M/100% CPU/64 tasks只是待实机核验的guard资源上限，不是模型容器资源。命令用固定/usr/bin/python3及-B -E -s，脚本在本run/code，参数绑定registration SHA；不用scope/pty/pipe或shell，路径拒绝空白、美元/百分号、换行及非canonical路径。授权操作系统manager可能需要管理员权限，本候选不申请或扩大权限。

预算只从原batch cleanup倒推，预留最多10秒提交间隔、10秒激活和5秒停止，剩余运行秒数向下取整；未来/已到guard fire/预算不足即拒绝。launch_before_utc是未来实际控制器必须在提交时强制核验的期限；不能保存命令后晚些时候直接执行，也不能只凭相对RuntimeMaxSec代替guard内部原绝对截止。当前没有实现实际提交/单元接管或观测，不宣称截止守护已部署。

实现依据为[systemd-run官方手册](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)和[systemd.service官方手册](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)：transient service由service manager作为父进程，Type=exec只保证exec已发生，不等于guard ready。下一步必须核当前节点systemd版本/权限、实际unit属性/命令/MainPID/cgroup/启动时间和同次主动challenge，再接monitor/60秒释放；所有独立服务/部署/runtime标志继续false。

45项相关CPU测试通过；其中fresh CPU子进程真实使用-B -E -s确认没有Torch/vLLM导入，未调用systemd。CLI分支以显式code gate/renderer mock验证绑定失败先拒绝、不进入watchdog、不创建事件文件，不冒充真实部署包通过。全套324项中320通过、4POSIX FIFO因Windows跳过（29.600秒），当前两文件静态复审PASS，完整哈希及范围见source-schedule-review.json最新追加。

## 宿主服务只读验收候选（2026-10-31）

新增service-probe分支：`python -B <run>/code/source_schedule.py service-probe --registration <run>/registration.json --registration-sha <SHA> --service-plan-sha <SHA>`。共享源码/配置和ready核验后，从既有run根读取source-watchdog-service-plan.json并核外部SHA和同一registration，再严格JSON比对由原schedule重派生的完整计划；不能用False与0的Python相等性混淆计划类型。该文件是未来部署控制器应保存的清单，本轮未创建实际run或部署文件。

生产默认路径固定本机/usr/bin/systemctl --system --no-pager --timestamp=us+utc show，使用干净环境和既有有界capture，核Id/Transient/UID2147/Typeexec/system.slice/MainPID/ControlGroup/资源/超时/InvocationID及激活时间。仅接受所生成的整数秒时长政策。启动和激活时间必须位于本批计划界限，实际active+RuntimeMax+stop不得越过原cleanup。us+utc格式依据[systemctl官方手册](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html)，文档标明从248支持；实际节点版本或输出不支持时拒绝，不能静默跳过。UID参数按[systemd v249官方源码](https://raw.githubusercontent.com/systemd/systemd/v249/src/run/run.c)传入User字符串；这只是源码依据，不声称节点运行v249。

同时检查实际/proc的UID/boot/start_ticks/namespace/cgroup，读取最多4097字节并拒绝超过4096的cmdline，核argv与绑定的python -B -E -s命令逐项一致，以及/proc/PID/exe与/usr/bin/python3的dev/inode。主动challenge使用该PID/nonce/完整模型目标身份，前后再次检查服务属性、InvocationID、PID寿命和命令/可执行inode；一个最多5秒绝对预算包含计划重派生、查询、proc检查和最多2秒主动probe。同步文件读取仍不能强制中断，需要独立宿主边界兜底。

全部条件成立时，函数仅证明这次service身份验收，返回conditional independent_service_verified；实际提交前截止尚未证明，service_submission_deadline_verified、guard_deployed、runtime_admitted和resources_released保持false。后续实际部署控制器必须先核launch_before再提交、保存提交/启动证据并确认上述unit/probe；本候选不能单独批准模型或GPU。

53项相关CPU测试通过；systemctl/proc/active-probe均为明确替身。额外真实临时文件和hardlink核有界cmdline/最后NUL/exe inode，只有proc路径替换，不冒称Linux实测。初次UTC标记夹具失败、严格JSON类型负例失败及修正均保留source-schedule-tests.json；全套332项中328通过、4POSIX FIFO因Windows跳过（29.555秒），当前两文件静态复审PASS并核SHA。未查询远端服务、安装unit、启动模型或创建池；下一项仍是实际提交边界与monitor/60秒释放。


## P0a 身份与守护输入接线候选（2026-10-31）

复用原source_bundle的逐文件字节验证，但P0单独使用kimi-p0身份、kimi-cuda-ipc-p0a-run-v1 spec、kimi-p0-window-v1 plan、kimi-p0-watchdog-registration-v1 registration和kimi-p0-guard-target-v1 target。既有run根读取p0-spec.json/p0-plan.json/source-code-manifest.json，code清单必须包含native_record_call.py和source_schedule.py；不创建新树或环境。spec限定qjhs003、完整镜像ID和单GPU UUID、40字节至1MiB载荷、4至300秒组件上限。common code manifest和guard event/service-plan沿用格式名，不授予source模型审批。

P0 phases为preflight/guard_setup/component及原shutdown/observe/cleanup/margin尾部。完整批次最多1800秒、component必须等于spec上限、观察至少60秒、最终清理至少预约终点前10分钟，仍由原batch anchor计算，不因验证或创建服务重置。所有booking/guard/runtime准入标志保持false。

P0 target要求purpose KIMI-CUDA-IPC-P0A-UNREVIEWED；guard在不可变ID/Created/UID/五项身份核对之外要求一个指定UUID的nvidia DeviceRequest、2CPU/4GiB/128PID/64MiB shm、network none、private IPC、非privileged和无额外device/cap。它们是待实际准入核验的上限，不是实测占用。旧source schema/purpose或跨scope组合不能批准P0；source原行为和回归保持。load_watchdog_inputs由现有guard/service-plan/probe入口共享，尚无P0 worker CLI或实际宿主控制器。

本次349项CPU测试345通过/4 Windows POSIX FIFO跳过（42.715秒），相关bundle16/schedule56通过。当前四文件静态复审PASS及精确SHA追加原source-schedule-review.json，不等于运行R1或guard/GPU通过。下一项只连接P0 worker命令、宿主启动/监控/60秒释放，以及有效窗口和可复用合规runtime；不以模型完整转换或native全编译作为这个Torch小组件的前提。


## P0 独立守护一次性提交候选（2026-10-31）

在现有模块增加service-submit命令，参数同service-probe（包括外部--service-plan-sha），仅接受P0 scope。原run/code与spec/manifest/registration/plan验证后，再核source-watchdog-service-plan.json的外部SHA及registration；在既有run根独占source-watchdog-submit-events.jsonl。重复文件即拒绝，不自动重新提交。此命令有真实提交能力，但本轮只完成本机CPU代码与测试，没有在远端执行；不得绕过完整运行审阅、窗口及资源授权使用。

submit_p0_watchdog精确重派生计划，使用原guard_setup绝对截止及最多30秒monotonic总预算；提交前后均检查launch_before_utc。先核当前UID2147/boot/PID namespace/qjhs3或qjhs003宿主身份，再inspect不可变容器ID/Created/image/labels与P0隔离上限，并要求尚未运行。P0 systemd-run命令增加--no-ask-password，不触发交互认证或改变系统权限；此选项由[固定systemd v249源码](https://raw.githubusercontent.com/systemd/systemd/v249/src/run/run.c)核验。现有system manager是否授权仍未知，失败即拒绝，代码不安装polkit规则、不使用凭据或扩权。旧source renderer不变。

systemd-run只调用一次；原提交期限内未返回、超时、非零或后续ready/probe失败均不启动计算。超时不能证明manager未创建service，错误附guard_submission_receipt，submission_outcome保留unknown或client_returned_zero；不得自动重试或取消未知unit。ready等待使用普通文件非阻塞有界读取，每次至多16385字节，缺失/未写完只在同一原预算内等待。取得两条完整就绪事件后调用既有严格身份/服务属性/进程/active challenge探针，再次inspect确认目标仍停止。最终证据写入也计入原总预算。

成功仅条件性证明这次守护提交与独立服务验收，guard_deployed和service_submission_deadline_verified可为true，runtime_admitted/resources_released仍false。实际使用还必须同时要求真实CLI exit0及当前完整启动准入；仅一条submission_verified事件不足以批准GPU。错误证据写入失败不会替换原错误。这里没有Docker start、容器删除、unit停止或GPU调用；宿主完整启动、持续监控与60秒观察仍待接线。

原Windows orphan-child CPU夹具曾再次在child.err清理时报WinError32，原因是文件关闭marker先于进程实际退出/原生继承handle释放。现在仅在测试中为launcher返回的自有child申请SYNCHRONIZE权限，使用[WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)等待实际退出后关闭句柄，不signal/kill进程，不改变生产guard。依据[OpenProcess官方说明](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess)，没有申请终止/写内存等权限。

61项相关CPU测试通过5.276秒；完整356项352过4 Windows POSIX FIFO跳过44.065秒。systemd/Docker/proc均明确替身，ready用真实小文件和模拟时钟；WinAPI child等待在实际Windows执行。初始旧fixture失败、新TimeoutExpired测试异常类型修正及最终精确hash/只读复审PASS均追加原review/tests文件，历史失败不删除。没有远端unit、GPU或服务通过。


## 创建前冻结输入与 P0 target v2（2026-10-31）

在接Docker创建命令时，源码分析发现旧P0候选存在循环：worker Cmd需要host registration SHA，但registration包含只有create后才得到的container ID。本聊天该候选尚未实际部署；这不是一次已发生的Docker/GPU失败。现改为创建前冻结p0-worker-input.json，精确四字段schema/plan_sha256/batch_entered_utc/environment_sha256。它通过原plan绑定完整code manifest、spec、固定image/GPU和原预算，不包含CID或host registration SHA。

准备顺序为：先核既有固定image Config.Env并生成完整环境摘要，冻结code/spec/plan/worker input及其外部SHA；再由p0_container_command输出确定的Docker create argv；只有create返回CID/Created后才生成宿主registration。新target schema为kimi-p0-guard-target-v2，包含run_root/worker_input_sha256/environment_sha256；宿主将其与同份worker input的plan/anchor/spec/schedule逐项核对。旧P0 target v1拒绝，原source target schema与GLM结果保持。worker现在只接受 `python -B -E -s <run>/code/native_record_call.py p0a --worker-input <run>/p0-worker-input.json --worker-input-sha <SHA>`，仍绑定自身文件与原截止。旧带--registration的P0 worker示例仅是上节历史候选，不能用于新版本。

p0_container_command是纯argv生成器，没有Docker调用或目录创建。固定--pull=never、不可变image、nvidia runtime与一GPU UUID、UID2147、2CPU/4GiB且无swap/128PID/64MiB shm、private IPC/network none、cap-drop ALL及no-new-privileges。覆盖image默认entrypoint为python3，使用-B -E -s，固定工作目录与精确worker Cmd。仅run根RW，code及四个JSON输入文件分别只读bind；不额外挂载模型/Store目录或发布服务端口；实际run根仍须由准入确认是本次小运行目录。这是候选配置，不是Docker实际接收或runtime通过证明。

完整环境摘要由p0_image_environment_sha256对固定image基础ENV加六固定覆盖派生，使用唯一key的canonical JSON map；只存SHA，不保存基础ENV明文。guard核整个Config.Env摘要及label，顺序可变但重复key、额外变量或修改值都拒绝，不能只检查必需变量子集。初审新增LD_PRELOAD负例已先失败（1项0.004秒，ValueError未抛），修后通过；旧源码/测试SHA及NEEDS_FIX记录保留于source-schedule-review.json。-E/-s另外限制Python环境变量和用户包目录，但不把它们当作动态链接器环境校验的替代。

guard_target同时核精确Cmd/entrypoint/workdir、全部挂载/读写性、runtime/MemorySwap/capdrop/security policy。宿主真正prepare时必须查询选定image的实际Config.Env并与固定image ID绑定；当前CPU夹具里的ENV摘要只是声明，不能证明真实image/ABI已验。源码、预算和输入验证仍不代替有效窗口、实际资源、独立守护和完整运行审阅。实际create/start、监控与退出后60秒观察仍未闭合，本轮无远端计算。


最终同时保留六项固定环境值校验，防止准备阶段自己把错误override冻结进合法摘要；错冻结CUDA_VISIBLE_DEVICES=all负例先失败1项0.006秒，修后通过。完整358项354通过4 Windows POSIX FIFO跳过40.820秒，62相关4.956秒；当前六文件静态只读复审PASS/SHA及全部中间失败追加原review/tests，模块specific ledger指向同一最终审阅。没有重标历史失败，没有实际创建或启动容器；不能据此声称本轮开始时计划中的monitor/60秒已完成。


## P0 只创建入口与宿主 Python3.10 时间兼容（2026-10-31）

原source_schedule增加p0-create命令：`python -B <run>/code/source_schedule.py p0-create --worker-input <run>/p0-worker-input.json --worker-input-sha <SHA>`。要求run/code与小输入已存在，不创建新的目录；现有根独占p0-create-events.jsonl，重复调用不会覆盖证据。core在原preflight wall截止和最多30秒monotonic预算内做完整输入/code验证及自身路径绑定，实际entered必须不早于冻结batch anchor。此候选有metadata create能力，但本轮没有远端执行，不得据此跳过整体运行审阅或资源授权。

只查询本机Docker exact-name不存在和固定image ID配置，不替换已有名字。p0_image_environment_sha256现在由这个core消费真实image.Config.Env再核已冻结摘要；同时在create之前拒非空Config.Volumes，避免与既定六bind之外的卷及隐式副本。这个检查不读取镜像文件内容、下载或复制层；不证明Torch/CUDA ABI已实际加载，runtime_abi_verified继续false。

仅一次Docker create，之后用完整CID inspect核固定Cmd/mount/ENV/profile、Name/Created和停止状态，返回给后续宿主registration/guard。源码没有start、stop、rm、volume删除或重试。timeout不等于daemon取消，state保留create_outcome unknown或id_returned、唯一name/image/workerSHA、cleanup_owed；如果ID已经返回，即使随后时间耗尽也先保留ID再拒绝。名字存在、镜像错误或提前调用等未执行create的失败不冒认清理所有权；取证错误不覆盖原错误。成功也仍然cleanup_owed，resources_released/runtime_admitted=false，不能当完整部署通过。

另在已有WSL原生Python3.10.12实际复现：旧_utc不能读Docker的九位小数Z或+00:00 Created时间，普通+00:00控制可读。现规范化Z及小数为Python可表示的微秒，与新版Python结果保持；Created身份核验仍比较完整原始字符串，差1ns也拒绝，并未把身份精度降为微秒。当前原生Linux 67相关控制全过3.649秒（Docker/宿主/verifier为明确替身，路径/文件/JSON/解析器实际执行），没有安装Linux Torch。Windows完整363项355通过8跳过52.418秒；其中4个新Linux创建控制已在WSL跑过，其余4个POSIX FIFO未在这个无Torch的Linux配置跑全。

初审future-anchor及未登记Volumes两负例旧core先失败（1方法2失败0.044秒）并留旧hash/NEEDS_FIX；修订两文件静态复审PASS与最终测试追加原review/tests。不宣称真实Docker创建成功。下一项是将成功create返回的target物化为同run的宿主registration及service计划，再接经实际准入的start/monitor/至少60秒释放；先前ENV来源缺口已具备代码检查，但仍需要真实节点执行证据。


## 创建结果到守护输入的物化（2026-10-31）

新增 `python -B <run>/code/source_schedule.py p0-register --worker-input <run>/p0-worker-input.json --worker-input-sha <SHA> --create-events-sha <SHA>`，只准备元数据，不部署unit或启动容器。所有目录必须已存在；现有run根独占p0-registration-events.jsonl。core完整核worker/code、原batch起点和guard_setup绝对截止，wall/mono总预算至多30秒。

从固定p0-create-events.jsonl普通文件有界读取最多16385字节并核外部SHA，只接受create_prepared/create_verified两条、同identity/holder、完整创建时序和停止状态来源。它不接受单独的布尔“已创建”声明。随后实际查询同CID，核fresh host boot/UID/PID namespace、Name/Created及全部v2 profile；写入后还要再查一次目标仍停止。CPU测试里的这些Docker/host查询是明确替身，生产默认使用既有local Unix socket，没有在本轮实际执行。

fresh nonce及原plan SHA/batch anchor生成registration.json；同一target/registration SHA派生source-watchdog-service-plan.json。复用native_index._publish的exclusive .partial+hardlink不覆盖发布，并fsync目录；不改已有原生Store发布行为。已有final或partial即拒绝。第二份文件发布失败时保留第一份及其SHA，失败不回滚、不重试覆盖；临时hardlink删除失败则报告pending_partial_cleanup，不能隐藏其清理成本。最终真实调用既有load_watchdog_inputs/load_source_plan读回消费者，校验原schedule、两个文件SHA/内容及原service launch-before。

返回registration_materialized只说明元数据物化。create日志不证明原创建CLI exit0，因此creation_cli_exit_verified=false；guard_deployed/runtime_abi_verified/runtime_admitted/resources_released也全部false。后续执行控制器仍须独立记录真实各CLI退出状态、重新核deadline及其他完整准入；pending partial必须保留记录并在本实验退出时归属清理。此入口没有start/stop/rm或systemd-run，不宣称计算已经开跑。

WSL Python3.10原生71项全过3.812秒；新四个Linux控制在真实小文件上从实际create core事件走到原消费者读回，Docker/host/frozen-code verifier仍明确mock。覆盖错误SHA/stage/holder/停止证明、未来/过期/boot/运行/Created变化、半发布失败保留、原件拒覆盖、真实hardlink残留报告及CLI重复journal拒绝。Windows完整367项355过12跳过43.182秒，其中8个Linux创建/登记用例已在WSL覆盖，另外4个POSIX FIFO尚未在这个无Torch Linux环境跑全。当前两文件静态复审PASS/SHA保存在原source-schedule-review.json，不代表完整运行R1。下一项是把实际CLI exit0、真实guard提交/probe、当前有效窗口/资源与ABI、start/monitor/至少60秒释放串成完整执行闭包。
