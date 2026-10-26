import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Constrain only the native Store process before exec; preserve memory policy."""
import json
import os
import pathlib
import sys
import time

from cache_prepare_v8 import cpu_list, numa_receipt as inherited_receipt


def numa_receipt():
    result = inherited_receipt()
    result['collector_pid'] = os.getpid()
    result['policy'] = 'memory policy unchanged; snapshot self fields describe the collector; Store PID and thread masks are recorded separately'
    return result


def audit_store_threads(pid, target_cpus):
    proc = pathlib.Path('/proc')/str(pid)
    tasks = []
    disappeared = []
    for path in sorted((proc/'task').iterdir()):
        try:
            status = (path/'status').read_text()
            uid = next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:'))
            assert int(uid) == os.getuid() == 2147
            allowed = next(line.split(':',1)[1].strip() for line in status.splitlines() if line.startswith('Cpus_allowed_list:'))
            assert cpu_list(allowed) <= set(target_cpus)
            tasks.append(dict(tid=int(path.name),cpus_allowed_list=allowed))
        except FileNotFoundError:
            disappeared.append(int(path.name))
    assert tasks
    result = dict(pid=pid,monotonic_ns=time.monotonic_ns(),tasks=tasks,
                  disappeared_during_snapshot=disappeared,status=(proc/'status').read_text(),
                  numa_maps=(proc/'numa_maps').read_text(),collector_context_and_global_numa=numa_receipt(),
                  scope='Store threads only; affinity is not a physical pinned-page NUMA guarantee; node counters are global')
    try:
        result['smaps_rollup'] = (proc/'smaps_rollup').read_text()
    except PermissionError:
        result['smaps_rollup_error'] = 'permission denied; unavailable'
    return result


if __name__ == '__main__':
    root = pathlib.Path('/experiment')
    path = pathlib.Path(sys.argv[1]).resolve()
    assert path.is_relative_to(root) and path.parent.name.startswith('service-bootstrap-')
    assert sys.argv[2:5] == ['/experiment/.venv/bin/python','-u','/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1/run_store_server13.py']
    original = set(os.sched_getaffinity(0))
    sysfs = pathlib.Path('/sys/devices/system/node/node0/cpulist').read_text()
    target = original & cpu_list(sysfs)
    assert target
    before = numa_receipt()
    os.sched_setaffinity(0,target)
    assert set(os.sched_getaffinity(0)) == target
    record = dict(pid=os.getpid(),pid_namespace=pathlib.Path('/proc/self/ns/pid').readlink().as_posix(),
                  start_ticks=pathlib.Path('/proc/self/stat').read_text().rpartition(')')[2].split()[19],
                  boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),monotonic_ns=time.monotonic_ns(),node='node0',
                  prior_cpu_affinity=sorted(original),target_cpu_affinity=sorted(target),
                  sysfs_cpu_list=sysfs,before=before,after=numa_receipt(),
                  scope='only native Store exec and inherited threads; no parent/engine affinity or memory-policy changes')
    assert not path.exists()
    temporary=path.with_suffix('.tmp')
    with temporary.open('x') as stream: json.dump(record,stream,indent=2)
    temporary.replace(path)
    os.execv(sys.argv[2],sys.argv[2:])
