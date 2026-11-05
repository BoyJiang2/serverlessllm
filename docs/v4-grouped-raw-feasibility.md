# P3 分组raw Store可行性与失败确认缺口

2026-11-06。依据SLLM `a0fc726616f3d460cf8c4cf8b107d8ff24aacea2` 与固定vLLM 0.20.1 `132765e3560659ff63ebd236203672e991b70e08`。本轮只做源码、元数据分析及抽取函数的CPU探针；没有实施分组loader、改变现有格式、安装环境、构建完整native扩展或运行GPU。路线选择仍待用户回复。

## 1 当前失败原因与分组边界

当前loader将整rank的raw张量放入同一allocation，并在model._sllm_storage_owners保留所有wrapper。RestoreTensors让同一allocation内各tensor共享一个cudaFree owner；只要仍有一个wrapper/参数引用，整块存储就不能释放。因此“删掉owner tuple”不能解决raw slab在转换期间与新参数并存的问题，也不能把共用slab的单层tensor单独释放。

固定模型config的E=384、H=7168、I=3072/TP8=384、61个decoder MoE层、EP/MTP off；Marlin所需H/256、I/128对齐已满足。只计每层4个专家weight/scale的字节数，不以HF文件大小推导每rank非专家内存。

| 项目 | 字节 | GiB/rank |
|---|---:|---:|
| 单层专家raw | 1,684,537,344 | 1.568848 |
| 61层专家raw或持久Marlin参数 | 102,756,777,984 | 95.699707 |
| 当前单slab raw+全部专家转换参数的必要下界 | 205,513,555,968 | 191.399414 |
| 分组逐层转换最后一层的共存端点下界 | 104,441,315,328 | 97.268555 |
| 已有H20清单容量 | 150,754,820,096 | 140.401367 |
| 分组端点后尚可容纳其他分配的上限 | 46,313,504,768 | 43.132812 |

最后一项不是已验证余量。分组下界只说明消除了整slab额外保活的结构性障碍，不能证明H20可装载。native Marlin转换中每专家repack输出先放list，再torch.cat；旧list和cat结果会共存，还有transpose contiguous、BF16 scale转换、workspace、非专家参数及副本、fresh构造峰值、attention/KV/压缩缓存、通信和driver/allocator成本。不能把97.27GiB当实测或峰值上界。

数值、shape、公式输入和未预算项见 `docs/evidence/v4-grouped-raw-p3/capacity-lower-bounds.json`。MTP-off不表示HF分片中不存在MTP数据；未将总payload减专家字节称为非专家实际rank用量。

## 2 上游顺序决定不能只拆文件

固定目标model_loader/utils.py先遍历全部quant_method.process_weights_after_loading，再遍历Attention/MLAAttention派生后处理。Mxfp4MoEMethod._setup_kernel校验raw shapes，调用oracle的Marlin转换，替换4个参数，再创建moe_quant_config/kernel和路由引用。Marlin scales最终转回E8M0；BF16中间表示不能误计为最终持久表示。

因此不能在load_weights里把每组先转成runtime，然后继续调用未修改的BaseModelLoader全模型后处理；第二遍会再次转换或触发raw-shape断言。也不能简单跳过全模型后处理，因为非专家FP8、attention/MLA对象仍需按原生顺序建立。

推荐评审的最小接点是worker内、仅对选中专家quant_method调用安装短期的逐组restore→原生一次postprocess→同步/引用检查→raw owner退休接点，继续让原生model-wide函数管理两遍顺序；接点在返回或异常时恢复。不是修改全局class，也不是经Ray传输闭包。若该接点无法保持device_loading_context/调用唯一性与对象身份，改为一个明确的上游可选hook补丁，不能默默复制并长期维护整套BaseModelLoader。

这是设计候选，不是已实施机制。每个实际quant_method必须唯一对应一个组，shared方法对象或跨组alias应先拒绝。所有rank仍需核对原生实际遍历、prefix、expert归属与backend选择。

## 3 候选格式与最小修改

候选新格式名为 `sllm-v4-pro-grouped-raw-v1`，尚未冻结。每rank暂按61个专家组加1个remainder组，共62个Store identifiers；TP8合计496个，而不是旧8个。remainder包含mHC/FP8非专家、buffer及非专家派生所需raw参数，实际manifest枚举为准。

1. 导出逐组生成独立contiguous CPU storage，再复用save_dict；不能先生成整rank CPU contiguous大副本再分组，否则host峰值未解决。record persistent/nonpersistent、empty、alias和逐tensor SHA等现有契约保留。
2. 组内必须包括一个专家模块的全部4个weight/scale及其闭合alias。跨组storage/view先拒绝，不能复制成不等价别名；确需合并组则重新计算容量。每rank manifest增加有序group→tensor覆盖表及组payload hash；8 rank和全部组验证成功才发布全局COMPLETE。
3. 恢复先fresh构造/取得expected metadata，释放skeleton参数storage，再加载remainder。按原生quant遍历逐组load_dict_owned→确认copy和IPC关闭→重绑raw→一次Marlin postprocess。不得预加载全61个raw组。
4. 完成转换后，要验证所有输出、quant config、kernel/scale/routing引用不再落入本组raw地址范围；保留buffer/参数子类语义。GPU调用可能异步，必须有覆盖实际使用stream的完成栅栏再释放raw owner。Python方法返回和cudaFree行为不能替代明确的完成契约。
5. 处理中失败须以rank/process生命周期可达的强引用隔离raw/新参数，不能仅把owner挂在即将因异常失去引用的临时model上。未确认的IPC/copy不能释放，已转换成功组也不能当整个rank已完成。
6. 两遍原生后处理、cache/派生对象重建与全rank验证全部通过才允许执行。层组成功不构成MODEL_READY，MODEL_READY也不构成自然EOS业务完成。

已有Store path、save_dict、load_dict_owned和confirm接口在静态上可分别服务多个identifier；这不证明496组的容量、时延或生命周期已经通过。现有单slab格式和容量guard保持原样，新旧格式不得混读。

## 4 pinned pool与实验准入代价

PinnedMemoryPool::Allocate按每个Model请求分别向上取整到chunk_size。分组即使payload字节相同，pinned占用也会增加。以下只是专家部分、假设实际data文件无额外padding的三个说明场景，不是已选择或实测的server配置：

| chunk | 488个专家组的合计pinned需求（不含8个remainder组） | 比专家按8rank单slab增加 |
|---|---:|---:|
| 32MiB | 777.75GiB | 12GiB |
| 256MiB | 854GiB | 88GiB |
| 1GiB | 976GiB | 208GiB |

专家组为61×8=488个，另外8个remainder才组成496总组；表中数值仅计算专家组。不得把组数或rounding成本藏进pool初始化之外。实际预算用全组data文件bytes及选定chunk重新算，并包括metadata/host峰值/余量。

store_pool_hit必须要求496个identifier中全部实际payload完整驻留，不能仍只检查8个rank根目录，也不能在计时中边读盘边声称pool hit。disk-cold必须覆盖全部组文件/索引的实际输入闭包；mincore前后读取/哈希顺序仍需冻结。每次组的RPC/confirm/后处理栅栏进入同一V4 weights_pipeline/quantization边界，不能从正式延迟中扣除新增开销。1200秒是否足够在正式12ID前验证，不能中途放宽；pool chunk/格式等变化须新协议revision，原GLM数据和失败分母保留。

## 5 本轮已复现的独立Store失败确认缺口

实际C++ LoadModelFromMem在ToGpu返回错误后调用Model::FreeGpu删除replica条目；WaitInGpu在UUID不存在时等待该条目出现。GPU Load RPC是异步提交，迟到的Confirm可能正好看到被删除的失败条目。client.ConfirmModel没有deadline；server的5次retry也不能解除其中一次native WaitInGpu永远等待的情形。

CPU探针抽取实际model.cpp的完整WaitInGpu与FreeGpu函数，使用std mutex/condition_variable和受控失败副本，无CUDA链接。结果：失败条目还在时确认返回1；FreeGpu返回0并删除条目后，迟到的Confirm在200ms观察窗仍未返回；探针手动重插失败条目并notify后返回1，正常结束进程。源码等待谓词表明，没有条目重现事件时会继续等待。这是可复现的迟到确认缺口，不是完整Store故障注入或GPU死锁证明。

fixture、stdout/stderr、源码/抽取函数hash和重放脚本都位于 `docs/evidence/v4-grouped-raw-p3/`。此处没有应用修复。按用户AGENTS.md对代码矛盾的要求，已请求明确失败回执语义：优先候选为保留失败副本直至Confirm完整终态被确认，再清理元数据；备选为独立失败账本。两种方案都不能释放状态未知的GPU owner。

无论选择哪种，终态要覆盖“条目创建前失败”、复制失败、IPC关闭失败、迟到/重复Confirm、Confirm自身取消等窗口；还必须跨越server内部5次重试。不能在第一次native Wait返回错误后就erase，否则下一次retry又等待不存在的UUID。没有可靠回执确认/隔离依据时不清理失败状态。成功replica元数据的长期退休也需审计，现有HTTP UnloadModel只支持CPU，不能假设已有GPU cleanup RPC。

## 6 选择后的CPU验收与停止条件

分组路线获明确选择后，下一批仅实现CPU可审查的组manifest/覆盖/alias闭合、全rank COMPLETE、逐组调用顺序与失败强引用测试，保持不安装环境、不运行GPU。随后才在获准的运行阶段构建和检查实际ELF、取得全模型反射与峰值、验证原生参考→完整Store→原生服务→两次卸载/再次拉起和60秒物理释放。

任何跨组alias不闭合、量化对象共享/二次转换、组payload/scale缺项、rank遗漏、raw引用未断开、设备/host/pool预算不足或失败确认不收敛，都停止进入正式计时。当前仅有可行性证据和失败确认复现，未证明分组路线可执行，也未完成SLLM×V4-Pro目标。
