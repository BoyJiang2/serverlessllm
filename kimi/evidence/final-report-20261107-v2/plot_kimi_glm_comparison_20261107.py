"""Plot frozen Kimi/GLM evidence; no runtime or statistical reanalysis."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

C = ("safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit")
LABELS = ("SF disk cold", "SF disk hot", "Store disk cold", "Store pool hit")
COLORS = ("#4477AA", "#88CCEE", "#CC6677", "#228833")


def check(ok, message):
    if not ok:
        raise ValueError(message)


def valid(values, positive=False):
    check(bool(values) and all(type(x) in (int, float) and math.isfinite(x) and
          (x > 0 if positive else x >= 0) for x in values), "Invalid duration array")
    return values


def mean(values):
    return statistics.fmean(valid(values))


def close(a, b):
    check(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-8), "Segment identity failed")


def extract(k, g):
    check(k["schema"] == "kimi-four-analysis-report-v1", "Kimi schema mismatch")
    blocks = k["complete_block_ids"]
    check(len(blocks) == 8 and blocks == sorted(set(blocks)), "Expected8 complete Kimi blocks")
    original = [r for r in k["runs"] if r["cohort"] == "original"]
    check(len(original) == 40 and {(r["block"], r["condition"]) for r in original} ==
          {(b, c) for b in range(1, 11) for c in C}, "Original40 geometry mismatch")
    qualified = [r for r in original if r["qualified"] is True]
    check(len(qualified) == 38 and all(r["launched"] for r in qualified), "Expected38 qualified originals")
    kp, kd, ks, fixed = {}, {}, {}, {}
    for c in C:
        rows = sorted([r for r in qualified if r["condition"] == c and r["block"] in blocks],
                      key=lambda r: r["block"])
        check([r["block"] for r in rows] == blocks, "Kimi primary geometry mismatch")
        kp[c] = valid([r["timing"]["A_s"] for r in rows], True)
        kd[c] = valid([r["timing"]["A_s"] for r in qualified if r["condition"] == c], True)
        check(8 <= len(kd[c]) <= 10, "Kimi descriptive geometry mismatch")
        ks[c] = {key: valid([r["timing"][key] for r in rows]) for key in ("Prep_s", "Gap_s", "A_s")}
        fixed[c] = valid([r["timing"]["D_fixed32_s"] for r in rows], True)
        for r in rows:
            t = r["timing"]
            close(t["Prep_s"] + t["A_s"], t["Prep_plus_A_s"])
            close(t["Prep_s"] + t["Gap_s"] + t["A_s"], t["contiguous_s"])
    check(g["status"] == "READONLY_EXISTING_GLM_PLOT_ARRAYS_NO_IMPUTATION", "GLM schema mismatch")
    gp, gd, gs = {}, {}, {}
    for c in C:
        p, d = g["primary_complete_blocks_5_to_10"][c], g["descriptive_C23_26"][c]
        check(p["n"] == 6 and p["blocks"] == list(range(5, 11)), "GLM primary geometry mismatch")
        gp[c], gd[c] = valid(p["A_s"], True), valid(d["A_s"], True)
        check(len(gp[c]) == 6 and len(gd[c]) == d["n"], "GLM sample count mismatch")
        check(d["blocks"] == list(range(5 if c.startswith("safetensors") else 4, 11)),
              "GLM descriptive blocks mismatch")
        seg = p["event_mutually_exclusive_segments_s"]
        check(all(len(valid(v)) == 6 for v in seg.values()), "GLM segment geometry mismatch")
        check(len(p["Prep_s"]) == len(p["Prep_plus_A_s"]) == 6, "GLM Prep count mismatch")
        for i, a in enumerate(gp[c]):
            close(a, sum(seg[key][i] for key in ("command_to_route_s",
                  "route_to_backend_first_token_s", "backend_first_token_to_correct_http_s")))
            close(p["Prep_s"][i], seg["preparation_s"][i])
            close(p["Prep_plus_A_s"][i], p["Prep_s"][i] + a)
        gs[c] = {"Prep_s": p["Prep_s"], "Gap_s": seg["preparation_to_startup_gap_s"], "A_s": gp[c]}
    check(sum(map(len, gd.values())) == 26, "Expected26 GLM C23 samples")
    return ({"Kimi-K2.6": kp, "GLM-5.1-FP8": gp},
            {"Kimi-K2.6": kd, "GLM-5.1-FP8": gd},
            {"Kimi-K2.6": ks, "GLM-5.1-FP8": gs}, fixed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kimi_analysis", type=Path)
    ap.add_argument("glm_plot_input", type=Path)
    ap.add_argument("outputdir", type=Path)
    a = ap.parse_args()
    kb, gb = a.kimi_analysis.read_bytes(), a.glm_plot_input.read_bytes()
    primary, descriptive, stacks, fixed = extract(json.loads(kb), json.loads(gb))
    stems = ("fig1_startup_means", "fig2_qualified_ecdf", "fig3_preparation_gap_startup")
    files = [a.outputdir / (s + "." + ext) for s in stems for ext in ("png", "pdf", "svg")]
    check(not any(p.exists() for p in files + [a.outputdir / "plot-provenance.json"]), "Refusing overwrite")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.fonttype": "none", "pdf.fonttype": 42})
    a.outputdir.mkdir(parents=True, exist_ok=True)

    def save(fig, stem, note):
        fig.text(.5, -.035, note, ha="center", va="top", fontsize=9)
        for ext in ("png", "pdf", "svg"):
            fig.savefig(a.outputdir / (stem + "." + ext), dpi=200, bbox_inches="tight")
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for mi, (model, groups) in enumerate(primary.items()):
        base = mean(groups[C[0]])
        for ci, c in enumerate(C):
            x, vals = mi + (ci - 1.5) * .18, groups[c]
            for ax, scale in zip(axes, (1, base)):
                ax.bar(x, mean(vals) / scale, .16, color=COLORS[ci],
                       label=LABELS[ci] if mi == 0 else None)
                ax.scatter([x + (j - (len(vals) - 1) / 2) * .009 for j in range(len(vals))],
                           [v / scale for v in vals], s=12, color="black", alpha=.65, zorder=3)
                ax.annotate(format(mean(vals) / scale, ".1f" if scale == 1 else ".2f"),
                            (x, max(vals) / scale), xytext=(0, 4), textcoords="offset points",
                            ha="center", fontsize=8)
    for ax in axes:
        ax.set_xticks([0, 1], ["Kimi-K2.6\n8 complete blocks", "GLM-5.1-FP8\n6 complete blocks"])
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("Register call to correct arithmetic HTTP completion (s)")
    axes[0].set_title("(a) Arithmetic startup: mean and individual samples")
    axes[1].set_ylabel("Ratio to each model's mean SF disk-cold startup")
    axes[1].set_title("(b) Within-model normalization")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=4, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, .92))
    save(fig, stems[0], "Figure-6a-inspired four-condition extension; not pure model loading.\n"
         "Bars: arithmetic means; dots: samples. Complete qualified blocks only; no cross-model paired inference.")

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, (model, groups) in zip(axes, descriptive.items()):
        for ci, c in enumerate(C):
            v = sorted(groups[c])
            ax.step([0] + v, [0] + [(i + 1) / len(v) for i in range(len(v))], where="post",
                    color=COLORS[ci], label=LABELS[ci] + " (n=" + str(len(v)) + ")")
        ax.set_title(model + " (" + str(sum(map(len, groups.values()))) + " qualified samples)")
        ax.set_xlabel("Register call to correct arithmetic HTTP completion (s)")
        ax.set_ylabel("Empirical cumulative probability")
        ax.set_ylim(0, 1.02)
        ax.set_xlim(left=0)
        ax.grid(alpha=.2)
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    save(fig, stems[1], "Current qualified Kimi original cohort (38) and GLM C23 (26); failures are not assigned zero.\n"
         "Historical, diagnostic and supplementary cohorts are excluded; partial-block successes remain descriptive.")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    parts = (("Prep_s", "Target-condition preparation", "#BBBBBB"),
             ("Gap_s", "Preparation-to-register gap", "#DDAA33"),
             ("A_s", "Register-to-correct arithmetic HTTP", "#4477AA"))
    for ax, (model, groups) in zip(axes, stacks.items()):
        bottom = [0.] * 4
        for key, label, color in parts:
            h = [mean(groups[c][key]) for c in C]
            ax.bar(range(4), h, bottom=bottom, color=color, label=label)
            bottom = [x + y for x, y in zip(bottom, h)]
        for x, total in enumerate(bottom):
            ax.annotate(format(total, ".1f"), (x, total), xytext=(0, 3),
                        textcoords="offset points", ha="center", fontsize=8)
        ax.set_xticks(range(4), LABELS, rotation=20, ha="right")
        ax.set_ylabel("Mean contiguous condition-to-response interval (s)")
        ax.set_title(model + (" (8 complete blocks)" if model == "Kimi-K2.6" else " (6 complete blocks)"))
        ax.margins(y=.13)
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=3, frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, .92))
    save(fig, stems[2], "Prep differs: Kimi target construction/admission; GLM can include previous-pool unload.\n"
         "Common environment/pool initialization and cleanup/release are excluded: not a complete lifecycle or pure I/O.\n"
         "Prep + gap + arithmetic startup are exclusive; overlapping rank phases are not added.")
    provenance = {"kimi_input_sha256": hashlib.sha256(kb).hexdigest(),
                  "glm_input_sha256": hashlib.sha256(gb).hexdigest(),
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "matplotlib_version": matplotlib.__version__,
                  "Kimi_primary_fixed32_mean_whole_HTTP_s": {c: mean(v) for c, v in fixed.items()},
                  "GLM_fixed32_whole_HTTP_s": None,
                  "fixed32_limit": "Ready fixed32 whole HTTP, not pure decode; GLM has no saved begin/end.",
                  "outputs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with (a.outputdir / "plot-provenance.json").open("x", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
