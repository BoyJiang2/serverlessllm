# 图5统一口径：v2

修订原因：v1把Kimi缺rank身份的f7日志与V4八rank三个函数包络并排。它们不等价，不能作为统一加载阶段对比。V4的旧字段名weight_ingestion/quantization_postprocess也不能直接解释为纯摄入/纯量化。

## 主图只用一个指标

`weight_load_host_all_rank_span_s = (max(rank_end_ns) - min(rank_begin_ns)) / 1e9`

共同功能边界：每rank进入完整权重加载调用，在fresh模型构造/权重storage分配前开始；全部权重被赋值/恢复、必要原生量化和布局后处理执行且该加载调用返回时结束。V4当前接点为`gpu_worker.Worker.load_model`，已有原字段`phase_weights_pipeline_including_allocation_span_s`。其他版本须按实际源码核对功能边界，不能只匹配函数名。

包含该函数内实际发生的构造/分配、文件读取或Store/RPC等待、copy/IPC确认及原生权重处理。设备/通信初始化、KV分配、profile/warmup/graph、路由和请求不应混进共同窗口；若某版本夹带额外功能，需披露并判定是否可比，不自行从总耗时减去猜测值。

这是主机调用包络，不新增CUDA同步，不称精确设备完成时间或纯I/O。仅对原始证据中已存在的同步作说明。必须TP8齐全、同实例/请求窗口、PID/startticks/rank和同boot/time namespace/CLOCK_MONOTONIC可核验。`max(end-begin)`可另列，但不是主图统计量。

Kimi仍按原8个完整block，GLM仍按C23原6个完整block；V4展示用B2–B10的9个同源完整block。正式分母、资格和检验族不变。每条件先逐attempt计算8rank跨度，再在固定样本集上取均值；不把8rank当8个独立样本。任一缺端点/rank则该统一指标缺失，标明有效n，不用另一个成功ID补齐。

## V4专项字段的更正

`v4_trace.py`旧weight_ingestion接在`V4GroupedStoreLoader.load_weights`。实际该调用通过session.begin恢复remainder并安装专家hook；61个专家层的load_group在后续原生后处理遍历中发生。因此旧12秒/3秒等load_weights跨度不是全模型权重摄入。

旧quantization_postprocess接在base_loader.process_weights_after_loading；这个外层调用会触发专家组恢复、原生后处理、同步及owner检查等，不能称纯量化成本。现有`grouped_store_copy`和`grouped_expert_postprocess_host`原事件可以另做V4专项分析，但copy包络可能包含RPC/等待，后处理包络也非纯设备kernel时间。它们不进入三模型共同主图，不以总跨度相减得到纯开销。

旧字段、原事件、正式统计JSON和失败记录不改；更正展示标签及解释即可。Kimi旧f7保留专项观测，GLM既有phase继续保留。主图两者目前为“待共同边界/完整rank核验”，不再称“GLM没有加载数据”。
