import csv
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
worker = run / "L2/worker-K"

def read(path):
    return json.loads((run / path).read_text())

def ranks(ack):
    entries = ack["receipt"]["ranks"]
    result = {row["rank"]: row for row in entries}
    assert len(entries) == 8 and set(result) == set(range(8))
    return result

qualification = read("qualification.json")
assert qualification["passed"]
cold, sleep, wake, warm = [
    json.loads((worker / f"ack-{n:04d}.json").read_text()) for n in range(4)
]
assert [ack["op"] for ack in (cold, sleep, wake, warm)] == ["run", "sleep", "wake", "run"]
assert sleep["sleeping"] and not wake["sleeping"]
assert cold["checks"] and cold["checks"].keys() == warm["checks"].keys()
assert all(row["passed"] for ack in (cold, warm) for row in ack["checks"].values())
before, after = ranks(sleep), ranks(wake)
for rank in before:
    assert before[rank]["identity"] == after[rank]["identity"]
    assert before[rank]["tags"]["weights"]["cpu_backup_bytes"] == 0
assert (worker / "normal-backend-shutdown.json").is_file()
state = read("L2/worker-K-final.json")["State"]
assert state["ExitCode"] == 0 and not state["Running"] and not state["OOMKilled"]
with (run / "resource-samples.csv").open() as stream:
    release = [row for row in csv.DictReader(stream) if row["phase"] == "final-release"]
assert all(all(int(row[f"gpu{i}"]) == 0 for i in range(8))
           and int(row["compute_apps"]) == 0 for row in release)
seconds = (int(release[-1]["ns"]) - int(release[0]["ns"])) / 1e9
assert seconds >= 60
stage = qualification["results"][0]["records"][1]["stage"]
assert stage["kind"] == "wake"
assert stage["reload_seconds"] == round(wake["reload_ns"] / 1e9, 3)
print(json.dumps(dict(run=run.name, worker_pids={k: v["identity"]["pid"] for k, v in after.items()},
                      golds=list(warm["checks"]), wake_seconds=stage["seconds"],
                      reload_seconds=stage["reload_seconds"], released_seconds=seconds), indent=2))
