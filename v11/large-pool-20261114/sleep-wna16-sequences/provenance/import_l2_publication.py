"""Import a verified L2 evidence archive, or verify it against Git blobs."""

import argparse
import csv
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


PREFIX = Path('v11/large-pool-20261114/sleep-wna16-sequences')
RUN = re.compile(r'B2-WNA16-S[123]-r\d+-\d{8}T\d{6}Z')
SHA256 = re.compile(r'[0-9a-f]{64}')


def fingerprint(data):
    return len(data), hashlib.sha256(data).hexdigest()


def relative(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or '..' in path.parts or '.git' in path.parts
            or any(character in name for character in '\\:\x00\r\n')):
        raise ValueError(f'Invalid archive path: {name!r}')
    return path


def group(path):
    if RUN.fullmatch(path.parts[0]) and len(path.parts) >= 2:
        return PurePosixPath(path.parts[0])
    if path.parts[0] == 'provenance' and len(path.parts) >= 3:
        return PurePosixPath(*path.parts[:2])
    raise ValueError(f'Unexpected publication path: {path}')


def unpack(archive, checksum, out):
    sha, name = checksum.read_text(encoding='ascii').split(maxsplit=1)
    assert name.strip().removeprefix('*') == archive.name
    with archive.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == sha, 'Archive SHA-256 mismatch'
    seen = set()
    with tarfile.open(archive) as tar:
        for entry in tar:
            if entry.isdir():
                if entry.name not in ('.', './'):
                    relative(entry.name)
                continue
            path = relative(entry.name)
            assert entry.isfile() and path.as_posix().casefold() not in seen, entry.name
            seen.add(path.as_posix().casefold())
            if path.as_posix() != 'audit.json':
                group(path)
            target = out / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(tar.extractfile(entry).read())


def verify_indexes(out):
    files = {PurePosixPath(path.relative_to(out).as_posix()): fingerprint(path.read_bytes())
             for path in out.rglob('*') if path.is_file()}
    audit = PurePosixPath('audit.json')
    published = {audit}
    stage_audits = []
    groups = {group(path) for path in files if path != audit}
    for folder in groups:
        index = folder / 'evidence-index.csv'
        attrs = folder / '.gitattributes'
        assert index in files and (out / attrs).read_bytes() == b'* -text\n', folder
        published.update((index, attrs))
        base = folder if RUN.fullmatch(folder.name) else PurePosixPath()
        with (out / index).open(newline='', encoding='utf-8') as stream:
            for row in csv.DictReader(stream):
                name, sha = row['published_path'], row['sha256']
                size = int(row['bytes'])
                assert size >= 0 and SHA256.fullmatch(sha), row
                if not name:
                    continue
                path = base / relative(name)
                assert group(path) == folder and path not in published, path
                assert files[path] == (size, sha), path
                published.add(path)
                if path.name == 'combined-audit.json' and base == PurePosixPath():
                    stage_audits.append(path)
    assert len(stage_audits) == 1 and files[audit] == files[stage_audits[0]]
    assert set(files) == published, set(files) - published
    return files


def git(repo, *args, data=None):
    return subprocess.run(['git', '-C', str(repo), *args], input=data,
                          stdout=subprocess.PIPE, check=True).stdout


def check_git(repo, files, ref):
    paths = [f'{ref}:{(PREFIX / path).as_posix()}' for path in files]
    content = git(repo, 'cat-file', '--batch', data=('\n'.join(paths) + '\n').encode())
    with io.BytesIO(content) as stream:
        for path, fingerprint_expected in files.items():
            header = stream.readline().split()
            assert len(header) == 3 and header[1] == b'blob', (path, header)
            body = stream.read(int(header[2]))
            assert fingerprint(body) == fingerprint_expected and stream.read(1) == b'\n', path
        assert not stream.read(1)


def apply(repo, out, files):
    root = repo / PREFIX
    audit = PurePosixPath('audit.json')
    audit_path = (PREFIX / audit).as_posix()
    git(repo, 'diff', '--exit-code', '--', audit_path)
    git(repo, 'diff', '--cached', '--exit-code', '--', audit_path)
    changes = []
    for path, expected in files.items():
        target = root / path
        if not target.exists():
            if path != audit:
                assert not (root / group(path)).exists(), f'Incomplete previous evidence: {target}'
            changes.append(path)
        elif fingerprint(target.read_bytes()) != expected:
            assert path == audit, f'Prior publication differs: {target}'
            changes.append(path)
    unchanged = {path: files[path] for path in files if path not in changes}
    if unchanged:
        check_git(repo, unchanged, '')
    for path in changes:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((out / path).read_bytes())
    if changes:
        data = b''.join((PREFIX / path).as_posix().encode() + b'\0' for path in changes)
        git(repo, '--literal-pathspecs', 'add', '-f', '--pathspec-from-file=-', '--pathspec-file-nul',
            data=data)
    check_git(repo, files, '')
    print(f'Staged {len(changes)} paths. Index verified for all {len(files)} publication files.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('archive', type=Path)
    parser.add_argument('checksum', type=Path)
    parser.add_argument('--verify-ref', help='check Git blobs at a fetched ref and leave the repository untouched')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        unpack(args.archive, args.checksum, out)
        files = verify_indexes(out)
        if args.verify_ref:
            ref = git(args.repo, 'rev-parse', '--verify', args.verify_ref + '^{commit}').decode().strip()
            check_git(args.repo, files, ref)
            print(f'Verified {len(files)} publication files at {ref}')
        else:
            apply(args.repo, out, files)


if __name__ == '__main__':
    main()
