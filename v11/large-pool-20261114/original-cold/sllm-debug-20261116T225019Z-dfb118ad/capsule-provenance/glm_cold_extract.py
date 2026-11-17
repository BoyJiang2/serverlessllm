#!/usr/bin/env python3
import gzip
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

base = Path("/data/brick1/boyjiang/glm5.1-fp8")
run = sys.argv[1]
assert re.fullmatch(r"sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", run)
root = base / "debug" / run
checks = {}
files = {}


def verify(name, condition):
    checks[name] = condition is True


def read(relative):
    path = root / relative
    if not path.is_file():
        files[relative] = {"missing": True}
        return {}
    data = path.read_bytes()
    files[relative] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return json.loads(data)


def cache_summary():
    cache = read("debug-service/cache-admission/summary.json")
    if not cache:
        return None
    store_meta = read("source/store-metadata-inputs.json")
    sf = read("source/cache-inputs13.json")
    raw = read("/data/brick1/boyjiang/glm5.1-fp8/store/tp8/raw-disk-verification.json")
    verify("frozen_store_metadata", files["source/store-metadata-inputs.json"]["sha256"] == "888fd5cf5f277f275de865b2ac0fd269295b8836f5d871e7e3ae0d6ca9e45734")
    verify("frozen_checkpoint_closure", files["source/cache-inputs13.json"]["sha256"] == "a210862f708c1b8c106d1d7a7c235cc818ce9b53a0c5232f7ffab53979ef5473")
    verify("frozen_raw_store", files["/data/brick1/boyjiang/glm5.1-fp8/store/tp8/raw-disk-verification.json"]["sha256"] == "dcc2d3c6d63b12802206e17273bc823b664a61cd9783e8d8e8a22063ca2eacdb")
    prefix = "store/vllm/glm51-fp8/"
    expected = {prefix+key: item["bytes"] for key, item in store_meta["files"].items()}
    labels = {key: "store_metadata" for key in expected}
    for r in raw["ranks"]:
        if r["rank"] == "inactive_mtp":
            continue
        for i, size in enumerate(r["partition_bytes"]):
            key = prefix + f"rank_{r['rank']}/tensor.data_{i}"
            expected[key] = size
            labels[key] = "store_payload"
    for item in sf["files"]:
        if item["path"] in ("config.json", "generation_config.json", "tokenizer_config.json",
                            "tokenizer.json", "chat_template.jinja", "model.safetensors.index.json"):
            key = "checkpoint/" + item["path"]
            expected[key] = item["bytes"]
            labels[key] = "shared_checkpoint_metadata"
    rows = cache["files"]
    verify("cache_complete", len(rows) == len(expected) and {r["file"]: r["bytes"] for r in rows} == expected)
    groups = {}
    vectors_valid = True
    for row in rows:
        file = row["file"]
        if file not in labels:
            vectors_valid = False
            continue
        group = groups.setdefault(labels[file], Counter())
        group.update(files=1, bytes=row["bytes"], pages=row["pages"], resident_pages=row["resident_pages"])
        pages = (expected[file]+4095)//4096
        vector = "debug-service/cache-admission/" + file + ".mincore.gz"
        path = root / vector
        if (row["vector"] != vector.removeprefix("debug-service/") or row["source_path"] != "/experiment/"+file
                or row["page_size"] != 4096 or row["pages"] != pages
                or not path.is_file() or path.is_symlink()):
            vectors_valid = False
            continue
        with gzip.open(path, "rb") as stream:
            flags = stream.read(pages+1)
        resident = flags.translate(bytes(i&1 for i in range(256))).count(bytes([1]))
        vectors_valid &= len(flags) == pages and hashlib.sha256(flags).hexdigest() == row["vector_sha256"] and resident == row["resident_pages"] == 0
    verify("cache_vectors", vectors_valid)
    verify("cache_accepted", cache["condition"] == "store_disk_cold" and cache["passed"] is True and not cache["pool_confirmed_ranks"])
    return groups


def observation(relative):
    path = root / relative
    if not path.is_file():
        return None
    data = path.read_bytes()
    files[relative] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    stamps = [row["monotonic_ns"] for row in rows]
    return {
        "samples": len(stamps),
        "first_ns": min(stamps) if stamps else None,
        "last_ns": max(stamps) if stamps else None,
        "span_s": (stamps[-1]-stamps[0])/1e9 if len(stamps)>1 else None,
        "ordered_fresh": len(stamps)>1 and all(0 < b-a <= 5*10**9 for a,b in zip(stamps, stamps[1:])),
    }


def all_zero_released(path, start):
    path = root / path
    if not path.is_file():
        return False
    devices = {row["uuid"] for row in start["resource_admission"]["gpu_devices"]}
    for line in path.read_bytes().splitlines():
        row = json.loads(line)
        gpu = [r.split(",") for r in row["gpu"].splitlines()]
        if (row["run_id"] != run or row["boot_id"] != start["boot_id"]
                or row["gpu_processes"].strip() or len(gpu)!=8
                or {item[1].strip() for item in gpu} != devices
                or any(item[3].strip() != "0 MiB" for item in gpu)):
            return False
    return True


names = ("Id","Result","MainPID","SubState","ExecMainCode","ExecMainStatus","InvocationID","ExecMainStartTimestampMonotonic","ExecMainExitTimestampMonotonic")
raw = subprocess.check_output(["systemctl","show",run+"-controller.service","--property="+",".join(names)],text=True)
unit = dict(line.split("=",1) for line in raw.splitlines() if "=" in line)
terminal = unit.get("MainPID") == "0" and unit.get("SubState") in ("exited","failed","dead") and int(unit.get("ExecMainExitTimestampMonotonic") or "0") > 0
if not terminal:
    print(json.dumps({"run_id":run,"state":"active_or_unavailable","unit":unit},indent=2))
    sys.exit(2)
start = read("controller-start.json")
result = read("controller-result.json")
worker = read("debug-service/result.json")
final = read("controller-final.json")
cache = cache_summary()
release = observation("release60.jsonl")
idle = observation("debug-service/idle60.jsonl")
verify("kernel_exit_success", tuple(unit.get(k) for k in ("Result","ExecMainCode","ExecMainStatus")) == ("success","1","0"))
verify("terminal_results_success", result.get("success") is True and worker.get("success") is True)
verify("terminal_identity", bool(result and worker and start) and
       all(row.get("run_id")==run and row.get("condition")=="store_disk_cold" and row.get("holder")==start["holder"] and row.get("boot_id")==start["boot_id"] for row in (start,result,worker)) and
       start["independent_controller_unit"]["InvocationID"]==unit["InvocationID"])
verify("delete_idle_60s", bool(idle and idle["ordered_fresh"] and idle["span_s"]>=60 and worker.get("idle_observation_seconds",0)>=60))
verify("all_removed_all_zero_60s", bool(release and release["ordered_fresh"] and release["span_s"]>=60
       and result.get("release_observation_s",0)>=60 and all_zero_released("release60.jsonl",start)))
timing = None
keys = ("preparation_begin_ns","preparation_complete_ns","startup_command_ns","business_trigger_ns","correct_business_response_ns","delete_complete_ns")
if all(key in worker for key in keys) and "all_owned_containers_removed_ns" in result:
    pb, pe, begin, trigger, end, deleted = (worker[k] for k in keys)
    c0 = int(unit["ExecMainStartTimestampMonotonic"])*1000
    c1 = int(unit["ExecMainExitTimestampMonotonic"])*1000
    requests = worker.get("requests", [])
    verify("ordered_time_and_first_gold", bool(requests and all(r["gold_match"] is True for r in requests) and
           requests[0]["name"]=="business_arithmetic" and
           (requests[0]["begin_ns"],requests[0]["response_complete_ns"])==(trigger,end) and
           c0==start["controller_begin_ns"] and c0<pb<pe<=begin<trigger<end<deleted<result["all_owned_containers_removed_ns"]<c1))
    timing = dict(origin_ns=begin, first_correct_full_ns=end, first_trigger_ns=trigger,
                  prep_s=(pe-pb)/1e9, A_s=(end-begin)/1e9, trigger_to_first_s=(end-trigger)/1e9,
                  controller_s=(c1-c0)/1e9)
    verify("worker_metric_crosscheck", worker["metrics"]["command_to_correct_business_response_s"]==timing["A_s"])
    if final.get("success") is True:
        verify("finalizer_crosscheck", timing["A_s"]==final["metrics"]["command_to_correct_business_response_s"]
               and timing["controller_s"]==final["metrics"]["controller_s"])
verify("timing_present", timing is not None)
verify("cold_receipt_present", cache is not None)
print(json.dumps(dict(run_id=run, condition="store_disk_cold", state="terminal",
                      supported_for_reporting=bool(final.get("success") is True and all(checks.values())),
                      finalizer_present=bool(final),
                      checks=checks, timing=timing, cache=cache,
                      post_delete=idle, post_all_removed=release,
                      failure=final.get("failure") or result.get("error") or worker.get("error"), unit=unit, files=files),indent=2))
