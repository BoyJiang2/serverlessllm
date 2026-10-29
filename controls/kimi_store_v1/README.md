# Kimi INT4 Store CPU 适配候选

2026-10-29：本目录只有**导出前契约与字节分块规划**，不是可运行的Store导出器或vLLM loader，不提供启动入口。不修改GLM runtime/active。

已实现：

- 原字节校验官方Kimi config SHA及嵌套compressed-tensors量化位置，拒绝其他revision/TP/rank。
- 为候选vLLM0.19.1 / Torch2.10.0（cu129或无local suffix）记录ABI身份。这是计划输入约束，不是二进制镜像已验证声明。
- 对外部审计获得的每rank tensor记录检查shape、dtype、字节数和contiguous状态；只允许I32/BF16/F32，未知状态拒绝并等待审计，不自动转换dtype。
- 对原始storage字节按元素边界规划连续块，最大单块2GiB、默认64MiB；不解包INT4，不创建CPU tensor，不读取模型文件、不写Store。
- 输出明确保留payload_verified=false、tp_mapping_verified=false、store_created=false、runtime_admitted=false。

验证：`python -X utf8 -m unittest discover -s controls/kimi_store_v1 -p test_contract.py -v`。使用微型元数据夹具验证拒绝与分块边界，不使用假权重进行模型推理，也不声称完成八rank权重校验。

## 必须继续完成的接线

1. 从冻结backend实际初始化后的rank状态生成完整清单，区分checkpoint参数、必要buffer、别名与scratch。当前I32/BF16/F32集合来自官方checkpoint；实际backend若出现其他dtype，先审查来源再扩展，不能直接删掉。
2. 查明Kimi TP8融合专家、scale、weight_shape侧车映射。计划函数不根据名字猜TP切分，也不猜kernel；当前只校验已提供的记录，不能证明清单完整。
3. 实现GPU tensor逐块复制及同步写入SLLM Store。现有save_dict累积CPU字典或一次性写全tensor的接口，尚未证明支持本规划，不能把每一块当独立模型Store保存后声称可恢复。
4. 每块写完释放临时CPU引用，计算逐tensor流式SHA；同rank只允许一块在途。审计hash临时缓冲、GPU→CPU staging、writer对齐缓冲也占内存，因此2GiB仅是单块上限，**不是rank整体锁页/RSS上限**。进一步给整个进程加经测量的上限再批准运行。
5. 读取端恢复同一初始化前置状态，再严格执行一次backend权重后处理。保存官方配置/原始shape、完整rank映射、布局规则、ABI及派生状态hash。不要重复Marlin repack或将postprocess状态当preprocess。
6. 对I32 packed值、BF16 scales/权重、F32路由参数和所有必要元数据做八rank全量校验；建立新原生金标。现有GLM格式和金标不得借用。

本轮不连接服务器、不读595GB输入、不占GPU/Store池/页缓存；CPU通过后仍需镜像/完整适配审阅、有效资源窗口、独立守护与模型实际验证。先GLM实际验收及释放，再Kimi原生参考→Store正确性→完整服务。
