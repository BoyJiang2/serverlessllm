from pathlib import Path
import csv
import gzip
import hashlib
import json
from statistics import mean, median

from plot_v11 import A, B1, B2, L1, ROOT, render

RUNS = ROOT / "runs"
OLD_B1 = RUNS / "B1-seq-7-agents-r1-20261112T144041Z"
OOM = RUNS / "B2-Kimi-L2-diagnostic-r1-20261115T031303Z"
KIMI = RUNS / "B2-Kimi-L2-memory-r1-20261115T041505Z"
OUT = ROOT / "derived"
GIB = 2**30
PHASES = ("full_weight_load", "weight_ingestion", "quantization_postprocess")
CLOCK = ("boot_id", "time_namespace", "clock_id")
MODELS = {"K": "Kimi-K2.6", "G": "GLM-5.1-FP8", "D": "DeepSeek-V4-Pro"}


def read(path):
    return json.loads(path.read_bytes())


def lines(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def csv_rows(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def seconds(end, start):
    return (end - start) / 1e9


def phase_spans(step, run_id, domain, lower, upper):
    events = {}
    for path in sorted((step / "events").glob("*.jsonl")):
        for event in lines(path):
            name = event["name"]
            if name.rsplit("_", 1)[0] not in PHASES:
                continue
            assert event["run_id"] == f"{run_id}-{step.name}"
            assert tuple(event[k] for k in CLOCK) == domain
            events.setdefault(name, {}).setdefault(event["rank"], []).append((event, path))
    spans = {}
    for phase in PHASES:
        begin, end = [events.get(phase + "_" + suffix, {}) for suffix in ("begin", "end")]
        pairs = {}
        for rank in sorted(begin.keys() & end.keys()):
            assert len(begin[rank]) == len(end[rank]) == 1, (run_id, step, phase, rank)
            (x, source), (y, target) = begin[rank][0], end[rank][0]
            assert source == target and all(x[k] == y[k] for k in ("pid", "process_start_ticks"))
            assert x["sequence"] < y["sequence"]
            assert lower <= x["monotonic_ns"] < y["monotonic_ns"] <= upper
            pairs[rank] = dict(begin_ns=x["monotonic_ns"], end_ns=y["monotonic_ns"],
                               duration_s=seconds(y["monotonic_ns"], x["monotonic_ns"]),
                               pid=x["pid"], start_ticks=x["process_start_ticks"],
                               source=source.relative_to(ROOT).as_posix())
        complete = set(pairs) == set(range(8))
        spans[phase] = dict(
            ranks=pairs, begin_ranks=sorted(begin), end_ranks=sorted(end),
            envelope_s=seconds(max(x["end_ns"] for x in pairs.values()),
                               min(x["begin_ns"] for x in pairs.values())) if complete else None,
            max_rank_s=max(x["duration_s"] for x in pairs.values()) if complete else None)
    return spans


def sequence(run):
    rows = []
    for record in csv_rows(run / "switch-events.csv"):
        i = int(record["step"])
        step, = [p for p in run.glob(f"{i:02d}-{record['to_model']}-*")
                 if (p / "store-step.json").is_file()]
        t2, t3, t4 = [int(record[k + "_ns"]) for k in ("t2", "t3", "t4")]
        ident = read(step / "constructed.json")["clock_identity"]
        domain = tuple(ident[k] for k in CLOCK)
        ready = read(step / "ready.json")["ns"]
        assert domain[2] == "CLOCK_MONOTONIC" and t2 <= ready <= t3 <= t4
        cache = read(step / "store-step.json")["cache"]
        for field in ("hit_bytes", "file_read_bytes", "allocated_blocks", "freed_blocks"):
            assert cache[field] == int(record[field])
        assert cache["cache_state"] == record["cache_state"]
        assert len(cache["evicted_paths"]) == int(record["evicted_paths"])
        checked = {}
        for check in read(step / "before-correctness-passed.json"):
            name = check["name"]
            prefix = step / f"before-{name}"
            assert check == read(prefix.with_name(prefix.name + "-checks.json")) and check["passed"]
            response = read(prefix.with_name(prefix.name + "-response.json"))
            assert response["response"]["id"] == f"{step.name}-before-{name}"
            assert response["response"]["model"] == "shared-" + step.name.split("-", 2)[2]
            assert all(choice["finish_reason"] for choice in response["response"]["choices"])
            assert ready <= response["started_ns"] <= response["received_ns"]
            checked[name] = dict(start_ns=response["started_ns"], end_ns=response["received_ns"])
        fill = checked.pop("kv_fill", None)
        first = min(checked, key=lambda name: checked[name]["end_ns"])
        first_end = checked[first]["end_ns"]
        last_end = max(x["end_ns"] for x in checked.values())
        assert last_end <= t4 and (not fill or fill["end_ns"] > t4)
        assert abs(seconds(t4, t2) - float(record["activation_to_correct_seconds"])) <= .00051
        t0, t1 = [int(record[k + "_ns"]) if record[k + "_ns"] else None for k in ("t0", "t1")]
        previous = rows[-1] if rows else None
        prior_fill = previous["kv_fill"] if previous else None
        overlap = None
        if previous:
            assert domain == tuple(previous["clock_domain"]) and t0 == previous["t4_all_ns"]
            assert t0 <= t1 <= t2
            if prior_fill:
                overlap = max(0, min(t1, prior_fill["end_ns"]) - max(t0, prior_fill["start_ns"])) / 1e9
        rows.append(dict(run_id=run.name, step=step.name, model=record["to_model"], t0_ns=t0,
                         t1_ns=t1, t2_ns=t2, t3_ns=t3, t4_first_ns=first_end, t4_all_ns=t4,
                         t4_last_response_ns=last_end, first_request=first, responses=checked,
                         first_s=seconds(first_end, t2), all_s=seconds(t4, t2),
                         prior_fill=prior_fill, kv_fill=fill, fill_in_t0_t1_s=overlap,
                         old_exit_s=seconds(t1, t0) if previous else None,
                         exit_to_activation_s=seconds(t2, t1) if previous else None,
                         switch_all_excl_s=(seconds(t4, t0) - float(record["observation_seconds"]))
                         if previous else None, clock_domain=domain, cache=cache,
                         phases=phase_spans(step, run.name, domain, t2, ready)))
    assert [row["model"] for row in rows] == list("KGDGKDK")
    return rows


def release(run):
    q = read(run / "qualification.json")
    path = run / "release-samples.jsonl"
    if path.exists():
        rows = lines(path)
    elif (run / "resource-samples.csv").exists():
        rows = [row for row in csv_rows(run / "resource-samples.csv")
                if row["phase"] == "final-release"]
    else:
        rows = []
    elapsed = q["final_release_seconds"]
    zero = q["final_all_gpu_zero"]
    strict = elapsed >= 60 and zero
    if rows:
        exact = seconds(int(rows[-1]["ns"]), int(rows[0]["ns"]))
        all_zero = all(all(int(row[f"gpu{i}"]) == 0 for i in range(8))
                       and int(row["compute_apps"]) == 0 for row in rows)
        assert abs(exact - elapsed) < .00051 and all_zero == zero, run
        strict = exact >= 60 and all_zero
    if "strict_release_passed" in q:
        assert strict == q["strict_release_passed"]
    return dict(run_id=run.name, release_s=elapsed, zero=zero, strict=strict,
                samples=len(rows), original_passed=q["passed"])


def table(headers, rows):
    return "\n".join("| " + " | ".join(map(str, row)) + " |"
                     for row in (headers, ["---"] * len(headers), *rows))


def fmt(x):
    return "—" if x is None else f"{x:.3f}"


def output(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")


def link(run, path, label):
    return f"[{label}](runs/{run.name}/{path})"

def main():
    OUT.mkdir(exist_ok=True)
    a, b = sequence(A), sequence(B1)
    qa, qb, q2 = [read(p / "qualification.json") for p in (A, B1, B2)]
    kv = qb["b1_host_kv"]["steps"]
    assert len(kv) == 7 and all(x["closed"] and x["zero_hit"] and x["used_after_clear"] == 0
                               and x["lookup_delta"]["hits"] == 0 for x in kv)
    assert b[3]["cache"]["hit_bytes"] == 95315935232 and a[3]["cache"]["hit_bytes"] == 0
    assert b[3]["cache"]["cache_state"] == "partial_hit" and a[3]["cache"]["cache_state"] == "full_miss"
    assert all(s["kv_fill"] is not None for s in b)
    releases = [release(p) for p in (A, B1, OLD_B1, B2, L1, OOM, KIMI)]
    l1 = read(L1 / "L1/level-result.json")
    l2 = q2["results"][0]
    assert all(x["golds_passed"] and x["pid_continuity"] for x in (l1, l2))
    assert all(x["weights_cpu_backup_bytes"] == 0 for x in l2["records"])
    loads, wakes = l2["records"][:3], l2["records"][3:]
    assert [x["stage"]["kind"] for x in loads + wakes] == ["load"] * 3 + ["wake"] * 4
    mounts = [x for x in read(B2 / "L2/02-D-created.json")["inspect"]["Mounts"]
              if "/b2-v4-sink-router-overlay/" in x["Source"]]
    assert {Path(x["Source"]).name for x in mounts} == {"deepseek_v4.py", "meta.py", "v4_source_pins.json"}
    assert all(not x["RW"] for x in mounts)

    historical = read(ROOT / "report-inputs/historical.json")
    trials = read(ROOT / "report-inputs/v4-historical-store-cold.json")
    assert [row["block"] for row in trials] == list(range(2, 11))
    v4_cold, = [row for row in historical if row["model"] == MODELS["D"]
                and row["condition"] == "Store 冷读"]
    assert len(trials) == v4_cold["n"] and round(mean(row["seconds"] for row in trials), 3) == v4_cold["a_seconds"]
    manifest = read(ROOT / "report-inputs/run-manifest.json")
    assert set(manifest["runs"]) == {r["run_id"] for r in releases}
    index = ROOT / "report-inputs/evidence-index.csv.gz"
    assert hashlib.sha256(index.read_bytes()).hexdigest() == manifest["index_sha256"]
    host = read(ROOT / "report-inputs/host-observation.json")
    assert all(tuple(row["clock_domain"]) == (host["boot_id"], host["time_namespace"],
                                              "CLOCK_MONOTONIC") for row in a + b)
    failed = read(OOM / "qualification.json")
    oom = any(event["Action"] == "oom" for event in lines(OOM / "docker-events.jsonl"))
    audit = read(KIMI / "audit.json")
    assert oom and not failed["passed"] and read(KIMI / "qualification.json")["passed"]
    phase_trace = read(KIMI / "trace-summary.json")["phases"]
    samples = [json.loads(line) for line in gzip.decompress(
        (KIMI / "resource-samples.jsonl.gz").read_bytes()).splitlines()]
    wake_samples = [x for x in samples if x["phase"] == "L2-01-K:wake" and "cgroup_memory" in x]
    peak = max(wake_samples, key=lambda x: int(x["cgroup_memory"]["current"]))
    stat = peak["cgroup_memory"]["stat"]
    mem = read(KIMI / "memory-summary.json")["phase_memory"]["L2-01-K:wake"]
    assert peak["wall_ns"] == mem["peak_sample_wall_ns"]
    assert int(peak["cgroup_memory"]["current"]) == mem["sampled_peak_bytes"]
    assert stat == mem["memory_stat_at_peak"] and stat["workingset_refault_file"] == 184923939
    assert all(x["cgroup_memory"]["events"]["oom_kill"] == 0 for x in wake_samples)
    rss_kib = {rank: int(peak["cgroup_processes"][str(pid)]["RssAnon"].split()[0])
               for rank, pid in audit["worker_pids"].items()}
    rss = {rank: value / 2**20 for rank, value in rss_kib.items()}
    refault = stat["workingset_refault_file"] * host["page_bytes"] / GIB
    data = dict(A=a, B1=b, host_kv=kv, release=releases, l1=l1["records"],
                l2_load=loads, l2_wake=wakes, overlay_mounts=mounts, history=historical,
                kimi=dict(audit=audit, peak_bytes=mem["sampled_peak_bytes"],
                          peak_stat=stat, rss_anon_kib=rss_kib, rss_anon_gib=rss, refault_gib=refault,
                          page_bytes=host["page_bytes"], failed_oom=oom, phases=phase_trace,
                          failed_trace=read(OOM / "trace-summary.json")))
    output("report-data.json", data)
    render(data)

    parts = ["# V11 三模型切换复现实验报告", """## 实验结论

A 和 B1 各完成同一固定序列的七段业务校验。A 验证常驻权重 Store 的空间复用，B1 加入 LMCache Host KV 写入与清理。B2-L1 验证 V4 单模型的 CPU 备份唤醒，B2-L2 验证原 Worker 从 checkpoint 恢复。Kimi 单模型补测在 1664 GiB 容器上限下通过。

qjhs003 使用 8×H20 和 TP8。七段为 Kimi → GLM → V4 → GLM → Kimi → V4 → Kimi。B1 正文、表和图统一使用 **052724Z**，带 HMA 补丁的 LMCache 0.5.6。144041Z 保留为历史证据。图的序号从 1 开始，原始目录从 00 开始。

**计时口径。** t2 是控制器放行 Backend 启动的 CLOCK_MONOTONIC 时间。t4_first 是有配对通过金标的首个完整业务响应的 received_ns，经段目录、模型和请求 ID 绑定。归档 Backend 的 boot ID 和 time namespace 与同一启动周期的 qjhs003 宿主读回一致，Worker 事件也逐条核对。t4_all 沿用原 t4，表示控制器观察到全套业务响应及检查文件，包含轮询。B1 的 kv_fill 在全套业务之后。B2 Wake 到唤醒回执结束，金标随后验证。"""]
    for label, run, rows, graph in (("A", A, a, "a-activation"), ("B1", B1, b, "a-b1-sequence")):
        parts.append(f"""## {label} · {'常驻权重 Store' if label == 'A' else '常驻权重 Store + Host KV'}

### 数据 1 · 启动和切换

{table(["段", "首个请求", "t4_first − t2（s）", "t4_all − t2（s）", "全套切换扣观察（s）"],
        [(r["step"], r["first_request"], fmt(r["first_s"]), fmt(r["all_s"]),
          fmt(r["switch_all_excl_s"])) for r in rows])}

七段首个响应均值 **{mean(r['first_s'] for r in rows):.3f} s**，全套均值 **{mean(r['all_s'] for r in rows):.3f} s**。全套切换由上一段 t4_all 计至本段 t4_all，另扣原协议观察时间。

![{label} 启动图](figures/{graph}.png)

### 数据 2 · 权重 Store 状态

{table(["段", "cache_state", "命中（GiB）", "读取（GiB）", "分配块", "归还块", "驱逐路径"],
        [(r["step"], c["cache_state"], f"{c['hit_bytes']/GIB:.2f}", f"{c['file_read_bytes']/GIB:.2f}",
          c["allocated_blocks"], c["freed_blocks"], len(c["evicted_paths"]))
         for r in rows for c in [r["cache"]]])}

{link(run, 'switch-events.csv', '原时间戳')} · {link(run, 'qualification.json', '原验收')}""")
        if label == "A":
            parts.append("![A 权重读取与池块](figures/a-store.png)")
            historical_a_runs = [RUNS / run_id for run_id in (
                "A-seq-7-1-r1-20261111T081622Z", "A-seq-8-1-r1-20261111T120359Z",
                "A-seq-10-1-r1-20261111T142600Z")]
            historical_a = []
            for run in historical_a_runs:
                raw = run / "release-samples.jsonl"
                samples = lines(raw)
                recorded = release(run)
                exact = seconds(samples[-1]["ns"], samples[0]["ns"])
                qualification = read(run / "qualification.json")
                assert recorded["original_passed"] and recorded["zero"] and not recorded["strict"]
                historical_a.append([f"[{run.name}]({raw.relative_to(ROOT).as_posix()})",
                                     qualification["activations"], fmt(exact), len(samples),
                                     hashlib.sha256(raw.read_bytes()).hexdigest()])
            parts.append("历史 A 的三轮各通过本轮业务验收，共 28/28 段。八卡均归零，三轮原始采样跨度均低于 60 秒。"
                         "上方同序列 A 是独立的七段对照。\n\n" + table(
                             ["历史轮次与原始释放采样", "有效段数", "零显存观察（s）", "采样数", "原件 SHA-256"],
                             historical_a))
    parts.append(f"""### B1 填充与旧 Backend 退出

16 GiB Host KV 池在准备阶段初始化。Backend 就绪后先跑业务金标，再以 kv_fill 推理请求生成 KV，验证 Host 写入和清理。七段 Host KV 命中均为 0 token。

{table(["下一段", "A t0→t1（s）", "B1 t0→t1（s）", "A t1→t2（s）", "B1 t1→t2（s）",
        "B1 上一段 kv_fill 总长（s）", "B1 落在 t0→t1 的 kv_fill（s）"],
        [(r["step"], fmt(x["old_exit_s"]), fmt(r["old_exit_s"]), fmt(x["exit_to_activation_s"]),
          fmt(r["exit_to_activation_s"]), fmt(seconds(r["prior_fill"]["end_ns"], r["prior_fill"]["start_ns"])),
          fmt(r["fill_in_t0_t1_s"])) for x, r in zip(a[1:], b[1:])])}

t0 等于上一段 t4_all，t1 是旧 Backend 退出。以 max(0, min(t1, fill_end) − max(t0, fill_start)) 计算实际相交时长。

{table(["段", "Host KV 峰值（MiB）", "提交 token", "命中 token", "清理后字节"],
        [(r["step"], f"{k['peak_bytes']/2**20:.2f}", k["committed_tokens"],
          int(k["lookup_delta"]["hits"]), k["used_after_clear"]) for r, k in zip(b, kv)])}

![B1 Host KV 写入与清理](figures/b1-host-kv.png)

### A 与 B1 的逐段差值

{table(["段", "首个响应 B1−A（s）", "全套 B1−A（s）"],
        [(y["step"], fmt(y["first_s"]-x["first_s"]), fmt(y["all_s"]-x["all_s"])) for x, y in zip(a, b)])}

B1 的 03-G-glm 命中 rank 6 的 **88.77 GiB**，读取 **621.39 GiB**。A 对应段为全未命中，读取 **710.16 GiB**。V4 的 gpu_memory_utilization 为 A **{qa['gpu_memory_utilization']['D']}**、B1 **{qb['gpu_memory_utilization']['D']}**。读取状态、显存配置和填充等差异共同影响观测，无法单独量化 LMCache 成本。""")

    phase_rows = [(g, r["step"], *(fmt(r["phases"][p]["envelope_s"]) for p in PHASES))
                  for g, rows in (("A", a), ("B1", b)) for r in rows]
    counts = [(g, MODELS[k], *(sum(x["phases"][p]["envelope_s"] is not None for x in rows if x["model"] == k)
                             for p in PHASES))
              for g, rows in (("A", a), ("B1", b)) for k in MODELS]
    parts.append(f"""## 八 rank 权重阶段（秒）

同一 run、时钟域和 Worker 身份内配对 begin/end。八 rank 齐全才计算最晚结束减最早开始。派生 JSON 提供逐 rank 起止、最大单 rank 时长和原始路径。破折号对应 null。

{table(["组", "段", *PHASES], phase_rows)}

{table(["组", "模型", *(p + " 有效 n" for p in PHASES)], counts)}

完整加载接点覆盖 Worker 调用的加载器 load_model()，包括构造与必要恢复步骤，设备初始化和 KV 分配在此窗口外。Kimi 摄入包住 load_dict_owned()，有单独的 restore_postprocess，缺少本表的 quantization_postprocess 接点。GLM 摄入包住 load_weights()，后处理包住 process_weights_after_loading()。V4 后处理包含分组权重恢复及 H2D，摄入未覆盖全部专家权重传输。重叠阶段各自展示。

[Kimi 加载器](../v7/vllm_kimi20_loader.py) · [GLM 加载器](../v7/vllm_glm20_loader.py) · [V4 加载接点](../v7/v7_trace.py)

## 历史参照

{table(["模型", "条件", "有效 n", "A（s）", "提交", "端点与准入"],
        [(r["model"], r["condition"], r["n"], r["a_seconds"], r["commit"], r["boundary"]) for r in historical])}

{table(["V11 模型", "每组 n", "A 首个响应均值（s）", "B1 首个响应均值（s）",
        "A 全套均值（s）", "B1 全套均值（s）"],
        [(name, sum(r["model"] == model for r in a),
          *(fmt(mean(r[field] for r in rows if r["model"] == model))
            for field in ("first_s", "all_s") for rows in (a, b))) for model, name in MODELS.items()])}

历史逐次控制缓存。V11 A/B1 在常驻 Store 内自然复用或重读权重，OS 页缓存冷暖未逐段验证。V11 首个响应在 Backend 内记录，历史以服务/控制器接收为终点。审阅提到的 V4 236.737 s 在指定提交中无对应指标，已核实该提交的 Store 冷读 A 为 223.752 s。236.737 s 保留为待核值。

[GLM 历史参照](https://github.com/BoyJiang2/serverlessllm/blob/15d93417f24bdc00291c791d7f95bdd50dd7725e/comparison/evidence/three-model-final-20261108/REPORT.md) · [Kimi/V4 历史参照](https://github.com/BoyJiang2/serverlessllm/blob/d3c10bda437b29d821b4845ca6a79c39b00fb975/comparison/evidence/three-model-final-20261108/REPORT.md) · [V4 九条历史冷读样本](report-inputs/v4-historical-store-cold.json)""")
    failed_release = next(x["release_s"] for x in releases if x["run_id"] == OOM.name)
    prior_trace = read(OOM / "trace-summary.json")["phases"]
    prior_init = prior_trace["initialize"]["all_rank_envelope_seconds"]
    assert prior_trace["reload"]["all_rank_envelope_seconds"] is None
    parts.append(f"""## B2 · 原生 Sleep / Wake

### B2-L1 · CPU 权重备份

V4 单模型回到原 Worker，金标通过。CPU 权重备份 **{l1['records'][0]['weights_cpu_backup_bytes']/GIB:.2f} GiB**，首次 Sleep **{l1['records'][0]['sleep_seconds']:.3f} s**，Wake **{l1['records'][1]['stage']['seconds']:.3f} s**。多模型 L1 受主机内存容量限制。

![B2-L1 单模型](figures/b2-l1-timing.png)

### B2-L2 · 正式七段

{table(["原段", "模型", "首次加载至就绪（s）"],
        [(x["step"], MODELS[x["model"]], fmt(x["stage"]["seconds"])) for x in loads])}

{table(["原段", "模型", "唤醒至回执（s）", "重载 RPC（s）"],
        [(x["step"], MODELS[x["model"]], fmt(x["stage"]["seconds"]),
          fmt(x["stage"]["reload_seconds"])) for x in wakes])}

首次加载 n=3，均值 **{mean(x['stage']['seconds'] for x in loads):.3f} s**。唤醒 n=4，均值 **{mean(x['stage']['seconds'] for x in wakes):.3f} s**。原 Worker PID 连续，全七段金标通过，权重 CPU 备份为零。V4 实际容器创建回执中的三条只读 bind 均指向 b2-v4-sink-router-overlay，包含 deepseek_v4.py、meta.py 和 v4_source_pins.json。

![B2-L2 首次加载及原 Worker 唤醒](figures/b2-l2-wake.png)

{link(B2, 'qualification.json', '正式回执')} · {link(B2, 'L2/02-D-created.json', 'V4 容器挂载回执')}

### 11 月 15 日 Kimi L2 单模型诊断

{table(["运行", "RAM 上限（GiB）", "Wake（s）", "重载 RPC（s）", "验收"],
        [("031303Z", 1400, "—", "—", "rank 5 重载期间退出，Docker 记录 OOM"),
         ("041505Z", 1664, fmt(audit["wake_seconds"]), fmt(audit["reload_seconds"]),
          "八 rank 身份连续、四项原金标通过，正常退出")])}

031303Z 八 rank 重载初始化包络 {prior_init:.6f} s，没有完整重载端点，最终全卡归零 {failed_release:.3f} s。041505Z 与失败轮及历史轮的 Kimi 引擎配置一致。相对历史 2584.591 s 和 3066.760 s，本轮 Wake 短 {(1-audit['wake_seconds']/2584.591):.2%} 和 {(1-audit['wake_seconds']/3066.760):.2%}。历史为三模型序列，本轮为 Kimi → Kimi，页缓存状态未对齐。

{table(["阶段", "八 rank 包络（s）"],
        [(p, fmt(v["all_rank_envelope_seconds"])) for p, v in phase_trace.items()])}

041505Z 唤醒窗口的同一采样点中，memory.current 为 **{mem['sampled_peak_bytes']/GIB:.2f} GiB**，anon **{stat['anon']/GIB:.2f} GiB**，file **{stat['file']/GIB:.2f} GiB**。

{table(["Rank", "RssAnon 原值（kB，按 KiB）", "折算 GiB"],
        [(rank, f"{value:,}", f"{rss[rank]:.2f}") for rank, value in rss_kib.items()])}

审阅中的 131–249 GiB 混用了单位并只覆盖部分 rank。五个较高 rank 的区间为 {sorted(rss.values())[3]:.2f}–{max(rss.values()):.2f} GiB。共享页可同时记入多进程 RSS。

同点 workingset_refault_file 为 **{stat['workingset_refault_file']:,} 页**，按宿主 {host['page_bytes']} B 页大小折算 **{refault:.2f} GiB**。这是该容器 cgroup 从创建到采样点的累计文件页 refault 折算量，可重复计页。它与唯一扫描量、物理盘读字节以及唤醒窗口增量各有不同边界。收尾的进程读取计数仍增长。固定镜像的 layerwise.py 会延迟回放权重加载并处理、复制及恢复 kernel 张量，支持此处存在延迟处理的机制。各层内存占比仍缺分配级打点。1400 GiB 轮的 OOM 及 1664 GiB 轮越过旧上限的采样共同表明容量压力。

{link(OOM, 'docker-events.jsonl', '失败轮 Docker OOM')} · {link(OOM, 'qualification.json', '失败回执')} · {link(OOM, 'trace-summary.json', '失败轮八 rank 接点')} · {link(KIMI, 'audit.json', '复跑验收')} · {link(KIMI, 'memory-summary.json', '内存采样')} · {link(KIMI, 'trace-summary.json', '阶段端点')} · {link(KIMI, 'finalizer-io-snapshots.txt', '收尾 I/O')} · [固定镜像源码](report-inputs/layerwise.py)

## 释放观察

{table(["run ID", "八卡归零且无计算进程", "最终观察（s）", "严格 ≥60s", "原运行通过标记", "原始采样数"],
        [(r["run_id"], r["zero"], fmt(r["release_s"]), r["strict"], r["original_passed"],
          r["samples"] or "原回执") for r in releases])}

144041Z 的 59.504 s 保留原值。实际资源回落和观察时长按各自原始记录核验，严格门槛为 60 秒。

## 重算与证据

仓库根目录运行 python v11/make_experiment_report.py。单独重绘可运行 python v11/plot_v11.py。生成数据、图和报告使用同一批输入。

[派生数据和逐 rank 起止](derived/report-data.json) · [运行清单](report-inputs/run-manifest.json) · [原始证据索引](report-inputs/evidence-index.csv.gz) · [三模型大池复验](large-pool-20261114/REPORT.md)

大池 B1 九轮共 54 次后续切换。逐路径配对 Store 日志后，可重算缺块加载到主机的跨度，以及副本创建到恢复成功确认的上下界。首次 Store 查找至八 rank 权重就绪的实测范围为 91.203–139.670 秒。Store 驻留、驱逐释放块与 Host KV clear 前后字节均可从原回执重算。独立 `pread` 和 CUDA 拷贝耗时仍为 null。54 次切换缺旧 GPU KV 释放起点，完整 KV 清理至就绪跨度同样保留 null。已归档的 S1 补测单列，正式样本仍为九轮。

[大池 B1 九轮可服务前指标](large-pool-20261114/READINESS.md) · [独立 S1 补测与释放回执](large-pool-20261114/supplement/B1-S1-r1-20261115T180856Z/README.md) · [Sleep L1/L2 前检](large-pool-20261114/SLEEP-PREFLIGHT-20261116.md)

新打点 S1 首次尝试 `B1-S1-r1-20261116T165642Z` 在 Kimi 冷页准入后、权重加载前被源码哈希校验拒绝。七段均未验收，最终八卡归零观察覆盖 60.536 s。[失败与释放证据索引](large-pool-20261114/supplement/B1-S1-r1-20261116T165642Z/evidence-index.csv)""")
    supplements = sorted((ROOT / "large-pool-20261114/supplement").glob("*/derived-metrics.json"))
    if supplements:
        measured_runs = [read(path) for path in supplements]
        qualified = [stage["metrics"] for run in measured_runs if run["round_status"]
                     for stage in run["stages"]]
        parts.append("""## 大池 B1 逐 rank 补测

TP8，864 GiB 权重 Store，813 GiB LMCache Host KV。每轮首段通过 SafeTensors 冷页准入，后六段走同一常驻 Store。有效轮次单列，历史九轮继续使用原样本。

可服务和首个正确响应均从控制器调度计时，终点分别为直接引擎就绪和首个通过业务金标的完整响应。权重准备以八 rank 权重及必要后处理完成为终点。独立 GPU KV 物理释放接点缺测，旧 GPU KV 释放至新 KV 就绪留空。逐次计时、可观测区间并集及权重重叠见派生数据。""")
        summary = []
        for label, key in (
            ("首段 SF 权重准备", "3"), ("首段 GPU KV 初始化及注册", "4"),
            ("Store 切换权重准备", "5"), ("缺块读取至 Host 数据发布", "5.2"),
            ("旧 GPU KV 释放至新 KV 就绪", "6"), ("旧 GPU KV 注销", "7.1"),
            ("旧 Host KV 清理", "7.3"), ("新 GPU KV 分配与初始化", "7.4"),
            ("新 GPU KV 注册", "7.5"),
        ):
            values = [row[key]["seconds"] for row in qualified
                      if row.get(key, {}).get("seconds") is not None]
            summary.append([label, len(values), fmt(median(values) if values else None),
                            fmt(min(values) if values else None),
                            fmt(max(values) if values else None)])
        parts.append(table(["可服务前指标", "有效 n", "中位数（s）", "最小（s）", "最大（s）"], summary))
        for metrics, data in zip(supplements, measured_runs, strict=True):
            receipt = read(metrics.parent / "qualification.json")
            evidence = metrics.parent.relative_to(ROOT).as_posix()
            run = data["run_id"]
            parts.append(f"### {run}\n\n"
                         f"整轮通过 {receipt['passed']}，同池复用通过 "
                         f"{receipt['checks']['b1_same_host_kv_pool']}，"
                         f"八卡归零观察 {fmt(receipt['final_release_seconds'])} 秒。"
                         f"[验收]({evidence}/qualification.json) · "
                         f"[证据索引]({evidence}/evidence-index.csv) · "
                         f"[Host KV 逐点数据]({evidence}/host-kv-time-memory.csv) · "
                         f"[可复算端点]({evidence}/derived-metrics.json)")
            init = data["round_metrics"]
            parts.append(f"Host KV 池初始化 {fmt(init['2']['seconds'])} 秒，"
                         f"权重 Store 初始化 {fmt(init['1']['seconds'])} 秒，"
                         f"串行合计 {fmt(init['init_serial_sum_s'])} 秒，"
                         f"两池初始化墙钟 {fmt(init['init_wall']['seconds'])} 秒。")
            rows = []
            for stage, kv in zip(data["stages"], receipt["b1_host_kv"]["steps"], strict=True):
                timings = stage["metrics"]
                weight = timings.get("3", timings.get("5", {})).get("seconds")
                rows.append([stage["step"], fmt(weight),
                             *(fmt(timings.get(key, {}).get("seconds"))
                               for key in ("10", "12", "7.1", "7.3")),
                             kv["committed_tokens"], kv["used_after_clear"]])
            parts.append(table(["段", "权重准备（s）", "至可服务（s）", "至首个正确响应（s）",
                                "旧 KV 注销（s）", "旧 Host KV 清理（s）", "写入 token",
                                "本段清理后字节"], rows))
            figure = ROOT / f"figures/host-kv-{run}.png"
            if figure.is_file():
                parts.append(f"![{run} Host KV 与进程内存](figures/{figure.name})")
        parts.append("在原始证据所在节点运行 `python v11/large-pool-20261114/"
                     "recompute_b1_metrics.py <run-directory>` 重算。")
    sleep_timing = ROOT / "large-pool-20261114/sleep-timing.json"
    if sleep_timing.is_file():
        sleep_data = json.loads(sleep_timing.read_bytes())
        rounds = sleep_data["rounds"]
        audit_path = sleep_timing.parent / "sleep-vram-audit.json"
        vram_audits = {}
        if audit_path.is_file():
            vram_audits = {row["run_id"]: row for row in json.loads(audit_path.read_bytes())}
        parts.append("""## 同序列 Sleep L2 复验

TP8，首模型经 SafeTensors 冷页准入，回访模型在原 Worker 重载 checkpoint。配置为每 rank 16 GiB 的 GPU KV 预算。此路径不使用 SLLM Store 或 LMCache。权重就绪、GPU KV 就绪和 Backend 可服务是独立接点，表中跨度存在重叠。
""")
        protocol = json.loads((sleep_timing.parent / "sleep-l2-protocol.json").read_bytes())
        parts.append(table(["序列", "七段顺序", "已归档合格轮", "目标轮"],
                           [(seq, " → ".join(MODELS[model] for model in models),
                             sum(run["passed"] for run in rounds if run["sequence"] == seq),
                             protocol["target_repetitions_per_sequence"])
                            for seq, models in protocol["sequences"].items()]))
        s3_gates = []
        for run in rounds:
            if run["sequence"] != "S3":
                continue
            gate_rel = f"large-pool-20261114/sleep-runs/{run['run']}/L2/06-D-vram-gate.json"
            gate_path = ROOT / gate_rel
            if gate_path.is_file():
                gate = json.loads(gate_path.read_bytes())
                free = min(gate["free_mib"])
                required = gate["required_mib"]
                assert len(gate["free_mib"]) == 8 and gate["passed"] == (free >= required)
                s3_gates.append((f"[第 {run['rep']} 轮]({gate_rel})", f"{free:,}", f"{required:,}",
                                 f"{free - required:+,}", "通过" if run["passed"] else "未通过"))
        if s3_gates:
            parts.append("S3 末段 V4 显存准入取唤醒前八卡可用显存的最小值。余量为该值减去准入线。")
            parts.append(table(["轮次", "最低可用（MiB）", "准入线（MiB）", "余量（MiB）", "整轮验收"], s3_gates))
        b1_rounds = json.loads((sleep_timing.parent / "readiness-20261116.json").read_bytes())
        cold_kv = []
        for seq, models in protocol["sequences"].items():
            b1_values = [r["steps"][0]["allocation_s"] for r in b1_rounds if r["sequence"] == seq]
            b1_values = [value for value in b1_values if value is not None]
            l2_values = [r["steps"][0]["kv_allocation_s"] for r in rounds
                         if r["sequence"] == seq and r["passed"]
                         and r["steps"][0]["kv_allocation_s"] is not None]
            cold_kv.append((seq, MODELS[models[0]], len(b1_values),
                            fmt(median(b1_values) if b1_values else None), len(l2_values),
                            fmt(median(l2_values) if l2_values else None)))
        parts.append("首段 GPU KV 初始化取八 rank 最早开始到最晚结束。大池 B1 的历史事件包住 "
                     "Worker.initialize_from_config，含分配、绑定与 LMCache 注册。"
                     "服务器注册成功日志的首末跨度与该初始化区间重叠。B1 按显存利用率自动计算预算，"
                     "Sleep L2 固定每 rank 16 GiB，使用原生初始化。池初始化和 Backend 预热各有独立接点。"
                     "B1 的旧 GPU KV 释放起点缺失，严格的清理至新 KV 就绪跨度为 null。")
        parts.append(table(["序列", "冷启动模型", "大池 B1 n", "B1 中位数（s）",
                            "Sleep L2 n", "L2 中位数（s）"], cold_kv))
        metrics = ("load_or_wake_weight_s", "kv_allocation_s", "weight_switch_s",
                   "kv_switch_s", "kv_release_to_service_ready_s")
        summaries = []
        for seq, models in protocol["sequences"].items():
            for step, model in enumerate(models):
                stats = sleep_data["summary"].get(f"L2-{seq}-{step:02d}", {})
                cells = [
                    f"{row['n']} · {fmt(row['median'])} [{fmt(row['min'])}, {fmt(row['max'])}]"
                    if (row := stats.get(field)) and row["n"] else "0 · —"
                    for field in metrics
                ]
                summaries.append((seq, step + 1, MODELS[model], *cells))
        parts.append("### 各序列与切换位置汇总\n\n"
                     "单元格为有效 n · 中位数 [最小值, 最大值]，单位为秒。"
                     "每项只计入通过轮次中具备该指标起止端点的观测。")
        parts.append(table(["序列", "段", "模型", "权重准备", "GPU KV 分配",
                            "权重切换", "KV 切换", "旧 KV 释放至可服务"], summaries))
        for run in rounds:
            rid = run["run"]
            base = f"large-pool-20261114/sleep-runs/{rid}"
            release_obs = run["final_release"]
            status = "通过" if run["passed"] else "未通过"
            parts.append(f"### {run['sequence']} 第 {run['rep']} 轮 · {status}\n\n"
                         f"`{rid}` · [验收原件]({base}/qualification.json) · "
                         f"[qjhs003 原始证据索引]({base}/evidence-index.csv)")
            campaign_path = ROOT / base / "repro/campaign.json"
            if campaign_path.is_file():
                campaign = json.loads(campaign_path.read_bytes())
                if campaign.get("scope") == "formal_s3_marlin_rebind":
                    mount_path = ROOT / base / "v4-overlay-mounts.json"
                    if mount_path.is_file():
                        mounts = json.loads(mount_path.read_bytes())["mounts"]
                        overlay = next(mount for mount in mounts
                                       if mount["destination"].endswith("/reload/layerwise.py"))
                        assert not overlay["rw"] and overlay["sha256"] == campaign["candidate_overlay_sha256"]
                        parts.append("本轮采用 V4 Marlin scale 引用修复。"
                                     f"[运行配置]({base}/repro/campaign.json) · "
                                     f"[容器挂载回执]({base}/v4-overlay-mounts.json)")
                    else:
                        assert not run["passed"]
                        parts.append(f"本轮配置启用 V4 Marlin scale 引用修复，容器挂载回执缺失。"
                                     f"[运行配置]({base}/repro/campaign.json)")
                    gate_path = ROOT / base / "L2/06-D-vram-gate.json"
                    if gate_path.is_file():
                        gate = json.loads(gate_path.read_bytes())
                        if gate["passed"]:
                            parts.append(f"末段 V4 唤醒前八卡最低可用显存为 {min(gate['free_mib']):,} MiB，"
                                         f"通过原 {gate['required_mib']:,} MiB 准入。"
                                         f"[显存准入回执]({base}/L2/06-D-vram-gate.json)")
            for gate in run.get("failed_vram_gates", []):
                parts.append(f"本轮完成 {len(run['steps'])}/{len(protocol['sequences'][run['sequence']])} 段。"
                             f"下一段准入时，八卡最低可用显存为 {min(gate['free_mib']):,} MiB，"
                             f"低于要求的 {gate['required_mib']:,} MiB。"
                             f"[显存准入回执]({base}/{gate['source']})")
            audit = vram_audits.get(rid, {})
            failed_v4 = audit.get("failed_v4_gate")
            if failed_v4 and "cap_projection" in failed_v4:
                previous = next(wake for wake in audit["wakes"] if wake["stage"] == failed_v4["prior_v4_wake_stage"])
                stages = [wake for wake in audit["wakes"] if previous["stage"] <= wake["stage"] < failed_v4["stage"]]
                retained = []
                for wake in stages:
                    reserved_bytes = max(row["torch_deltas_bytes"]["resleep"]["cuda_reserved_bytes"]
                                         for row in wake["rank_rows"])
                    retained.append(f"{MODELS[wake['model']]} {reserved_bytes / 2**20:,.0f} MiB")
                margin = failed_v4["cap_projection"]["original_min_sampled_peak_margin_mib"]
                units = "、".join(retained)
                parts.append(f"第 {previous['stage']} 段 V4 唤醒的设备显存峰值增量为 "
                             f"{max(previous['sampled_peak_increment_mib']):,} MiB。"
                             f"第 {failed_v4['stage']} 段可用显存比按卡重放该峰值少至多 {-margin:,} MiB。"
                             f"回访后再次休眠，PyTorch 保留显存的逐 rank 最大增量为 {units}。"
                             "V4 的增量在 checkpoint 重载后、KV 恢复前已经出现。"
                             "[逐 rank 原始接点与容量推算](large-pool-20261114/sleep-vram-audit.json)")
                source = "https://github.com/vllm-project/vllm/blob/132765e3560659ff63ebd236203672e991b70e08/vllm/model_executor"
                parts.append("源码显示，MARLIN MXFP4 后处理先用临时 scale 张量创建量化配置和 kernel，"
                             "随后 layerwise reload 将参数复制回原存储并重新注册参数。"
                             "量化配置可能仍持有临时 scale。按 V4 的 61 层、384 专家、"
                             "7168 隐藏维度、TP8 后每 rank 的 384 个中间通道及每 32 元素 1 字节计算，"
                             "三组 scale 合计 5,764.5 MiB/rank，实测活跃分配增量为 5,795.5 MiB/rank。"
                             "待逐 rank 对照 scale 的存储地址和权重分配标签，确认具体归属。"
                             f"[MXFP4 后处理]({source}/layers/quantization/mxfp4.py#L651-L730) · "
                             f"[量化配置]({source}/layers/fused_moe/config.py#L676-L696) · "
                             f"[layerwise reload]({source}/model_loader/reload/layerwise.py#L290-L367)")
            if release_obs:
                parts.append(f"最终八卡归零且无计算进程 {release_obs['all_gpu_zero']}，"
                             f"{release_obs['samples']} 个采样点覆盖 {release_obs['elapsed_s']:.3f} s。"
                             f"末次采样的整机可用 RAM 为 "
                             f"{fmt(release_obs['last_host_available_bytes'] / GIB)} GiB。"
                             f"[最终释放采样]({base}/resource-samples.csv)")
            parts.append(table(["段", "模型", "操作", "权重准备（s）", "GPU KV 分配（s）",
                                "权重切换（s）", "KV 切换（s）", "旧 KV 释放至可服务（s）"],
                               [(s["step"] + 1, MODELS[s["model"]], s["stage"],
                                 fmt(s["load_or_wake_weight_s"]), fmt(s["kv_allocation_s"]),
                                 fmt(s["weight_switch_s"]), fmt(s["kv_switch_s"]),
                                 fmt(s["kv_release_to_service_ready_s"])) for s in run["steps"]]))
            if run["passed"]:
                parts.append(f"![{run['sequence']} 第 {run['rep']} 轮可服务前切换跨度]"
                             f"(figures/sleep-{rid}.png)")
            residency = []
            for step in run["steps"]:
                sample = step["post_sleep_residency"]
                if sample is None:
                    continue
                mem = sample["host_memory_kib"]
                index = step["step"] + 1
                residency.append((
                    index, MODELS[step["model"]], fmt(sum(sample["gpu_used_mib"]) / 1024),
                    fmt(mem["MemAvailable"] / 2**20), fmt(mem["AnonPages"] / 2**20),
                    sum(len(pids) for pids in sample["processes"].values()),
                    step["sleep_cpu_weight_backup_bytes"],
                ))
            if residency:
                parts.append("逐段 sleep 后采样。HBM 为八卡合计，RAM 为整机统计，"
                             "PID 计数覆盖该轮仍存活的 Backend 容器。CPU 权重备份字节来自 allocator 回执。")
                parts.append(table(["段", "休眠模型", "HBM（GiB）", "可用 RAM（GiB）",
                                    "匿名页（GiB）", "存活 PID 数", "CPU 权重备份（B）"],
                                   residency))
            memory_rows = []
            for step in run["steps"]:
                memory = step.get("checkpoint_memory")
                if memory:
                    peak = memory["peak"]
                    memory_rows.append((step["step"] + 1, memory["sample_count"],
                                        fmt(peak["current_bytes"] / GIB),
                                        fmt(peak["stat"]["anon"] / GIB), fmt(peak["stat"]["file"] / GIB),
                                        f"{memory['refault_delta_pages']:,}",
                                        memory["last"]["events"]["oom_kill"] -
                                        memory["first"]["events"]["oom_kill"]))
            if memory_rows:
                parts.append("Kimi checkpoint 重载窗口的 cgroup 实测。匿名页和文件页取自同一峰值采样点。"
                             "refault 为采样区间计数增量。采样覆盖范围见派生数据。")
                parts.append(table(["段", "采样数", "峰值（GiB）", "匿名页（GiB）", "文件页（GiB）",
                                    "文件页 refault 增量（页）", "OOM kill 增量"], memory_rows))
        parts.append("从仓库根目录运行 `python v11/large-pool-20261114/recompute_sleep.py "
                     "v11/large-pool-20261114/sleep-runs` 重算。"
                     "[逐 rank 端点及各位置统计](large-pool-20261114/sleep-timing.json)")
        if vram_audits:
            parts.append("显存回执重算使用 `python v11/large-pool-20261114/recompute_sleep_vram.py "
                         "v11/large-pool-20261114/sleep-runs`。")
        scale_path = sleep_timing.parent / "v4-scale-diagnostic.json"
        if scale_path.is_file():
            scale = json.loads(scale_path.read_bytes())
            rid = scale["run_id"]
            evidence = f"large-pool-20261114/sleep-diagnostics/{rid}"
            receipt = json.loads((ROOT / evidence / "qualification.json").read_bytes())
            assert receipt["passed"] and scale["qualification_passed"]
            sleeps = [row for row in scale["phases"] if row["phase"] == "after_sleep"]
            sleep_rows = []
            for index, (row, nvml) in enumerate(zip(sleeps, scale["post_sleep_nvml"], strict=True)):
                outside = row["storage_total_bytes"]["outside_weights"] / GIB
                mib = nvml["gpu_used_per_card_mib"]
                per_card = str(mib[0]) if min(mib) == max(mib) else f"{min(mib)}–{max(mib)}"
                sleep_rows.append((index + 1, per_card, fmt(outside)))
            parts.append("### V4 Sleep L2 单模型重复唤醒诊断")
            parts.append(f"`{rid}` · V4 → V4 → V4，每 rank 16 GiB GPU KV，沿用原显存准入。"
                         f"金标和 Worker 连续性通过，{scale['phase_count']} 个阶段各覆盖八 rank，"
                         f"退出后八卡归零观察 {fmt(receipt['final_release_seconds'])} s。"
                         f"[验收]({evidence}/qualification.json) "
                         f"· [原始证据索引]({evidence}/evidence-index.csv)")
            parts.append(table(["第几次 sleep", "每卡 NVML 驻留（MiB）",
                                "八 rank 权重分配区外 scale（GiB）"], sleep_rows))
            delta = scale["post_sleep_nvml"][1]["gpu_used_delta_per_card_mib"]
            increase = str(delta[0]) if min(delta) == max(delta) else f"{min(delta)}–{max(delta)}"
            parts.append("首次 checkpoint 重载后，Marlin 的 scale 引用指向权重分配区外的存储，"
                         f"这些存储在下一次 Sleep 后仍被引用。首次重载前后每卡休眠驻留增加 {increase} MiB，"
                         "与 S3 的 V4 驻留增量一致。第二次重载后，区外 scale 容量保持稳定。"
                         "NVML 总驻留同时包含其他显存分配，按原始读数列示。"
                         "[逐 rank 派生回执](large-pool-20261114/v4-scale-diagnostic.json)")
            parts.append("从仓库根目录运行 "
                         f"`python v11/large-pool-20261114/recompute_v4_scale_diagnostic.py v11/{evidence}`。")
        rebind_path = sleep_timing.parent / "v4-rebind-diagnostic.json"
        if rebind_path.is_file():
            rebind = json.loads(rebind_path.read_bytes())
            evidence = f"large-pool-20261114/sleep-diagnostics/{rebind['run_id']}"
            receipt = json.loads((ROOT / evidence / "qualification.json").read_bytes())
            assert receipt["passed"] and rebind["rebind"]["scale_contract_passed"]
            residency = " → ".join(str(row["gpu_used_max_mib"]) for row in rebind["post_sleep_nvml"])
            parts.append(f"修复短测 `{rebind['run_id']}` 沿用相同的 V4 请求和显存准入。"
                         "两次 checkpoint 唤醒的金标与 Worker 连续性通过，八 rank 的 Marlin scale "
                         f"在权重分配区外均为 0 字节。三次 Sleep 后每卡最大驻留为 {residency} MiB。"
                         f"最终八卡归零观察 {fmt(receipt['final_release_seconds'])} s。"
                         f"[验收]({evidence}/qualification.json) · [原始证据索引]({evidence}/evidence-index.csv) "
                         "· [逐 rank 重算](large-pool-20261114/v4-rebind-diagnostic.json)")
            parts.append("从仓库根目录运行 "
                         f"`python v11/large-pool-20261114/recompute_v4_scale_diagnostic.py v11/{evidence}`。")
    wna16 = sorted((ROOT / "large-pool-20261114/sleep-diagnostics").glob(
        "B2-Kimi-L2-wna16-*/audit.json"))
    if wna16:
        parts.append("""## Kimi Sleep L2 逐层重载对照

固定 vLLM 0.20.1，TP8，每 rank 16 GiB GPU KV，容器内存上限 1664 GiB。两组首次加载和 checkpoint 唤醒前各做一次 SafeTensors 冷页准入。逐层记录覆盖八 rank 的 480 个专家层实例。匿名内存峰值与文件页 refault 均取重载窗口，refault 折算量按页计数计算。""")
        rows = []
        arms = {}
        for audit in wna16:
            result = read(audit)
            arms[result["arm"]] = result
            lifecycle, memory = result["lifecycle"], result["memory"]
            counts = result["layer_completions"]
            finalized = sum(row.get("finalize", 0) for row in counts.values())
            observed = sum(sum(row.values()) for row in counts.values())
            evidence = audit.parent.relative_to(ROOT).as_posix()
            rows.append([result["arm"], f"[{lifecycle['run']}]({evidence}/evidence-index.csv)",
                         f"{finalized}/{observed}", fmt(result["reload_seconds"]),
                         fmt(result["kv_wake_seconds"]),
                         fmt(memory["reload_sampled_anon_peak"]["anon_bytes"] / GIB),
                         fmt(memory["refault_bytes"] / GIB),
                         fmt(lifecycle["released_seconds"])])
            parts.append(f"[{result['arm']} 派生审计]({evidence}/audit.json) · "
                         f"[验收回执]({evidence}/qualification.json) · "
                         f"[重算脚本]({evidence}/capsule-provenance/audit_wna16.py)")
        parts.append(table(["组", "运行证据", "finalize 完成数", "重载（s）",
                            "GPU KV 唤醒（s）", "匿名内存峰值（GiB）",
                            "文件页 refault 折算（GiB）", "八卡释放观察（s）"], rows))
        if len(wna16) == 2 and {"baseline", "candidate"} == arms.keys():
            baseline = arms["baseline"]["reload_seconds"]
            candidate = arms["candidate"]["reload_seconds"]
            parts.append(f"同配置各测一次，两组均以同一轮内的原八个 Worker 完成唤醒并通过前后金标。"
                         f"本对照重载缩短 {fmt(baseline - candidate)} s（{(1 - candidate / baseline):.1%}）。"
                         "基线的专家层实例全部在最终收尾阶段完成，修复组全部在重载过程中完成。")

    pool = ROOT / "large-pool-20261114"
    original_glm = pool / "original-cold/sllm-debug-20261116T225019Z-dfb118ad"
    if (original_glm / "controller-final.json").is_file():
        final = read(original_glm / "controller-final.json")
        assert final["success"]
        original_a = final["metrics"]["command_to_correct_business_response_s"]
        original_v4 = {}
        for review_path in sorted(original_glm.parent.glob("v4n40_pilot_003_*/review.json")):
            review = read(review_path)
            assert review["runtime_qualified"] and review["full_model_condition_pilot_qualified"]
            assert not review["formal_id_consumed"] and review["qjhs003_initialized_store_context"]["passed"]
            assert review["qjhs003_initialized_store_context"]["span_s"] >= 60
            assert review["source_head"] == "88cc71ec50c8668c26f26636d7d432c3351356fe"
            assert review["native_disk_loads"] == {"safetensors_disk_cold": 0, "store_disk_cold": 496}[review["condition"]]
            assert min(review["release_seconds"].values()) >= 60
            assert review["condition"] not in original_v4
            original_v4[review["condition"]] = (review, review_path)
        rows = []
        for label, historical_a, sequence_name, run_id, step_id, request in (
            ("V4 SF 冷读", "556.222（n=9）", "S3", "B1-S3-r1-20261116T201045Z", "00-D-v4", "business"),
            ("V4 Store 冷读", "223.752（n=9）", "S2", "B1-S2-r1-20261116T184708Z", "01-D-v4", "business"),
            ("GLM Store 冷读", "599.712（n=1）", "S1", "B1-S1-r2-20261116T171537Z", "01-G-glm", "business_arithmetic"),
        ):
            run = pool / "supplement" / run_id
            step = run / step_id
            qualification = read(run / "qualification.json")
            assert qualification["passed"] and qualification["strict_release_passed"]
            row, = [r for r in csv_rows(run / "switch-events.csv") if int(r["step"]) == int(step_id[:2])]
            assert read(step / f"before-{request}-checks.json")["passed"]
            response = step / f"before-{request}-response.json"
            received = read(response)
            assert received["response"]["id"] == f"{step_id}-before-{request}"
            assert int(row["t2_ns"]) <= received["received_ns"] <= int(row["t4_ns"])
            cache = read(step / "store-step.json")["cache"]
            if label == "V4 SF 冷读":
                assert cache["cache_state"] == "safetensors_disk_cold"
                state = "首段 SF，64 个分片零驻留页"
            else:
                assert cache["cache_state"] == "full_miss" and cache["hit_bytes"] == 0
                state = f"空权重池，{len(cache['miss_paths'])} 条路径未命中，读 {cache['file_read_bytes']:,} B"
            observed_a = fmt(seconds(received["received_ns"], int(row["t2_ns"])))
            reference = f"[{sequence_name} / {step_id}]({response.relative_to(ROOT).as_posix()})"
            original_value = fmt(original_a) + "（n=1）" if label == "GLM Store 冷读" else "待测"
            condition = "safetensors_disk_cold" if label == "V4 SF 冷读" else "store_disk_cold"
            if label.startswith("V4 ") and condition in original_v4:
                review, review_path = original_v4[condition]
                original_value = (f"{fmt(review['command_to_correct_response_seconds'])}（n=1） "
                                  f"[{review_path.parent.name}]({review_path.relative_to(ROOT).as_posix()})")
            rows.append([label, historical_a, original_value, reference, observed_a, state])
        parts.append("## 原版冷读补测与现版本 B1 参照\n\n" + table(
            ["条件", "历史图 1 A（s）", "原版重测 A（s）", "现版本 B1 段", "B1 t4_first − t2（s）", "B1 缓存证据"], rows))
        parts.append("原版 GLM 延用 HTTP `/register` 前到外部完整业务响应的 A。"
                     "本轮 Store 数据、Store 元数据和共享 checkpoint 元数据共 108 个文件，合并准入的驻留页均为零。"
                     "B1 从控制器放行 Backend 到内部正确完整响应，两条 Store 段的全未命中属于 RAM 权重池状态，"
                     "历史/原版冷读还验证 OS 页缓存。计时端点、Store 路径和缓存准入有差异，版本差值缺测。\n\n"
                     "[历史图 1](https://github.com/BoyJiang2/serverlessllm/blob/870d7daac4071790874884d2b3a9bdfc3af00f95/comparison/evidence/three-model-final-20261108/REPORT.md#图-1四条件启动至首个正确业务响应) · "
                     f"[GLM 原版终态]({original_glm.relative_to(ROOT).as_posix()}/controller-final.json) · "
                     f"[源证据索引]({original_glm.relative_to(ROOT).as_posix()}/evidence-index.csv)")
        if original_v4:
            parts.append("V4 原版代码复测在 qjhs003 运行固定源码 `88cc71e` 和原镜像，A 沿用启动命令到外部完整金标响应。"
                         "SafeTensors 沿用 NFS 路径，Store 使用 qjhs003 的本地 XFS 副本。启动器适配节点路径、"
                         "清单和 GPU 准入，并在冷页检查前采样至少 60 s 的本机 Store 显存基线。"
                         "两项均使用独立核验的 JIT 缓存，包含原清单的 174 个文件及 70 个 DeepGEMM 配套源文件。")
            parts.extend(f"[{path.parent.name} 证据索引]({path.with_name('evidence-index.csv').relative_to(ROOT).as_posix()})"
                         for _, path in original_v4.values())
        failure_root = original_glm.parent / "v4n40_pilot_003_sf_20261117T0046Z"
        if (failure_root / "failure-excerpt.json").is_file():
            parts.append("qjhs003 的早期 SF 尝试分别止于启动许可握手和 DeepGEMM 缓存目录缺失配套源文件。"
                         "两次均无有效 A。 "
                         "[握手超时证据](large-pool-20261114/original-cold/v4n40_pilot_003_sf_20261117T0043Z/evidence-index.csv) · "
                         f"[JIT 缺失证据]({(failure_root / 'failure-excerpt.json').relative_to(ROOT).as_posix()})")
    (ROOT / "report.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
