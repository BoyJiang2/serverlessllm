"""Appendix only: original40 Kimi/GLM outcomes across mixed protocol versions."""
import argparse
import hashlib
import json
import math
from pathlib import Path

CONDITIONS = ("safetensors_disk_cold", "safetensors_disk_hot",
              "store_disk_cold", "store_pool_hit")
LABELS = ("SF disk cold", "SF disk hot", "Store disk cold", "Store pool hit")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def cells(rows, kimi):
    require(len(rows) == 40 and {(r["block"], r["condition"]) for r in rows} ==
            {(b, c) for b in range(1, 11) for c in CONDITIONS}, "Expected40 unique original cells")
    result = {}
    for r in rows:
        if kimi:
            success = r["qualified"] is True
            value = r["timing"]["A_s"] if success else None
            category = 0 if success else 2
        else:
            require(r["status"] in ("success", "failed"), "Unexpected GLM status")
            success = r["status"] == "success"
            value = r["A_s"] if success else None
            require(type(r["is_C23"]) is bool, "Missing GLM revision classification")
            category = (0 if r["is_C23"] else 1) if success else 2
        require(not success or (type(value) in (int, float) and math.isfinite(value) and value > 0),
                "Successful cell lacks positive finite A")
        result[r["block"], r["condition"]] = (category, value)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kimi_analysis", type=Path)
    ap.add_argument("glm_all40_ledger", type=Path)
    ap.add_argument("outputdir", type=Path)
    args = ap.parse_args()
    kb, gb = args.kimi_analysis.read_bytes(), args.glm_all40_ledger.read_bytes()
    k, g = json.loads(kb), json.loads(gb)
    require(k["schema"] == "kimi-four-analysis-report-v1", "Kimi schema mismatch")
    require(g["schema"] == "glm-original-all40-ledger-comparison-appendix-v1", "GLM schema mismatch")
    kr = [r for r in k["runs"] if r["cohort"] == "original"]
    kc, gc = cells(kr, True), cells(g["rows"], False)
    require(sum(v[1] is not None for v in kc.values()) == 38, "Kimi expected38/40")
    require(sum(v[1] is not None for v in gc.values()) == 35, "GLM expected35/40")
    stem = "appendix_original40_outcomes"
    files = [args.outputdir / (stem + "." + ext) for ext in ("png", "pdf", "svg")]
    receipt = args.outputdir / "all40-plot-provenance.json"
    require(not any(p.exists() for p in files + [receipt]), "Refusing to overwrite evidence")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.fonttype": "none", "pdf.fonttype": 42})
    colors = ("#99CCBB", "#CCCCCC", "#EE9988")
    cmap = ListedColormap(colors)
    norm = BoundaryNorm([-.5, .5, 1.5, 2.5], cmap.N)
    fig, axes = plt.subplots(2, 1, figsize=(13, 7.5))
    for ax, (name, values) in zip(axes, (("Kimi-K2.6: 38/40 qualified", kc),
                                        ("GLM-5.1-FP8: 35/40 successful", gc))):
        image = [[values[b, c][0] for b in range(1, 11)] for c in CONDITIONS]
        ax.imshow(image, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
        for ci, c in enumerate(CONDITIONS):
            for b in range(1, 11):
                _, value = values[b, c]
                ax.text(b - 1, ci, "FAIL" if value is None else format(value, ".1f"),
                        ha="center", va="center", fontsize=10)
        ax.set_xticks(range(10), range(1, 11))
        ax.set_yticks(range(4), LABELS)
        ax.set_xlabel("Logical block")
        ax.set_title(name + " | original40 only")
        ax.set_xticks([x - .5 for x in range(11)], minor=True)
        ax.set_yticks([x - .5 for x in range(5)], minor=True)
        ax.grid(which="minor", color="white", linewidth=2)
        ax.tick_params(which="minor", bottom=False, left=False)
    fig.suptitle("Original40 outcome appendix: descriptive, noncausal comparison", fontsize=14)
    fig.legend(handles=[Patch(facecolor=color, label=label) for color, label in zip(colors,
               ("Kimi currently qualified / GLM C23 success", "GLM earlier-protocol success",
                "Original failure / not qualified"))], loc="lower center",
               bbox_to_anchor=(.5, .045), ncol=3, frameon=False, fontsize=9)
    fig.text(.5, .015, "Cell values: register call to correct first arithmetic HTTP completion (seconds). "
             "Failures are not assigned zero.\n"
             "Earlier GLM protocols remain labeled and do not enter the C23 primary analysis. "
             "Supplementary and historical Kimi runs do not fill cells.",
             ha="center", va="bottom", fontsize=9)
    fig.tight_layout(rect=(0, .12, 1, .95))
    args.outputdir.mkdir(parents=True, exist_ok=True)
    for p in files:
        fig.savefig(p, dpi=200, bbox_inches="tight")
    plt.close(fig)
    data = {"kimi_input_sha256": hashlib.sha256(kb).hexdigest(),
            "glm_input_sha256": hashlib.sha256(gb).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "matplotlib_version": matplotlib.__version__,
            "geometry": "Each model40 unique original block-condition cells",
            "success_counts": {"Kimi": 38, "GLM": 35},
            "scope": "Descriptive mixed-version appendix; no pooled causal/paired statistical inference",
            "outputs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with receipt.open("x", encoding="utf-8") as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
