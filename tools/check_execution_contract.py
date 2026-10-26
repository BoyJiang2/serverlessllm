"""Read-only identity/source audit; never a workload launcher or R2 approval."""
import argparse
import collections
import hashlib
import json
import pathlib


def read(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('input escapes repository: ' + name)
    return path.read_bytes()


def audit(root):
    contract = json.loads(read(root, 'protocol/execution-contract.json'))
    for row in contract['pinned_files']:
        data = read(root, row['path'])
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('changed contract input: ' + row['path'])
    prereg = json.loads(read(root, 'protocol/measurement-preregistration.json'))
    ledger = json.loads(read(root, 'runtime/active/initial-ledger.json'))['attempts']
    ids = [x['attempt_id'] for x in prereg['attempts']]
    if len(ids) != 40 or len(set(ids)) != 40:
        raise ValueError('original forty unique IDs required')
    if [x['attempt_id'] for x in ledger] != ids:
        raise ValueError('ledger changed original ID order')
    if collections.Counter(x['condition'] for x in prereg['attempts']) != {
            'store_pool_hit': 10, 'store_disk_cold': 10,
            'safetensors_disk_cold': 10, 'safetensors_disk_hot': 10}:
        raise ValueError('original four conditions required')
    counts = collections.Counter(x['status'] for x in ledger)
    if counts != {'success': 5, 'failed': 4, 'unstarted': 31}:
        raise ValueError('historical baseline changed')
    for row in ledger:
        if row['status'] == 'unstarted':
            continue
        data = read(root, 'evidence/formal-history/' + row['attempt_id'] + '--result.json')
        result = json.loads(data)
        if (hashlib.sha256(data).hexdigest() != row['result_sha256']
                or result['attempt_id'] != row['attempt_id']
                or bool(result['success']) != (row['status'] == 'success')):
            raise ValueError('historical result mismatch: ' + row['attempt_id'])
    active = json.loads(read(root, 'runtime/active/run-manifest.json'))
    manifest = json.loads(read(root, 'runtime/active/control-input-manifest.json'))
    if active['scope'] != 'G1 independent untimed controls only; zero formal IDs':
        raise ValueError('control identity cannot authorize formal IDs')
    if active['run_id'] == contract['experiment_id']:
        raise ValueError('control and experiment identities must be distinct')
    for name in contract['control_files']:
        rows = [x for x in manifest['files'] if x['path'] == name]
        data = read(root, 'runtime/active/' + name)
        if len(rows) != 1 or hashlib.sha256(data).hexdigest() != rows[0]['sha256']:
            raise ValueError('missing or stale control manifest entry: ' + name)
    for name in ('cleanup_guard13.sh', 'install_guard13.sh'):
        if ('run_id=' + active['run_id']).encode() not in read(root, 'runtime/active/' + name).splitlines():
            raise ValueError('guard/control identity mismatch: ' + name)
    return dict(identity_audit='passed', experiment_id=contract['experiment_id'],
                control_run_id=active['run_id'], baseline_counts=dict(counts),
                next_original_id=next(x['attempt_id'] for x in ledger if x['status'] == 'unstarted'),
                formal_ready=False, blockers=contract['formal_blockers'],
                scope='repository baseline only; not current remote ledger, deployment or runtime proof')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-formal', action='store_true')
    args = parser.parse_args()
    try:
        result = audit(pathlib.Path(__file__).resolve().parents[1])
    except (ValueError, KeyError, OSError) as error:
        print(json.dumps(dict(identity_audit='failed', formal_ready=False, error=str(error))))
        return 1
    print(json.dumps(result, indent=2))
    return 2 if args.require_formal and not result['formal_ready'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
