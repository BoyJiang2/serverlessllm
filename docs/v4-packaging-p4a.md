# P4A：V4 专用打包配置和构建前依赖图

2026-11-06；父提交f3fd0593973879e6a9d744bf7aeb97d539f30472。用户已批准隔离环境构建与无GPU验证，未批准GPU或模型执行。本批提交的是打包代码、CPU回归和构建前候选依赖图；不宣称真实扩展、原生模型或完整P4A已验收。

## 最小代码改动

两份setup.py增加显式SLLM_BUILD_PROFILE开关，仅接受legacy（默认）或v4-pro，错误值直接失败。V4生成独立版本0.8.0+v4pro.1的服务/Store wheel；服务元数据绑定同版本Store，worker及Store vllm extra固定0.20.1。新requirements-v4.txt/requirements-worker-v4.txt及Store的V4 runtime/build输入只在该配置下选用。原四份legacy依赖文件逐字节未改，默认版本0.8.0及旧依赖行为通过CPU元数据测试。

V4修正Torch2.11、OpenAI>=2、Pydantic>=2.12（移除旧core独立pin），并固定Transformers5.6。真实依赖求解进一步证明旧Click8.1.7与HF hub1.33不兼容、旧FastAPI0.115.12与目标vLLM服务依赖的Starlette>=0.49.1不兼容；仅在V4配置改为Click>=8.4.2、FastAPI0.121.3。官方候选元数据允许Starlette<0.51，最终解0.50.0；原Uvicorn0.23.1继续可解。没有通过删依赖、忽略metadata或跳pip check来掩盖矛盾。

MANIFEST.in包含新构建输入；V4源代码构建必须始终设置SLLM_BUILD_PROFILE=v4-pro，不把默认legacy构建当作V4。交付目标是带明确版本和METADATA的wheel，未声明V4 sdist可以在省略此开关时自动选对配置。Store V4 CMake compile pool为4，legacy仍为8，与本次4 CPU预算一致。算法、loader与模型/请求冻结输入未改。

## 已执行的证据

实际setup源码的CPU元数据测试先红后绿：默认legacy保留、V4服务/Store身份及依赖、无效配置拒绝五项。Click/FastAPI各有独立红例；最终完整CPU套件297/297通过，无跳过/失败/错误。它执行实际源码AST和CPU C++替身，不是实际wheel、CUDA或ABI通过。源码回执额外覆盖根目录setup/requirements/MANIFEST等旧runner diff范围未包含的文件：9个输入逐一与暂存Git blob/原始字节SHA一致，完整packaging diff SHA 9963df658f9124afdcace3e6cd3f16729dbb4104b9acc015fac6a32a903ebbbc；runner原范围diff SHA 24c11557139a6a9920ae99c53645dd9d6512d67840b251eeb70ba64b641647b4。

GPUtil1.4.0只有sdist；严格no-build解析先失败。固定PyPI sdist SHA后，在已缓存CPython3.12.3镜像中、runc/no network/no GPU设备的只读容器构建通用wheel。首次默认容器UID无法导出，后按宿主UID2147成功，实际wheel SHA 13fe2ac6ac21ce73f99432589b0f5720150335a31b4bbe8fbe237f30f10c35d5。该镜像的旧Torch/vLLM不作为目标ABI，未用于目标环境。

Windows UV0.11.28先解析候选；随后在qjhs2使用实际Linux CPython3.12.3和相同UV版本重新解析。两份图的203个包记录（含marker）版本一致，Linux pylock SHA c8d28434c48cb50cd409d7cd791bf6be25c2940744a8725299784f6ae8b27ed4。核心16包约束复用P2冻结值，索引截止2026-11-05T00:00:00+08:00；实际软件来源/hash保存在锁中。这是依赖可解性，不是实际安装/import或最终可运行wheel组合证明。

## Bootstrap 与最终锁的区别

pylock.v4-bootstrap-linux.toml包含两个PyPI vLLM0.20.1 wheel候选，只用它们的元数据闭合依赖，不选其一冒充固定源码构建。v4-bootstrap-external.txt显式no-emit-package vllm，保留它的依赖供构建；实际安装必须带本地GPUtil wheel的find-links和全部hash检查。外部同步第一次漏带该路径，解析阶段已停止、未成功安装，失败日志保留于活动bootstrap目录；修正的第二次同步正在构建容器内执行，不能据此声称已成功。

最终必须以132765e...源码构建的vLLM wheel及本仓服务/Store wheel作为明确本地文件/hash重新闭合最终锁，真实安装后pip check、ELF/ABI/import及CPU实际扩展检查全部过关。不得直接把bootstrap候选锁当最终环境锁，也不得安装候选PyPI vLLM代替源码版。

## 活动 P4A 执行状态

原位模型64完整权重/16运行输入已验证，路径仍/mnt/raid/models/DeepSeek-V4-Pro；本阶段不挂载完整模型目录、不创建LLM或运行GPU。

实际基础镜像config ID ec9a1a479ad110600f15a0959a7b4c5bc9ddaed8b34a2d020abb7aea8b5b1707，来自官方CUDA13.0.2 Linux amd64 manifest6b661759...；全部11个rootfs diff ID与官方配置相等，完整crane验证及docker load成功。原Docker拉取客户端仍活但大层未完成时，经过PID/start ticks核对后明确停止本任务客户端，改用已验证工具和临时代理获取；没有重启Docker或删除共享层。镜像/软件大文件留/data，不入Git或/home。

新构建容器ac3a81f1426c8697120ddcadca8f3688654d0aa7370acba9f86cee6b924f0d8c：实际HostConfig为runc、无Devices/DeviceRequests、4 CPU/48GiB、cap-drop ALL及no-new-privileges。新Python/venv是3.12.3、glibc2.35，sys.prefix/base_prefix和禁用user-site已核对；容器内/dev/nvidia*为空。容器仅挂载受控软件工作目录和日志，未挂载权重。软件获取阶段使用host网络上的临时回环代理，不修改全局代理或主机推理包。

活动证据/作业根：/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106；大缓存/软件：/data/brick1/boyjiang/sllm-v4-pro-build；目标引擎源码：/home/boyjiang/sllm-v4-pro/engine-src/vllm-132765e35606，实际HEAD及v0.20.1 tag均等于冻结commit。当前还不能证明扩展构建/import、CPU真实codec、目标CPU tokenizer或完整P4A通过。GPU/原生gold/全Store恢复/生命周期仍属于后续独立准入。

## P4A 实际扩展与 wheel 闭合修复（后续状态）

外部202包已按hash安装；实际Torch2.11.0+cu130/CUDA13.0/CXX11 ABI=true。Store v4pro.1虽可导入，隐藏构建lib目录后新进程报缺libglog.so.1；首次成功不能当独立wheel通过。

最小修复仅作用于v4-pro：package_data增加*.so.*，CMake BUILD_RPATH=$ORIGIN及BUILD_RPATH_USE_ORIGIN=ON。服务/Store版本同时提升至0.8.0+v4pro.2，服务精确依赖新版Store；legacy保持原行为。两项新增检查先红后绿，完整CPU契约299/299通过。closure-source-snapshot.json另外冻结包含根setup在内的三个修改文件SHA。

新版Store wheel624429字节，SHA256 1bd312360d236d9e8164e93b93a4d82e5c2d89b21be66da688d17f73f722475b；服务wheel74585字节，SHA256 1093af648de35d946c1b8624ab1e5421db6eab14f3698840c5b6f30e838e4723。隐藏构建lib目录后的新进程导入通过；实际glog来自venv内包目录，hash等于wheel内SONAME，未加载/opt/src库。构建目录已恢复。

真实_C.save_tensors在/data本地XFS以原生O_DIRECT写入tiny CPU张量：MXFP4打包I8、FP8 e4m3fn、UE8M0 e8m0fnu、BF16、mHC FP32及I64覆盖全部256种原始字节，精确别名共享offset，逐段字节及CPU重建一致。单partition4096字节，SHA a5d0f3ecf00a5104be264073b460387ba33acdc13874404e0df4de3aea857936。只证明真实CPU写入器与位保存，未验证GPU恢复、后处理或全Store。

实际目标Transformers5.6.0/tokenizers0.22.2重新执行固定wrapper AST的BPE，两请求仍为30/43输入token，逐ID与早期候选相同；BOS一次、roundtrip一致。首次复制输入目录权限失败，改用所有者UID2147后通过，未增加容器cap。尚需源码vLLM安装后完整tokenizer导入，不把AST替代当引擎导入。

当前uv pip check通过204个已安装包，但尚缺进行中的源码vLLM wheel与最终锁，P4A尚未整体验收。编译固定132765e...、SM90、4CPU/48GiB、MAX_JOBS4/NVCC_THREADS1、8小时上限，无GPU设备或完整模型挂载。上述失败均为构建验证，不占正式12个attempt ID。
