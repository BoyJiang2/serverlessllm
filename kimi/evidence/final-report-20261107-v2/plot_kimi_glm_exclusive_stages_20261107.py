"""Plot verified exclusive intervals, not overlapping loader/engine phases."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage_input", type=Path)
    ap.add_argument("outputdir", type=Path)
    args = ap.parse_args()
    raw = args.stage_input.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "0593266d8e924e5fc21b98e32e48fe97eb7d2f16e9d6e4a31db1741bb6169a04"
    data = json.loads(raw)
    assert data["status"] == "VERIFIED32_KIMI_CLOCK_OWNER_REQUEST_JOINS_PLUS_FROZEN_GLM24_PRIMARY"
    conditions = ("safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit")
    names = ("SF disk cold", "SF disk hot", "Store disk cold", "Store pool hit")
    keys = data["segment_order"]
    assert keys == ["preparation_s", "preparation_to_startup_gap_s", "command_to_route_s",
                    "route_to_backend_first_token_s", "backend_first_token_to_correct_http_s"]
    labels = ("Target preparation", "Preparation-to-register gap", "Register-to-route",
              "Route-to-backend first output", "Backend first output-to-correct HTTP")
    colors = ("#BBBBBB", "#DDAA33", "#4477AA", "#228877", "#AA66AA")
    expected = {"Kimi-K2.6": [1, 2, 3, 5, 6, 7, 8, 10], "GLM-5.1-FP8": list(range(5, 11))}
    for model, blocks in expected.items():
        assert set(data["models"][model]) == set(conditions)
        for c in conditions:
            d = data["models"][model][c]
            assert d["blocks"] == blocks and d["n"] == len(blocks)
            assert len(d["run_ids"]) == len(set(d["run_ids"])) == len(blocks)
            assert all(len(d["segments_s"][k]) == len(blocks) for k in keys)
            assert all(type(v) in (int, float) and math.isfinite(v) and v >= 0
                       for k in keys for v in d["segments_s"][k])
            assert len(d["total_values_s"]) == len(blocks)
            for i, total in enumerate(d["total_values_s"]):
                assert math.isclose(sum(d["segments_s"][k][i] for k in keys), total, rel_tol=1e-12)
            for k in keys:
                assert math.isclose(statistics.fmean(d["segments_s"][k]), d["mean_segments_s"][k], rel_tol=1e-12)
            assert math.isclose(statistics.fmean(d["total_values_s"]), d["mean_total_s"], rel_tol=1e-12)
    files = [args.outputdir / ("fig6_exclusive_observed_stages." + ext) for ext in ("png", "pdf", "svg")]
    table, receipt = args.outputdir / "exclusive-small-intervals.csv", args.outputdir / "exclusive-stage-provenance.json"
    assert not any(p.exists() for p in files + [table, receipt]), "Refusing overwrite"
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none", "pdf.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax, model in zip(axes, expected):
        groups = data["models"][model]
        bottom = [0.] * 4
        for k, label, color in zip(keys, labels, colors):
            heights = [groups[c]["mean_segments_s"][k] for c in conditions]
            ax.bar(range(4), heights, bottom=bottom, color=color, label=label)
            bottom = [x + y for x, y in zip(bottom, heights)]
        for i, c in enumerate(conditions):
            vals = groups[c]["total_values_s"]
            ax.scatter([i + (j - (len(vals) - 1) / 2) * .045 for j in range(len(vals))],
                       vals, color="black", s=13, alpha=.65, zorder=3)
            ax.annotate(format(bottom[i], ".1f"), (i, bottom[i]), xytext=(0, 5),
                        textcoords="offset points", ha="center", fontsize=8)
        ax.set_title(model + " (" + str(len(expected[model])) + " complete blocks)")
        ax.set_xticks(range(4), names, rotation=15, ha="right")
        ax.set_ylim(bottom=0); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
        ax.set_ylabel("Condition preparation to correct arithmetic HTTP (s)")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=3, frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, .86))
    fig.text(.5, -.03, "Exclusive contiguous intervals; dots show per-sample totals, labels show mean totals. Linear shared scale.\n"
             "Prep semantics differ; GLM can include prior-pool unload. Common environment/pool initialization and cleanup/release excluded.\n"
             "Backend observation is not client TTFT; HTTP tail is not pure generation. Small intervals: exclusive-small-intervals.csv.",
             ha="center", va="top", fontsize=9)
    args.outputdir.mkdir(parents=True, exist_ok=True)
    for p in files: fig.savefig(p, dpi=200, bbox_inches="tight")
    plt.close(fig)
    with table.open("x", newline="", encoding="utf-8") as f:
        writer = csv.writer(f); writer.writerow(["model", "condition", "n", *keys])
        for model in expected:
            for c in conditions:
                d = data["models"][model][c]
                writer.writerow([model, c, d["n"], *[format(d["mean_segments_s"][k], ".9f") for k in keys]])
    result = {"input_sha256": hashlib.sha256(raw).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Exclusive intervals, no new tests or causal interpretation; source proofs retained in input",
              "outputs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files + [table]}}
    with receipt.open("x", encoding="utf-8") as f: json.dump(result, f, indent=2); f.write("\n")

if __name__ == "__main__":
    main()
