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
    wakes = [(labels[i], r["stage"]) for i, r in enumerate(records) if r["stage"]["kind"] == "wake"]
    fig, ax = plt.subplots(figsize=(10, 3.8), layout="constrained")
    bars = ax.barh([label for label, _ in wakes], [s["seconds"] for _, s in wakes], color="#406795")
    ax.bar_label(bars, fmt="%.1f s", padding=5)
    ax.set_xlim(0, max(s["seconds"] for _, s in wakes) * 1.17)
    ax.invert_yaxis()
    ax.set(title="B2-L2 wake on the original workers", xlabel="Wake acknowledgement (seconds)")
    fig.savefig(out / "b2-l2-wake.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    render()
