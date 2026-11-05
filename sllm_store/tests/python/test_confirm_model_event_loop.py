"""ConfirmModel must not freeze the gRPC event loop while a rank waits for its copy.

WaitModelInGpu blocks on a condition variable until that rank's GPU copy finishes,
which is minutes for a full model. Called inline from an async handler it stalls
every other rank's Load/Confirm RPC until their clients cancel. This checks the
production handler source and its actual event-loop behaviour; no grpc, torch or
native module is imported.
"""
import ast
import asyncio
import pathlib
import time
import types
import unittest

SERVER = pathlib.Path(__file__).resolve().parents[2] / 'sllm_store/server.py'
WAIT = 'self.storage.wait_model_in_gpu'


class FakeProto:
    DEVICE_TYPE_GPU = 1

    class ConfirmModelResponse:
        def __init__(self, model_path=''):
            self.model_path = model_path


class FakeGrpc:
    class StatusCode:
        INVALID_ARGUMENT = 'INVALID_ARGUMENT'
        UNIMPLEMENTED = 'UNIMPLEMENTED'
        INTERNAL = 'INTERNAL'


def confirm_model():
    tree = ast.parse(SERVER.read_text())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'StorageServicer')
    method = next(n for n in cls.body
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == 'ConfirmModel')
    method.decorator_list = []
    method.returns = None
    namespace = {'asyncio': asyncio, 'grpc': FakeGrpc, 'storage_pb2': FakeProto,
                 'logger': types.SimpleNamespace(error=lambda *a: None, info=lambda *a: None)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 '<ConfirmModel>', 'exec'), namespace)
    return namespace['ConfirmModel'], method


class ConfirmModelEventLoopTest(unittest.TestCase):
    def test_wait_is_offloaded_to_a_thread(self):
        _, method = confirm_model()
        bare = [n for n in ast.walk(method) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == WAIT]
        self.assertEqual(bare, [], 'the native wait must not be called inline')
        offloaded = [n.value for n in ast.walk(method) if isinstance(n, ast.Await)
                     and isinstance(n.value, ast.Call)
                     and ast.unparse(n.value.func) == 'asyncio.to_thread'
                     and n.value.args and ast.unparse(n.value.args[0]) == WAIT]
        self.assertEqual(len(offloaded), 1,
                         'the native wait must be awaited through asyncio.to_thread once')

    def test_event_loop_stays_responsive_during_the_wait(self):
        handler, _ = confirm_model()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(self.scenario(handler))
        finally:
            loop.close()
        self.assertGreaterEqual(result['elapsed'], 0.4, 'the fake wait must actually run')
        self.assertGreaterEqual(result['beats'], 10, 'the event loop must keep running')
        self.assertLess(result['max_gap'], 0.15,
                        'the native wait must not block the event loop')
        self.assertEqual(result['model_path'], 'm')

    async def scenario(self, handler):
        class Storage:
            def wait_model_in_gpu(self, model_path, replica_uuid):
                # time.sleep releases the GIL, like the fixed pybind11 binding.
                time.sleep(0.5)
                return 0

        beats = []
        stop = asyncio.Event()

        async def heartbeat():
            while not stop.is_set():
                beats.append(time.monotonic())
                await asyncio.sleep(0.02)

        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)
        request = types.SimpleNamespace(model_path='m', replica_uuid='r',
                                        target_device_type=FakeProto.DEVICE_TYPE_GPU)
        context = types.SimpleNamespace(set_code=lambda code: None)
        started = time.monotonic()
        response = await handler(types.SimpleNamespace(storage=Storage()), request, context)
        elapsed = time.monotonic() - started
        stop.set()
        await task
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        return {'max_gap': max(gaps) if gaps else float('inf'), 'beats': len(beats),
                'elapsed': elapsed, 'model_path': response.model_path}


if __name__ == '__main__':
    unittest.main()
