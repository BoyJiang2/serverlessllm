"""Read original results and declared execution ledgers; never launch or retry."""
import hashlib
import json
import pathlib

PREREG_SHA = '90d4c91bf8b0c9495e0cb95376a55f84d17351308541414d58dd650d8e2dd37c'
BASELINE_SHA = '46fff94b59eec2f1365a7fc056508f953c8b040c6ae810d12daea790bfa70860'


def checked_json(path, expected):
    data = pathlib.Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('changed frozen input: ' + str(path))
    return json.loads(data)


def collect(prereg_file, baseline_file, history_dir, execution_roots):
    """Missing terminal records consume IDs but block admission until closed."""
    prereg = checked_json(prereg_file, PREREG_SHA)
    baseline = checked_json(baseline_file, BASELINE_SHA)['attempts']
    ids = [row['attempt_id'] for row in prereg['attempts']]
    if len(ids) != 40 or len(set(ids)) != 40 or ids != [row['attempt_id'] for row in baseline]:
        raise ValueError('original forty-ID order required')
    roots = [pathlib.Path(root) for root in execution_roots]
    if len({root.resolve() for root in roots}) != len(roots):
        raise ValueError('duplicate execution root')
    for root in roots:
        if root.is_symlink():
            raise ValueError('execution root symlink')
        if root.exists() and any(path.name not in ids or not path.is_dir() for path in root.iterdir()):
            raise ValueError('unexpected entry in formal-trials root')
    rows = []
    for initial in baseline:
        name = initial['attempt_id']
        found = [root / name for root in roots if (root / name).exists()]
        if len(found) > 1:
            raise ValueError('attempt appears in multiple executions: ' + name)
        if initial['status'] != 'unstarted':
            if found:
                raise ValueError('original terminal ID was rerun: ' + name)
            path = pathlib.Path(history_dir) / (name + '--result.json')
            result = checked_json(path, initial['result_sha256'])
            status = initial['status']
        elif found:
            directory = found[0]
            if directory.is_symlink() or directory.resolve().parent not in {root.resolve() for root in roots}:
                raise ValueError('attempt directory escaped execution root')
            path = directory / 'result.json'
            if not path.is_file() or not (directory / 'origin.json').is_file():
                raise ValueError('consumed attempt lacks durable terminal/origin evidence: ' + name)
            origin = json.loads((directory / 'origin.json').read_bytes())
            result = json.loads(path.read_bytes())
            if origin['attempt_id'] != name:
                raise ValueError('origin ID differs: ' + name)
            status = 'success' if result.get('success') is True else 'failed'
        else:
            rows.append(dict(initial))
            continue
        if result['attempt_id'] != name or type(result.get('success')) is not bool:
            raise ValueError('result identity/success differs: ' + name)
        if result['success'] != (status == 'success'):
            raise ValueError('terminal success differs: ' + name)
        rows.append(dict(attempt_id=name, status=status, result_path=str(path),
                         result_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return rows
