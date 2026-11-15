"""Prepare a Sleep backend copy with independent host-call timing receipts."""
from pathlib import Path
import ast
import hashlib
import sys

BACKEND_SHA256 = "4925ef47d3cf3bcada77a129dc3d1f5be89891d90e92f01e0f03a98a1fe88cb9"
RUNNER_SHA256 = "29905f582ce60b5a4640ecb460ba5580aa5085410b9280b34240d4c4d2d74350"
POOL_RUNNER_SHA256 = "4f0801143942b8e34ef804d96e5e2604bf79498ebc09e68f68edc63d56e882f6"
ALLOCATOR_SHA256 = "7bfce5277dc508bcf1f0678ee97e349618418e09316d993c570caa1de78ac992"


def prepare_allocator(path):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ALLOCATOR_SHA256
    source = raw.decode()
    start = source.index("    def sleep(")
    end = source.index("    @contextmanager", start)
    methods = source[start:end]
    operations = {
        "libcudart.cudaMemcpy(cpu_ptr, ptr, size_in_bytes)": "cpu_backup_copy",
        "unmap_and_release(handle)": "gpu_unmap_release",
        "create_and_map(handle)": "gpu_create_map",
        "libcudart.cudaMemcpy(ptr, cpu_ptr, size_in_bytes)": "cpu_restore_copy",
    }
    for operation, phase in operations.items():
        (line,) = [line for line in methods.splitlines() if line.strip() == operation]
        indent = line[:len(line) - len(operation)]
        lines = [
            "_phase_start = _sleep_timing_clock()",
            operation,
            "_phase_end = _sleep_timing_clock()",
            f"_sleep_timing_log({phase!r}, data.tag, handle[1], _phase_start, _phase_end)",
        ]
        methods = methods.replace(line, "\n".join(indent + line for line in lines))
    helper = '''

from time import monotonic_ns as _sleep_timing_clock


def _sleep_timing_log(phase, tag, size, start_ns, end_ns):
    rank = torch.distributed.get_rank() if torch.distributed.is_initialized() else None
    logger.info("sleep_allocator phase=%s tag=%s rank=%s bytes=%s start_ns=%s end_ns=%s",
                phase, tag, rank, size, start_ns, end_ns)
'''
    source = source[:start] + methods + source[end:] + helper
    compile(source, str(path), "exec")
    return source


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


def prepare_runner(path, large_pool):
    raw, pool_raw = path.read_bytes(), large_pool.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RUNNER_SHA256
    assert hashlib.sha256(pool_raw).hexdigest() == POOL_RUNNER_SHA256
    pool = pool_raw.decode()
    helpers = "\n\n".join(ast.get_source_segment(pool, n) for n in ast.parse(pool).body
                         if isinstance(n, ast.FunctionDef)
                         and n.name in {"admit_sf", "start_guard", "residency"})
    source = raw.decode()
    replacements = {
        "import csv\n": "import csv\nfrom datetime import datetime\nimport hashlib\n",
        "\ndef native_input": "\nCAMPAIGN = pathlib.Path(__file__).resolve().parent\n\n" + helpers + "\n\ndef native_input",
        "(str(V11 / 'v11_sleep_backend.py'), '/control/script.py')":
            "(str(CAMPAIGN / 'v11_sleep_backend.py'), '/control/script.py')",
        "    for source, target in mounts:": """    mounts.append((str(CAMPAIGN / 'cumem.py'),
                   '/opt/sllm-v4/lib/python3.12/site-packages/vllm/device_allocator/cumem.py'))
    for source, target in mounts:""",
        "os.environ.get('V11_B2_V4_SINK_OVERLAY') if key == 'D' else None":
            "str(V11 / 'b2-v4-sink-router-overlay/deepseek_v4.py') if key == 'D' else None",
        "'--memory', '1400g'": "'--memory', '1664g' if key == 'K' else '1400g'",
        "    parser.add_argument('--sequence', default=None)":
            "    parser.add_argument('--sequence', required=True, choices=('S1', 'S2', 'S3'))\n"
            "    parser.add_argument('--booking-start', required=True)\n"
            "    parser.add_argument('--booking-end', required=True)",
        "    args = parser.parse_args()": """    args = parser.parse_args()
    if args.models or args.levels != '2' or args.reload_mode != 'checkpoint' or args.kv_cache_gib != 16:
        parser.error('this cohort requires fixed S1/S2/S3, L2 checkpoint and 16 GiB per rank')
    start, end = map(datetime.fromisoformat, (args.booking_start, args.booking_end))
    if start.utcoffset() is None or end.utcoffset() is None:
        parser.error('booking timestamps require explicit offsets')
    wall = time.time()
    available_s = end.timestamp() - wall - 480
    if wall < start.timestamp() or available_s <= 0:
        parser.error('outside booking execution window')
    deadline_ns = time.monotonic_ns() + int(min(available_s, 14000) * 10**9)""",
        "        sequences = json.loads((V11 / 'switch-sequences.json').read_bytes())":
            "        config = (CAMPAIGN / 'campaign.json').read_bytes()\n"
            "        sequences = dict(sequences=[dict(id=k, models=v) for k, v in "
            "json.loads(config)['sequences'].items()])",
        "        protocol_doc = json.loads((V11 / 'protocol-hash.json').read_bytes())":
            "        protocol_doc = dict(protocol_hash=hashlib.sha256(config).hexdigest())",
        "run_dir = V11 / 'runs'": "run_dir = CAMPAIGN / 'runs'",
        "        holder = {'cid': None, 'phase': 'startup'}": """        write(run_dir / 'booking.json', dict(start=args.booking_start, end=args.booking_end,
              guard_deadline_ns=deadline_ns, cleanup_reserve_s=480))
        owned = {}
        holder = {'cid': None, 'phase': 'startup'}""",
        "                        cid, command = worker_container": """                        assert time.monotonic_ns() < deadline_ns
                        if index == 0:
                            (checkpoint,) = [src for src, dst in profiles[MODEL_INPUT[key]]['mounts']
                                             if dst == NATIVE_PATHS[MODEL_INPUT[key]]]
                            admit_sf(pathlib.Path(checkpoint), worker_dir, deadline_ns, run_dir.name)
                        cid, command = worker_container""",
        "                        log = (worker_dir / 'worker.log').open('w')":
            "                        owned[cid] = (None, None)\n"
            "                        owned[cid] = start_guard(cid, run_dir, deadline_ns, phase)\n"
            "                        log = (worker_dir / 'worker.log').open('w')",
        "                    holder['phase'] = phase + ':observation'":
            "                    write(level_dir / f'{phase}-post-sleep-residency.json',\n"
            "                          residency({name: item['cid'] for name, item in workers.items()}))\n"
            "                    holder['phase'] = phase + ':observation'",
        "                    call('docker', 'rm', worker['cid'], timeout=60)": """
                    guard, guard_log = owned[worker['cid']]
                    assert guard.wait(timeout=15) == 0
                    guard_log.close()
                    call('docker', 'rm', worker['cid'], timeout=60)
                    owned.pop(worker['cid'])""".strip("\n"),
        "            sampler.stop.set()": """            for cid, (guard, guard_log) in owned.items():
                try:
                    if row(cid)['State']['Running']:
                        call('docker', 'stop', '--time', '30', cid, timeout=45)
                except Exception as error:
                    failures.setdefault('cleanup', {})[cid] = repr(error)
                if guard_log:
                    guard_log.close()
            sampler.stop.set()""",
    }
    for old, new in replacements.items():
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    compile(source, str(path), "exec")
    return source


if __name__ == "__main__":
    source, destination = map(Path, sys.argv[1:3])
    if len(sys.argv) == 3:
        text = prepare(source)
    elif sys.argv[3] == "--allocator":
        text = prepare_allocator(source)
    else:
        text = prepare_runner(source, Path(sys.argv[3]))
    with destination.open("x", encoding="utf-8", newline="\n") as out:
        out.write(text)
