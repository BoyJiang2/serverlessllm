# Llama-3.1-8B-Instruct README 基准复现

状态：random 与 cached 均完成。qjhs002 / GPU7 / NVIDIA H20-3e；原始权重只读，正式执行结束后完成13次、连续超过60秒释放观察，显存均0 MiB、容器PID0、scope已移除。

| 场景 | 格式 | 次数 | 平均秒 | 最小秒 | 最大秒 | 总体标准差秒 |
|---|---|---:|---:|---:|---:|---:|
| random | safetensors | 30 | 11.885181 | 8.189584 | 12.862525 | 0.980037 |
| random | sllm | 30 | 2.378430 | 2.337431 | 2.426435 | 0.024670 |
| cached | safetensors | 5 | 3.221306 | 3.103249 | 3.555353 | 0.168625 |
| cached | sllm | 5 | 0.520336 | 0.516096 | 0.525862 | 0.003289 |

Random 加速 4.9971x；Cached 加速 6.1908x。加速比为SafeTensors平均加载时间/SLLM平均加载时间。

官方流程：random按torch.randperm随机顺序各加载30份独立副本；cached先做一次不计时预热，再测同一副本5次。每次加载后按官方脚本生成50个新token并清理模型。加载计时为原版benchmark_utils.measure的API墙钟区间；转换、模块导入、CUDA/推理预热、Store启动和后续生成不计入加载时间，计时终点未额外加入CUDA同步。随机顺序保留在原始JSON中，不把本实验称作cold-cache测试。

实现基线为官方9f50241baa5386e06a9321c51f19a9ef5f964c2b的benchmarks脚本，使用已有同提交C++ SDK。test_loading.py、measure函数和官方run-benchmark.sh的字节保持；未修改官方checkout跟踪文件，也未修改共享SDK。

本任务的必要适配：

- 下载入口通过环境变量读取已经下载的本地Llama权重；不重复下载。未计时的推理预热也使用本地Llama，替代原脚本硬编码OPT-6.7B。
- 镜像原本是CPU构建镜像，启动时显式设置CUDA_VISIBLE_DEVICES=0与NVIDIA_VISIBLE_DEVICES=GPU7 UUID。
- SDK没有安装console script，使用3行薄入口直接调用官方sllm_store.cli.main；不新建环境或安装依赖。
- Transformers 5.5.1已移除_get_no_split_modules；仅本任务容器只读挂载device_map_utils_compat.py，缺少旧方法时读取新版已汇总的_no_split_modules。保留device_map=auto。CPU/meta测试通过，实际GPU日志返回auto映射为根模块0。C++和其他SDK文件未改。此项意味着软件环境包含兼容修正，并非发布环境逐版本复现。

资源：一个H20-3e，GPU UUID GPU-21336138-6e64-ec08-305b-26c99d661ab9；8个容器CPU、128 GiB容器内存限额、32GB Store池、4个I/O线程、16MB chunk、FP16、单GPU。主线程获准共享NVMe，因此不是隔离存储测量。原README数据来自H100与29.4GB/s NVMe；未在本次机器重测fio或宣称硬件等效。

实际运行版本：{"torch": "2.10.0+cu129", "cuda": "12.9", "transformers": "5.5.1", "accelerate": "1.12.0", "peft": "0.18.0"}

失败与恢复均保留：首个GPU小探针因CPU镜像的可见设备变量失败；修正变量后探针退出0并完成释放观察。第一轮random完成SafeTensors30次后因缺CLI入口停止；补CLI后只补SLLM，没有重跑SF。下一轮已转换30份SLLM后因TF5私有接口缺失停止；CPU/meta兼容检查通过后复用这30份数据，只补测SLLM。原SF结果SHA验证始终一致；没有挑选或重做SF计时。最终恢复容器退出0，再执行cached两组各5次。

核验范围：random各30个不同ID恰为0..29，cached各5个ID均0；所有加载时间为有限正值，所有生成文本非空。只验证推理完成，不宣称生成准确率或两格式输出逐字相同；未额外执行全参数字节等价测试。

所有临时权重副本已由专属storage目录清理，原始checkpoint保留。完整运行日志保留服务器；本地及Git仅保存脚本、配置、原始小JSON、SHA和本报告，不含权重、Store或环境。

服务器证据目录：/data/brick1/boyjiang/ServerlessLLM/models/.llama31-readme-random-20261104

官方说明：https://github.com/ServerlessLLM/ServerlessLLM/blob/main/README.md
官方基准：https://github.com/ServerlessLLM/ServerlessLLM/tree/main/benchmarks

原始结果见results/random与results/cached；配置/容器状态见registration、resume1-registration、resume2-registration、final-audit；统计与随机顺序见verified-summary.json；最终释放观察见final-release.jsonl。
