# qjhs002 S1–S3 接续核验（2026-11-03，未闭环）

保留现有阶段、金标、失败和审阅。唯一构建/服务基线为 /data/brick1/boyjiang/ServerlessLLM，资产复用 /data/brick1/boyjiang/kimi-k2.6。未启动模型、转换或服务，未安装、回滚或修改远端源码。

|阶段|本轮实际核验|尚缺证据|
|---|---|---|
|S1|官方 HEAD 9f50241baa5386e06a9321c51f19a9ef5f964c2b；requirements-worker.txt 固定 vllm==0.11.2；默认 /usr/bin/python3 的 pip show 未找到 vllm/torch/transformers|既有 Kimi 实际解释器、包路径、commit、依赖与模型支持；旧0.9.0.1文档定位。不能据此降级|
|S2|git status 唯一所见差异为 storage.proto 类型 T；Git 模式120000，原内容 ../../proto/storage.proto；现为1994字节普通文件；现文件和目标均 SHA256 95547af27bb76f8acf61fac5692e0b612e8ac57a42d89ffcab2bd029630bbc5b|类型变化原因尚未证明，可能与迁移复制有关；资产完整性、补丁与编译库身份尚待核验；未恢复链接|
|S3|已读 patch.sh；正向dry-run失败一律打印 already applied，存在误报；已读补丁头，涉及 model_loader 注册 serverless_llm 与新增 ServerlessLLMLoader|尚未定位实际安装树，未完成正反干跑及接口比对；未应用/已应用/冲突/不兼容均不能先行判定|

## 当前最小补丁清单

零项已获证据支持的生产修改。先补齐上述证据，再确定官方对应文件的最小修复。patch.sh 的失败分支是已确认的诊断缺口，不能替代实际双向干跑或作为模型适配成功证据。当前未修改此脚本。

## p0 与窗口

已只读读取远端 p0/p0-lifecycle-receipt.json：docker start 10秒超时，inspect 清理502.196739秒超时；torch_cuda_ipc_verified/full_model_verified/native_loader_verified/store_created 均false。此为历史失败收据，不能覆盖后续释放关账记录。

当前迁移来的 p0/p0-spec.json 仍绑定 node=qjhs003、run=kimi-p0-20261102T201641Z-99679319、code_commit=844e56736f0a7f5775ed51a8c878f20cc0b64f4c。不能在002原样复用旧gate、GPU UUID、窗口或批准。

本轮使用 gputime-booking-agent 官方 helper 的只读 snapshot，复用 Windows 既有 token（WSL 默认无token）；确认 bk-20261103T0429-3ecd2dc4，用户boyjiang，用途sllm kimi，h20/qjhs002，2026-11-03 13:00–20:00 +08:00，8 H20，CPU lane disabled。用户已明确允许002继续适配。预约不替代完整预算、资源核验、独立审阅及新守护。

远端多次短SSH出现超时/断连，未取到的输出不记成功。未运行新的40项或R24，GLM原35成功/5失败保持。下一步继续只读定位迁移环境/官方转换加载源码与编译产物，再双向干跑；S1–S3完成前不全量转换。