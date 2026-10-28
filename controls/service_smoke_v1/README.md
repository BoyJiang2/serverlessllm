# 原生服务单次smoke候选（未完成，不可部署）

当前实现范围/预算草案，以及方案B的纯Docker参数构造候选；尚未接入启动器、监控、宿主守护或业务验证。没有完整R1批准，不是新的运行gate。优先复用正式路径的expected_spec/container_mounts，不再另造完整配置来源，也不从docker inspect反推配置。

计划复用现有原生ServerlessLLM栈，只做一次Store格式真实生命周期，保持官方FP8、TP8、八rank权重/scale审计、原金标、自然EOS与独立固定32-token验证。删除后60秒观察保留。原40个ID不消费、不重试，原runtime/active不修改。

候选最大预算39分钟：初始化/真实加载/业务1800秒，实例删除后观察60秒，Store SIGINT宽限240秒，整个部署释放120秒，证据120秒。它是待完整审阅的上限，不是预测耗时，也不是新授权。模块只接受外部实际授权截止并拒绝不足窗口；返回runtime_admission=false，不自动获得GPU或独占资源。

后续必须接齐：新身份与授权快照、实际资源核验、独立宿主守护及probe、连续监控、原生head/worker/Store/路由启动、三项业务与全rank审计、finally清理、释放取证和commit/manifest完整独立审阅。镜像恢复在这些准备完成后按已校验远端备份进行，不反复创建被清理的anchor。

## 方案B参数构造的回归基线

`docker_args.py`仅构造参数，不执行Docker，不授予准入，调用者必须保留原路径/文件元数据、guard和preflight检查。当前还没有让正式launcher调用它，原runtime/active保持原字节。

回归测试校验C23归档收据所绑定的旧launcher/workspace/manifest/initial-ledger及所依赖formal_gate/attempt_state源码SHA，读取实际保留的headformal13、formal13、headservice13a和service13a dispatch spec并检查各SHA。使用旧launcher的原AST参数块、旧挂载生成函数重建基线，再与纯函数生成的argv序列JSON字节比较；四个实际spec均一致，额外合成monitor/export分支也一致。

限制：这不是直接保存下来的历史Docker CLI argv录制；远端路径存在性在本地测试用元数据替身，不声称本机路径真实存在。完整expected_spec调试覆盖白名单、workspace调试路径及实际Docker状态对比仍待实现/验证，不能以这两个CPU测试宣称整体行为等价或服务已就绪。
