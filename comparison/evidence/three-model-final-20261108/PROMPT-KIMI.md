# 发给 Kimi 任务

请为SLLM三模型报告补齐Kimi图5的统一口径，并核查图4全程数据。先只读既有源码/日志/phase及CPU整理；不启动GPU、模型或Store，不安装环境、不重读权重、不补跑/替换ID。若历史记录不足，明确缺失，另给最小采集方案，不直接执行新实验。

参考已发布Kimi报告：1213421633a6e63fa6afdfc960207560c7d3acf1，kimi/evidence/final-report-20261107-v2/REPORT.md。原40=38合格/2失败，主集B1/2/3/5/6/7/8/10共8完整block；额外补跑和早期历史分开。原失败/NOT_L1/所有者接受等身份不改。

图5共同主指标固定为：
weight_load_host_all_rank_span_s = (max(rank_end_ns)-min(rank_begin_ns))/1e9。
功能边界：每rank进入完整模型权重加载调用、fresh模型构造/权重storage分配前，至权重赋值/恢复及必要原生量化/布局后处理执行并返回。V4实际接点为gpu_worker.Worker.load_model；Kimi须检查实际vLLM0.19.1和f7补丁，证明对应功能边界，不能只用同名函数认定相同。

主图应包含该调用内的分配、读取/Store等待、copy/IPC及权重后处理；不混设备/通信初始化、KV分配、profile/warmup/graph、路由和请求。夹带LoRA/drafter/aux等则记录其是否实际启用、是否造成边界不等价；不能从宽日志总数减去估值。保持原同步，不为补证新增同步；指标只是host包络，不称纯I/O或精确CUDA完成。

重点核对现有f7日志：其getloader前至loadmodel/可选附加步骤后的单值，是否有足够原始PID/rank/实例和端点证明与上述共同边界一致。没有则仅保留f7专项日志值，不直接重命名、不推成TP8跨度。优先寻找已有八rank原事件。

逐attempt输出8rank begin/end/duration和主指标；另列max_rank_duration_s=max(end-begin)，不得替代主指标。校验同boot/time namespace/monotonic、PID/startticks/rank/模型实例、唯一开始结束与顺序。每attempt先算8rank跨度，再按原主集每条件取均值，rank不是独立样本。缺rank/端点保留null和原因、有效n，不能补零或换成功ID补齐。

同时核查图4：原controller elapsed的真实起止和包含项目。分开环境/池初始化、目标Prep、startup、delete、正常退出、最终至少60秒释放；说明哪些每条件独立、哪些批次共享。没有完整端点不能用Prep+A代替全程，不用mtime补时间，不把强杀清理称正常成功。

交付独立目录：metric-definitions.md（实际源码SHA/行号及与V4等价结论）、per-attempt.json/CSV（原ID/资格/版本/原端点/统一指标/缺失原因）、summary.json（原8完整块和全部38合格描述集分开、数组/n/均值/中位数）、evidence-manifest.json（每项数值对应原路径/SHA/事件或日志行）。保留旧f7值和原数组，不新增检验族。

最终明确：哪些数据已可进入统一主图、哪些只有专项观测、哪些必须新采集。不要为了画齐柱子声称历史上没有记录的八rank证据已经存在。
