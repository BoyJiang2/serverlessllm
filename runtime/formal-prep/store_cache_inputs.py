"""Exact timed Store inputs; payloads are statted, metadata hashes are checked."""
import hashlib
import pathlib

METADATA = ('config.json', 'generation_config.json', 'tokenizer_config.json',
            'tokenizer.json', 'chat_template.jinja', 'model.safetensors.index.json')


def store_inputs(store, checkpoint, rank_rows, metadata_hashes):
    store, checkpoint = pathlib.Path(store), pathlib.Path(checkpoint)
    rows = [row for row in rank_rows if row['rank'] != 'inactive_mtp']
    if len(rows) != 8 or sorted(row['rank'] for row in rows) != list(range(8)):
        raise ValueError('eight Store rank receipts required')
    rank_names = {f'rank_{rank}' for rank in range(8)}
    if {path.name for path in store.glob('rank_*')} != rank_names:
        raise ValueError('Store rank closure differs')
    metadata = set(METADATA) | {f'rank_{rank}/{name}' for rank in range(8)
                               for name in ('raw_state.json', 'tensor_index.json')}
    if set(metadata_hashes) != metadata:
        raise ValueError('complete frozen Store metadata required')
    paths = []
    for row in sorted(rows, key=lambda row: row['rank']):
        directory = store / f"rank_{row['rank']}"
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Store rank directory differs')
        sizes = row['partition_bytes']
        if len(sizes) != 10 or {p.name for p in directory.glob('tensor.data_*')} != {f'tensor.data_{i}' for i in range(10)}:
            raise ValueError('ten partitions per rank required')
        for number, size in enumerate(sizes):
            path = directory / f'tensor.data_{number}'
            if path.is_symlink() or not path.is_file() or size <= 0 or path.stat().st_size != size:
                raise ValueError('Store partition identity differs')
            paths.append(path)
    for name in sorted(metadata):
        path = store / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(store.resolve()):
            raise ValueError('Store metadata path differs')
        record = metadata_hashes[name]
        if path.stat().st_size != record['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Store metadata identity differs: ' + name)
        paths.append(path)
    # Native configurations use the original checkpoint tokenizer/config as well.
    for name in METADATA:
        path = checkpoint / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('checkpoint metadata missing')
        paths.append(path)
    if len(paths) != 108 or len({str(path.resolve()) for path in paths}) != 108:
        raise ValueError('Store timed input closure differs')
    return paths
