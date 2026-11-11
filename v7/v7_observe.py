"""Existing exact-container observer adapted to V7's one-second windows."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def observed_run(args):
    started = time.monotonic_ns()
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        row = dict(exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except (subprocess.TimeoutExpired, OSError) as error:
        row = dict(exit=-1, stdout=None, stderr=repr(error),
                   partial_stdout_repr=repr(getattr(error, 'stdout', None)),
                   partial_stderr_repr=repr(getattr(error, 'stderr', None)))
    return dict(started_ns=started, completed_ns=time.monotonic_ns(), **row)


def collect_sample(root):
    sample = dict(ns=time.monotonic_ns(), containers={},
                  boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  time_namespace=os.readlink('/proc/self/ns/time'))
    for created in root.glob('*-created.json'):
        name = created.name.removesuffix('-created.json')
        if (root/name/'container-removal.json').exists():
            continue
        cid = json.loads(created.read_bytes())['id']
        result = observed_run(['docker', 'inspect', cid])
        if result['exit']:
            sample['containers'][name] = dict(cid=cid, inspection_error=result)
            continue
        row = json.loads(result['stdout'])[0]
        data = dict(cid=cid, state=row['State'], constraints={
            key: row['HostConfig'][key] for key in
            ('NanoCpus', 'Memory', 'DeviceRequests', 'PidMode', 'PidsLimit', 'CpusetCpus')})
        pid = row['State']['Pid']
        if pid:
            proc = Path('/proc', str(pid))
            try:
                relative = next(line.split(':', 2)[2].lstrip('/') for line in
                                (proc/'cgroup').read_text().splitlines() if line.startswith('0:'))
                group = Path('/sys/fs/cgroup')/relative
                data['cgroup_path'] = relative
                data['cgroup'] = {key: (group/key).read_text().strip() for key in
                                  ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.current',
                                   'memory.max', 'memory.events', 'pids.current', 'pids.max')}
                data['status'] = '\n'.join(line for line in (proc/'status').read_text().splitlines()
                                           if line.startswith(('VmRSS:', 'VmLck:', 'VmPin:', 'Threads:')))
            except OSError as error:
                data['process_race'] = repr(error)
        sample['containers'][name] = data
    for key, query in [('gpu', '--query-gpu=index,uuid,memory.used,utilization.gpu'),
                       ('apps', '--query-compute-apps=pid,used_gpu_memory')]:
        sample[key] = observed_run(['nvidia-smi', query, '--format=csv,noheader,nounits'])
    sample['app_cgroups'] = {}
    if sample['apps']['exit'] == 0:
        for line in sample['apps']['stdout'].splitlines():
            pid = line.split(',')[0].strip()
            if pid not in sample['app_cgroups']:
                try:
                    sample['app_cgroups'][pid] = next(row.split(':', 2)[2].lstrip('/') for row in
                        Path('/proc', pid, 'cgroup').read_text().splitlines() if row.startswith('0:'))
                except OSError as error:
                    sample['app_cgroups'][pid] = dict(process_race=repr(error))
    sample['meminfo'] = {line.split(':')[0]: int(line.split()[1])*1024
                         for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith(('MemTotal:', 'MemAvailable:', 'Mlocked:', 'Unevictable:'))}
    sample['completed_ns'] = time.monotonic_ns()
    return sample


def main(root, deadline_ns):
    root.resolve().relative_to(Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7'))
    assert time.monotonic_ns() < deadline_ns <= time.monotonic_ns()+14700*10**9
    reason = 'deadline'
    with (root/'resources.jsonl').open('x', buffering=1) as log:
        with (root/'observer-started.json').open('x') as marker:
            json.dump(dict(pid=os.getpid(), ns=time.monotonic_ns()), marker)
        while time.monotonic_ns() < deadline_ns:
            log.write(json.dumps(collect_sample(root))+'\n')
            if (root/'observation-stop.json').exists() or (root/'result.json').exists():
                reason = 'stop_marker'
                break
            time.sleep(1)
    with (root/'observer-complete.json').open('x') as marker:
        json.dump(dict(ns=time.monotonic_ns(), reason=reason), marker)
    return 0 if reason == 'stop_marker' else 1


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1]), int(sys.argv[2])))
