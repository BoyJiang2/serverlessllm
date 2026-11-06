# 第四次原生参考：设备编号表示不兼容

用户明确授权的第四次尝试 `native-reference-20261106T200833Z` 已通过实际Python入口、UID/缓存和vLLM导入，进入LLM构造的ModelConfig阶段。V4架构检查子进程在导入模型类时失败，权重尚未开始加载，无业务/fixed32输出或gold；容器退出1、非OOM。

根因有固定源码依据：vLLM提交132765e...的 `platforms/interface.py:230` 将 `CUDA_VISIBLE_DEVICES` 的条目直接 `int()`，CUDA平台以该值查询NVML物理设备。控制器把Torch可接受的GPU UUID用于该变量，因而模型架构识别报invalid literal。该结论限于当前固定版本/调用路径，不泛化到所有vLLM版本，也不归为V4量化、Store或原SLLM新增机制。

修正只改变容器内CUDA编号表示：本任务已限定整机八卡且宿主编号精确0..7，CUDA_VISIBLE_DEVICES改为0,1,2,3,4,5,6,7；Docker DeviceRequests及NVIDIA_VISIBLE_DEVICES继续使用相同八个UUID、相同顺序。候选入口在真实GPU前检中用vLLM自身的UUID查询确认该映射，再进入LLM；没有修改目标引擎、wheel、TP/EP、精度、revision、请求或seed/mp。

无GPU CPU容器已调用实际安装版本的CUDA平台映射函数，复现UUID失败，并验证八个整数逐一映射正确；安装的interface.py SHA与固定源码完全相等。该测试没有NVML能力查询、CUDA初始化或模型构造，不能当作完整架构识别或GPU内核通过。

失败后61点真实释放观察覆盖76.096秒，八卡每次0MiB且无compute进程，绑定容器及捕获的原进程身份、控制器和守护均消失。单容器/GPU观察通过，physical_cleanup_verified=false、runtime_qualified=false；原权重元数据不变。四次失败、单次尝试消耗与原正式ID保持，没有自动第五次启动。

原始嵌套错误、完整释放样本、执行时控制器、固定源码函数实测及候选修正见 [证据目录](evidence/v4-device-mask-failure)。后续完整权重加载、正确输出、Store和生命周期仍未验证。
