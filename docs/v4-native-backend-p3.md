# V4 原生 SLLM backend CPU 接点

2026-11-06。承接 Store/loader 里程碑 `fcb69b31200670a02bf4785bb7a1076b024c6294`，本批只修改原生 `sllm/backends/vllm_backend.py` 与相应用例。Store 表示选择仍待用户回复，未继续实现分组/处理后/TP16路线。未安装推理环境、启动Ray/Store/model、导入真实Torch/vLLM/native扩展或运行GPU。

## 已实现

- 当backend_config显式设置 `tokenizer_mode=deepseek_v4` 且输入为messages时，调用实际目标tokenizer的 `apply_chat_template(tokenize=True, enable_thinking=False, drop_thinking=True)`，用TokensPrompt传入；复用目标编码，避免role文本拼接与二次BOS。显式input_tokens/prompt保留优先级。
- 复制顶层请求参数，不再pop调用者原对象。复用GLM的 `return_token_ids` 布尔选项；启用时额外返回token_ids和原始stop_reason，与finish_reason一起供独立验证器使用。默认响应字段和非V4消息格式保持原样。
- 在生成器执行前登记PENDING及当前请求Task，拒绝同时复用相同request ID；finally清理活跃请求。异常与取消都不遗留普通trace，debug history与活跃Task分开，V4 stop不等待已完成history。
- finally显式关闭结果生成器，覆盖“生成器已yield、调用者在trace更新处被取消”的窗口，触发目标生成器自身的GeneratorExit/abort路径。
- shutdown取消活跃请求、发出abort并等待Task退出，再调用engine.shutdown；所有调用者等待同一cleanup Task，调用者取消不会取消cleanup。abort失败仍尝试原生shutdown后传播失败；shutdown失败保留engine引用和异常，不声称已删除。
- V4 shutdown不额外调用frontend的CUDA is_available/empty_cache/synchronize；释放依赖原生engine及worker。该行为不证明frontend初始化时从未建立CUDA context。

继承并复用了Kimi分支已有Counter路径和基本engine.shutdown；GLM现有实现提供了可选token IDs和awaitable shutdown的对照。没有将GLM的所有权/计时结果当V4运行证据。

## 固定目标接口与验证

固定B1为 `132765e3560659ff63ebd236203672e991b70e08`。静态核实public AsyncLLMEngine是AsyncLLM alias；get_tokenizer、shutdown为同步方法，abort为异步，generate的前三个业务参数仍为prompt/sampling_params/request_id。root exports、inputs与Counter也已读取。逐文件Git blob及SHA256见 `sllm_store/tests/fixtures/vllm0201_backend_sources.json`，不以版本字符串代替源码身份。

CPU测试执行实际backend源码的类/函数，以及固定B1 tokenizer wrapper和编码函数；第三方import隔离。B1编码文件依赖regex，但本次只抽取不调用regex的chat编码路径，未安装或替代regex执行解析路径。其字符串结果与P2固定revision官方业务prompt逐字符一致。**底层encode方法返回受控占位IDs；没有证明真实BPE IDs、实际tokenizer/engine import或模型输出正确。** Fake engine的输出4、EOS=1、finish_reason仅用于数据透传与控制契约测试，不是V4实际回答。

初次8项用例在旧代码上得到1通过、5失败、2错误，检出了V4格式错误、首token前trace缺失、异常trace残留、固定返回字段缺失、重复ID与debug stop等待等问题；原始before日志保留。修正并扩展后，14项backend CPU用例通过；与原27项Store/loader用例合跑共41项通过，实际日志为 `docs/evidence/v4-native-backend-p3/all-v4-with-backend.log`。旧12项Store回归的源码未改，沿用上一里程碑日志，未重复包装成新运行。

非V4的role拼接、debug history查询和shutdown时对debug记录的abort语义专门保留并用CPU用例覆盖。完整旧pytest套件依赖真实Torch/vLLM等，本轮未执行；不能据这批替身测试声称Kimi/GLM完整实验回归通过。

## 请求与证据边界

后续实验控制层可把冻结 `request.json` 中的messages与sampling组成请求，另显式加 `return_token_ids=true`。冷业务请求仍自然EOS，固定32token请求仍独立且不能填写业务正确完成事件。backend只透传输出与结束原因，不自动将“无异常”“prefix token一致”或返回4判作完整业务成功；实际语义/EOS/token一致性和外部monotonic计时仍由控制与验收层记录。

本批没有移植完整GLM phase trace、Controller/router删除、native worker/quarantine清理或服务启动watchdog。尤其不能将backend.shutdown返回替代Ray actor/所有子进程退出及至少60秒物理释放证明。同步构造AsyncLLM时的启动中断需要外部控制/进程守护，未盲目改为线程执行可能依赖event-loop的constructor。

完整Pro单slab TP8恢复仍由容量guard拒绝；新的backend接点没有绕过该拒绝。工具输出结构、thinking、MTP、长上下文与在途KV物理迁移不在当前冻结场景的已验收范围。整个迁移目标保持未完成。
