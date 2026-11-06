# 第三次原生启动：GPU 前检通过，运行用户配置失败

独立尝试 `native-reference-20261106T190522Z` 已首次通过真实 GPU 前检：八张 H20-3e 可见，能力均为 SM90，Torch 已初始化 CUDA，实际加载宿主 `libcuda.so.580.95.05`，未使用 CPU compat/stubs；实际205包安装图与锁一致。这是设备身份、驱动库加载及安装图证明，不是八rank计算、通信、完整模型或 Store 验收。

随后 vLLM 导入触发 Torch Dynamo 缓存目录初始化，getpass 因容器没有 UID2147 的账户及用户名环境而失败：`KeyError: getpwuid(): uid not found: 2147`。容器正常终止为代码1、非OOM、守护记录 container_exit；模型尚未构造，无原生请求输出或gold。控制器原有 natural_process_exit 字段按guard返回0判定，不能用该布尔值替代这里的实际退出状态。

本次61点真实释放观察覆盖75.868秒，八卡每次0MiB、无compute进程，绑定容器及捕获的原worker身份均消失，原控制器与守护也消失。报告本次单容器/GPU观察通过，physical_cleanup_verified=false、runtime_qualified=false；未覆盖后续Store/IPC或完整服务生命周期。权重前后元数据相同，未重新完整读取权重SHA。

已按实际运行条件完成CPU修正验证：同一不可变镜像、UID/GID2147、只读根目录和可写缓存，先复现原vLLM导入错误，再以容器专用的passwd/group只读文件补齐boyjiang身份，并显式设置USER/LOGNAME及Torch缓存路径；父进程与一个mp spawn子进程均实际导入vLLM成功，CUDA未初始化、没有模型。仅为无设备CPU导入显式使用官方compat库，不能当作新的GPU导入或内核证明。

候选控制器采用同一已验证用户/缓存配置，账户文件来自固定源码证据目录，逐次复制并记录SHA；不改宿主账户，不重建镜像或重装依赖。末态增加明确的failed_native_process或candidate_pending_review，仍不能自动授予运行PASS。三次启动、失败及正式协议均保留，没有自动第四次GPU启动。

原始GPU前检、完整错误、释放样本、原/修正控制器及CPU父子进程证明见 [证据目录](evidence/v4-runtime-user-failure)。本批归为运行封装与记录修正，固定vLLM、SLLM/Store机制、量化精度、revision、请求和seed/mp不变。下一次仍须沿用已批准的失败停止与单次范围。
