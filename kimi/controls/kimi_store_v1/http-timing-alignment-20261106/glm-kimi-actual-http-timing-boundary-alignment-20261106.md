# GLM / Kimi actual HTTP timing boundaries

GLM历史注册命令至第一条算术17+25正确回复；不是纯权重读取，不是第三条fixed32天空题。正确性在后面验证，但通过后采用先前received_ns作为终点。

GLM source: /data/brick1/boyjiang/glm5.1-fp8/validation/controls/sllm-glm51fp8-20261027T011726Z-28c92313/executed-source-f176e05/formal_trials.py
SHA256: 18cf7d97d9c12f971fcfcdf1ae1c09e483bb76a48fa45314bc1ff9b5417094e4

| Metric | Actual GLM | Required Kimi | Verified state |
|---|---|---|---|
| A 注册→算术正确回复 | startup_command_ns：同HTTP进程中调用http('/register',config)前 → received_ns：http(arithmetic)返回后立即取时；含JSON解析及响应上下文退出，不含落盘/金标校验 | 首条HTTP进程连续注册→算术；registration_command_ns→received_ns；父启动/轮询/存盘/校验另记 | 修订中，最终代码与实际消费者尚待独审，不能宣称已经完全一致 |
| B 算术触发→完整回复 | business_trigger_ns：调用http('/v1/chat/completions')前，含Request/JSON请求构造 → 同received_ns；不等同纯网络读取终点 | 同一HTTP进程business_trigger_ns→received_ns，另列urlopen/read/parse/validation时间 | 修订中 |
| 响应体读取/解析/校验 | json.load(response)内部读取并解析 → 未独立采集body-read及validation终点；received_ns在解析后、验证前 | 分开记录body_complete、json_parse_complete、http_return received_ns、gold_validation_complete及parent验证/落盘 | Kimi新增分离字段，不反推GLM缺测细分 |
| C 权重/摄入阶段 | 实际phase事件的跨rank min_begin → 跨rank max_end；span可能重叠，非纯磁盘IO | 现有Model loading日志覆盖get_model_loader/load_model/aux setup，仅可报该较粗duration，不能改名为相同weight_ingestion span | 当前细分口径不一致；缺少的同口径摄入事件明确缺测，不据此修改loader/内核 |
| D 已就绪fixed32 | 算术→翻译之后，第三条天空题调用HTTP → 解析后的received_ns；32 tokens/length | 同顺序、自己模板IDs；max_tokens32/ignore_eos true；trigger→received_ns与实际32/length分开证明 | 请求金标已接受；正式消费与计时修订待审，正式样本0 |
| E 初始化/缓存/删除/释放 | 分别使用初始化/准备/delete实际事件 → 各自真实完成点；历史准备含旧池卸载影响需保留 | 公共初始化、条件构造、准入、删除、正常退出及>=60秒释放分别记录，不能藏入A或固定32 | 既有功能证据保留；正式成本不复用L1耗时 |

Formal samples: 0. Active run ID: none. This is a source boundary audit, not a runtime measurement.
