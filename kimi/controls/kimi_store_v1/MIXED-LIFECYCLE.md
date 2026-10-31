# 独立混设备loader生命周期候选

## 2026-10-31：延迟rank的私有native注册接口

`register_deferred_mixed_loader`现复用冻结registry，登记`sllm_kimi_native_v1`的Base子类。登记仅接收八份固定字段的纯字符串/整数元数据及八个设备预算，浅快照不复制Tensor载荷；不读取CUDA组、侧车或实际native ABI。内置格式不变，foreign native登记拒绝覆盖。

仅在该实例`load_model`中调用既有`current_rank_context`，要求实际TP8/PP1及合法整数rank/device；权重按rank选，预算按device选，两者不要求相等。随后调用原`make_mixed_store_loader`，其实际build/侧车/完整目标、2×预算拒绝和官方Base上下文仍生效。失败保留delegate及其部分native状态，禁止同实例重试；不自行free、取消DMA或宣称释放，仍需guard负责worker退出取证。

CPU控制执行冻结registry→deferred→原mixed factory→私有Base小模型链；CUDA、native ABI/IPC、安装边界仍明确mock。另测注册不查询context、rank≠device、配置快照、非法context、未初始化CUDA、预算及部分加载失败。独立审阅发现旧`deepcopy`可复制Tensor，负例先失败，现纯元数据预检后浅快照拒绝Tensor/opaque且不调用其copy钩子。

该私有API现由`source_plugin.register_native`消费：独立native spec/manifest及private entrypoint，文件校验后才导入runtime；细节见SOURCE-PLUGIN。export回调仍独立。CPU子进程替身注册不是实际vLLM spawn传播，实际ABI/活跃Store、峰值、八rank和服务门槛尚待完成。没有远端计算、完整转换或GPU通过。

`vllm_mixed_hook.make_mixed_store_loader` 已把现有组件组合成独立BaseModelLoader子类，不注册、不部署worker、不改旧工厂或旧运行包。顺序为：受限源码/二进制绑定及侧车预检 → 官方Base dtype与目标设备上下文 → CPU文本/官方CUDA视觉构造（保留官方quant/current-config/reload）→ mixed Marlin辅助规范化 → 逐记录allocator/多handle请求/确认 → 独立全payload核验与按角色安装 → 放弃loader持有的全raw状态引用 → 显式最终CUDA target的官方quant/Attention后处理 → Base eval。

执行冻结Base函数body时仅复制其globals到本类私有namespace，用两个本地callback连接混设备构造与后处理；不修改官方模块全局函数，也不重新实现Base body。源码校验使用unwrap后的body，因此此组件不保留外层Base tracing decorator的span；没有用于正式计时，后续测量必须明确事件来源，不冒充旧入口等价耗时。Base与initializer必须持有同一个model_config对象，避免两套配置分叉。

每实例为一次性lifecycle/weights状态；各阶段异常保留失败状态与discard_worker_required。native失败携带的同次状态和receipt保留；install失败保留raw引用，postprocess前成功安装则清掉loader的整state引用。后者只是代码寿命约束，不是实际GPU释放证明；任何未知DMA、部分包装失败或IPC关闭问题仍由本run独立守护/worker退出取证，不主动free地址或卸载共享Store。没有删除实例、释放服务或60秒观察成功证据。

## 保留的预算停止点

新候选仍保留checkpoint_bytes×2的保守停止条件，直到完整混设备峰值方案通过单独审阅。该条件不是声称混设备路径必然同时持有两份完整GPU权重，也不是足够的总峰值预算；不能填大预算绕过。在原H20容量及冻结TP8下，现有静态下界仍会阻止正式模型进入。CPU目标驻留、8rank Store池/页缓存、GPU视觉初始副本、单层重排workspace/allocator粒度、服务池与安全余量尚须分别给出全局预算和可验证上限，才可修订此停止条件。当前build receipt也没有实际已编译补丁可通过的生产证据。

## CPU验证与边界

新增5项测试，总计207项通过，见mixed-lifecycle-tests.json。成功用小CPU模型执行固定Base body，构造、全rank辅助、native、安装和后处理是明确mock边界；只证明组件接线顺序、私有namespace无全局替换、全raw引用在后处理前不由loader继续持有、eval及禁止重试。各阶段注入失败验证终态和清理义务保留；错误config、预算不足及未编译ledger拒绝。没有实际CUDA、完整Kimi或native ABI通过。首次测试因fixture漏receipt.stage失败已保留，修正fixture后通过，未放宽生产规则。

下一项为实际预算/构建身份与worker集成；独立registry仍使用旧native factory，尚未切换到本候选。完整源码/测量审阅、当前窗口、八rank验证、业务金标、新守护/probe/监控及释放证据都没有被此CPU里程碑替代。GLM原40ID/金标/历史失败冻结，不启R24。
