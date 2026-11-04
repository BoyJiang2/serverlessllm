# 官方仓库默认次数复现结果

状态：全部通过。每模型、每种格式的random和cached均30次；预检及cached预热不计入正式样本。FP16，fox输入，generate(max_new_tokens=50)，各自原始生成配置保持采样；输出按相同seed配对。标准差为总体标准差（除以N）。

| 模型 | 模式 | 格式 | N | 平均秒 | 中位数秒 | 标准差秒 |
|---|---|---|---:|---:|---:|---:|
| Llama-3.1-8B-Instruct | random | safetensors | 30 | 11.421353 | 12.188942 | 2.591253 |
| Llama-3.1-8B-Instruct | random | sllm | 30 | 2.392457 | 2.384167 | 0.035438 |
| Llama-3.1-8B-Instruct | cached | safetensors | 30 | 2.553693 | 2.515677 | 0.093570 |
| Llama-3.1-8B-Instruct | cached | sllm | 30 | 0.513640 | 0.512505 | 0.004176 |
| Qwen3-8B | random | safetensors | 30 | 11.767700 | 12.658861 | 2.644970 |
| Qwen3-8B | random | sllm | 30 | 2.433736 | 2.433008 | 0.030329 |
| Qwen3-8B | cached | safetensors | 30 | 2.679107 | 2.656050 | 0.084062 |
| Qwen3-8B | cached | sllm | 30 | 0.537544 | 0.532886 | 0.016640 |

| 模型 | 模式 | 平均加载加速 |
|---|---|---:|
| Llama-3.1-8B-Instruct | random | 4.774x |
| Llama-3.1-8B-Instruct | cached | 4.972x |
| Qwen3-8B | random | 4.835x |
| Qwen3-8B | cached | 4.984x |

四组共120对输出逐token匹配（每对SF/SLLM），配置及缓冲区检查全部通过。Llama random30复用此前已经验证的原始运行；其余三组为本轮默认次数运行。完整参数字节SHA在两模型各自的独立GPU配对预检中验证，不声称240次正式加载全部进行了全参数字节hash。

复用官方9f50241加载微基准和既有运行镜像/SDK。必要任务适配：离线复用本地checkpoint，预热用本地目标模型；加载入口恢复generation_config，指定参数dtype初始化以保留FP32 RoPE缓冲区，兼容当前Transformers私有接口变化。Torch2.10+cu129、Transformers5.5.1，GPU7 H20-3e，容器8CPU/128GiB，Store32GB/4IO线程/16MBchunk。运行命令不传num-replicas，清除同名环境变量后使用runner默认30。

加载计时保留官方API墙钟区间，未在终点额外添加CUDA同步；转换、模块导入、预热、校验与生成不计入加载时间。恢复generation_config文件的IO计入SLLM加载API。生成时间额外做CUDA同步，单独记录。Random使用30份独立副本随机顺序各加载一次，不清系统page/inode cache；cached只制作一份副本，预热后重复加载30次。这是仓库协议，不是论文Figure6的20副本/清缓存协议，本机为H20，与README的H100环境不同。

本次Qwen预检、Qwen正式组、Llama cached组均独立native scope限时，正常退出后至少60秒GPU7memory0、PID0和scope移除观察通过。临时副本清理完成。共享SDK/原checkpoint只读，Kimi代码与历史实验不变。配对一致性结论限本次输入/种子，未验证fully_parallel GPU或完整原生服务。

## 服务端原始数据

Llama-3.1-8B-Instruct / random：
- `/data/brick1/boyjiang/ServerlessLLM/models/.llama31-buffer-parity-final-20261104/results/random/meta-llama_Llama-3.1-8B-Instruct_safetensors_30_random.json`
- `/data/brick1/boyjiang/ServerlessLLM/models/.llama31-buffer-parity-final-20261104/results/random/meta-llama_Llama-3.1-8B-Instruct_sllm_30_random.json`

Llama-3.1-8B-Instruct / cached：
- `/data/brick1/boyjiang/ServerlessLLM/models/.llama31-default-cached30-20261104/results/cached/meta-llama_Llama-3.1-8B-Instruct_safetensors_30_cached.json`
- `/data/brick1/boyjiang/ServerlessLLM/models/.llama31-default-cached30-20261104/results/cached/meta-llama_Llama-3.1-8B-Instruct_sllm_30_cached.json`

Qwen3-8B / random：
- `/data/brick1/boyjiang/ServerlessLLM/models/.qwen3-official-random-cached-20261104/results/random/Qwen_Qwen3-8B_safetensors_30_random.json`
- `/data/brick1/boyjiang/ServerlessLLM/models/.qwen3-official-random-cached-20261104/results/random/Qwen_Qwen3-8B_sllm_30_random.json`

Qwen3-8B / cached：
- `/data/brick1/boyjiang/ServerlessLLM/models/.qwen3-official-random-cached-20261104/results/cached/Qwen_Qwen3-8B_safetensors_30_cached.json`
- `/data/brick1/boyjiang/ServerlessLLM/models/.qwen3-official-random-cached-20261104/results/cached/Qwen_Qwen3-8B_sllm_30_cached.json`
