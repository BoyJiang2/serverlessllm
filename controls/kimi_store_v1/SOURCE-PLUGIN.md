# 源模型转换worker的独立插件候选

本轮只做本机CPU代码、小测试及临时distribution metadata，不安装到服务器/全局Python、不创建模型/Store、不初始化GPU。不修改GLM运行包、正式结果或native注册。源SF转换不依赖native双份预算，但其完整worker、实际峰值和守护仍未准入。

## 配置与进程传播

`source_plugin.register` 是独立general-plugin回调。未设置SLLM_KIMI_SOURCE_SPEC和SLLM_KIMI_SOURCE_SPEC_SHA256时不导入Torch/vLLM、不注册；只设置一个则拒绝。启用时读取绝对路径下最多64KiB的spec，先核外部SHA，再验证固定schema/node/revision/mode、run_id/holder/code_commit格式、明确字节块上限及路径。checkpoint必须与run_root互不包含；spec和checkpoint contract必须位于run_root，output_root是run_root专属子目录。官方config与1428目标contract继续使用既有冻结校验。

spec.schema=kimi-source-worker-plugin-v1；mode仅export；node固定qjhs003；run_id格式kimi-export-YYYYMMDDTHHMMSSZ-8hex，holder为32hex，code_commit为40hex。配置只声明身份，**不证明实际SSH节点/boot、运行代码SHA、所有者授权、资源或有效窗口**；完整worker gate必须另核。这里不把配置中的commit字符串当作已验证完整code manifest。

实际runtime要求vLLM0.19.1、Torch2.10.0+cu129、torch.version.cuda=12.9，不沿用GLM cu130镜像。插件调用已有source factory和源码绑定registry，仅登记sllm_kimi_export_v1，默认SF和native不改；rank_context不由spec传入，仍由已初始化的实际TP/PP/CUDA组读取。注册不会创建engine、初始化通信组或加载权重。factory仍会独立核验完整官方index及本地分片metadata。

同进程重复回调仅在同一spec哈希、同一registry及同一loader class仍持有该名称时允许；其他配置/foreign替换拒绝。每次仍重查config/contract，不从cache追认被改动的文件。不同进程各自注册，不能只在parent执行一次就声称worker已继承。

## distribution metadata

`materialize_plugin_metadata(directory)`仅在调用方指定且已存在的私有目录中新增serverlessllm_kimi_source_candidate-0.1.0.dist-info，使用独占mkdir和文件x模式；不覆盖已有目录，不全局pip install，部分失败由调用者留证。entry point为vllm.general_plugins组的sllm_kimi_source_candidate=source_plugin:register。

后续受守护容器需把完整受审候选源码和此metadata放入同一受控PYTHONPATH，并设置VLLM_PLUGINS=sllm_kimi_source_candidate及上述两个spec环境变量，确认它们实际传到engine/worker。此处只是接口约定，不是可直接执行的启动命令；尚未生成窗口/guard/容器/完整code manifest或安装metadata。挂载权重只读，输出归本run，worker负责原子run登记和精确清理。

## 已验证与未验证

新增9项CPU测试，总计220项通过。使用固定registry函数code核验真实注册/同类幂等/foreign冲突及SF保留；source factory和CUDA runtime边界为明确mock，没有调用完整模型factory成功路径。坏哈希/身份/路径/字节界限在runtime导入前拒绝；缓存后改config仍拒绝；实际CPU Torch不能通过CUDA ABI要求。

一个新子进程使用标准importlib.metadata从临时dist-info发现并加载回调，未配置时返回None且未导入Torch；另一个新进程从继承环境读取相同spec/holder/块上限。**没有运行vLLM本身的general-plugin加载器或真正TP8子进程**，不把这两项小进程测试冒充实际worker传播、通信组、八rank转换或原生服务验证。

下一项：完整source worker的代码与配置manifest、实际镜像/ABI、模型与导出总预算、独立窗口/guard/probe/监控、受控退出取证与八rank结果收集。实际GPU运行仍需单独完整审阅和资源核验；沿用RESERVATION-PLAN.md，避免占着预约等待审批。native补丁构建和完整峰值预算仍单独处理，保守停止不变。

初审确认stat/read_bytes之间存在文件增长竞态；新增小回归在旧实现失败，新实现打开流后只请求65,537字节，超过64KiB即拒绝，再核SHA/解析。source-plugin-tests.json保留初审源码SHA、可重建原实现的精确片段、失败及最终测试记录；不以stat作为硬读取界限。
