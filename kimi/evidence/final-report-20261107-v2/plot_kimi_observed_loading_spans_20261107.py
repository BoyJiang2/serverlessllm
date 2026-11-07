"""Observed, non-additive Kimi stage envelopes; no new inference or tests."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

def load(path, expected):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected, "Input SHA mismatch"
    return json.loads(raw)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("analysis", "cost", "loading_audit", "outputdir"):
        p.add_argument(name, type=Path)
    args = p.parse_args()
    a = load(args.analysis, "0959cf17761ec076a07711bd010e8ab4e0f061bb3707b9cd252963fe29758df1")
    e = load(args.cost, "07a44e897eec5d8b6aab2c37cfaeaacedd87201939b37d8ebc308a5e2cbaf247")
    c = load(args.loading_audit, "9f69c5d1dad96abc25098b526d951bfdfe11d0896c18c7e2f5a21dcd956f4de4")
    conditions = ("safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit")
    labels = ("SF disk cold", "SF disk hot", "Store disk cold", "Store pool hit")
    blocks = a["complete_block_ids"]
    assert blocks == [1, 2, 3, 5, 6, 7, 8, 10]
    rows = [r for r in a["runs"] if r["cohort"] == "original" and r["qualified"] and r["block"] in blocks]
    assert len(rows) == 32 and len({r["run_id"] for r in rows}) == 32
    ei = {r["run_id"]: r for r in e["rows"]}
    ci = {r["run_id"]: r for r in c["rows"]}
    assert len(ei) == len(e["rows"]) == len(ci) == len(c["rows"]) == 38
    values = {}
    for condition in conditions:
        rs = sorted([r for r in rows if r["condition"] == condition], key=lambda r: r["block"])
        assert [r["block"] for r in rs] == blocks
        values[condition] = [[], [], [], []]
        for r in rs:
            er, cr = ei[r["run_id"]], ci[r["run_id"]]
            assert er["block"] == cr["block"] == r["block"] and er["condition"] == cr["condition"] == condition
            assert er["in_primary_complete_block"] and cr["primary_complete_block"]
            assert er["elapsed_s"]["A_s"] == r["timing"]["A_s"]
            assert er["elapsed_s"]["D_fixed32_s"] == r["timing"]["D_fixed32_s"]
            for dest, v in zip(values[condition], (r["timing"]["A_s"], er["elapsed_s"]["backend_init_observed_s"],
                               cr["C_MODEL_LOADING_LOG_SPAN_s"], r["timing"]["D_fixed32_s"])):
                assert type(v) in (int, float) and math.isfinite(v) and v > 0
                dest.append(v)
        assert all(len(v) == 8 for v in values[condition])
        for index, key in ((0, "A_s"), (1, "backend_init_observed_s"), (3, "D_fixed32_s")):
            assert math.isclose(statistics.fmean(values[condition][index]),
                   e["primary_complete8_summary"][condition]["costs"][key]["mean_s"], rel_tol=1e-12)
        assert values[condition][2] == c["primary8_complete_blocks_description"][condition]["raw_values_s"]
        assert math.isclose(statistics.fmean(values[condition][2]),
               c["primary8_complete_blocks_description"][condition]["mean_s"], rel_tol=1e-12)
    files = [args.outputdir / ("fig5_observed_stage_envelopes." + ext) for ext in ("png", "pdf", "svg")]
    receipt = args.outputdir / "observed-stage-provenance.json"
    assert not any(p.exists() for p in files + [receipt]), "Refusing overwrite"
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none", "pdf.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    series = (("Register to correct arithmetic HTTP", "#4477AA"),
              ("Observed backend initialization", "#CC6677"), ("Model-loader log span", "#228833"))
    for i, condition in enumerate(conditions):
        for j, (label, color) in enumerate(series):
            x, v = i + (j - 1) * .25, values[condition][j]
            axes[0].bar(x, statistics.fmean(v), .23, color=color, label=label if i == 0 else None)
            axes[0].scatter([x + (n - 3.5) * .012 for n in range(8)], v, color="black", s=12, alpha=.65, zorder=3)
        v = values[condition][3]
        axes[1].bar(i, statistics.fmean(v), .6, color="#4477AA")
        axes[1].scatter([i + (n - 3.5) * .04 for n in range(8)], v, color="black", s=16, alpha=.65, zorder=3)
    for ax in axes:
        ax.set_xticks(range(4), labels, rotation=15, ha="right")
        ax.set_ylim(bottom=0); ax.set_ylabel("Seconds"); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    axes[0].set_title("(a) Independent observed envelopes (n=8 per condition)")
    axes[1].set_title("(b) Ready fixed32: whole HTTP duration (n=8)")
    axes[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.text(.5, -.03, "Bars: arithmetic means; dots: original complete-block samples. Left spans overlap and are not additive; do not subtract them as fixed overhead.\n"
             "Model-loader log span is not isolated weight ingestion or the eight-rank maximum. Right is whole HTTP, not pure decode or per-token latency.",
             ha="center", va="top", fontsize=9)
    args.outputdir.mkdir(parents=True, exist_ok=True)
    for path in files: fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    result = {"scope": "Observed independent envelopes; no new p tests, no memory metrics",
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.analysis, args.cost, args.loading_audit)},
              "complete_blocks": blocks, "raw_values_order": ["A_s", "backend_init_observed_s", "C_MODEL_LOADING_LOG_SPAN_s", "D_fixed32_s"],
              "raw_values_s": values, "outputs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with receipt.open("x", encoding="utf-8") as f: json.dump(result, f, indent=2, allow_nan=False); f.write("\n")

if __name__ == "__main__":
    main()
