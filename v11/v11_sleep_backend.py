"""V11 B2 backend: one resident worker driven by command files.

The worker loads natively (no SLLM Store), runs the frozen requests on demand,
and can sleep/wake in place so the process (and its PID) survives every cycle.

Commands (written by the host controller, one file per step):
  {"op": "run",  "phase": "c1", "name": "K"}
  {"op": "sleep", "level": 1|2}
  {"op": "wake",  "level": 1|2}
  {"op": "receipt"}
  {"op": "shutdown"}
Each command is acknowledged with ack-<n>.json containing timestamps and, where
applicable, the eight-rank worker receipts.
"""
import asyncio
import json
import os
import pathlib
import sys
import time
import traceback

sys.path.insert(0, '/control')
import v7_lib as lib
import v11_lib as runner

O = pathlib.Path('/output')
write = lib.write
SEQ = {'value': 0}


def next_command():
    index = SEQ['value']
    path = O / f'cmd-{index:04d}.json'
    deadline = time.monotonic() + 7200
    while not path.exists():
        assert time.monotonic() < deadline, 'command wait timed out'
        time.sleep(.2)
    SEQ['value'] = index + 1
    return index, json.loads(path.read_bytes())


def ack(index, payload):
    write(f'ack-{index:04d}.json', payload)


async def receipt(backend):
    started = time.monotonic_ns()
    rows = await backend.engine.collective_rpc('sllm_v7_sleep_receipt', timeout=120)
    return dict(started_ns=started, received_ns=time.monotonic_ns(), ranks=rows)


async def main():
    import sllm_store.server  # frozen native extension preload
    from sllm.backends.vllm_backend import VllmBackend
    from sllm_store.event_trace import process_identity
    import torch
    import vllm

    data = json.loads(pathlib.Path('/control/input.json').read_bytes())
    backend = VllmBackend('shared-' + data['name'], data['config'])
    write('constructed.json', dict(pid=os.getpid(), ns=time.monotonic_ns(),
                                   clock_identity=process_identity(),
                                   torch=torch.__version__, vllm=vllm.__version__))
    initialized = False
    try:
        await backend.init_backend()
        initialized = True
        write('ready.json', dict(ns=time.monotonic_ns(), pid=os.getpid()))
        while True:
            index, command = next_command()
            op = command['op']
            started = time.monotonic_ns()
            if op == 'run':
                await runner.run_requests_streaming(backend, data, command['phase'],
                                                    command.get('record_only', False))
                checks = {}
                for item in data['requests']:
                    path = O / f"{command['phase']}-{item['name']}-checks.json"
                    checks[item['name']] = json.loads(path.read_bytes())
                ack(index, dict(op=op, started_ns=started, finished_ns=time.monotonic_ns(),
                                checks=checks))
            elif op == 'sleep':
                level = int(command['level'])
                await backend.engine.sleep(level=level, mode='wait')
                rows = await receipt(backend)
                ack(index, dict(op=op, level=level, started_ns=started,
                                finished_ns=time.monotonic_ns(),
                                sleeping=await backend.engine.is_sleeping(), receipt=rows))
            elif op == 'wake':
                level = int(command['level'])
                if level == 1:
                    await backend.engine.wake_up()
                    reload_ns = None
                else:
                    await backend.engine.wake_up(tags=['weights'])
                    write(f'wake-{index:04d}-weights-mapped.json', await receipt(backend))
                    reload_started = time.monotonic_ns()
                    checkpoint_format = command.get('reload_mode', 'checkpoint') == 'checkpoint'
                    await backend.engine.collective_rpc(
                        'reload_weights', kwargs={'is_checkpoint_format': checkpoint_format})
                    reload_ns = time.monotonic_ns() - reload_started
                    write(f'wake-{index:04d}-weights-reloaded.json', await receipt(backend))
                    await backend.engine.wake_up(tags=['kv_cache'])
                rows = await receipt(backend)
                ack(index, dict(op=op, level=level, started_ns=started,
                                finished_ns=time.monotonic_ns(),
                                sleeping=await backend.engine.is_sleeping(),
                                reload_ns=reload_ns, receipt=rows))
            elif op == 'receipt':
                rows = await receipt(backend)
                ack(index, dict(op=op, started_ns=started, finished_ns=time.monotonic_ns(),
                                receipt=rows))
            elif op == 'shutdown':
                ack(index, dict(op=op, started_ns=started, finished_ns=time.monotonic_ns()))
                break
            else:
                raise ValueError(f'unknown op {op!r}')
    finally:
        if initialized:
            write('backend-shutdown-begin.json', dict(ns=time.monotonic_ns()))
            await backend.shutdown()
            write('normal-backend-shutdown.json', dict(ns=time.monotonic_ns()))
    write('backend-passed.json', dict(commands=SEQ['value'], normal_shutdown=True))


if __name__ == '__main__':
    try:
        write('waiting.json', dict(pid=os.getpid(), ns=time.monotonic_ns()))
        asyncio.run(main())
    except BaseException as error:
        write('failure.json', dict(error=repr(error), traceback=traceback.format_exc(),
                                   ns=time.monotonic_ns()))
        raise
