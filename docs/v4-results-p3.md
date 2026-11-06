# V4 结果重建与 GLM 字段导出（P3 CPU）

本批增加 benchmarks/v4_results.py，只读重放已声明的 attempt journal，并在独立新目录生成每个冻结 ID 的 result.json、summary.json 和带输入/输出 SHA 的 manifest.json。它不创建执行 origin、不消费正式 ID，不调用模型/Store/Ray/HTTP/缓存准备或 GPU。当前活动代码和证据仍在 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}，权重按用户指定保留 /mnt/raid/models/DeepSeek-V4-Pro，Store 大数据留 /data。

## 定义与比较边界

对齐既有 GLM 的 attempt_id/condition/block、execution_run_id、code_commit、source_manifest_sha256、preregistration_sha256、preparation_begin/complete、startup_command、register_complete、business_trigger、correct_business_response、first_token、delete_command/complete 等字段。指标仍由已有 timing_metrics/phase_metrics 重算，不复制 journal 中预填的浮点数；保存值和重算值不符即拒绝导出。后端首非空 token、完整自然 EOS 业务 HTTP 响应、固定32长度请求三者保持各自边界。

GLM C23 原 result/phases/idle60 三份历史文件重新只读核对服务器 SHA，并与现有 fixture 原字节一致；回执为 glm-reference-receipt.json。历史 16 个指标重算仍由统一 CPU 套件覆盖，不把历史 PASS 赋给 V4。V4 的模型 revision、请求 SHA、量化、TP/EP、上下文等配置以独立冻结协议为准；导出 manifest 包含完整 protocol_document 和其原字节 SHA，便于显示配置差异。既有 V4 三块/12 ID、1200 秒、61 样本至少60秒与最大5秒间隔均不变。

派生结果与 GLM 可并列阅读字段，不是旧 GLM formal admission 的直接替代输入。GLM 的 protocol/resource/preparation revision、物理清理/JIT/NUMA/干扰等不存在的新证据不补造。V4 当前仍是记录器，不能推出真实控制面成功、native gold 来源、容量、完整权重、模型精度或 Store 物理恢复/释放。

## 失败信息修复和共同校验

此前失败终态只有 correct_business_response_observed 标志，若业务响应正确但事件读取失败，原始响应与时刻会丢失。新 AttemptJournal 在失败终态补存当时 record 和 business_observation（响应、native reference、received_ns，已读取时附带 events）。业务正确性与 native token parity 分别保存；语义/自然 EOS 正确但 native parity 失败时，仍保留正确业务响应已经返回的事实，整体失败不变。

旧记录没有原始证明时，correct_business_response_observed/right_censored 保持 null，并列出 missing_evidence=legacy_business_observation；不从失败时刻或假设补出 HTTP 完成时间。新失败记录完全缺事件时可以重算已观测的业务响应延迟，但没有 first_token 或 phase 字段，并明确缺 business_events。首 token 缺失或 rank 阶段不完整的已记录失败，也分别保留可证明的响应/首 token 指标，并列出 missing_evidence；已接受的 business 阶段仍要求完整事件链。timeout 的 startup_censor_elapsed_seconds 单独列出，不填进完成延迟；响应已正确观测而后续释放超时时不再右删失响应。

实时 JSONL 读取与 journal 事件重放共用 validate_event_window，保留原 run/boot/time namespace、进程身份、窗口和 legacy host/pid 歧义检查。重放还拒绝窗口外事件、篡改早期边界的失败 snapshot、伪造 censor、无效固定32、乱序/越期完成或改变原 deadline。collect_attempts 现在一次读取每份原字节，同时返回 journal_files SHA，避免读取 JSON 与计算其哈希使用两个不同版本。

## 分母、输出和限度

summary 按四条件分别保留 preregistered、started、unstarted、incomplete、failed、timeout、evidence_complete_unqualified、已验证业务响应、未知业务观测、right_censored 和 failed_after_stage。准备失败与空的已抢占目录都计入 started；未闭合不会退回 unstarted。failed_after_stage 表示最后完成的阶段，不凭此推测实际失败函数。

所有派生记录 success=false、runtime_qualified=false、physical_cleanup_verified=false；formal_successes=0、formal_success_rate=null。结构证据齐备不会被提升为成功率或性能结论。记录器 fsync 的开销保留在 command/trigger 指标内，和 GLM 比较须披露。NFS 客户端缓存、服务端缓存、Store 本地介质差异仍不可忽略。

export_results 只写绝对路径的新目录，禁止放进任何执行根，存在即拒绝覆盖。生成前重新检查全部已收集 journal 原字节 SHA 和协议 SHA，manifest 标为 derived_not_execution 并记录执行根、每个输入和每个输出的 SHA、exporter 源码 SHA。调用方必须声明全部执行根、封闭数据集并禁止并发写入；本地 SHA 链不是 WORM 或签名系统，也不是驻留租约。中途导出失败保留部分目录，没有 manifest 的目录不能视为完整导出。

## CPU 验证与使用

新增 22 项用例覆盖完整扁平重建、准备/超时/未闭合分母、原有业务后超时、新旧缺 trace 记录、native parity 与业务正确性分离、指标/时钟/窗口/snapshot/censor/固定32/完成边界篡改、输入重放期间变化、独立目录和不覆盖、真实 CLI 调用及上批原 journal 兼容。所有新增请求、缓存、worker、释放时间均为合成。

CLI：在活动代码根使用现有 CPU Python，并将 sllm_store 加入 PYTHONPATH，执行 benchmarks/v4_results.py；必需参数 --preregistration、--prereg-sha256、一个或多个 --execution-root、--output。它只做读取与派生导出，不启动实验。当前留档样例仅重放上批 cpu_ 前缀 fixture，时间数值不得用作性能数据。首轮 255 项通过后补齐两个合法失败路径，最终统一 257/257 CPU 回归通过、无跳过。CLI 样例生成 12 份派生结果：1 个 unqualified、11 个 unstarted；输入原字节未变，manifest 及 14 个派生文件 SHA 已核对。最终日志、回执和样例位于 docs/evidence/v4-results-p3/。

后续仍需真实控制面执行器、独立资源/缓存/gold/清理准入、外层硬超时和失败后重新准入机制；未安装推理环境、完整构建 native 扩展或运行 GPU，正式编号未使用。
