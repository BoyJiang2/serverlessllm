"""One explicitly authorized resume after validating both archive prefixes."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from relay_original_image import IMAGE, REMOTE, SSH


def main(output):
    marker = output/'resume-registration.json'
    if marker.exists():
        raise FileExistsError('resume already registered; inspect rather than duplicate')
    registration = json.loads((output/'relay-registration.json').read_text())
    receipt = json.loads((output/'archive-receipt.json').read_text())
    remote = registration['remote']
    if not remote.startswith('/data/brick1/boyjiang/glm5.1-fp8/recovery/image-relay-'):
        raise ValueError('unexpected registered directory')
    if receipt['image_id'] != IMAGE:
        raise ValueError('image mismatch')
    archive = output/'original-image.docker.tar'
    if archive.stat().st_size != receipt['archive_bytes']:
        raise ValueError('local archive size differs')
    marker.write_text(json.dumps(dict(run_id=registration['run_id'], status='prefix_check',
                                     scope='Human-authorized single resume; transfer600s/import600s/probe90s'))+'\n')
    code = "import pathlib,json,sys,subprocess; p=pathlib.Path(sys.argv[1]); print(json.dumps(dict(bytes=p.stat().st_size,sha256=subprocess.check_output(['sha256sum',str(p)],text=True).split()[0])))"
    probe = subprocess.run(SSH+[shlex.join(['python3','-c',code,remote+'/original-image.docker.tar.partial'])],
                           capture_output=True,text=True,check=True,timeout=90)
    prefix = json.loads(probe.stdout)
    if not 0 < prefix['bytes'] <= receipt['archive_bytes']:
        raise ValueError('invalid remote partial size')
    h = hashlib.sha256()
    remaining = prefix['bytes']
    with archive.open('rb') as stream:
        while remaining:
            block = stream.read(min(8*1024*1024, remaining))
            if not block:
                raise ValueError('local prefix truncated')
            h.update(block); remaining -= len(block)
    if h.hexdigest() != prefix['sha256']:
        raise ValueError('remote partial does not match local prefix; no append performed')
    (output/'resume-prefix-proof.json').write_text(json.dumps(prefix,indent=2)+'\n')
    print('PREFIX_VERIFIED', prefix['bytes'], flush=True)
    batch = output/'resume.sftp'
    batch.write_text('reput "'+archive.as_posix()+'" "'+remote+'/original-image.docker.tar.partial"\n',encoding='utf-8')
    try:
        with (output/'sftp-resume.log').open('x') as log:
            subprocess.run(['sftp','-q','-b',str(batch),'-o','BatchMode=yes','-o','ConnectTimeout=12',
                            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','qjhs003'],
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        print('RESUME_COMPLETE_IMPORT_BEGIN', flush=True)
        result = subprocess.run(SSH+[shlex.join(['python3','-c',REMOTE,remote,receipt['archive_sha256'],IMAGE])],
                                capture_output=True,text=True,check=True,timeout=900)
        proof = json.loads(result.stdout)
        (output/'startup-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
        print(json.dumps(proof),flush=True)
    except Exception as error:
        (output/'resume-failure.json').write_text(json.dumps(dict(error_type=type(error).__name__,status='failed_no_retry'))+'\n')
        raise


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output.resolve())
