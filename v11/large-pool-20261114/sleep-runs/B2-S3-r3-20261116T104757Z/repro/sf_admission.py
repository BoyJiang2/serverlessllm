"""Run the existing file-specific cold-page admission for a checkpoint."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("code", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("deadline_ns", type=int)
    parser.add_argument("run_id")
    args = parser.parse_args()
    sys.path[:0] = [str(args.code / "benchmarks"), str(args.code / "sllm_store")]
    from v4_cache_state import run_cache, source_stat

    index = json.loads((args.checkpoint / "model.safetensors.index.json").read_bytes())
    shards = set(index["weight_map"].values())
    if not shards or any(Path(shard).name != shard for shard in shards):
        raise ValueError("checkpoint index must list local shard filenames")
    paths = [args.checkpoint / shard for shard in sorted(shards)]
    files = {str(path): source_stat(path) for path in paths}
    run_cache(files, "safetensors_inputs", "cold", args.output, args.deadline_ns, args.run_id)


if __name__ == "__main__":
    main()
