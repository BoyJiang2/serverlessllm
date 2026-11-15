from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
A = ROOT / "runs/A-seq-7-agents-r1-20261113T035857Z"
B1 = ROOT / "runs/B1-seq-7-agents-r1-20261113T052724Z"
B2 = ROOT / "runs/B2-seq-7-agents-r1-20261113T012344Z"
L1 = ROOT / "runs/B2-B2-smoke-r1-20261111T113723Z"


def render(data):
    out = ROOT / "figures"
    out.mkdir(exist_ok=True)
    a, b = data["A"], data["B1"]
    labels = [f"{i + 1} {r['model']}" for i, r in enumerate(a)]

    def save(fig, name):
        fig.savefig(out / (name + ".png"), dpi=160)
        plt.close(fig)

    def bars(ax, rows, fields, names):
        for field, name, offset, color in zip(fields, names, (-.19, .19), ("#406795", "#d28045")):
            ax.bar([i + offset for i in range(len(rows))], [r[field] for r in rows],
                   width=.36, label=name, color=color)
        ax.legend(frameon=False, loc="upper right", ncols=2)
        ax.set(xticks=range(len(labels)), xticklabels=labels, ylabel="Seconds",
               ylim=(0, max(r[field] for r in rows for field in fields) * 1.19))

    fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    bars(ax, a, ("first_s", "all_s"), ("First correct response", "All business gold checks"))
    ax.set_title("A 035857Z · Activation to response and full checks")
    save(fig, "a-activation")

    fig, axes = plt.subplots(2, 1, figsize=(10, 6), layout="constrained")
    for ax, rows, title in zip(axes, (a, b), ("A 035857Z", "B1 052724Z")):
        bars(ax, rows, ("first_s", "all_s"), ("First correct response", "All business gold checks"))
        ax.set_title(title)
    save(fig, "a-b1-sequence")

    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True, layout="constrained")
    caches = [r["cache"] for r in a]
    axes[0].bar(labels, [r["file_read_bytes"] / 2**30 for r in caches], color="#406795")
    axes[0].set(title="A 035857Z · Store file bytes read", ylabel="GiB")
    for key, label in (("allocated_blocks", "Allocated"), ("freed_blocks", "Freed")):
        axes[1].plot(labels, [r[key] for r in caches], marker="o", label=label)
    axes[1].set(ylabel="Blocks")
    axes[1].legend(frameon=False)
    save(fig, "a-store")

    fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    kv = data["host_kv"]
    ax.bar(labels, [r["peak_bytes"] / 2**20 for r in kv], color="#387d72", label="Observed peak")
    ax.scatter(labels, [r["used_after_clear"] / 2**20 for r in kv], marker="D", color="#ad542e",
               label="After clear", zorder=3)
    ax.set(title="B1 052724Z · Host KV occupancy", ylabel="MiB",
           ylim=(-2, max(r["peak_bytes"] / 2**20 for r in kv) * 1.18))
    ax.legend(frameon=False)
    save(fig, "b1-host-kv")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
    for ax, key, caption, color in zip(axes, ("l2_load", "l2_wake"),
                                      ("First load to ready", "Wake to acknowledgement"),
                                      ("#d28045", "#406795")):
        rows = data[key]
        bars_ = ax.barh([f"{r['step'] + 1} {r['model']}" for r in rows],
                        [r["stage"]["seconds"] for r in rows], color=color)
        ax.bar_label(bars_, fmt="%.1f s", padding=4)
        ax.margins(x=.25)
        ax.invert_yaxis()
        ax.set(title=f"{caption} (n={len(rows)})", xlabel="Seconds")
    fig.suptitle("B2-L2 012344Z")
    save(fig, "b2-l2-wake")

    first, wake = data["l1"]
    fig, ax = plt.subplots(figsize=(8, 3), layout="constrained")
    bars_ = ax.barh(["First sleep", "Wake"], [first["sleep_seconds"], wake["stage"]["seconds"]],
                    color=["#406795", "#387d72"])
    ax.bar_label(bars_, fmt="%.3f s", padding=5)
    ax.margins(x=.2)
    ax.set(title="B2-L1 113723Z · DeepSeek-V4-Pro", xlabel="Seconds")
    ax.invert_yaxis()
    save(fig, "b2-l1-timing")

    sleep_timing = ROOT / "large-pool-20261114/sleep-timing.json"
    if sleep_timing.is_file():
        for run in json.loads(sleep_timing.read_bytes())["rounds"]:
            if not run["passed"]:
                continue
            steps = run["steps"][1:]
            names = {"K": "Kimi-K2.6", "G": "GLM-5.1-FP8", "D": "DeepSeek-V4-Pro"}
            labels = [f"{row['step'] + 1} {names[row['model']]}\n{row['stage']}" for row in steps]
            fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
            for offset, field, caption in (
                (-.24, "weight_switch_s", "Weight release to weight ready"),
                (0, "kv_switch_s", "KV release to KV ready"),
                (.24, "kv_release_to_service_ready_s", "KV release to backend ready"),
            ):
                observed = [(i + offset, row[field]) for i, row in enumerate(steps)
                            if row[field] is not None]
                if observed:
                    positions, seconds = zip(*observed)
                    ax.bar(positions, seconds, width=.23, label=caption)
            ax.set_xticks(range(len(labels)), labels)
            ax.set(title=f"Sleep L2 {run['sequence']} rep {run['rep']} · {run['run']}",
                   ylabel="Elapsed seconds", ylim=(0, None))
            ax.legend(frameon=False)
            save(fig, f"sleep-{run['run']}")


if __name__ == "__main__":
    render(json.loads((ROOT / "derived/report-data.json").read_bytes()))
