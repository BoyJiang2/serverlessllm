"""At most two missing-file rereads; the parent enforces one 600s process budget."""
import gzip
import hashlib
import json
import os
import pathlib
import time

from cache_prepare_v6 import read_hot, wait_read_idle
from cache_prepare_v8 import residency, LSB


def scan_files(paths, base, directory, started_ns):
    rows = []
    for path in paths:
        if time.monotonic_ns()-started_ns >= 600*10**9:
            raise TimeoutError('whole cache preparation budget exhausted')
        flags = residency(path)
        relative = path.relative_to(base).as_posix()
        vector = directory/(relative+'.mincore.gz')
        vector.parent.mkdir(parents=True,exist_ok=True)
        vector.write_bytes(gzip.compress(flags,compresslevel=1,mtime=0))
        rows.append(dict(file=relative,source_path=str(path),bytes=path.stat().st_size,
                         pages=len(flags),resident_pages=flags.translate(LSB).count(b'\x01'),
                         page_size=os.sysconf('SC_PAGE_SIZE'),vector=vector.relative_to(directory.parent).as_posix(),
                         vector_sha256=hashlib.sha256(flags).hexdigest()))
    return rows


def repair_hot(paths, base, dest, rows, reader_cpus, started_ns, deadline):
    whitelist = {str(path):path for path in paths}
    assert len(whitelist) == len(paths)
    history = []
    for number in (1,2):
        assert {row['source_path'] for row in rows} == set(whitelist)
        missing = [row for row in rows if row['resident_pages'] != row['pages']]
        if not missing:
            return rows,history
        if time.monotonic_ns()-started_ns >= 600*10**9:
            raise TimeoutError('whole cache preparation budget exhausted')
        selected = [whitelist[row['source_path']] for row in missing]
        assert all(path.resolve().is_relative_to(base.resolve()) and path.stat().st_uid == os.getuid() for path in selected)
        directory = dest/f'repair_{number}'
        directory.mkdir(exist_ok=False)
        record = dict(pass_number=number,begin_ns=time.monotonic_ns(),
                      selected_files=[str(path) for path in selected],
                      selected_bytes=sum(path.stat().st_size for path in selected),
                      prior_missing=missing)
        (directory/'selection.json').write_text(json.dumps(record,indent=2))
        reads = read_hot(selected)
        assert {row['file'] for row in reads} == {str(path) for path in selected}
        assert all(row['bytes_read'] == whitelist[row['file']].stat().st_size for row in reads)
        assert all(row['cpu_begin'] in reader_cpus and row['cpu_end'] in reader_cpus for row in reads)
        record.update(reads=reads,read_threads_joined_ns=time.monotonic_ns())
        (directory/'reads.json').write_text(json.dumps(record,indent=2))
        remaining = 600-(time.monotonic_ns()-started_ns)/1e9
        if remaining <= 0:
            raise TimeoutError('whole cache preparation budget exhausted')
        record['read_idle_receipt'] = wait_read_idle(min(deadline,time.time()+remaining+120))
        record['verification_begin_ns'] = time.monotonic_ns()
        rows = scan_files(paths,base,directory,started_ns)
        record.update(verification_complete_ns=time.monotonic_ns(),files=rows,
                      cache_condition_passed=all(row['resident_pages']==row['pages'] for row in rows))
        (directory/'summary.json').write_text(json.dumps(record,indent=2))
        history.append(record)
    assert all(row['resident_pages']==row['pages'] for row in rows), 'hot condition failed after two bounded repair passes'
    return rows,history
