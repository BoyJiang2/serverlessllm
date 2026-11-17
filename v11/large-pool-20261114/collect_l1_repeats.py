"""Read the terminal qjhs003 L1 batch into a verified publication directory."""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


CAMPAIGN = Path("/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117")
SOURCE_FILES = ("campaign.json", "capacity-admission.json", "v11_run_b2.py", "v11_sleep_backend.py",
                "v7_sleep_worker.py", "cumem.py", "sf_admission.py", "layerwise.py",
                "candidate-manifest.json", "observe-failed-l1-release.py")
FIELDS = ("server_path", "bytes", "sha256", "published_path")
KDK_CAMPAIGN = CAMPAIGN.with_name("v11-b2-l1-kdk-exact-20261118")
KDK_SOURCES = ("campaign.json", "historical-bounds.json", "v11_run_b2.py", "v11_sleep_backend.py",
               "v7_sleep_worker.py", "cumem.py", "sf_admission.py", "layerwise.py",
               "candidate-manifest.json", "cumem-exact-backup.patch", "test_cumem_cpu.py",
               "run-after-l2-r3.sh", "handoff.log")


def terminal_qualification(run):
    receipt = run / "qualification.json"
    if receipt.is_file():
        return json.loads(receipt.read_bytes())
    recovery = run / "qualification-recovery-release.jsonl"
    if (run / "failure.json").is_file() and recovery.is_file():
        samples = [json.loads(line) for line in recovery.read_bytes().splitlines()]
        if (samples and samples[-1]["ns"] - samples[0]["ns"] >= 60 * 10**9
                and all(a["ns"] < b["ns"] for a, b in zip(samples, samples[1:]))
                and all(row["gpu_used_mib"] == [0] * 8 and row["compute_apps"] == 0
                        and row["container_running"] is False for row in samples)):
            return None
    raise SystemExit(f"Missing terminal qualification or failed-run release observation for {run.name}")


def evidence_files(require_complete=True, kdk=False):
    campaign = KDK_CAMPAIGN if kdk else CAMPAIGN
    runs = sorted((campaign / "runs").glob("B2-L1-KDK-*" if kdk else "B2-L1-D-*"))
    if not runs:
        raise SystemExit("No L1 repetitions found")
    runner_sources = sorted(campaign.glob("v11_run_b2-used-by-l1-*.py"))
    runner_pins = {hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in [campaign / "v11_run_b2.py", *runner_sources]}
    accepted = []
    for run in runs:
        q = terminal_qualification(run)
        if q and q["passed"] and q["final_all_gpu_zero"] and q["final_release_seconds"] >= 60:
            accepted.append(q["rep"])
        if (run / "instrumentation.json").is_file():
            pins = json.loads((run / "instrumentation.json").read_bytes())
            for name, digest in pins.items():
                sources = runner_pins if name == "v11_run_b2.py" else {
                    hashlib.sha256((campaign / name).read_bytes()).hexdigest()}
                if digest not in sources:
                    raise ValueError(f"{run.name}: provenance checksum mismatch for {name}")
    if require_complete and sorted(accepted) != [1, 2, 3]:
        raise SystemExit(f"Expected all three L1 repetitions to qualify, found {accepted}")

    for name in KDK_SOURCES if kdk else SOURCE_FILES:
        yield campaign / name, name if name == "capacity-admission.json" else f"provenance/{name}"
    for path in runner_sources:
        yield path, f"provenance/{path.name}"
    for pattern in ("L1-D-r*-controller.*.log", "l1-batch*.log", "run-l1-batch*.sh"):
        for path in sorted(campaign.glob(pattern)):
            yield path, f"provenance/{path.name}"
    for run in runs:
        for folder, dirs, files in os.walk(run):
            dirs[:] = sorted(name for name in dirs if name not in {"cache", ".cache", "__pycache__"})
            for name in sorted(files):
                path = Path(folder) / name
                if path.is_symlink():
                    continue
                if path.suffix in {".json", ".jsonl", ".log", ".csv"} or name.endswith(".mincore.gz"):
                    rel = path.relative_to(run.parent)
                    yield path, (Path("runs") / rel if kdk else rel).as_posix()
                elif name == "receipt.tar":
                    yield path, ""


def emit_archive(require_complete, kdk=False):
    paths = list(evidence_files(require_complete, kdk))
    index = io.StringIO(newline="")
    writer = csv.writer(index)
    writer.writerow(FIELDS)
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
        for source, published in paths:
            data = source.read_bytes()
            writer.writerow((source.as_posix(), len(data), hashlib.sha256(data).hexdigest(), published))
            if published:
                add_file(archive, published, data)
        add_file(archive, "evidence-index.csv", index.getvalue().encode("utf-8"))


def add_file(archive, name, data):
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))


def verify(output):
    with (output / "evidence-index.csv").open(newline="", encoding="utf-8") as stream:
        index = list(csv.DictReader(stream))
    published = {}
    for row in index:
        name = row["published_path"]
        if name:
            if name in published:
                raise ValueError(f"Duplicate evidence path: {name}")
            published[name] = row
    files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    if files != set(published) | {"evidence-index.csv"}:
        raise ValueError("Archive contents differ from the evidence index")
    for name, row in published.items():
        data = (output / name).read_bytes()
        if len(data) != int(row["bytes"]) or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError(f"Evidence checksum mismatch: {name}")
    return len(published), len(index) - len(published)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--terminal-only", action="store_true",
                        help="collect terminal runs even if the batch stopped before all three passed")
    parser.add_argument("--kdk", action="store_true", help="collect exact-size KDK cohort")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error(f"output already exists: {output}")
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        staged = Path(directory) / "publication"
        with tempfile.TemporaryFile() as archive_stream:
            command = ["ssh", "-o", "BatchMode=yes", "qjhs003", "python3", "-B", "-", "--emit-archive"]
            if args.terminal_only:
                command.append("--terminal-only")
            if args.kdk:
                command.append("--kdk")
            result = subprocess.run(command, input=Path(__file__).read_bytes(), stdout=archive_stream)
            if result.returncode:
                raise SystemExit(result.returncode)
            archive_stream.seek(0)
            with tarfile.open(fileobj=archive_stream, mode="r:gz") as archive:
                archive.extractall(staged, filter="data")
        published, server_only = verify(staged)
        (staged / ".gitattributes").write_text("* -text\n", encoding="utf-8", newline="\n")
        staged.rename(output)
    print(f"Verified {published} published files and indexed {server_only} server-only files: {output}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--emit-archive":
        emit_archive("--terminal-only" not in sys.argv, "--kdk" in sys.argv)
    else:
        main()
