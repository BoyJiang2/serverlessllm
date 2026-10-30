# Kimi混设备预算：已知下界与未完成的峰值门槛

本轮仅本机读取冻结config、23.6MB官方索引及120KB全分片header摘要，未读取权重payload、连接服务器或占用GPU。`memory_floors.py`已对实际冻结输入执行，输出mixed-memory-floors.json；输入SHA与分片名称/张量计数覆盖匹配，不以“当前内存空闲”替代预算。当前upper_bounds_known、peak_memory_verified、runtime_admitted均为false，旧2×checkpoint停止条件不变。

## 证据等级与算式

- 元数据确定值：SF文件共595,177,988,208 B，payload 595,148,192,736 B。官方索引显示第63分片只有mm_projector（108,555,776 B），第64分片只有vision_tower（833,732,064 B）；其余为language_model，合计594,205,904,896 B。按完整索引逐名字分组，再与64个header分片计数和dtype字节汇总交叉核验，无混合根分片。
- 条件静态下界：固定TP8/PP1/noEP/MLA/Marlin，视觉encoder=data导致视觉和projector每rank复制。文本先保守去掉552,960 B专家shape源元数据，除必需复制的MLA A（源1,846,935,552 B）外按理想8分。text_rank=(text_payload-shape_bytes+7×MLA_A_bytes)//8。其他文本复制、额外buffer/shape重建、分配粒度均未计入，因此不是完整rank实测，也不是上限。
- 配置逻辑分配：Marlin构造的四个g_idx/sort辅助数组，60层×2×384×(7168+2048/8)×4 B/rank，共10,947,133,440 B/8rank。源码及已有CPU辅助政策确认维度；这只是逻辑bytes，不证明RSS或页已触碰，归一为空后也不推断进程RSS一定下降。

| 项目 | 数值 GiB | 证据等级 |
|---|---:|---|
| 每rank文本raw | ≥70.680 | 条件静态下界 |
| 每rank视觉+projector raw | 0.878 | 冻结源BF16 payload，条件复制政策 |
| 每rank总raw | ≥71.557 | 条件静态下界 |
| 8rank CPU文本目标 | ≥565.438 | 条件静态下界，混设备构造 |
| 8rank初始CPU辅助数组 | 10.195 | 配置逻辑分配，非RSS |
| 8rank Store有效payload | ≥572.458 | 条件静态下界，非pool容量配置 |
| 完整SF文件页缓存 | 554.303 | 假设全部驻留的文件字节，非当前实测 |
| 完整Store文件页缓存 | ≥572.458 | 假设全部驻留，未含对齐和尾部 |
| native复制完成/安装前每卡raw+初始视觉 | ≥72.435 | 条件下界，非峰值可容纳证明 |

GiB=2^30 B；不把595GB与595GiB混用。Store池至少覆盖raw有效数据，还需文件对齐、pool分配粒度、RPC和辅助状态；实际pool上限尚未冻结。

## 各阶段同时驻留

| 阶段 | 主机匿名/锁页 | 可回收文件页 | GPU | 未知项/停止点 |
|---|---|---|---|---|
| 静态准备 | 本机小文件/索引；远端不计算 | 不全量读取 | 无 | 不改变共享IO |
| 源SF转换/参考 | 不需要native Store池；分块导出另计 | SF按实际读取驻留 | 一份模型raw及初始辅助 | 完整初始化/重排/workspace峰值未知，需独立source worker预算 |
| mixed构造 | CPU文本≥565.438GiB+逻辑辅助10.195GiB；若已建Store池，再加pool | 既有SF/Store缓存按真实驻留记录 | 视觉raw约0.878GiB/卡 | CPU allocator/RSS、context与额外buffer未知 |
| native传输至安装前 | CPU文本目标仍在；独立Store池另计 | Store读入页可与pool同时存在 | native raw+原视觉≥72.435GiB/卡 | native分配粒度、DMA、辅助服务未知 |
| 安装/重排 | CPU目标逐项被替换；不得据引用删除宣称RSS已回收 | 两类页缓存可能同时留存 | 旧canonical逐记录与新重排结果局部重叠 | 最大层重排、新旧allocator/workspace及最终服务池未知 |
| 实例删除及60秒观察 | Store池可仍常驻，按实验层级单列 | 普通页缓存可保留 | 实例拥有的显存须实测 | 线程/RSS/PSS/锁页/shm/IO需归属证据 |
| 整体退出 | 仅本run池/worker/辅助资源释放 | 磁盘资产和普通缓存保留 | 仅本run资源清理 | 新守护、退出取证和60秒证据未完成 |

例如CPU文本下界+Store有效payload下界已约1137.896GiB；若Store文件全部驻留则约1710.354GiB；再同时完整保留SF页缓存则约2264.656GiB，超过历史名义1923GiB。**这不是OOM结论**：普通文件页可回收，CPU/pool数值也不是完整峰值或当前驻留；它说明不能同时承诺两类全热缓存和大池/CPU目标。不得用清全机页缓存或悄悄改变正式缓存语义解决。实际运行要分别核验可回收页、匿名、锁页与服务开销，并明确需要保留哪类缓存；若条件无法满足，则拒绝启动或单独修订协议。

原始SF资产已下载；未来Store磁盘有效字节下界约614.672GB，两份磁盘数据合计至少约1.210TB，外加对齐/partial/日志和余量。已有SF不重复扣作新增下载，Store池和文件页是内存副本，分别计入RAM，不当作额外磁盘资产。没有重新核验本窗口磁盘空闲，不据此分配空间。

## 下一步门槛

目前不能撤去保守停止点。需要实际8rank初始化/转换后metadata、最大canonical/重排输出与workspace/allocator上限、显存context/KV/graph/服务预留，和全主机匿名/锁页/页缓存/NUMA峰值。小GPU probe仍需完整审阅、新窗口及守护，不在本CPU授权内。native补丁完整构建/ABI/live Store身份也尚未完成。

源SF转换本身不依赖native双份驻留，可继续准备一份模型的独立worker、插件及预算，不能把native未通过扩大成所有CPU工作阻塞。新增4项CPU算式/拒绝测试，总计211项通过；全输入生成仅静态metadata，不是权重/完整模型或服务通过。历史exit137原因仍未知，GLM结果及40ID冻结。
