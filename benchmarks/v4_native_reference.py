"""One native SafeTensors reference, not a formal trial. Default is CPU-only plan."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import time

from sllm_store.v4_state import CONFIG_SHA256, REVISION, write_once
from v4_evidence import choice, require


MODEL = "/mnt/raid/models/DeepSeek-V4-Pro"
REQUESTS = (
    ("business", "request.json", "707d5d8b6efbfe257ff310466a03d6e19df5dce1b6b205270eb6a3d7e9374876",
     "7acd634c883de63235e830f40432454566484c243e2431da81b45d26ea15f354"),
    ("workload32", "workload32.json", "75a520893b656a8bc921ed810d910f0b5cf53b5c35f769c9e3c25e085ad88b9d",
     "1cb6d8d933c62759be85c424ca1c45f75c6315c9583008751897d28499c8bc8a"),
)


def inputs(repository):
    evidence = Path(repository) / "docs/evidence"
    encoded = json.loads((evidence / "v4-native-build-p4a/target-engine-cpu-smoke-compat.json").read_bytes())
    rows = {row["file"]: row for row in encoded["cpu_tokenizer_outputs"]}
    result = []
    for purpose, name, request_sha, ids_sha in REQUESTS:
        raw = (evidence / "v4-cpu-tokenizer-p3/inputs" / name).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == request_sha, "frozen request changed")
        ids = rows[name]["input_tokens"]
        require(hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest() == ids_sha,
                "frozen input token IDs changed")
        result.append(dict(purpose=purpose, request=json.loads(raw), input_tokens=ids,
                           request_sha256=request_sha, input_tokens_sha256=ids_sha))
    return result


def model_inventory(model):
    model = Path(model)
    require(not (model / "CONFIG_ONLY_NOT_RUNTIME_WEIGHTS").exists(), "config-only model forbidden")
    require(hashlib.sha256((model / "config.json").read_bytes()).hexdigest() == CONFIG_SHA256,
            "model config differs")
    index = json.loads((model / "model.safetensors.index.json").read_bytes())
    names = sorted(set(index["weight_map"].values()))
    require(len(names) == 64 and {p.name for p in model.glob("*.safetensors")} == set(names),
            "requires all 64 frozen SafeTensors shards")
    files = {}
    for name in names:
        require(Path(name).name == name, "shard path must be a basename")
        stat = (model / name).stat()
        require(stat.st_size > 0, "empty shard")
        files[name] = dict(bytes=stat.st_size, inode=stat.st_ino, mtime_ns=stat.st_mtime_ns)
    require(sum(row["bytes"] for row in files.values()) == 864721029744, "weight byte count differs")
    return dict(files=files, scope="Metadata presence only; reuse historical full SHA proof, not a new hash or writer lease")


def engine_config():
    return dict(model=MODEL, tokenizer=MODEL, revision=REVISION, tokenizer_revision=REVISION,
        tokenizer_mode="deepseek_v4", dtype="bfloat16", load_format="safetensors",
        kv_cache_dtype="fp8", attention_config=dict(use_fp4_indexer_cache=False),
        tensor_parallel_size=8, pipeline_parallel_size=1, data_parallel_size=1,
        enable_expert_parallel=False, speculative_config=None, enforce_eager=True,
        max_model_len=2048, max_num_seqs=1, max_num_batched_tokens=2048,
        enable_prefix_caching=False, cpu_offload_gb=0, gpu_memory_utilization=0.92,
        seed=20261105, distributed_executor_backend="mp")


def validate_reference(response, purpose):
    result = choice(response)
    if purpose == "business":
        require(result["message"]["content"].strip() == "4" and result["token_ids"][-1] == 1
                and result["finish_reason"] == "stop"
                and (result["stop_reason"] is None or type(result["stop_reason"]) is int
                     and result["stop_reason"] == 1), "business semantic/natural EOS failure")
    else:
        require(purpose == "workload32" and len(result["token_ids"]) == 32
                and result["finish_reason"] == "length", "fixed32 length failure")


def effective_config(engine):
    config = engine.llm_engine.vllm_config
    model, parallel = config.model_config, config.parallel_config
    result = dict(revision=model.revision, tokenizer_revision=model.tokenizer_revision,
        seed=model.seed, quantization=model.quantization, dtype=str(model.dtype),
        max_model_len=model.max_model_len, enforce_eager=model.enforce_eager,
        tensor_parallel_size=parallel.tensor_parallel_size,
        pipeline_parallel_size=parallel.pipeline_parallel_size, data_parallel_size=parallel.data_parallel_size,
        enable_expert_parallel=parallel.enable_expert_parallel,
        distributed_executor_backend=parallel.distributed_executor_backend,
        cache_dtype=config.cache_config.cache_dtype,
        gpu_memory_utilization=config.cache_config.gpu_memory_utilization,
        enable_prefix_caching=config.cache_config.enable_prefix_caching,
        use_fp4_indexer_cache=config.attention_config.use_fp4_indexer_cache,
        max_num_seqs=config.scheduler_config.max_num_seqs,
        max_num_batched_tokens=config.scheduler_config.max_num_batched_tokens,
        speculative_config=config.speculative_config)
    return result


def run_reference(config, requests, output, llm_factory, sampling_factory):
    """Factories allow CPU error-path checks; they do not qualify a native engine."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    engine, stage, responses = None, "constructing", {}
    try:
        write_once(output / "started.json", dict(started_ns=time.monotonic_ns(), engine_config=config,
                   requests=requests, runtime_qualified=False))
        engine = llm_factory(**config)
        effective = effective_config(engine)
        write_once(output / "effective-config.json", effective)
        expected = {key: config[key] for key in effective if key in config}
        expected.update(quantization="deepseek_v4_fp8", dtype="torch.bfloat16")
        require(all(effective[key] == value for key, value in expected.items())
                and effective["cache_dtype"] in ("fp8", "fp8_ds_mla")
                and effective["use_fp4_indexer_cache"] is False, "effective model configuration differs")
        for row in requests:
            purpose = stage = row["purpose"]
            started = time.monotonic_ns()
            outputs = engine.generate([dict(prompt_token_ids=row["input_tokens"])],
                                      sampling_factory(**row["request"]["sampling"]), use_tqdm=False)
            received = time.monotonic_ns()
            # Persist the complete returned object snapshot before projecting/validating it.
            snapshot = json.loads(json.dumps(outputs, default=lambda obj: vars(obj)
                                 if hasattr(obj, "__dict__") else repr(obj)))
            write_once(output / (purpose + ".raw.json"), dict(outputs=snapshot,
                       trigger_ns=started, received_ns=received))
            require(len(outputs) == 1, "expected one native output")
            response = dict(choices=[dict(message=dict(content=result.text),
                token_ids=list(result.token_ids), finish_reason=result.finish_reason,
                stop_reason=result.stop_reason) for result in outputs[0].outputs])
            write_once(output / (purpose + ".response.json"), response)
            validate_reference(response, purpose)
            responses[purpose] = response
        stage = "shutdown"
        engine.llm_engine.engine_core.shutdown(timeout=60)
        engine = None
        write_once(output / "candidate.json", dict(responses=responses, completed_ns=time.monotonic_ns(),
            status="native_output_candidate_pending_host_release_and_review",
            native_token_parity=None, runtime_qualified=False, physical_cleanup_verified=False))
    except BaseException as error:
        failure = dict(stage=stage, error=repr(error), failed_ns=time.monotonic_ns(),
                       fixed32_skipped=stage in ("constructing", "business"), runtime_qualified=False)
        if engine is not None and stage != "shutdown":
            try:
                engine.llm_engine.engine_core.shutdown(timeout=60)
            except BaseException as shutdown_error:
                failure["shutdown_error"] = repr(shutdown_error)
        write_once(output / "failure.json", failure)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Requires separately authorized GPU admission")
    parser.add_argument("--guard", type=Path, help="Read-only mounted external armed.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    requests = inputs(Path(__file__).resolve().parents[1])
    inventory, config = model_inventory(MODEL), engine_config()
    if not args.execute:
        print(json.dumps(dict(engine_config=config, requests=requests, inventory=inventory,
                              gpu_or_model_run=False), indent=2))
        return
    require(args.guard is not None and args.output is not None, "guard and new output directory required")
    armed = json.loads(args.guard.read_bytes())
    require(socket.gethostname() == armed["identity"]["container_id"][:12], "guard targets another container")
    require(armed["armed_ns"] < time.monotonic_ns() < armed["deadline_ns"], "guard deadline elapsed")
    require(os.environ.get("VLLM_PLUGINS") == "", "native reference requires plugins explicitly disabled")
    require(importlib.metadata.version("vllm") == "0.20.1", "target vLLM version differs")
    from vllm import LLM, SamplingParams
    run_reference(config, requests, args.output, LLM, SamplingParams)


if __name__ == "__main__":
    main()
