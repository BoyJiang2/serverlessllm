"""Recompute service-readiness endpoints from the nine qualified large-pool rounds."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from statistics import median

from summarize_pilot import load, summarize, seconds

REG = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\].*?Registered KV cache for GPU ID (\d+)")
DISK_DONE = re.compile(r"^(\S+).*?Finished loading model (\S+) from disk")


def registration_log(path):
    out = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = REG.search(line)
        if match:
            stamp = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
            out.append(dict(wall_ns=int(stamp.timestamp()) * 10**9 + stamp.microsecond * 1000,
                            instance_id=match[2], line=line_number))
    return out


def collect(root):
    config = load(root / "campaign.json")
    results = []
    for rep, block in enumerate(config["blocks"], 1):
        for name in block:
            qualified = [p for p in (root / "runs").glob(f"B1-{name}-r{rep}-*")
                         if (p / "qualification.json").is_file()
                         and load(p / "qualification.json")["passed"]
                         and load(p / "qualification.json")["strict_release_passed"]]
            run, = qualified
            assert load(run / "qualification.json")["models"] == config["sequences"][name]
            data = summarize(run)
            boot = load(run / "admission.json")
            offset = boot["wall_ns"] - boot["checked_ns"]
            samples = [json.loads(line) for line in (run / "resource-samples.jsonl").read_text().splitlines()]
            clock_error = max(abs(x["wall_ns"] - x["ns"] - offset) for x in samples)
            log_path = run / "lmcache/lmcache.log"
            registrations = registration_log(log_path)
            with (run / "switch-events.csv").open(newline="") as source:
                timings = list(csv.DictReader(source))
            assert len(timings) == len(data["steps"]) == 7
            steps = []
            for i, (row, item) in enumerate(zip(timings, data["steps"])):
                step = run / item["step"]
                ready = item["engine_ready_ns"]
                began = int(row["t2_ns"])
                registered = [x for x in registrations if began <= x["wall_ns"] - offset <= ready]
                registration = None
                if clock_error <= 5_000_000 and len(registered) == len({x["instance_id"] for x in registered}) == 8:
                    registration = dict(first_host_log_ns=registered[0]["wall_ns"] - offset,
                                        last_host_log_ns=registered[-1]["wall_ns"] - offset,
                                        records=registered, source="lmcache/lmcache.log")
                cache = load(step / "store-step.json")["cache"]
                disk_done = []
                if i:
                    for number, line in enumerate((step / "store-events.log").read_text().splitlines(), 1):
                        match = DISK_DONE.search(line)
                        if match:
                            disk_done.append(dict(line=number, log_utc=match[1], path=match[2]))
                disk_completion = dict(count=len(disk_done),
                                       unique_paths=len({event["path"] for event in disk_done}),
                                       first=disk_done[0], last=disk_done[-1]) if disk_done else None
                events = [json.loads(line) for source in sorted((step / "events").glob("*.jsonl"))
                          for line in source.read_text().splitlines()]
                def bounds(phase):
                    result = []
                    for suffix, select in (("begin", min), ("end", max)):
                        rows = [x for x in events if x["name"] == phase + "_" + suffix]
                        assert len(rows) == 8 and {x["rank"] for x in rows} == set(range(8))
                        result.append(select(x["monotonic_ns"] for x in rows))
                    return result
                kv_bounds = bounds("kv_cache_allocation")
                weight_bounds = bounds("full_weight_load")
                assert began <= weight_bounds[0] <= weight_bounds[1] <= ready
                assert began <= kv_bounds[0] <= kv_bounds[1] <= ready
                old = data["steps"][i - 1] if i else None
                old_res = old["residency"] if old else None
                steps.append(dict(step=step.name, model=row["to_model"], activation_ns=began,
                                  service_ready_ns=ready, allocation_s=item["gpu_kv_allocation_eight_rank_s"],
                                  kv_allocation_begin_ns=kv_bounds[0], kv_allocation_end_ns=kv_bounds[1],
                                  weight_begin_ns=weight_bounds[0], weight_ready_ns=weight_bounds[1],
                                  registration=registration, cache_state=row["cache_state"],
                                  hit_bytes=cache["hit_bytes"], read_bytes=cache["file_read_bytes"],
                                  disk_completion=disk_completion,
                                  evicted_paths=len(cache["evicted_paths"]),
                                  eviction_envelope_s=item["eviction_event_envelope_s"],
                                  store_access_envelope_s=item["store_lookup_to_complete_s"],
                                  store_combined_envelope_s=item["eviction_and_host_restore_envelope_s"],
                                  full_load_s=item["full_eight_rank_weight_load_s"],
                                  missing_block_file_read_s=None, old_gpu_kv_free_start_ns=None,
                                  old_gpu_kv_free_end_ns=None, kv_switch_wall_s=None,
                                  old_host_clear_seconds=float(timings[i-1]["kv_clear_seconds"]) if i else None,
                                  old_exit_observed_ns=int(row["t1_ns"]) if i else None,
                                  old_after_clear_sample_ns=old_res["after_clear"]["ns"] if old_res else None,
                                  request_to_store_log_s=item["request_to_last_store_log_s"]))
            results.append(dict(run=run.name, sequence=name, repetition=rep, clock_error_ns=clock_error,
                                **{k:v for k,v in data.items() if k != "steps"}, steps=steps))
    return results


def statistics(values):
    values = [v for v in values if v is not None]
    return dict(n=len(values), median=median(values) if values else None,
                min=min(values) if values else None, max=max(values) if values else None)


def report(rounds):
    def cell(values):
        row = statistics(values)
        return (f"{row['median']:.3f} [{row['min']:.3f}, {row['max']:.3f}], n={row['n']}"
                if row["n"] else "— (n=0)")

    def registration(step):
        rows = step["registration"]
        return seconds(rows["first_host_log_ns"], rows["last_host_log_ns"]) if rows else None

    rows = ["# V11 大池 B1 可服务前阶段",
            "九个正式 run、63 段。下表为中位数 [最小，最大]，单位秒。每行的逐轮值、可服务端点、注册日志行号以及原 run ID 见 [重算数据](readiness-20261116.json)。[服务器证据索引](readiness-evidence-index.csv.gz) 记录绝对路径、字节数与 SHA-256。",
            f"共有 {sum(s['disk_completion'] is not None for r in rounds for s in r['steps'][1:])}/54 次切换"
            "留有 Store 分路径磁盘加载完成日志。派生数据保留首末日志及行号。日志缺少对应读起点，独立重读历时仍为 null。",
            "## 首段 KV 冷启动与准备",
            "| 序列 | Host KV 池初始化 | 权重 Store 池初始化 | 串行用时之和 | 前检至首段放行 | 八 rank GPU KV 分配 | LMCache 首末注册日志 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for seq in ("S1", "S2", "S3"):
        selected = [r for r in rounds if r["sequence"] == seq]
        cols = [cell(r[key] for r in selected) for key in
                ("host_kv_init_s", "weight_store_init_s", "init_sum_s")]
        cols += [cell(r["preparation_to_first_activation"]["wall_s"] for r in selected),
                 cell(r["steps"][0]["allocation_s"] for r in selected),
                 cell(registration(r["steps"][0]) for r in selected)]
        rows.append(f"| {seq} | " + " | ".join(cols) + " |")
    rows.extend(["",
        "GPU KV 端点取八 rank 最早分配开始至最晚结束。LMCache 取同一启动窗口内八个不同实例的服务器成功注册日志，表中的首末跨度覆盖注册完成事件之间的时间。注册调用开始端点缺失。Backend ready 是可服务回执，逐段保留原始时间戳。",
        "",
        "## 后六段切换",
        "| 序列/段 | 模型 | Store 驱逐事件跨度 | Store 访问至完成 | Store 事件总跨度 | 八 rank 权重加载 | 旧 Host 清理调用 | 新 GPU KV 分配 | 新 LMCache 首末注册日志 | 旧 KV 清理至新 KV 就绪 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for seq in ("S1", "S2", "S3"):
        for i in range(1, 7):
            steps = [r["steps"][i] for r in rounds if r["sequence"] == seq]
            cols = [cell(s[key] for s in steps) for key in
                    ("eviction_envelope_s", "store_access_envelope_s", "store_combined_envelope_s",
                     "full_load_s", "old_host_clear_seconds", "allocation_s")]
            cols += [cell(registration(s) for s in steps), cell(s["kv_switch_wall_s"] for s in steps)]
            rows.append(f"| {seq}/{i+1} | {steps[0]['model']} | " + " | ".join(cols) + " |")
    rows.extend(["",
        "独立缺块文件重读耗时缺开始和结束端点，保留 null，文件读取字节由 Store 台账核验。驱逐次数、访问与恢复事件并列；无驱逐或缺少驱逐包络时以逐轮驱逐路径计数区分。Store 事件可能与权重加载重叠。旧 Backend 退出和清理后快照有记录，实际 GPU KV 释放起止缺失。因此 54 次严格的 KV 清理至就绪总时长留空。旧 Host KV 为常驻池 clear，下一段记录注册和 GPU KV 分配。",
        "",
        "业务金标、kv_fill 和 Host 写入属于请求阶段。填充至提交日志保留在原大池报告，硬件驻留、有效载荷、池容量及 PID 同样保留原报告和原始快照。",
        "",
        "重算时在 qjhs003 使用 python3 recompute_readiness.py /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-large-pool-20261113 --report READINESS.md，重定向标准输出可更新 JSON。",
    ])
    return "\n".join(rows[:1] + [""] + rows[1:2] + [""] + rows[2:3] + [""] + rows[3:4] + [""] + rows[4:]) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    rounds = collect(args.root)
    if args.report:
        args.report.write_text(report(rounds), encoding="utf-8")
    print(json.dumps(rounds, indent=2))
