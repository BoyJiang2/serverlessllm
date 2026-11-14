"""Generate matching tables and figures from a verified campaign export."""
import argparse
import json
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FIGURE_TITLES = ("Serial pool initialization", "Switch to correct response",
                 "Host KV rebuild", "Eight-GPU memory around teardown",
                 "Weight Store and service processes", "Host KV around clear",
                 "Final residency and GPU release windows")

def render(output, name, title, labels, series):
    fig, ax = plt.subplots(figsize=(max(8, min(len(labels) * .55, 24)), 4), layout="constrained")
    for label, values in series:
        ax.plot(range(len(labels)), values, marker=".", label=label)
    ax.set_title(title)
    ax.set_xticks(range(len(labels)), labels, rotation=90)
    ax.legend()
    ax.grid(axis="y", alpha=.25)
    fig.savefig(output / name, dpi=150)
    plt.close(fig)


def section(output, number, title, unit, labels, series):
    name = f"figure-{number}.png"
    render(output, name, f"{FIGURE_TITLES[number - 1]} ({unit})", labels, series)
    headers = ["样本"] + [key for key, _ in series]
    lines = [f"## {number}. {title}（{unit}）", "",
             "| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for i, label in enumerate(labels):
        values = [v[i] for _, v in series]
        lines.append("| " + " | ".join([label] + [
            "—" if x is None else f"{x:.3f}" for x in values]) + " |")
    return "\n".join(lines + ["", f"![{title}]({name})"])


def by_model(steps, columns):
    rows = ["| 模型 | " + " | ".join(name for name, _ in columns) + " |",
            "| --- | " + " | ".join("---:" for _ in columns) + " |"]
    for key, model in (("K", "Kimi-K2.6"), ("G", "GLM-5.1-FP8"), ("D", "DeepSeek-V4-Pro")):
        cells = []
        for _, metric in columns:
            values = [value for step in steps if step["switch"]["to_model"] == key
                      and (value := metric(step)) is not None]
            cells.append(f"{median(values):.3f}（n={len(values)}）" if values else "—")
        rows.append(f"| {model} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def main(snapshot, output):
    data = json.loads(snapshot.read_text(encoding="utf-8-sig"))
    rounds = data["rounds"]
    if not rounds:
        raise SystemExit("No verified formal rounds")
    output.mkdir(parents=True, exist_ok=True)
    labels = [f"{r['sequence']}-r{r['repeat']}" for r in rounds]
    steps = [s for r in rounds for s in r["steps"]]
    stages = [f"{label}/{i + 1}-{s['switch']['to_model']}"
              for label, r in zip(labels, rounds) for i, s in enumerate(r["steps"])]
    store_times = [s["eviction_and_host_restore_envelope_s"] for s in steps
                   if s["eviction_and_host_restore_envelope_s"] is not None]
    kv_times = [s["request_to_last_store_log_s"] for s in steps]
    init_times = [r["init_wall_s"] for r in rounds]
    report = [
        "# V11 三模型大池实验",
        f"已纳入 {len(rounds)}/9 轮、{len(steps)}/63 段。qjhs003，TP8，"
        "864 GiB 权重 Store，813 GiB Host KV，512-token 填充。",
        "S1 以 Kimi 首尾，S2 以 GLM 首尾，S3 以 V4 首尾。每轮首段是通过冷页准入的 SafeTensors 启动，后六段使用该轮常驻 Store。",
        section(output, 1, "双池串行初始化", "s", labels, [
            ("Host KV", [r["host_kv_init_s"] for r in rounds]),
            ("Weight Store", [r["weight_store_init_s"] for r in rounds]),
            ("Sum", [r["init_sum_s"] for r in rounds]),
            ("Wall clock", [r["init_wall_s"] for r in rounds])]),
        f"双池串行启动至就绪的墙钟范围为 **{min(init_times):.3f}–{max(init_times):.3f} 秒**。",
        section(output, 2, "切换与首个正确响应", "s", stages, [
            ("SF activation to correct", [s["activation_to_first_correct_business_s"]
             if not s["switch"]["t0_ns"] else None for s in steps]),
            ("Switch to correct", [s["switch_to_first_correct_business_s"] for s in steps]),
            ("Activation to engine ready", [
                (s["engine_ready_ns"] - int(s["switch"]["t2_ns"])) / 1e9 for s in steps]),
            ("Eviction event envelope", [s["eviction_event_envelope_s"] for s in steps]),
            ("Store event envelope", [s["eviction_and_host_restore_envelope_s"] for s in steps])]),
        f"已记录的 {len(store_times)} 次 Store 恢复合并区间为 "
        f"**{min(store_times):.3f}–{max(store_times):.3f} 秒**。"
        "该区间使用 Store 事件首尾，包含期间的等待，驱逐和恢复的重叠部分只计一次。",
        "下表按目标模型汇总中位数。冷启动从本段激活计时，Store 切换从切换请求计时，分别保留样本数。",
        by_model(steps, [
            ("SF 激活至正确响应（s）", lambda s: s["activation_to_first_correct_business_s"]
             if not s["switch"]["t0_ns"] else None),
            ("Store 切换至正确响应（s）", lambda s: s["switch_to_first_correct_business_s"]),
            ("Store 驱逐与恢复区间（s）", lambda s: s["eviction_and_host_restore_envelope_s"])]),
        "| 样本 | 文件读取 GiB | 权重命中 GiB | 驱逐路径 | 释放块 | 分配块 |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: |\n" + "\n".join(
            f"| {label} | {int(row['file_read_bytes']) / 2**30:.3f} | "
            f"{int(row['hit_bytes']) / 2**30:.3f} | {row['evicted_paths']} | "
            f"{row['freed_blocks']} | {row['allocated_blocks']} |"
            for label, row in zip(stages, (s["switch"] for s in steps), strict=True)
            if row["t0_ns"]),
        "文件读取量来自 Store 计数，包含可能由 Linux 页缓存满足的读取。",
        section(output, 3, "Host KV 重建", "s", stages, [
            ("Fill request to STORE log", [s["request_to_last_store_log_s"] for s in steps]),
            ("Activation to first nonzero sample", [
                (s["host_kv"]["first_write_ns"] - int(s["switch"]["t2_ns"])) / 1e9
                if s["host_kv"]["first_write_ns"] is not None else None for s in steps])]),
        f"填充请求到 STORE 完成日志的范围为 **{min(kv_times):.3f}–{max(kv_times):.3f} 秒**。"
        f"{len(steps)} 段均提交 512 token，查询零命中，清理后归零。",
        by_model(steps, [("填充请求至最后一条 STORE 完成日志中位数（s）",
                          lambda s: s["request_to_last_store_log_s"])]),
        "| 样本 | STORE 提交 token | 查询 token | 命中 token | 清理后字节 |\n"
        "| --- | ---: | ---: | ---: | ---: |\n" + "\n".join(
            f"| {label} | {kv['committed_tokens']} | {kv['lookup_delta']['requested']:.0f} | "
            f"{kv['lookup_delta']['hits']:.0f} | {kv['used_after_clear']} |"
            for label, kv in zip(stages, (s["host_kv"] for s in steps), strict=True)),
        section(output, 4, "销毁前后八卡显存", "GiB", stages, [
            (phase, [s["residency_metrics"][phase]["device_hbm_mib"] / 1024 for s in steps])
            for phase in ("before_backend_exit", "after_exit_before_clear", "after_clear")]),
        section(output, 5, "模型退出并清理后 RAM 驻留", "GiB", stages, [
            ("Store payload", [s["residency"]["weight_pool"]["resident_payload_bytes"] / 2**30
                               for s in steps]),
            *[(f"{role} {metric[:-4].upper()}", [
                value / 2**20 if (value := s["residency_metrics"]["after_clear"]
                                  ["processes"][role][metric]) is not None else None
                for s in steps])
              for role in ("store", "lmcache") for metric in ("rss_kib", "pss_kib")]]),
        section(output, 6, "清理前后 Host KV 数据占用", "MiB", stages, [
            (phase, [s["residency"][f"host_kv_{phase}"]["memory_used_bytes"] / 2**20 for s in steps])
            for phase in ("before_clear", "after_clear")]),
        section(output, 7, "末段常驻与八卡归零观察", "s", labels, [
            ("Resident services after clear", [
                (r["final_residency"][-1]["ns"] -
                 r["steps"][-1]["residency"]["after_clear"]["ns"]) / 1e9 for r in rounds]),
            ("All GPUs zero after service exit", [r["release_seconds"] for r in rounds])]),
        "末段常驻窗口从清理后快照算到服务退出前最后一次采样。"
        "八卡归零窗口取服务退出后的独立采样。",
        "八卡显存为设备总量。权重及 KV 的显存分项缺少独立归因，导出中留空。"
        "原始导出逐段保留 RAM 权重、Host KV、进程 PID/RSS/PSS 和释放记录。",
        "清理后的驻留中位数按本段销毁的模型分组。Store 有效载荷也受此前切换留下的权重影响。",
        by_model(steps, [
            ("八卡 HBM（GiB）", lambda s: s["residency_metrics"]["after_clear"]["device_hbm_mib"] / 1024),
            ("RAM Store 有效载荷（GiB）", lambda s: s["residency"]["weight_pool"]["resident_payload_bytes"] / 2**30),
            ("RAM Host KV 数据（MiB）", lambda s: s["host_kv"]["used_after_clear"] / 2**20)]),
        "## 原始运行\n\n" + "\n".join(f"- `runs/{r['run']}/`" for r in rounds),
        "S1 第 3 轮首次尝试 `runs/B1-S1-r3-20261114T013711Z/` 中，"
        "第 5 段容器正常退出后被 Docker prune 清除，控制器读取退出状态失败。"
        "该尝试单独保留，八卡归零观察为 60.786 秒。"
        "正式 S1-r3 采用修复退出回执校验后的完整重跑。短测证据保留在原运行目录。",
        "重算时在 qjhs003 实验目录运行 `python3 collect_campaign.py . > formal.json`，"
        "然后在有 Matplotlib 的环境运行 `python report_campaign.py formal.json report`。",
    ]
    (output / "REPORT.md").write_text("\n\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.snapshot, args.output)
