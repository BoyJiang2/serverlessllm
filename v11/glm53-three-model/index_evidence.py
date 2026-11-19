"""Index completed qjhs003 run evidence by path, byte count and SHA-256."""

import argparse
import hashlib
import json
from pathlib import Path


def indexed_file(root, path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError(f'Evidence changed while hashing: {path}')
    return dict(path=path.relative_to(root).as_posix(), bytes=before.st_size, sha256=digest.hexdigest())


def index(root):
    terminal = [root / name for name in ('result.json', 'qualification.json') if (root / name).is_file()]
    if len(terminal) != 1:
        raise ValueError(f'Expected one terminal qualification receipt: {root}')
    state = json.loads(terminal[0].read_bytes())
    if type(state['qualified']) is not bool:
        raise ValueError(f'Unknown qualification state: {terminal[0]}')
    files = [indexed_file(root, path) for path in sorted(root.rglob('*'))
             if not path.is_symlink() and path.is_file()]
    return dict(run_id=root.name, source=f'qjhs003:{root.as_posix()}', qualified=state['qualified'],
                qualification=terminal[0].name, files=files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    runs = [index(path.resolve(strict=True)) for path in args.runs]
    result = json.dumps(dict(runs=runs), indent=2) + '\n'
    if args.output is None:
        print(result, end='')
    else:
        args.output.write_text(result, encoding='utf-8')
