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


def add_joint_companions(run, summary):
    if (summary['run_id'], summary['qualified']) != (run['run_id'], run['qualified']):
        raise ValueError(f"Recomputed and source qualification differ for {run['run_id']}")
    host = summary['host_monitor']
    trace = Path(host['path']).resolve(strict=True)
    files = [('host_memory', trace), ('controller_log', trace.with_name(f"controller-{run['run_id']}.log")),
             ('observer_log', trace.with_name(f"observe-{run['run_id']}.log"))]
    companions = [dict(role=role, **indexed_file(path.parent, path)) for role, path in files]
    for entry, (_, path) in zip(companions, files, strict=True):
        entry['path'] = f'qjhs003:{path.as_posix()}'
    if (host['read_bytes'], host['sha256']) != (companions[0]['bytes'], companions[0]['sha256']):
        raise ValueError(f'Host memory trace differs from the recomputed complete file: {trace}')
    kimi = summary.get('kimi_trace')
    if kimi:
        expected = run['source'] + '/worker-K-final.log'
        indexed = next(item for item in run['files'] if item['path'] == 'worker-K-final.log')
        if (f"qjhs003:{Path(kimi['path']).resolve(strict=True).as_posix()}", kimi['bytes'], kimi['sha256']) != (
                expected, indexed['bytes'], indexed['sha256']):
            raise ValueError(f"Kimi log differs from recomputed source in {run['run_id']}")
    run['companions'] = companions


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--recomputed', type=Path, nargs='+', help='joint B2 summaries with original host traces')
    args = parser.parse_args()
    runs = [index(path.resolve(strict=True)) for path in args.runs]
    if args.recomputed:
        summaries = [json.loads(path.read_bytes()) for path in args.recomputed]
        by_run = {item['run_id']: item for item in summaries}
        if len(by_run) != len(summaries) or len(runs) != len(summaries) or set(by_run) != {r['run_id'] for r in runs}:
            raise ValueError('Expected one recomputed summary for every indexed run')
        for run in runs:
            add_joint_companions(run, by_run[run['run_id']])
    result = json.dumps(dict(runs=runs), indent=2) + '\n'
    if args.output is None:
        print(result, end='')
    else:
        args.output.write_text(result, encoding='utf-8')
