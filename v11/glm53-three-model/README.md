# GLM-5.3 三模型复现实验

本目录保存 GLM-5.3 替换 GLM-5.1-FP8 后的新增证据。硬件为 qjhs003 8×H20，TP8。B1 使用 864 GiB 权重 Store 与 813 GiB LMCache Host KV 池。正式联合 B2 使用 **813 GiB LMCache Host KV 单池**，前三段从 SafeTensors 冷页首载，后四段验证原 Worker Sleep L2 唤醒与 Host KV 回读。384 GiB 先行轮次独立留证。[执行交接](B2-HANDOVER.md) · [当前 813 GiB CPU 前检与冻结哈希](derived/b2-nostore-813-reference-preflight.json) · [容量变更补丁](diagnostics/joint-b2-nostore-813.patch) · [384 GiB 前检](derived/b2-nostore-384-preflight.json)

**正式 B2 的 S1、S2、S3 各通过三轮，共 9/9 轮、63/63 段。** 27 次首载和 36 次回访均通过金标。每次回访均在原 Worker 实际回读 512 token，删键后均为零命中。九轮均通过至少 60 秒八卡归零观察，813 GiB 整池 CUDA 注册均观察到降级。[正式 B2 报告](B2-JOINT.md) · [B2 逐段 CSV](derived/b2-segments.csv) · [B2 原始证据索引](derived/b2-evidence-index.json)

**B1 已通过 9/9 轮、63/63 段。** S1、S2、S3 各 3 轮，包含历史 r1 三轮和本批 r2/r3 六轮。九轮均观察到 813 GiB Host KV 整池 CUDA 注册失败，降级后仍通过逐段写入、清理及八卡释放验收。双池开轮准入要求 1871 GiB 可用 RAM。[B1 正式报告](REPORT.md) · [B1 服务器原始证据索引](derived/evidence-index.json)

S3 重试首段的 [系统盘、cgroup 与 rank 原始采样](raw/host-memory/B1-GLM53-S3-20261119T175803Z-root-io.jsonl) 共 180 条，SHA-256 `b32ef5b03abe4dd8940d1670ed678293a8198e8961c90ff4e90ba8527a86d070`。服务器原件位于 `qjhs003:/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/B1-GLM53-S3-20261119T175803Z-root-io.jsonl`。

## 从原始回执重算 B1

在 qjhs003 的仓库根目录执行。列表依次为 r1、r2、r3 各三轮，末四项为保留的失败轮。

```bash
base=/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b1-runs
runs=(
  B1-GLM53-S1-20261119T141542Z B1-GLM53-S2-20261119T154121Z B1-GLM53-S3-20261119T175803Z
  B1-GLM53-S1-20261120T232702Z B1-GLM53-S2-20261121T021542Z B1-GLM53-S3-20261121T042117Z
  B1-GLM53-S1-20261121T070054Z B1-GLM53-S2-20261121T082711Z B1-GLM53-S3-20261121T094530Z
  B1-GLM53-S1-20261119T134935Z B1-GLM53-S3-20261119T165820Z
  B1-GLM53-S1-20261120T230829Z B1-GLM53-S3-20261121T033218Z
)
for j in "${!runs[@]}"; do runs[$j]="$base/${runs[$j]}"; done
out=$(mktemp -d)
python3 -B v11/glm53-three-model/recompute_b1.py "${runs[@]}" --output "$out/derived"
cp v11/glm53-three-model/derived/evidence-index.json "$out/derived/evidence-index.json"
python3 -B v11/glm53-three-model/plot_b1.py "$out/derived/b1-segments.csv" "$out/figures/b1-activation.png"
python3 -B v11/glm53-three-model/render_b1.py "$out/derived/b1-recomputed.json" "$out/REPORT.md"
```

复算应返回 9 轮合格、4 轮失败。正式 CSV 有 63 行，按同一序列、段号和缓存状态计算中位数与范围。用 `index_evidence.py "${runs[@]}"` 可重新核对各轮主目录文件的大小和 SHA-256。正式索引还记录 r2/r3 的主机采样、控制器、启动及退出回执，六轮各有 21 份原始回执可在 `raw/<run_id>/` 直接核对。主机端激活时间指放行激活到首个正确完整业务响应，完整权重加载、池初始化和释放观察按各自端点计量。

## 从原始回执重算 Kimi 重载对照

```bash
python v11/glm53-three-model/recompute_kimi_l2.py BASELINE_RUN CANDIDATE_RUN --output derived/kimi-l2.json
```

每份 Kimi 数据来自同一 1 GiB Host KV 配置的独立单模型 Sleep L2。脚本核对镜像和冻结输入、原八 rank 身份、实际补丁导入、三次探针命中、H2D、金标和释放。checkpoint 重载区间使用各轮原始单调端点，保留每个专家层在八 rank 的完成时点。`current_peak` 的字段来自同一采样点，`max_bytes` 逐字段取峰。[服务器原始证据索引](derived/kimi-l2-evidence.json)

TP 切片前移和 meta 计数分别有源码及 CPU 对照。联合 B2 容量采样使用只读程序记录两池与三个 Worker cgroup。[TP 切片补丁](diagnostics/kimi-l2/localize-wna16-transpose.patch) · [TP 切片 CPU 对照](diagnostics/kimi-l2/verify_cpu.py) · [meta 计数补丁](diagnostics/kimi-l2/wna16-count-meta-only.patch) · [meta 计数 CPU 对照](diagnostics/kimi-l2/verify_meta_cpu.py) · [联合容量采样](diagnostics/observe_pool_memory.py)

## 从原始回执重算 GLM-5.3 Sleep L2

```bash
python v11/glm53-three-model/recompute_glm53_l2_memory.py GLM53_RUN_DIR --output derived/glm53-l2-memory.json
```

`GLM53_RUN_DIR` 使用原始证据索引中的 qjhs003 目录。脚本核对两个 KV 分组、原八 rank 的实际 H2D、金标与释放，并按各自端点切分 RAM 采样。正式 S1 的容量生成器和 B1 原始水位重算脚本一并归档。[GLM-5.3 证据索引](derived/glm53-l2-memory-evidence.json) · [容量生成器](diagnostics/make_phase_capacity.py) · [B1 水位重算](diagnostics/replay-b1-host-capacity-20261120.py)

## 从原始回执重算联合 B2

在 qjhs003 的仓库根目录重算。以下 run ID 依次为 r1、r2、r3 各三轮，S3 的 `B2-GLM53-S3-20261121T220257Z` 单独保留在失败档案。若服务器缺图表依赖，可将复算 JSON 和索引复制到有 Matplotlib 的环境再渲染。

```bash
base=/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b2-runs
trace=/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/joint-b2-control.OtdU9n/formal-nostore-813-reference
ids=(
  B2-GLM53-S1-20261120T163053Z B2-GLM53-S2-20261120T190052Z B2-GLM53-S3-20261120T213052Z
  B2-GLM53-S1-20261121T123640Z B2-GLM53-S2-20261121T141943Z B2-GLM53-S3-20261121T160117Z
  B2-GLM53-S1-20261121T183659Z B2-GLM53-S2-20261121T202330Z B2-GLM53-S3-20261121T222136Z
)
out=$(mktemp -d)
runs=()
results=()
for id in "${ids[@]}"; do
  run="$base/$id"
  result="$out/$id.json"
  python3 -B v11/glm53-three-model/recompute_joint_b2.py "$run" \
    --host-memory "$trace/$id-host-memory.jsonl" --kimi-log "$run/worker-K-final.log" --output "$result"
  runs+=("$run")
  results+=("$result")
done
python3 -B v11/glm53-three-model/index_evidence.py "${runs[@]}" \
  --recomputed "${results[@]}" --output "$out/b2-evidence-index.json"
python3 -B v11/glm53-three-model/render_joint_b2.py "${results[@]}" --repeats 3 \
  --index "$out/b2-evidence-index.json" --figure "$out/b2-joint-ready.png" \
  --csv "$out/b2-segments.csv" --output "$out/B2-JOINT.md"
```

复算核对首次加载路径、八 rank 身份及 Host KV 传输、Kimi 参考探针、金标和末尾释放。每个序列的运行计划在核对时仅归一化 run ID、原始目录和预约派生的绝对截止时刻。报告分开列首载与回访，按同序列同段位汇总有效 n、中位数和范围，各阶段缺测保留在复算 JSON 和 CSV。九轮的 909 份紧凑原件见 `raw/<run_id>/`，大文件保留在索引所指的 qjhs003 路径。[联合 B2 前检及失败回执](B2-HANDOVER.md)

主机采样用于复算从逐段准入到该段休眠完成的可用 RAM 低点。复算按该轮实际启动的池服务和 Worker 容器 ID 匹配样本，记录参与采样数、最大间隔和低点同一采样的各容器匿名内存。

正式报表复算同时传入 `--kimi-log RUN/worker-K-final.log` 和同轮 `--host-memory`。以原 Kimi Worker 容器 ID 和各次 checkpoint 窗口核对匿名内存采样峰值、文件页 refault 计数及 60 层 × 八 rank 的专家实例完成时点。多个回访的日志按各自单调时间窗分别复算。历史原生和 WNA16 对照由 `render_joint_b2.py --kimi-history` 读取，默认使用仓库中的 `sleep-wna16-sequences/audit.json`。

等 Linux 观测器结束后重算并生成索引。`--recomputed` 依据同轮摘要核对主机采样是否为已读回的完整文件，并把执行目录外的主机采样、控制器和观测器日志纳入索引。Kimi 专家层日志的路径、大小及 SHA-256 与重算摘要逐项核对。

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
