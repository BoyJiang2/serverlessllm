# Docker环境恢复与P1进展

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
