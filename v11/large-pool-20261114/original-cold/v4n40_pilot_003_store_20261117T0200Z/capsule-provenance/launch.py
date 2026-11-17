"""Run an original V4 cold-read pilot using qjhs003 asset and device evidence."""
import argparse
import datetime
import fcntl
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

CAPSULE = Path(__file__).resolve().parent
ROOT = CAPSULE / "code"
INPUTS = CAPSULE / "host-inputs"
RUNTIME = CAPSULE / "runtime"
STORAGE = "/data/brick3/boyjiang/sllm-three-model-store-20261109/v4"
PIN = "88cc71ec50c8668c26f26636d7d432c3351356fe"
ENTRY = "benchmarks/v4_service_once.py"
SITE = Path("/home/boyjiang/sllm-v4-pro")
LOCK = SITE / "native-run.lock"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise SystemExit(f"Pinned launcher hook differs: {old!r}")
    return source.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("condition", choices=("safetensors_disk_cold", "store_disk_cold"))
    parser.add_argument("run_id")
    parser.add_argument("--protocol-sha", required=True)
    parser.add_argument("--amendment-sha", required=True)
    parser.add_argument("--store-sha", required=True)
    parser.add_argument("--booking-end")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"v4n40_pilot_[A-Za-z0-9_-]{1,47}", args.run_id):
        parser.error("Use a new nonformal name of at most 60 characters")
    if subprocess.check_output(["hostname", "-s"], text=True).strip() != "qjhs3":
        parser.error("qjhs003 host receipts require qjhs3")
    base = CAPSULE / "evidence" / args.run_id
    if base.exists() or base.is_symlink():
        parser.error("Run directory already exists")
    if not (CAPSULE / "native-run.lock").samefile(LOCK):
        parser.error("Isolated launcher must bind the original host lock")
    def git(*parts):
        return subprocess.check_output(["git", "-C", str(ROOT), *parts])
    if git("rev-parse", "HEAD").decode().strip() != PIN or git("status", "--porcelain", "--untracked-files=all"):
        parser.error("Exact clean original source tree required")
    entry = ROOT / ENTRY
    raw = entry.read_bytes()
    if raw != git("cat-file", "blob", f"{PIN}:{ENTRY}"):
        parser.error("Launcher bytes differ from the original Git blob")
    for filename, expected in (("protocol.json", args.protocol_sha),
                               ("amendment.json", args.amendment_sha),
                               ("qualified-store.json", args.store_sha)):
        if digest(INPUTS / filename) != expected:
            parser.error(f"{filename} changed")
    lock = None
    if args.execute:
        if not args.booking_end:
            parser.error("Pass the live verified booking end")
        end = datetime.datetime.fromisoformat(args.booking_end)
        if end.utcoffset() is None or (end - datetime.datetime.now(datetime.timezone.utc)).total_seconds() < 3720:
            parser.error("The active booking must cover the original 3600s guard plus 120s margin")
        lock = (SITE / "cache-competition-20261109/v11/active.lock").open("r+")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    source = raw.decode()
    source = replace_once(source, "ROOT=Path('/home/boyjiang/sllm-v4-pro/code')",
                          "ROOT=Path(__file__).resolve().parents[1]")
    source = replace_once(source, "STORAGE=Path('/data/brick1/boyjiang/sllm-v4-pro-store')",
                          f"STORAGE=Path({STORAGE!r})")
    source = replace_once(source, "BUILD=Path('/data/brick1/boyjiang/sllm-v4-pro-build')",
                          f"BUILD=Path({str(RUNTIME)!r})")
    source = replace_once(source, "ROOT/'docs/evidence/v4-store-data-integrity/qualified-store.json'",
                          f"Path({str(INPUTS / 'qualified-store.json')!r})")
    source = replace_once(source, "assert set(context_gpu)=={row[1] for row in baseline['rows']}",
        """# Re-cap the same historical bounds for the live device set, then measure this node.
    expected_mib, = set(context_gpu.values())
    context_gpu={row[1]:expected_mib for row in baseline['rows']}
    context_samples=[]""")
    source = replace_once(source,
        """                    write_once(output/'store-pre-model-baseline-checked.json',dict(checked_ns=sample['monotonic_ns'],
                        daemon_host_pid=daemon_host_pid,gpu=sample['gpu'],scope='Fresh initialized Store before first model request'))
""",
        """                    if context_samples and sample['monotonic_ns']-context_samples[-1]['observation_end_ns']>5_000_000_000:
                        context_samples.clear()
                    context_samples.append(dict(monotonic_ns=sample['monotonic_ns'],
                        observation_end_ns=sample['observation_end_ns'],gpu=sample['gpu']))
                    span=sample['monotonic_ns']-context_samples[0]['monotonic_ns']
                    if span>=60_000_000_000:
                        write_once(output/'store-pre-model-baseline-checked.json',dict(checked_ns=sample['monotonic_ns'],
                            daemon_host_pid=daemon_host_pid,gpu=sample['gpu'],scope='qjhs003 initialized Store before first model request',
                            expected_gpu_mib=context_gpu,tolerance_mib=16,sample_span_seconds=span/1e9,samples=context_samples))
""")
    source = replace_once(source,
        "            DRIVER_FUTURE=driver_pool.submit(drive_trial,base,preparation,ACTIVE_JOURNAL)",
        """            def drive_after_local_baseline():
                from v4_trial import wait_json
                wait_json(output/'store-pre-model-baseline-checked.json',container_deadline-120_000_000_000)
                return drive_trial(base,preparation,ACTIVE_JOURNAL)
            DRIVER_FUTURE=driver_pool.submit(drive_after_local_baseline)""")
    sys.dont_write_bytecode = True
    sys.argv = [str(entry), str(base), PIN, "--condition", args.condition,
                "--trial-protocol", str(INPUTS / "protocol.json"), "--trial-sha256", args.protocol_sha,
                "--amendment", str(INPUTS / "amendment.json"), "--amendment-sha", args.amendment_sha]
    if args.execute:
        sys.argv.append("--execute")
    try:
        exec(compile(source, str(entry), "exec"), {"__name__": "__main__", "__file__": str(entry)})
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    main()
