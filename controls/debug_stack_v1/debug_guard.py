import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Independent host guard for debug stacks: stops and removes debug containers whose deadline has passed.

Runs from a root systemd timer (install_debug_guard.sh), outside every container it guards and independent of
SSH sessions and of the in-container timeout. It only ever touches containers labelled
sllm.purpose=DEBUG-UNREVIEWED whose sllm.run_id is a debug identity; formal and foreign containers are never due.
Each completed sweep writes a heartbeat; debug GPU runs refuse to start unless it is fresh.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import time

PURPOSE = 'DEBUG-UNREVIEWED'
RUN = re.compile(r'sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}')
STOP_SECONDS = 320  # the worker's teardown needs the Store's 240 s SIGINT grace
MAX_AGE_SECONDS = 7200+900  # containers without a deadline label: longest debug lifetime plus margin
LOG = pathlib.Path('/var/log/sllm-debug-guard.jsonl')
HEARTBEAT = pathlib.Path('/var/lib/sllm-debug-guard/heartbeat.json')


def docker(*args, timeout=60):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)


def created_unix(created):
    # Docker reports nanoseconds; datetime parses at most microseconds.
    stamp, _, fraction = created.rstrip('Z').partition('.')
    return datetime.datetime.fromisoformat(stamp).replace(tzinfo=datetime.timezone.utc).timestamp()+float('0.'+(fraction or '0'))


def due(info, now):
    labels = info['Config']['Labels'] or {}
    if labels.get('sllm.purpose') != PURPOSE or not RUN.fullmatch(labels.get('sllm.run_id', '')):
        return False, 'not a debug-stack container'
    deadline = labels.get('sllm.deadline_unix', '')
    if deadline.isdigit():
        return now >= int(deadline), 'deadline label'
    return now-created_unix(info['Created']) >= MAX_AGE_SECONDS, 'no deadline label; maximum age'


def candidates():
    listing = docker('ps', '-aq', '--no-trunc', '--filter', 'label=sllm.purpose='+PURPOSE)
    if listing.returncode:  # an unreachable daemon must fail the service, not look like "nothing due"
        raise RuntimeError('docker ps failed: '+listing.stderr.strip())
    rows = []
    for cid in listing.stdout.split():
        result = docker('inspect', cid)
        if result.returncode == 0:  # a container that vanished meanwhile needs nothing
            rows.append(json.loads(result.stdout)[0])
    return rows


def sweep(dry_run, now=None):
    now = time.time() if now is None else now
    actions = []
    for info in candidates():
        expired, reason = due(info, now)
        if not expired:
            continue
        labels = info['Config']['Labels'] or {}
        row = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), container_id=info['Id'], name=info['Name'],
                   run_id=labels.get('sllm.run_id'), reason=reason, state=info['State']['Status'], dry_run=dry_run)
        if not dry_run:
            if info['State']['Running']:
                try:
                    row['stop_returncode'] = docker('stop', '-t', str(STOP_SECONDS), info['Id'], timeout=STOP_SECONDS+60).returncode
                except subprocess.TimeoutExpired:
                    row['stop_returncode'] = 'timeout'
            row['removed'] = docker('rm', '-f', info['Id']).returncode == 0
        actions.append(row)
    if actions and not dry_run:
        with LOG.open('a') as stream:
            for row in actions:
                stream.write(json.dumps(row)+'\n')
    if not dry_run:  # proof for debug_stack up that the guard runs and reaches Docker
        partial = HEARTBEAT.with_name(HEARTBEAT.name+'.partial')
        partial.write_text(json.dumps(dict(unix=time.time(), actions=len(actions)))+'\n')
        partial.replace(HEARTBEAT)
    return actions


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Remove expired debug-stack containers.')
    parser.add_argument('--dry-run', action='store_true', help='only report what is due; any user in the docker group')
    print(json.dumps(sweep(parser.parse_args().dry_run), indent=2))
