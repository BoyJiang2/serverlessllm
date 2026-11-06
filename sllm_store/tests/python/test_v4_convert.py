"""Grouped conversion control flow and pinned RPC AST; no inference imports."""
import ast
from collections import deque
from concurrent.futures import Future, InvalidStateError
from contextlib import nullcontext, redirect_stdout, suppress
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import types
from typing import Any, Callable
import unittest
from unittest import mock

from sllm_store import v4_convert as convert
from sllm_store import v4_export_worker as worker
from sllm_store import v4_grouped_state as grouped
import test_v4_grouped_state as fixtures
from test_v4_state import identity

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PINS = json.loads((Path(__file__).resolve().parents[2] / "sllm_store/v4_source_pins.json").read_text())


def source_tree(path):
    data = (FIXTURES / ("vllm0201_rpc_" + path.replace("/", "__") + ".txt")).read_bytes()
    if hashlib.sha256(data).hexdigest() != PINS[path]:
        raise AssertionError("RPC fixture differs from the selected source pin")
    return ast.parse(data)


def source_method(path, cls, method, namespace=None):
    tree = source_tree(path)
    parent = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    node = next(n for n in parent.body if isinstance(n, ast.FunctionDef) and n.name == method)
    node.decorator_list, node.returns = [], None
    for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
        arg.annotation = None
    if node.args.vararg:
        node.args.vararg.annotation = None
    if node.args.kwarg:
        node.args.kwarg.annotation = None
    namespace = {} if namespace is None else namespace
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), path, "exec"), namespace)
    return namespace[method]


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GroupedStateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.storage = self.fixture.root
        self.root = self.storage / "converted"
        self.fixture.root = self.root
        self.model = self.storage / "model"
        self.model.mkdir()
        (self.model / "config.json").write_bytes((FIXTURES / "v4_config.json").read_bytes())
        self.events, self.engine = [], None
        self.rpc_error, self.shutdown_error, self.construct_error = None, None, None
        self.results_edit = lambda results: results
        self.shutdown_edit = lambda: None
        self.addCleanup(convert._EXPORT_CONTEXTS.clear)
        self.env = mock.patch.dict(os.environ, STORAGE_PATH=str(self.storage),
                                   VLLM_ALLOW_INSECURE_SERIALIZATION="0")
        self.env.start()
        self.addCleanup(self.env.stop)
        def factory(**config):
            self.events.append("construct")
            self.config = config
            self.fixture.populate()
            if self.construct_error:
                raise self.construct_error
            def rpc(method, timeout=None, args=()):
                self.events.append("rpc")
                self.assertEqual(method, worker.RPC_METHOD)
                self.assertEqual(timeout, 1200)
                self.assertEqual(args, (identity(),))
                if self.rpc_error:
                    raise self.rpc_error
                return self.results_edit(self.fixture.results)
            def shutdown(timeout=None):
                self.events.append("shutdown")
                self.assertEqual(timeout, 60)
                self.shutdown_edit()
                if self.shutdown_error:
                    raise self.shutdown_error
            self.engine = types.SimpleNamespace(collective_rpc=rpc,
                llm_engine=types.SimpleNamespace(engine_core=types.SimpleNamespace(shutdown=shutdown)))
            return self.engine
        patch = mock.patch.dict(sys.modules, vllm=types.SimpleNamespace(LLM=factory))
        patch.start()
        self.addCleanup(patch.stop)

    def run_conversion(self):
        return convert.convert_grouped_store(str(self.model), str(self.root), identity())

    def test_publishes_only_after_eight_receipts_shutdown_and_full_hash_validation(self):
        original = grouped.publish_grouped_complete
        def publish(*args):
            self.assertEqual(self.events, ["construct", "rpc", "shutdown"])
            self.events.append("publish")
            return original(*args)
        with mock.patch.object(convert, "publish_grouped_complete", side_effect=publish):
            complete = self.run_conversion()
        self.assertEqual(len(complete["workers"]), 8)
        self.assertEqual(grouped.validate_grouped_complete(self.root, identity(), True), complete)
        self.assertFalse(convert.export_contexts())
        self.assertEqual(self.config["worker_extension_cls"], worker.WORKER_EXTENSION)
        self.assertEqual(self.config["distributed_executor_backend"], "mp")
        self.assertEqual(self.config["max_model_len"], 2048)
        self.assertEqual(self.config["tensor_parallel_size"], 8)
        self.assertEqual(self.config["revision"], convert.REVISION)
        self.assertEqual(self.config["tokenizer_revision"], convert.REVISION)
        self.assertEqual(self.config["kv_cache_dtype"], "fp8")
        self.assertEqual(self.config["attention_config"], {"use_fp4_indexer_cache": False})
        self.assertNotIn("num_gpu_blocks_override", self.config)
        self.assertFalse((self.root / "CONVERSION_FAILED.json").exists())

    def test_receipts_are_durable_before_shutdown_and_publication(self):
        def inspect_receipts():
            receipt = json.loads((self.root / "EXPORT_RECEIPTS.json").read_text())
            self.assertEqual(receipt["identity"], identity())
            self.assertEqual(receipt["workers"], self.fixture.results)
            self.assertFalse((self.root / "ENGINE_SHUTDOWN.json").exists())
        self.shutdown_edit = inspect_receipts
        def stop_before_publication(*args):
            self.assertTrue((self.root / "ENGINE_SHUTDOWN.json").exists())
            raise KeyboardInterrupt()
        with mock.patch.object(convert, "publish_grouped_complete", side_effect=stop_before_publication):
            with self.assertRaises(KeyboardInterrupt):
                self.run_conversion()
        self.assertFalse((self.root / "COMPLETE.json").exists())
        self.assertTrue((self.root / "CONVERSION_FAILED.json").exists())

    def test_receipt_write_failure_still_shuts_down_and_blocks_publication(self):
        original = convert.write_once
        def write(path, document):
            if path.name == "EXPORT_RECEIPTS.json":
                raise OSError("receipt disk failed")
            return original(path, document)
        with mock.patch.object(convert, "write_once", side_effect=write):
            with self.assertRaisesRegex(OSError, "receipt disk failed"):
                self.run_conversion()
        self.assertEqual(self.events, ["construct", "rpc", "shutdown"])
        self.assertFalse((self.root / "ENGINE_SHUTDOWN.json").exists())
        self.assertFalse((self.root / "COMPLETE.json").exists())
        self.assertFalse(convert.export_contexts()[0]["engine_retained"])

    def test_shutdown_record_failure_does_not_claim_shutdown_itself_failed(self):
        original = convert.write_once
        def write(path, document):
            if path.name == "ENGINE_SHUTDOWN.json":
                raise OSError("shutdown receipt disk failed")
            return original(path, document)
        with mock.patch.object(convert, "write_once", side_effect=write):
            with self.assertRaisesRegex(OSError, "shutdown receipt disk failed"):
                self.run_conversion()
        self.assertEqual(self.events.count("shutdown"), 1)
        failure = json.loads((self.root / "CONVERSION_FAILED.json").read_text())
        self.assertEqual(failure["stage"], "recording_shutdown")
        self.assertIsNone(failure["shutdown_error"])
        self.assertFalse(failure["engine_retained"])
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_timeout_retains_failure_and_never_substitutes_saved_rank_files(self):
        self.rpc_error = TimeoutError("rank timed out")
        with self.assertRaises(TimeoutError):
            self.run_conversion()
        self.assertTrue(all((self.root / f"rank_{n}/export_done.json").exists() for n in range(8)))
        self.assertFalse((self.root / "COMPLETE.json").exists())
        self.assertEqual(self.events, ["construct", "rpc", "shutdown"])
        failure = json.loads((self.root / "CONVERSION_FAILED.json").read_text())
        self.assertEqual((failure["stage"], failure["error"]), ("collecting", "TimeoutError"))
        self.assertFalse(failure["engine_retained"])

    def test_missing_duplicate_or_foreign_worker_results_cannot_publish(self):
        for edit in (lambda r: r[:-1], lambda r: r[:-1] + [r[0]],
                     lambda r: [dict(v, identity={}) if i == 3 else v for i, v in enumerate(r)]):
            with self.subTest(edit=edit):
                # Each attempted conversion gets its own directory and failure evidence.
                self.root = self.storage / ("converted-" + str(len(convert.export_contexts())))
                self.fixture.root = self.root
                self.results_edit = edit
                with self.assertRaises(ValueError):
                    self.run_conversion()
                self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_shutdown_failure_retains_engine_and_blocks_complete(self):
        self.shutdown_error = RuntimeError("workers still active")
        with self.assertRaisesRegex(RuntimeError, "workers still active"):
            self.run_conversion()
        self.assertFalse((self.root / "COMPLETE.json").exists())
        self.assertTrue((self.root / "EXPORT_RECEIPTS.json").exists())
        self.assertFalse((self.root / "ENGINE_SHUTDOWN.json").exists())
        context = convert.export_contexts()[0]
        self.assertTrue(context["engine_retained"])
        self.assertEqual(context["shutdown_error"], "RuntimeError")
        self.assertEqual(self.events.count("shutdown"), 1)

    def test_primary_rpc_error_is_preserved_if_shutdown_also_fails(self):
        self.rpc_error, self.shutdown_error = TimeoutError("primary"), RuntimeError("cleanup")
        with self.assertRaisesRegex(TimeoutError, "primary"):
            self.run_conversion()
        context = convert.export_contexts()[0]
        self.assertEqual((context["error"], context["shutdown_error"]), ("TimeoutError", "RuntimeError"))
        self.assertTrue(context["engine_retained"])

    def test_constructor_failure_does_not_infer_success_from_all_files(self):
        self.construct_error = RuntimeError("warmup failed")
        with self.assertRaisesRegex(RuntimeError, "warmup failed"):
            self.run_conversion()
        self.assertEqual(self.events, ["construct"])
        self.assertFalse((self.root / "COMPLETE.json").exists())
        self.assertEqual(convert.export_contexts()[0]["stage"], "constructing")

    def test_payload_corruption_after_receipts_is_rejected_after_shutdown(self):
        def corrupt():
            path = self.root / "groups/expert_0003/rank_5/tensor.data_0"
            path.write_bytes(b"x" * path.stat().st_size)
        self.shutdown_edit = corrupt
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.run_conversion()
        self.assertFalse((self.root / "COMPLETE.json").exists())
        self.assertEqual(convert.export_contexts()[0]["stage"], "publishing")

    def test_existing_success_or_partial_directory_is_never_overwritten(self):
        self.run_conversion()
        original = (self.root / "COMPLETE.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_conversion()
        self.assertEqual((self.root / "COMPLETE.json").read_bytes(), original)
        self.assertEqual(self.events.count("construct"), 1)

    def test_insecure_settings_wrong_config_and_store_escape_fail_before_claim(self):
        with mock.patch.dict(os.environ, VLLM_ALLOW_INSECURE_SERIALIZATION="1"):
            with self.assertRaisesRegex(ValueError, "secure"):
                self.run_conversion()
        with self.assertRaises(ValueError):
            convert.export_engine_config(self.model, self.storage.parent / "escape", identity())
        (self.model / "config.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "config"):
            self.run_conversion()
        self.assertFalse(self.events)
        self.assertFalse(self.root.exists())

    def test_plan_mode_has_no_engine_import_or_store_claim(self):
        path = self.storage / "identity.json"
        path.write_text(json.dumps(identity()))
        out = io.StringIO()
        with mock.patch.dict(sys.modules, vllm=None), mock.patch.object(sys, "argv", [
                "v4_convert", "--model", str(self.model), "--store", str(self.root),
                "--identity", str(path), "--plan"]), redirect_stdout(out):
            convert.main()
        result = json.loads(out.getvalue())
        self.assertEqual(result["load_format"], "sllm_v4_grouped_export")
        self.assertFalse(self.root.exists())
        self.assertFalse(self.events)

    def test_failed_evidence_write_retains_the_primary_error_and_context(self):
        self.rpc_error = TimeoutError("primary")
        original = convert.write_once
        def write(path, document):
            if path.name == "CONVERSION_FAILED.json":
                raise OSError("evidence disk failed")
            return original(path, document)
        with mock.patch.object(convert, "write_once", side_effect=write):
            with self.assertRaisesRegex(TimeoutError, "primary"):
                self.run_conversion()
        self.assertEqual(convert.export_contexts()[0]["record_error"], "OSError")
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_interruption_after_publication_leaves_evidence_but_blocks_admission(self):
        def publish_then_interrupt(*args):
            grouped.publish_grouped_complete(*args)
            raise KeyboardInterrupt()
        with mock.patch.object(convert, "publish_grouped_complete", side_effect=publish_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_conversion()
        self.assertTrue((self.root / "COMPLETE.json").exists())
        self.assertTrue((self.root / "CONVERSION_FAILED.json").exists())
        with self.assertRaisesRegex(ValueError, "failure marker"):
            grouped.validate_grouped_complete(self.root, identity())


class RpcSourceTests(unittest.TestCase):
    def setUp(self):
        self.synchronize = mock.Mock()
        path = Path(__file__).resolve().parents[2] / "sllm_store/vllm_v4_loader.py"
        node = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "grouped_export_result")
        ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
        patch = mock.patch.dict(sys.modules, {
            "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(synchronize=self.synchronize)),
            "sllm_store.vllm_v4_loader": types.SimpleNamespace(grouped_export_result=ns["grouped_export_result"])})
        patch.start()
        self.addCleanup(patch.stop)

    def make_wrapper(self, rank):
        log = types.SimpleNamespace(info=lambda *a: None, warning_once=lambda *a: None)
        class Base:
            pass
        class Worker(Base):
            get_model = source_method("v1/worker/gpu_worker.py", "Worker", "get_model")
            def __init__(self, vllm_config, rank):
                self.rank, self.load_config = rank, vllm_config.load_config
                self.device = f"cuda:{rank}"
                receipt = dict(format=grouped.FORMAT, identity=identity(), rank=rank,
                               grouped_state_sha256="0" * 64)
                model = types.SimpleNamespace(_sllm_v4_stage="postprocessed",
                                               _sllm_grouped_export_result=receipt)
                self.model_runner = types.SimpleNamespace(get_model=lambda: model)
        ns = dict(resolve_obj_by_qualname=lambda name: Worker if name == "Worker" else worker.V4ExportWorkerExtension,
                  set_current_vllm_config=lambda *a: nullcontext(), logger=log)
        init = source_method("v1/worker/worker_base.py", "WorkerWrapperBase", "init_worker", ns)
        get = source_method("v1/worker/worker_base.py", "WorkerWrapperBase", "__getattr__")
        wrapper = type("Wrapper", (), dict(init_worker=init, __getattr__=get))()
        wrapper.rpc_rank = 0
        config = types.SimpleNamespace(enable_trace_function_call_for_thread=lambda: None,
            parallel_config=types.SimpleNamespace(worker_cls="Worker", worker_extension_cls=worker.WORKER_EXTENSION),
            model_config=types.SimpleNamespace(multimodal_config=None),
            load_config=types.SimpleNamespace(load_format="sllm_v4_grouped_export",
                                              model_loader_extra_config=dict(identity=identity())))
        with mock.patch.dict(sys.modules, {"vllm.plugins": types.SimpleNamespace(load_general_plugins=lambda: None)}):
            wrapper.init_worker([dict(vllm_config=config, rank=rank)])
        return wrapper

    def test_actual_worker_extension_injection_and_get_model_reach_small_receipt(self):
        wrapper = self.make_wrapper(3)
        result = getattr(wrapper, worker.RPC_METHOD)(identity())
        self.assertEqual(result["rank"], 3)
        self.assertLess(len(json.dumps(result)), 16384)
        self.synchronize.assert_called_once_with("cuda:3")
        wrapper.worker.model_runner.get_model()._sllm_v4_stage = "failed"
        with self.assertRaisesRegex(ValueError, "postprocessing"):
            getattr(wrapper, worker.RPC_METHOD)(identity())

    def test_delayed_cuda_failure_prevents_the_worker_success_receipt(self):
        wrapper = self.make_wrapper(1)
        self.synchronize.side_effect = RuntimeError("delayed CUDA error")
        with self.assertRaisesRegex(RuntimeError, "delayed CUDA error"):
            getattr(wrapper, worker.RPC_METHOD)(identity())

    def test_worker_rejects_restore_loader_foreign_identity_rank_or_nonprimitive_result(self):
        wrapper = self.make_wrapper(0)
        owner = wrapper.worker
        owner.load_config.load_format = "sllm_v4_grouped_store"
        with self.assertRaises(ValueError):
            owner.sllm_v4_export_receipt(identity())
        owner.load_config.load_format = "sllm_v4_grouped_export"
        foreign = dict(identity(), conversion_id="another-conversion")
        with self.assertRaises(ValueError):
            owner.sllm_v4_export_receipt(foreign)
        model = owner.model_runner.get_model()
        model._sllm_grouped_export_result["rank"] = 1
        with self.assertRaises(ValueError):
            owner.sllm_v4_export_receipt(identity())
        model._sllm_grouped_export_result["rank"] = 0
        model._sllm_grouped_export_result["grouped_state_sha256"] = object()
        with self.assertRaises(TypeError):
            owner.sllm_v4_export_receipt(identity())
        model._sllm_grouped_export_result["grouped_state_sha256"] = "a" * 18000
        with self.assertRaisesRegex(ValueError, "control-message limit"):
            owner.sllm_v4_export_receipt(identity())

    def test_pinned_model_runner_unwraps_the_exported_model(self):
        class Wrapper:
            def unwrap(self):
                return "raw-model"
        method = source_method("v1/worker/gpu_model_runner.py", "GPUModelRunner", "get_model",
                               dict(CUDAGraphWrapper=Wrapper, UBatchWrapper=Wrapper))
        self.assertEqual(method(types.SimpleNamespace(model=Wrapper())), "raw-model")
        with self.assertRaises(ValueError):
            method(types.SimpleNamespace())

    def test_named_rpc_traverses_actual_llm_core_mp_dispatch_and_future_aggregation(self):
        class EndLoop(Exception):
            pass
        class Queue:
            def __init__(self):
                self.items = deque()
            def dequeue(self, **kwargs):
                if not self.items:
                    raise EndLoop()
                return self.items.popleft()
        log = types.SimpleNamespace(exception=lambda *a: None)
        status = types.SimpleNamespace(SUCCESS=0)
        ns = dict(time=time, WorkerProc=types.SimpleNamespace(ResponseStatus=status),
                  cloudpickle=types.SimpleNamespace(dumps=mock.Mock(side_effect=AssertionError("callable RPC"))))
        cls = next(n for n in source_tree("v1/executor/multiproc_executor.py").body
                   if isinstance(n, ast.ClassDef) and n.name == "FutureWrapper")
        ns.update(Future=Future, deque=deque, Callable=Callable, Any=Any,
                  suppress=suppress, InvalidStateError=InvalidStateError)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), "FutureWrapper", "exec"), ns)
        executor_rpc = source_method("v1/executor/multiproc_executor.py", "MultiprocExecutor", "collective_rpc", ns)
        busy = source_method("v1/executor/multiproc_executor.py", "WorkerProc", "worker_busy_loop", dict(logger=log))
        responses = [Queue() for _ in range(8)]
        owners = []
        for rank in range(8):
            owner = types.SimpleNamespace(worker=self.make_wrapper(rank), rpc_broadcast_mq=Queue(), rank=rank)
            def output(value, queue=responses[rank]):
                queue.items.append((1, str(value)) if isinstance(value, Exception) else (0, value))
            owner.handle_output = output
            owners.append(owner)
        def broadcast(message):
            for owner in owners:
                owner.rpc_broadcast_mq.items.append(message)
                with self.assertRaises(EndLoop):
                    busy(owner)
        executor = types.SimpleNamespace(is_failed=False, response_mqs=responses, futures_queue=deque(),
                                         rpc_broadcast_mq=types.SimpleNamespace(enqueue=broadcast))
        executor.collective_rpc = types.MethodType(executor_rpc, executor)
        core_rpc = source_method("v1/engine/core.py", "EngineCore", "collective_rpc")
        core = types.SimpleNamespace(model_executor=executor)
        core.collective_rpc = types.MethodType(core_rpc, core)
        def utility(name, *args):
            self.assertEqual(name, "collective_rpc")
            json.dumps(args, allow_nan=False)  # Values are primitive; actual wire is not exercised.
            return core.collective_rpc(*args)
        client_rpc = source_method("v1/engine/core_client.py", "SyncMPClient", "collective_rpc")
        client = types.SimpleNamespace(call_utility=utility)
        client.collective_rpc = types.MethodType(client_rpc, client)
        engine_rpc = source_method("v1/engine/llm_engine.py", "LLMEngine", "collective_rpc")
        engine = types.SimpleNamespace(engine_core=client)
        engine.collective_rpc = types.MethodType(engine_rpc, engine)
        llm_rpc = source_method("entrypoints/llm.py", "LLM", "collective_rpc")
        llm = types.SimpleNamespace(llm_engine=engine)
        llm.collective_rpc = types.MethodType(llm_rpc, llm)
        result = convert.collect_export_receipts(llm, identity())
        self.assertEqual([r["rank"] for r in result], list(range(8)))
        ns["cloudpickle"].dumps.assert_not_called()
