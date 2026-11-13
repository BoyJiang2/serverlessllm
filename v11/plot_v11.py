from pathlib import Path
import csv
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
A = ROOT / "runs/A-seq-7-agents-r1-20261113T035857Z"
B1 = ROOT / "runs/B1-seq-7-agents-r1-20261113T052724Z"
B2 = ROOT / "runs/B2-seq-7-agents-r1-20261113T012344Z"
L1 = ROOT / "runs/B2-B2-smoke-r1-20261111T113723Z"


def render():
    out = ROOT / "figures"
    out.mkdir(exist_ok=True)
    a, b = [list(csv.DictReader((run / "switch-events.csv").read_text().splitlines()))
            for run in (A, B1)]
    models = {"K": "Kimi", "G": "GLM", "D": "V4"}
    assert [row["to_model"] for row in a] == [row["to_model"] for row in b] == list("KGDGKDK")
    labels = [f"{i + 1} · {models[row['to_model']]}" for i, row in enumerate(a)]
    qa, qb, ql = [json.loads((run / "qualification.json").read_text())
                  for run in (A, B1, B2)]
    assert all(q["passed"] and q["final_all_gpu_zero"] and q["final_release_seconds"] >= 60
               for q in (qa, qb, ql))

    fig, ax = plt.subplots(figsize=(10, 3.8), layout="constrained")
    bars = ax.bar(labels, [float(row["activation_to_correct_seconds"]) for row in a],
                  color="#406795")
    ax.scatter(labels[1:], [float(row["switch_excluding_observation_seconds"]) for row in a[1:]],
               color="#d28045", marker="D", label="Switch excluding observation", zorder=3)
    bars.set_label("Activation to first correct response")
    ax.set(title="A · Activation and switch timing", ylabel="Seconds", ylim=(0, 630))
    ax.legend(loc="upper right", frameon=False)
    fig.savefig(out / "a-activation.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(3, 1, figsize=(10, 7), sharex=True, layout="constrained")
    axes[0].bar(labels, [int(row["file_read_bytes"]) / 2**30 for row in a], color="#406795")
    axes[0].set(ylabel="GiB", title="Store weight bytes read from files")
    for field, color, name in (("allocated_blocks", "#406795", "Allocated"),
                               ("freed_blocks", "#d28045", "Freed")):
        axes[1].plot(labels, [int(row[field]) for row in a], marker="o", color=color, label=name)
    axes[1].set(ylabel="Blocks", title="Store pool block turnover")
    axes[1].legend(frameon=False)
    axes[2].bar(labels, [int(row["evicted_paths"]) for row in a], color="#387d72")
    axes[2].set(ylabel="Paths", title="Evicted Store paths")
    fig.savefig(out / "a-store.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, layout="constrained")
    for ax, field, unit, scale in (
        (axes[0], "activation_to_correct_seconds", "Seconds", 1),
        (axes[1], "file_read_bytes", "GiB", 2**30),
    ):
        for rows, offset, color, name in ((a, -.19, "#406795", "A"), (b, .19, "#d28045", "B1 · LMCache 0.5.6")):
            ax.bar([i + offset for i in range(7)], [float(row[field]) / scale for row in rows],
                   .36, color=color, label=name)
        ax.set_ylabel(unit)
        ax.grid(axis="y", alpha=.25)
        ax.set_axisbelow(True)
    axes[0].set_title("Activation to first correct response")
    axes[0].set_ylim(top=600)
    axes[0].legend(loc="upper right", frameon=False)
    axes[1].set_title("Weight Store bytes read from files")
    axes[1].set_xticks(range(7), labels)
    fig.savefig(out / "a-b1-sequence.png", dpi=160)
    plt.close(fig)

    steps = qb["b1_host_kv"]["steps"]
    assert len(steps) == 7 and all(s["closed"] and s["zero_hit"] and s["used_after_clear"] == 0 for s in steps)
    fig, ax = plt.subplots(figsize=(10, 3.8), layout="constrained")
    ax.bar(labels, [s["peak_bytes"] / 2**20 for s in steps], color="#387d72", label="Observed peak")
    ax.scatter(labels, [s["used_after_clear"] / 2**20 for s in steps],
               marker="D", color="#ad542e", label="After clear", zorder=3)
    ax.set(title="B1 Host KV occupancy", ylabel="MiB", ylim=(-2, 60))
    ax.legend(frameon=False, ncols=2)
    fig.savefig(out / "b1-host-kv.png", dpi=160)
    plt.close(fig)

    records = ql["results"][0]["records"]
    assert [r["model"] for r in records] == list("KGDGKDK")
    stages = [r["stage"] for r in records]
    durations = [stage["seconds"] for stage in stages]
    fig, ax = plt.subplots(figsize=(10, 3.8), layout="constrained")
    for kind, color, name in (("load", "#d28045", "First load"),
                              ("wake", "#406795", "Wake on original workers")):
        indexes = [i for i, stage in enumerate(stages) if stage["kind"] == kind]
        bars = ax.barh(indexes, [durations[i] for i in indexes], color=color, label=name)
        ax.bar_label(bars, fmt="%.1f s", padding=5)
    ax.set_yticks(range(7), labels)
    ax.set_xlim(0, max(durations) * 1.17)
    ax.invert_yaxis()
    ax.legend(loc="upper right", frameon=False)
    ax.set(title="B2-L2 first load and wake", xlabel="Host stage acknowledgement (seconds)")
    fig.savefig(out / "b2-l2-wake.png", dpi=160)
    plt.close(fig)

    first, wake = json.loads((L1 / "L1/level-result.json").read_text(encoding="utf-8"))["records"]
    fig, ax = plt.subplots(figsize=(8, 2.8), layout="constrained")
    bars = ax.barh(["First sleep", "Wake"], [first["sleep_seconds"], wake["stage"]["seconds"]],
                   color=["#406795", "#387d72"])
    ax.bar_label(bars, fmt="%.3f s", padding=5)
    ax.set(title="B2-L1 · DeepSeek-V4-Pro single-model sleep and wake", xlabel="Seconds",
           xlim=(0, first["sleep_seconds"] * 1.2))
    ax.invert_yaxis()
    fig.savefig(out / "b2-l1-timing.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    render()
