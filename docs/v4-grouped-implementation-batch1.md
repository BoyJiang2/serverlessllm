# 分组raw与失败确认：首批CPU实现

2026-11-06，用户已选择继续分组raw与保留失败终态路线。父提交e409faee427650da4653e1b9865e0471d2fd48ea；仍不安装推理环境、不运行GPU。

## 失败确认实现

GPU提交的future从原本只能顺序保留的queue改成按(model_path, replica_uuid)保存的shared_future。Confirm等待对应操作完成，不占用元数据锁；完成失败、抛异常、早于replica创建的失败均返回非零。ToGpu完成结果本身已覆盖copy与IPC close，不再在终态后查找可能已消失的replica并重新等待。未知UUID立即失败；重复提交同UUID被拒绝，不能覆盖原结果。多个/迟到Confirm可反复读取同一结果。

ToGpu失败后不再调用FreeGpu删除失败replica。当前协议没有客户端终态ACK，因此完成结果和失败replica保留至Store生命周期结束，不在第一次Confirm返回后删除。ClearMem遇到未完成、失败或抛异常的GPU任务会拒绝并保留状态，不能报告已清理。它不代表未知IPC/owner已释放，也不解决复制线程本身永久阻塞；后续仍需RPC预算与进程级隔离。

两个C++ CPU用例编译并执行实际LoadModelFromMemAsync、WaitModelInGpu、ClearMem方法，worker/Model受控，无CUDA链接；覆盖成功/失败/异常、重复提交、未知UUID、多次确认、等待时锁可用、活跃/失败ClearMem拒绝，以及成功清理后的迟到Confirm。另静态检查失败路径不再erase。最初2项用例在旧源码失败，日志保留；最终用例扩展包含上述ClearMem窗口。

## 分组元数据实现

新增v4_grouped_state.py，完全stdlib。外层格式sllm-v4-pro-grouped-raw-v1；每个payload组保留既有raw格式、dtype/alias/空项/逐文件hash契约。全局root/groups/{remainder,expert_0000..0060}/rank_N对应496个identifier。每rank独立grouped_state.json列出组、模块、tensor覆盖与组manifest hash，全部8rank验证成功才能原子发布COMPLETE。

专家组必须包含其全部4个weight/scale，且Marlin backend匹配；remainder保留非专家及buffer。原生专家遍历顺序使用显式列表持久化，不能依赖JSON字典键顺序。拒绝跨组alias、重叠模块、组遗漏、tensor重复、混合manifest、组内设备UUID变化与跨rank重复UUID。校验完成后的同字节payload损坏仍须在未计时preflight用verify_payload=True重哈希；不能在正式cold计时中遍历payload来改变缓存状态。

8项CPU用例覆盖62组完整划分、alias闭合、缺scale/层/错误backend、全rank COMPLETE与重复发布、部分rank不得发布、同长度payload损坏及丢文件、调换组顺序。fixture每张量仅几个字节，是元数据TP8模拟，不是真实权重。

## 统一验证与未完成项

qjhs002现有Python3.10.12/g++上统一108项测试通过，0失败、0错误、0跳过。receipt记录父HEAD与5个被测文件暂存diff的SHA256（d9d7e374662c2ef5a1adc5d4496de793c3dba820462edd4e7adee3a1c3ce8298）；随后新增本说明与日志不改变被测代码。未编译完整native扩展、导入真实Torch/vLLM或运行Ray/GPU，不能作为ABI或物理回收证明。

这批完成了失败确认CPU接点和分组元数据，不是完整分组恢复。下一批接入逐组导出/恢复、与原生quant遍历保持一致的一次后处理、完成栅栏、raw引用断开与异常强引用隔离，并与当前manifest比较fresh实际布局。尚未注册可执行grouped loader；旧单slab容量guard保持原样，不能靠仅有新manifest跳过它。

完整模型容量、真实FP8/UE8M0往返、原生金标、服务生命周期和60秒物理释放都仍未验收。原目标保持active，不再等待之前已获明确选择的两个路线问题。
