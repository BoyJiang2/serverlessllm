# GLM-5.3 三模型复现实验

本目录保存 GLM-5.3 替换 GLM-5.1-FP8 后的新增证据。硬件为 qjhs003 8×H20，TP8。B1 使用 864 GiB 权重 Store 与 813 GiB LMCache Host KV 池。联合 B2 沿用双池容量并验证原 Worker Sleep L2 唤醒。[联合 B2 容量与验收](B2-admission-20261119.md)

[正式 B1 报告](REPORT.md) · [服务器原始证据索引](derived/evidence-index.json)

S3 重试首段的 [系统盘、cgroup 与 rank 原始采样](raw/host-memory/B1-GLM53-S3-20261119T175803Z-root-io.jsonl) 共 180 条，SHA-256 `b32ef5b03abe4dd8940d1670ed678293a8198e8961c90ff4e90ba8527a86d070`。服务器原件位于 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/B1-GLM53-S3-20261119T175803Z-root-io.jsonl`。

## 从原始回执重算 B1

在可读取 qjhs003 原始回执的环境运行。`RUN_DIR` 为该轮证据目录，替换为实际的一个或多个路径。

```bash
python v11/glm53-three-model/recompute_b1.py RUN_DIR --output derived
python v11/glm53-three-model/index_evidence.py RUN_DIR --output derived/evidence-index.json
python v11/glm53-three-model/plot_b1.py derived/b1-segments.csv figures/b1-activation.png
python v11/glm53-three-model/render_b1.py derived/b1-recomputed.json REPORT.md
```

`--include-partial` 将在途轮次的已落盘回执纳入 JSON，正式 CSV 只包含整轮通过的七段。绘图需要至少一轮通过。主机端激活时间指请求激活到首个正确完整业务响应。完整权重加载、池初始化和释放观察按独立端点计量。

## 从原始回执重算 Kimi 重载对照

```bash
python v11/glm53-three-model/recompute_kimi_l2.py BASELINE_RUN CANDIDATE_RUN --output derived/kimi-l2.json
```

每份 Kimi 数据来自同一 1 GiB Host KV 配置的独立单模型 Sleep L2。脚本核对三次探针命中、八 rank H2D、金标和释放。checkpoint 重载区间使用各轮原始单调端点。`current_peak` 的字段来自同一采样点，`max_bytes` 逐字段取峰，两个统计分别展示。

## 从双池采样重算主机 slab

在 qjhs003 将 `S1_TRACE` 和 `S2_TRACE` 替换为 [Store 内存时间线](derived/store-memory-lifecycle.json) 中的路径。命令只读取各轮指定单调端点之前的完整记录，并输出该前缀的 SHA-256。

```bash
python v11/glm53-three-model/recompute_pool_memory.py S1_TRACE 2227199063470316 S2_TRACE 2227895300589156 > derived/store-memory-lifecycle.json
```

Store 内存分配的 CPU 小测在 qjhs003 上用四个 32 MiB 缓冲验证。每种模式三次，记录 CPU 首写耗时、缺页和 `smaps` THP。原始环境和数据在 [`diagnostics/store-thp-cpu`](diagnostics/store-thp-cpu)。

```bash
cd v11/glm53-three-model/diagnostics/store-thp-cpu
cc -std=c11 -O2 -Wall -Wextra -Werror store_thp_probe.c -o /tmp/store_thp_probe
for mode in current thp thp current current thp; do
    /tmp/store_thp_probe "$mode"
done
```
