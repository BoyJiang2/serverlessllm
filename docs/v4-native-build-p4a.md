# P4A 构建与无GPU验收结果

固定vLLM0.20.1/132765e...真实源码构建成功，wheel为179431059字节，SHA256 f978d9bf907eebca3c682574b0e8461edd9133c7360614fb94a91c00d5351cf0。构建耗时7414.12秒，属于一次性CPU构建成本，不是模型启动指标。

最终使用该wheel及既有Store/服务v4pro.2 wheel离线闭合205包，外部包版本保持原冻结值；实际同步及pip check通过，安装图与最终锁相等。最终pylock SHA 962d317dfadf10a152df5bf8c9132f8bf69491cfea8279c2e699bf25ab91df40。权重未重读，环境未重建，既有Store构建/序列化/源码证据直接复用。

新进程隐藏两构建源码树后，九个实际vLLM原生扩展、V4模型模块、SLLM backend及四个loader注册通过；所有源码树已恢复。ELF14文件无未解析依赖，包含SM90/SM90a。实际Torch2.11.0+cu130、CXX11 ABI=true。完整目标tokenizer业务/固定长度输入为30/43，逐ID等于冻结值，非模型输出gold。

默认无设备容器的搜索路径缺少libcuda.so.1，第一次导入失败原文已保留。仅为CPU检查显式指定固定CUDA13镜像自带真实compat库后通过；不使用stubs或GPU设备。因此验收结论以该加载路径为条件，不能替代未来真实宿主driver/H20执行验证。CUDA未初始化，没有构造模型或引擎，没有运行GPU/服务或正式ID。

当前P4A wheel、依赖、安装、ELF/CPU导入与tokenizer项目通过，整体迁移未完成。运行前仍须闭合revision在注册/backend/ModelConfig/loader中的传递，以及显式FP8 KV与实际解析格式；旧协议保留，需确认的运行配置修订只提交最小决策。事件完整顺序和失败原响应留存放到下一安全修改批次。失败owner隔离不当作清理完成。

下一次拟在既有授权/资源准入完成并事先告知后，执行单次有界完整SafeTensors参考：8×H20、TP8/EPoff、BF16激活、原生混合量化、2048/eager/prefixoff，使用原冻结业务及fixed32请求，业务先自然EOS正确完成后才执行fixed32。先闭合1200秒外层硬超时、守护回收、正常退出及至少60秒释放观察，失败清理不确定则停止重新准入。此次不做Store转换或正式四条件，不消耗/重置旧12个ID。

原SLLM系统机制、引擎接口迁移、V4分组/量化增量和本批打包记录保持分开归因。本批不把Kimi/GLM继承修复或分组格式称为V4新增原论文机制。原始回执、失败与最终结果见docs/evidence/v4-native-build-p4a；大wheel/缓存留/data，代码和证据/home。构建与验证进程已终止，容器仅保留sleep用于下一CPU批次，临时17891下载转发已关闭。
