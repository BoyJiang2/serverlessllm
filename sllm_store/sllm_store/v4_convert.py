"""Offline grouped conversion coordinator. Imports vLLM only for execution."""
import argparse
import json
import os
from pathlib import Path
import time

from sllm_store.v4_export_worker import RPC_METHOD, WORKER_EXTENSION
from sllm_store.v4_grouped_state import publish_grouped_complete, worker_receipts
from sllm_store.v4_state import CONFIG_SHA256, REVISION, file_digest, validate_identity, write_once

RPC_TIMEOUT_SECONDS = 1200
SHUTDOWN_TIMEOUT_SECONDS = 60
_EXPORT_CONTEXTS = {}


def export_engine_config(model_path, store_path, identity):
    validate_identity(identity)
    if os.environ.get("VLLM_ALLOW_INSECURE_SERIALIZATION", "0") not in ("", "0"):
        raise ValueError("Grouped conversion requires secure serialization settings")
    identity = json.loads(json.dumps(identity, allow_nan=False))
    model, root = Path(model_path), Path(store_path)
    if not model.is_absolute() or not root.is_absolute():
        raise ValueError("Model and Store paths must be absolute")
    model, root = model.resolve(), root.resolve()
    root.relative_to(Path(os.environ["STORAGE_PATH"]).resolve())
    if file_digest(model / "config.json") != CONFIG_SHA256:
        raise ValueError("V4 model config differs from the frozen source")
    return dict(
        model=str(model), tokenizer=str(model), revision=REVISION,
        tokenizer_revision=REVISION, tokenizer_mode="deepseek_v4",
        dtype="bfloat16", quantization="fp8", kv_cache_dtype="fp8",
        attention_config=dict(use_fp4_indexer_cache=False), tensor_parallel_size=8,
        pipeline_parallel_size=1, data_parallel_size=1, enable_expert_parallel=False,
        speculative_config=None, enforce_eager=True, max_model_len=2048,
        max_num_seqs=1, max_num_batched_tokens=2048, enable_prefix_caching=False,
        cpu_offload_gb=0, gpu_memory_utilization=0.92, seed=20261105,
        distributed_executor_backend="mp", worker_extension_cls=WORKER_EXTENSION,
        load_format="sllm_v4_grouped_export",
        model_loader_extra_config=dict(store_path=str(root), identity=identity),
    )


def collect_export_receipts(engine, identity):
    validate_identity(identity)
    results = engine.collective_rpc(RPC_METHOD, timeout=RPC_TIMEOUT_SECONDS,
                                    args=(identity,))
    worker_receipts(results, identity)
    return results


def export_contexts():
    return tuple(dict(path=path, stage=ctx["stage"], error=ctx["error"],
                      shutdown_error=ctx.get("shutdown_error"),
                      record_error=ctx.get("record_error"),
                      engine_retained=ctx["engine"] is not None)
                 for path, ctx in _EXPORT_CONTEXTS.items())


def convert_grouped_store(model_path, store_path, identity):
    config = export_engine_config(model_path, store_path, identity)
    identity = config["model_loader_extra_config"]["identity"]
    root = Path(config["model_loader_extra_config"]["store_path"])
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=False)  # Claim a fresh conversion; never resume partial data.
    ctx = dict(engine=None, stage="starting", error=None)
    _EXPORT_CONTEXTS[str(root)] = ctx
    shutdown_attempted = False
    try:
        write_once(root / "CONVERSION_STARTED.json", dict(identity=identity,
            engine_config=config, started_unix_ns=time.time_ns(),
            rpc_timeout_seconds=RPC_TIMEOUT_SECONDS,
            shutdown_timeout_seconds=SHUTDOWN_TIMEOUT_SECONDS))
        # Installed general plugin registers the grouped loader in parent/workers.
        from vllm import LLM

        ctx["stage"] = "constructing"
        engine = LLM(**config)
        ctx["engine"] = engine
        ctx["stage"] = "collecting"
        results = collect_export_receipts(engine, identity)
        write_once(root / "EXPORT_RECEIPTS.json", dict(identity=identity,
            workers=results, collected_unix_ns=time.time_ns()))
        ctx["stage"] = "shutting_down"
        shutdown_attempted = True
        engine.llm_engine.engine_core.shutdown(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        ctx["engine"] = None
        ctx["stage"] = "recording_shutdown"
        write_once(root / "ENGINE_SHUTDOWN.json", dict(identity=identity,
            completed_unix_ns=time.time_ns()))
        ctx["stage"] = "publishing"
        complete = publish_grouped_complete(root, identity, results)
    except BaseException as error:
        ctx["error"] = type(error).__name__
        if ctx["engine"] is not None and not shutdown_attempted:
            try:
                ctx["engine"].llm_engine.engine_core.shutdown(timeout=SHUTDOWN_TIMEOUT_SECONDS)
                ctx["engine"] = None
            except BaseException as shutdown_error:
                ctx["shutdown_error"] = type(shutdown_error).__name__
        elif ctx["stage"] == "shutting_down":
            ctx["shutdown_error"] = type(error).__name__
        failure = dict(identity=identity, failed_unix_ns=time.time_ns(),
            stage=ctx["stage"], error=ctx["error"],
            shutdown_error=ctx.get("shutdown_error"),
            engine_retained=ctx["engine"] is not None)
        try:
            write_once(root / "CONVERSION_FAILED.json", failure)
        except BaseException as record_error:
            ctx["record_error"] = type(record_error).__name__
        raise
    _EXPORT_CONTEXTS.pop(str(root))
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--identity", required=True, help="Frozen raw Store identity JSON")
    parser.add_argument("--plan", action="store_true", help="Print config without importing vLLM")
    args = parser.parse_args()
    identity = json.loads(Path(args.identity).read_text())
    if args.plan:
        print(json.dumps(export_engine_config(args.model, args.store, identity), indent=2))
    else:
        print(json.dumps(convert_grouped_store(args.model, args.store, identity)))


if __name__ == "__main__":
    main()
