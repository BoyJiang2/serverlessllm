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
DISK_LOG = r"^(\S+) I(\d{8} \d{2}:\d{2}:\d{2}\.\d{6})\s+(\d+) model\.cpp:"
DISK_BEGIN = re.compile(DISK_LOG + r"120\] Loading model (\S+) size (\d+) to host$")
DISK_DONE = re.compile(DISK_LOG + r"236\] Finished loading model (\S+) from disk$")
GLOG_TIME = "%Y%m%d %H:%M:%S.%f"
STORE_LOOKUP = re.compile(r"I(\d{8} \d\d:\d\d:\d\d\.\d+)\s+\d+ checkpoint_store\.cpp:118\] SLLM_CACHE_LOOKUP path=(\S+) resident=[01] bytes=(\d+)$")
STORE_EVICT = re.compile(r"\] Model (\S+) is freed from memory$")
STORE_COMPLETE = re.compile(r"\] SLLM_CACHE_COMPLETE path=(\S+) rc=0 resident=1$")
REPLICA_CREATE = re.compile(r"I(\d{8} \d\d:\d\d:\d\d\.\d+)\s+\d+ model\.cpp:279\] Creating replica (\S+)$")
REPLICA_CONFIRM = re.compile(r"INFO (\d\d-\d\d \d\d:\d\d:\d\d) server\.py:156\] Confirm model (\S+) replica (\S+) success$")
RECOVERY_REQUEST = re.compile(r"\(Worker_TP(\d+) pid=(\d+)\).*?client\.py:93\] load_into_gpu: (\S+), (\S+)$")


def wall_ns(text, fmt):
    stamp = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    return int(stamp.timestamp()) * 10**9 + stamp.microsecond * 1000


def registration_log(path):
    out = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = REG.search(line)
        if match:
            out.append(dict(wall_ns=wall_ns(match[1], "%Y-%m-%d %H:%M:%S,%f"),
                            instance_id=match[2], line=line_number))
    return out


def disk_load_log(path, cache):
    starts, ends = {}, {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        begin = DISK_BEGIN.match(line)
        end = DISK_DONE.match(line)
        if match := begin or end:
            docker_utc, stamp, tid, model_path = match.groups()[:4]
            parsed = dict(line=line_number, log_utc=docker_utc, path=model_path)
            records = starts if begin else ends
            assert model_path not in records, (path, model_path, line_number)
            records[model_path] = (wall_ns(stamp, GLOG_TIME), int(tid), parsed, int(match[5]) if begin else None)
    assert set(starts) == set(ends) == set(cache["miss_paths"]), path
    if not starts:
        assert cache["file_read_bytes"] == 0, path
        return None, None
    phases = []
    for model_path, (begin_ns, begin_tid, begin, size) in starts.items():
        end_ns, end_tid, end, _ = ends[model_path]
        assert begin_ns <= end_ns and begin["line"] < end["line"] and begin_tid == end_tid, (path, model_path)
        phases.append(dict(path=model_path, tid=begin_tid, bytes=size,
                           begin_ns=begin_ns, end_ns=end_ns,
                           begin_line=begin["line"], end_line=end["line"]))
    assert sum(phase["bytes"] for phase in phases) == cache["file_read_bytes"], path
    begin_ns = min(phase["begin_ns"] for phase in phases)
    end_ns = max(phase["end_ns"] for phase in phases)
    completion = dict(count=len(ends), unique_paths=len(ends),
                      first=next(iter(ends.values()))[2], last=next(reversed(ends.values()))[2])
    load_phase = dict(source=path.name, clock="glog_realtime_utc", count=len(phases),
                      begin_ns=begin_ns, end_ns=end_ns, wall_s=seconds(begin_ns, end_ns), paths=phases)
    return completion, load_phase


def store_replica_log(path):
    queued, confirmed = {}, {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if match := REPLICA_CREATE.search(line):
            stamp, replica = match.groups()
            assert replica not in queued, replica
            queued[replica] = (wall_ns(stamp, GLOG_TIME), stamp[:4], line_number)
        elif match := REPLICA_CONFIRM.search(line):
            stamp, model_path, replica = match.groups()
            assert replica not in confirmed, replica
            confirmed[replica] = (stamp, model_path, line_number)
    return queued, confirmed


def store_gpu_recovery(step, cache, queued, confirmed, rank_pids):
    records = []
    for line_number, line in enumerate((step / "backend.log").read_text(errors="replace").splitlines(), 1):
        if match := RECOVERY_REQUEST.search(line):
            rank, pid, model_path, replica = match.groups()
            rank, pid = int(rank), int(pid)
            assert rank_pids[rank] == pid, (step, rank, pid)
            begin, year, begin_line = queued[replica]
            stamp, confirmed_path, end_line = confirmed[replica]
            end_floor = wall_ns(year + "-" + stamp, "%Y-%m-%d %H:%M:%S")
            assert model_path == confirmed_path and begin_line < end_line and begin < end_floor + 10**9
            records.append(dict(rank=rank, pid=pid, path=model_path, replica_uuid=replica,
                                backend_request_line=line_number, create_line=begin_line,
                                confirm_line=end_line, create_wall_ns=begin, confirm_wall_floor_ns=end_floor))
    assert len(records) == len({row["replica_uuid"] for row in records})
    assert sorted(row["path"] for row in records) == sorted(cache["hit_paths"] + cache["miss_paths"]), step
    assert {row["rank"] for row in records} == set(rank_pids), step
    begin = min(row["create_wall_ns"] for row in records)
    end_floor = max(row["confirm_wall_floor_ns"] for row in records)
    return dict(backend_source=f"{step.name}/backend.log", store_source="store/store.log",
                clock="store_realtime_utc", confirm_resolution_ns=10**9, count=len(records),
                create_wall_ns=begin, last_confirm_wall_floor_ns=end_floor,
                wall_s_lower=max(0, seconds(begin, end_floor)),
                wall_s_upper=seconds(begin, end_floor + 10**9), paths=records)


def store_space(path, cache, before, sizes, chunk_bytes):
    resident = set(before["resident_paths"])
    begins, evicted_payload = [], 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if match := STORE_LOOKUP.search(line):
            stamp, model_path, size = match.groups()
            size = int(size)
            assert sizes.setdefault(model_path, size) == size
            begins.append((wall_ns(stamp, GLOG_TIME), number))
        elif match := STORE_EVICT.search(line):
            model_path = match[1]
            resident.remove(model_path)
            evicted_payload += sizes[model_path]
        elif match := STORE_COMPLETE.search(line):
            resident.add(match[1])
    after = cache["pool_after"]
    assert resident == set(after["resident_paths"]), path
    assert after["resident_payload_bytes"] == before["resident_payload_bytes"] + cache["file_read_bytes"] - evicted_payload
    assert before["used_blocks"] + cache["allocated_blocks"] - cache["freed_blocks"] == after["used_blocks"]
    assert after["used_blocks"] == sum((sizes[p] + chunk_bytes - 1) // chunk_bytes for p in resident)
    assert after["used_blocks"] + after["free_blocks"] == before["used_blocks"] + before["free_blocks"]
    return dict(source=path.name, chunk_bytes=chunk_bytes,
                capacity_bytes=(after["used_blocks"] + after["free_blocks"]) * chunk_bytes,
                resident_payload_before_bytes=before["resident_payload_bytes"],
                resident_payload_after_bytes=after["resident_payload_bytes"],
                occupied_blocks_before_bytes=before["used_blocks"] * chunk_bytes,
                occupied_blocks_after_bytes=after["used_blocks"] * chunk_bytes,
                evicted_payload_bytes=evicted_payload,
                freed_blocks_bytes=cache["freed_blocks"] * chunk_bytes,
                first_lookup_wall_ns=min(begins)[0] if begins else None,
                first_lookup_line=min(begins)[1] if begins else None)


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
            queued, confirmed = store_replica_log(run / "store/store.log")
            command = load(run / "store-created.json")["command"]
            chunk_bytes = int(command[command.index("--chunk") + 1])
            pool_bytes = int(command[command.index("--pool") + 1])
            assert chunk_bytes == 32 * 2**20 and pool_bytes == 864 * 2**30
            pool = dict(resident_paths=[], resident_payload_bytes=0, used_blocks=0,
                        free_blocks=pool_bytes // chunk_bytes)
            store_sizes = {}
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
                disk_completion, disk_load = disk_load_log(step / "store-events.log", cache) if i else (None, None)
                space = store_space(step / "store-events.log", cache, pool, store_sizes, chunk_bytes)
                pool = cache["pool_after"]
                host = item["residency"]
                host_before, host_after = host["host_kv_before_clear"], host["host_kv_after_clear"]
                assert host_before["memory_configured_bytes"] == host_after["memory_configured_bytes"]
                host_kv_space = dict(source="residency.json", peak_source="kv-write-window.json",
                                     configured_capacity_bytes=host_before["memory_configured_bytes"],
                                     effective_capacity_bytes=host_before["memory_total_bytes"],
                                     peak_used_bytes=load(step / "kv-write-window.json")["summary"]["peak_bytes"],
                                     before_clear_used_bytes=host_before["memory_used_bytes"],
                                     after_clear_used_bytes=host_after["memory_used_bytes"])
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
                recovery, lookup_to_weight_ready = None, None
                if i:
                    rank_pids = {event["rank"]: event["pid"] for event in events
                                 if event["name"] == "full_weight_load_begin"}
                    recovery = store_gpu_recovery(step, cache, queued, confirmed, rank_pids)
                    if clock_error <= 5_000_000:
                        lookup_ns = space["first_lookup_wall_ns"] - offset
                        assert began <= lookup_ns <= weight_bounds[1], step
                        lookup_to_weight_ready = dict(begin_ns=lookup_ns, end_ns=weight_bounds[1],
                                                      wall_s=seconds(lookup_ns, weight_bounds[1]),
                                                      clock_calibration_max_error_ns=clock_error)
                old = data["steps"][i - 1] if i else None
                old_res = old["residency"] if old else None
                gpu_exit_samples = None
                if old_res:
                    before = old_res["before_backend_exit"]
                    after = old_res["immediately_after_backend_exit"]
                    pids = lambda sample: {int(line.split(",")[0]) for line in sample["compute_apps"]}
                    disappeared = sorted(pids(before) - pids(after))
                    if disappeared and before["ns"] < after["ns"] <= began:
                        gpu_exit_samples = dict(before_ns=before["ns"], after_ns=after["ns"],
                                                disappeared_compute_pids=disappeared,
                                                source=f"{old['step']}/residency.json")
                steps.append(dict(step=step.name, model=row["to_model"], activation_ns=began,
                                  service_ready_ns=ready, allocation_s=item["gpu_kv_allocation_eight_rank_s"],
                                  kv_allocation_begin_ns=kv_bounds[0], kv_allocation_end_ns=kv_bounds[1],
                                  kv_allocation_and_registration_observed_ns=(
                                      max(kv_bounds[1], registration["last_host_log_ns"])
                                      if registration else None),
                                  weight_begin_ns=weight_bounds[0], weight_ready_ns=weight_bounds[1],
                                  registration=registration, cache_state=row["cache_state"],
                                  hit_bytes=cache["hit_bytes"], read_bytes=cache["file_read_bytes"],
                                  disk_completion=disk_completion,
                                  miss_path_disk_load=disk_load,
                                  store_gpu_recovery=recovery,
                                  store_lookup_to_eight_rank_weight_ready=lookup_to_weight_ready,
                                  store_space=space, host_kv_space=host_kv_space,
                                  evicted_paths=len(cache["evicted_paths"]),
                                  eviction_envelope_s=item["eviction_event_envelope_s"],
                                  store_access_envelope_s=item["store_lookup_to_complete_s"],
                                  store_combined_envelope_s=item["eviction_and_host_restore_envelope_s"],
                                  full_load_s=item["full_eight_rank_weight_load_s"],
                                  missing_block_file_read_s=None, old_gpu_kv_free_start_ns=None,
                                  old_gpu_kv_free_end_ns=None, kv_switch_wall_s=None,
                                  gpu_exit_samples=gpu_exit_samples,
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

    def disk_load(step):
        row = step["miss_path_disk_load"]
        return row["wall_s"] if row else None

    def values(step, field, key, scale=1):
        row = step[field]
        return row[key] / scale if row and row[key] is not None else None

    rows = ["# V11 大池 B1 可服务前阶段",
            "九个正式 run、63 段。下表为中位数 [最小，最大]，单位秒。每行的逐轮值、可服务端点、注册日志行号以及原 run ID 见 [重算数据](readiness-20261116.json)。[服务器证据索引](readiness-evidence-index.csv.gz) 记录绝对路径、字节数与 SHA-256。",
            f"共有 {sum(s['miss_path_disk_load'] is not None for r in rounds for s in r['steps'][1:])}/54 次切换"
            "留有 Store 缺失路径磁盘加载开始和完成日志。派生数据保留逐路径的端点、线程、字节和行号。"
            f" 另有 {sum(s['gpu_exit_samples'] is not None for r in rounds for s in r['steps'][1:])}/54 次"
            "切换可从退出前后快照核对消失的 GPU 计算 PID。该采样窗口包含整个进程的资源变化，精确 KV 释放历时仍为 null。",
            "## 首段 KV 冷启动与准备",
            "| 序列 | Host KV 池初始化 | 权重 Store 池初始化 | 串行用时之和 | 前检至首段放行 | 八 rank GPU KV 初始化 | LMCache 首末注册日志 |",
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
        "历史事件 kv_cache_allocation 覆盖 Worker.initialize_from_config，取八 rank 最早调用开始至最晚返回。该调用包含 KV 分配、绑定和 LMCache 注册。LMCache 首末跨度取同一启动窗口内八个不同实例的服务器成功注册日志，注册调用开始端点缺失。这两列不可相加。Backend ready 是独立的可服务回执。",
        "",
        "## 后六段切换",
        "| 序列/段 | 模型 | Store 驱逐事件跨度 | Store 缺失路径磁盘加载 | Store 访问至完成 | Store 事件总跨度 | 八 rank 权重加载 | 旧 Host 清理调用 | 新 GPU KV 初始化 | 新 LMCache 首末注册日志 | 旧 KV 清理至新 KV 就绪 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for seq in ("S1", "S2", "S3"):
        for i in range(1, 7):
            steps = [r["steps"][i] for r in rounds if r["sequence"] == seq]
            cols = [cell(s["eviction_envelope_s"] for s in steps), cell(disk_load(s) for s in steps)]
            cols += [cell(s[key] for s in steps) for key in
                    ("store_access_envelope_s", "store_combined_envelope_s",
                     "full_load_s", "old_host_clear_seconds", "allocation_s")]
            cols += [cell(registration(s) for s in steps), cell(s["kv_switch_wall_s"] for s in steps)]
            rows.append(f"| {seq}/{i+1} | {steps[0]['model']} | " + " | ".join(cols) + " |")
    rows.extend(["",
        "Store 缺失路径磁盘加载取所有缺失路径的最早开始日志至最晚完成日志，使用同一 Store 的 glog 实时时钟。开始日志位于打开分区文件后、派发读取线程前，完成日志位于线程回收、关闭文件和更新驻留状态之后。逐次 pread 的开始与返回未记录，独立缺块文件重读耗时保留 null。驱逐次数、访问与恢复事件并列，无驱逐或缺少驱逐包络时以逐轮驱逐路径计数区分。Store 事件可能与权重加载重叠。旧 Backend 退出和清理后快照有记录，实际 GPU KV 释放起止缺失，因此 54 次严格的 KV 清理至就绪总时长留空。旧 Host KV 为常驻池 clear。新段取 GPU KV 分配结束与末条注册成功日志中较晚的接点，并保留时钟换算偏差和独立的 Backend 就绪回执。",
        "",
        "## Store 到 GPU 的恢复接点",
        "",
        "按 Worker rank、PID、Store 路径和副本 UUID 核对客户端分派、原生 Store 创建副本及 Store 确认成功，54 次切换共 9,216 个副本。创建至确认包含等待 Host 数据、H2D、句柄关闭和 RPC 确认。原生创建日志是微秒分辨率，确认日志是整秒，表中分别报告真实跨度的下界与上界。单次 CUDA 拷贝时间缺测。首次 Store 查找至权重就绪用同机时钟校准换算到单调时钟的八 rank 完整加载结束。",
        "",
        "| 序列/段 | 模型 | 首次 Store 查找至八 rank 权重就绪（s） | 副本阶段下界（s） | 副本阶段上界（s） |",
        "| --- | --- | ---: | ---: | ---: |",
    ])
    for seq in ("S1", "S2", "S3"):
        for i in range(1, 7):
            steps = [r["steps"][i] for r in rounds if r["sequence"] == seq]
            cols = [cell(values(s, "store_lookup_to_eight_rank_weight_ready", "wall_s") for s in steps)]
            cols += [cell(values(s, "store_gpu_recovery", key) for s in steps)
                     for key in ("wall_s_lower", "wall_s_upper")]
            rows.append(f"| {seq}/{i+1} | {steps[0]['model']} | " + " | ".join(cols) + " |")
    rows.extend(["",
        "## Host 权重和 KV 空间",
        "",
        "权重 Store 使用 864 GiB 池和 32 MiB 块。有效驻留按原始 Store 路径字节计，池占用和驱逐释放另按块计。切换前取上一段已结算台账，切换后取当前段已结算台账。首段在常驻池为空时走 SafeTensors。Host KV 用状态采样取段内峰值和 clear 前后有效字节，配置容量 813 GiB。每段块占用和实际返回的 KV 池容量见派生数据。",
        "",
        "| 序列/段 | 模型 | Store 驻留前（GiB） | Store 驻留后（GiB） | 驱逐释放块（GiB） | Host KV 峰值（MiB） | Host KV clear 前（MiB） | Host KV clear 后（MiB） |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for seq in ("S1", "S2", "S3"):
        for i in range(7):
            steps = [r["steps"][i] for r in rounds if r["sequence"] == seq]
            cols = [cell(values(s, "store_space", key, 2**30) for s in steps)
                    for key in ("resident_payload_before_bytes", "resident_payload_after_bytes", "freed_blocks_bytes")]
            cols += [cell(values(s, "host_kv_space", key, 2**20) for s in steps)
                     for key in ("peak_used_bytes", "before_clear_used_bytes", "after_clear_used_bytes")]
            rows.append(f"| {seq}/{i+1} | {steps[0]['model']} | " + " | ".join(cols) + " |")
    rows.extend(["",
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
