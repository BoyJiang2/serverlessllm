"""Prepare a Sleep backend copy with independent host-call timing receipts."""
from pathlib import Path
import hashlib
import sys

BACKEND_SHA256 = "4925ef47d3cf3bcada77a129dc3d1f5be89891d90e92f01e0f03a98a1fe88cb9"


def prepare(path):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BACKEND_SHA256
    source = raw.decode()
    replacements = {
        "                await backend.engine.sleep(level=level, mode='wait')": """
                sleep_begin_ns = time.monotonic_ns()
                await backend.engine.sleep(level=level, mode='wait')
                sleep_end_ns = time.monotonic_ns()
                write(f'sleep-{index:04d}-phases.json', dict(
                    sleep_begin_ns=sleep_begin_ns, sleep_end_ns=sleep_end_ns,
                    old_gpu_kv_release_begin_ns=None, old_gpu_kv_release_end_ns=None))
""".strip("\n"),
        "                if level == 1:\n                    await backend.engine.wake_up()": """
                timings = {}
                if level == 1:
                    timings['combined_wake_begin_ns'] = time.monotonic_ns()
                    await backend.engine.wake_up()
                    timings['combined_wake_end_ns'] = time.monotonic_ns()
""".strip("\n"),
        "                    await backend.engine.wake_up(tags=['weights'])": """
                    timings['weight_map_begin_ns'] = time.monotonic_ns()
                    await backend.engine.wake_up(tags=['weights'])
                    timings['weight_map_end_ns'] = time.monotonic_ns()
""".strip("\n"),
        "                    reload_ns = time.monotonic_ns() - reload_started": """
                    timings['checkpoint_reload_begin_ns'] = reload_started
                    timings['checkpoint_reload_end_ns'] = time.monotonic_ns()
                    reload_ns = timings['checkpoint_reload_end_ns'] - reload_started
""".strip("\n"),
        "                    await backend.engine.wake_up(tags=['kv_cache'])": """
                    timings['kv_wake_begin_ns'] = time.monotonic_ns()
                    await backend.engine.wake_up(tags=['kv_cache'])
                    timings['kv_ready_ns'] = time.monotonic_ns()
                write(f'wake-{index:04d}-phases.json', timings)
""".strip("\n"),
    }
    for old, new in replacements.items():
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    compile(source, str(path), "exec")
    return source


if __name__ == "__main__":
    source, destination = map(Path, sys.argv[1:])
    with destination.open("x", encoding="utf-8", newline="\n") as out:
        out.write(prepare(source))
