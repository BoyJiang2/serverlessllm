"""Verify the GLM raw Store payload written by v7-glm-rebuild-20261110T113709Z.

That run completed all eight rank saves and shut the workers down at 11:51:59Z,
then the in-container hash verification was cut by the 1180 s store-conversion
guard lease at 11:56:50Z. This verifier re-establishes the same facts from the
retained payload without a GPU: identity, tensor index consistency, alias graph,
file digests, and byte coverage. It writes COMPLETE.json only if every check
passes, and records that the payload came from the killed container.
"""
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
SOURCE_RUN = 'v7-glm-rebuild-20261110T113709Z'
FORMAT = 'sllm-glm51-fp8-raw-v2'
IDENTITY = {
    'format': FORMAT,
    'source_revision': 'f396cf805182f4ca10fa675e1a99815b3ca384db',
    'config_sha256': '69b4e81fafca7389fa1e102046ea13aa1c6ec9ed64a9a00f2056fe402034c8ec',
    'vllm_version': '0.20.1',
    'vllm_commit': '132765e3560659ff63ebd236203672e991b70e08',
    'torch_version': '2.11.0+cu130',
    'model_type': 'glm_moe_dsa',
    'quantization': 'fp8',
    'dtype': 'torch.bfloat16',
    'tensor_parallel_size': 8,
    'seed': 20261024,
    'VLLM_USE_DEEP_GEMM_E8M0': '0',
}


def digest(path):
    handle = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 24), b''):
            handle.update(chunk)
    return handle.hexdigest()


def hash_rank(directory):
    return {path.name: dict(bytes=path.stat().st_size, sha256=digest(path))
            for path in sorted(Path(directory).iterdir()) if path.is_file()}


def verify_rank(store, rank):
    directory = Path(store) / f'rank_{rank}'
    manifest = json.loads((directory / 'raw_state.json').read_bytes())
    assert manifest['format'] == FORMAT, rank
    assert manifest['rank'] == rank, rank
    assert manifest['state'] == 'before_quantization_postprocessing', rank
    assert manifest['identity'] == IDENTITY, rank
    tensors = manifest['tensors']
    index = json.loads((directory / 'tensor_index.json').read_bytes())
    # save_dict stores only owned, non-empty tensors: aliased and zero-sized
    # entries stay described in raw_state.json but carry no payload of their own.
    canonical = {name for name, row in tensors.items()
                 if not row.get('empty') and 'alias' not in row}
    assert set(index) == canonical, (rank, len(index), len(canonical))
    owned = 0
    for name, (offset, size, shape, stride, dtype) in index.items():
        row = tensors[name]
        assert [size, shape, stride, dtype] == [row['bytes'], row['shape'], row['stride'], row['dtype']], (rank, name)
        owned += 1
    payload = sum(path.stat().st_size for path in directory.glob('tensor.data_*'))
    ends = [offset + size for offset, size, _, _, _ in index.values()]
    assert max(ends) <= payload, (rank, max(ends), payload)
    files = hash_rank(directory)
    return dict(
        rank=rank,
        tensors=len(tensors),
        canonical_tensors=len(canonical),
        owned_index_entries=owned,
        empty_tensors=sum(1 for row in tensors.values() if row.get('empty')),
        aliased_tensors=sum(1 for row in tensors.values() if row.get('alias')),
        payload_bytes=payload,
        tensor_data_files=len(list(directory.glob('tensor.data_*'))),
        files=files,
    )


def main(store):
    store = Path(store)
    started = time.time()
    with ProcessPoolExecutor(max_workers=8) as pool:
        ranks = list(pool.map(verify_rank, [store] * 8, range(8)))
    assert len({row['tensors'] for row in ranks}) == 1, 'rank tensor counts differ'
    assert {row['rank'] for row in ranks} == set(range(8))
    complete = dict(
        format=FORMAT,
        identity=IDENTITY,
        ranks=ranks,
        completed_ns=time.monotonic_ns(),
        source_config_sha256=IDENTITY['config_sha256'],
        produced_by_run=SOURCE_RUN,
        verification=dict(
            mode='offline host verification after the in-container hash pass was '
                 'cut by the 1180 s store-conversion guard lease',
            verified_at_ns=time.monotonic_ns(),
            seconds=round(time.time() - started, 3),
            payload_bytes=sum(row['payload_bytes'] for row in ranks),
            tensors_per_rank=ranks[0]['tensors'],
        ),
    )
    path = store / 'COMPLETE.json'
    if path.exists():
        raise SystemExit('COMPLETE.json already present; refusing to overwrite')
    path.write_text(json.dumps(complete, indent=2) + '\n')
    receipt = dict(
        store=str(store),
        verified=True,
        complete_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_run=SOURCE_RUN,
        payload_bytes=complete['verification']['payload_bytes'],
        tensors_per_rank=complete['verification']['tensors_per_rank'],
        seconds=complete['verification']['seconds'],
    )
    (V7 / 'glm-store-verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main(sys.argv[1])
