"""Verify the native L1 Kimi → V4 → Kimi capacity run from terminal receipts."""

import argparse
import json
from pathlib import Path

from recompute_sleep import allocator_span, final_release, load_span, read, seconds


STAGES = (("K", "load", "arithmetic", 0), ("D", "load", "business", 0),
          ("K", "wake", "arithmetic", 1))
MODELS = {"K": "Kimi-K2.6", "D": "DeepSeek-V4-Pro"}


def rank_receipt(ack):
    receipts = {row["rank"]: row for row in ack["receipt"]["ranks"]}
    assert len(ack["receipt"]["ranks"]) == 8 and set(receipts) == set(range(8))
    return receipts


def acknowledged_steps(level):
    """Recover measured partial work if the controller exited before writing level-result."""
    steps = {}
    lifecycle = {key: [(path, read(path)) for path in sorted((level / f"worker-{key}").glob("ack-*.json"))]
                 for key in MODELS}
    for index, (key, kind, _, cycle) in enumerate(STAGES):
        worker = level / f"worker-{key}"
        rows = lifecycle[key]
        responses = [ack for _, ack in rows if ack["op"] == "run"]
        if len(responses) <= cycle or not responses[cycle]["checks"] or not all(
                row["passed"] for row in responses[cycle]["checks"].values()):
            continue
        response = responses[cycle]
        stage = dict(kind=kind)
        sleep_receipt, ranks, backup = None, {}, None
        if kind == "load":
            stage["ready_ns"] = read(worker / "ready.json")["ns"]
            for path, ack in rows:
                if ack["op"] == "sleep" and ack["sleeping"] and ack["started_ns"] >= response["finished_ns"]:
                    ranks = rank_receipt(ack)
                    backup = sum(row["tags"]["weights"]["cpu_backup_bytes"] for row in ranks.values())
                    sleep_receipt = f"sleep-{path.stem.split('-')[1]}-phases.json"
                    break
        else:
            path, wake = next((path, ack) for path, ack in rows if ack["op"] == "wake")
            ranks = rank_receipt(wake)
            backup = sum(row["tags"]["weights"]["cpu_backup_bytes"] for row in ranks.values())
            stage.update(ready_ns=wake["finished_ns"], phases_receipt=f"wake-{path.stem.split('-')[1]}-phases.json")
        steps[index] = dict(step=index, model=key, stage=stage, checks=response["checks"], t4_ns=response["finished_ns"],
                            sleep_phases_receipt=sleep_receipt, weights_cpu_backup_bytes=backup,
                            kv_bytes=sum(row["tags"]["kv_cache"]["bytes"] for row in ranks.values()) if ranks else None)
    return steps


def snapshot(stage, run, record, old_kv_begin):
    key, kind, request, cycle = STAGES[stage]
    level = run / "L1"
    worker = level / f"worker-{key}"
    log = worker / "worker.log"
    phase = f"L1-{stage:02d}-{key}"
    result = dict(index=stage, model=key, kind=kind, request=request, gold=None,
                  weight_s=None, kv_s=None, old_kv_release_to_new_ready_s=None,
                  weight_begin_to_ready_s=None, first_correct_s=None, all_golds_s=None,
                  gpu_kv_bytes=None, cpu_backup_bytes=None, cpu_backup_copy_s=None,
                  cpu_restore_copy_s=None, sleep_s=None, wake_s=None, sources={})

    paths = dict(vram_gate=level / f"{stage:02d}-{key}-vram-gate.json",
                 host_gate=level / f"{phase}-ram-admission.json",
                 backup_gate=level / f"{phase}-backup-capacity.json",
                 post_sleep=level / f"{phase}-mem-after-sleep.json",
                 container_created=level / f"{stage:02d}-{key}-created.json")
    result.update(container_created=paths["container_created"].is_file(),
                  **{name: read(path) if path.is_file() else None
                     for name, path in paths.items() if name != "container_created"})
    result["sources"].update({name: path.relative_to(run).as_posix() for name, path in paths.items()
                              if path.is_file()})
    if record is None:
        return result, None
    raw_stage = record["stage"]
    assert (record["step"], record["model"], raw_stage["kind"]) == (stage, key, kind)
    ready = raw_stage["ready_ns"]
    response_path = worker / f"c{cycle}-{request}-response.json"
    response = read(response_path)
    checks = read(worker / f"c{cycle}-{request}-checks.json")
    assert record["checks"][request] == checks and checks["passed"]
    assert ready <= response["started_ns"] <= response["received_ns"] <= record["t4_ns"]
    assert record["checks"] and all(check["passed"] for check in record["checks"].values())
    result["sources"]["response"] = response_path.relative_to(run).as_posix()
    result["sources"]["worker_log"] = log.relative_to(run).as_posix()

    if kind == "load":
        weight = load_span(worker, "full_weight_load", run.name, ready)
        kv = load_span(worker, "kv_cache_allocation", run.name, ready)
        weight_start = weight["begin_ns"]
        sleep_name = record["sleep_phases_receipt"]
        if sleep_name:
            lifecycle = read(worker / sleep_name)
            start, finish = lifecycle["sleep_begin_ns"], lifecycle["sleep_end_ns"]
            assert record["t4_ns"] <= start < finish
            backup = allocator_span(log, "cpu_backup_copy", "weights", start, finish)
            unmap = allocator_span(log, "gpu_unmap_release", "kv_cache", start, finish)
            if backup["begin_ns"] is not None:
                assert sum(row["bytes"] for row in backup["rows"]) == record["weights_cpu_backup_bytes"]
            result.update(sleep_s=seconds(start, finish),
                          cpu_backup_copy_s=seconds(backup["begin_ns"], backup["end_ns"]),
                          cpu_backup=backup, old_kv_unmap=unmap)
            result["sources"]["sleep_phases"] = (worker / sleep_name).relative_to(run).as_posix()
    else:
        wake_name = raw_stage["phases_receipt"]
        lifecycle = read(worker / wake_name)
        start, finish = lifecycle["combined_wake_begin_ns"], lifecycle["combined_wake_end_ns"]
        weights_map = allocator_span(log, "gpu_create_map", "weights", start, finish)
        weight = allocator_span(log, "cpu_restore_copy", "weights", start, finish)
        kv = allocator_span(log, "gpu_create_map", "kv_cache", start, finish)
        weight_start = weights_map["begin_ns"]
        result.update(wake_s=seconds(start, finish),
                      cpu_restore_copy_s=seconds(weight["begin_ns"], weight["end_ns"]))
        result["sources"]["wake_phases"] = (worker / wake_name).relative_to(run).as_posix()
    result.update(gold=True, weight_s=seconds(weight_start, weight["end_ns"]),
                  kv_s=seconds(kv["begin_ns"], kv["end_ns"]),
                  old_kv_release_to_new_ready_s=seconds(old_kv_begin, kv["end_ns"]),
                  weight_begin_to_ready_s=seconds(weight_start, ready),
                  first_correct_s=seconds(ready, response["received_ns"]),
                  all_golds_s=seconds(ready, record["t4_ns"]),
                  gpu_kv_bytes=record["kv_bytes"], cpu_backup_bytes=record["weights_cpu_backup_bytes"],
                  weight_phase=weight, gpu_kv_phase=kv)
    return result, result.get("old_kv_unmap", {}).get("begin_ns")


def audit(run):
    q = read(run / "qualification.json")
    assert q["sequence"] == "L1-KDK" and q["levels"] == [1] and q["models"] == ["K", "D", "K"]
    release = final_release(run, q)
    level, = q["results"] or [None]
    level_path = run / "L1"
    recorded = {row["step"]: row for row in level["records"]} if level else {}
    records = acknowledged_steps(level_path) | recorded
    steps = []
    old_kv = None
    for stage in range(len(STAGES)):
        result, old_kv = snapshot(stage, run, records.get(stage), old_kv)
        steps.append(result)
    cold_path = level_path / "worker-K/sf-admission/summary.json"
    cold = read(cold_path) if cold_path.is_file() else None
    cold_passed = (cold["cache_condition_passed"] and cold["mode"] == "cold"
                   and len(cold["files"]) == 64 and all(row["resident_pages"] == 0 for row in cold["files"])) if cold else None
    config = read(run / "campaign-input.json")
    k_worker = level_path / "worker-K"
    k_life = [row for path in sorted(k_worker.glob("ack-*.json"))
              if (row := read(path))["op"] in {"sleep", "wake"}]
    same_ranks = None
    if k_life and k_life[0]["op"] == "sleep":
        asleep = rank_receipt(k_life[0])
        assert k_life[0]["sleeping"]
        assert sum(x["tags"]["weights"]["cpu_backup_bytes"] for x in asleep.values()) == config["l1_backup_bytes"]["K"]
        if len(k_life) == 2:
            awake = rank_receipt(k_life[1])
            assert k_life[1]["op"] == "wake" and not k_life[1]["sleeping"]
            same_ranks = all(asleep[i]["identity"] == awake[i]["identity"]
                             and asleep[i]["device_uuid"] == awake[i]["device_uuid"]
                             and asleep[i]["allocation_signature"] == awake[i]["allocation_signature"]
                             and asleep[i]["parameter_signature"] == awake[i]["parameter_signature"]
                             and awake[i]["tags"]["weights"]["cpu_backup_bytes"] == 0 for i in range(8))
    blocker_path = level_path / "ram-infeasible.json"
    blocker = read(blocker_path) if blocker_path.is_file() else None
    if blocker and blocker["step"] == 1 and "required_bytes" in blocker:
        assert blocker["available_bytes"] - blocker["required_bytes"] == blocker["margin_bytes"] < 0
        assert not steps[1]["container_created"] and steps[1]["gold"] is None
    stopped = {}
    for model in MODELS:
        path = level_path / f"worker-{model}-final.json"
        if path.is_file():
            state = read(path)["State"]
            normal = (level_path / f"worker-{model}/normal-backend-shutdown.json").is_file()
            stopped[model] = normal and not state["Running"] and state["ExitCode"] == 0
    if q["passed"]:
        assert cold_passed and same_ranks and all(row["gold"] for row in steps) and stopped == {"K": True, "D": True}
        assert release and release["all_gpu_zero"] and release["meets_60s"]
        assert steps[1]["cpu_backup_bytes"] == config["l1_backup_bytes"]["D"] and not blocker
    stop = None
    if blocker:
        i = blocker["step"]
        stop = dict(kind="ram_infeasible", step=i, model=steps[i]["model"],
                    worker_created=steps[i]["container_created"],
                    source=blocker_path.relative_to(run).as_posix())
    elif not q["passed"]:
        for step in steps:
            if step["vram_gate"] and not step["vram_gate"]["passed"]:
                stop = dict(kind="hbm_infeasible", step=step["index"], model=step["model"],
                            worker_created=step["container_created"], source=step["sources"]["vram_gate"])
                break
        if q["failures"]:
            stop = dict(kind="runtime_failure", detail=q["failures"])
    return dict(run=run.name, passed=q["passed"], steps=steps, cold_passed=cold_passed,
                same_kimi_ranks=same_ranks, shutdown=stopped, release=release, blocker=blocker,
                stop=stop, failures=q["failures"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.run), ensure_ascii=False, indent=2))
