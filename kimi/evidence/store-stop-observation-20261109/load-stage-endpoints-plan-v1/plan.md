# 分阶段加载端点最小方案（仅源码审计）

完整边界继续采用现f919 runner：调用所选loader.load_model前至返回后，先于LoRA/drafter/aux；不新增CUDA同步。

| 路径 | 权重摄入 | 必要后处理 |
|---|---|---|
| SF / BaseModelLoader | line64 self.load_weights调用前后 | line78 online-quant条件判定前，至line81 process_weights_after_loading返回后 |
| Store / SllmLoader | line123 load_dict前，至line130所有参数绑定及buffer迁移完成 | line132 runtime-hook遍历前，至line139 attention处理返回后 |

SF加载中可能发生在线量化；摄入不是纯I/O。SF构造/检查、allocator peak日志、末尾model.eval及Store构造/释放旧storage/设备准备等都在完整区间内，但不归这两个inner段。不要用相减将其命名为构造/摄入/后处理。各rank阶段可能错开，跨rank极值区间不可相加。

最小实现为现canonical patch与现loader-interface测试两个tracked文件，生成新版本runner/base-loader/SLLM-loader小overlay。复用现rank原子文件、身份和完整端点；loader实例上仅opt-in stage字典，不新建模块/写入器/框架。消费者仅改现preparer与现测试的新supplement分支。旧f7/f919/v6和历史spec不动。

旧数据只有完整8端点，摄入/后处理仍null。本次完整块n=0：SFhot合格，Storecold退出60失败，后两项未启动；失败观测不进入完整块。新正式块必须合并新stage overlay并独审、冻结、资源准入，不能把停服诊断当正式分段数据。

完整源路径、SHA、行号、来源限制、错误/null规则、CPU用例与冻结表见plan.json。此处未修改源码或启动任何GPU/Store/容器。
