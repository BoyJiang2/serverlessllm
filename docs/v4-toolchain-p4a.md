# P4A 工具链、源码闭包与辅助模块实际导入

本证据批次无算法改动。Store wheel闭合修复及299项CPU契约见v4-wheel-closure-p4a；本批次不重复该套件，不宣称vLLM编译已完成。

实际目标flashinfer、flashinfer_cubin、tilelang、tvm_ffi、compressed_tensors在独立子进程中均导入成功；Store torch包装/client/utils/server/CLI也实际导入通过。没有调用GPU算子、客户端方法或启动服务/模型。

官方CUDA13.0.2根镜像及受控Git安装之外，实际CPython3.12.3来自20240415 standalone归档67368051字节，SHA a73ba777b5d55ca89edef709e6b8521e3f3d4289581f174c8699adfb608d09d6，与UV0.11.28固定版本官方download-metadata一致。独立完整归档hash通过；安装的解释器/libpython及4137个非pyc文件与归档相同，未缺文件，4个pyc未比较。三处差异为两个pkgconfig文件和_sysconfigdata：host UV管理安装与容器中对应字节完全相等，实际差异及SHA、ABI关键字段不变均已归档；没有宣称整个Python树原封不动。两次checksum/API读取403也保留，最终通过官方固定UV源码元数据取得expected SHA并校验实际归档。

nvcc13.0.88、实际g++/Git/CMake/Ninja/UV、Python二进制hash和完整OS包清单均已冻结。本轮只读源码快照实际核实vLLM132765e...以及Cutlass、DeepGEMM、FlashMLA、QuTLASS、Triton、FlashAttention的git HEAD和递归submodule身份；主源码及依赖工作树均干净。此快照来自进行中的真实构建，完成时仍需再核对，不能把源码身份当编译/链接通过。

实际容器runc/no Devices/no DeviceRequests、4CPU/48GiB、cap-drop ALL/no-new-privileges，仅挂载任务软件和日志。cgroup累计oom/oom_kill=0，memory.max事件87163表示触发过上限回收，不等于零压力；该内核未提供memory.peak，无法据此报告峰值。临时磁盘仍在预算内；这些不是GPU预订或模型准入。

vLLM builder PID2907/start_ticks50976357已重新验证存活；8小时界限仍有效。最终源码wheel、本地三wheel闭合锁、真实安装依赖图/ELF/组件和完整tokenizer导入仍待完成。无正式实验ID被消耗。
