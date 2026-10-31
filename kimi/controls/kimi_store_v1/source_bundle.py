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



def _p0_identity(identity):
    patterns = dict(run_id='kimi-p0-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',
        holder='[0-9a-f]{32}', code_commit='[0-9a-f]{40}',
        spec_sha256='[0-9a-f]{64}', source_manifest_sha256='[0-9a-f]{64}')
    if (not isinstance(identity, dict) or set(identity) != set(patterns)
            or any(not isinstance(identity[k], str) or not re.fullmatch(pattern, identity[k])
                   for k, pattern in patterns.items())):
        raise ValueError('exact P0 run identity required')
    return dict(identity)


def verify_p0_run(directory, manifest_path, manifest_sha256, spec_path, spec_sha256):
    """Bind the tiny Torch IPC component, without authorizing a model or Store."""
    from vllm_export_hook import _metadata_stream
    if not isinstance(spec_sha256, str) or not re.fullmatch('[0-9a-f]{64}', spec_sha256):
        raise ValueError('external P0 spec SHA required')
    with _metadata_stream(spec_path) as stream:
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != spec_sha256:
        raise ValueError('P0 spec length or SHA differs')
    spec = json.loads(raw)
    keys = {'schema', 'node', 'run_id', 'holder', 'code_commit', 'run_root', 'gpu_uuid',
            'image_id', 'max_payload_bytes', 'component_max_seconds'}
    if (not isinstance(spec, dict) or set(spec) != keys
            or spec['schema'] != 'kimi-cuda-ipc-p0a-run-v1' or spec['node'] != 'qjhs003'):
        raise ValueError('exact P0 component spec required')
    identity = _p0_identity(dict(run_id=spec['run_id'], holder=spec['holder'], code_commit=spec['code_commit'],
        spec_sha256=spec_sha256, source_manifest_sha256=manifest_sha256))
    if (not isinstance(spec['gpu_uuid'], str)
            or not re.fullmatch('GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', spec['gpu_uuid'])
            or not isinstance(spec['image_id'], str) or not re.fullmatch('sha256:[0-9a-f]{64}', spec['image_id'])
            or type(spec['max_payload_bytes']) is not int or not 40 <= spec['max_payload_bytes'] <= 1024**2
            or type(spec['component_max_seconds']) is not int or not 4 <= spec['component_max_seconds'] <= 300):
        raise ValueError('bounded P0 payload, lifetime, immutable image and one GPU UUID required')
    if not isinstance(spec['run_root'], str) or not Path(spec['run_root']).is_absolute():
        raise ValueError('absolute existing P0 run root required')
    root = Path(spec['run_root']).resolve(strict=True)
    if (not root.is_dir() or Path(directory).resolve(strict=True) != root/'code'
            or Path(manifest_path).resolve(strict=True) != root/'source-code-manifest.json'
            or Path(spec_path).resolve(strict=True) != root/'p0-spec.json'):
        raise ValueError('P0 code, manifest and spec belong to another run layout')
    receipt = verify_source_bundle(directory, manifest_path, manifest_sha256,
                                   expected_code_commit=identity['code_commit'])
    # The common inventory verifies bytes only; it grants no source-model approval.
    for name in ('native_record_call.py', 'source_schedule.py'):
        if not (root/'code'/name).is_file():
            raise ValueError('P0 entry absent from verified code inventory: ' + name)
    return dict(receipt, run_id=identity['run_id'], holder=identity['holder'], spec_sha256=spec_sha256,
        run_root=str(root), gpu_uuid=spec['gpu_uuid'], image_id=spec['image_id'],
        max_payload_bytes=spec['max_payload_bytes'], component_max_seconds=spec['component_max_seconds'],
        identity_declared_only=True)



def verify_p0_worker_input(path, expected_sha256):
    """Freeze worker input before Docker assigns the host-only container ID."""
    from source_schedule import load_source_plan, p0_schedule, _utc
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute P0 worker input required')
    root = path.resolve(strict=True).parent
    if path.resolve(strict=True) != root/'p0-worker-input.json':
        raise ValueError('fixed P0 worker input layout required')
    document = load_source_plan(path, expected_sha256)
    if (not isinstance(document, dict) or set(document) != {'schema','plan_sha256','batch_entered_utc','environment_sha256'}
            or document['schema'] != 'kimi-p0-worker-input-v1'):
        raise ValueError('exact P0 worker input schema required')
    plan = load_source_plan(root/'p0-plan.json', document['plan_sha256'])
    receipt = verify_p0_run(root/'code', root/'source-code-manifest.json', plan['source_manifest_sha256'],
                            root/'p0-spec.json', plan['spec_sha256'])
    schedule = p0_schedule(plan, receipt, now=_utc(document['batch_entered_utc']))
    if (not isinstance(document['environment_sha256'],str)
            or not re.fullmatch('[0-9a-f]{64}',document['environment_sha256'])):
        raise ValueError('complete P0 environment digest required')
    receipt['worker_input_sha256'] = expected_sha256
    receipt['environment_sha256'] = document['environment_sha256']
    identity = dict(run_id=receipt['run_id'],holder=receipt['holder'],code_commit=receipt['code_commit'],
                    spec_sha256=receipt['spec_sha256'],source_manifest_sha256=receipt['manifest_sha256'])
    return path, root, document, receipt, schedule, identity


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
