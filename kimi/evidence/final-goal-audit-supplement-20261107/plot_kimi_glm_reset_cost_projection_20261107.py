"""One-off cost projection from verified historical evidence; no runtime work."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

source, output = Path(sys.argv[1]), Path(sys.argv[2])
raw = source.read_bytes()
assert hashlib.sha256(raw).hexdigest() == "07a44e897eec5d8b6aab2c37cfaeaacedd87201939b37d8ebc308a5e2cbaf247"
data = json.loads(raw)
projection = data["N_projection"]
conditions = ("safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit")
labels = ("SF disk cold", "SF disk hot", "Store disk cold", "Store pool hit")
colors = ("#4477AA", "#88CCEE", "#CC6677", "#228833")
proxy = data["conversion"]["controller_envelope_s"]
assert math.isclose(proxy, 678.1289425040013)
assert data["conversion"]["pure_conversion_s"] is None
n_values = [1, 2, 5, 10, 20, 50, 100]
for model, groups in projection["parameters"].items():
    for condition, row in groups.items():
        assert row["N"] == n_values
        base = row["measured_reset_online_window_mean_s"]
        assert base > 0 and all(math.isclose(x, base) for x in row["reset_online_average_s"])
        charge = row["one_time_conversion_controller_proxy_s"]
        if charge is None:
            assert model == "GLM" and condition.startswith("store")
            assert row["including_proxy_average_s"] is None
        else:
            assert all(math.isclose(x, base + charge / n, rel_tol=1e-12)
                       for x, n in zip(row["including_proxy_average_s"], n_values))
# Configured capacity is not observed resident memory or a monetary bill.
full = {}
for condition in conditions:
    rows = [r for r in data["rows"] if r["condition"] == condition and r["block"] in (1, 2, 3, 5, 6, 7, 8, 10)]
    assert len(rows) == 8
    summary = data["primary_complete8_summary"][condition]["costs"]["controller_total_reported_s"]
    full[condition] = summary["mean_s"]
    assert math.isclose(statistics.mean(r["elapsed_s"]["controller_total_reported_s"] for r in rows), full[condition], rel_tol=1e-12)
    assert full[condition] > projection["parameters"]["Kimi"][condition]["measured_reset_online_window_mean_s"]
outputs = [output / ("fig4_reset_reuse_projection." + x) for x in ("png", "pdf", "svg")]
assert not any(p.exists() for p in outputs + [output / "projection-provenance.json"])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none", "pdf.fonttype": 42})
fig, axes = plt.subplots(1, 3, figsize=(17, 5))
for ax, model in zip(axes[:2], ("Kimi", "GLM")):
    for condition, color in zip(conditions, colors):
        row = projection["parameters"][model][condition]
        ax.plot(n_values, row["reset_online_average_s"], color=color, linewidth=2)
        if condition.startswith("store") and row["including_proxy_average_s"] is not None:
            ax.plot(n_values, row["including_proxy_average_s"], "--", color=color, marker=".", linewidth=1.5)
    ax.set_title(model + ": preparation + gap + arithmetic response")
    ax.set_ylabel("Modeled average online-window cost (s)")
axes[1].text(.97, .58, "GLM Store conversion cost unknown:\nincluding-conversion curves omitted.",
             transform=axes[1].transAxes, ha="right", fontsize=9)
for condition, color in zip(conditions, colors):
    axes[2].plot(n_values, [full[condition]] * len(n_values), color=color, linewidth=2)
    if condition.startswith("store"):
        axes[2].plot(n_values, [full[condition] + proxy / n for n in n_values], "--", color=color, marker=".", linewidth=1.5)
axes[2].set_title("Kimi: full formal-controller envelope")
axes[2].set_ylabel("Modeled average controller-envelope cost (s)")
for ax in axes:
    ax.set_xscale("log")
    ax.set_xticks(n_values, [str(n) for n in n_values])
    ax.set_xlabel("Hypothetical restore count N")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=.2)
handles = [Line2D([0], [0], color=c, linewidth=3, label=l) for c, l in zip(colors, labels)]
handles += [Line2D([0], [0], color="#555555", linewidth=2, label="Preconverted/local assets: repeated-reset cost"),
            Line2D([0], [0], color="#555555", linestyle="--", label="+ one historical conversion-controller proxy / N")]
fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, fontsize=9)
fig.tight_layout(rect=(0, 0, 1, .83))
fig.text(.5, -.03, "MODEL PROJECTION; no N>1 resident-service reuse or break-even measured. Every cycle repeats its own fixed cache/pool preparation.\n"
         "First two panels exclude common initialization and final cleanup; third includes the Kimi controller envelope and mandated observations.\n"
         "Dashed curves charge the historical qjhs002 conversion/controller envelope (678.13 s, including validation/exit/release), not pure conversion; later asset localization is excluded.\n"
         "Kimi: 8 complete blocks; GLM: 6. GLM preparation may include preceding-pool unload. No uncertainty interval for this hypothetical trajectory is estimated.",
         ha="center", va="top", fontsize=8.5)
output.mkdir(parents=True, exist_ok=True)
for path in outputs:
    fig.savefig(path, dpi=180, bbox_inches="tight")
plt.close(fig)
receipt = {"status": "MODEL_PROJECTION_RENDERED_NOT_NEW_MEASUREMENTS", "cost_input_sha256": hashlib.sha256(raw).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "matplotlib_version": matplotlib.__version__, "N_values": n_values,
           "formula_online": "mean(Prep+Gap+A) + applicable_known_conversion_controller_proxy/N",
           "formula_full_Kimi": "mean(controller_total_reported_s) + applicable_conversion_controller_proxy/N",
           "full_Kimi_mean_s": full, "GLM_conversion_and_full_lifecycle_missing": True,
           "persistent_pool_or_warm_service_reuse_measured": False, "break_even_claim": False,
           "outputs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}}
with (output / "projection-provenance.json").open("x", encoding="utf-8") as stream:
    json.dump(receipt, stream, indent=2, allow_nan=False)
    stream.write("\n")
