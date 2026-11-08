# 发给 GLM 任务

请从qjhs003:/data/brick1/boyjiang/glm5.1-fp8的既有成果，补齐SLLM三模型报告图5的统一加载口径，并核查图4全程边界。只读历史源码/phase/log/result，CPU整理；不启动GPU/模型/Store，不安装环境，不重读权重或Store payload，不补跑或替换ID。证据不足时明确缺失，另给最小新采集方案，不直接执行。

保留原40=35成功/5失败及各历史协议。主集仅C23 B5–B10，每条件6个完整块；C23全部26合格描述集为6/6/7/7，另列。v6/v7/v8/C19/C21等不得混进C23主图；原失败和release分类不改。

GLM已有weight_ingestion等phase事件，请直接查原始phases.json/事件生产源码，不因三模型报告现有汇总缺字段便判定历史没有数据。

图5共同主指标固定为：
weight_load_host_all_rank_span_s = (max(rank_end_ns)-min(rank_begin_ns))/1e9。
功能边界：每rank进入完整模型权重加载调用、fresh模型构造/权重storage分配前，至权重赋值/恢复及必要原生量化/布局后处理执行并返回。V4接点为gpu_worker.Worker.load_model，旧字段phase_weights_pipeline_including_allocation_span_s。GLM须检查C23实际引擎/patch的生产端，证明功能边界等价，不能只匹配字段名。

包含该调用内的分配、读取或Store/RPC等待、copy/IPC和权重后处理；不混设备/通信初始化、KV分配、profile/warmup/graph、路由与请求。若旧接点包含其他步骤，明确差异，禁止凭相减估值修成等价。只解释原同步，不新加CUDA同步；host跨度不是纯I/O或精确CUDA完成。

逐attempt核对完整TP8端点及PID/startticks/rank/模型实例、同boot/time namespace/CLOCK_MONOTONIC和顺序。输出各rank begin/end/duration、统一all_rank_span；max_rank_duration=max(end-begin)可另列但不替代主指标。每attempt先计算8rank跨度，再对固定主集取均值；缺rank/端点null并记有效n，不补0、不换ID。

weight_ingestion、quantization_postprocess等分项先审实际函数和工作范围，另作专项字段。注意V4旧load_weights只恢复remainder，专家copy在postprocess遍历中，所以不得仅凭同名旧字段与V4作纯摄入/纯量化对比，也不把重叠包络相加。

图4检查是否存在真实的准备起点到最终正常释放观察结束的完整端点链，列出环境/池初始化、目标Prep、startup、delete ack、Store退出、release结束。GLM正式批共享池，不能把批次初始化捏造成每条件单独全程或任意摊入；部分Prep包含前池卸载也要保留。不以Prep+A、文件mtime或退出轮询时间补造生命周期，不将失败回收改成正常成功。

交付独立目录：metric-definitions.md（生产源码SHA/行号与等价结论）、per-attempt.json/CSV（原ID/协议/资格/八rank原端点/统一值/缺失原因）、summary.json（C23主24条与描述26条分开、数组/n/均值/中位数）、evidence-manifest.json（原路径/SHA/事件选择器）。保留已有字段和原失败，不新增统计检验、不重新定义主集。

最终说明：统一图5可用哪些数据；图4是确实未记录还是现有汇总未提取；哪些只能留缺测。历史数据能补则补，无证据不得填柱。
