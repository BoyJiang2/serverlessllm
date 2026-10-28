# Docker环境恢复与P1进展

## 19:53 检查：GPU空闲，镜像及引用容器被删除

八卡均0MiB、无GPU计算进程，主机MemAvailable约1983906639872字节。但原镜像和created引用容器均不再存在；7.602GB远端备份仍在。没有启动模型或重复导入。

Docker只读事件明确记录：19:49:59引用容器`20fc8b93...`发生destroy，19:50:01镜像`aa161e38...`发生delete。事件不包含调用者或操作命令，不能归因给某人或认定具体清理机制。原始事件及摘要见`evidence/docker-debug-cpu-20261028/image-removal-events-1950.jsonl`与`image-removal-summary-1953.json`。

这不推翻19:33的CPU生命周期成功，但当前镜像可用性已变化。引用容器不防止显式删除或system-prune，本次没有修改全局清理策略。新原生服务smoke的scope/完整代码及预算审阅、新run守护probe和监控仍未完成，因此不能因GPU刚空闲而直接开跑。先解决环境持久性及准入闭包，不无限重复导入镜像。

## 18:12 实际资源检查：服务启动被GPU占用阻断

原镜像仍在。八卡显存均约103259MiB，实际GPU计算进程存在（本次未归属为本实验），没有启动本实验模型或Store池，也没有停止其他任务。准确快照见`evidence/image-recovery-20261028/resources-20261028T1811.json`，内部UTC为10:12:30，以实际时间字段为准。

新增只读入口：`python -X utf8 tools/docker_status.py`。它仅通过固定SSH入口qjhs003读取镜像及GPU/内存元数据，报告BLOCKED或RESOURCE_SNAPSHOT_ONLY；即使资源空闲也始终`admission=false`。三项CPU测试覆盖占用、镜像缺失、卡身份不完整及空闲不等于准入。它不是共享锁，也不授予停止其他任务的权限。

### 下一步最小服务控制的修改闭包（尚未实现/批准）

新建非计时原生服务smoke身份，不消耗或改写原40ID。仅Store格式的一次注册、冻结业务响应/32-token验证、删除、60秒观察及完整释放；必须明确标注不等于完整G1或正式测量。复用既有原生服务、加载器、模型/Store和金标，不使用替代权重。

需修改并完整复审的闭包：新run manifest与用户授权记录、资源window解析、guard/observer新截止及实际probe、预算scope、host操作器阶段白名单、bootstrap单格式分支、validation生命周期选择、materialize/launch模板及包清单。旧C23 run/window/R1/R2/ready不能授权新进程。保留Store SIGINT 240秒及60秒观察，不用缩短释放换取启动。

若不新增smoke scope而直接复用完整G1，现有prepare阶段要求剩余5820秒，service阶段要求4080秒。当前只有“短时使用”的授权表述，不能把它机械解释为该长度的独占窗口；实际资源和完整最大预算不满足则不启动。新scope的预算需从完整初始化、真实校验及清理闭包明确推导后审阅，不在此先宣称已准入。

## 恢复成功：2026-10-28 17:42 +08:00

原镜像已通过本机校验下载、SCP/SFTP断点中继和远端Docker load恢复。实际image ID为`sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f`，与历史环境一致。SSH实际启动无GPU、runc、无网络、UID2147的有界容器，取得vLLM `0.19.1.dev1+g43a9b1afb.cu130`、PyTorch `2.10.0+cu130`、Transformers `5.4.0`、Triton `3.6.0`，验证容器已释放。

远端备份位于`/data/brick1/boyjiang/glm5.1-fp8/recovery/image-relay-20261028T091001Z/original-image.docker.tar`。该文件由官方已校验的config和压缩层组装为Docker-load格式，非docker save产物；Docker导入校验各层diffID并恢复原image ID。备份7602462720字节，SHA256为`15fc28ff7074bb1285fb80931245e6ed3671e943975060849d230fc3bd74c48b`。

本节仅确认Docker原环境和SSH CPU启动能力恢复。模型服务、独立新run守护、实际GPU业务和P1完整up/down尚未验证，不能复用旧gate。归档输入、实际启动、独立复核及本地清理收据另存小型证据；历史传输失败保留。下节为此前过程记录。

17:45独立复核远端归档SHA、原image ID及无CPU探针容器残留；17:46按用户要求删除本地blob目录和归档，共15204860541字节（约15.20 GB），仅保留小型JSON/log证据。远端备份保留。小型收据位于`evidence/image-recovery-20261028/local-relay/`，不包含镜像层或模型数据。

## 过程记录：新网络下载及断点续传

用户换网络后，官方镜像29个blob全部按大小和SHA256核验完成。Docker可导入归档7602462720字节，SHA256为`15fc28ff7074bb1285fb80931245e6ed3671e943975060849d230fc3bd74c48b`。本地归档及blob仅为临时恢复资产，用户要求远端验证成功后删除。

首次SCP在6278709248字节处Broken pipe，保留失败。用户再次明确授权继续后，远端partial全SHA与本地同长度prefix一致，正通过SFTP reput仅续传余量。宿主实际Python3.10，已修复远端hashlib.file_digest不兼容问题；这次预检失败未追加任何数据。旧失败不冒充当前失败。导入后仍需完整归档SHA、精确image ID、CPU容器启动及释放验证；目前不宣称恢复成功。独立只读审阅见`evidence/image-recovery-20261028/resume-review.json`。

## 先前两次远端拉取：传输超时

2026-10-28 16:58 +08:00：1ms源1800秒、DaoCloud源1200秒均达到预设截止，日志以`context canceled`结束。多数层已经下载/校验，但完整image ID仍不可inspect；CPU容器验证及镜像备份均未执行。失败日志摘要及SHA见`evidence/image-recovery-20261028/failure.json`。不延长旧截止或自动重复拉取，保留缓存层；恢复监控停用。需要新的可用大层传输方案后再继续。下文描述的是此前计划和已完成的元数据/源码整改，不代表环境恢复成功。

## 原镜像已找到可恢复来源

官方vLLM说明公开使用`vllm/vllm-openai:glm51-cu130`：https://docs.vllm.ai/projects/recipes/en/stable/GLM/GLM5.html 。此前“可能仅为本地标签”的猜测撤回。

本次通过官方Docker Hub和registry API读取小型元数据，并分别验证manifest和config原始字节SHA256：

- linux/amd64 manifest：`sha256:f17a0d64023227305acb47a26edebb52c43e683817ffcac4cca4e6bb7a83692a`。
- config/image ID：`sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f`，与原C23完全相同。
- 28层、压缩层合计7602365454字节。
- 仓库tag的多平台index digest不是本平台manifest digest，均不能混称为config digest。

公开manifest和解析记录保存在`evidence/image-recovery-20261028/`。没有记录registry bearer token。镜像层通过qjhs003已有网络可用的docker.1ms.run源按上述不可变manifest拉取；实际Docker完整内容校验及image ID仍需完成。没有把同名标签当作同一镜像。

## 恢复任务与边界

远端证据目录：`/data/brick1/boyjiang/glm5.1-fp8/recovery/image-20261028T075000Z`。

拉取有1800秒截止，日志`pull.log`。后续流程等待image ID可用，执行内部90秒、外层120秒的无GPU、runc、无网络、2CPU/2GiB版本元数据探针；成功后以600秒上限执行docker save到本实验目录，完成才更名并生成SHA256。任何失败不认定恢复完成。镜像层及备份不是性能计时输入；此时没有本实验正式测量运行。

开始前核验brick1约2.0TiB可用、根盘约926GiB可用、八卡0MiB、主机约1.8TiB可用，但仍有第三方CPU容器。未停止它们，未把GPU空闲当作新实验准入。

## 常驻参考源码已恢复

从C23不可变归档复制到`code/c23-reference-f176e05`，81个清单文件及manifest SHA核验通过，约844KiB；原归档保留。该树仅作常驻开发参考，不是已激活的`code/experiment`，不复用旧window、guard或gate。

## Docker配置重构候选

`tools/docker_plan.py`从单份显式JSON生成Docker argv，而不是依赖现存容器反推：

- 固定完整image ID、`--pull=never`、runc、无网络、显式资源上限和UID2147。
- 明确`--mount`，资产只读，可写源限制为该debug run目录。
- 只接受独立debug身份，统一标注`DEBUG-UNREVIEWED`，拒绝formal run名称及未知权限参数。
- `--check`仅检查实际UID、image ID及挂载元数据，不读权重内容，不create/start容器。

本地示例：`python -X utf8 tools/docker_plan.py docs/docker-debug-probe.example.json`。

该例仅生成计划，不是服务启动命令或正式准入。Linux `--check`需要在授权账户与有效环境执行。编译/预检源码获独立本地只读审阅PASS，七项本地CPU回归测试通过。正式入口拒绝调试栈、head网络物化、实际独立截止守护以及完整服务up/down仍待接入与验证，不声称P1全部完成。

原40个正式ID、历史失败、金标和运行分层均不变。镜像环境恢复也不自动恢复旧运行态证明。
