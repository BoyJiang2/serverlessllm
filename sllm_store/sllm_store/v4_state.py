"""Raw V4 Store metadata. This module never imports Torch or CUDA."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


FORMAT = "sllm-v4-pro-raw-v1"
REVISION = "b5968e9190ef611bbf34a7229255be88a0e937c1"
ENGINE_COMMIT = "132765e3560659ff63ebd236203672e991b70e08"
CONFIG_SHA256 = (
    "5fe4568daee51c208cb8a79538eaeda090ae011ade1dee2c386aa95f569c810e"
)
TP_SIZE = 8
KERNEL_PACKAGES = {
    "flashinfer-python": "0.6.8.post1",
    "flashinfer-cubin": "0.6.8.post1",
    "tilelang": "0.1.9",
    "apache-tvm-ffi": "0.1.9",
}
DTYPE_BYTES = {
    "torch.uint8": 1, "torch.int8": 1,
    "torch.float8_e4m3fn": 1, "torch.float8_e8m0fnu": 1,
    "torch.int16": 2, "torch.float16": 2, "torch.bfloat16": 2,
    "torch.int32": 4, "torch.float32": 4,
    "torch.int64": 8, "torch.float64": 8,
}


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path, document):
    """Publish a complete JSON file atomically, without replacing an old run."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write((json.dumps(document, sort_keys=True) + "\n").encode())
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink()
            raise
    linked = False
    try:
        os.link(temporary, path)
        linked = True
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        if linked:
            path.unlink()
        raise
    finally:
        temporary.unlink()


def checked_file(directory, name):
    if (not isinstance(name, str) or Path(name).name != name
            or name in ("", ".", "..")):
        raise ValueError("Store file name must be a basename")
    path = Path(directory) / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or linked Store file: {path}")
    return path


def validate_identity(identity):
    if (identity.get("format") != FORMAT
            or identity.get("state") != "before_quantization_postprocessing"
            or identity.get("source_revision") != REVISION
            or identity.get("engine_commit") != ENGINE_COMMIT
            or identity.get("vllm_version") != "0.20.1"
            or identity.get("tp") != TP_SIZE
            or identity.get("pp") != 1 or identity.get("dp") != 1
            or identity.get("ep") is not False
            or identity.get("activation_dtype") != "torch.bfloat16"
            or not identity.get("conversion_id")
            or not isinstance(identity.get("torch_version"), str)
            or identity["torch_version"].split("+", 1)[0] != "2.11.0"
            or identity.get("kernel_packages") != KERNEL_PACKAGES):
        raise ValueError("V4 Store identity, stage or topology mismatch")


def validate_rows(rows):
    if not isinstance(rows, dict) or not rows:
        raise ValueError("Missing tensor metadata")
    for name, row in rows.items():
        dtype = row.get("dtype")
        shape, stride = row.get("shape"), row.get("stride")
        if (not isinstance(name, str) or not name or dtype not in DTYPE_BYTES
                or not isinstance(shape, list) or not isinstance(stride, list)
                or len(shape) != len(stride)
                or any(type(n) is not int or n < 0 for n in shape + stride)):
            raise ValueError(f"Invalid tensor metadata: {name}")
        size = math.prod(shape) * DTYPE_BYTES[dtype]
        if (type(row.get("bytes")) is not int or row["bytes"] != size
                or row.get("empty", False) != (size == 0)):
            raise ValueError(f"Tensor byte count/empty mismatch: {name}")
        contiguous_stride = 1
        for dim, step in zip(reversed(shape), reversed(stride)):
            if dim > 1 and step != contiguous_stride and size:
                raise ValueError(f"Unsupported noncontiguous tensor: {name}")
            contiguous_stride *= dim
        checksum = row.get("sha256")
        if (not isinstance(checksum, str) or len(checksum) != 64
                or any(c not in "0123456789abcdef" for c in checksum)):
            raise ValueError(f"Invalid tensor checksum: {name}")
        if size == 0 and checksum != hashlib.sha256(b"").hexdigest():
            raise ValueError(f"Invalid empty tensor checksum: {name}")
        if row.get("kind") not in ("parameter", "buffer"):
            raise ValueError(f"Unknown tensor role: {name}")
        if row["kind"] == "buffer" and type(row.get("persistent")) is not bool:
            raise ValueError(f"Buffer persistence must be explicit: {name}")
        if "alias" in row:
            canonical = rows.get(row["alias"])
            if (not canonical or row["alias"] == name or "alias" in canonical
                    or size == 0 or any(
                        row[key] != canonical[key]
                        for key in ("shape", "stride", "dtype", "bytes", "sha256")
                    )):
                raise ValueError(f"Invalid or incompatible alias: {name}")


def validate_expected(rows, expected):
    validate_rows(rows)
    if set(rows) != set(expected):
        raise ValueError("V4 raw key mismatch (including scales/buffers)")
    for name, metadata in expected.items():
        if any(rows[name].get(key) != value for key, value in metadata.items()):
            raise ValueError(f"V4 raw tensor metadata mismatch: {name}")


def check_monolithic_capacity(document, capacity_bytes):
    """Lower bound only: retained raw slab plus new Marlin expert parameters."""
    rows = document["tensors"]
    raw = sum(row["bytes"] for row in rows.values()
              if not row.get("empty") and "alias" not in row)
    converted = 0
    for prefix, method in document["quant_methods"].items():
        if not method["class"].endswith(".Mxfp4MoEMethod"):
            continue
        backend = method["backends"].get("mxfp4_backend")
        if backend not in (
            "Mxfp4MoeBackend.MARLIN", "Mxfp4MoeBackend.BATCHED_MARLIN"
        ):
            raise ValueError("Monolithic V4 capacity contract requires Marlin")
        for suffix in (
            "w13_weight", "w2_weight", "w13_weight_scale", "w2_weight_scale"
        ):
            # Marlin FP4 packed weights/scales retain their raw byte counts.
            name = prefix + "." + suffix
            if name not in rows:
                raise ValueError(f"Missing V4 expert state: {name}")
            converted += rows[name]["bytes"]
    result = dict(raw_bytes=raw, converted_expert_bytes=converted,
                  minimum_peak_bytes=raw + converted)
    if result["minimum_peak_bytes"] > capacity_bytes:
        raise ValueError(
            "Monolithic raw+Marlin lower bound exceeds device HBM: "
            f"{result['minimum_peak_bytes']} > {capacity_bytes}; "
            "grouped raw or postprocessed Store representation is required"
        )
    return result


def validate_rank(root, rank, identity, verify_payload=False):
    directory = Path(root) / f"rank_{rank}"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"Missing or linked rank directory: {rank}")
    path = checked_file(directory, "raw_state.json")
    document = json.loads(path.read_text())
    if document.get("identity") != identity or document.get("rank") != rank:
        raise ValueError(f"Rank identity mismatch: {rank}")
    coordinates = dict(global_rank=rank, tp=rank, pp=0, dp=0)
    if (document.get("coordinates") != coordinates
            or not isinstance(document.get("device_uuid"), str)
            or not document["device_uuid"]):
        raise ValueError(f"Rank coordinates/device provenance missing: {rank}")
    validate_rows(document.get("tensors"))
    methods = document.get("quant_methods")
    if not isinstance(methods, dict) or any(
        not isinstance(method, dict)
        or not isinstance(method.get("class"), str)
        or not isinstance(method.get("backends"), dict)
        for method in methods.values()
    ):
        raise ValueError("Missing quantization method/backend metadata")
    files = document.get("files", {})
    if not files or "tensor_index.json" not in files:
        raise ValueError("Missing Store file manifest")
    for name, info in files.items():
        payload = checked_file(directory, name)
        if (type(info.get("bytes")) is not int or info["bytes"] < 0
                or not isinstance(info.get("sha256"), str)
                or len(info["sha256"]) != 64):
            raise ValueError(f"Invalid Store file metadata: {name}")
        if payload.stat().st_size != info["bytes"]:
            raise ValueError(f"Truncated Store file: {name}")
        if verify_payload and file_digest(payload) != info["sha256"]:
            raise ValueError(f"Store file checksum mismatch: {name}")
    canonical = {name: row for name, row in document["tensors"].items()
                 if not row.get("empty") and "alias" not in row}
    index = json.loads(checked_file(directory, "tensor_index.json").read_text())
    if set(index) != set(canonical):
        raise ValueError("Store index omits/adds a tensor (including scales)")
    parts = [name for name in files if re.fullmatch(r"tensor\.data_\d+", name)]
    if set(files) != set(parts) | {"tensor_index.json"} or not parts:
        raise ValueError("Unexpected or missing Store data parts")
    total_bytes = sum(files[name]["bytes"] for name in parts)
    for name, row in canonical.items():
        entry = index[name]
        if (not isinstance(entry, list) or len(entry) != 5
                or any(type(n) is not int or n < 0 for n in entry[:2])
                or entry[1] != row["bytes"] or entry[0] + entry[1] > total_bytes
                or entry[2:] != [row["shape"], row["stride"], row["dtype"]]):
            raise ValueError(f"Store index layout/extent mismatch: {name}")
    spans = sorted((entry[0], entry[0] + entry[1]) for entry in index.values())
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError("Canonical Store tensor extents overlap without an alias")
    return document, file_digest(path)


def publish_complete(root, identity):
    """Publish after all export workers have returned successfully."""
    validate_identity(identity)
    ranks = {}
    devices = set()
    for rank in range(TP_SIZE):
        document, ranks[str(rank)] = validate_rank(
            root, rank, identity, verify_payload=True
        )
        if document["device_uuid"] in devices:
            raise ValueError("Export ranks reported the same device UUID")
        devices.add(document["device_uuid"])
    document = {"identity": identity, "ranks": ranks}
    write_once(Path(root) / "COMPLETE.json", document)
    return document


def validate_complete(root, identity, verify_payload=False):
    """Rehash payloads untimed; timed cold loads only read metadata."""
    validate_identity(identity)
    complete = json.loads(checked_file(root, "COMPLETE.json").read_text())
    if (complete.get("identity") != identity
            or set(complete.get("ranks", {})) != {
                str(rank) for rank in range(TP_SIZE)
            }):
        raise ValueError("Incomplete or incompatible V4 Store")
    for rank in range(TP_SIZE):
        _, digest = validate_rank(root, rank, identity, verify_payload)
        if digest != complete["ranks"][str(rank)]:
            raise ValueError(f"Rank manifest changed after publication: {rank}")
    return complete
