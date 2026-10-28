"""Bounded, single-copy Kimi asset download; never imports model code or uses GPUs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request

ROOT = Path('/data/brick1/boyjiang/kimi-k2.6')
REVISION = '7eb5002f6aadc958aed6a9177b7ed26bb94011bb'
RATE = 32 * 1024**2
RESERVE = (800 + 300) * 1024**3  # Future Store allowance + disk safety margin.


def fetch_file(url, target, size, expected_sha=None):
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    partial = target.with_name(target.name + '.partial')
    digest, count = hashlib.sha256(), 0
    start = time.monotonic()
    with urllib.request.urlopen(url, timeout=30) as response:
        if response.status != 200 or int(response.headers['Content-Length']) != size:
            raise ValueError('unexpected HTTP status or file size')
        with partial.open('xb') as stream:
            flushed = 0
            while True:
                block = response.read(8 * 1024**2)
                if not block:
                    break
                count += len(block)
                if count > size:
                    raise ValueError('oversized response')
                stream.write(block)
                digest.update(block)
                if count - flushed >= 256 * 1024**2:
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.posix_fadvise(stream.fileno(), flushed, count - flushed,
                                     os.POSIX_FADV_DONTNEED)
                    flushed = count
                time.sleep(max(0, count / RATE - (time.monotonic() - start)))
            stream.flush()
            os.fsync(stream.fileno())
            os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    actual = digest.hexdigest()
    if count != size or (expected_sha is not None and actual != expected_sha):
        raise ValueError('download size or SHA256 mismatch; partial retained')
    partial.rename(target)
    return {'path': target.name, 'bytes': count, 'sha256': actual,
            'expected_sha256': expected_sha, 'seconds': time.monotonic() - start}


def download(run):
    if run.parent != ROOT / 'downloads' or run.is_symlink():
        raise ValueError('unexpected run directory')
    manifest = json.loads((run / 'official-metadata.json').read_text())
    if manifest['model'] != 'moonshotai/Kimi-K2.6' or manifest['revision'] != REVISION:
        raise ValueError('unfrozen model input')
    files = manifest['all_files']
    paths = [Path(row['path']) for row in files]
    if len(set(paths)) != len(paths) or any(p.is_absolute() or '..' in p.parts for p in paths):
        raise ValueError('unsafe or duplicate manifest paths')
    total = sum(row['bytes'] for row in files)
    if shutil.disk_usage(ROOT).free < total + RESERVE:
        raise ValueError('insufficient disk for download, future Store allowance and reserve')
    destination = ROOT / 'checkpoint' / REVISION
    if destination.parent.exists() and destination.parent.is_symlink():
        raise ValueError('checkpoint parent symlink')
    destination.mkdir(parents=True, exist_ok=False)
    weights = {row['path']: row['lfs_sha256'] for row in manifest['weight_files']}
    if len(weights) != 64 or any(len(v) != 64 for v in weights.values()):
        raise ValueError('incomplete official weight digests')
    begin = time.time()
    with (run / 'events.jsonl').open('x') as events:
        for row in sorted(files, key=lambda r: (r['path'].endswith('.safetensors'), r['path'])):
            name = row['path']
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(ROOT).free < row['bytes'] + RESERVE:
                raise ValueError('disk reserve exhausted by concurrent usage')
            events.write(json.dumps({'event': 'begin', 'path': name, 'utc_epoch': time.time()}) + '\n')
            events.flush()
            expected = weights.get(name)
            if name == 'config.json':
                expected = manifest['config_sha256']
            if name == 'model.safetensors.index.json':
                expected = '00a982d005d6e39b25cfcb379a7e6d57f393fc0346e70892ea4271c259c0d1e3'
            result = fetch_file('https://huggingface.co/moonshotai/Kimi-K2.6/resolve/'
                                + REVISION + '/' + name, target, row['bytes'], expected)
            events.write(json.dumps({'event': 'complete', **result, 'relative_path': name}) + '\n')
            events.flush()
    (run / 'complete.json').write_text(json.dumps({'revision': REVISION, 'files': len(files),
        'weight_files': len(weights), 'bytes': total, 'seconds': time.time() - begin,
        'payload_sha256_verified': True, 'store_created': False, 'model_started': False}) + '\n')


if __name__ == '__main__':
    import sys
    directory = Path(sys.argv[1])
    try:
        download(directory)
    except Exception as error:
        # Do not expose signed redirect URLs or credentials in logs.
        (directory / 'failure.json').write_text(json.dumps({
            'exception_type': type(error).__name__, 'utc_epoch': time.time(),
            'partial_retained': True, 'automatic_retry': False}) + '\n')
        raise SystemExit(1)
