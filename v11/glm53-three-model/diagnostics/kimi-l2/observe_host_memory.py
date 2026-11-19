"""Sample host/worker memory on an already-running diagnostic."""
import json
import re
import sys
import time
from pathlib import Path

run, cid, controller = Path(sys.argv[1]), sys.argv[2], Path('/proc') / sys.argv[3]
assert run.is_dir() and re.fullmatch(r'[0-9a-f]{64}', cid) and controller.is_dir()
group = Path('/sys/fs/cgroup/system.slice') / f'docker-{cid}.scope'
assert group.is_dir()
fields = {'anon', 'file', 'shmem', 'active_file', 'inactive_file', 'file_mapped', 'file_dirty',
          'file_writeback', 'workingset_refault_file', 'pgmajfault', 'slab_unreclaimable'}
until = time.monotonic() + 3200
with (run / 'host-memory-live.jsonl').open('x') as stream:
    while time.monotonic() < until and controller.is_dir():
        begin = time.monotonic_ns()
        host = Path('/proc/meminfo').read_text()
        try:
            stats = {key: int(value) for line in (group / 'memory.stat').read_text().splitlines()
                     for key, value in (line.split(),) if key in fields}
            worker = dict(stats=stats, current_bytes=int((group / 'memory.current').read_text()),
                          events=(group / 'memory.events').read_text())
        except FileNotFoundError:
            worker = None
        stream.write(json.dumps(dict(begin_ns=begin, end_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
                                     host_meminfo=host, worker=worker)) + '\n')
        stream.flush()
        time.sleep(2)
