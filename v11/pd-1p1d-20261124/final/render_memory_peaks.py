from argparse import ArgumentParser
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path


HOST_FIELDS = ('AnonPages', 'SUnreclaim', 'PageTables', 'Shmem', 'Cached')
CGROUP_FIELDS = ('current_bytes', 'anon_plus_shmem_bytes', 'file_minus_shmem_bytes')
GIB = 2**30


def gib(value):
    return f'{value / GIB:.3f}'


def read_ref(ref):
    source = Path(ref['path']).read_bytes()
    if sha256(source).hexdigest() != ref['sha256']:
        raise ValueError(f"Receipt hash differs: {ref['path']}")
    return json.loads(source)


def table(header, rows):
    print('| ' + ' | '.join(header) + ' |')
    print('| ' + ' | '.join(['---'] * len(header)) + ' |')
    for row in rows:
        print('| ' + ' | '.join(row) + ' |')


def report(runs):
    host_rows, container_rows = [], []
    for run in runs:
        if run['case'][2] is not None:
            continue
        baseline, sequence, _ = run['case']
        memory = read_ref(run['memory'])
        if not memory['passed'] or memory['run_id'] != run['run_id']:
            raise ValueError(f"Run memory receipt is incomplete: {run['run_id']}")
        for role, key in (('P', 'prefill'), ('D', 'decode')):
            ledger = memory[key]
            peak = ledger['host_peak']
            stamp = datetime.fromtimestamp(peak['host_wall_ns'] / 1e9, timezone.utc).isoformat(timespec='milliseconds')
            host = peak['host_bytes']
            measured = host['MemTotal'] - host['MemAvailable']
            if measured != ledger['host_peak_bytes'] or peak['errors']:
                raise ValueError(f"{run['run_id']} {role} peak sample differs")
            scope = (baseline, sequence, role)
            host_rows.append((*scope, stamp, gib(measured), *(gib(host[k]) for k in HOST_FIELDS)))
            for container in peak['containers']:
                if container.get('exited'):
                    continue
                container_rows.append((*scope, container['service'],
                                       *(gib(container[k]) for k in CGROUP_FIELDS)))
    print('\n## 正式轮整机峰值采样明细\n')
    print('每行取该节点 MemTotal − MemAvailable 的最高采样点，容器各列来自同一采样点。'
          'P、D 的峰值可发生在不同时间。表中容量以 GiB 表示，注册页在上文单列。\n')
    table(('基线', '序列', '节点', 'UTC 采样点', '整机占用', *HOST_FIELDS), host_rows)
    print()
    table(('基线', '序列', '节点', '活跃容器', 'current', 'anon + shmem', 'file − shmem'), container_rows)


if __name__ == '__main__':
    cli = ArgumentParser(description='Render same-sample host and container peak ledgers.')
    cli.add_argument('evidence', type=Path)
    args = cli.parse_args()
    evidence = json.loads(args.evidence.read_text())
    if not evidence['passed'] or evidence['through'] != 'B1' or evidence['formal_switches'] != 36:
        raise ValueError('Combined report requires 36 audited formal switches')
    report(evidence['runs'])
