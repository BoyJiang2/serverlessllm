# C4 本地断线/清理链只读审查

2026-10-26，目标run=sllm-glm51fp8-20261026T101414Z-b7e048db。仅读本地文件，无SSH、资源运行、Git或源码修改、无DeepSeek调用。与Oct25正式CID8ebd事件严格分开。

## 已记录

1. operator-service13a-client.log第22行：10:58:59.666784Z开始docker wait，目标完整CID5549ec44672e8738e503f886fe632c6b1294187083ced31f6e12944ca43e3946；下一行client_loop: send disconnect: Connection reset，无docker wait结果事件。本地client返回255由主agent提供，末行本身没有可读的returncode字段。
2. 当前work/g1_operator_canonical4.py:95等待ssh子进程；:97–98在SSH非零且cleanup-requested不存在时同步调用root_active_control.py cleanup，check=True。root_active_control.py:15–18先写cleanup-requested，:23执行自身run的清理脚本，:29传播返回码。marker明确写着“not release evidence”。它防重复请求，不能作成功释放/禁止失败重试的证据。
3. cleanup_log-stdout.log记录19:19:09+08开始、target严格为本run。当前runtime/active/cleanup_guard13.sh同样冻结本run；先systemctl kill --kill-who=all --signal=SIGKILL target.slice || true，然后按run label、uid2147逐CID timeout15s docker stop --time=5。
4. 原始清理日志对目标service的stop?t=5报context canceled，随后inspect仍running，19:19:25结束 remaining=5549ec44672e failed=1。故该次自动清理确实尝试执行，但未完成释放证明。systemctl kill无返回/信号递送记录且||true不能由无输出证明成功。
5. 同一清理中monitor于11:19:09.892729454Z、head于11:19:10.402275814Z退出137。service最终11:19:34.230795862Z exit137/OOMKilled=false及supervisor-result缺失由主agent提供；本次本地文件尚未包含service最终inspect/独立release/snapshot。

## 证据强度

SSH reset → 本地非零处理进入明确安全清理路径，有源码+marker+正确run清理日志相互支持。该清理先发SIGKILL的意图与monitor/head紧随退出同向，强支持C4终止为断线触发的本地安全清理结果；service稍后退出与未完成清理的异步收尾相容。但没有逐信号/daemon事件，不把全部延迟过程写成唯一已实证因果链。

网络reset来源未知，不推断服务器、代理、NAT、用户或平台故障。退出137不能单独证明模型OOM或模型崩溃；OOMKilled=false也不是绝对排除其他并发原因。C4未形成passed including cleanup，不作成功业务/完整服务通过。Oct25事件已有独立daemon stop90s升级证据，此次不可混用。

当前源码属于审查时副本，非不可变执行轨迹；与历史执行一致的前提必须保留。cleanup-stdout.log审查期间被主流程更新，未作为固定证据复制；最终释放以主agent重试后精确收据为准。

## 最小改进建议（未实施）

- 在现有ssh argv显式添加-o ServerAliveInterval=30 -o ServerAliveCountMax=6，减少长时间无输出时的空闲断开、给连续失联明确界限。ConnectTimeout=10仅控制连接建立。原operator未显式传ServerAlive；不能据此说SSH config有效设置完全没有。keepalive不能保证抵御主动reset，也不解决已发生reset。
- 断线先记录本地UTC、returncode、phase/CID/boot/starttime；在独立guard deadline仍有效、预算充足前提下，最多一次短超时重连，只读对账同一身份的Docker State、supervisor-result及轻量heartbeat；不重复launch。若身份/状态不明确或无法重连，沿现有安全清理策略执行，不能把重连当无限延长资源窗口。
- 清理请求marker与已释放receipt分开。清理非零/remaining非空时不能因marker存在就当成功或拒绝明确重试；以同run/uid的bounded cleanup+独立release60及snapshot结束。保留首次失败和后续重试，不覆盖原始日志。无需新后台框架。
- C4作为控制失败/中断保留。再次控制的首要验证是SSH通信和断线对账/清理收据，不把本次137解释成FP8本体错误。

## 原始文件SHA（审查时）

operator-service13a-client.log: 48f04c8c7fdb029c462146e7bbb0528e2e7e713853db7f837f81e2cb0a8762a4
cleanup_log-stdout.log: 6fe8e058068b72a0baedfeed2dcec1e448878b80c05f3eedc594866860276b66

本报告不是R1/R2或正式计时审批。主agent正在进行的新精确清理/release/snapshot不在本次固定历史证据内，收到后另行对账。