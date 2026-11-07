"""Grouped raw V4 metadata; payload groups retain the existing raw contract."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from sllm_store.v4_state import (
    TP_SIZE, checked_file, file_digest, validate_identity, validate_rank,
    validate_rows, write_once,
)

FORMAT = "sllm-v4-pro-grouped-raw-v2"
EXPERT_SUFFIXES = ("w13_weight", "w2_weight", "w13_weight_scale", "w2_weight_scale")


def partition_groups(rows, methods, expert_order=None):
    """Preserve native module traversal order; reject cross-allocation aliases."""
    experts = [prefix for prefix, method in methods.items()
               if method["class"].endswith(".Mxfp4MoEMethod")]
    if len(experts) != 61:
        raise ValueError("Grouped V4 requires all 61 expert modules")
    if expert_order is not None:
        if (any(not isinstance(p, str) for p in expert_order)
                or len(expert_order) != 61 or set(expert_order) != set(experts)):
            raise ValueError("Invalid explicit expert traversal order")
        experts = expert_order
    groups, membership = [], {}
    for index, prefix in enumerate(experts):
        if methods[prefix]["backends"].get("mxfp4_backend") not in (
            "Mxfp4MoeBackend.MARLIN", "Mxfp4MoeBackend.BATCHED_MARLIN"
        ):
            raise ValueError("Grouped V4 requires the Marlin expert backend")
        canonical = {prefix + "." + suffix for suffix in EXPERT_SUFFIXES}
        names = sorted(name for name in rows if name in canonical
                       or name.startswith(prefix + ".") and rows[name].get("alias") in canonical)
        if not canonical.issubset(names):
            raise ValueError(f"Incomplete expert weights/scales: {prefix}")
        group = dict(id=f"expert_{index:04d}", module=prefix, tensors=names)
        for name in names:
            if name in membership:
                raise ValueError("Overlapping expert module groups")
            membership[name] = group["id"]
        groups.append(group)
    remainder = sorted(set(rows) - set(membership))
    if not remainder:
        raise ValueError("Missing V4 remainder state")
    for name in remainder:
        membership[name] = "remainder"
    for name, row in rows.items():
        if "alias" in row and membership.get(row["alias"]) != membership[name]:
            raise ValueError(f"Cross-group alias is not supported: {name}")
    return [dict(id="remainder", module=None, tensors=remainder)] + groups


def group_methods(methods, names):
    return {prefix: value for prefix, value in methods.items()
            if any(name.startswith(prefix + ".") for name in names)}


def validate_grouped_rank(root, rank, identity, verify_payload=False):
    validate_identity(identity)
    root = Path(root)
    directory = root / f"rank_{rank}"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Missing or linked grouped rank")
    path = checked_file(directory, "grouped_state.json")
    document = json.loads(path.read_text())
    if (document.get("format") != FORMAT or document.get("identity") != identity
            or document.get("rank") != rank):
        raise ValueError("Grouped rank identity/format mismatch")
    groups, methods = document.get("groups"), document.get("quant_methods")
    if not isinstance(groups, list) or len(groups) != 62 or not isinstance(methods, dict):
        raise ValueError("Incomplete grouped rank manifest")
    rows, device = {}, None
    for index, group in enumerate(groups):
        expected_id = "remainder" if index == 0 else f"expert_{index - 1:04d}"
        if group.get("id") != expected_id:
            raise ValueError("Group order/identifier mismatch")
        parent = root / "groups"
        group_root = parent / expected_id
        if any(p.is_symlink() or not p.is_dir() for p in (parent, group_root)):
            raise ValueError("Missing or linked payload group")
        raw, digest = validate_rank(group_root, rank, identity, verify_payload)
        names = group.get("tensors")
        if (not isinstance(names, list) or len(names) != len(set(names))
                or set(names) != set(raw["tensors"])
                or digest != group.get("manifest_sha256")
                or raw["quant_methods"] != group_methods(methods, names)):
            raise ValueError("Group tensor/method/hash coverage mismatch")
        if set(rows) & set(names):
            raise ValueError("A tensor appears in more than one group")
        rows.update(raw["tensors"])
        if device is None:
            device = raw["device_uuid"]
        if device != raw["device_uuid"]:
            raise ValueError("Group device provenance differs within rank")
    validate_rows(rows)
    plan = [{key: group[key] for key in ("id", "module", "tensors")} for group in groups]
    order = [group.get("module") for group in groups[1:]]
    if plan != partition_groups(rows, methods, order):
        raise ValueError("Group plan differs from native module/state coverage")
    return document, file_digest(path), device


def worker_receipts(results, identity):
    if not isinstance(results, list) or len(results) != TP_SIZE:
        raise ValueError("All eight export worker results are required")
    mapped = {}
    for result in results:
        rank = result.get("rank") if isinstance(result, dict) else None
        if (type(rank) is not int or not 0 <= rank < TP_SIZE or rank in mapped
                or result.get("identity") != identity or result.get("format") != FORMAT):
            raise ValueError("Duplicate or incompatible export worker result")
        mapped[rank] = result
    return mapped


def publish_grouped_complete(root, identity, results):
    failure = Path(root) / "CONVERSION_FAILED.json"
    if failure.exists() or failure.is_symlink():
        raise ValueError("Grouped conversion has a failure marker")
    workers = worker_receipts(results, identity)
    ranks, devices = {}, set()
    with ThreadPoolExecutor(max_workers=TP_SIZE) as executor:
        verified = list(executor.map(
            lambda rank: validate_grouped_rank(root, rank, identity, True), range(TP_SIZE)))
    for rank, (_, digest, device) in enumerate(verified):
        receipt = validate_export_done(root, rank, identity, digest)
        if workers[rank] != receipt:
            raise ValueError("Worker result differs from the saved export completion")
        if device in devices:
            raise ValueError("Grouped ranks reported duplicate GPU UUIDs")
        devices.add(device)
        ranks[str(rank)] = digest
    complete = dict(format=FORMAT, identity=identity, ranks=ranks,
                    workers=[workers[r] for r in range(TP_SIZE)])
    write_once(Path(root) / "COMPLETE.json", complete)
    return complete


def validate_grouped_complete(root, identity, verify_payload=False):
    failure = Path(root) / "CONVERSION_FAILED.json"
    if failure.exists() or failure.is_symlink():
        raise ValueError("Grouped conversion has a failure marker")
    complete = json.loads(checked_file(root, "COMPLETE.json").read_text())
    if (complete.get("format") != FORMAT or complete.get("identity") != identity
            or set(complete.get("ranks", {})) != {str(r) for r in range(TP_SIZE)}):
        raise ValueError("Incomplete or incompatible grouped Store")
    workers = worker_receipts(complete.get("workers"), identity)
    devices = set()
    for rank in range(TP_SIZE):
        _, digest, device = validate_grouped_rank(root, rank, identity, verify_payload)
        if workers[rank] != validate_export_done(root, rank, identity, digest):
            raise ValueError("Published export worker result changed")
        if digest != complete["ranks"][str(rank)] or device in devices:
            raise ValueError("Grouped rank changed or GPU UUID was repeated")
        devices.add(device)
    return complete


def complete_grouped_export_rank(root, rank, identity):
    """Called after the export loader's native postprocessing returns successfully."""
    _, digest, _ = validate_grouped_rank(root, rank, identity)
    receipt = dict(format=FORMAT, identity=identity, rank=rank, grouped_state_sha256=digest)
    write_once(Path(root) / f"rank_{rank}" / "export_done.json", receipt)
    return receipt


def validate_export_done(root, rank, identity, digest):
    path = checked_file(Path(root) / f"rank_{rank}", "export_done.json")
    expected = dict(format=FORMAT, identity=identity, rank=rank, grouped_state_sha256=digest)
    if json.loads(path.read_text()) != expected:
        raise ValueError("Export worker completion does not match grouped rank")
    return expected
