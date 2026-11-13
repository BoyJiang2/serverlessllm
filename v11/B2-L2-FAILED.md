# B2-L2 未通过：两条权重重载路径都有原生证据

DeepSeek-V4-Pro + 固定引擎（vLLM 0.20.1 @132765e）下，Sleep Level 2 的 **Sleep 本身成功**，
但 **Wake 无法恢复权重**。两条路径都试过：

## 路径 A：`collective_rpc('reload_weights', is_checkpoint_format=True)`

run `B2-B2-smoke-r1-20261111T115102Z`：

- Sleep L2：8 rank，`cpu_backup_bytes = 0`，`sleeping = true`（正确释放，无 CPU 备份）；
- Wake：逐层报
  `WARNING [layerwise.py:225] DeepseekV4Attention / DeepseekV4MLAAttention / LayerNorm: Failed to load weights`，
  随后 `wake_up(tags=['kv_cache'])` 在 `cumem.py create_and_map` 处
  `CUDA Error: out of memory`。

## 路径 B：`collective_rpc('reload_weights', is_checkpoint_format=False)`（kernel 格式）

run `B2-B2-smoke-r1-20261112T005532Z`：

- Sleep L2 同样成功（ack-0001：`sleeping = true`，备份 0）；
- Wake 时 worker 直接退出：
  `AttributeError: DeepseekV4ForCausalLM has no attribute 'embed'`
  （`gpu_model_runner.py:5031 param = model.get_parameter(name)`），
  说明 kernel 格式重载按 checkpoint 名字找参数，与 V4 的参数命名不匹配。

## 结论

在本机 + 固定引擎上，**B2-L2 对 V4 不可用**，因此按协议记"未通过"，不给任何 L2 的时延/显存数字。
B2 的可用证据只有 L1（单模型全周期通过：880.9 GB CPU 备份、PID 跨 Sleep/Wake 不变；两模型因 RAM
上限不可行，见 `B2-L1-RAM-BOUNDARY.md`）。

复现：

```bash
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11
python3 v11_run_b2.py --models DD --levels 2 --reload-mode checkpoint   # 路径 A
python3 v11_run_b2.py --models DD --levels 2 --reload-mode kernel       # 路径 B
```

## 16 GiB／rank KV 池诊断（`B2-B2-smoke-r1-20261112T160706Z`）

固定 checkpoint 路径。首轮金标通过。八 rank 在 Sleep、权重映射、重载返回和 KV 唤醒回执中的进程身份一致。KV 唤醒成功返回（`ack-0002.json`），但唤醒后的第一个金标请求在 CUDA 中触发 illegal instruction，错误报告位置为 indexer compressor 的 router GEMM。CUDA 异步执行使该栈不足以确定最先出错的 kernel。整轮未通过，最终八卡归零采样跨度 59.093 秒。

## 诊断启动失败

两次均在权重加载前退出。

| 运行编号 | 原始失败 | 八卡归零采样跨度 |
| --- | --- | ---: |
| `B2-B2-smoke-r1-20261113T001803Z` | 补丁源文件未匹配原始源码哈希清单，插件初始化拒绝加载 | 59.870 s |
| `B2-B2-smoke-r1-20261113T001929Z` | 诊断清单与 profile 原清单挂载目标重复，Docker 拒绝创建容器 | 59.889 s |
