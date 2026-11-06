# V4-Pro 完整权重内容校验

2026-11-06；执行时代码父提交 f0701e0f86834906daaa3c38507a557a0f7276d8。本批用户已明确将 CPU 串行完整 SHA-256 校验纳入范围；仍未安装推理环境、运行模型/GPU、转换 Store 或使用正式实验编号。

## 实际结果

| 项目 | 新鲜证据 |
|---|---|
| 原位模型 | qjhs2:/mnt/raid/models/DeepSeek-V4-Pro；SSH 别名 qjhs002 |
| 固定 revision | b5968e9190ef611bbf34a7229255be88a0e937c1 |
| 完整权重 | **64/64 整文件 SHA-256 匹配**官方固定 revision 的 LFS SHA；包括 safetensors 长度、JSON header 和全部 tensor 数据区 |
| 总字节 | 864721029744，约 805.334 GiB |
| 工具与并发 | GNU coreutils sha256sum 8.32；单线程，nice 10、ionice idle；实际可执行文件 SHA、进程启动 ticks 和只读文件描述符已归档 |
| 时间 | 08:51:45.676884Z 启动，09:41:09.179157Z 终态；任务包络 2963.512 秒，约 49.39 分钟 |
| 原始结果 | sha256sum.stdout.log 正好64条唯一 OK、stderr 空、退出码0；64行输入清单与官方JSON/normalized清单绑定 |
| 文件状态 | 64份文件的realpath/dev/inode/size/mtime_ns/ctime_ns/mode在读取前、读取后及独立审计时一致；没有额外root safetensors |
| 小型运行输入 | 完整权重后新鲜读取27份非权重文件，总请求18831953字节；既定15小资产+index共16个运行输入全部匹配 |
| 其余目录差异 | 27个官方非权重文件中26匹配；.gitattributes仍不同。未修改任何模型文件 |

这次将“权重尺寸/header相符、完整内容未验证”的历史状态提升为 **WEIGHT_FILES_CONTENT_MATCHES_PINNED_REVISION**。范围仅是64权重与单列的小型输入；不把ModelScope master标记升级为完整HF checkout证明。非权重文件按官方Git blob内容或LFS SHA核对，运行输入的SHA-256逐项保存在post-weight-assets.json。

时间来自监控任务单调时钟，包含读取前元数据、周期状态检查和最多约10秒终态检测粒度，不是纯hash CPU耗时、底层NFS吞吐或任何模型启动指标。864721029744是64个完整文件的逻辑字节数，不是NFS服务端物理读取量。此次完整读取会改变客户端/服务端缓存；不清缓存，也不将它当作正式hot/cold准入。正式批次必须在之后重新准备并采集完整缓存证据，bootstrap成本单列。

## 原始监控问题及独立终态审计

实际运行脚本在try内sys.exit，被外层BaseException误捕获，因此成功终态后额外留下failure.json（SystemExit:0，原始字节保留）。progress.json是最后运行中快照，停在63项，不是终态来源。没有为这些记账问题重启、重算或替换日志。

terminal-audit.json独立检查了固定manifest/官方metadata/实际运行脚本SHA、精确64项OK覆盖、空stderr、coreutils退出码0、所有结果行、前后/当前文件状态，以及原PID已消失（运行期间也逐次校验boot ID/start ticks）。因此明确区分“正常退出的wrapper误记”和真实checksum失败。实际64文件通过由原始coreutils结果和上述独立审计共同证明，不能仅凭有result文件或只看progress/failure文件名判定。

未来版本verify_v4_whole_weights_v2.py把sys.exit移到错误处理之外，已做语法/AST检查，**未用于本次805GiB校验**。实际执行版本、其hash、原始failure和实现审计全部归档。独立日志覆盖检查的4项小测试通过（正确覆盖、缺项、重复、失败/额外项）；它们不冒充新的完整292项CPU套件。29份生产运行源码仍与上一批292项源清单一致，本批不改生产源码，也未重复跑全套。

## 证据与剩余门槛

原始服务器任务：/home/boyjiang/sllm-v4-pro/evidence/full-weight-sha-20261106T085145Z。仓库归档：docs/evidence/v4-full-weight-sha-p3/；artifact-manifest.json逐文件记录原始字节SHA，Git属性关闭自动换行转换。核心回执为terminal-audit.json、result.json、before/after-files.json、post-weight-assets.json及64条原始stdout。

这是实际文件内容证据，不是未来加载期间的写者排除租约。后续加载/转换仍须保持输入封闭、复核身份、控制worker/Store epoch及缓存准备；不能因为stat稳定就宣称任意并发修改均可检测。主代码/脚本/证据继续在/home，权重不搬运，Store和GLM参考继续保留既定/data位置。

完整权重内容门槛已闭合；目标native环境/ABI/内核、原生业务与fixed32输出gold、全量Store转换恢复/逐rank后处理与容量峰值、原生SLLM服务、卸载再拉起及至少60秒物理释放仍未获得运行证明。没有用本次hash通过替代这些验收，也没有扩展此前“暂不安装推理环境或运行GPU”的授权。
