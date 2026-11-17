import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path("/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-v4-original-qjhs003")
PIN = "88cc71ec50c8668c26f26636d7d432c3351356fe"
REVIEWER_SHA = "ef0ce428ff9078f02fbada92a00a8797e457c4677fa2746c36daf20991d5cfb4"


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def stopped(rid):
    pid = int((ROOT / f"{rid}.pid").read_text())
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return
    require(state == "Z", f"Controller PID {pid} is still present ({state})")


def review_host_baseline(run, prep, expected):
    out = run / "output"
    path = out / "store-pre-model-baseline-checked.json"
    receipt = read(path)
    rows = receipt["samples"]
    gpu_map = receipt["expected_gpu_mib"]
    observed_gpu_set = {r[1] for r in prep["gpu_initial"]["rows"]}
    require(gpu_map == prep["context_gpu_baseline_mib"] and len(gpu_map) == 8
            and set(gpu_map.values()) == {expected} and set(gpu_map) == observed_gpu_set
            and receipt["tolerance_mib"] == 16, "Configured GPU baseline differs")
    start, end = rows[0]["monotonic_ns"], rows[-1]["monotonic_ns"]
    startup = read(out / "trial-startup.json")["record"]["startup_command_ns"]
    require(end-start >= 60_000_000_000 and receipt["sample_span_seconds"] == (end-start)/1e9
            and receipt["checked_ns"] == end <= rows[-1]["observation_end_ns"] <= startup
            and receipt["gpu"] == rows[-1]["gpu"], "Initialized baseline duration/order differs")
    require(all(0 <= b["monotonic_ns"]-a["observation_end_ns"] <= 5_000_000_000
                for a, b in zip(rows, rows[1:])), "Baseline sample gap exceeds original guard")
    telemetry = {}
    with (run / "telemetry.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            if start <= row["monotonic_ns"] <= end:
                telemetry[row["monotonic_ns"]] = row
    for row in rows:
        parent = telemetry[row["monotonic_ns"]]
        require(all(parent[k] == row[k] for k in ("monotonic_ns", "observation_end_ns", "gpu")),
                "Baseline sample differs from raw controller telemetry")
        gpu = row["gpu"]
        require(len(gpu["rows"]) == 8 and {r[1] for r in gpu["rows"]} == set(gpu_map)
                and all(abs(int(r[4])-gpu_map[r[1]]) <= 16 for r in gpu["rows"]), "GPU baseline differs")
        apps = [line.split(",") for line in gpu["apps"].splitlines() if line.strip()]
        require(len(apps) == 8 and {r[1].strip() for r in apps} == set(gpu_map)
                and all(int(r[0]) == receipt["daemon_host_pid"] and 0 <= int(r[2]) <= 338 for r in apps),
                "Initialized Store process set differs")
    return dict(passed=True, span_s=receipt["sample_span_seconds"], samples=len(rows),
                expected_gpu_mib=expected, tolerance_mib=16, receipt_sha256=sha(path))


def review_jit(run, condition):
    kind = "sf-retry" if condition == "safetensors_disk_cold" else "store"
    runtime = Path("/data/brick3/boyjiang") / f"v11-v4-original-qjhs003-runtime-{kind}"
    files = [runtime / f"jit-closure-{n}.json" for n in ("receipt", "manifest")]
    receipt = read(files[0])
    declared = read(run / "READY.json")["roles"]
    expected = str(ROOT / "runtime/cache-native-reference-20261106T205752Z")
    require(f"type=bind,src={expected},dst=/runtime-cache" in declared["worker"]["command"]
            and (runtime / ("native-service-" + run.name) / "head-ray").is_dir(),
            "Can't bind run to the selected per-arm JIT runtime")
    require(receipt["condition"] == condition and receipt["readback_files"] == 244
            and receipt["paired_deepgemm_dirs"] == 70 and receipt["bytes"] == 9_488_482
            and receipt["closure_manifest_sha256"] == sha(files[1]),
            "Cold arm JIT closure receipt differs")
    return dict(runtime=str(runtime), receipt_sha256=sha(files[0]), manifest_sha256=sha(files[1]))


def main():
    require(__debug__ and len(sys.argv) == 2
            and re.fullmatch(r"v4n40_pilot_003_(sf|store)_[0-9]{8}T[0-9]{4,6}Z", sys.argv[1]),
            "Use a cold nonformal run ID and non-optimized Python")
    rid = sys.argv[1]
    stopped(rid)
    run = ROOT / "evidence" / rid
    require((run / "cleanup.json").is_file() and not (run / "failure.json").exists(), "Successful terminal needed")
    prep = read(run / "preparation.json")
    condition = prep["trial"]["condition"]
    require(condition in ("safetensors_disk_cold", "store_disk_cold"), "Outside cold scope")
    host = ROOT / "host-inputs"
    require(prep["trial"]["protocol_sha256"] == sha(host / "protocol.json")
            and prep["trial"]["amendment_sha256"] == sha(host / "amendment.json"), "Host input digest mismatch")
    require(sha(ROOT / "code/benchmarks/v4_trial_review.py") == REVIEWER_SHA, "Pinned trial reviewer source differs")
    sys.path[:0] = [str(ROOT / "code/benchmarks"), str(ROOT / "code/sllm_store")]
    from v4_trial_review import review_trial

    result = review_trial(run, expected_head=PIN, publish=False, amendment_path=host / "amendment.json",
                          amendment_sha=prep["trial"]["amendment_sha256"])
    require(result["runtime_qualified"] and result["full_model_condition_pilot_qualified"]
            and not result["formal_id_consumed"] and not result["success"], "Not qualified as a nonformal pilot")
    result["qjhs003_initialized_store_context"] = review_host_baseline(
        run, prep, 345 if condition == "safetensors_disk_cold" else 1997)
    result["qjhs003_jit_closure"] = review_jit(run, condition)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
