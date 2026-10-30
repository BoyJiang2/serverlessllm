"""Copy a verified source manifest to a new evidence directory; never move source."""
import argparse
import hashlib
import json
from pathlib import Path


def snapshot(source, destination, manifest_sha):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists')
    if destination.resolve().is_relative_to(source):
        raise ValueError('archive must be outside source')
    manifest_path = source/'control-input-manifest.json'
    payload = manifest_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest_sha:
        raise ValueError('manifest hash mismatch')
    rows = json.loads(payload)['files']
    names = set()
    for row in rows:
        rel = Path(row['path'])
        path = source/rel
        if rel.is_absolute() or '..' in rel.parts or rel.as_posix() in names:
            raise ValueError('unsafe or duplicate manifest path')
        names.add(rel.as_posix())
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(source):
            raise ValueError('source path escaped or symlinked')
        data = path.read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('source bytes mismatch: '+str(rel))
    destination.mkdir(parents=False, exist_ok=False)
    # Read again during copy: mutation after validation must fail, not publish success.
    for row in rows:
        data = (source/row['path']).read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('source changed during copy')
        target = destination/row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        if hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('archive bytes mismatch')
    with (destination/'control-input-manifest.json').open('xb') as stream:
        stream.write(payload)
    return dict(status='verified-copy', manifest_sha256=manifest_sha, files=len(rows),
                source_retained=source.is_dir(), scope='Manifest files only; not a runtime admission or deployment')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source, args.destination, args.manifest_sha256), indent=2))
