"""Build immutable candidate bytes from Git; verify local files without loading models."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

PREFIX = 'kimi/controls/kimi_store_v1'
SCHEMA = 'kimi-source-code-bundle-v1'
GIT_READER_SHA256 = '944d9c8abe9c2ddd2002ca2267915e263d43961f95da7261243a0ed992e8d819'
MAX_FILE_BYTES = 2 * 1024**2
MAX_TOTAL_BYTES = 16 * 1024**2
MAX_FILES = 512
MAX_MANIFEST_BYTES = 256 * 1024


def _rows(document):
    if (not isinstance(document, dict) or set(document) != {'schema', 'source_prefix', 'code_commit', 'files', 'runtime_admitted'}
            or document['schema'] != SCHEMA or document['source_prefix'] != PREFIX
            or not isinstance(document['code_commit'], str)
            or not re.fullmatch('[0-9a-f]{40}', document['code_commit'])
            or document['runtime_admitted'] is not False):
        raise ValueError('fixed source manifest identity required')
    rows = document['files']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError('bounded nonempty source inventory required')
    names, total = set(), 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}:
            raise ValueError('invalid source row')
        name = row['path']
        if not isinstance(name, str):
            raise ValueError('source path must be text')
        path = PurePosixPath(name)
        if (path.is_absolute() or path.as_posix() != name
                or any(part in ('.', '..') or not re.fullmatch('[A-Za-z0-9_.-]+', part) for part in path.parts)
                or not path.parts or name in names):
            raise ValueError('unique safe relative source path required')
        if (type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_FILE_BYTES
                or not isinstance(row['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('invalid source length or digest')
        total += row['bytes']
        names.add(name)
    if total > MAX_TOTAL_BYTES or not {'source_plugin.py', 'source_bundle.py', 'checkpoint-targets.json'} <= names:
        raise ValueError('bounded complete candidate inventory required')
    return rows


def build_source_inventory(commit, repo):
    # Reuse existing Git-object reader, without its GLM deployment commands.
    helper = Path(__file__).resolve().parents[2] / 'tools/deploy_tree.py'
    with helper.open('rb') as stream:
        reader_raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if hashlib.sha256(reader_raw).hexdigest() != GIT_READER_SHA256:
        raise ValueError('reviewed Git-object reader differs')
    namespace = {'__file__': str(helper), '__name__': '_kimi_git_source_reader'}
    exec(compile(reader_raw, str(helper), 'exec'), namespace)
    commit = namespace['resolve'](commit, repo)
    listing = subprocess.check_output(['git', 'ls-tree', '-r', '-l', '-z', '--full-tree',
        commit, '--', PREFIX], cwd=repo)
    entries = list(filter(None, listing.split(b'\0')))
    if len(entries) > MAX_FILES:
        raise ValueError('Git source inventory exceeds file bound')
    total = 0
    for entry in entries:
        mode, kind, oid, size = entry.split(b'\t', 1)[0].split()
        if mode not in (b'100644', b'100755') or kind != b'blob' or not size.isdigit():
            raise ValueError('regular Git source blobs required')
        count = int(size)
        total += count
        if count > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError('Git source bytes exceed bound before blob read')
    files = namespace['commit_files'](commit, PREFIX, repo)
    document = dict(schema=SCHEMA, source_prefix=PREFIX, code_commit=commit,
                    files=namespace['inventory'](files), runtime_admitted=False)
    _rows(document)
    return files, (json.dumps(document, sort_keys=True, indent=2) + '\n').encode('utf-8')


def verify_source_bundle(directory, manifest_path, expected_sha256, *, expected_code_commit):
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('externally bound manifest SHA required')
    with Path(manifest_path).open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('source manifest length or external SHA differs')
    document = json.loads(raw)
    rows = _rows(document)
    if document['code_commit'] != expected_code_commit:
        raise ValueError('source commit differs from expected reviewed commit')
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('regular source directory required')
    actual, entries = set(), 0
    def scan_error(error):
        raise error
    for parent, directories, filenames in os.walk(root, onerror=scan_error, followlinks=False):
        for name in directories + filenames:
            path = Path(parent) / name
            entries += 1
            if entries > MAX_FILES * 4 or path.is_symlink():
                raise ValueError('source traversal budget or symlink violation')
            if path.is_file(): actual.add(path.relative_to(root).as_posix())
            elif not path.is_dir(): raise ValueError('source contains nonregular entry')
    if actual != {row['path'] for row in rows}:
        raise ValueError('source file set differs: missing or extra files')
    for row in rows:
        with (root / row['path']).open('rb') as stream:
            data = stream.read(row['bytes'] + 1)
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('source bytes differ: ' + row['path'])
    return dict(code_commit=document['code_commit'], manifest_sha256=expected_sha256,
                files_verified=len(rows), source_bytes_verified=True,
                git_history_checked=False, runtime_admitted=False)


def materialize_source_bundle(commit, directory, manifest_path, *, repo):
    files, raw = build_source_inventory(commit, repo)
    target, manifest = Path(directory), Path(manifest_path)
    if (not target.is_absolute() or not manifest.is_absolute() or target.exists()
            or manifest.exists() or manifest == target or manifest.is_relative_to(target)):
        raise ValueError('fresh absolute source tree and external manifest paths required')
    target.mkdir()  # Keep partial failures; never overwrite or move another tree.
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(data)
    with manifest.open('xb') as stream: stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = verify_source_bundle(target, manifest, digest,
        expected_code_commit=json.loads(raw)['code_commit'])
    return receipt


def verify_source_run(directory, manifest_path, manifest_sha256, spec_path, spec_sha256):
    from source_plugin import _load_spec
    spec, _, _ = _load_spec(spec_path, spec_sha256)
    expected_root = spec['run_root'] / 'code'
    expected_manifest = spec['run_root'] / 'source-code-manifest.json'
    if (Path(directory).resolve(strict=True) != expected_root
            or Path(manifest_path).resolve(strict=True) != expected_manifest):
        raise ValueError('source tree and manifest belong to another run layout')
    receipt = verify_source_bundle(directory, manifest_path, manifest_sha256,
        expected_code_commit=spec['code_commit'])
    return dict(receipt, run_id=spec['run_id'], holder=spec['holder'],
                spec_sha256=spec_sha256, identity_declared_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    build = sub.add_parser('build')
    for name in ('commit', 'repo', 'directory', 'manifest'): build.add_argument('--' + name, required=True)
    verify = sub.add_parser('verify')
    for name in ('directory', 'manifest', 'sha256', 'commit'): verify.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.mode == 'build':
        receipt = materialize_source_bundle(args.commit, args.directory, args.manifest, repo=args.repo)
    else:
        receipt = verify_source_bundle(args.directory, args.manifest, args.sha256, expected_code_commit=args.commit)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
