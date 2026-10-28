"""Record and bound one asset download independently of the SSH connection."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    run = Path(__file__).resolve().parent
    launch = json.loads((run / 'launch.json').read_text())
    source = run / 'download_kimi_weights.py'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == launch['source_sha256']
    # Claim before spawning: duplicate supervisors must not start a second download.
    with (run / 'registration.json').open('x') as stream:
        json.dump(dict(launch, status='pending'), stream)
    command = ['/usr/bin/timeout', '--signal=TERM', '--kill-after=30', '21600',
               '/usr/bin/nice', '-n', '19', '/usr/bin/ionice', '-c', '3',
               sys.executable, str(source), str(run)]
    start = time.time()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        try:
            starttime = Path('/proc', str(process.pid), 'stat').read_text().split(') ', 1)[1].split()[19]
        except (FileNotFoundError, ProcessLookupError):
            if process.poll() is None:
                raise
            starttime = None
        identity = dict(launch, status='spawned', start_epoch=start, deadline_epoch=start + 21600,
                        kill_latest_epoch=start + 21630, timeout_pid=process.pid,
                        supervisor_pid=os.getpid(), command=command,
                        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                        timeout_starttime=starttime)
        with (run / 'registration.json').open('w') as stream:
            json.dump(identity, stream, indent=2)
    except BaseException as error:
        # Popen owns a fresh session; this cleanup cannot target another experiment.
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        with (run / 'exit.json').open('x') as stream:
            json.dump(dict(status='identity publication failed', exception_type=type(error).__name__,
                           end_epoch=time.time(), automatic_restart=False), stream)
        raise
    result = process.wait()
    with (run / 'exit.json').open('x') as stream:
        json.dump(dict(exit_code=result, end_epoch=time.time(),
                       completed_receipt=(run / 'complete.json').exists(),
                       automatic_restart=False), stream)


if __name__ == '__main__':
    main()
