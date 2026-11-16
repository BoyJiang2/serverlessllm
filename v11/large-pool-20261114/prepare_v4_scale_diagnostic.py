"""Stage a V4-only L2 scale diagnostic from the archived S3 runner and inputs."""

import argparse
import asyncio
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


SEQUENCE = "V4ScaleDiag"
BASE_PINS = {
    "repro/v11_run_b2.py": "35445c8231586f18703d86e9a6f86aefecaa633963d253fe4e29ec6933c269f8",
    "repro/v11_sleep_backend.py": "253c9812f0c1e8d3be6ad4e7cf2a75886707941bf943e8977889ca927b027c47",
    "repro/cumem.py": "e90097b2017df0ceeabbd5a09905cf4e80ab6073f3760b3f04a5aae79bf04bc4",
    "repro/sf_admission.py": "078b7db65c57042d1fde4177ec2c7d2eaeb01fe0beb5aefab79a58ab41b025ef",
    "repro/campaign.json": "bc6b5bd095fbe863d3535d28462cdeee1c28b820d989678d444f1d1a24201d36",
    "b2-inputs/v4-input.json": "631038f096b8d509779bdb47b3d087e07be518e13a9193b1421122ef58347300",
    "v4-overlay-mounts.json": "85106d60fb5684addea56197d44d34575bfb1ddbcb4f4309c6e31ee442c15a22",
}
HELPER = "v11_v4_mxfp4_scale_receipt.py"
EXTENSION = "sllm_store.v11_v4_mxfp4_scale_receipt.V11V4ScaleWorkerExtension"

CHECKS = '''
DIAGNOSTIC_SECONDS = 3000
CLEANUP_RESERVE_SECONDS = 480


def execution_window(start, end, now):
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("booking timestamps require explicit offsets")
    if now < start.timestamp() or end.timestamp() - now < DIAGNOSTIC_SECONDS + CLEANUP_RESERVE_SECONDS:
        raise ValueError("require a current booking with 3000 s execution and 480 s cleanup remaining")
    return DIAGNOSTIC_SECONDS


def vram_gate(free, weight_bytes, kv_gib):
    mib = 1024 ** 2
    weight_mib = weight_bytes // 8 // mib
    kv_mib = kv_gib * 1024
    margin_mib = 8192
    required_mib = weight_mib + kv_mib + margin_mib
    min_free_mib = min(free) if free else None
    return dict(free_mib=free, min_free_mib=min_free_mib, required_mib=required_mib,
                weight_mib=weight_mib, kv_budget_mib=kv_mib, headroom_margin_mib=margin_mib,
                passed=len(free) == 8 and min_free_mib >= required_mib)

'''

SAMPLE = '''
SCALE_IDENTITIES = {}


async def scale_sample(backend, filename, phase):
    started_ns = time.monotonic_ns()
    ranks = await backend.engine.collective_rpc(
        'sllm_v4_mxfp4_scale_receipt', kwargs={'phase': phase}, timeout=120)
    finished_ns = time.monotonic_ns()
    write(filename, dict(phase=phase, started_ns=started_ns,
                         finished_ns=finished_ns, ranks=ranks))
    assert len(ranks) == 8 and {item['rank'] for item in ranks} == set(range(8)), phase
    assert all(item['phase'] == phase and item['layer_count'] > 0
               and started_ns <= item['begin_ns'] <= item['end_ns'] <= finished_ns
               for item in ranks), phase
    clocks = {(item['identity']['boot_id'], item['identity']['time_namespace'],
               item['identity']['clock_id']) for item in ranks}
    assert len(clocks) == 1 and next(iter(clocks))[2] == 'CLOCK_MONOTONIC', phase
    current = {
        item['rank']: tuple(item['identity'][key] for key in (
            'boot_id', 'time_namespace', 'pid_namespace', 'pid', 'process_start_ticks'))
        for item in ranks
    }
    if SCALE_IDENTITIES:
        assert current == SCALE_IDENTITIES, f'worker identity changed at {phase}'
    else:
        SCALE_IDENTITIES.update(current)

'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"expected a unique archived source anchor: {old!r}")
    return source.replace(old, new)


def patch_runner(source):
    source = replace(source, "def main():\n", CHECKS + "\ndef main():\n")
    source = replace(source, "choices=('S1', 'S2', 'S3')", f"choices=({SEQUENCE!r},)")
    source = replace(
        source, "    if args.models or args.levels != '2'",
        "    if args.rep != 1 or args.models or args.levels != '2'",
    )
    source = replace(
        source,
        """    if start.utcoffset() is None or end.utcoffset() is None:
        parser.error('booking timestamps require explicit offsets')
    wall = time.time()
    available_s = end.timestamp() - wall - 480
    if wall < start.timestamp() or available_s <= 0:
        parser.error('outside booking execution window')
    deadline_ns = time.monotonic_ns() + int(min(available_s, 14000) * 10**9)""",
        """    try:
        runtime_s = execution_window(start, end, time.time())
    except ValueError as error:
        parser.error(str(error))
    deadline_ns = time.monotonic_ns() + runtime_s * 10**9""",
    )
    source = replace(
        source,
        "        inputs = {key: native_input(run_dir, key, args.kv_cache_gib) for key in MODEL_INPUT}",
        """        assert models == ['D', 'D', 'D']
        assert profiles['v4']['image'] == protocol_doc['expected_v4_image']
        inputs = {'D': run_dir / 'b2-inputs/v4-input.json'}
        frozen_input = (CAMPAIGN / 'v4-diagnostic-input.json').read_bytes()
        assert hashlib.sha256(frozen_input).hexdigest() == protocol_doc['expected_v4_input_sha256']
        inputs['D'].write_bytes(frozen_input)""",
    )
    source = replace(
        source, "        protocol_doc = dict(protocol_hash=hashlib.sha256(config).hexdigest())",
        """        protocol_doc = json.loads(config)
        protocol_doc['protocol_hash'] = hashlib.sha256(config).hexdigest()
        for item in protocol_doc['expected_v4_overlay']:
            raw = pathlib.Path(item['source']).read_bytes()
            assert not item['rw'] and len(raw) == item['size']
            assert hashlib.sha256(raw).hexdigest() == item['sha256'], item['source']""",
    )
    source = replace(
        source, "              guard_deadline_ns=deadline_ns, cleanup_reserve_s=480))",
        """              guard_deadline_ns=deadline_ns, runtime_s=runtime_s,
              cleanup_reserve_s=CLEANUP_RESERVE_SECONDS))""",
    )
    source = replace(
        source, "                         'sf_admission.py', 'campaign.json')",
        f"                         'sf_admission.py', 'campaign.json', '{HELPER}',\n"
        "                         'v4-diagnostic-input.json', 'diagnostic-manifest.json')",
    )
    source = replace(
        source, "    if sink_overlay:\n        mounts.append",
        f"    mounts.append((str(CAMPAIGN / '{HELPER}'),\n"
        f"                   '/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/{HELPER}'))\n"
        "    if sink_overlay:\n        mounts.append",
    )
    source = replace(source, "                    if level == 2 and workers:", "                    if level == 2:")
    source = replace(
        source,
        """                        kv_bytes = (args.kv_cache_gib or 0) * 1024 ** 3
                        required_mib = (WEIGHT_BYTES[key] // 8 + kv_bytes) // 1024 ** 2 + 8192
                        gate = dict(free_mib=free, required_mib=required_mib,
                                    passed=len(free) == 8 and min(free) >= required_mib)""",
        """                        gate = vram_gate(free, WEIGHT_BYTES[key], args.kv_cache_gib)""",
    )
    source = replace(
        source, "                    worker = workers[key]\n                    run_ack = send",
        "                    worker = workers[key]\n"
        "                    assert time.monotonic_ns() < deadline_ns, 'diagnostic execution deadline'\n"
        "                    run_ack = send",
    )
    source = replace(
        source, "                    phase = f'L{level}-{index:02d}-{key}'",
        "                    assert time.monotonic_ns() < deadline_ns, 'diagnostic execution deadline'\n"
        "                    phase = f'L{level}-{index:02d}-{key}'",
    )
    source = replace(
        source, "                    sleep_command_index = counters[key]",
        "                    assert time.monotonic_ns() < deadline_ns, 'diagnostic execution deadline'\n"
        "                    sleep_command_index = counters[key]",
    )
    return source


def patch_backend(source):
    source = replace(source, "async def main():\n", SAMPLE + "\nasync def main():\n")
    source = replace(
        source, "        while True:\n            index, command = next_command()",
        "        await scale_sample(backend, 'scales-initial-ready.json', 'initial_ready')\n"
        "        while True:\n            index, command = next_command()",
    )
    source = replace(
        source, "                sleep_begin_ns = time.monotonic_ns()",
        "                await scale_sample(backend, f'scales-sleep-{index:04d}-before.json', 'before_sleep')\n"
        "                sleep_begin_ns = time.monotonic_ns()",
    )
    source = replace(
        source, """                    old_gpu_kv_release_begin_ns=None, old_gpu_kv_release_end_ns=None))
                rows = await receipt(backend)""",
        """                    old_gpu_kv_release_begin_ns=None, old_gpu_kv_release_end_ns=None))
                await scale_sample(backend, f'scales-sleep-{index:04d}-after.json', 'after_sleep')
                rows = await receipt(backend)""",
    )
    for suffix, phase in (("weights-mapped", "weights_mapped"),
                          ("weights-reloaded", "weights_reloaded")):
        anchor = f"                    write(f'wake-{{index:04d}}-{suffix}.json', await receipt(backend))"
        source = replace(source, anchor, anchor + "\n" +
                         f"                    await scale_sample(backend, f'scales-wake-{{index:04d}}-{suffix}.json', {phase!r})")
    return replace(
        source, "                write(f'wake-{index:04d}-phases.json', timings)",
        "                write(f'wake-{index:04d}-phases.json', timings)\n"
        "                await scale_sample(backend, f'scales-wake-{index:04d}-kv-ready.json', 'kv_ready')",
    )


def build(archive, destination):
    raw = {name: (archive / name).read_bytes() for name in BASE_PINS}
    if {name: sha(value) for name, value in raw.items()} != BASE_PINS:
        raise ValueError("source bytes differ from the archived S3 pins")
    frozen = json.loads(raw["b2-inputs/v4-input.json"])
    frozen["config"]["worker_extension_cls"] = EXTENSION
    expected_input = (json.dumps(frozen, indent=2) + "\n").encode()
    overlay = json.loads(raw["v4-overlay-mounts.json"])
    campaign = json.loads(raw["repro/campaign.json"])
    campaign.update(sequences={SEQUENCE: ["D", "D", "D"]},
                    target_repetitions_per_sequence=1,
                    scope="isolated_v4_scale_diagnostic",
                    expected_v4_image=overlay["image_id"],
                    expected_v4_overlay=overlay["mounts"],
                    expected_v4_input_sha256=sha(expected_input))
    files = {
        "v11_run_b2.py": patch_runner(raw["repro/v11_run_b2.py"].decode()).encode(),
        "v11_sleep_backend.py": patch_backend(raw["repro/v11_sleep_backend.py"].decode()).encode(),
        "cumem.py": raw["repro/cumem.py"],
        "sf_admission.py": raw["repro/sf_admission.py"],
        HELPER: Path(__file__).with_name(HELPER).read_bytes(),
        "campaign.json": (json.dumps(campaign, indent=2) + "\n").encode(),
        "v4-diagnostic-input.json": expected_input,
    }
    for name, content in files.items():
        if name.endswith(".py"):
            compile(content, name, "exec")
    destination.mkdir()
    for name, content in files.items():
        (destination / name).write_bytes(content)
    manifest = dict(source_run=archive.name, source_sha256=BASE_PINS,
                    files_sha256={name: sha(content) for name, content in files.items()},
                    phase_timing="Native operation bounds stay outside metadata RPC; round and command totals include diagnostic work")
    (destination / "diagnostic-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                         encoding="utf-8")


def self_test():
    ns = {}
    exec(CHECKS, ns)
    gate = ns["vram_gate"]
    assert gate([129209] * 8, 877729513472, 16)["required_mib"] == 129209
    assert gate([129209] * 8, 877729513472, 16)["passed"]
    assert not gate([129208] * 8, 877729513472, 16)["passed"]
    assert not gate([143000] * 7, 877729513472, 16)["passed"]
    start = datetime.fromtimestamp(100000, timezone.utc)
    end = datetime.fromtimestamp(103480, timezone.utc)
    assert ns["execution_window"](start, end, 100000) == 3000
    for invalid_now in (99999, 100001):
        try:
            ns["execution_window"](start, end, invalid_now)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid booking passed")

    import time

    seen = []
    values = dict(time=time, write=lambda *args: seen.append(args))
    exec(SAMPLE, values)

    identity_shift = 0

    async def fake_rpc(name, *, kwargs, timeout):
        assert name == "sllm_v4_mxfp4_scale_receipt" and timeout == 120
        instant = time.monotonic_ns()
        return [dict(rank=rank, layer_count=61, phase=kwargs["phase"], begin_ns=instant,
                     end_ns=instant, identity=dict(boot_id="boot", time_namespace="time",
                                                   clock_id="CLOCK_MONOTONIC", pid_namespace="ns",
                                                   pid=rank + 1 + identity_shift,
                                                   process_start_ticks=rank + 100))
                for rank in range(8)]

    backend = SimpleNamespace(engine=SimpleNamespace(collective_rpc=fake_rpc))
    asyncio.run(values["scale_sample"](backend, "sample.json", "before_sleep"))
    assert seen[0][1]["phase"] == "before_sleep" and len(seen[0][1]["ranks"]) == 8
    asyncio.run(values["scale_sample"](backend, "sample2.json", "after_sleep"))
    identity_shift = 1
    try:
        asyncio.run(values["scale_sample"](backend, "sample3.json", "weights_mapped"))
    except AssertionError as error:
        assert "worker identity changed" in str(error)
    else:
        raise AssertionError("changed worker identity passed")
    for source in (CHECKS, SAMPLE):
        assert ast.parse(source).body
    print("validated booking boundaries, eight-card VRAM gate and eight-rank phase receipt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        if args.archive is None or args.destination is None:
            parser.error("provide archive and new destination")
        build(args.archive, args.destination)
