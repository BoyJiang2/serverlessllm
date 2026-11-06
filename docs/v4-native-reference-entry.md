# 单次原生参考入口与释放采集

本批仅准备入口、CPU 验证和环境快照，未授权或运行 GPU/完整模型。复用固定 vLLM wheel、v4pro.4 及 205 包安装图；原空闲构建容器只有 sleep 进程，已保存为不可变镜像 `sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d`，约 21.11 GB。原容器没有重启，基础镜像层不变，数据 bind mount 未进入镜像；没有重装、重编译或重新验算 805 GiB 权重。

`benchmarks/v4_native_reference.py` 默认仅输出 CPU 计划。qjhs2 实际计划核对了真实模型路径的 config SHA、64 分片存在性及总大小 864721029744 字节，拒绝 config-only 标记；这是元数据检查，不是新 SHA 证明或写者租约。请求 JSON、30/43 个输入 token 的 SHA 绑定原证据。执行模式使用原生 vLLM LLM、禁用插件、SafeTensors、固定 revision、BF16/原生混合量化、TP8/EPoff、2048/eager/prefixoff、FP8 KV、seed=20261105/mp。先记录引擎实际有效配置，再请求业务自然 EOS；仅业务成功才运行 fixed32。完整返回对象快照先落盘，再投影和验证，失败留存且不得覆盖输出目录。正常 shutdown 后只发布待审核候选，native parity 为未知，不用参考输出和自身比对来制造 PASS。

`benchmarks/v4_native_release.py` 在宿主收集精确容器 ID/Created/Image、绑定的原 cgroup、原 worker PID/start_ticks、选定八卡显存和 compute process。基线、worker/cgroup 和 boot/time namespace 必须来自本次启动前及退出前记录；不能由历史值填充。沿用 GLM 的 61 点/至少 60 秒、最大 5 秒采样间隔、基线+16 MiB显存界限，保留每次原始查询；命令失败、N/A、缺卡、残留进程或时限不足都失败。范围仅是单次 native 容器与选定 GPU 的释放观察，不替代后续 SLLM Store/IPC/两轮服务生命周期验收。

12 项 CPU 契约通过，覆盖配置漂移、失败原值先保存、fixed32 跳过、shutdown 失败、不生成自我 parity、资源缺测和残留以及虚拟时钟下的采样覆盖。这里的推理输出/GPU数据/60秒时钟均为明确替身，不声称实际模型或物理释放通过。实际模型目录元数据计划及环境快照单列为真实证据，见 [证据目录](evidence/v4-native-entry-cpu)。

下一步拟申请的范围仅为 **一次资源准入和单次完整 SafeTensors 原生参考**，不做 Store 转换、服务或正式四条件，不动旧 12 个 ID。拟用 qjhs2 的 8×H20、TP8，CPU上限8核、内存上限1 TiB；实际 GPU容量、可用内存、干扰、预约和驱动/运行镜像必须先过准入，不满足即停止，不自动换卡或降低精度。软件缺失不得临时联网换依赖。

可审阅执行顺序：

1. 完成明确授权与预约/独占准入，固定八个 UUID、基线和本次运行编号；占用 GPU 前告知。核对模型写者排除、既有 SHA 对应的文件身份与固定软件镜像。当前尚无这些准入证据。
2. 从上述不可变镜像 **create 而非 run**：指定已准入八卡、无网络、禁重启/AutoRemove、cap-drop ALL/no-new-privileges、私有 IPC、只读代码和真实模型 bind、仅本次输出/缓存可写。继承快照的 CUDA_VISIBLE_DEVICES 空值/NVIDIA_VISIBLE_DEVICES=void 必须显式替换为获准 UUID，GPU 库使用实际宿主 driver 路径，不能复用 CPU compat 导入路径充当验证。
3. 从准备开始记录原期限1200秒；以完整 CID 启动独立 session 的 `v4_container_guard.py`，模型容器期限设为准备起1080秒。核对 armed 身份、PID/start_ticks及守护存活后才 start。只读挂载 armed.json；入口使用 `--execute --guard /guard/armed.json --output /output/native`。该标志本身不代表授权。
4. 宿主持续保留资源及所有 worker/cgroup 身份。原生入口写出候选、shutdown、进程退出后，按同一时钟建立释放 job：identity、baseline_gpu_mib、cgroup、worker_processes、boot_id、time_namespace、preparation_begin_ns、delete_ack_ns、deadline_ns。执行 `v4_native_release.py JOB NEW_OUTPUT`，61点不得缩短；容器 stopped 不是释放 PASS。
5. 若业务/EOS、fixed32、实际配置、正常退出、释放或时限任一失败，保留原输出/错误/owner，立即停止，不自动重试。容器由守护回收，状态不明不得重新准入。仅全证据审核后才固定首份原生输出 gold；缓存未作四条件准入，不能把本次耗时当正式冷/热数据。
