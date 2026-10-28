"""Build one run's source tree from a single git commit, deploy it without overwriting, and check it against history.

Bytes come from git objects (ls-tree + cat-file), never from the working tree, so every deployed tree can be
rebuilt from its commit. `formal` trees follow package_inventory13.reviewed_files and are only built and compared
here; deploying them stays with the R1-gated deploy_control13.py. `debug` trees drop the formal run identity and
carry the debug entry from the same commit.
"""
import argparse
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile

REPO = pathlib.Path(__file__).resolve().parents[1]
ACTIVE = 'runtime/active'
DEBUG_ENTRY = 'controls/debug_stack_v1/debug_service.py'
FORMAL_IDENTITY = 'run-manifest.json'
MANIFEST = 'control-input-manifest.json'
REVIEW_EXTRAS = (MANIFEST, 'REVIEW-PROMPT.md')
HOST = '/data/brick1/boyjiang/glm5.1-fp8'
SSH_HOST = 'qjhs003'
RECEIPT = 'deploy-tree.json'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def resolve(commit, repo=REPO):
    return subprocess.check_output(['git', 'rev-parse', '--verify', commit+'^{commit}'], cwd=repo, text=True).strip()


def _blobs(commit, prefix, repo):
    listing = subprocess.check_output(['git', 'ls-tree', '-r', '-z', '--full-tree', commit, '--', prefix], cwd=repo)
    entries = []
    for row in filter(None, listing.split(b'\0')):
        meta, path = row.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('only regular files may be deployed: %s %s' % (mode, path.decode()))
        entries.append((path.decode(), oid))
    if not entries:
        raise ValueError('nothing under %s at %s' % (prefix, commit))
    output = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo, check=True, capture_output=True,
                            input=''.join(oid+'\n' for _, oid in entries).encode()).stdout
    blobs, offset = {}, 0
    for path, oid in entries:
        end = output.index(b'\n', offset)
        got, kind, size = output[offset:end].decode().split()
        if got != oid or kind != 'blob':
            raise ValueError('unexpected git object for '+path)
        blobs[path] = output[end+1:end+1+int(size)]
        offset = end+2+int(size)
    return blobs


def commit_files(commit, prefix=ACTIVE, repo=REPO):
    """Exact committed bytes under a directory; the working tree, untracked files and git attributes never participate."""
    return {pathlib.PurePosixPath(path).relative_to(prefix).as_posix(): data
            for path, data in _blobs(commit, prefix, repo).items()}


def commit_file(commit, path, repo=REPO):
    blobs = _blobs(commit, path, repo)
    if list(blobs) != [path]:
        raise ValueError('%s is not a single committed file at %s' % (path, commit))
    return blobs[path]


def reviewed_inventory_exact(files):
    """package_inventory13.reviewed_files on an in-memory tree: manifest rows plus the manifest and review prompt."""
    if MANIFEST not in files:
        return False
    rows = json.loads(files[MANIFEST])['files']
    allowed = {row['path'] for row in rows} | set(REVIEW_EXTRAS)
    actual = {path for path in files if '__pycache__' not in pathlib.PurePosixPath(path).parts}
    return actual == allowed and all(len(files[r['path']]) == r['bytes'] and sha256(files[r['path']]) == r['sha256'] for r in rows)


def formal_tree(files):
    """What deploy_control13 ships, and nothing else."""
    if not reviewed_inventory_exact(files):
        raise ValueError('commit tree differs from its own reviewed inventory')
    rows = json.loads(files[MANIFEST])['files']
    return {path: files[path] for path in sorted({r['path'] for r in rows} | set(REVIEW_EXTRAS))}


def debug_tree(files, entry):
    """Committed runtime files without the formal run identity, plus the debug entry from the same commit."""
    tree = {path: data for path, data in files.items()
            if path != FORMAL_IDENTITY and '__pycache__' not in pathlib.PurePosixPath(path).parts}
    tree['debug_service.py'] = entry
    return dict(sorted(tree.items()))


def inventory(tree):
    return [dict(path=path, bytes=len(data), sha256=sha256(data)) for path, data in sorted(tree.items())]


def build(commit, kind, repo=REPO):
    commit = resolve(commit, repo)
    files = commit_files(commit, ACTIVE, repo)
    if kind == 'formal':
        tree = formal_tree(files)
    elif kind == 'debug':
        tree = debug_tree(files, commit_file(commit, DEBUG_ENTRY, repo))
    else:
        raise ValueError('kind must be formal or debug')
    rows = inventory(tree)
    receipt = dict(kind=kind, code_commit=commit, source_prefix=ACTIVE, reviewed_inventory_exact=reviewed_inventory_exact(files),
                   manifest_sha256=sha256(files[MANIFEST]) if MANIFEST in files else None,
                   tree_sha256=sha256(json.dumps(rows, sort_keys=True).encode()), files=rows)
    return tree, receipt


def verify_receipt(record, repo=REPO):
    """Rebuild a recorded run's formal tree and compare it with that run's executed-source receipt."""
    tree, built = build(record['code_commit'], 'formal', repo)
    rows = {r['path']: r for r in built['files']}
    recorded = record['files_checked_before_and_after']
    mismatched = [r['path'] for r in recorded if rows.get(r['path']) != dict(path=r['path'], bytes=r['bytes'], sha256=r['sha256'])]
    manifest_rows = sorted(json.loads(tree[MANIFEST])['files'], key=lambda r: r['path'])
    result = dict(run_id=record['run_id'], code_commit=built['code_commit'], recorded_files=len(recorded), mismatched=mismatched,
                  manifest_sha256_match=built['manifest_sha256'] == record['manifest_sha256'],
                  recorded_rows_equal_manifest=sorted(recorded, key=lambda r: r['path']) == manifest_rows)
    result['identical'] = not mismatched and result['manifest_sha256_match'] and result['recorded_rows_equal_manifest']
    return result


REMOTE_SNAPSHOTS = r'''
import hashlib, json, os, pathlib
root = pathlib.Path(%r)/'validation/controls'
out = {}
for run in sorted(p for p in root.iterdir() if p.is_dir()):
    snapshots = sorted(p for p in run.glob('executed-source-*') if p.is_dir() and not p.is_symlink())
    if not (run/'deployment.json').is_file() or not snapshots:
        continue
    record = dict(deployment=json.loads((run/'deployment.json').read_text()), snapshots={})
    for snapshot in snapshots:
        files, links = {}, []
        for path in sorted(snapshot.rglob('*')):
            if path.is_symlink():
                links.append(path.relative_to(snapshot).as_posix())
            elif path.is_file():
                digest, size = hashlib.sha256(), 0
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1 << 20), b''):
                        digest.update(block); size += len(block)
                    os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                files[path.relative_to(snapshot).as_posix()] = [size, digest.hexdigest()]
        record['snapshots'][snapshot.name] = dict(files=files, symlinks=links)
    out[run.name] = record
print(json.dumps(out))
'''


def compare_snapshots(remote, repo=REPO):
    """Per archived executed-source tree: what the recorded commit's formal tree lacks, adds or changes.

    Like reviewed_files, __pycache__ is not source: host-side imports leave bytecode in the deployed tree."""
    report = {}
    for run, record in remote.items():
        deployment = record['deployment']
        try:
            _, built = build(deployment['code_commit'], 'formal', repo)
        except Exception as error:
            report[run] = dict(code_commit=deployment.get('code_commit'), error=repr(error))
            continue
        expected = {r['path']: [r['bytes'], r['sha256']] for r in built['files']}
        for name, snapshot in record['snapshots'].items():
            files = snapshot['files']
            caches = sorted(p for p in files if '__pycache__' in pathlib.PurePosixPath(p).parts)
            row = dict(code_commit=built['code_commit'],
                       manifest_sha256_match=deployment.get('input_manifest_sha256') == built['manifest_sha256'],
                       files=len(files), bytecode_cache_files=len(caches), missing=sorted(set(expected)-set(files)),
                       extra=sorted(set(files)-set(expected)-set(caches)),
                       mismatched=sorted(p for p in set(expected) & set(files) if expected[p] != files[p]), symlinks=snapshot['symlinks'])
            row['identical'] = row['manifest_sha256_match'] and not any(row[k] for k in ('missing', 'extra', 'mismatched', 'symlinks'))
            report[run+'/'+name] = row
    return report


VERIFY = r'''
import hashlib, json, pathlib
root = pathlib.Path(%r)
receipt = json.loads((root/%r).read_text())
expected = {r['path']: (r['bytes'], r['sha256']) for r in receipt['files']}
actual = {}
for path in root.rglob('*'):
    relative = path.relative_to(root).as_posix()
    assert not path.is_symlink(), 'symlink in deployed tree: '+relative
    if path.is_file() and relative != %r:
        data = path.read_bytes()
        actual[relative] = (len(data), hashlib.sha256(data).hexdigest())
assert actual == expected, 'deployed tree differs from its receipt'
assert receipt['kind'] != 'debug' or 'run-manifest.json' not in actual
print(json.dumps(dict(destination=str(root), verified_files=len(actual), code_commit=receipt['code_commit'])))
'''


def destination_for(receipt):
    if receipt['kind'] != 'debug':
        raise ValueError('only debug trees are pushed here; formal deployment stays with deploy_control13.py')
    return '%s/code/source-debug-%s-%s' % (HOST, receipt['code_commit'][:12], receipt['tree_sha256'][:12])


def push(tree, receipt, host=SSH_HOST):
    """Content-addressed and never overwritten: an existing destination is only re-verified."""
    destination = destination_for(receipt)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        for name, data, mode in [(p, d, 0o444) for p, d in tree.items()]+[(RECEIPT, json.dumps(receipt, indent=2).encode(), 0o444)]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    remote = ('set -e; if [ -e {d} ]; then echo existing; else mkdir -m 700 {d}.partial; tar -C {d}.partial -xf -; '
              'mv -T {d}.partial {d}; echo created; fi').format(d=destination)
    state = subprocess.run(['ssh', '-o', 'BatchMode=yes', host, remote], input=archive.getvalue(), check=True, capture_output=True).stdout.decode().strip()
    checked = subprocess.run(['ssh', '-o', 'BatchMode=yes', host, 'python3', '-'], check=True, capture_output=True,
                             input=(VERIFY % (destination, RECEIPT, RECEIPT)).encode()).stdout.decode()
    return dict(state=state, **json.loads(checked))


def main():
    parser = argparse.ArgumentParser(description='Build, compare or push a source tree made from one git commit.')
    sub = parser.add_subparsers(dest='action', required=True)
    make = sub.add_parser('build', help='materialize a tree locally and print its receipt')
    make.add_argument('--commit', default='HEAD')
    make.add_argument('--kind', choices=('formal', 'debug'), required=True)
    make.add_argument('--out', type=pathlib.Path, help='new directory to write the tree into')
    check = sub.add_parser('verify-receipt', help='rebuild a run from its executed-source receipt and compare')
    check.add_argument('receipt', type=pathlib.Path)
    sub.add_parser('verify-snapshots', help='rebuild every archived executed-source tree on qjhs003 and compare (read-only)')
    send = sub.add_parser('push', help='deploy a debug tree to qjhs003 (content-addressed, never overwritten)')
    send.add_argument('--commit', default='HEAD')
    args = parser.parse_args()
    if args.action == 'build':
        tree, receipt = build(args.commit, args.kind)
        if args.out:
            args.out.mkdir()
            for path, data in tree.items():
                (args.out/path).parent.mkdir(parents=True, exist_ok=True)
                (args.out/path).write_bytes(data)
            (args.out/RECEIPT).write_text(json.dumps(receipt, indent=2))
        result = {k: v for k, v in receipt.items() if k != 'files'} | dict(file_count=len(receipt['files']))
    elif args.action == 'verify-receipt':
        result = verify_receipt(json.loads(args.receipt.read_text()))
    elif args.action == 'verify-snapshots':
        remote = subprocess.run(['ssh', '-o', 'BatchMode=yes', SSH_HOST, 'python3', '-'], check=True, capture_output=True,
                                input=(REMOTE_SNAPSHOTS % HOST).encode()).stdout
        result = compare_snapshots(json.loads(remote))
    else:
        result = push(*build(args.commit, 'debug'))
    print(json.dumps(result, indent=2))
    return 0 if result.get('identical', True) is not False else 1


if __name__ == '__main__':
    sys.exit(main())
