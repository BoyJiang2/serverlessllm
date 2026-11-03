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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--component", required=True,
                        help="Exact indexed projection prefix, without .weight_packed")
    parser.add_argument("--storage", type=pathlib.Path, required=True)
    parser.add_argument("--archive", required=True,
                        help="New relative archive name under --storage")
    parser.add_argument("--save-only", action="store_true")
    args = parser.parse_args()
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
            # Official blocking API also confirms and closes the server IPC mapping.
            load_attempted = True
            restored = load_dict(args.archive, {"": 0}, str(args.storage.resolve()))
            torch.cuda.synchronize()
            require(set(restored) == set(state), "restored tensor names differ")
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
                require(torch.equal(actual_cpu, expected),
                        "restored numeric mismatch: " + name)
                del actual, actual_cpu
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
