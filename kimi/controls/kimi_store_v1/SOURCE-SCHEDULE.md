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
