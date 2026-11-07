"""Root-only bounded collection of existing Kimi startup logs; never runs a model."""
import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import stat

PER_FILE = 8 << 20
TOTAL = 128 << 20
MODULES = {"ray", "sllm.cli.clic", "sllm.controller", "sllm.routers.roundrobin_router", "sllm.backends.vllm_backend", "sllm_store.client"}
PACKAGES = {"torch", "vllm", "transformers", "grpcio", "ray", "msgpack", "GPUtil", "speedtest-cli", "flashinfer-python", "flashinfer-cubin"}
LOAD = re.compile(r"Model loading took ([0-9]+(?:\.[0-9]+)?) GiB memory and ([0-9]+(?:\.[0-9]+)?) seconds")


def read_stable(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("nonregular_or_size_limit")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if len(raw) > limit or identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError("file_identity_changed_or_size_limit")
        return raw, {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "mtime_ns": after.st_mtime_ns}
    finally:
        os.close(fd)


def read_ref(ref):
    raw, _ = read_stable(ref["path"], 8 << 20)
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("reference_sha_mismatch")
    return json.loads(raw)


def extract(raw):
    markers, imports = [], []
    for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        for match in LOAD.finditer(line):
            memory, seconds = map(float, match.groups())
            if not all(math.isfinite(x) for x in (memory, seconds)):
                raise ValueError("nonfinite_load_marker")
            markers.append({"line": line_number, "model_loading_seconds": seconds, "model_memory_GiB": memory, "rank": None, "pid": None})
        if "NATIVE_IMPORTS_PASS " in line:
            try:
                record = json.loads(line.split("NATIVE_IMPORTS_PASS ", 1)[1])
                origins = {k: v for k, v in record.get("origins", {}).items() if k in MODULES and isinstance(v, str) and len(v) <= 512 and v.startswith(("/source/", "/usr/local/lib/")) and not any(ord(c) < 32 for c in v)}
                versions = {k: v for k, v in record.get("versions", {}).items() if k in PACKAGES and isinstance(v, str) and re.fullmatch(r"[0-9][A-Za-z0-9.+_-]{0,79}", v)}
                imports.append({"line": line_number, "origins": origins, "versions": versions})
            except (ValueError, TypeError, AttributeError):
                imports.append({"line": line_number, "status": "MALFORMED_IMPORT_MARKER"})
    return markers, imports


def collect(manifest_ref, output):
    if os.geteuid() != 0:
        raise PermissionError("root_required_before_collection")
    manifest = read_ref(manifest_ref)
    selected = [r for r in manifest["runs"] if r["cohort"] == "original" and r["refs"].get("validity") and read_ref(r["refs"]["validity"]).get("primary_sample_qualified") is True]
    if len(selected) != 38 or len({r["run_id"] for r in selected}) != 38:
        raise ValueError("expected_38_distinct_original_qualified_runs")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        result = {"schema": "kimi-existing-startup-evidence-v1", "manifest": manifest_ref, "runs": [], "log_bytes_read": 0,
                  "semantics": "Model loading duration spans perf_counter before get_model_loader through load_model and optional LoRA/drafter/aux setup; not GLM weight-ingestion, capture_seconds, backend initialization, TTFT or pure decode."}
        for run in selected:
            root = pathlib.Path(run["original_runtime_root"])
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                raise ValueError("invalid_runtime_root")
            files = sorted([*root.glob("*.stdout"), *root.glob("*.stderr")])
            row = {"run_id": run["run_id"], "files": [], "model_loading_seconds": None, "observation_status": "NO_LOG_FILES" if not files else "NO_MATCH"}
            durations = []
            for path in files:
                raw, identity = read_stable(path, min(PER_FILE, TOTAL - result["log_bytes_read"]))
                result["log_bytes_read"] += len(raw)
                markers, imports = extract(raw)
                row["files"].append(dict(identity, source_path=str(path), model_loading_markers=markers, import_markers=imports))
                durations.extend(q["model_loading_seconds"] for q in markers)
            if durations:
                row["model_loading_seconds"] = durations
                row["observation_status"] = "OBSERVED"
            result["runs"].append(row)
        payload = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "EXISTING_LOG_SUMMARY_WRITTEN", "runs": len(selected), "bytes_read": result["log_bytes_read"], "output_sha256": hashlib.sha256(payload).hexdigest()}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--manifest-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(collect({"path": args.manifest, "sha256": args.manifest_sha}, args.output)))


if __name__ == "__main__":
    main()
