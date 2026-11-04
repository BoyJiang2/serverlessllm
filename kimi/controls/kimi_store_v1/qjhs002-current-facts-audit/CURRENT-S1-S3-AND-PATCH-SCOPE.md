# 当前 S1–S3 事实与最小补丁范围

2026-11-04，接续既有记录；不替换历史失败、审阅或金标。当前适配源码仅为 qjhs002:/data/brick1/boyjiang/kimi-new/ServerlessLLM，HEAD 5d197a9b91658ebe2e84de338173b07e21c1bc7e。此前历史表中的旧路径和未决状态须结合后续补充阅读。

| 阶段 | 已有证据及本轮核验 | 尚未证明 |
| --- | --- | --- |
| S1 版本 | 已有运行清单为 Python3.12.3、Torch2.10.0+cu129、vLLM0.19.1/gb1388b1fb、Transformers5.5.1；本轮重读当前 worker requirements 仍固定 vLLM0.11.2。0.9.0.1 属旧 Git 文档，Kimi 官方部署指南推荐0.19.1，因此不降级。修正版小探针实际证实该镜像的 Torch/CUDA 小张量路径。 | 本轮没有重新执行 vLLM 包探针。README 的 Transformers<5 与现有5.5.1差异尚未由模型推理闭环；既有清单缺少 bitsandbytes、grpcio-tools、peft，不能称全部 SDK/原生服务依赖就绪。 |
| S2 基线与资产 | 本轮实际 SSH：适配 HEAD5d197a9；proto Git模式120000，实际链接 ../../proto/storage.proto，目标SHA95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b。用户确认历史 scp -r 跟随链接，恢复前证据已留存。旧官方目录 HEAD9f50241，只有未跟踪 models/，保留。最终24项源码/库/组件资产校验通过。 | 权重64片头与索引一致属于元数据完整性，未做595GB全量payload哈希。8MB组件校验不等于完整模型字节验收。 |
| S3 官方补丁 | 当前 patch.sh SHA214cd01c6cec786fbe5040e4b148ca9bf3e423c48387987106664142792e1e54；内层补丁SHAa734b0864c0bff21357db8b83ab1a46955abb381e136bc506f4c752067acab53。原官方0.11.2补丁对0.19.1原样移植有worker文本冲突；当前候选已在官方补丁文件做接口/tokenizer适配。既有真实安装树干跑回执：当前候选正向0、反向1、源文件SHA不变，分类未应用；三态脚本另有已应用/冲突夹具检查。 | 当前候选没有应用到现有镜像的vLLM树。干跑通过不是运行兼容证明；不能称实际模型加载、完整转换或原生服务通过。 |

## 当前八个生产文件

本轮以官方9f50241对比当前HEAD，实际得到以下八项。它们是已实现补丁的范围，并非本轮新增修改，也不是要求继续扩展的清单。

| 文件（均相对当前适配目录） | 已证实缺口及现有修复 | 证据边界 |
| --- | --- | --- |
| sllm_store/csrc/checkpoint/aligned_buffer.cpp | 非页对齐尾部丢失、旧文件残留；补齐尾部、检查写入/截断、重写时截断。 | 有界C++回归；不是10GiB性能测试。 |
| sllm_store/csrc/checkpoint/tensor_writer.cpp | 下一物理分片偏移遗漏padding；计入页边界。 | 缩小阈值夹具验证，生产常量未降。 |
| sllm_store/csrc/checkpoint/checkpoint.cpp | 首张量析构可能释放仍被别名使用的base；所有恢复storage共享base owner。 | 编译及CPU检查通过，实际组件最后别名释放尚待S4a。 |
| sllm_store/csrc/sllm_store/model.cpp | 成功通知早于IPC关闭、关闭错误未传播；关闭结束后通知并传播中断。 | CPU回归；本次组件RPC/IPC运行待验证。 |
| sllm_store/sllm_store/client.py | Confirm失败被布尔返回吞掉；抛异常并保留原始RPC原因。 | CPU异常路径回归；不是服务器运行成功证明。 |
| sllm_store/sllm_store/torch.py | 阻塞load在成功Confirm前构建拥有GPU内存的tensor；改为Confirm后Restore。 | 默认nonblocking语义不变；全局失败所有权/即时回收未闭环。 |
| sllm_store/vllm_patch/patch.sh | 失败误报already applied；严格双向干跑、冲突退出失败。 | 真实patch夹具及源码干跑；不代替接口兼容验证。 |
| sllm_store/vllm_patch/sllm_load.patch | 0.19.1 loader参数和Kimi官方tokenizer工厂接入差异。 | 官方tokenizer/core/chat的CPU证据；未获完整模型推理证据。 |

其余22项差异是测试/探针/输入清单。torch.py普通diff含较多行尾差异；本轮另用 --ignore-space-at-eol 核对实际逻辑，不借机格式化。当前未跟踪的两份libglog文件不纳入Git。没有新增codec、模型loader管理框架或expert切分规则。

## 当前门槛

已获批且执行完的修正版GPU小探针：Python、1MiB CUDA计算、正常退出以及两路至少60秒释放观察通过；只支持PID512加双visibility UUID的该配置，不证明最小PID阈值或唯一根因。

S4a最终控制器及24项输入已固定并通过独立技术审阅；运行尚未开始。11:58核验时原预约12:00结束，不足完整180秒，记为NOT_RUN_GATE而非运行失败。新12:00–19:00预约用途autoscaling，已询问是否可供Kimi使用，答复前不启动计算。

本轮只读取Git差异、requirements、链接及小文件SHA，没有创建容器、启动Store、执行GPU、模型、转换或TP8。两条只读命令分别因错误补丁路径及stdin末行CR失败，后续按真实路径与单独status命令补齐；原始输出保留在work/current-production-diff-audit*.txt，不把失败返回码当完整成功。阶段和goal仍未完成。

证据入口：QJHS002-S1-S3.md（历史追加记录）、runtime-dependency-closure.md、kimi-tokenizer-installed-patch-dry-run.json、gpu512-visible-pass-receipt.json、s4a-final-publication-receipt.json；本轮新增只读差异输出见work/current-production-diff-audit.txt、work/current-production-diff-audit-completion.txt、work/current-official-runtime-diff.txt及work/current-pristine-status-audit.txt。
