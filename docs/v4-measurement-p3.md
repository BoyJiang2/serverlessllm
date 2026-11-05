# P3 GLM指标定义复核与V4离线计算

2026-11-06。父提交 `5cd66066fc34683d465733373f9daf0994f8fa79`。独立分支 `sllm/v4-raw-store-p3-20261106`。本批是离线证据计算库和CPU测试，不是实验runner、正式准入器或新GPU运行。

## 对P1/P2措辞的两项更正

本轮直接读取 `qjhs003:/data/brick1/boyjiang/glm5.1-fp8/code/c23-reference-f176e05/` 下formal_trials.py、phase_summary.py、accept_control13.py及release_control13.py等6个控制文件，并对照已有GLM backend/patch。来源与完整字节SHA见 `docs/evidence/v4-measurement-p3/glm-control-source-manifest.json`。以下实际源码定义优先于早期P1/P2主方案中“客户端首token”等措辞；不修改历史结果或冻结请求。

| 字段 | GLM C23实际来源 | V4应采用的边界 |
|---|---|---|
| first_token_observed_ns | backend_first_token_observed：backend生成循环首次看到非空token_ids时记录，phase_summary按request_id提取 | 同名backend主机事件；不是HTTP客户端首字节/首token，也不是精确GPU生成时刻 |
| command_to_route_publication_s | router_instance_published事件减startup_command_ns；事件在实例从starting移入ready池后发出 | ready实例发布。Controller注册冷路由仍是register_complete_ns等单独事件，不能替换它 |
| correct_business_response_ns | 完整HTTP响应被控制端接收且业务校验通过后的received_ns | V4自然EOS业务请求的完整正确响应；warm fixed32不得填入 |
| phase_*_span_s | TP8每rank恰好一对begin/end，全rank最早begin至最晚end | 主机函数包络；嵌套/重叠和各rank耗时不得求和当总时长 |

同一个first_token字段不能在GLM使用backend、V4改用client。当前P3服务还没有迁入完整GLM phase/event recorder，因此实际V4 TTFT缺证据时必须保持缺失；不能取HTTP完整响应时间补上。未来客户端流式计时另列client_*字段。所有monotonic跨进程相减必须先验证同主机/boot/时钟域，离线库本身不验证这些身份。

## EOS源码核对与业务检查

固定目标B1仍为vLLM `132765e3560659ff63ebd236203672e991b70e08`。官方[check_stop](https://github.com/vllm-project/vllm/blob/132765e3560659ff63ebd236203672e991b70e08/vllm/v1/core/sched/utils.py)在EOS分支设置停止状态，但不必填写stop_reason；[detokenizer](https://github.com/vllm-project/vllm/blob/132765e3560659ff63ebd236203672e991b70e08/vllm/v1/engine/detokenizer.py)跳过EOS文本解码时仍保留该token ID；[output processor](https://github.com/vllm-project/vllm/blob/132765e3560659ff63ebd236203672e991b70e08/vllm/v1/engine/output_processor.py)累计输出使用detokenizer token IDs并透传结束原因。不能将stop_reason=None自动判失败，亦不能只看stop_reason=1便声称完整业务成功。

`benchmarks/v4_evidence.py`提供四个纯stdlib计算函数：

- response_checks：business严格检查strip文本为4、finish_reason=stop、末尾token ID=1且stop_reason为空或整数1；与原生token一致性分别返回。workload32单独检查32token及length结束，绝不返回complete_business_success。
- timing_metrics：重算原GLM命令/触发至首token或正确响应及preparation指标。缺backend首token原文时不生成TTFT；拒绝字段与原文不符、重复事件、时间倒序，以及业务失败却保留正确响应事件。准备或注册阶段失败无需伪造响应时间。
- phase_metrics：按GLM的(hostname,pid)→rank映射校验TP8完整阶段对，计算7个span、2个旧别名及backend_construct_s和command_to_route_publication_s。调用者传入已限定本次启动/请求窗口的事件，库不替代窗口身份验证。
- release_coverage：检查61个顺序样本、首末实际跨度至少60秒、最大间隔5秒、GPU采样晚于删除确认且新鲜。返回sample_span_seconds作为覆盖证据，**不覆盖**原idle_observation_seconds：原字段是控制端实际循环起止耗时，两者边界不同。

这些函数不读取或修改CUDA、驱动、Store、缓存、Ray进程。response_checks输入是backend原始API响应；参考金标的模型/revision/请求/引擎身份、transport完整性及原生金标本身正确性必须由调用者独立验证。它们不授予attempt success。

## 历史实物重放与CPU验证

只读复制C23历史run `sllm-glm51fp8-20261027T011726Z-28c92313` 的store_pool_hit_10/result.json、phases.json、idle60.jsonl。路径为规范根validation/controls/<run>/formal-trials/<id>/；result与此前P1/P2读取的原文件逐字节相同。源路径、字节SHA和两个上游源码blob见 `sllm_store/tests/fixtures/v4-evidence/manifest.json`，fixture禁止Git换行转换。

16项CPU用例在本机及qjhs002通过。以独立历史raw_events重新算出原result的全部16个metrics，键集和数值均匹配至小数点后9位；同时重放历史60秒采样时间覆盖。其余用例拒绝缺rank/重复phase、缺backend事件、时间倒序、把fixed32当业务、stop_reason误判、少于60秒、缺采样和陈旧GPU快照。两个EOS测试执行固定上游源码AST的check_stop及detokenizer.update，没有导入真实vLLM/Torch/tokenizer。

历史GLM的35/40成功计数、各协议分层与原始失败不改变；重放不是新GLM实验，也不是V4模型金标。新V4自然EOS业务与历史GLM业务/金标条件的差异必须随跨模型描述性比较披露；不能因字段名相同便合并成功率或归因绝对延迟。

## 未实现的准入和运行工作

物理释放仍需沿用GLM的helper身份/所有进程、GPU UUID/HBM、RSS/PSS/locked/driver/IPC等完整证据；本函数即便返回60秒覆盖也不能批准release。缓存mincore位图与输入闭包、Store pinned-pool八rank完整驻留、所有源码/请求/参考hash、监控身份及覆盖、超时/失败分母、两次生命周期和原始stdout/stderr的完整验收不在本库中。没有移植完整accept_control13：其中GLM 148文件等模型特定约束不能直接套给V4。

新V4冻结的12ID顺序、3次/条件、1200秒上限、自然EOS业务后warm32等规则保持不变，尚未消费任何正式ID。未启动真实Ray/模型/Store或安装环境。完整Pro单slab TP8容量guard仍拒绝恢复，表示路线仍待用户选择；整体迁移/运行验收保持未完成。
