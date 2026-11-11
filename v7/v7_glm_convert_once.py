"""Build a fresh GLM TP8 raw Store payload on pinned vLLM 0.20.1."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import traceback

from sllm_store.vllm_glm20_loader import (
    EXPORT_FORMAT,
    TARGET_RAW_FORMAT,
    TARGET_VERSIONS,
    validate_target_identity,
)


METADATA_FILES = (
    "chat_template.jinja",
    "config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "tokenizer_config.json",
    "tokenizer.json",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def verify_store(store, identity):
    ranks = []
    for rank in range(8):
        directory = store / f"rank_{rank}"
        manifest = json.loads((directory / "raw_state.json").read_text())
        validate_target_identity(manifest["identity"])
        assert manifest["identity"] == identity
        assert manifest["format"] == TARGET_RAW_FORMAT
        assert manifest["rank"] == rank
        assert manifest["state"] == "before_quantization_postprocessing"
        files = {
            path.name: dict(bytes=path.stat().st_size, sha256=digest(path))
            for path in directory.iterdir()
        }
        ranks.append(dict(
            rank=rank,
            tensors=len(manifest["tensors"]),
            payload_bytes=sum(
                path.stat().st_size for path in directory.glob("tensor.data_*")
            ),
            files=files,
        ))
    assert len({row["tensors"] for row in ranks}) == 1
    return ranks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    model, store = args.model.resolve(), args.store.resolve()
    identity = json.loads(args.identity.read_bytes())
    validate_target_identity(identity)
    assert digest(model / "config.json") == identity["config_sha256"]
    assert args.output.is_dir()
    store.mkdir(parents=True, exist_ok=False)
    for name in METADATA_FILES:
        source = model / name
        if source.is_file():
            shutil.copy2(source, store / name)
    config = dict(
        model=str(model),
        tokenizer=str(model),
        dtype="bfloat16",
        revision=None,
        tokenizer_revision=None,
        load_format=EXPORT_FORMAT,
        model_loader_extra_config=dict(
            store_path=str(store),
            identity=identity,
        ),
        tensor_parallel_size=8,
        distributed_executor_backend="mp",
        max_model_len=2048,
        max_num_seqs=1,
        max_num_batched_tokens=2048,
        gpu_memory_utilization=0.9,
        enable_prefix_caching=False,
        moe_backend="deep_gemm",
        num_gpu_blocks_override=256,
        enforce_eager=True,
        seed=20261024,
        speculative_config=None,
    )
    write_once(args.output / "started.json", dict(
        config=config, identity=identity, trigger_ns=time.monotonic_ns(),
    ))
    engine = None
    try:
        from vllm import LLM
        engine = LLM(**config)
        assert (TARGET_VERSIONS[0], TARGET_VERSIONS[1]) == (
            __import__("importlib.metadata").metadata.version("vllm"),
            __import__("torch").__version__,
        )
        engine.llm_engine.engine_core.shutdown(timeout=60)
        engine = None
        ranks = verify_store(store, identity)
        complete = dict(
            format=TARGET_RAW_FORMAT,
            identity=identity,
            ranks=ranks,
            completed_ns=time.monotonic_ns(),
            source_config_sha256=identity["config_sha256"],
        )
        write_once(store / "COMPLETE.json", complete)
        write_once(args.output / "candidate.json", dict(
            complete_sha256=digest(store / "COMPLETE.json"),
            completed_ns=time.monotonic_ns(),
            runtime_qualified=False,
            physical_cleanup_verified=False,
        ))
    except BaseException as error:
        failure = dict(
            error=repr(error), traceback=traceback.format_exc(),
            failed_ns=time.monotonic_ns(),
        )
        write_once(args.output / "failure.json", failure)
        if engine is not None:
            try:
                engine.llm_engine.engine_core.shutdown(timeout=30)
            except BaseException as shutdown_error:
                write_once(args.output / "shutdown-failure.json",
                           dict(error=repr(shutdown_error)))
        raise


if __name__ == "__main__":
    main()
