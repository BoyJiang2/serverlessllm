"""Plan (and later verify) a migration of the three V7 Store payloads to qjhs000.

Modes:
  --plan            run on the node that holds the payloads (qjhs3); writes
                    v7/migration-plan-qjhs000.json from the accepted protocol
  --check           run on the target node (qjhs0); compares the plan against a
                    target root and exits 2 when the space is insufficient

Nothing is copied or deleted here. Copy commands are printed for review only, so
that a migration can start the moment the target has room.
"""
import argparse
import hashlib
import json
import os
import socket
import time
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
PLAN = V7 / 'migration-plan-qjhs000.json'
SOURCE_PROTOCOL = V7 / 'protocol.json'
TARGET_NODE = 'qjhs0'
DEFAULT_TARGET_ROOT = '/raid/v7store'
HEADROOM = 1.03


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan(target_root):
    protocol = json.loads(SOURCE_PROTOCOL.read_bytes())
    models = {}
    for model in ('kimi', 'glm', 'v4'):
        paths = protocol['model_paths'][model]
        absolute = [str(Path('/data') / path) for path in paths]
        # Kimi/GLM keep one rank dir per path under a single root; the V4 payload
        # is grouped per expert, so the common ancestor is the copy unit.
        source_root = os.path.commonpath(absolute)
        if source_root.rstrip('/').endswith(tuple(f'rank_{i}' for i in range(8))):
            source_root = str(Path(source_root).parent)
        total = sum(protocol['path_sizes'][path] for path in paths)
        present = sum(1 for path in absolute if Path(path).is_dir())
        models[model] = dict(
            source_root=source_root,
            payload_paths=len(paths),
            payload_bytes=total,
            rank_dirs_present=present,
            all_present=present == len(paths),
            filesystem=protocol['storage'].get(model),
            payload_metadata=protocol['payload_metadata'].get(model),
            copy_from=source_root,
            copy_to=f'{target_root}/{model}',
        )
    required = sum(row['payload_bytes'] for row in models.values())
    document = dict(
        generated_wall_ns=time.time_ns(),
        generated_on=socket.gethostname(),
        source_node='qjhs3',
        source_protocol=str(SOURCE_PROTOCOL),
        source_protocol_sha256=sha(SOURCE_PROTOCOL),
        target_node=TARGET_NODE,
        target_root=target_root,
        models=models,
        required_payload_bytes=required,
        required_with_headroom_bytes=int(required * HEADROOM),
        controller_change=(
            'v7_store_sequence.py now reads store_root from the protocol '
            "(default '/data'); the migrated protocol sets store_root to the "
            'target root and rewrites every model_paths key relative to it.'
        ),
        refreeze_steps=[
            'copy the three payload roots to <target_root>/<model> and verify sizes',
            'new protocol: store_root=<target root>, model_paths relative to it, '
            'path_sizes recomputed from the copied tensor.data_* files',
            'new gpu_uuids from the target nvidia-smi; new admission with the target resource id',
            'payload_metadata roots rewritten; per-rank metadata digests re-verified',
            'runtime_source_hashes recomputed for the working tree',
            'fresh 1 TiB / 32 MiB Store; sequence Kimi -> GLM -> V4 -> Kimi -> Kimi',
        ],
        copy_commands=[
            ['rsync', '-a', '--info=progress2', row['copy_from'] + '/', row['copy_to'] + '/']
            for row in models.values()
        ],
    )
    PLAN.write_text(json.dumps(document, indent=2) + '\n')
    return document


def check(target_root):
    document = json.loads(PLAN.read_bytes())
    root = Path(target_root or document['target_root'])
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    stats = os.statvfs(probe)
    free = stats.f_bavail * stats.f_frsize
    total = stats.f_blocks * stats.f_frsize
    required = document['required_with_headroom_bytes']
    per_model = {name: row['payload_bytes'] for name, row in document['models'].items()}
    print(json.dumps(dict(
        target_node=socket.gethostname(),
        checked_path=str(root),
        existing_ancestor=str(probe),
        filesystem_total_bytes=total,
        filesystem_free_bytes=free,
        required_bytes=required,
        required_per_model=per_model,
        shortfall_bytes=max(0, required - free),
        fits=free >= required,
        fits_single_smallest_model=free >= min(per_model.values()),
    ), indent=2))
    return 0 if free >= required else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--target-root', default=None)
    args = parser.parse_args()
    if args.plan:
        document = plan(args.target_root or DEFAULT_TARGET_ROOT)
        print(json.dumps(dict(
            plan=str(PLAN),
            source_protocol_sha256=document['source_protocol_sha256'],
            required_bytes=document['required_payload_bytes'],
            required_with_headroom_bytes=document['required_with_headroom_bytes'],
            per_model={name: row['payload_bytes'] for name, row in document['models'].items()},
            all_present=all(row['all_present'] for row in document['models'].values()),
        ), indent=2))
        return 0
    if args.check:
        return check(args.target_root)
    parser.error('choose --plan or --check')


if __name__ == '__main__':
    raise SystemExit(main())
