请在同一只读会话继续 R1b 的受影响修订审阅，本次是 R1c。你是 DeepSeek Harness 只读 AI 交叉审阅者，Codex 实施。仅用 read/search 读取本仓库，不执行 shell/远端/资源操作，不修改文件，不读凭据，不派代理。调用附上当前 clean code commit 与 manifest SHA；最终 approved/changes_required/blocked 必须绑定这两者，不签 R2 或真实 G1。

先读 controls/candidate13c/USER-OBJECTIVE.md、PLAN.md、R1-RESPONSE.md、control-input-manifest.json。旧 controls/candidate13b 完整保留；你的上轮 N01–N14 结论在 reviews/verdicts/deepseek-r1-control13b-final.md。直接读改动后的实际源码及相应 .diff，不能只审摘要。未变的源码可复用你在原会话实际读取的上下文，同时所有178个文件均有完整哈希清单可定位；工具不能算哈希时如实说明。

重点：root guard 的真实 probe/sentinel/result 与持久私有证据、root snapshot/index；preflight 的祖先 cgroup 内存、observer身份/心跳；launcher/supervisor 的全预算与停止；deployer 的 code commit、源前缀与 metadata-only后续提交约束；prepare_host的复制字节/目标hash；验收逐字段核对/流式监控/清理61条及60s；分离的 bounded PSS 子进程不得拖住快心跳；新 store_numa/run_store wrapper、负向测试、所有配置及 materializer。全部是未执行代码；真实 probe/G1/八rank/R2 均未通过。25本地测试、Python compile与3bash-n只证明本地检查。

本版新加入缓存控制，必须审实际 cache-inputs13.json、cache_contract13.py、cache_state_control13.py、byte-exact cache_prepare_v6/v8.py、cache_repair_v9.py、bootstrap触发位置与 accept.validate_cache。冻结148输入；初始完整 mincore 0、末次完整100%，最多2修复/总600s/8读线程/NUMA容量余量64GiB；pgsteal仅解释，不是临时约等于零准入。控制不代替四次原生生命周期，也不声称之后生命周期缓存条件等价。官方LFS权重hash是冻结身份，不是本次fresh全payloadhash；运行前尺寸/闭包和元数据hash检查，后续真实8rank权重审计独立。

历史终止新证据必须实际读 evidence/historical-termination/CURRENT-BOUNDARY.md 和两个 target-journal JSON，可读联合调查报告/真实DSH收据。正确CID/boot的dockerd有signal15等待90s后强制退出记录，但上游stop请求人/原因未知；旧137失败不改判，不归因Ray。旧kernel查询--kernel失败已由-k成功的v3补充，不据无匹配绝对排除OOM。不再机械沿用已经过时的无daemon机制记录说法。没有新信号探针/全局Docker改动；如认为R1仍需最小独立非计时探针，请具体论证范围/信息增益，不凭猜测。

N01 pool标记不放宽：logging-source真实21前缀18标记及before-InitGoogleLogging STDERR证据。N10 StoreManager实际连接worker-address:8073，改loopback会破坏跨容器原生服务，请看pinned实际源码而非猜测。N06 readonly父挂载需唯一空子挂载点，不写权重；N08原契约是token/text/finish精确+logprob有限，不是float logprob精确。这些作者反证也需你独立核验，不预设正确。

当前用户明确GPU被其他作业占用；仅CPU工作，没有Ray/大池/全量缓存提前运行，不假设02:50释放，不终止他人。08:50新独立守护仍待安装；旧02:50守护到期不允许遗留无守护进程；正式原run还需另行守护交接。实际释放后必须新核验占用/NUMA/窗口与预算。现有直接授权与三条预约保留，不扩大成无限使用。不重置40IDs、金标、历史失败；9终态5成功4失败，下一项仍store_pool_hit_03但要G1/R2/gate后。

请逐项给剩余 finding 的文件/函数/实际证据/影响/最小验证，纠正已证伪推测。静态R1可列明确运行前置条件，但不把未执行G1或最终R2说已通过。可输出 JSON(stage,verdict,code_commit,manifest_sha256,findings,runtime_conditions,limitations)。请完成这次审阅并返回独立结论，不只给中间计划。
