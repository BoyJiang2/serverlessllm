from pathlib import Path
import csv
import gzip
import hashlib
import json
from statistics import mean

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

[大池 B1 九轮可服务前指标](large-pool-20261114/READINESS.md) · [独立 S1 补测与释放回执](large-pool-20261114/supplement/B1-S1-r1-20261115T180856Z/README.md) · [Sleep L1/L2 前检](large-pool-20261114/SLEEP-PREFLIGHT-20261116.md)""")
    sleep_timing = ROOT / "large-pool-20261114/sleep-timing.json"
    if sleep_timing.is_file():
        rounds = json.loads(sleep_timing.read_bytes())["rounds"]
        parts.append("""## 同序列 Sleep L2 复验

TP8，首模型经 SafeTensors 冷页准入，回访模型在原 Worker 重载 checkpoint。配置为每 rank 16 GiB 的 GPU KV 预算。此路径不使用 SLLM Store 或 LMCache。权重就绪、GPU KV 就绪和 Backend 可服务是独立接点，表中跨度存在重叠。
""")
        for run in rounds:
            rid = run["run"]
            base = f"large-pool-20261114/sleep-runs/{rid}"
            release_obs = run["final_release"]
            status = "通过" if run["passed"] else "未通过"
            parts.append(f"### {run['sequence']} 第 {run['rep']} 轮 · {status}\n\n"
                         f"`{rid}` · [验收原件]({base}/qualification.json) · "
                         f"[qjhs003 原始证据索引]({base}/evidence-index.csv)")
            if release_obs:
                parts.append(f"最终八卡归零且无计算进程 {release_obs['all_gpu_zero']}，"
                             f"{release_obs['samples']} 个采样点覆盖 {release_obs['elapsed_s']:.3f} s。")
            parts.append(table(["段", "模型", "操作", "权重准备（s）", "GPU KV 分配（s）",
                                "权重切换（s）", "KV 切换（s）", "旧 KV 释放至可服务（s）"],
                               [(s["step"] + 1, MODELS[s["model"]], s["stage"],
                                 fmt(s["load_or_wake_weight_s"]), fmt(s["kv_allocation_s"]),
                                 fmt(s["weight_switch_s"]), fmt(s["kv_switch_s"]),
                                 fmt(s["kv_release_to_service_ready_s"])) for s in run["steps"]]))
            if run["passed"]:
                parts.append(f"![{run['sequence']} 第 {run['rep']} 轮可服务前切换跨度]"
                             f"(figures/sleep-{rid}.png)")
        parts.append("从仓库根目录运行 `python v11/large-pool-20261114/recompute_sleep.py "
                     "v11/large-pool-20261114/sleep-runs` 重算。"
                     "[逐 rank 端点及各位置统计](large-pool-20261114/sleep-timing.json)")
    (ROOT / "report.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
