"""Launch the frozen GLM v3 conversion as soon as qjhs3 GPUs are idle.

The node is shared with another session that holds the 19:00-21:00 +08 booking
and starts native P8/P9 runs without notice. This watcher never touches that
work: it only observes nvidia-smi, and the freeze step re-samples 60 s of idle
evidence before any container is created.
"""
import fcntl
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
BASELINE_MIB = 16
WAIT_SECONDS = 90 * 60
LOG = V7 / 'glm-autolaunch.log'


def log(message):
    stamp = datetime.now(timezone.utc).strftime('%H:%M:%SZ')
    with LOG.open('a') as stream:
        stream.write(f'{stamp} {message}\n')


def gpu():
    gpu_rows = subprocess.run(
        ['nvidia-smi', '--query-gpu=index,memory.used', '--format=csv,noheader,nounits'],
        capture_output=True, text=True, timeout=30).stdout
    apps = subprocess.run(
        ['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'],
        capture_output=True, text=True, timeout=30).stdout
    memory = [int(row.split(',')[1]) for row in gpu_rows.splitlines() if row.strip()]
    return memory, apps.strip()


def idle():
    memory, apps = gpu()
    return len(memory) == 8 and not apps and max(memory) <= BASELINE_MIB


def launch():
    run_id = 'v7-glm-rebuild-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if (V7 / 'runs' / run_id).exists():
        return None
    log_path = V7 / (run_id + '-controller.log')
    with log_path.open('x') as stream:
        process = subprocess.Popen(
            ['python3', str(V7 / 'v7_glm_convert.py'), run_id],
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    protocol = V7 / 'glm-conversion-protocol.json'
    import hashlib
    record = dict(
        run_id=run_id, pid=process.pid, wall_ns=time.time_ns(),
        log=str(log_path),
        fix='guard lease 1180 s; container 1600 GiB; eight-rank barrier; fadvise drops',
        retry_of='v7-glm-rebuild-20261110T113029Z',
        protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
        launched_by='v7_glm_autolaunch.py after observed idle GPUs',
    )
    (V7 / (run_id + '-launch.json')).write_text(json.dumps(record, indent=2) + '\n')
    return record


def main():
    with (V7 / 'glm-autolaunch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < stop:
            if idle():
                log('idle observed; freezing admission and protocol')
                result = subprocess.run(
                    ['python3', str(V7 / 'v7_freeze_glm_v3.py')],
                    capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    record = launch()
                    log('launched ' + json.dumps(record))
                    if record:
                        print(json.dumps(record))
                        return 0
                else:
                    log('freeze aborted: ' + result.stderr.strip().splitlines()[-1:][0]
                        if result.stderr.strip() else 'freeze aborted')
            time.sleep(15)
        log('watcher window expired without a launch')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
