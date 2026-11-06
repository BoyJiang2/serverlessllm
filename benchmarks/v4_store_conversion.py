"""Guarded full Store conversion entry. Default is a metadata-only plan."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import time
import traceback

from sllm_store.v4_convert import convert_grouped_store, export_engine_config
from sllm_store.v4_state import file_digest, write_once


def run_conversion(model, store, identity, guard, output):
    armed = json.loads(Path(guard).read_bytes())
    if (armed.get("purpose") != "store-conversion"
            or socket.gethostname() != armed["identity"]["container_id"][:12]
            or not armed["armed_ns"] < time.monotonic_ns() < armed["deadline_ns"]):
        raise ValueError("current container requires an armed Store conversion guard")
    if os.environ.get("VLLM_PLUGINS") != "sllm_v4_store":
        raise ValueError("Store conversion requires the explicit installed loader plugin")
    if importlib.metadata.version("vllm") != "0.20.1":
        raise ValueError("target vLLM version differs")
    config = export_engine_config(model, store, identity)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_once(output / "started.json", dict(guard=armed, engine_config=config,
               trigger_ns=time.monotonic_ns(), runtime_qualified=False))
    try:
        convert_grouped_store(model, store, identity)
        complete = Path(store) / "COMPLETE.json"
        write_once(output / "candidate.json", dict(identity=identity,
            complete_sha256=file_digest(complete), completed_ns=time.monotonic_ns(),
            status="store_conversion_candidate_pending_host_release_and_review",
            runtime_qualified=False, physical_cleanup_verified=False))
    except BaseException as error:
        write_once(output / "failure.json", dict(error=repr(error),
            traceback=traceback.format_exc(), failed_ns=time.monotonic_ns(),
            runtime_qualified=False, physical_cleanup_verified=False))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--guard", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    identity = json.loads(args.identity.read_bytes())
    if not args.execute:
        print(json.dumps(export_engine_config(args.model, args.store, identity), indent=2))
        return
    if args.guard is None or args.output is None:
        parser.error("execution requires --guard and --output")
    run_conversion(args.model, args.store, identity, args.guard, args.output)


if __name__ == "__main__":
    main()
