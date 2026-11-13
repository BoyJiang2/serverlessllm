from statistics import mean
import csv
import json
import subprocess

from plot_v11 import A, B2, L1, ROOT, render

MODELS = {"K": "Kimi-K2.6", "G": "GLM-5.1-FP8", "D": "DeepSeek-V4-Pro"}
BASELINE = "79301b2484c7c6b9ed6bd7480355994a7f19ea19"
GIB = 2**30
MIB = 2**20


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def switches(run):
    with (run / "switch-events.csv").open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def table(headers, rows):
    return "\n".join("| " + " | ".join(map(str, row)) + " |"
                     for row in (headers, ["---"] * len(headers), *rows))


def evidence(run, filename, label):
    return f"[{label}]({(run / filename).relative_to(ROOT).as_posix()})"


def main():
    render()
    baseline = subprocess.check_output(
        ["git", "show", f"{BASELINE}:v11/report.md"], cwd=ROOT
    ).decode("utf-8").replace("\r\n", "\n")
    b1_start = baseline.index("## B1 ")
    b1_end = baseline.index("## B2 ", b1_start)
    b1_section = baseline[b1_start:b1_end].strip()
    a = switches(A)
    qa, q2, q1 = [read_json(run / "qualification.json") for run in (A, B2, L1)]
    activations = [float(row["activation_to_correct_seconds"]) for row in a]
    switch_times = [float(row["switch_excluding_observation_seconds"]) for row in a[1:]]
    assert not a[0]["switch_excluding_observation_seconds"]
    assert len({row["store_pid"] for row in a}) == 1
    assert all(row["cache_state"] == "full_miss" and int(row["hit_bytes"]) == 0 for row in a)
    assert all(int(row["evicted_paths"]) > 0 for row in a[1:])
    assert all(abs((int(row["t4_ns"]) - int(row["t2_ns"])) / 1e9
                   - float(row["activation_to_correct_seconds"])) < .00051 for row in a)

    assert qa["checks"]["requests"]
    records = q2["results"][0]["records"]
    assert q2["results"][0]["golds_passed"] and q2["results"][0]["pid_continuity"]
    assert all(row["weights_cpu_backup_bytes"] == 0 for row in records)
    wakes = [row["stage"] for row in records if row["stage"]["kind"] == "wake"]
    assert len(wakes) == 4
    l1 = read_json(L1 / "L1/level-result.json")
    first, wake = l1["records"]
    assert l1["pid_continuity"] and l1["golds_passed"]

    sections = [f"""# V11 三模型切换复现实验报告

## 实验结论

| 路径 | 样本 | 实验结论 |
| --- | --- | --- |
| A · 常驻权重 Store | 固定序列 7 段 | 三模型均返回正确结果。六次切换均发生路径驱逐和权重重读，验证了常驻 Store 的空间复用。 |
| B1 · Store + Host KV | 原版正式轮 7 段 | 七段均观测到 Host KV 实际写入并在清理后归零，沿用原版正式轮结论。 |
| B2-L1 · Sleep + CPU 权重备份 | V4 单模型，1 次唤醒 | 原 Worker 唤醒后通过金标，单份 CPU 权重备份为 {first['weights_cpu_backup_bytes'] / GIB:.2f} GiB。 |
| B2-L2 · Sleep + checkpoint 重载 | 同序列 7 段，其中 4 次唤醒 | 三模型金标通过、PID 连续，记录的 CPU 权重备份为 0。四次唤醒的重载 RPC 占唤醒时间的 {min(x['reload_seconds'] / x['seconds'] for x in wakes):.1%}–{max(x['reload_seconds'] / x['seconds'] for x in wakes):.1%}。 |

## 实验设置

qjhs003，8×H20，TP8。A 与 B2-L2 各取一轮完整七段，顺序均为 Kimi → GLM → V4 → GLM → Kimi → V4 → Kimi。A 使用 vLLM {qa['vllm']}。A 与 B2-L2 最终八卡全零采样分别覆盖 {qa['final_release_seconds']:.3f} 和 {q2['final_release_seconds']:.3f} 秒。A/B2 表格从 1 开始编号，原始记录从 0 开始。B1 沿用原版正式轮及其统计口径。""",
    "## A · 常驻 Store",
    f"""### 数据 1 与图 1 · 激活及切换耗时

激活耗时从本段激活到首个正确完整响应，包含权重恢复、引擎初始化和推理。切换耗时从上一段正确响应到本段正确响应，表中另扣协议观察时间。首段是首次启动，切换列留空。

{table(["段", "模型", "激活至正确（s）", "切换扣除观察（s）"], [
    (i, MODELS[row["to_model"]], row["activation_to_correct_seconds"],
     row["switch_excluding_observation_seconds"] or "—") for i, row in enumerate(a, 1)])}

![图 1 A 激活至首个正确响应](figures/a-activation.png)

七段激活均值 **{mean(activations):.1f} s**，六次切换扣除观察后均值 **{mean(switch_times):.1f} s**。本轮七段均为权重池未命中，适合作为同序列的重读基线。""",
    f"""### 数据 2 与图 2 · 权重读取和空间复用

{table(["段", "模型", "读取（GiB）", "命中（GiB）", "分配块", "归还块", "驱逐路径"], [
    (i, MODELS[row["to_model"]], f"{int(row['file_read_bytes']) / GIB:.2f}",
     f"{int(row['hit_bytes']) / GIB:.2f}", row["allocated_blocks"], row["freed_blocks"],
     row["evicted_paths"]) for i, row in enumerate(a, 1)])}

![图 2 A 权重读取、块周转及路径驱逐](figures/a-store.png)

同一 Store PID 贯穿全序列。以第 4 段 V4 → GLM 为例，驱逐 302 条路径、归还 22,238 块并分配 22,728 块，GLM 重读后返回正确响应。路径数和池块数分别来自 Store 计数，文件读取量不区分 OS 页缓存和物理盘 I/O。

{evidence(A, 'switch-events.csv', 'A 逐段数据')} · {evidence(A, 'qualification.json', 'A 验收回执')}""",
    b1_section,
    f"""## B2 · 原生 Sleep / Wake

### 数据 1 与图 B2-1 · L1 单模型唤醒与主机容量

| 模型 | CPU 权重备份（GiB） | 首次 Sleep（s） | Wake（s） | 唤醒后金标 |
| --- | ---: | ---: | ---: | --- |
| DeepSeek-V4-Pro | {first['weights_cpu_backup_bytes'] / GIB:.2f} | {first['sleep_seconds']:.3f} | {wake['stage']['seconds']:.3f} | 通过 |

![图 B2-1 V4 单模型 Sleep 和 Wake 耗时](figures/b2-l1-timing.png)

V4 单模型同 PID 唤醒成功。该轮最终全零采样为 {q1['final_release_seconds']:.3f} 秒，低于 60 秒严格门槛。1.9 TiB 主机上，Kimi 备份仍驻留时写入 GLM 备份，最低 `MemAvailable` 为 0.316 GiB，因此 L1 的结论限于单模型唤醒。

{evidence(L1, 'L1/level-result.json', 'L1 原始回执')} · [主机容量证据](B2-L1-RAM-BOUNDARY.md)""",
    f"""### 数据 2 与图 B2-2 · L2 首次加载及原 Worker 唤醒

{table(["段", "模型", "动作", "动作至回执（s）", "重载 RPC（s）", "本段 Sleep（s）", "Worker PID"], [
    (i, MODELS[row["model"]], "首次加载" if row["stage"]["kind"] == "load" else "唤醒",
     f"{row['stage']['seconds']:.3f}",
     f"{row['stage']['reload_seconds']:.3f}" if row["stage"]["kind"] == "wake" else "—",
     f"{row['sleep_seconds']:.3f}", row["worker_pid"]) for i, row in enumerate(records, 1)])}

![图 B2-2 B2-L2 首次加载和原 Worker 唤醒耗时](figures/b2-l2-wake.png)

L2 使用 checkpoint 重载，V4 使用独立 overlay。七段 Sleep 均值 **{mean(row['sleep_seconds'] for row in records):.3f} s**，四次 Wake 均值 **{mean(stage['seconds'] for stage in wakes):.1f} s**。两次 Kimi 返回各耗时超过 2,500 s，且耗时主要落在重载 RPC。L2 证明了保留原 Worker 并恢复正确推理的可行性，当前实现的恢复耗时受 checkpoint 重载限制。表中动作耗时止于回执，金标在随后验证，与 A/B1 首个正确响应的时间端点不同。

{evidence(B2, 'qualification.json', 'L2 逐段验收与回执')} · {evidence(B2, 'diagnostic-config.json', 'L2 重载配置')}

## 重算

在仓库根目录运行 `python v11/make_experiment_report.py`。脚本从链接中的 CSV 和 JSON 重算 A/B2 的表格和图，并校验 A 激活时间戳与 B2 金标及 PID 连续性。B1 正文从固定提交读取。"""]
    (ROOT / "report.md").write_text("\n\n".join(sections) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
