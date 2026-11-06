#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C23 reanalysis of /data/brick1/boyjiang/glm5.1-fp8

Reads (never writes) results/formal-ledger.json and the result.json files it
points at.  Writes only into results/reanalysis-20261106/.

Usage:
    /usr/bin/python3 recompute.py --check   # diagnostics + sanity gates only, no writes
    /usr/bin/python3 recompute.py           # full run, writes all deliverables

Pure Python 3 standard library (no numpy/pandas on this host).
"""

import hashlib
import json
import os
import random
import statistics
import sys
from datetime import datetime, timezone

ROOT = "/data/brick1/boyjiang/glm5.1-fp8"
LEDGER = os.path.join(ROOT, "results", "formal-ledger.json")
OUTDIR = os.path.join(ROOT, "results", "reanalysis-20261106")

C23 = "v13-canonical23-qualified-disk-jit-seed"
CONDS = [
    "safetensors_disk_cold",
    "safetensors_disk_hot",
    "store_disk_cold",
    "store_pool_hit",
]
SHORT = {
    "safetensors_disk_cold": "sf_cold",
    "safetensors_disk_hot": "sf_hot",
    "store_disk_cold": "store_cold",
    "store_pool_hit": "pool_hit",
}
LONG = {v: k for k, v in SHORT.items()}

SEED = 20261024
NBOOT = 10000

# set in main(); consumed by the markdown builders so every deliverable carries
# the gate verdict
GATE_STATUS = "PASS"

# PRIMARY timings, all on the unified command (/register) origin.
METRIC_KEYS = {
    "prep_s": "preparation_s",
    "route_s": "command_to_route_publication_s",
    "first_tok_s": "command_to_first_token_s",
    "biz_s": "command_to_correct_business_response_s",
}
# SEPARATE trigger-origin pair: reported on its own, never mixed with the above.
TRIGGER_KEYS = {
    "trigger_first_tok_s": "trigger_to_first_token_s",
    "trigger_biz_s": "trigger_to_correct_business_response_s",
}
ANCILLARY_KEY = "command_to_first_token_s"
ANCILLARY_BIZ_KEY = "command_to_correct_business_response_s"
TIMINGS = ["prep_s", "route_s", "first_tok_s", "biz_s"]

# previously computed C23 means: (n, prep_s, route_s, first_tok_s, biz_s)
GATES = {
    "safetensors_disk_cold": (6, 39.82, 447.09, 448.27, 448.50),
    "safetensors_disk_hot": (6, 323.43, 184.54, 185.53, 185.75),
    "store_disk_cold": (7, 12.05, 179.61, 180.63, 180.87),
    "store_pool_hit": (7, 132.89, 61.74, 62.71, 62.94),
}
GATE_TOL = 0.05

CONTRASTS = [
    ("store_disk_cold - safetensors_disk_cold", "store_disk_cold", "safetensors_disk_cold"),
    ("store_pool_hit - safetensors_disk_cold", "store_pool_hit", "safetensors_disk_cold"),
    ("store_pool_hit - store_disk_cold", "store_pool_hit", "store_disk_cold"),
    ("store_disk_cold - safetensors_disk_hot", "store_disk_cold", "safetensors_disk_hot"),
]
WINDOWS = ["business_only", "including_preparation"]


# ---------------------------------------------------------------- helpers
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mean(xs):
    return statistics.fmean(xs)


def sd(xs):
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def med(xs):
    return statistics.median(xs)


def jsafe(x):
    if x is None:
        return None
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    return x


def f2(x, nd=2):
    return "n/a" if x is None else ("%.*f" % (nd, x))


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def gate_banner():
    """Markdown banner stamped into every deliverable."""
    if GATE_STATUS == "PASS":
        return ("> **Sanity gates: PASS** \u2014 every published C23 condition mean "
                "(prep / route / first_tok / biz) reproduced within %.2f s on the "
                "unified command(/register) origin.\n" % GATE_TOL)
    return ("> **Sanity gates: FAIL** \u2014 see `sanity-gate.md`. No analysis outputs "
            "were produced.\n")


def gate_banner_html():
    return gate_banner().replace("> ", "").replace(">", "")


def bootstrap_ci(diffs, B=NBOOT, seed=SEED):
    """Percentile bootstrap resampling BLOCKS (each diff is one block)."""
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(B):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * (B - 1))]
    hi = means[int(0.975 * (B - 1))]
    return lo, hi


def signflip_p(diffs):
    """Exact two-sided paired permutation test: all 2**n sign flips."""
    n = len(diffs)
    obs = abs(mean(diffs))
    total = 1 << n
    hits = 0
    for mask in range(total):
        s = 0.0
        for i, d in enumerate(diffs):
            s += d if (mask >> i) & 1 else -d
        if abs(s / n) >= obs - 1e-12:
            hits += 1
    return hits / total, total


def holm(pvals):
    """Holm-Bonferroni step-down. pvals: list of floats -> adjusted list."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = (m - rank) * pvals[i]
        running = max(running, val)
        adj[i] = min(1.0, running)
    return adj


# ---------------------------------------------------------------- load
def load_population():
    with open(LEDGER, "r", encoding="utf-8") as fh:
        led = json.load(fh)
    rows = led["rows"]
    c23 = [r for r in rows if r.get("protocol_revision") == C23]
    excl = [r for r in rows if r.get("protocol_revision") != C23]

    problems = []
    if len(c23) != 26:
        problems.append("expected 26 C23 rows, found %d" % len(c23))
    bad_status = [r for r in c23 if r.get("status") != "success"]
    if bad_status:
        problems.append("C23 rows with status != success: %s"
                        % ", ".join(r["attempt_id"] + "=" + str(r.get("status")) for r in bad_status))

    attempts = []
    for r in c23:
        path = r["result_path"]
        if not os.path.exists(path):
            problems.append("missing result_path: " + path)
            continue
        actual = sha256_file(path)
        if r.get("result_sha256") and actual != r["result_sha256"]:
            problems.append("sha256 mismatch for %s: ledger=%s actual=%s"
                            % (r["attempt_id"], r["result_sha256"], actual))
        with open(path, "r", encoding="utf-8") as fh:
            res = json.load(fh)
        for field in ("condition", "block", "status", "protocol_revision"):
            if field not in res:
                problems.append("%s: result.json missing field %r" % (r["attempt_id"], field))
        if res.get("protocol_revision") != C23:
            problems.append("%s: result.json protocol_revision=%r"
                            % (r["attempt_id"], res.get("protocol_revision")))
        if res.get("condition") != r["condition"]:
            problems.append("%s: condition mismatch ledger=%r result=%r"
                            % (r["attempt_id"], r["condition"], res.get("condition")))
        if res.get("block") != r["block"]:
            problems.append("%s: block mismatch ledger=%r result=%r"
                            % (r["attempt_id"], r["block"], res.get("block")))
        metrics = res.get("metrics")
        if not isinstance(metrics, dict):
            problems.append("%s: metrics missing or not a dict" % r["attempt_id"])
            metrics = {}
        for name, key in METRIC_KEYS.items():
            if key not in metrics:
                problems.append("%s: MISSING metric key %r" % (r["attempt_id"], key))
        attempts.append({
            "attempt_id": r["attempt_id"],
            "order": r["order"],
            "condition": r["condition"],
            "block": r["block"],
            "result_path": path,
            "result_sha256": actual,
            "status": res.get("status"),
            "metrics": metrics,
            "raw": res,
        })
    return led, c23, excl, attempts, problems


def extract(attempts):
    """Per-attempt timing record, plus a hard check that every key exists."""
    recs = []
    missing = []
    for a in attempts:
        m = a["metrics"]
        rec = {"attempt_id": a["attempt_id"], "condition": a["condition"],
               "block": a["block"], "result_sha256": a["result_sha256"]}
        for name, key in METRIC_KEYS.items():
            if key not in m:
                missing.append((a["attempt_id"], key))
                rec[name] = None
            else:
                rec[name] = float(m[key])
            rec["key_" + name] = key
        for name, key in TRIGGER_KEYS.items():
            if key not in m:
                missing.append((a["attempt_id"], key))
                rec[name] = None
            else:
                rec[name] = float(m[key])
        rec["cmd_first"] = rec["first_tok_s"]
        rec["cmd_biz"] = rec["biz_s"]
        recs.append(rec)
    if missing:
        print("STOP: missing required metric keys:")
        for aid, k in missing:
            print("  - %s: %s" % (aid, k))
        sys.exit(2)
    return recs


# ---------------------------------------------------------------- sanity gates
def check_gates(recs):
    """Compare the published C23 condition means with the recomputed ones.

    PRIMARY definitions, all on the unified command (/register) origin:
        prep_s      = preparation_s
        route_s     = command_to_route_publication_s
        first_tok_s = command_to_first_token_s
        biz_s       = command_to_correct_business_response_s

    The trigger-origin pair is carried separately and is never subtracted from
    or added to a command-origin quantity.
    """
    print("\n== SANITY GATES (tolerance %.2f s) ==" % GATE_TOL)
    print("  primary definitions (command origin): "
          "first_tok_s=command_to_first_token_s, "
          "biz_s=command_to_correct_business_response_s")
    detail = {}
    statuses = []
    for c in CONDS:
        v = [r for r in recs if r["condition"] == c]
        n = len(v)
        exp = list(GATES[c][1:])
        got = [mean([r[t] for r in v]) for t in TIMINGS]
        deltas = [abs(g - e) for g, e in zip(got, exp)]
        worst = max(deltas)
        n_ok = (n == GATES[c][0])
        verdict = "PASS" if (n_ok and worst <= GATE_TOL) else "FAIL"
        statuses.append(verdict)
        trig_first = mean([r["trigger_first_tok_s"] for r in v])
        trig_biz = mean([r["trigger_biz_s"] for r in v])
        detail[c] = {
            "n": n, "n_expected": GATES[c][0],
            "gate": dict(zip(TIMINGS, exp)),
            "computed": dict(zip(TIMINGS, got)),
            "max_abs_delta": worst,
            "deltas": dict(zip(TIMINGS, deltas)),
            "trigger_origin": {"trigger_first_tok_s": trig_first,
                               "trigger_biz_s": trig_biz,
                               "offset_first_s": got[2] - trig_first,
                               "offset_biz_s": got[3] - trig_biz},
            "verdict": verdict,
        }
        print("\n  %-28s n=%d (gate n=%d)" % (c, n, GATES[c][0]))
        print("    gate     : prep %8.2f  route %8.2f  first %8.2f  biz %8.2f"
              % tuple(exp))
        print("    computed : prep %8.2f  route %8.2f  first %8.2f  biz %8.2f  "
              "maxdelta %.4f" % (got[0], got[1], got[2], got[3], worst))
        print("    delta    : prep %+8.4f  route %+8.4f  first %+8.4f  biz %+8.4f  -> %s"
              % (got[0] - exp[0], got[1] - exp[1], got[2] - exp[2],
                 got[3] - exp[3], verdict))
        print("    trigger  : first %8.2f  biz %8.2f   offset command-trigger: "
              "first %+0.4f  biz %+0.4f"
              % (trig_first, trig_biz, got[2] - trig_first, got[3] - trig_biz))

    overall = "PASS" if all(s == "PASS" for s in statuses) else "FAIL"
    print("\nSANITY GATES: %s" % overall)
    if overall != "PASS":
        print("STOP: recomputed means do not reproduce the published gate within "
              "%.2f s. No outputs will be written." % GATE_TOL)
    return overall, detail


# ---------------------------------------------------------------- task A
def task_a(recs):
    blocks = sorted({r["block"] for r in recs})
    conds_present = {b: {r["condition"] for r in recs if r["block"] == b} for b in blocks}
    cell = {}
    for r in recs:
        cell[(r["block"], r["condition"])] = r

    rows = []
    for b in blocks:
        row = [b]
        for c in CONDS:
            row.append(f2(cell[(b, c)]["biz_s"]) if (b, c) in cell else "\u2014")
        n_here = len(conds_present[b])
        row.append(("yes (4/4)" if n_here == 4 else "NO (%d/4)" % n_here))
        rows.append(row)

    header = ["block"] + [SHORT[c] for c in CONDS] + ["all four present?"]
    table = md_table(header, rows)

    counts = {c: sum(1 for r in recs if r["condition"] == c) for c in CONDS}
    means = {c: {t: mean([r[t] for r in recs if r["condition"] == c]) for t in TIMINGS}
             for c in CONDS}

    return table, blocks, conds_present, counts, means


# ---------------------------------------------------------------- task B
def task_b(blocks, conds_present):
    in_blocks = [b for b in blocks if len(conds_present[b]) == 4]
    out_blocks = [b for b in blocks if len(conds_present[b]) < 4]
    ledger_only = [b for b in range(1, 4) if b not in blocks]
    reason = {}
    for b in out_blocks:
        have = sorted(SHORT[c] for c in conds_present[b])
        reason[b] = ("only %d of 4 conditions at C23: %s" % (len(have), ", ".join(have)))
    for b in ledger_only:
        reason[b] = "no C23 attempt at all (only pre-C23 protocol revisions in ledger)"
    return in_blocks, out_blocks, ledger_only, reason


# ---------------------------------------------------------------- tasks C/D
def paired_stats(recs, blocks_in):
    vals = {c: {} for c in CONDS}
    for r in recs:
        if r["block"] in blocks_in:
            vals[r["condition"]][r["block"]] = {
                "business_only": r["biz_s"],
                "including_preparation": r["prep_s"] + r["biz_s"],
            }

    results = []
    p_by_window = {w: [] for w in WINDOWS}
    for ci, (label, a, b) in enumerate(CONTRASTS, start=1):
        entry = {"id": ci, "label": label, "a": a, "b": b,
                 "a_short": SHORT[a], "b_short": SHORT[b], "windows": {}}
        for w in WINDOWS:
            diffs = [vals[a][blk][w] - vals[b][blk][w] for blk in blocks_in]
            n = len(diffs)
            m = mean(diffs)
            s = sd(diffs)
            dz = m / s if s > 0 else None
            lo, hi = bootstrap_ci(diffs)
            p_raw, n_perm = signflip_p(diffs)
            mean_a = mean([vals[a][blk][w] for blk in blocks_in])
            mean_b = mean([vals[b][blk][w] for blk in blocks_in])
            ratios = [vals[a][blk][w] / vals[b][blk][w] for blk in blocks_in]
            entry["windows"][w] = {
                "n_pairs": n,
                "blocks": list(blocks_in),
                "per_block_diff": {str(blk): vals[a][blk][w] - vals[b][blk][w]
                                   for blk in blocks_in},
                "mean_diff": m,
                "sd_diff": s,
                "median_diff": med(diffs),
                "ci95_low": lo,
                "ci95_high": hi,
                "ci_excludes_zero": (lo > 0) or (hi < 0),
                "cohens_dz": jsafe(dz),
                "mean_a": mean_a,
                "mean_b": mean_b,
                "ratio_of_paired_means": mean_a / mean_b,
                "median_per_block_ratio": med(ratios),
                "p_raw": p_raw,
                "permutation_space": n_perm,
            }
            p_by_window[w].append(p_raw)
        results.append(entry)

    mc = {}
    for w in WINDOWS:
        pv = p_by_window[w]
        adj = holm(pv)
        mc[w] = {
            "family": "4 paired contrasts",
            "p_value_method": "exact two-sided paired sign-flip permutation over all 2^6 = 64 sign assignments",
            "correction": "Holm-Bonferroni step-down, alpha=0.05, within window",
            "min_attainable_raw_p": 2.0 / 64.0,
            "contrasts": [],
        }
        for i, e in enumerate(results):
            e["windows"][w]["p_holm"] = adj[i]
            e["windows"][w]["survives_holm_0.05"] = adj[i] < 0.05
            mc[w]["contrasts"].append({
                "id": e["id"], "label": e["label"],
                "p_raw": pv[i], "p_holm": adj[i],
                "survives_holm_0.05": adj[i] < 0.05,
            })
    return results, mc, vals


# ---------------------------------------------------------------- follow-ups
def power_floor():
    """Minimum attainable p (exact sign flip) and Holm floor, family of 4."""
    rows = []
    for n in (6, 7, 8, 9, 10):
        pmin = 2.0 / (2 ** n)
        rows.append({"n": n, "min_raw_p": pmin, "min_holm_p": 4.0 * pmin,
                     "attainable_at_0.05": 4.0 * pmin < 0.05})
    first = next(r["n"] for r in rows if r["attainable_at_0.05"])
    return rows, first


def contrast3_sensitivity(recs, blocks_in):
    """Contrast 3 (pool_hit - store_cold) under both legitimate pairing rules.

    n=6 complete-block rule vs n=7 maximal pair (adds block 4, which has both
    conditions but not all four).
    """
    a, b = "store_pool_hit", "store_disk_cold"
    idx = {(r["block"], r["condition"]): r for r in recs}

    def val(blk, cond, w):
        r = idx[(blk, cond)]
        return r["biz_s"] if w == "business_only" else r["prep_s"] + r["biz_s"]

    out = {"a": a, "b": b,
           "label": "store_pool_hit - store_disk_cold",
           "block4": {
               "pool_hit": {"prep_s": idx[(4, a)]["prep_s"], "biz_s": idx[(4, a)]["biz_s"]},
               "store_cold": {"prep_s": idx[(4, b)]["prep_s"], "biz_s": idx[(4, b)]["biz_s"]},
           },
           "rules": {}}
    out["block4"]["biz_diff"] = idx[(4, a)]["biz_s"] - idx[(4, b)]["biz_s"]
    out["block4"]["prep_plus_biz_diff"] = (
        idx[(4, a)]["prep_s"] + idx[(4, a)]["biz_s"]
        - idx[(4, b)]["prep_s"] - idx[(4, b)]["biz_s"])

    for name, blocks in (("n6_complete_block", list(blocks_in)),
                         ("n7_maximal_pair", list(blocks_in) + [4])):
        per_window = {}
        for w in WINDOWS:
            va = [val(blk, a, w) for blk in blocks]
            vb = [val(blk, b, w) for blk in blocks]
            diffs = [x - y for x, y in zip(va, vb)]
            m = mean(diffs)
            s = sd(diffs)
            lo, hi = bootstrap_ci(diffs)
            per_window[w] = {
                "blocks": blocks, "n": len(diffs),
                "diffs": diffs, "mean": m, "sd": s, "median": med(diffs),
                "ci95_low": lo, "ci95_high": hi,
                "ci_excludes_zero": (lo > 0) or (hi < 0),
                "cohens_dz": jsafe(m / s if s > 0 else None),
                "mean_a": mean(va), "mean_b": mean(vb),
                "ratio_of_paired_means": mean(va) / mean(vb),
                "median_per_block_ratio": med([x / y for x, y in zip(va, vb)]),
            }
        out["rules"][name] = per_window
    return out


def build_c3_json(sens3):
    """JSON form of the contrast-3 pairing sensitivity (additive key)."""
    method = "block bootstrap, 10000 resamples, seed 20261024, percentile"

    def win(d):
        return {
            "n": d["n"],
            "mean": d["mean"],
            "sd": d["sd"],
            "median": d["median"],
            "ci95_low": d["ci95_low"],
            "ci95_high": d["ci95_high"],
            "ci_excludes_zero": d["ci_excludes_zero"],
            "d_z": jsafe(d["cohens_dz"]),
            "ratio": d["ratio_of_paired_means"],
            "method": method,
            "per_block_diff": {str(b): v for b, v in zip(d["blocks"], d["diffs"])},
        }

    rules = {}
    for name in ("n6_complete_block", "n7_maximal_pair"):
        r = sens3["rules"][name]
        rules[name] = {
            "n": r["business_only"]["n"],
            "blocks": r["business_only"]["blocks"],
            "business_only": win(r["business_only"]),
            "including_preparation": win(r["including_preparation"]),
        }

    stable = True
    for w in WINDOWS:
        a = sens3["rules"]["n6_complete_block"][w]["mean"]
        b = sens3["rules"]["n7_maximal_pair"][w]["mean"]
        if (a > 0) != (b > 0):
            stable = False

    return {
        "label": "store_pool_hit - store_disk_cold",
        "note": "Two legitimate pairing rules reported side by side; neither is "
                "preferred. n=7 adds block 4, which has both conditions but not all "
                "four. store_disk_cold_04 is a 3-MAD outlier and is retained.",
        "sign_stable_across_rules": stable,
        "sign_stable_definition": "true when, for every window, the n=6 and n=7 means "
                                  "have the same sign",
        "block4": sens3["block4"],
        "rules": rules,
    }


def build_power_floor_json(pf_rows, pf_min_n):
    return {
        "test": "exact two-sided paired sign-flip",
        "family_size": 4,
        "by_n": {
            str(r["n"]): {
                "min_raw_p": r["min_raw_p"],
                "min_holm_p_family4": r["min_holm_p"],
                "attainable_at_0.05": r["attainable_at_0.05"],
            } for r in pf_rows
        },
        "smallest_attainable_n": pf_min_n,
        "constraint": "With a family of four paired contrasts and an exact sign-flip "
                      "test, at least %d complete blocks are required before "
                      "family-wise significance at alpha=0.05 is attainable at all."
                      % pf_min_n,
    }


# ---------------------------------------------------------------- task E
def task_e(means):
    b = means["safetensors_disk_cold"]["biz_s"]
    s = means["store_disk_cold"]["biz_s"]
    p = means["store_pool_hit"]["biz_s"]
    return {
        "note": "Unpaired ratio-of-condition-means, DESCRIPTIVE ONLY. "
                "NOT the primary estimate (primary = paired block contrasts).",
        "sf_cold_over_store_cold": b / s,
        "sf_cold_over_pool_hit": b / p,
        "values_used": {"sf_cold": b, "store_cold": s, "pool_hit": p},
    }


# ---------------------------------------------------------------- task F
def task_f(means):
    """MODEL PROJECTION ONLY. No crossover point (N*) is solved or reported.

    preparation_s is measured once per attempt, is contaminated by the teardown /
    unload of the previous attempt's pool, and no reuse of a prepared service was
    ever measured.  The only defensible output is an explicitly hypothetical
    projection under a stated assumption, plus the plain cost-vs-N table.
    """
    P = {c: means[c]["prep_s"] for c in CONDS}
    H = {c: means[c]["biz_s"] for c in CONDS}
    Ns = [1, 2, 3, 5, 10]
    table_with = {}
    table_without = {}
    for n in Ns:
        table_with[n] = {SHORT[c]: (P[c] + n * H[c]) / n for c in CONDS}
        table_without[n] = {SHORT[c]: H[c] for c in CONDS}
    return {
        "kind": "MODEL PROJECTION, NOT A MEASUREMENT",
        "no_crossover_reported": True,
        "crossover_withheld_reason":
            "preparation_s is contaminated by the teardown/unload of the previous "
            "attempt's pool and no prepared service was ever reused, so no payback "
            "point can be identified from these data",
        "formula": "mean cost per reuse over N reuses = (sum_fill + sum_restore + "
                   "N\u00b7H)/N = P/N + H",
        "assumption": "the prepared service is reused N times unchanged, preparation "
                      "is paid exactly once, and H (business-only) is identical on "
                      "every reuse",
        "P": {SHORT[c]: P[c] for c in CONDS},
        "H": {SHORT[c]: H[c] for c in CONDS},
        "N_values": Ns,
        "cost_with_preparation": {str(k): v for k, v in table_with.items()},
        "cost_without_preparation": {str(k): v for k, v in table_without.items()},
    }


# ---------------------------------------------------------------- task G
def task_g(recs):
    out = {}
    flagged_all = []
    for c in CONDS:
        v = sorted([r for r in recs if r["condition"] == c], key=lambda r: r["block"])
        xs = [r["biz_s"] for r in v]
        m = med(xs)
        mad = med([abs(x - m) for x in xs])
        rows = []
        for r in v:
            x = r["biz_s"]
            dev = abs(x - m)
            ratio = (dev / mad) if mad > 0 else (0.0 if dev == 0 else float("inf"))
            rz = ratio / 1.4826 if mad > 0 else (0.0 if dev == 0 else float("inf"))
            flag = ratio > 3.0
            flagz = rz > 3.0
            rows.append({"attempt_id": r["attempt_id"], "block": r["block"],
                         "biz_s": x, "median": m, "mad": mad,
                         "abs_dev": dev, "dev_over_mad": ratio,
                         "robust_z": rz, "flag_gt3MAD": flag, "flag_robust_z": flagz})
            if flag:
                flagged_all.append(r["attempt_id"])
        out[c] = {"median_biz_s": m, "mad_biz_s": mad, "n": len(xs), "attempts": rows}
    return out, flagged_all


# ---------------------------------------------------------------- main
def main():
    check_only = "--check" in sys.argv[1:]
    followups_only = "--followups-only" in sys.argv[1:]
    paired_stats_only = "--paired-stats-only" in sys.argv[1:]

    print("ledger:", LEDGER)
    led, c23, excl, attempts, problems = load_population()
    print("ledger rows: %d | C23 included: %d | excluded: %d"
          % (len(led["rows"]), len(c23), len(excl)))
    print("ledger sha256:", sha256_file(LEDGER))

    print("\n== EXCLUDED ROWS (not C23) ==")
    for r in sorted(excl, key=lambda x: x["order"]):
        rev = r.get("protocol_revision")
        print("  #%2d %-26s %-24s block %-2s status=%-7s revision=%s"
              % (r["order"], r["attempt_id"], r["condition"], r["block"],
                 r["status"], rev if rev is not None else "null"))

    if problems:
        print("\nSTOP: data integrity problems:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)

    recs = extract(attempts)

    print("\n== C23 ATTEMPTS ==")
    print("%-26s %-24s blk %9s %9s %9s %9s" %
          ("attempt", "condition", "prep", "route", "first", "biz"))
    for r in sorted(recs, key=lambda x: (x["block"], x["condition"])):
        print("%-26s %-24s %3d %9.2f %9.2f %9.2f %9.2f"
              % (r["attempt_id"], r["condition"], r["block"],
                 r["prep_s"], r["route_s"], r["first_tok_s"], r["biz_s"]))

    keysets = [frozenset(r["metrics"].keys()) for r in attempts]
    inter = set.intersection(*[set(k) for k in keysets]) if keysets else set()
    union = set.union(*[set(k) for k in keysets]) if keysets else set()
    print("\nmetrics keys: common=%d union=%d identical_across_attempts=%s"
          % (len(inter), len(union), len(inter) == len(union)))
    print("common keys:", ", ".join(sorted(inter)))

    print("\n== origin offset: command_to_* minus trigger_to_* (register round-trip) ==")
    print("%-28s %14s %14s %14s" % ("condition", "offset first", "offset biz", "max |first|"))
    for c in CONDS:
        v = [r for r in recs if r["condition"] == c]
        d = [r["first_tok_s"] - r["trigger_first_tok_s"] for r in v]
        db = [r["biz_s"] - r["trigger_biz_s"] for r in v]
        print("  %-28s %14.4f %14.4f %14.4f"
              % (c, mean(d), mean(db), max(abs(x) for x in d)))

    gate_status, gate_detail = check_gates(recs)
    global GATE_STATUS
    GATE_STATUS = gate_status

    table, blocks, conds_present, counts, means = task_a(recs)
    print("\n== TASK A: block x condition matrix (biz_s) ==")
    print(table)

    blocks_in, out_blocks, ledger_only, reason = task_b(blocks, conds_present)
    print("\n== TASK B: complete-block paired set ==")
    print("  in :", blocks_in)
    print("  out:", out_blocks, "| no C23 at all:", ledger_only)
    for b, why in sorted(reason.items()):
        print("   block %s: %s" % (b, why))

    results, mc, vals = paired_stats(recs, blocks_in)

    print("\n== TASK C: paired contrasts ==")
    for e in results:
        print("\n  [%d] %s" % (e["id"], e["label"]))
        for w in WINDOWS:
            d = e["windows"][w]
            print("    %-22s n=%d mean=%+8.2f sd=%7.2f median=%+8.2f "
                  "CI=[%+8.2f,%+8.2f] dz=%s ratios=%s p_raw=%.4f p_holm=%.4f"
                  % (w, d["n_pairs"], d["mean_diff"], d["sd_diff"], d["median_diff"],
                     d["ci95_low"], d["ci95_high"], f2(d["cohens_dz"], 2),
                     f2(d["ratio_of_paired_means"], 3), d["p_raw"], d["p_holm"]))

    print("\n== TASK D: Holm ==")
    for w in WINDOWS:
        print(" ", w)
        for c in mc[w]["contrasts"]:
            print("    %-45s p_raw=%.4f p_holm=%.4f %s"
                  % (c["label"], c["p_raw"], c["p_holm"],
                     "SURVIVES" if c["survives_holm_0.05"] else "ns"))

    e_res = task_e(means)
    sens3 = contrast3_sensitivity(recs, blocks_in)
    pf_rows, pf_min_n = power_floor()

    print("\n== FOLLOW-UP 1: contrast 3 under both pairing rules ==")
    for name in ("n6_complete_block", "n7_maximal_pair"):
        for w in WINDOWS:
            d = sens3["rules"][name][w]
            print("  %-18s %-22s n=%d mean=%+8.2f sd=%7.2f CI=[%+8.2f,%+8.2f] dz=%s"
                  % (name, w, d["n"], d["mean"], d["sd"], d["ci95_low"],
                     d["ci95_high"], f2(d["cohens_dz"], 2)))
    m6 = sens3["rules"]["n6_complete_block"]["including_preparation"]["mean"]
    m7 = sens3["rules"]["n7_maximal_pair"]["including_preparation"]["mean"]
    print("  block4 biz diff %+0.3f ; prep+biz diff %+0.3f"
          % (sens3["block4"]["biz_diff"], sens3["block4"]["prep_plus_biz_diff"]))
    print("  sign of prep-included estimate: %s (n6 %+.2f, n7 %+.2f)"
          % ("FLIPS" if (m6 > 0) != (m7 > 0) else "STABLE", m6, m7))

    print("\n== FOLLOW-UP 2: power floor ==")
    for r in pf_rows:
        print("  n=%d min raw p=%.5f  min Holm(4)=%.5f  attainable=%s"
              % (r["n"], r["min_raw_p"], r["min_holm_p"], r["attainable_at_0.05"]))
    print("  smallest n with Holm < 0.05:", pf_min_n)

    print("\n== TASK E: unpaired descriptive ==")
    print("  sf_cold/store_cold = %.4f   sf_cold/pool_hit = %.4f"
          % (e_res["sf_cold_over_store_cold"], e_res["sf_cold_over_pool_hit"]))

    f_res = task_f(means)
    print("\n== TASK F: model projection (NO crossover / N* reported) ==")
    print("  assumption:", f_res["assumption"])
    print("  %-4s" % "N" + "".join("%12s" % SHORT[c] for c in CONDS))
    for n in f_res["N_values"]:
        row = f_res["cost_with_preparation"][str(n)]
        print("  %-4d" % n + "".join("%12.2f" % row[SHORT[c]] for c in CONDS))
    print("  business-only H (P excluded):",
          "  ".join("%s=%.2f" % (SHORT[c], f_res["H"][SHORT[c]]) for c in CONDS))

    g_res, flagged = task_g(recs)
    print("\n== TASK G: anomalies ==")
    for c in CONDS:
        g = g_res[c]
        print("  %-28s median=%.2f MAD=%.2f  flagged=%s" %
              (c, g["median_biz_s"], g["mad_biz_s"],
               [a["attempt_id"] for a in g["attempts"] if a["flag_gt3MAD"]] or "none"))
    print("  block 8:", [(r["attempt_id"], round(r["biz_s"], 2))
                         for r in sorted(recs, key=lambda x: x["block"]) if r["block"] == 8])

    if check_only:
        print("\n--check: no files written.")
        return

    os.makedirs(OUTDIR, exist_ok=True)

    # The gate verdict is always recorded on a full run (and on any failure).
    if (not followups_only and not paired_stats_only) or gate_status != "PASS":
        write_text(os.path.join(OUTDIR, "sanity-gate.md"),
                   build_gate_md(recs, gate_detail, gate_status))
        write_json(os.path.join(OUTDIR, "sanity-gate.json"),
                   {"status": gate_status, "tolerance_s": GATE_TOL,
                    "definitions": {
                        "prep_s": "metrics.preparation_s",
                        "route_s": "metrics.command_to_route_publication_s",
                        "first_tok_s": "metrics.command_to_first_token_s",
                        "biz_s": "metrics.command_to_correct_business_response_s"},
                    "detail": gate_detail})

    if gate_status != "PASS":
        print("\nSTOP: sanity gate status = %s. Wrote sanity-gate.md/.json only; "
              "no analysis outputs." % gate_status)
        return

    # ---------------- block-matrix.md
    if not followups_only and not paired_stats_only:
        write_text(os.path.join(OUTDIR, "block-matrix.md"), build_block_matrix_md(
            table, counts, means, blocks, conds_present, recs))
    # ---------------- paired-stats.json
    paired = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "population": {
            "rule": "protocol_revision == " + C23,
            "included": len(c23),
            "ledger_rows_total": len(led["rows"]),
            "excluded": [
                {"order": r["order"], "attempt_id": r["attempt_id"],
                 "condition": r["condition"], "block": r["block"],
                 "status": r["status"],
                 "protocol_revision": r.get("protocol_revision")}
                for r in sorted(excl, key=lambda x: x["order"])
            ],
            "all_c23_success": all(r.get("status") == "success" for r in c23),
        },
        "timing_definitions": {
            "origin": "unified command (/register) origin for all four primary timings",
            "prep_s": "metrics.preparation_s",
            "route_s": "metrics.command_to_route_publication_s",
            "first_tok_s": "metrics.command_to_first_token_s",
            "biz_s": "metrics.command_to_correct_business_response_s",
            "derived": ["prep_s", "route_s",
                        "post_route_to_first_token_s = first_tok_s - route_s",
                        "first_token_to_response_s = biz_s - first_tok_s"],
            "windows": {
                "business_only": "biz_s",
                "including_preparation": "prep_s + biz_s",
            },
            "trigger_origin_separate": {
                "trigger_to_first_token_s": "reported separately, never mixed",
                "trigger_to_correct_business_response_s": "reported separately, never mixed",
            },
            "note": "per-attempt computation, then average; preparation_s includes "
                    "teardown/unload of the previous attempt's pool",
        },
        "metrics_schema": {
            "keys_common": sorted(inter),
            "keys_union": sorted(union),
            "identical_across_attempts": len(inter) == len(union),
            "origin_families": {
                "note": "command origin (/register) vs trigger origin (HTTP request); "
                        "the offset is the register round-trip and is never removed by "
                        "subtracting across families",
                "per_condition": {
                    c: {
                        "command_to_first_token_s": mean(
                            [r["first_tok_s"] for r in recs if r["condition"] == c]),
                        "trigger_to_first_token_s": mean(
                            [r["trigger_first_tok_s"] for r in recs if r["condition"] == c]),
                        "offset_first_token_s": mean(
                            [r["first_tok_s"] - r["trigger_first_tok_s"]
                             for r in recs if r["condition"] == c]),
                        "command_to_correct_business_response_s": mean(
                            [r["biz_s"] for r in recs if r["condition"] == c]),
                        "trigger_to_correct_business_response_s": mean(
                            [r["trigger_biz_s"] for r in recs if r["condition"] == c]),
                        "offset_business_s": mean(
                            [r["biz_s"] - r["trigger_biz_s"]
                             for r in recs if r["condition"] == c]),
                    } for c in CONDS
                },
                "all_four_metric_keys_present": True,
            },
        },
        "sanity_gates": {"tolerance_s": GATE_TOL, "detail": gate_detail,
                         "status": gate_status, "all_pass": gate_status == "PASS",
                         "note": "all 16 published C23 means reproduced on the unified "
                                 "command origin; see sanity-gate.md"},
        "paired_set": {
            "rule": "complete-block: all four conditions present and successful at C23",
            "blocks_in": blocks_in,
            "blocks_out": sorted(out_blocks),
            "blocks_out_reason": {str(k): v for k, v in sorted(reason.items())},
            "no_c23_blocks": ledger_only,
        },
        "condition_means": {
            c: {"n": counts[c], **{t: means[c][t] for t in TIMINGS},
                "non_overlap": {
                    "prep_s": means[c]["prep_s"],
                    "route_s": means[c]["route_s"],
                    "post_route_to_first_token_s": means[c]["first_tok_s"] - means[c]["route_s"],
                    "first_token_to_response_s": means[c]["biz_s"] - means[c]["first_tok_s"],
                },
                "including_preparation": means[c]["prep_s"] + means[c]["biz_s"]}
            for c in CONDS
        },
        "contrasts": results,
        "multiple_comparisons": mc,
        "task_e_unpaired_descriptive": e_res,
        "contrast3_pairing_sensitivity": build_c3_json(sens3),
        "power_floor": build_power_floor_json(pf_rows, pf_min_n),
    }
    if paired_stats_only:
        # Amend in place: every pre-existing key must keep its value, so the
        # original generated_utc is preserved and the amendment is recorded
        # under a new key.
        pj = os.path.join(OUTDIR, "paired-stats.json")
        if os.path.exists(pj):
            with open(pj, "r", encoding="utf-8") as fh:
                old = json.load(fh)
            paired["generated_utc"] = old.get("generated_utc", paired["generated_utc"])
            paired["amended_utc"] = datetime.now(timezone.utc).isoformat()
            paired["amendment_note"] = (
                "added contrast3_pairing_sensitivity and power_floor only; all other "
                "keys unchanged from generated_utc")
        write_json(pj, paired)
    elif not followups_only:
        write_json(os.path.join(OUTDIR, "paired-stats.json"), paired)
    if not paired_stats_only:
        write_text(os.path.join(OUTDIR, "paired-contrasts.md"),
                   build_paired_md(results, mc, e_res, sens3, pf_rows, pf_min_n))

    # ---------------- amortization
    if not followups_only and not paired_stats_only:
        write_json(os.path.join(OUTDIR, "amortization.json"), f_res)
    if not paired_stats_only:
        write_text(os.path.join(OUTDIR, "amortization.md"), build_amort_md(f_res))

    # ---------------- anomalies
    if not followups_only and not paired_stats_only:
        write_text(os.path.join(OUTDIR, "anomalies.md"), build_anom_md(g_res, recs, flagged))

    # ---------------- MANIFEST
    if not paired_stats_only:
        write_text(os.path.join(OUTDIR, "MANIFEST.md"), build_manifest_md(
            led, recs, attempts, gate_detail, inter, union, sens3, pf_rows, pf_min_n))

    print("\nwrote outputs to", OUTDIR, "(%s)" %
          ("follow-up revision only" if followups_only else "full run"))
    for fn in sorted(os.listdir(OUTDIR)):
        print("  ", fn, os.path.getsize(os.path.join(OUTDIR, fn)))


# ---------------------------------------------------------------- markdown builders
def build_gate_md(recs, detail, status):
    L = []
    L.append("# Sanity gate outcome \u2014 **%s**" % status)
    L.append("")
    L.append("Rule: the four published C23 condition means "
             "(prep_s / route_s / first_tok_s / biz_s) must be reproduced within "
             "**%.2f s**; otherwise stop and report the gap rather than proceed."
             % GATE_TOL)
    L.append("")
    L.append("Primary definitions, all on the unified command (/register) origin: "
             "`prep_s = preparation_s`, `route_s = command_to_route_publication_s`, "
             "`first_tok_s = command_to_first_token_s`, "
             "`biz_s = command_to_correct_business_response_s`.")
    L.append("")
    L.append("## Gate check")
    L.append("")
    rows = []
    for c in CONDS:
        d = detail[c]
        g = d["gate"]
        k = d["computed"]
        rows.append([SHORT[c], d["n"],
                     f2(g["prep_s"]), f2(k["prep_s"]), "%+.4f" % (k["prep_s"] - g["prep_s"]),
                     f2(g["route_s"]), f2(k["route_s"]), "%+.4f" % (k["route_s"] - g["route_s"]),
                     f2(g["first_tok_s"]), f2(k["first_tok_s"]),
                     "%+.4f" % (k["first_tok_s"] - g["first_tok_s"]),
                     f2(g["biz_s"]), f2(k["biz_s"]), "%+.4f" % (k["biz_s"] - g["biz_s"]),
                     "PASS" if d["verdict"] == "PASS" else "FAIL"])
    L.append(md_table(["cond", "n", "gate prep", "calc prep", "\u0394",
                       "gate route", "calc route", "\u0394",
                       "gate first", "calc first", "\u0394",
                       "gate biz", "calc biz", "\u0394", "verdict"], rows))
    L.append("")
    L.append("Maximum absolute deviation across all 16 means: **%.4f s** (tolerance "
             "%.2f s)." % (max(d["max_abs_delta"] for d in detail.values()), GATE_TOL))
    L.append("")
    L.append("## Origin check \u2014 the two metric families are kept separate")
    L.append("")
    L.append("`command_to_*` starts at the /register command; `trigger_to_*` starts when "
             "the HTTP request is issued. The offset `command_to_first_token_s \u2212 "
             "trigger_to_first_token_s` is the register round-trip. It is not zero, so the "
             "two families must never be subtracted from one another.")
    L.append("")
    rows = []
    for c in CONDS:
        d = detail[c]
        k = d["computed"]
        t = d["trigger_origin"]
        rows.append([SHORT[c], f2(k["first_tok_s"]), f2(t["trigger_first_tok_s"]),
                     "%+.3f" % t["offset_first_s"],
                     f2(k["biz_s"]), f2(t["trigger_biz_s"]), "%+.3f" % t["offset_biz_s"]])
    L.append(md_table(["cond", "command first", "trigger first", "offset",
                       "command biz", "trigger biz", "offset"], rows))
    L.append("")
    offs = sorted((r["cmd_first"] - r["trigger_first_tok_s"], r["attempt_id"], r["block"])
                  for r in recs)
    L.append("- 25 of 26 attempts: offset **%.3f s \u2013 %.3f s**"
             % (offs[0][0], offs[-2][0]))
    L.append("- outlier: **%s** (block %d): **%.3f s** \u2014 the register round-trip for "
             "that attempt was ~17x longer" % (offs[-1][1], offs[-1][2], offs[-1][0]))
    L.append("")
    L.append("The trigger-origin pair is reported above as a separate quantity only; it "
             "does not enter any decomposition, contrast, CI or effect size in this "
             "reanalysis.")
    L.append("")
    if status != "PASS":
        L.append("## Consequence")
        L.append("")
        L.append("Gate failed: no analysis outputs were written.")
        L.append("")
    return "\n".join(L)



def build_block_matrix_md(table, counts, means, blocks, conds_present, recs):
    L = []
    L.append("# Task A \u2014 C23 block \u00d7 condition matrix (biz_s)")
    L.append("")
    L.append(gate_banner())
    L.append("")
    L.append("Population: ledger rows with `protocol_revision == "
             "`v13-canonical23-qualified-disk-jit-seed`` \u2014 26 of 40 rows.")
    L.append("Cell value = `metrics.command_to_correct_business_response_s` "
             "(biz_s, seconds; command /register origin).")
    L.append("All 26 C23 attempts are `status=success`, so \u201cpresent\u201d and "
             "\u201csucceeded\u201d coincide; a cell is non-empty only where a successful "
             "C23 attempt exists.")
    L.append("`\u2014` = no C23 attempt for that block/condition (see Task B).")
    L.append("")
    L.append(table)
    L.append("")
    L.append("Condition n: " + ", ".join("%s=%d" % (SHORT[c], counts[c]) for c in CONDS))
    L.append("")
    L.append("## Table A2 \u2014 C23 condition means, mutually exclusive decomposition")
    L.append("")
    L.append("Unified command (/register) origin, computed per attempt then averaged "
             "(never average-then-subtract).")
    L.append("")
    rows = []
    for c in CONDS:
        m = means[c]
        rows.append([SHORT[c], counts[c], f2(m["prep_s"]), f2(m["route_s"], 1),
                     f2(m["first_tok_s"] - m["route_s"], 1),
                     f2(m["biz_s"] - m["first_tok_s"], 1),
                     f2(m["biz_s"], 1), f2(m["prep_s"] + m["biz_s"], 1)])
    L.append(md_table(["condition", "n", "prep_s", "route_s",
                       "post_route_to_first_token", "first_token_to_response",
                       "biz_s", "prep+biz"], rows))
    L.append("")
    L.append("## Table A3 \u2014 every C23 attempt")
    L.append("")
    rows = []
    for r in sorted(recs, key=lambda x: (x["block"], CONDS.index(x["condition"]))):
        rows.append([r["block"], SHORT[r["condition"]], r["attempt_id"],
                     f2(r["prep_s"]), f2(r["route_s"]), f2(r["first_tok_s"]),
                     f2(r["biz_s"])])
    L.append(md_table(["block", "condition", "attempt_id", "prep_s", "route_s",
                       "first_tok_s", "biz_s"], rows))
    L.append("")
    return "\n".join(L)


def build_paired_md(results, mc, e_res, sens3, pf_rows, pf_min_n):
    L = []
    L.append("# Tasks C/D \u2014 paired block contrasts (C23)")
    L.append("")
    L.append(gate_banner())
    L.append("")
    L.append("Paired set = complete-block rule (all four conditions present and successful).")
    L.append("Bootstrap CI: 10,000 resamples of BLOCKS, seed 20261024, percentile method.")
    L.append("p: exact two-sided paired sign-flip permutation over all 2^6 = 64 assignments.")
    L.append("Holm-Bonferroni within the family of 4 contrasts, separately per window.")
    L.append("")
    L.append("Windows: `business_only = biz_s` (command origin); "
             "`including_preparation = prep_s + biz_s`. **`prep_s` also contains the "
             "teardown / unload of the previous attempt's pool when `pool_loaded` was true "
             "at attempt start (see MANIFEST.md \u2014 Documented limitations), so the "
             "second window is not a pure fresh-service cost.**")
    L.append("")
    for e in results:
        L.append("## %d. %s" % (e["id"], e["label"]))
        L.append("")
        rows = []
        for w in WINDOWS:
            d = e["windows"][w]
            rows.append([w, d["n_pairs"], f2(d["mean_diff"]), f2(d["sd_diff"]),
                         f2(d["median_diff"]), "[%s, %s]" % (f2(d["ci95_low"]), f2(d["ci95_high"])),
                         "yes" if d["ci_excludes_zero"] else "no",
                         f2(d["cohens_dz"]), f2(d["ratio_of_paired_means"], 3),
                         f2(d["median_per_block_ratio"], 3),
                         "%.4f" % d["p_raw"], "%.4f" % d["p_holm"]])
        L.append(md_table(["window", "n", "mean diff", "sd", "median",
                           "95% CI", "CI excl 0", "d_z",
                           "ratio of means", "median block ratio",
                           "p raw", "p Holm"], rows))
        L.append("")
        L.append("Per-block differences:")
        L.append("")
        rows = []
        for blk in e["windows"]["business_only"]["blocks"]:
            rows.append([blk, f2(e["windows"]["business_only"]["per_block_diff"][str(blk)]),
                         f2(e["windows"]["including_preparation"]["per_block_diff"][str(blk)])])
        L.append(md_table(["block", "business_only diff", "including_preparation diff"], rows))
        L.append("")
    L.append("## Follow-up 1 \u2014 contrast 3 under two legitimate pairing rules "
             "(sensitivity, no winner)")
    L.append("")
    L.append("Contrast 3 (`store_pool_hit \u2212 store_disk_cold`) needs only those two "
             "conditions. Block 4 contains both, so two pairing rules are both legitimate:")
    L.append("")
    L.append("- **n=6 complete-block** \u2014 blocks 5\u201310 (all four conditions present).")
    L.append("- **n=7 maximal pair** \u2014 blocks 4\u201310, adding block 4.")
    L.append("")
    rows = []
    for name, label in (("n6_complete_block", "n=6 complete-block"),
                        ("n7_maximal_pair", "n=7 maximal pair")):
        for w in WINDOWS:
            d = sens3["rules"][name][w]
            rows.append([label, w, d["n"], "%+.2f" % d["mean"], f2(d["sd"]),
                         "[%s, %s]" % (f2(d["ci95_low"]), f2(d["ci95_high"])),
                         "yes" if d["ci_excludes_zero"] else "no",
                         f2(d["cohens_dz"]), f2(d["ratio_of_paired_means"], 3),
                         f2(d["median_per_block_ratio"], 3)])
    L.append(md_table(["pairing rule", "window", "n", "mean diff", "sd", "95% CI",
                       "CI excl 0", "d_z", "ratio of means", "median block ratio"], rows))
    L.append("")
    b4 = sens3["block4"]
    L.append("Block 4 contribution: `store_pool_hit_04` prep %.3f / biz %.3f vs "
             "`store_disk_cold_04` prep %.3f / biz %.3f \u2192 biz diff **%+.3f s**, "
             "prep+biz diff **%+.3f s**."
             % (b4["pool_hit"]["prep_s"], b4["pool_hit"]["biz_s"],
                b4["store_cold"]["prep_s"], b4["store_cold"]["biz_s"],
                b4["biz_diff"], b4["prep_plus_biz_diff"]))
    L.append("")
    m6 = sens3["rules"]["n6_complete_block"]["including_preparation"]["mean"]
    m7 = sens3["rules"]["n7_maximal_pair"]["including_preparation"]["mean"]
    flips = (m6 > 0) != (m7 > 0)
    L.append("**Does the sign of the prep-included estimate depend on the pairing rule? "
             "%s.** n=6 gives %+.2f s and n=7 gives %+.2f s; both are %s, so the "
             "direction is %s and only the magnitude changes (%.0f%% smaller at n=7)."
             % ("YES" if flips else "NO", m6, m7,
                "positive" if (m6 > 0 and m7 > 0) else "negative",
                "unstable" if flips else "stable",
                100.0 * abs(m6 - m7) / abs(m6)))
    L.append("")
    L.append("Disclosure: `store_disk_cold_04` is itself flagged as an anomaly in "
             "`anomalies.md` (16.2 MAD; also the attempt carrying the 10.233 s register "
             "round-trip). The n=7 estimate therefore leans on one flagged attempt. It is "
             "reported side by side, not excluded, and no post-hoc exclusion is applied "
             "anywhere in this reanalysis.")
    L.append("")
    L.append("## Holm summary")
    L.append("")
    for w in WINDOWS:
        L.append("**%s** \u2014 min attainable raw p = 2/64 = 0.03125" % w)
        L.append("")
        rows = []
        for c in mc[w]["contrasts"]:
            rows.append([c["label"], "%.4f" % c["p_raw"], "%.4f" % c["p_holm"],
                         "yes" if c["survives_holm_0.05"] else "no"])
        L.append(md_table(["contrast", "p raw", "p Holm", "survives 0.05"], rows))
        L.append("")
    L.append("## Follow-up 2 \u2014 power floor of the family-wise correction")
    L.append("")
    L.append("Exact two-sided paired sign-flip test: the most extreme configuration "
             "(all blocks the same sign) has probability 2/2^n, so that is the minimum "
             "attainable raw p. Holm multiplies the smallest p by the family size (4).")
    L.append("")
    rows = []
    for r in pf_rows:
        rows.append([r["n"], "2/%d = %.5f" % (2 ** r["n"], r["min_raw_p"]),
                     "%.5f" % r["min_holm_p"],
                     "yes" if r["attainable_at_0.05"] else "no"])
    L.append(md_table(["blocks n", "min two-sided raw p", "min Holm p (family of 4)",
                       "Holm < 0.05 attainable"], rows))
    L.append("")
    L.append("With a family of four paired contrasts and an exact sign-flip test, at "
             "least **%d complete blocks** are required before family-wise significance "
             "at alpha=0.05 is attainable at all." % pf_min_n)
    L.append("")
    L.append("This is a design constraint for any future pre-registration: the present "
             "C23 set has 6 complete blocks, so its Holm floor is 0.125 and no contrast "
             "can reach family-wise significance regardless of effect size.")
    L.append("")
    L.append("## Task E \u2014 unpaired ratio of means (DESCRIPTIVE, not primary)")
    L.append("")
    L.append("sf_cold biz_s / store_cold biz_s = %.3f" % e_res["sf_cold_over_store_cold"])
    L.append("")
    L.append("sf_cold biz_s / pool_hit biz_s = %.3f" % e_res["sf_cold_over_pool_hit"])
    L.append("")
    L.append("These are ratios of unconditionally pooled condition means. They ignore "
             "block pairing and are reported for reference only; the paired contrasts "
             "above are the primary estimate.")
    L.append("")
    return "\n".join(L)


def build_amort_md(f):
    L = []
    L.append("# Task F \u2014 cost-vs-N **model projection** (no crossover, no N*)")
    L.append("")
    L.append(gate_banner())
    L.append("")
    L.append("> **This is a projection under an assumption, not a measurement.** No "
             "prepared service was ever reused in this experiment, so no payback point "
             "can be estimated from the data. No N* is reported.")
    L.append("")
    L.append("Formula: mean cost per reuse over N reuses = "
             "(P_reuse_setup + N\u00b7H)/N = P/N + H, where P is the measured "
             "`preparation_s` composite.")
    L.append("")
    L.append("**Assumption:** the prepared service is reused N times unchanged, "
             "preparation is paid exactly once, and H (business-only) is identical on "
             "every reuse.")
    L.append("")
    L.append("**P is a measured composite, not fill-only.** `preparation_s` also spans "
             "the teardown / unload of the previous attempt's pool whenever `pool_loaded` "
             "was true at attempt start: in `code/c23-reference-f176e05/formal_trials.py` "
             "the window opens at L147, the `unload_from_cpu` loop is at L159\u2013L163, "
             "and it closes at L182, with the metric computed at L225. P is therefore a "
             "composite of pool load + teardown + cache preparation, used as-is and never "
             "decomposed. See MANIFEST.md \u2014 Documented limitations.")
    L.append("")
    L.append("| condition | P (prep_s, contaminated) | H (biz_s) |")
    L.append("|---|---|---|")
    for c in CONDS:
        L.append("| %s | %s | %s |" % (SHORT[c], f2(f["P"][SHORT[c]]),
                                       f2(f["H"][SHORT[c]])))
    L.append("")
    L.append("## Mean cost vs N, WITH preparation (P/N + H) \u2014 projection")
    L.append("")
    L.append(md_table(["N"] + [SHORT[c] for c in CONDS],
                      [[n] + [f2(f["cost_with_preparation"][str(n)][SHORT[c]]) for c in CONDS]
                       for n in f["N_values"]]))
    L.append("")
    L.append("## Mean cost vs N, WITHOUT preparation (H only)")
    L.append("")
    L.append(md_table(["N"] + [SHORT[c] for c in CONDS],
                      [[n] + [f2(f["cost_without_preparation"][str(n)][SHORT[c]]) for c in CONDS]
                       for n in f["N_values"]]))
    L.append("")
    L.append("The two tables differ only by P/N. Ranking at any N is a property of the "
             "projection assumption above, not an empirical finding: with an unmeasured "
             "reuse count, no crossover is identifiable.")
    L.append("")
    return "\n".join(L)


def build_anom_md(g, recs, flagged):
    L = []
    L.append("# Task G \u2014 anomaly report (biz_s)")
    L.append("")
    L.append(gate_banner())
    L.append("")
    L.append("Criterion: |x \u2212 median| > 3 \u00d7 MAD, where MAD = median(|x \u2212 median|), "
             "computed within each condition over all C23 attempts of that condition.")
    L.append("Robust z = |x \u2212 median| / (1.4826 \u00d7 MAD) is shown for reference.")
    L.append("No attempt is excluded anywhere in this reanalysis; block 8 is retained.")
    L.append("")
    L.append("## Flagged attempts")
    L.append("")
    if flagged:
        rows = []
        for c in CONDS:
            for a in g[c]["attempts"]:
                if a["flag_gt3MAD"]:
                    rows.append([c, a["attempt_id"], a["block"], f2(a["biz_s"]),
                                 f2(a["median"]), f2(a["mad"]),
                                 f2(a["dev_over_mad"], 2), f2(a["robust_z"], 2)])
        L.append(md_table(["condition", "attempt", "block", "biz_s", "median",
                           "MAD", "abs dev/MAD", "robust z"], rows))
    else:
        L.append("None. No C23 attempt exceeds the 3-MAD threshold in any condition.")
    L.append("")
    L.append("## Block 8 (retained, not excluded)")
    L.append("")
    b8 = sorted([r for r in recs if r["block"] == 8],
                key=lambda x: CONDS.index(x["condition"]))
    L.append(md_table(["condition", "attempt", "biz_s", "condition median", "MAD",
                       "abs dev/MAD"],
                      [[SHORT[r["condition"]], r["attempt_id"], f2(r["biz_s"]),
                        f2(g[r["condition"]]["median_biz_s"]),
                        f2(g[r["condition"]]["mad_biz_s"]),
                        f2(abs(r["biz_s"] - g[r["condition"]]["median_biz_s"])
                           / g[r["condition"]]["mad_biz_s"], 2)
                        if g[r["condition"]]["mad_biz_s"] > 0 else "inf"]
                       for r in b8]))
    L.append("")
    b8_flag = [r["attempt_id"] for r in b8 if
               any(a["attempt_id"] == r["attempt_id"] and a["flag_gt3MAD"]
                   for a in g[r["condition"]]["attempts"])]
    if b8_flag:
        L.append("Block 8 IS an anomaly: %s exceeds 3 MAD. It is reported here and "
                 "**retained in every analysis**; nothing in this reanalysis drops or "
                 "reweights it." % ", ".join("`%s`" % a for a in b8_flag))
    else:
        L.append("Block 8 is **not** an anomaly under the 3-MAD criterion; no attempt "
                 "in block 8 is flagged. It is retained in every analysis regardless.")
    L.append("")
    L.append("## Per-condition detail (all C23 attempts)")
    L.append("")
    for c in CONDS:
        L.append("### %s (n=%d, median=%.2f s, MAD=%.2f s)"
                 % (c, g[c]["n"], g[c]["median_biz_s"], g[c]["mad_biz_s"]))
        L.append("")
        rows = []
        for a in g[c]["attempts"]:
            rows.append([a["attempt_id"], a["block"], f2(a["biz_s"]),
                         f2(a["abs_dev"]), f2(a["dev_over_mad"], 2),
                         f2(a["robust_z"], 2),
                         "**YES**" if a["flag_gt3MAD"] else "no"])
        L.append(md_table(["attempt", "block", "biz_s", "abs dev", "abs dev/MAD",
                           "robust z", ">3 MAD?"], rows))
        L.append("")
    L.append("## Origin note")
    L.append("")
    L.append("Every number on this page uses `command_to_correct_business_response_s` "
             "(command /register origin). The trigger-origin pair "
             "(`trigger_to_*`) is reported separately in `sanity-gate.md` and is never "
             "subtracted from a command-origin quantity.")
    neg = [r for r in recs if r["first_tok_s"] - r["route_s"] < 0]
    if neg:
        L.append("")
        L.append("Negative `first_tok_s \u2212 route_s` segments: " + ", ".join(
            "`%s` (%.2f s, block %d)" % (r["attempt_id"], r["first_tok_s"] - r["route_s"],
                                         r["block"]) for r in neg) + ".")
    L.append("")
    return "\n".join(L)


def build_manifest_md(led, recs, attempts, gate_detail, inter, union,
                      sens3, pf_rows, pf_min_n):
    import platform
    L = []
    L.append("# MANIFEST \u2014 C23 reanalysis")
    L.append("")
    L.append(gate_banner())
    L.append("")
    L.append("Generated: " + datetime.now(timezone.utc).isoformat())
    L.append("")
    L.append("## Environment")
    L.append("")
    L.append("- Host user: `%s`" % os.environ.get("USER", "unknown"))
    L.append("- Python: %s" % platform.python_version())
    L.append("- Python executable: %s" % sys.executable)
    L.append("- Platform: %s" % platform.platform())
    L.append("- numpy/pandas: not available on host; pure standard library used")
    L.append("")
    L.append("## Reproducibility parameters")
    L.append("")
    L.append("- Seed: %d" % SEED)
    L.append("- Bootstrap resamples: %d" % NBOOT)
    L.append("- Bootstrap unit: block (paired differences within block)")
    L.append("- CI method: percentile, 2.5% / 97.5%")
    L.append("- Permutation test: exact two-sided sign-flip, all 2^n assignments")
    L.append("- Multiple comparisons: Holm-Bonferroni, family of 4, per window")
    L.append("")
    L.append("## Inputs")
    L.append("")
    L.append("- Ledger: `%s`" % LEDGER)
    L.append("- Ledger sha256: `%s`" % sha256_file(LEDGER))
    L.append("- Ledger generated_at_utc: %s" % led.get("generated_at_utc"))
    L.append("- Population: `protocol_revision == %s` (%d rows)" % (C23, len(recs)))
    L.append("- Code: `%s`" % os.path.join(OUTDIR, "recompute.py"))
    L.append("- Code sha256: `%s`" % sha256_file(os.path.abspath(__file__)))
    L.append("")
    L.append("## result.json sha256 used (%d)" % len(recs))
    L.append("")
    L.append(md_table(["attempt_id", "block", "condition", "sha256"],
                      [[r["attempt_id"], r["block"], r["condition"], "`%s`" % r["result_sha256"]]
                       for r in sorted(recs, key=lambda x: (x["block"], x["condition"]))]))
    L.append("")
    L.append("All of the above hashes were recomputed from disk and matched the "
             "`result_sha256` recorded in the ledger.")
    L.append("")
    L.append("## Metrics keys observed")
    L.append("")
    L.append("- common across all 26 attempts: %d" % len(inter))
    L.append("- union: %d" % len(union))
    L.append("- identical key sets: %s" % (len(inter) == len(union)))
    L.append("")
    for k in sorted(union):
        L.append("- `%s`" % k)
    L.append("")
    L.append("## Sanity gate")
    L.append("")
    L.append("- Status: **%s**" % GATE_STATUS)
    L.append("- Tolerance: \u00b1%.2f s on all 16 published condition means"
             % GATE_TOL)
    L.append("- Origin: unified command (/register) for prep, route, first token and "
             "business response")
    L.append("- Detail: `sanity-gate.md`")
    L.append("")
    L.append("## Documented limitations")
    L.append("")
    L.append("### `preparation_s` is a composite measurement, not fill-only "
             "(verified in source)")
    L.append("")
    L.append("Source: `code/c23-reference-f176e05/formal_trials.py` "
             "(commit f176e05607d04999ab28e8ea8cd49b41270cb528, matching "
             "`code_commit` on every C23 result).")
    L.append("")
    L.append("- L147: `'preparation_begin_ns':time.monotonic_ns()` opens the window.")
    L.append("- L152: `try:` \u2014 the window is already open before the try block.")
    L.append("- L159\u2013L163: `if pool_loaded:` then a loop over all 8 identifiers "
             "calling `client.unload_from_cpu(identifier)`, then `pool_loaded = False`. "
             "**This teardown of the previous attempt's pool runs inside the preparation "
             "window.**")
    L.append("- L182: `record['preparation_complete_ns'] = time.monotonic_ns()` closes "
             "the window.")
    L.append("- L225: `'preparation_s':(preparation_complete_ns - "
             "preparation_begin_ns)/1e9` \u2014 so the recorded preparation time includes "
             "that unload.")
    L.append("")
    L.append("Conditional scope: the unload only executes when `pool_loaded` is true at "
             "attempt start, which is set at L170 (after a `store_pool_hit` "
             "`load_into_cpu`) and at L236 (after any `store_*` attempt). Attempts that "
             "start with `pool_loaded = False` carry no teardown. Either way, "
             "`preparation_s` is a measured composite, not a fill-only measurement, so "
             "it is reported as measured and is never decomposed.")
    L.append("")
    L.append("### Pairing rule sensitivity (contrast 3)")
    L.append("")
    L.append("Contrast 3 admits n=6 (complete-block) and n=7 (maximal pair, adding block "
             "4). Both are reported side by side in `paired-contrasts.md`; neither is "
             "preferred. The n=7 variant is influenced by `store_disk_cold_04`, itself a "
             "3-MAD outlier.")
    L.append("")
    L.append("### Family-wise power floor")
    L.append("")
    for r in pf_rows:
        L.append("- n=%d: min raw p = %.5f, min Holm p (4 contrasts) = %.5f%s"
                 % (r["n"], r["min_raw_p"], r["min_holm_p"],
                    "" if r["attainable_at_0.05"] else " (not significant at 0.05)"))
    L.append("")
    L.append("With a family of four paired contrasts and an exact sign-flip test, at "
             "least **%d complete blocks** are required before family-wise significance "
             "at alpha=0.05 is attainable at all." % pf_min_n)
    L.append("")
    L.append("## Re-run")
    L.append("")
    L.append("```")
    L.append("/usr/bin/python3 %s --check   # diagnostics + gate check, writes nothing"
             % os.path.join(OUTDIR, "recompute.py"))
    L.append("/usr/bin/python3 %s           # full run, rewrites all 10 files"
             % os.path.join(OUTDIR, "recompute.py"))
    L.append("/usr/bin/python3 %s --followups-only   # rewrites only the three "
             "follow-up files" % os.path.join(OUTDIR, "recompute.py"))
    L.append("```")
    L.append("")
    L.append("The delivered revisions of `paired-contrasts.md`, `amortization.md` and "
             "this file were produced with `--followups-only`, so no other output was "
             "touched.")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()

