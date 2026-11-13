# B2-L1 跨模型尝试：在 1.9 TiB 主机上撞到 RAM 上限（如实记录）

run：`B2-B2-smoke-r1-20261111T134318Z`（`--models KGKG --levels 1`）
终止方式：**人工中止**（保护共享主机），不是自然结束；本目录不作为"通过"证据。

## 已发生的事实（来自本目录的采样与 ack）

| 事件 | 实测 |
|---|---|
| K 原生加载 + 答题 | 通过（`worker-K/ack-0000.json`，金标全过） |
| K Sleep L1 | 通过：8 rank，`cpu_backup_bytes = 622,250,164,224`（622 GB），`sleeping = true`，worker PID `1829905` |
| G 原生加载 + 答题 | 通过（`worker-G/ack-0000.json`，金标全过） |
| G Sleep L1（第 2 份备份） | 写入过程中宿主 `MemAvailable` 掉到 **0.3 GiB** |
| 之后 | 人工停止 worker 与控制器，`MemAvailable` 恢复到 1.59 TiB |

`resource-samples.jsonl` 里的原值（1,944 个采样）：

```
startup      mem_available 1877.1 GiB
L1-00-K      mem_available 1863.3 GiB  (K 权重在 GPU)
L1-01-G      mem_available  952.2 GiB  (K 备份 622 GB 已占)
L1-01-G ...  mem_available    0.3 GiB  (G 备份 762 GB 写入中)
```

## 结论（写入报告的口径）

在本机 1.9 TiB RAM 上，**L1 只能容纳"一份大模型权重备份 + 当前活动模型"**：

- 单模型睡眠/唤醒已验证通过（V4：880.9 GB 备份、PID 跨 Sleep/Wake 不变；Kimi：622 GB 备份）；
- 两个模型同时睡下需要 622 + 762 = 1.38 TB 匿名内存，加上加载 checkpoint 留下的页缓存，
  会把 `MemAvailable` 压到 0.3 GiB —— 因此**三模型 L1 campaign 在本机不可行**，
  这不是"没跑"，而是实测到的容量边界。

协议里"多个超大模型权重备份总量可能超过可用 RAM，必须如实记录"指的就是这种情况。

## 没有做的事

- 没有为了"跑完"而强杀 sleeping worker 再重新加载冒充 Wake；
- 没有把这份中止的 run 计入 B2-L1 的通过统计；
- 没有在 RAM 紧张时继续压测（会触发宿主级 OOM，可能影响同机其它任务）。

## 原始回执复核

V4 单模型 `113723Z` 的八 rank PID 在两次 Sleep 与中间 Wake 一致。两次 Sleep 各记录 `880,921,280,512 B`（820.422 GiB），Wake 后为 0。Kimi → GLM 的 `134318Z` 在 GLM 阶段最低 `MemAvailable=339,124,224 B`（0.316 GiB），第二份备份没有完成。
