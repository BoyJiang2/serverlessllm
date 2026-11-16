"""Render one B1 run from its per-snapshot Host KV CSV."""
import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot(input_path, output_path):
    with input_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    run_ids = {row["run_id"] for row in rows}
    starts = [int(row["monotonic_ns"]) for row in rows
              if row["event"] == "server_start_before_pool_init"]
    if len(run_ids) != 1 or len(starts) != 1:
        raise ValueError("Expected one run and one observed LMCache server start")
    origin = starts[0]

    def minutes(row, key="monotonic_ns"):
        return (int(row[key]) - origin) / 60e9

    def series(value, scale, clock="monotonic_ns"):
        selected = sorted((minutes(row, clock), int(row[value]) / scale)
                          for row in rows if row[value] and row[clock])
        if not selected:
            return [], []
        return tuple(zip(*selected))

    fig, (kv, mem) = plt.subplots(2, 1, sharex=True, figsize=(12, 5.7),
                                  layout="constrained")
    colors = {"K": "#345ea8", "G": "#266f54", "D": "#915314"}
    models = {"K": "Kimi", "G": "GLM", "D": "V4-Pro"}
    fig.suptitle(f"B1 Host KV  |  {next(iter(run_ids))}", x=.02, ha="left",
                 fontsize=16, fontweight="bold")

    x, values = series("effective_kv_bytes", 2**20)
    kv.scatter(x, values, color="#315da8", s=6, linewidths=0, label="Effective KV")
    clear = [row for row in rows if row["event"] == "after_clear"
             and row["effective_kv_bytes"] == "0"]
    kv.scatter([minutes(row) for row in clear], [0] * len(clear), marker="o", s=35,
               facecolors="white", edgecolors="#127268", label="Zero after clear", zorder=3)
    kv.set_ylabel("Effective KV (MiB)")
    kv.set_ylim(-3, max(values, default=0) * 1.2 + 2)

    x, values = series("pool_configured_bytes", 2**30)
    mem.scatter(x, values, color="#925821", s=13, linewidths=0, label="Configured pool capacity")
    x, values = series("lmcache_rss_bytes", 2**30, "lmcache_memory_ns")
    mem.scatter(x, values, color="#28566b", s=5, linewidths=0, label="LMCache process RSS")
    mem.set_ylabel("Pool and process (GiB)")
    mem.set_xlabel("Minutes since LMCache server start")

    for row in rows:
        event = row["event"]
        if event not in ("pool_ready", "backend_registered"):
            continue
        x = minutes(row)
        color = colors.get(row["model"], "#657384")
        for axis in (kv, mem):
            axis.axvline(x, color=color, linestyle=":", alpha=.6, linewidth=1)
        label = f'{int(row["step"]) + 1} {models.get(row["model"], row["model"])}' if (
            event == "backend_registered") else "Pool ready"
        kv.annotate(label, (x, .93), xycoords=kv.get_xaxis_transform(),
                    xytext=(3, 0), textcoords="offset points", color=color, fontsize=8)
    for axis in (kv, mem):
        axis.grid(axis="y", color="#e1e7ed", linewidth=.8)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(loc="lower left", frameon=False, ncols=2, bbox_to_anchor=(0, 1.00))
    kv.set_xlim(left=0)
    fig.savefig(output_path, dpi=170, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.input, args.output)
