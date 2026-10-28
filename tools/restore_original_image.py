"""Startup-only exact image restoration. No anchor or background process."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

from docker_plan import IMAGE, ROOT

BACKUP = Path(str(ROOT))/'recovery/image-relay-20261028T091001Z/original-image.docker.tar'
ARCHIVE_SHA = '15fc28ff7074bb1285fb80931245e6ed3671e943975060849d230fc3bd74c48b'


def ensure_image():
    begin = time.monotonic_ns()
    record = dict(image_id=IMAGE,backup=str(BACKUP),begin_ns=begin,bytes_hashed=0,
                  anchor_created=False,load_performed=False)
    inspected = subprocess.run(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],
                               capture_output=True,text=True,timeout=15)
    if inspected.returncode == 0:
        if inspected.stdout.strip() != IMAGE:
            raise RuntimeError('unexpected image identity')
        record.update(status='existing exact image verified',end_ns=time.monotonic_ns())
        return record
    if 'No such image' not in inspected.stderr:
        raise RuntimeError('image inspect failed without confirmed absence; no import attempted')
    if BACKUP.resolve(strict=True) != BACKUP or not BACKUP.is_file():
        raise ValueError('backup path changed or is not a regular file')
    sha = hashlib.sha256()
    with BACKUP.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):
            sha.update(block); record['bytes_hashed'] += len(block)
    if sha.hexdigest() != ARCHIVE_SHA:
        raise ValueError('backup SHA256 mismatch; no import attempted')
    record['archive_sha256'] = ARCHIVE_SHA
    record['load_begin_ns'] = time.monotonic_ns()
    loaded = subprocess.run(['docker','image','load','--input',str(BACKUP)],
                            capture_output=True,text=True,timeout=600)
    if loaded.returncode:
        raise RuntimeError('Docker load failed; inspect actual image state before retry')
    observed = subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True,timeout=15).strip()
    if observed != IMAGE:
        raise RuntimeError('image identity differs after load')
    record.update(status='backup restored and exact image verified',load_performed=True,end_ns=time.monotonic_ns())
    return record


def restore_with_receipt(path):
    # Exclusive reservation preserves failed initialization rather than overwriting it.
    begin = time.monotonic_ns()
    with path.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(dict(status='initializing',begin_ns=begin))+'\n')
    try:
        record = ensure_image()
    except Exception as error:
        record = dict(status='failed; actual image state must be rechecked',error_type=type(error).__name__,
                      begin_ns=begin,end_ns=time.monotonic_ns(),anchor_created=False)
        path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        raise
    record['begin_ns'] = begin
    path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    return record
