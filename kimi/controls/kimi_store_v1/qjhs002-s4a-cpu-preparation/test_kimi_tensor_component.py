"""Draft S4a: one real packed component through official save_dict/load_dict.

Not a converter, model test, or CPU readback. Run only inside the separately
approved single-GPU window/guard. The caller owns service startup/shutdown and
the >=60s release observation. Use an isolated official server at 127.0.0.1:8073,
storage_path matching --storage, pool=64MiB, chunk=1MiB, num_thread=1.
No test/service is started by importing this file. Evidence/archive are retained.
"""
import argparse
import gc
import hashlib
import json
import pathlib
import struct
import re
import sys

import torch
from safetensors import safe_open
from sllm_store.client import SllmStoreClient
from sllm_store.torch import load_dict, save_dict


LIMIT = 16 << 20


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def raw_bytes(tensor):
    # Safetensors is dense; never cast/dequantize packed values or scales.
    return tensor.contiguous().view(torch.uint8).numpy().tobytes()


GOLD_INDEX = {'language_model.model.layers.1.mlp.experts.0.down_proj.weight_packed': [0,
                                                                         7340032,
                                                                         [7168, 256],
                                                                         [256, 1],
                                                                         'torch.int32'],
 'language_model.model.layers.1.mlp.experts.0.down_proj.weight_scale': [7340032,
                                                                        917504,
                                                                        [7168, 64],
                                                                        [64, 1],
                                                                        'torch.bfloat16'],
 'language_model.model.layers.1.mlp.experts.0.down_proj.weight_shape': [8257536,
                                                                        8,
                                                                        [2],
                                                                        [1],
                                                                        'torch.int32']}
GOLD_RAW_SHA = {'language_model.model.layers.1.mlp.experts.0.down_proj.weight_packed': 'c440443a46091667c132cd93131fd3dec4f420309ed134d418d8cf4247e31963',
 'language_model.model.layers.1.mlp.experts.0.down_proj.weight_scale': '78053b2804dac91c8fa00756ccb2c7a3e96c61a0ea1c88ca81b246810d36e86e',
 'language_model.model.layers.1.mlp.experts.0.down_proj.weight_shape': '68d89fd501bef3663f80787cb8ad23be91d56afa7db1e1f93cf6e07fe4f20433'}
GOLD_FILE_SHA = {'tensor_index.json': '5fbcb6f759d0b473f6ed41ef21a825ca020c44d51c489e8af813c75e3a5b8993',
 'tensor.data_0': '4920e225718ab1a21e9dd771438c7f1c3c86277b76adfc5c86f91954569a80d8'}


def read_existing_archive(args):
    require(args.storage.is_absolute(), "storage root must be absolute")
    root = args.storage
    relative = pathlib.Path(args.archive)
    require(not relative.is_absolute() and relative.parts
            and all(part not in (".", "..") for part in relative.parts),
            "archive must be a relative path without traversal")
    destination = root / relative
    for path in (destination, *destination.parents):
        require(not path.is_symlink(), "symlink archive/root path")
    require(root.is_dir() and destination.is_dir()
            and destination.resolve().is_relative_to(root.resolve()),
            "archive is outside storage root")
    files = {path.name: path for path in destination.iterdir()}
    require(set(files) == set(GOLD_FILE_SHA), "archive must contain exactly two files")
    require(all(path.is_file() and not path.is_symlink() for path in files.values()),
            "archive files must be regular and not symlinks")
    require(sum(path.stat().st_size for path in files.values()) <= LIMIT,
            "archive including index exceeds 16MiB")
    require(args.receipt.is_file() and not args.receipt.is_symlink()
            and args.receipt.stat().st_size <= LIMIT, "invalid receipt file")
    receipt = json.loads(args.receipt.read_text())
    require(receipt["tensor_index"] == GOLD_INDEX
            and receipt["raw_sha256"] == GOLD_RAW_SHA
            and receipt["tensor_index_sha256"] == GOLD_FILE_SHA["tensor_index.json"]
            and receipt["tensor_data_sha256"] == GOLD_FILE_SHA["tensor.data_0"]
            and receipt["archive_bytes"] == 8261632
            and receipt["payload_bytes"] == 8257544
            and receipt["total_archive_bytes"] == 8261992,
            "receipt disagrees with reviewed gold")
    contents = {}
    for name, path in files.items():
        with path.open("rb") as stream:
            data = stream.read(LIMIT + 1)
        require(len(data) <= LIMIT, "archive file exceeds read bound")
        require(hashlib.sha256(data).hexdigest() == GOLD_FILE_SHA[name],
                "archive file digest differs: " + name)
        contents[name] = data
    index = json.loads(contents["tensor_index.json"])
    require(index == GOLD_INDEX and len(contents["tensor.data_0"]) == 8261632,
            "archive index/extent differs")
    dtypes = {"torch.int32": torch.int32, "torch.bfloat16": torch.bfloat16}
    state = {}
    for name, (offset, size, shape, stride, dtype) in index.items():
        data = contents["tensor.data_0"][offset:offset + size]
        require(len(data) == size and hashlib.sha256(data).hexdigest() == GOLD_RAW_SHA[name],
                "archive tensor digest differs: " + name)
        state[name] = torch.frombuffer(bytearray(data), dtype=dtypes[dtype]).reshape(shape)
        require(list(state[name].stride()) == stride, "archive tensor stride differs")
    return state, {"stage": "EXISTING_ARCHIVE_VALIDATED", "archive_bytes": 8261632,
                   "payload_bytes": 8257544, "raw_sha256": GOLD_RAW_SHA,
                   "tensor_index": index, "total_archive_bytes": 8261992}


def save_component(args):
    relative = pathlib.Path(args.archive)
    require(not relative.is_absolute() and relative.parts
            and all(part not in (".", "..") for part in relative.parts),
            "archive must be a new relative path")
    destination = args.storage.resolve() / relative
    require(not destination.exists(), "archive exists; preserve prior evidence")
    names = [args.component + suffix for suffix in
             (".weight_packed", ".weight_scale", ".weight_shape")]
    index = json.loads((args.checkpoint / "model.safetensors.index.json").read_text())
    weight_map = index["weight_map"]
    config_bytes = (args.checkpoint / "config.json").read_bytes()
    quant = json.loads(config_bytes)["text_config"]["quantization_config"]
    weights = quant["config_groups"]["group_0"]["weights"]
    require(quant["quant_method"] == "compressed-tensors"
            and quant["format"] == "pack-quantized"
            and quant["quantization_status"] == "compressed"
            and weights["type"] == "int" and weights["num_bits"] == 4
            and weights["group_size"] == 32 and weights["symmetric"] is True,
            "checkpoint quantization provenance differs")
    require(not any(re.match(pattern[3:], args.component)
                    if pattern.startswith("re:") else pattern == args.component
                    for pattern in quant["ignore"]), "component is excluded from quantization")
    require(all(name in weight_map for name in names),
            "component must have all three exact indexed tensors")
    # Inspect headers and enforce the payload cap BEFORE reading tensor payload.
    metadata = {}
    for shard in sorted({weight_map[name] for name in names}):
        shard_path = args.checkpoint / shard
        require(shard_path.resolve().parent == args.checkpoint.resolve(),
                "unexpected shard path")
        with shard_path.open("rb") as stream:
            length = struct.unpack("<Q", stream.read(8))[0]
            require(length <= 16 << 20, "unreasonably large safetensors header")
            header = json.loads(stream.read(length))
        for name in names:
            if weight_map[name] == shard:
                metadata[name] = header[name]
    sizes = {name: entry["data_offsets"][1] - entry["data_offsets"][0]
             for name, entry in metadata.items()}
    require(0 < sum(sizes.values()) <= LIMIT, "component exceeds 16MiB cap")
    expected_headers = [("I32", [7168, 256]), ("BF16", [7168, 64]), ("I32", [2])]
    require(args.component == "language_model.model.layers.1.mlp.experts.0.down_proj",
            "only the independently reviewed component is authorized")
    require(all((metadata[name]["dtype"], metadata[name]["shape"]) == expected
                for name, expected in zip(names, expected_headers)),
            "component header differs from reviewed bounds")
    state = {}
    for shard in sorted({weight_map[name] for name in names}):
        with safe_open(args.checkpoint / shard, framework="pt", device="cpu") as handle:
            for name in names:
                if weight_map[name] == shard:
                    source = handle.get_tensor(name)
                    clone = source.clone(memory_format=torch.preserve_format)
                    require(clone.dtype == source.dtype
                            and clone.stride() == source.stride()
                            and raw_bytes(clone) == raw_bytes(source),
                            "CPU clone changed source metadata or raw bits")
                    require(clone.untyped_storage().nbytes() == sizes[name],
                            "unexpected tensor storage extent")
                    state[name] = clone
    require(state[names[0]].dtype == torch.int32,
            "expected actual integer-packed INT4 storage, not dense/dequantized values")
    require(state[names[2]].tolist() == [7168, 2048],
            "logical weight shape disagrees with packed/scale dimensions")
    save_dict(state, str(destination))
    saved_index = json.loads((destination / "tensor_index.json").read_text())
    require(set(saved_index) == set(names), "archive tensor names differ")
    cursor = 0
    with (destination / "tensor.data_0").open("rb") as stream:
        for name, tensor in state.items():
            expected = [cursor, sizes[name], list(tensor.shape),
                        list(tensor.stride()), str(tensor.dtype)]
            require(saved_index[name] == expected, "archive index mismatch: " + name)
            stream.seek(cursor)
            require(stream.read(sizes[name]) == raw_bytes(tensor),
                    "archive raw bits mismatch: " + name)
            cursor += (sizes[name] + 7) // 8 * 8
    archive_bytes = (cursor + 4095) // 4096 * 4096
    require({p.name for p in destination.iterdir()}
            == {"tensor_index.json", "tensor.data_0"}, "unexpected archive files")
    require((destination / "tensor.data_0").stat().st_size == archive_bytes
            and archive_bytes <= LIMIT, "incorrect archive extent or cap")
    total_archive_bytes = sum(p.stat().st_size for p in destination.iterdir())
    require(total_archive_bytes <= LIMIT, "archive including index exceeds 16MiB")
    evidence = {"stage": "SAVE_ONLY", "payload_bytes": sum(sizes.values()),
                "archive_bytes": archive_bytes, "total_archive_bytes": total_archive_bytes,
                "tensor_index": saved_index,
                "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "quantization": quant, "source_headers": metadata,
                "logical_weight_shape": state[names[2]].tolist(),
                "raw_sha256": {name: hashlib.sha256(raw_bytes(tensor)).hexdigest()
                               for name, tensor in state.items()}}
    return state, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=pathlib.Path, required=False)
    parser.add_argument("--component", required=False,
                        help="Exact indexed projection prefix, without .weight_packed")
    parser.add_argument("--storage", type=pathlib.Path, required=True)
    parser.add_argument("--archive", required=True,
                        help="New relative archive name under --storage")
    parser.add_argument("--save-only", action="store_true")
    parser.add_argument("--existing-archive", action="store_true")
    parser.add_argument("--receipt", type=pathlib.Path)
    args = parser.parse_args()
    if args.existing_archive:
        require(args.receipt is not None and not args.save_only,
                "existing archive requires receipt and readback")
        state, evidence = read_existing_archive(args)
    else:
        require(args.checkpoint is not None and args.component is not None,
                "save requires checkpoint and component")
        state, evidence = save_component(args)
    archive_bytes = evidence["archive_bytes"]
    names = list(GOLD_INDEX)
    if not args.save_only:
        require(torch.cuda.is_available() and torch.cuda.device_count() == 1,
                "official load_dict requires exactly one approved visible GPU")
        client = SllmStoreClient("127.0.0.1:8073")
        restored = {}
        actual = None
        load_attempted = False
        try:
            require(client.get_server_config()
                    == {"chunk_size": 1 << 20, "mem_pool_size": 64 << 20},
                    "server pool/chunk do not match approved bounds")
            require(client.register_model(args.archive) == archive_bytes,
                    "official registration failed or size differs")
            torch.cuda.synchronize()
            free_before, total_memory = torch.cuda.mem_get_info(0)
            # Official blocking API also confirms and closes the server IPC mapping.
            load_attempted = True
            restored = load_dict(args.archive, {"": 0}, str(args.storage.resolve()))
            torch.cuda.synchronize()
            require(set(restored) == set(state), "restored tensor names differ")
            evidence["restored_raw_sha256"] = {}
            for name, expected in state.items():
                actual = restored[name]
                require(actual.device.type == "cuda" and actual.device.index == 0
                        and actual.dtype == expected.dtype
                        and actual.shape == expected.shape
                        and actual.stride() == expected.stride(),
                        "restored metadata mismatch: " + name)
                actual_cpu = actual.cpu()
                require(raw_bytes(actual_cpu) == raw_bytes(expected),
                        "restored raw bits mismatch: " + name)
                digest = hashlib.sha256(raw_bytes(actual_cpu)).hexdigest()
                require(digest == GOLD_RAW_SHA[name], "restored gold hash mismatch: " + name)
                evidence["restored_raw_sha256"][name] = digest
                require(torch.equal(actual_cpu, expected),
                        "restored numeric mismatch: " + name)
                del actual, actual_cpu
            # Keep only the packed alias: all tensors share one native allocation.
            actual = restored[names[0]]
            restored.clear()
            gc.collect()
            torch.cuda.synchronize()
            free_alias, _ = torch.cuda.mem_get_info(0)
            require(hashlib.sha256(raw_bytes(actual.cpu())).hexdigest() == GOLD_RAW_SHA[names[0]],
                    "packed alias invalid after dropping other tensors")
            require(free_before - free_alias >= evidence["payload_bytes"],
                    "driver does not show allocation retained by final alias")
            actual = None
            gc.collect()
            torch.cuda.synchronize()
            free_after, _ = torch.cuda.mem_get_info(0)
            evidence["driver_free_bytes"] = {"before_load": free_before,
                "last_alias_held": free_alias, "last_alias_dropped": free_after,
                "total": total_memory}
            print(json.dumps({"driver_free_bytes": evidence["driver_free_bytes"]}),
                  flush=True)
            require(free_after - free_alias >= evidence["payload_bytes"],
                    "driver does not show final alias allocation release")
            evidence["stage"] = "OFFICIAL_COMPONENT_ROUNDTRIP"
        finally:
            primary = sys.exc_info()[1]
            cleanup_errors = []
            try:
                actual = None
                restored.clear()
                gc.collect()
                torch.cuda.synchronize()
            except Exception as error:
                cleanup_errors.append("GPU cleanup: " + repr(error))
            finally:
                try:
                    if load_attempted:
                        require(client.unload_from_cpu(args.archive), "official CPU unload failed")
                except Exception as error:
                    cleanup_errors.append("CPU unload: " + repr(error))
                finally:
                    try:
                        client.channel.close()
                    except Exception as error:
                        cleanup_errors.append("channel close: " + repr(error))
            if cleanup_errors:
                print(json.dumps({"cleanup_errors": cleanup_errors}), file=sys.stderr, flush=True)
                if primary is None:
                    raise RuntimeError("; ".join(cleanup_errors))
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
