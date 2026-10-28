"""Fetch exact public registry blobs with resume; produce a Docker-load archive."""
import argparse
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import time
import urllib.parse
import urllib.request

REPO = 'vllm/vllm-openai'
MANIFEST = 'f17a0d64023227305acb47a26edebb52c43e683817ffcac4cca4e6bb7a83692a'
CONFIG = 'aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('non-HTTPS registry redirect')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main(manifest_path, output):
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST:
        raise ValueError('manifest identity mismatch')
    manifest = json.loads(raw)
    if manifest['config']['digest'] != 'sha256:'+CONFIG:
        raise ValueError('config identity mismatch')
    output.mkdir(parents=True, exist_ok=True)
    blobs = output/'blobs'
    blobs.mkdir(exist_ok=True)
    descriptors = [manifest['config'], *manifest['layers']]
    if shutil.disk_usage(output).free < 2*sum(x['size'] for x in descriptors)+5*2**30:
        raise ValueError('insufficient disk budget for blobs plus archive and reserve')
    opener = urllib.request.build_opener(PublicRedirect())
    with opener.open('https://auth.docker.io/token?service=registry.docker.io&scope=repository:'+REPO+':pull', timeout=25) as stream:
        token = json.load(stream)['token']
    deadline = time.monotonic()+1800

    def fetch(descriptor):
        name = descriptor['digest'].removeprefix('sha256:')
        if len(name) != 64 or any(c not in '0123456789abcdef' for c in name):
            raise ValueError('invalid digest')
        target, part = blobs/name, blobs/(name+'.part')
        if target.exists():
            if target.stat().st_size != descriptor['size'] or digest(target) != name:
                raise ValueError('existing verified blob is corrupt')
            return name
        for attempt in range(3):
            try:
                if time.monotonic() >= deadline:
                    raise TimeoutError('transfer deadline')
                offset = part.stat().st_size if part.exists() else 0
                if offset > descriptor['size']:
                    raise ValueError('partial blob exceeds descriptor')
                if offset < descriptor['size']:
                    request = urllib.request.Request('https://registry-1.docker.io/v2/'+REPO+'/blobs/'+descriptor['digest'],
                                                    headers={'Authorization': 'Bearer '+token, 'Range': f'bytes={offset}-'})
                    with opener.open(request, timeout=30) as response:
                        if response.status == 206:
                            if not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                                raise ValueError('range mismatch')
                        elif response.status != 200 or offset:
                            raise ValueError('server ignored resume offset')
                        with part.open('ab') as stream:
                            while True:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError('transfer deadline')
                                block = response.read(1024*1024)
                                if not block:
                                    break
                                stream.write(block)
                if part.stat().st_size != descriptor['size'] or digest(part) != name:
                    raise ValueError('blob size/hash mismatch')
                part.rename(target)
                print('VERIFIED', name[:12], descriptor['size'], flush=True)
                return name
            except Exception as error:
                print('ATTEMPT_FAILED', name[:12], attempt+1, type(error).__name__, flush=True)
                if attempt == 2 or time.monotonic() >= deadline:
                    raise RuntimeError('bounded blob transfer failed: '+name) from None
        raise RuntimeError('unreachable')

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, descriptors))
    config = json.loads((blobs/CONFIG).read_bytes())
    if len(config['rootfs']['diff_ids']) != len(manifest['layers']):
        raise ValueError('layer count mismatch')
    archive = output/'original-image.docker.tar'
    if archive.exists():
        raise FileExistsError('archive already exists; inspect receipt before reuse')
    partial = output/'original-image.docker.tar.partial'
    with tarfile.open(partial, 'w') as tar:
        tar.add(blobs/CONFIG, arcname=CONFIG+'.json', recursive=False)
        paths = []
        for layer in manifest['layers']:
            name = layer['digest'][7:]
            path = name+'/layer.tar'
            tar.add(blobs/name, arcname=path, recursive=False)
            paths.append(path)
        data = json.dumps([dict(Config=CONFIG+'.json', RepoTags=[], Layers=paths)]).encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    partial.rename(archive)
    receipt = dict(manifest_sha256=MANIFEST, image_id='sha256:'+CONFIG,
                   archive_sha256=digest(archive), archive_bytes=archive.stat().st_size,
                   verified_blobs=len(descriptors), status='archive_ready_not_yet_docker_loaded')
    (output/'archive-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.manifest.resolve(), args.output.resolve())
