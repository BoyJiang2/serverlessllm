# 显式混设备状态清单与安装边界

本次补齐元数据及独立安装入口对按名称设备声明的支持，未执行CUDA、完整模型、Store服务或worker。旧单设备默认入口保留；GLM历史数据及预算拒绝不变。

`state_devices.state_devices` 将默认单设备或显式名称映射变成严格设备声明：映射必须恰好覆盖全部待审名称，每项是CPU或带索引CUDA。不依据Tensor当前位置猜测预期设备，也不移动或读取payload。`runtime_inventory.collect_inventory(... device_map=...)` 仍完整扫描登记及标准容器内的隐藏Tensor；映射未声明隐藏Tensor时拒绝，不因只声明登记状态漏掉扫描。opaque对象审计限制保持原样，不能声称所有隐藏状态已证明完整。

`device_state.encode_device_state(... device_map=...)` 对各名字按声明核验实际device、全storage所有权、连续布局、dtype、显式codec。原生别名/共享storage识别加入device维度，避免不同设备碰巧出现相同数值地址时误认别名。默认CPU布局、alias结果保持一致。

`install_native_state(... target_device_map=...)` 只在独立记录分支接受显式目标映射。目标可处于CPU文本/CUDA视觉，而源仍必须为同一次独立分配、全部位于指定native设备；不是支持把源分散到任意设备。严格设备核验发生在payload复制/Parameter绑定之前，其他layout、角色、alias、全部摘要及buffer codec检查仍执行。赋值后使用源Storage设备，Param对象属性及buffer语义保留；真实CPU到CUDA的Parameter.data切换尚未运行验证。单块旧路径不能用此参数绕开引用锚或双份预算。

新增5项CPU测试，合计192项通过：默认/显式CPU映射的metadata及alias完全一致，真实小INT4/BF16位安装和Parameter身份保留，缺项/多项/错误设备/未索引CUDA拒绝，隐藏容器Tensor不被漏过，坏映射在payload调用和绑定前拒绝。声明CPU+CUDA字典的解析不触及CUDA，但不能冒充真实混设备正向运行。

下一步仍需把CPU Marlin辅助参数规范化、混设备构造与逐记录producer/install连接到新loader，明确Base后处理target_device行为并核验全部最终GPU状态。当前未修改rank normalizer/native工厂，没有完整混设备生命周期或峰值通过；实际build身份、worker/plugin、新守护及释放probe仍待闭合。
