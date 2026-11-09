import sys
"""Check loader argument forwarding without importing Torch or vLLM."""
import ast
import contextlib
import os
import pathlib
import tempfile
import types
import unittest
from unittest import mock


class LoaderInterfaceTest(unittest.TestCase):
    def test_selected_model_config_and_prefix_reach_initializer(self):
        patch = (pathlib.Path(__file__).resolve().parents[2]
                 / "vllm_patch/sllm_load.patch").read_text()
        marker = "+++ b/vllm/model_executor/model_loader/sllm_loader.py"
        section = patch.split(marker, 1)[1].split("diff --git ", 1)[0]
        source = "".join(line[1:] for line in section.splitlines(True)
                         if line.startswith("+") and not line.startswith("+++"))
        tree = ast.parse(source)
        loader = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef)
                      and node.name == "ServerlessLLMLoader")
        method = next(node for node in loader.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "load_model")

        class ReachedInitializer(Exception):
            pass

        initialize = mock.Mock(side_effect=ReachedInitializer)
        dtype_context = mock.Mock(return_value=contextlib.nullcontext())
        namespace = {
            "VllmConfig": object, "ModelConfig": object,
            "nn": types.SimpleNamespace(Module=object), "os": os,
            "set_default_torch_dtype": dtype_context,
            "torch": types.SimpleNamespace(
                device=lambda _: contextlib.nullcontext()),
            "initialize_model": initialize,
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]),
                     "<official-sllm-loader>", "exec"), namespace)
        store = types.ModuleType("sllm_store")
        store.__path__ = []
        store_torch = types.ModuleType("sllm_store.torch")
        store_torch.load_dict = mock.Mock(
            side_effect=AssertionError("Store must not run in this test"))
        vllm = types.ModuleType("vllm")
        vllm.__path__ = []
        distributed = types.ModuleType("vllm.distributed")
        distributed.get_tensor_model_parallel_rank = lambda: 0
        modules = {"sllm_store": store, "sllm_store.torch": store_torch,
                   "vllm": vllm, "vllm.distributed": distributed}
        with tempfile.TemporaryDirectory(prefix="sllm-loader-api-") as folder:
            selected = types.SimpleNamespace(model=folder, dtype=object())
            fallback = types.SimpleNamespace(
                model=os.path.join(folder, "must-not-use-fallback"),
                dtype="must-not-use-fallback")
            config = types.SimpleNamespace(model_config=fallback)
            with mock.patch.dict("sys.modules", modules):
                with mock.patch.object(os.path, "join", wraps=os.path.join) as join:
                    with self.assertRaises(ReachedInitializer):
                        namespace["load_model"](
                            object(), vllm_config=config,
                            model_config=selected, prefix="selected_prefix")
                join.assert_any_call(selected.model, "rank_0")
            initialize.assert_called_once_with(
                vllm_config=config, model_config=selected,
                prefix="selected_prefix")
            dtype_context.assert_called_once_with(selected.dtype)
            store_torch.load_dict.assert_not_called()


class LoadEvidenceTest(unittest.TestCase):
    def context(self):
        import textwrap
        patch = (pathlib.Path(__file__).resolve().parents[2]
                 / "vllm_patch/sllm_load.patch").read_text()
        section = patch.split("+++ b/vllm/v1/worker/gpu_model_runner.py", 1)[1].split("diff --git ", 1)[0]
        lines = section.splitlines()
        start = next(i for i, line in enumerate(lines)
                     if line == "             with DeviceMemoryProfiler() as m:")
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i] == "             self.model_memory_usage = m.consumed_memory")
        source = textwrap.dedent("\n".join(line[1:] for line in lines[start:end]
                                           if line.startswith((" ", "+"))))
        tree = ast.parse(source)
        self.assertIsInstance(tree.body[0], ast.With)
        self.assertIsInstance(tree.body[0].body[-1], ast.Try)
        self.assertTrue(tree.body[0].body[-1].finalbody)
        return source

    def namespace(self, load_error=None, snapshot_error=None):
        import time
        from collections import defaultdict

        class Device:
            type = "cuda"
            def __str__(self):
                return "cuda:0"

        class Storage:
            def __init__(self, pointer, size):
                self.pointer, self.size = pointer, size
            def data_ptr(self):
                return self.pointer
            def nbytes(self):
                return self.size

        class Tensor:
            device = Device()
            dtype = "torch.int32"
            def __init__(self, storage, shape):
                self.storage, self.shape = storage, shape
            def untyped_storage(self):
                return self.storage
            def stride(self):
                return (1,)
            def numel(self):
                return self.shape[0]
            def element_size(self):
                return 4
            def storage_offset(self):
                return 0

        class Quant:
            kernel_backend = "Marlin"

        shared = Storage(1000, 64)
        model = types.SimpleNamespace(
            named_modules=mock.Mock(return_value=[("layer", types.SimpleNamespace(quant_method=Quant(), workspace=object()))]),
            named_parameters=mock.Mock(return_value=[("weight", Tensor(shared, [16])), ("alias", Tensor(shared, [8])), ("weight_g_idx", Tensor(Storage(0, 0), [0]))]),
            named_buffers=mock.Mock(return_value=[("buffer", Tensor(Storage(2000, 32), [8]))]),
        )
        state = {"peak": 300, "reserved_peak": 400, "events": []}
        def capture(_):
            state["events"].append("capture_peak")
            if snapshot_error is not None:
                raise snapshot_error
            return state["peak"]
        cuda = types.SimpleNamespace(
            synchronize=mock.Mock(), memory_allocated=lambda _: 64,
            memory_reserved=lambda _: 128, max_memory_allocated=capture,
            max_memory_reserved=lambda _: state["reserved_peak"],
            mem_get_info=lambda _: (900, 1000),
            get_device_properties=mock.Mock(return_value=types.SimpleNamespace(
                uuid="GPU-736d7f37-f647-b82d-ab11-693753ac6822")),
        )
        logger = mock.Mock()
        instance = types.SimpleNamespace(device="cuda:0", load_config=object(),
                                         vllm_config=object(), model_config=object(),
                                         lora_config=None, use_aux_hidden_state_outputs=False)
        loader = types.SimpleNamespace(load_model=mock.Mock(side_effect=load_error, return_value=model))
        class Profiler:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                state["events"].append("profiler_reset")
                state.update(peak=64, reserved_peak=128)
        class EvidenceOS(types.SimpleNamespace):
            def __getattr__(self, name):
                return getattr(os, name)

        namespace = {"os": EvidenceOS(environ={"SLLM_CAPTURE_LOAD_EVIDENCE": "1"}, getpid=lambda: 123),
                     "torch": types.SimpleNamespace(cuda=cuda), "self": instance,
                     "logger": logger, "time": time, "defaultdict": defaultdict,
                     "get_tp_group": lambda: types.SimpleNamespace(rank_in_group=3),
                     "DeviceMemoryProfiler": Profiler,
                     "get_model_loader": lambda _: loader, "load_dummy_weights": False}
        return namespace, state, model

    def test_disabled_default_does_not_collect_metadata_or_touch_cuda(self):
        namespace, state, model = self.namespace()
        namespace["os"].environ = {}
        exec(self.context(), namespace)
        model.named_modules.assert_not_called()
        namespace["torch"].cuda.synchronize.assert_not_called()
        namespace["logger"].info.assert_not_called()
        self.assertEqual(state["events"], ["profiler_reset"])

    def test_peak_alias_backend_and_empty_metadata_before_reset(self):
        import json
        namespace, state, _ = self.namespace()
        exec(self.context(), namespace)
        row = json.loads(namespace["logger"].info.call_args.args[1])
        self.assertEqual(row["memory"]["peak_allocated_bytes"], 300)
        self.assertEqual(row["memory"]["peak_reserved_bytes"], 400)
        self.assertEqual(state["peak"], 64)
        self.assertEqual(state["events"], ["capture_peak", "profiler_reset"])
        self.assertTrue(row["load_completed"])
        self.assertEqual(row["tp_rank"], 3)
        self.assertEqual(row["pid"], 123)
        self.assertEqual(row["device_uuid"], "GPU-736d7f37-f647-b82d-ab11-693753ac6822")
        namespace["torch"].cuda.get_device_properties.assert_called_once_with("cuda:0")
        self.assertEqual(row["empty_g_idx_keys"], ["weight_g_idx"])
        self.assertEqual(row["unique_registered_storage_bytes"], 96)
        self.assertEqual(row["registered_storages"][0]["names"], ["parameter:weight", "parameter:alias"])
        self.assertEqual(sum(row["quant_method_counts"].values()), 1)
        self.assertTrue(next(iter(row["kernel_backend_counts"])).endswith(":Marlin"))
        self.assertEqual(len(row["registered_tensors"]), 4)
        self.assertIn("nonregistered workspace", row["coverage"])
        self.assertNotIn("workspace", row["registered_tensors"][0])
        namespace["torch"].cuda.synchronize.assert_called_once_with("cuda:0")

    def test_physical_uuid_normalizes_torch_metadata_without_rank_ordinal_assumption(self):
        import json
        for raw in ["736D7F37-F647-B82D-AB11-693753AC6822", "GPU-736d7f37-f647-b82d-ab11-693753ac6822"]:
            with self.subTest(raw=raw):
                namespace, _, _ = self.namespace()
                namespace["torch"].cuda.get_device_properties.return_value.uuid = raw
                exec(self.context(), namespace)
                row = json.loads(namespace["logger"].info.call_args.args[1])
                self.assertEqual(row["device_uuid"], "GPU-736d7f37-f647-b82d-ab11-693753ac6822")
                self.assertEqual(row["tp_rank"], 3)
                namespace["torch"].cuda.get_device_properties.assert_called_once_with("cuda:0")

    def test_model_failure_captures_peak_before_reset_and_preserves_identity(self):
        import json
        original = RuntimeError("original model load failure")
        namespace, state, _ = self.namespace(load_error=original)
        with self.assertRaises(RuntimeError) as raised:
            exec(self.context(), namespace)
        self.assertIs(raised.exception, original)
        row = json.loads(namespace["logger"].info.call_args.args[1])
        self.assertFalse(row["load_completed"])
        self.assertFalse(row["registered_model_available"])
        self.assertIsNone(row["registered_tensors"])
        self.assertIsNone(row["unique_registered_storage_bytes"])
        self.assertEqual(row["memory"]["peak_allocated_bytes"], 300)
        self.assertEqual(state["events"], ["capture_peak", "profiler_reset"])

    def test_failed_load_reports_existing_partial_model_without_success_claim(self):
        import json
        original = RuntimeError("load failed after partial model publication")
        namespace, _, model = self.namespace(load_error=original)
        namespace["self"].model = model
        with self.assertRaises(RuntimeError) as raised:
            exec(self.context(), namespace)
        self.assertIs(raised.exception, original)
        row = json.loads(namespace["logger"].info.call_args.args[1])
        self.assertFalse(row["load_completed"])
        self.assertTrue(row["registered_model_available"])
        self.assertEqual(len(row["registered_tensors"]), 4)

    def test_snapshot_failure_remains_missing_and_cannot_mask_model_error(self):
        import json
        original = RuntimeError("original model load failure")
        namespace, state, _ = self.namespace(load_error=original, snapshot_error=ValueError("snapshot unavailable"))
        with self.assertRaises(RuntimeError) as raised:
            exec(self.context(), namespace)
        self.assertIs(raised.exception, original)
        namespace["logger"].info.assert_not_called()
        row = json.loads(namespace["logger"].warning.call_args.args[1])
        self.assertEqual(row["event"], "SLLM_LOAD_EVIDENCE_MISSING")
        self.assertIsNone(row["memory"])
        self.assertFalse(row["load_completed"])
        self.assertEqual(state["events"], ["capture_peak", "profiler_reset"])

    def test_diagnostic_logging_failure_cannot_mask_model_error(self):
        original = RuntimeError("original model load failure")
        namespace, state, _ = self.namespace(load_error=original)
        namespace["logger"].info.side_effect = ValueError("diagnostic sink failed")
        namespace["logger"].warning.side_effect = ValueError("diagnostic warning sink failed")
        with self.assertRaises(RuntimeError) as raised:
            exec(self.context(), namespace)
        self.assertIs(raised.exception, original)
        self.assertEqual(state["events"], ["capture_peak", "profiler_reset"])

    def test_disabled_default_preserves_model_exception(self):
        original = RuntimeError("original model load failure")
        namespace, state, _ = self.namespace(load_error=original)
        namespace["os"].environ = {}
        with self.assertRaises(RuntimeError) as raised:
            exec(self.context(), namespace)
        self.assertIs(raised.exception, original)
        namespace["torch"].cuda.synchronize.assert_not_called()
        namespace["logger"].warning.assert_not_called()
        self.assertEqual(state["events"], ["profiler_reset"])

    def test_metadata_capture_has_no_tensor_payload_or_store_calls(self):
        tree = ast.parse(self.context())
        diagnostic = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                          and "SLLM_CAPTURE_LOAD_EVIDENCE" in ast.unparse(node.test))
        attributes = {node.func.attr for node in ast.walk(diagnostic)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(attributes & {"cpu", "clone", "copy_", "numpy", "tolist", "item"})
        self.assertNotIn("sllm_store", ast.unparse(diagnostic))
        self.assertNotIn("hashlib", ast.unparse(diagnostic))


    def test_atomic_file_lossless_payload_and_nonauthoritative_summary(self):
        import json
        legacy, _, _ = self.namespace()
        with mock.patch("time.perf_counter", return_value=100):
            exec(self.context(), legacy)
        expected = json.loads(legacy["logger"].info.call_args.args[1])
        with tempfile.TemporaryDirectory() as folder:
            namespace, _, _ = self.namespace()
            namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = folder
            with mock.patch("time.perf_counter", return_value=100):
                exec(self.context(), namespace)
            final = pathlib.Path(folder) / "rank-3-pid-123.json"
            self.assertEqual(json.loads(final.read_text()), expected)
            self.assertEqual(final.stat().st_mode & 0o777, 0o600)
            summary = json.loads(namespace["logger"].info.call_args.args[1])
            self.assertEqual(summary["filename"], final.name)
            self.assertEqual(summary["bytes"], final.stat().st_size)
            self.assertNotIn("registered_tensors", summary)
            self.assertLess(len(namespace["logger"].info.call_args.args[1]), 512)
            self.assertEqual(namespace["logger"].info.call_args.args[0], "SLLM_LOAD_EVIDENCE_FILE %s")

    def test_atomic_publish_no_partial_final_or_overwrite(self):
        import json
        real_link = os.link
        with tempfile.TemporaryDirectory() as folder:
            namespace, _, _ = self.namespace()
            namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = folder
            def link(source, destination, **kwargs):
                self.assertFalse((pathlib.Path(folder) / destination).exists())
                self.assertEqual(json.loads((pathlib.Path(folder) / source).read_text())["event"], "SLLM_LOAD_EVIDENCE")
                return real_link(source, destination, **kwargs)
            with mock.patch("os.link", side_effect=link):
                exec(self.context(), namespace)
            final = pathlib.Path(folder) / "rank-3-pid-123.json"
            before = final.read_bytes()
            again, _, _ = self.namespace()
            again["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = folder
            exec(self.context(), again)
            again["logger"].info.assert_not_called()
            self.assertEqual(json.loads(again["logger"].warning.call_args.args[1])["event"], "SLLM_LOAD_EVIDENCE_MISSING")
            self.assertEqual(final.read_bytes(), before)
            self.assertEqual(len(list(pathlib.Path(folder).iterdir())), 1)

    def test_file_sink_failures_are_missing_and_preserve_load_exception(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            for missing in (True, False):
                namespace, _, _ = self.namespace()
                namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = folder + "/missing" if missing else folder
                with mock.patch("os.fsync", side_effect=OSError("write failed")):
                    exec(self.context(), namespace)
                namespace["logger"].info.assert_not_called()
                self.assertEqual(json.loads(namespace["logger"].warning.call_args.args[1])["event"], "SLLM_LOAD_EVIDENCE_MISSING")
                self.assertEqual(list(pathlib.Path(folder).iterdir()), [])
            original = RuntimeError("original load failed")
            namespace, state, _ = self.namespace(load_error=original)
            namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = folder
            with mock.patch("os.fsync", side_effect=OSError("write failed")):
                with self.assertRaises(RuntimeError) as raised:
                    exec(self.context(), namespace)
            self.assertIs(raised.exception, original)
            self.assertEqual(state["events"], ["capture_peak", "profiler_reset"])
            self.assertEqual(list(pathlib.Path(folder).iterdir()), [])

    def test_concurrent_actual_producer_blocks_publish_eight_complete_files(self):
        import json
        from concurrent.futures import ThreadPoolExecutor
        source = self.context()
        with tempfile.TemporaryDirectory() as folder:
            def publish(rank):
                namespace, _, model = self.namespace()
                namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = folder
                namespace["os"].getpid = lambda: 1000 + rank
                namespace["get_tp_group"] = lambda: types.SimpleNamespace(rank_in_group=rank)
                tensor = model.named_buffers.return_value[0][1]
                model.named_buffers.return_value = [("buffer_" + str(i), tensor) for i in range(1000)]
                exec(source, namespace)
                namespace["logger"].warning.assert_not_called()
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(publish, rank) for rank in range(8)]
                while not all(f.done() for f in futures):
                    for path in pathlib.Path(folder).glob("rank-*.json"):
                        self.assertEqual(json.loads(path.read_text())["event"], "SLLM_LOAD_EVIDENCE")
                for future in futures:
                    future.result()
            files = list(pathlib.Path(folder).iterdir())
            self.assertEqual(len(files), 8)
            rows = [json.loads(p.read_text()) for p in files]
            self.assertEqual({r["tp_rank"] for r in rows}, set(range(8)))
            self.assertEqual({r["pid"] for r in rows}, set(range(1000, 1008)))
            self.assertTrue(all(len(r["registered_tensors"]) == 1003 for r in rows))


if __name__ == "__main__":
    unittest.main()

class DiagnosticFingerprintTest(unittest.TestCase):
    def test_diagnostic_original_load_error_is_preserved_without_value_reads(self):
        import json
        consumer=LoadEvidenceTest()
        original=RuntimeError("original load failure")
        namespace,_,_=consumer.namespace(load_error=original)
        namespace["os"].environ["SLLM_DIAGNOSTIC_VALUE_FINGERPRINT"]="1"
        with self.assertRaises(RuntimeError) as caught:
            exec(consumer.context(),namespace)
        self.assertIs(caught.exception,original)
        row=json.loads(namespace["logger"].info.call_args.args[1])
        self.assertIn("diagnostic_value_fingerprint",row)
        self.assertEqual(row["diagnostic_value_fingerprint"]["status"],"SKIPPED_LOAD_ERROR")

    def helper_source(self):
        patch=(pathlib.Path(__file__).resolve().parents[2]/"vllm_patch/sllm_load.patch").read_text()
        lines=patch.splitlines()
        start=next(i for i,line in enumerate(lines) if line.startswith("+def _sllm_diagnostic_value_fingerprint("))
        end=next(i for i in range(start+1,len(lines)) if not lines[i].startswith("+"))
        return "\n".join(line[1:] for line in lines[start:end])

    def fixture(self, large=False):
        import io
        import json
        consumer=LoadEvidenceTest()
        namespace,_,_=consumer.namespace()
        state={"live_bytes":0,"peak_bytes":0,"copies":[],"index_reads":0,"expire":False}
        class Payload(bytearray):
            def __init__(self,size):
                super().__init__(size)
                self.size=size
                state["live_bytes"]+=size
                state["peak_bytes"]=max(state["peak_bytes"],state["live_bytes"])
            def __del__(self):
                state["live_bytes"]-=self.size
        class CPU:
            def __init__(self,size):self.data=Payload(size)
            def numpy(self):return self.data
        class ByteView:
            def __init__(self,size,pointer=0):self.size,self.pointer=size,pointer
            def untyped_storage(self):return types.SimpleNamespace(data_ptr=lambda:self.pointer)
            def numel(self):return self.size
            def __getitem__(self,index):
                start,stop,step=index.indices(self.size)
                assert step==1
                return ByteView(stop-start)
            def cpu(self):
                assert self.size<=64<<20,"full tensor copy"
                assert state["live_bytes"]==0,"overlapping CPU payload allocations"
                state["copies"].append(self.size)
                return CPU(self.size)
        class Storage:
            def __init__(self,pointer,size):self.pointer,self.size=pointer,size
            def data_ptr(self):return self.pointer
            def nbytes(self):return self.size
        class Tensor:
            device=types.SimpleNamespace(type="cuda")
            dtype="torch.float32"
            def __init__(self,pointer,count,shape=None):
                self.shape=[count] if shape is None else shape
                self.count=count
                self.storage=Storage(pointer,count*4)
                self.contiguous=True
            def is_contiguous(self):return self.contiguous
            def untyped_storage(self):return self.storage
            def storage_offset(self):return 0
            def stride(self):return () if not self.shape else (1,)
            def numel(self):return self.count
            def element_size(self):return 4
            def detach(self):return self
            def reshape(self,*shape):
                assert self.contiguous and shape==(-1,)
                return self
            def view(self,dtype):
                assert dtype is namespace["torch"].uint8
                return ByteView(self.count*4,self.storage.pointer)
            def cpu(self):raise AssertionError("full tensor CPU copy is forbidden")
            def clone(self):raise AssertionError("clone is forbidden")
            def __str__(self):return "cuda:0"
        # Device string must be stable across tensor objects to validate exact ranges.
        class Device:
            type="cuda"
            def __str__(self):return "cuda:0"
        Tensor.device=Device()
        count=(64<<20)//4+1 if large else 4
        params={"weight":Tensor(1000,count),"param_alias":Tensor(1000,count),"empty":Tensor(0,0),"scalar":Tensor(4000,1,shape=[])}
        buffers={"cache":Tensor(2000,2),"cache_alias":Tensor(2000,2),"empty_runtime":Tensor(0,0)}
        model=types.SimpleNamespace(named_modules=lambda:[],
            named_parameters=lambda **kwargs:list(params.items()),named_buffers=lambda **kwargs:list(buffers.items()))
        index={name:[0,t.numel()*4,list(t.shape),list(t.stride()),str(t.dtype)] for name,t in params.items() if name!="param_alias"}
        expected="/store/vllm/kimi-k2.6-sf-reference/rank_3/tensor_index.json"
        real_open=open
        def index_open(path,*args,**kwargs):
            if path==expected:
                state["index_reads"]+=1
                return io.BytesIO(json.dumps(index).encode())
            return real_open(path,*args,**kwargs)
        namespace["torch"].uint8=object()
        namespace["get_model_loader"]=lambda _:types.SimpleNamespace(load_model=lambda **kwargs:model)
        namespace["os"].environ["SLLM_DIAGNOSTIC_VALUE_FINGERPRINT"]="1"
        exec(self.helper_source(),namespace)
        return consumer,namespace,state,params,buffers,index,index_open

    def execute_fingerprint(self,fixture):
        import json
        consumer,namespace,*_,index_open=fixture
        source=consumer.context()
        with mock.patch("builtins.open",side_effect=index_open):
            exec(source,namespace)
        return json.loads(namespace["logger"].info.call_args.args[1])["diagnostic_value_fingerprint"]

    def test_actual_consumer_hashes_persistent_runtime_alias_scalar_and_empty(self):
        import hashlib
        fixture=self.fixture()
        value=self.execute_fingerprint(fixture);_,_,state,_,_,index,_=fixture
        self.assertEqual(value["status"],"PASS");self.assertTrue(value["coverage_complete"])
        self.assertEqual(set(row["name"] for row in value["persistent"]),set(index))
        self.assertEqual(len(value["runtime_buffers"]),3);self.assertEqual(len(value["aliases"]),1)
        self.assertEqual(value["aliases"][0]["alias_of"],"weight")
        rows={row["name"]:row for group in ("persistent","runtime_buffers","aliases") for row in value[group]}
        self.assertEqual(len(rows),7);self.assertEqual(rows["param_alias"]["sha256"],rows["weight"]["sha256"])
        self.assertEqual(rows["cache_alias"]["alias_of"],"cache")
        for name in ("empty","empty_runtime"):
            self.assertEqual(rows[name]["sha256"],hashlib.sha256(b"").hexdigest())
            self.assertNotIn("alias_of",rows[name])
        self.assertEqual(rows["scalar"]["shape"],[])
        self.assertEqual(rows["scalar"]["sha256"],hashlib.sha256(bytes(4)).hexdigest())
        self.assertEqual(sorted(state["copies"]),[4,8,16])
        self.assertEqual(state["live_bytes"],0)
        self.assertEqual(value["counts"]["hashed_bytes"],28)
        self.assertEqual(value["stage"],"before_first_forward")

    def test_large_virtual_tensor_uses_at_most_one_64mib_cpu_chunk(self):
        fixture=self.fixture(large=True)
        value=self.execute_fingerprint(fixture);state=fixture[2]
        self.assertEqual(value["status"],"PASS")
        self.assertEqual(max(state["copies"]),64<<20)
        self.assertEqual(state["peak_bytes"],64<<20)
        self.assertEqual(state["live_bytes"],0)
        self.assertEqual(state["copies"].count(64<<20),1)
        self.assertEqual(value["counts"]["hashed_bytes"],(64<<20)+16)

    def test_missing_key_metadata_noncontiguous_and_uncovered_parameter_fail_closed(self):
        import json
        for mode in ("missing","metadata","noncontiguous","uncovered","nonexact_alias"):
            with self.subTest(mode=mode):
                fixture=self.fixture();consumer,namespace,state,params,_,index,index_open=fixture
                if mode=="missing":index["missing"]=[0,4,[1],[1],"torch.float32"]
                if mode=="metadata":index["weight"][1]+=4
                if mode=="noncontiguous":params["weight"].contiguous=False
                if mode=="uncovered":del index["weight"]
                if mode=="nonexact_alias":params["param_alias"].shape=[2,2]
                with mock.patch("builtins.open",side_effect=index_open):
                    with self.assertRaises(RuntimeError):exec(consumer.context(),namespace)
                value=json.loads(namespace["logger"].info.call_args.args[1])["diagnostic_value_fingerprint"]
                self.assertEqual(value["status"],"FAIL");self.assertFalse(value["coverage_complete"])
                self.assertIsNotNone(value["error"])
                self.assertEqual(state["live_bytes"],0)

    def test_timeout_releases_chunk_and_publishes_explicit_failure(self):
        import json
        fixture=self.fixture();consumer,namespace,state,_,_,_,index_open=fixture
        def clock():return 601 if state["copies"] else 0
        with mock.patch("builtins.open",side_effect=index_open),mock.patch("time.monotonic",side_effect=clock):
            with self.assertRaises(TimeoutError):exec(consumer.context(),namespace)
        value=json.loads(namespace["logger"].info.call_args.args[1])["diagnostic_value_fingerprint"]
        self.assertEqual(value["status"],"TIMEOUT");self.assertFalse(value["coverage_complete"])
        self.assertEqual(value["budget_seconds"],600);self.assertEqual(value["counts"]["hashed_bytes"],0)
        self.assertEqual(state["live_bytes"],0)

    def test_default_off_has_no_index_reads_or_tensor_payload_copy(self):
        import json
        fixture=self.fixture();consumer,namespace,state,*_,index_open=fixture
        del namespace["os"].environ["SLLM_DIAGNOSTIC_VALUE_FINGERPRINT"]
        with mock.patch("builtins.open",side_effect=index_open):exec(consumer.context(),namespace)
        row=json.loads(namespace["logger"].info.call_args.args[1])
        self.assertNotIn("diagnostic_value_fingerprint",row)
        self.assertEqual(state["index_reads"],0);self.assertEqual(state["copies"],[])

    def test_missing_capture_or_failed_sink_cannot_report_diagnostic_success(self):
        fixture=self.fixture();consumer,namespace,*_,index_open=fixture
        namespace["os"].environ["SLLM_CAPTURE_LOAD_EVIDENCE"]="0"
        with mock.patch("builtins.open",side_effect=index_open):
            with self.assertRaises(RuntimeError):exec(consumer.context(),namespace)
        self.assertEqual(fixture[2]["copies"],[])
        fixture=self.fixture();consumer,namespace,*_,index_open=fixture
        namespace["logger"].info.side_effect=OSError("evidence sink failed")
        with mock.patch("builtins.open",side_effect=index_open):
            with self.assertRaises(OSError):exec(consumer.context(),namespace)
        self.assertEqual(fixture[2]["live_bytes"],0)

    def test_index_read_error_is_explicit_and_original_error_still_wins(self):
        import json
        fixture=self.fixture();consumer,namespace,*_=fixture
        with mock.patch("builtins.open",side_effect=OSError("index unavailable")):
            with self.assertRaises(RuntimeError):exec(consumer.context(),namespace)
        value=json.loads(namespace["logger"].info.call_args.args[1])["diagnostic_value_fingerprint"]
        self.assertEqual(value["status"],"FAIL");self.assertEqual(value["error_type"],"OSError")
        original=RuntimeError("original load failure")
        namespace,_,_=consumer.namespace(load_error=original,snapshot_error=OSError("capture failed"))
        namespace["os"].environ["SLLM_DIAGNOSTIC_VALUE_FINGERPRINT"]="1"
        namespace["_sllm_diagnostic_value_fingerprint"]=mock.Mock(side_effect=AssertionError("must not hash failed model"))
        with self.assertRaises(RuntimeError) as caught:exec(consumer.context(),namespace)
        self.assertIs(caught.exception,original)
        namespace["_sllm_diagnostic_value_fingerprint"].assert_not_called()

    def test_new_overlay_actual_consumer_matches_canonical_patch(self):
        import json
        import time
        overlay=(pathlib.Path(__file__).resolve().parents[2]/"build/kimi-vllm0191-diagnostic-values-v1/v1/worker/gpu_model_runner.py")
        tree=ast.parse(overlay.read_text())
        helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_sllm_diagnostic_value_fingerprint")
        runner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="GPUModelRunner")
        method=next(n for n in runner.body if isinstance(n,ast.FunctionDef) and n.name=="load_model")
        context=next(n for n in ast.walk(method) if isinstance(n,ast.With) and any(isinstance(item.context_expr,ast.Call) and isinstance(item.context_expr.func,ast.Name) and item.context_expr.func.id=="DeviceMemoryProfiler" for item in n.items))
        expected=None
        for actual in (False,True):
            fixture=self.fixture();consumer,namespace,*_,index_open=fixture
            if actual:
                exec(compile(ast.Module(body=[helper],type_ignores=[]),"<actual-diag-overlay-helper>","exec"),namespace)
                source=compile(ast.Module(body=[context],type_ignores=[]),"<actual-diag-overlay-capture>","exec")
            else:source=consumer.context()
            with mock.patch("builtins.open",side_effect=index_open),mock.patch("time.perf_counter",return_value=100),mock.patch("time.monotonic",return_value=0):
                exec(source,namespace)
            row=json.loads(namespace["logger"].info.call_args.args[1])
            if actual:self.assertEqual(row,expected)
            else:expected=row
        self.assertEqual(expected["diagnostic_value_fingerprint"]["status"],"PASS")

    def test_123_runtime_names_share_only_two_real_ranges(self):
        fixture=self.fixture();buffers=fixture[4]
        tensor_type=type(buffers["cache"])
        buffers.clear()
        for index in range(123):
            buffers["runtime_%03d"%index]=tensor_type(2000 if index<122 else 3000,2)
        value=self.execute_fingerprint(fixture)
        self.assertEqual(value["status"],"PASS")
        self.assertEqual(len(value["runtime_buffers"]),123)
        self.assertEqual(fixture[2]["copies"].count(8),2)
        self.assertEqual(value["counts"]["unique_hashed_ranges"],4)
        self.assertEqual(value["counts"]["hashed_bytes"],36)
        self.assertEqual(fixture[2]["live_bytes"],0)


class LoadHostEndpointsTest(unittest.TestCase):
    def sources(self):
        consumer = LoadEvidenceTest()
        root = pathlib.Path(__file__).resolve().parents[2]
        tree = ast.parse((root / "build/kimi-vllm0191-load-endpoints-v1/v1/worker/gpu_model_runner.py").read_text())
        runner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GPUModelRunner")
        method = next(n for n in runner.body if isinstance(n, ast.FunctionDef) and n.name == "load_model")
        context = next(n for n in ast.walk(method) if isinstance(n, ast.With) and any(isinstance(q.context_expr, ast.Call) and isinstance(q.context_expr.func, ast.Name) and q.context_expr.func.id == "DeviceMemoryProfiler" for q in n.items))
        return [consumer.context(), compile(ast.Module(body=[context], type_ignores=[]), "<actual-endpoint-overlay>", "exec")]

    def fixture(self, load_error=None):
        namespace, state, model = LoadEvidenceTest().namespace(load_error=load_error)
        namespace["self"].model_config = types.SimpleNamespace(model="/model")
        namespace["os"].environ.update(SLLM_CAPTURE_LOAD_ENDPOINTS="1", SLLM_LOAD_SPAN_RUN_ID="test-run", SLLM_LOAD_SPAN_INSTANCE_ID="test-run-life1")
        clock = dict(boot_id="boot", container_hostname="container", process_startticks=1234,
                     time_namespace="time:[1]", monotonic_clock_implementation="CLOCK_MONOTONIC")
        helper = types.ModuleType("sllm.logger")
        helper.get_clock_identity = mock.Mock(return_value=clock)
        parent = types.ModuleType("sllm"); parent.__path__ = []; parent.logger = helper
        return namespace, state, model, clock, {"sllm": parent, "sllm.logger": helper}

    def test_exact_call_boundary_before_all_optional_setup_and_existing_sync(self):
        import json
        for source in self.sources():
            namespace, _, model, clock, modules = self.fixture()
            order = []
            namespace["get_model_loader"] = lambda _: order.append("factory") or types.SimpleNamespace(load_model=lambda **k: order.extend(("fresh_construct", "restore_assign", "required_postprocess")) or model)
            instance = namespace["self"]; instance.lora_config = True
            instance.load_lora_model = lambda *args: order.append("LoRA") or model
            instance.drafter = types.SimpleNamespace(load_model=lambda _: order.append("drafter"))
            instance.use_aux_hidden_state_outputs = True; instance.get_model = lambda: model
            instance._get_eagle3_aux_layers_from_config = lambda: [1]
            namespace["supports_eagle3"] = lambda _: True
            model.set_aux_hidden_state_layers = lambda _: order.append("aux")
            namespace["torch"].cuda.synchronize.side_effect = lambda _: order.append("sync")
            times = iter((100, 200))
            def timestamp():
                value = next(times); order.append("begin" if value == 100 else "end"); return value
            with mock.patch.dict(sys.modules, modules), mock.patch("time.monotonic_ns", side_effect=timestamp):
                exec(source, namespace)
            self.assertEqual(order, ["factory", "begin", "fresh_construct", "restore_assign", "required_postprocess", "end", "LoRA", "drafter", "aux", "sync"])
            row = json.loads(namespace["logger"].info.call_args.args[1]); span = row["weight_load_host_span"]
            self.assertEqual((span["begin_ns"], span["end_ns"], span["status"]), (100, 200, "COMPLETE"))
            self.assertEqual((span["pid"], span["tp_rank"], span["run_id"], span["model_instance_id"]), (123, 3, "test-run", "test-run-life1"))
            self.assertEqual(span["clock_identity"], clock)
            self.assertFalse(span["added_cuda_synchronization"])
            namespace["torch"].cuda.synchronize.assert_called_once()

    def test_default_off_never_reads_clock_identity_or_monotonic_endpoints(self):
        import json
        for source in self.sources():
            namespace, _, _, _, modules = self.fixture()
            namespace["os"].environ.pop("SLLM_CAPTURE_LOAD_ENDPOINTS")
            with mock.patch.dict(sys.modules, modules), mock.patch("time.monotonic_ns", side_effect=AssertionError("disabled")) as timer:
                exec(source, namespace)
            modules["sllm.logger"].get_clock_identity.assert_not_called(); timer.assert_not_called()
            self.assertNotIn("weight_load_host_span", json.loads(namespace["logger"].info.call_args.args[1]))

    def test_missing_run_instance_or_clock_is_explicit_missing(self):
        import json
        for source in self.sources():
            for fault in ("run", "instance", "clock"):
                namespace, _, _, _, modules = self.fixture()
                if fault == "run": namespace["os"].environ.pop("SLLM_LOAD_SPAN_RUN_ID")
                if fault == "instance": namespace["os"].environ["SLLM_LOAD_SPAN_INSTANCE_ID"] = ""
                if fault == "clock": modules["sllm.logger"].get_clock_identity.return_value = {"errors": {"boot_id": "OSError"}}
                with mock.patch.dict(sys.modules, modules), mock.patch("time.monotonic_ns") as timer:
                    exec(source, namespace)
                span = json.loads(namespace["logger"].info.call_args.args[1])["weight_load_host_span"]
                self.assertEqual(span["status"], "MISSING"); self.assertIsNone(span["begin_ns"]); self.assertIsNone(span["end_ns"]); timer.assert_not_called()

    def test_model_exception_identity_preserved_and_no_success_endpoint(self):
        import json
        for source in self.sources():
            original = RuntimeError("original loader failure")
            namespace, _, _, _, modules = self.fixture(original)
            with mock.patch.dict(sys.modules, modules), mock.patch("time.monotonic_ns", return_value=100):
                with self.assertRaises(RuntimeError) as caught: exec(source, namespace)
            self.assertIs(caught.exception, original)
            row = json.loads(namespace["logger"].info.call_args.args[1]); span = row["weight_load_host_span"]
            self.assertFalse(row["load_completed"]); self.assertEqual(span["status"], "FAILED"); self.assertEqual(span["begin_ns"], 100); self.assertIsNone(span["end_ns"])

    def test_identity_and_timestamp_errors_cannot_mask_model_error(self):
        import json
        for source in self.sources():
            for fault in ("identity", "begin", "end"):
                original = RuntimeError("original loader failure") if fault != "end" else None
                namespace, _, _, _, modules = self.fixture(original)
                if fault == "identity": modules["sllm.logger"].get_clock_identity.side_effect = OSError("clock unavailable")
                stamps = [100, OSError("end clock")] if fault == "end" else [OSError("begin clock")]
                with mock.patch.dict(sys.modules, modules), mock.patch("time.monotonic_ns", side_effect=stamps):
                    if original:
                        with self.assertRaises(RuntimeError) as caught: exec(source, namespace)
                        self.assertIs(caught.exception, original)
                    else: exec(source, namespace)
                span = json.loads(namespace["logger"].info.call_args.args[1])["weight_load_host_span"]
                self.assertEqual(span["status"], "MISSING"); self.assertIn("error_type", span)

    def test_atomic_file_contains_endpoints_and_sink_failure_is_missing(self):
        import json
        for source in self.sources():
            with tempfile.TemporaryDirectory() as directory:
                namespace, _, _, _, modules = self.fixture(); namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = directory
                with mock.patch.dict(sys.modules, modules): exec(source, namespace)
                p = pathlib.Path(directory) / "rank-3-pid-123.json"
                self.assertEqual(json.loads(p.read_text())["weight_load_host_span"]["status"], "COMPLETE")
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            original = RuntimeError("original")
            namespace, _, _, _, modules = self.fixture(original)
            with tempfile.TemporaryDirectory() as directory:
                namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = directory
                with mock.patch.dict(sys.modules, modules), mock.patch("os.fsync", side_effect=OSError("sink failure")):
                    with self.assertRaises(RuntimeError) as caught: exec(source, namespace)
                self.assertIs(caught.exception, original); namespace["logger"].info.assert_not_called()
                self.assertEqual(json.loads(namespace["logger"].warning.call_args.args[1])["event"], "SLLM_LOAD_EVIDENCE_MISSING")

    def test_new_overlay_delta_is_only_endpoint_code_no_fingerprint(self):
        root = pathlib.Path(__file__).resolve().parents[2] / "build"
        old = (root / "kimi-vllm0191-load-evidence-files-v1/v1/worker/gpu_model_runner.py").read_text()
        new = (root / "kimi-vllm0191-load-endpoints-v1/v1/worker/gpu_model_runner.py").read_text()
        class StripEndpoints(ast.NodeTransformer):
            def visit_Assign(self, node):
                return None if any(isinstance(x, ast.Name) and x.id == "load_span" for x in node.targets) else self.generic_visit(node)
            def visit_If(self, node):
                return None if "SLLM_CAPTURE_LOAD_ENDPOINTS" in ast.unparse(node.test) or "load_span" in ast.unparse(node.test) else self.generic_visit(node)
        self.assertEqual(ast.dump(StripEndpoints().visit(ast.parse(new)), include_attributes=False), ast.dump(ast.parse(old), include_attributes=False))
        self.assertNotIn("_sllm_diagnostic_value_fingerprint", new)
        self.assertNotIn("SLLM_DIAGNOSTIC_VALUE_FINGERPRINT", new)


    def test_concurrent_eight_endpoint_records_have_complete_distinct_identities(self):
        import json
        import threading
        from concurrent.futures import ThreadPoolExecutor
        for source in self.sources():
            local = threading.local()
            _, _, _, clock, modules = self.fixture()
            modules["sllm.logger"].get_clock_identity.side_effect = lambda: dict(clock, process_startticks=2000 + local.rank)
            with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules, modules):
                def publish(rank):
                    local.rank = rank
                    namespace, _, _, _, _ = self.fixture()
                    namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"] = directory
                    namespace["os"].getpid = lambda: 1000 + rank
                    namespace["get_tp_group"] = lambda: types.SimpleNamespace(rank_in_group=rank)
                    exec(source, namespace)
                    namespace["logger"].warning.assert_not_called()
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(publish, range(8)))
                rows = [json.loads(p.read_text()) for p in pathlib.Path(directory).iterdir()]
                self.assertEqual(len(rows), 8)
                self.assertEqual({r["weight_load_host_span"]["tp_rank"] for r in rows}, set(range(8)))
                for row in rows:
                    span = row["weight_load_host_span"]
                    self.assertEqual(span["status"], "COMPLETE")
                    self.assertEqual(span["pid"], row["pid"])
                    self.assertEqual(span["clock_identity"]["process_startticks"], 2000 + row["tp_rank"])
                    self.assertEqual(span["model_instance_id"], "test-run-life1")
                    self.assertLessEqual(span["begin_ns"], span["end_ns"])


class LoadStageEndpointsTest(unittest.TestCase):
    def test_actual_optin_producer_declares_both_stages(self):
        import json
        namespace, _, _, _, modules = LoadHostEndpointsTest().fixture()
        with mock.patch.dict(sys.modules, modules):
            exec(LoadEvidenceTest().context(), namespace)
        span=json.loads(namespace['logger'].info.call_args.args[1])['weight_load_host_span']
        self.assertEqual(span['stage_schema'],'kimi-weight-load-host-stages-v1')
        self.assertEqual(set(span['stages']),{'weight_ingestion','postprocessing'})
        for stage in span['stages'].values():
            self.assertEqual(stage['status'],'MISSING')
            self.assertIsNone(stage['begin_ns']);self.assertIsNone(stage['end_ns'])

    def runner_sources(self):
        root=pathlib.Path(__file__).resolve().parents[2]/"build/kimi-vllm0191-load-stages-v1"
        tree=ast.parse((root/"v1/worker/gpu_model_runner.py").read_text())
        runner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="GPUModelRunner")
        method=next(n for n in runner.body if isinstance(n,ast.FunctionDef) and n.name=="load_model")
        context=next(n for n in ast.walk(method) if isinstance(n,ast.With) and any(isinstance(x.context_expr,ast.Call) and isinstance(x.context_expr.func,ast.Name) and x.context_expr.func.id=="DeviceMemoryProfiler" for x in n.items))
        return [LoadEvidenceTest().context(),compile(ast.Module(body=[context],type_ignores=[]),"<actual-stage-runner-overlay>","exec")]

    def loader_fixture(self, kind, online=False, failure=None):
        namespace, state, model, clock, modules=LoadHostEndpointsTest().fixture()
        events=[]
        root=pathlib.Path(__file__).resolve().parents[2]/"build/kimi-vllm0191-load-stages-v1/model_executor/model_loader"
        base_tree=ast.parse((root/"base_loader.py").read_text())
        base_class=next(n for n in base_tree.body if isinstance(n,ast.ClassDef) and n.name=="BaseModelLoader")
        helper=next(n for n in base_class.body if isinstance(n,ast.FunctionDef) and n.name=="_sllm_stage_endpoint")
        filename="base_loader.py" if kind=="SF" else "sllm_loader.py"
        tree=ast.parse((root/filename).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="load_model")
        method.decorator_list=[]
        original=RuntimeError("actual-"+str(failure))
        def action(name):
            events.append(name)
            if name==failure:raise original
        class Device:
            type="cuda"
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __str__(self):return "cuda:0"
        def initialize(**kwargs):action("construct");return model
        def evaluate():action("eval");return model
        model.eval=evaluate
        named=namespace["self"].model_config=types.SimpleNamespace(model="/fixture",dtype="fake")
        config=types.SimpleNamespace(device_config=types.SimpleNamespace(device="cuda"),load_config=types.SimpleNamespace(device=None))
        namespace["self"].vllm_config=config
        torch=namespace["torch"];torch.device=lambda _:Device();torch.empty=lambda *a,**kw:object();torch.cuda.current_device=lambda:0
        loaderenv=dict(torch=torch,nn=types.SimpleNamespace(Module=object),VllmConfig=object,ModelConfig=object,os=types.SimpleNamespace(path=types.SimpleNamespace(isdir=lambda _:True,join=os.path.join,normpath=os.path.normpath,expanduser=lambda path:"/fixture"),getenv=lambda *a:"/fixture",pathsep=os.pathsep,sep=os.sep),gc=types.SimpleNamespace(collect=lambda:action("collect")),set_default_torch_dtype=lambda _:contextlib.nullcontext(),initialize_model=initialize,log_model_inspection=lambda _:action("inspection"),logger=namespace["logger"],current_platform=types.SimpleNamespace(is_cuda=lambda:False),_has_online_quant=lambda _:action("online_predicate") or online,finalize_layerwise_processing=lambda *a:action("finalize"),process_weights_after_loading=lambda *a:action("post"),process_attention_weights_after_loading=lambda *a:action("attention"))
        exec(compile(ast.Module(body=[helper,method],type_ignores=[]),"<actual-stage-loader-methods>","exec"),loaderenv)
        params=list(model.named_parameters());buffer=list(model.named_buffers())[0][1]
        class BufferData:
            def to(self,device):action("buffer_to");return self
        buffer.data=BufferData();buffer.device=types.SimpleNamespace(type="cpu")
        # Real Store method performs every assignment; trap both reset and restored binding.
        tracked=[]
        for name,tensor in params:
            tensor.data=object()
            class Tracked(type(tensor)):
                def __setattr__(self,key,value):
                    if key=="data":events.append("bind:"+self.label)
                    object.__setattr__(self,key,value)
            changed=Tracked.__new__(Tracked);changed.__dict__.update(tensor.__dict__);changed.label=name
            tracked.append((name,changed))
        model.named_parameters=mock.Mock(return_value=tracked)
        model.state_dict=lambda:dict(tracked)
        model.modules=lambda:[types.SimpleNamespace(quant_method=types.SimpleNamespace(prepare_runtime_for_restored_weights=lambda _:action("runtime")))]
        def restore(*args):
            action("store_ingest")
            return {name:object() for name,_ in tracked}
        store=types.ModuleType("sllm_store");store.__path__=[]
        store_torch=types.ModuleType("sllm_store.torch");store_torch.load_dict=restore
        vllm=types.ModuleType("vllm");vllm.__path__=[]
        distributed=types.ModuleType("vllm.distributed");distributed.get_tensor_model_parallel_rank=lambda:0
        modules.update({"sllm_store":store,"sllm_store.torch":store_torch,"vllm":vllm,"vllm.distributed":distributed})
        loader=type("ActualStageLoader",(),{"_sllm_stage_endpoint":loaderenv["_sllm_stage_endpoint"],"load_model":loaderenv["load_model"],"load_weights":lambda *a:action("ingest"),"_filter_subtensors":staticmethod(lambda values:values)})()
        namespace["get_model_loader"]=lambda _:loader
        return namespace,events,modules,loader,original

    def test_actual_sf_store_calls_and_all_six_endpoints(self):
        import json
        for source in self.runner_sources():
            for kind,online in (("SF",False),("SF",True),("Store",False)):
                with self.subTest(kind=kind,online=online):
                    namespace,events,modules,loader,_=self.loader_fixture(kind,online)
                    ticks=iter((100,110,120,130,140,150))
                    def stamp():
                        value=next(ticks);events.append("ns:"+str(value));return value
                    with mock.patch.dict(sys.modules,modules),mock.patch("time.monotonic_ns",side_effect=stamp):
                        exec(source,namespace)
                    span=json.loads(namespace["logger"].info.call_args.args[1])["weight_load_host_span"]
                    self.assertEqual((span["begin_ns"],span["end_ns"]),(100,150))
                    self.assertEqual(span["stage_schema"],"kimi-weight-load-host-stages-v1")
                    boundaries=(("before_base_load_weights","after_base_load_weights_return"),("before_online_quant_condition","after_process_weights_after_loading_return")) if kind=="SF" else (("before_store_load_dict","after_store_data_bindings_and_buffer_moves"),("before_store_runtime_hook_traversal","after_store_attention_postprocessing_return"))
                    for key,expected in zip(("weight_ingestion","postprocessing"),boundaries):
                        self.assertEqual((span["stages"][key]["begin_boundary"],span["stages"][key]["end_boundary"]),expected)
                    for key,expected in (("weight_ingestion",(110,120)),("postprocessing",(130,140))):
                        stage=span["stages"][key]
                        self.assertEqual(stage["status"],"COMPLETE");self.assertEqual((stage["begin_ns"],stage["end_ns"]),expected)
                    if kind=="SF":
                        self.assertLess(events.index("ns:110"),events.index("ingest"));self.assertLess(events.index("ingest"),events.index("ns:120"))
                        self.assertLess(events.index("ns:130"),events.index("online_predicate"));self.assertLess(events.index("post"),events.index("ns:140"))
                        self.assertEqual("finalize" in events,online)
                        self.assertLess(events.index("ns:140"),events.index("eval"))
                    else:
                        self.assertLess(events.index("ns:110"),events.index("store_ingest"))
                        positions=[i for i,name in enumerate(events) if name.startswith("bind:")]
                        restore=events.index("store_ingest")
                        self.assertEqual(len([i for i in positions if i>restore]),3)
                        self.assertTrue(all(i<events.index("ns:120") for i in positions))
                        self.assertLess(events.index("buffer_to"),events.index("ns:120"))
                        self.assertLess(events.index("ns:130"),events.index("runtime"))
                        self.assertLess(events.index("attention"),events.index("ns:140"))
                    namespace["torch"].cuda.synchronize.assert_called_once()

    def test_actual_default_off_has_no_stage_clock_or_identity_reads(self):
        for kind in ("SF","Store"):
            namespace,events,modules,loader,_=self.loader_fixture(kind)
            namespace["os"].environ.pop("SLLM_CAPTURE_LOAD_ENDPOINTS")
            with mock.patch.dict(sys.modules,modules),mock.patch("time.monotonic_ns",side_effect=AssertionError("default clock read")) as clock:
                exec(self.runner_sources()[0],namespace)
            clock.assert_not_called();modules["sllm.logger"].get_clock_identity.assert_not_called()
            self.assertFalse(hasattr(loader,"_sllm_load_stages"))

    def test_actual_loader_exception_retains_partial_stage_and_original_object(self):
        import json
        for source in self.runner_sources():
            for kind,phase,stage in (("SF","construct",None),("SF","ingest","weight_ingestion"),("SF","post","postprocessing"),("Store","store_ingest","weight_ingestion"),("Store","runtime","postprocessing"),("Store","attention","postprocessing")):
                with self.subTest(kind=kind,phase=phase):
                    namespace,_,modules,_,original=self.loader_fixture(kind,failure=phase)
                    with mock.patch.dict(sys.modules,modules):
                        with self.assertRaises(RuntimeError) as caught:exec(source,namespace)
                    self.assertIs(caught.exception,original)
                    row=json.loads(namespace["logger"].info.call_args.args[1]);span=row["weight_load_host_span"]
                    self.assertFalse(row["load_completed"]);self.assertEqual(span["status"],"FAILED")
                    if stage is not None:
                        self.assertEqual(span["stages"][stage]["status"],"FAILED")
                        self.assertIsNone(span["stages"][stage]["end_ns"])
                    else:self.assertTrue(all(x["status"]=="MISSING" for x in span["stages"].values()))

    def test_actual_stage_timestamp_errors_are_missing_and_do_not_mask_model_error(self):
        import json
        for failed_call in (2,3,4,5):
            namespace,_,modules,_,_=self.loader_fixture("SF")
            count=[0]
            def stamp():
                count[0]+=1
                if count[0]==failed_call:raise OSError("stage clock")
                return count[0]*100
            with mock.patch.dict(sys.modules,modules),mock.patch("time.monotonic_ns",side_effect=stamp):
                exec(self.runner_sources()[0],namespace)
            span=json.loads(namespace["logger"].info.call_args.args[1])["weight_load_host_span"]
            self.assertTrue(any(x["status"]=="MISSING" and x.get("error_type")=="OSError" for x in span["stages"].values()))
        for invalid in (0,-1,"invalid",True):
            namespace,_,modules,_,_=self.loader_fixture("SF")
            calls=[0]
            def invalid_stamp():
                calls[0]+=1
                return invalid if calls[0]==2 else calls[0]*100
            with mock.patch.dict(sys.modules,modules),mock.patch("time.monotonic_ns",side_effect=invalid_stamp):
                exec(self.runner_sources()[0],namespace)
            stage=json.loads(namespace["logger"].info.call_args.args[1])["weight_load_host_span"]["stages"]["weight_ingestion"]
            self.assertEqual(stage["status"],"MISSING");self.assertIsNone(stage["begin_ns"]);self.assertIsNone(stage["end_ns"])
        namespace,_,modules,_,original=self.loader_fixture("SF",failure="ingest")
        with mock.patch.dict(sys.modules,modules),mock.patch("time.monotonic_ns",side_effect=[100,OSError("begin")]):
            with self.assertRaises(RuntimeError) as caught:exec(self.runner_sources()[0],namespace)
        self.assertIs(caught.exception,original)

    def test_actual_eight_rank_stage_files_atomic_complete_and_no_overwrite(self):
        import json
        import threading
        from concurrent.futures import ThreadPoolExecutor
        for source in self.runner_sources():
            local=threading.local()
            _,_,_,clock,modules=LoadHostEndpointsTest().fixture()
            modules["sllm.logger"].get_clock_identity.side_effect=lambda:dict(clock,process_startticks=3000+local.rank)
            with tempfile.TemporaryDirectory() as directory,mock.patch.dict(sys.modules,modules):
                def publish(rank):
                    local.rank=rank
                    namespace,_,_,_,_=self.loader_fixture("SF")
                    namespace["os"].environ["SLLM_LOAD_EVIDENCE_DIR"]=directory
                    namespace["os"].getpid=lambda:2000+rank
                    namespace["get_tp_group"]=lambda:types.SimpleNamespace(rank_in_group=rank)
                    exec(source,namespace)
                    namespace["logger"].warning.assert_not_called()
                    return namespace
                with ThreadPoolExecutor(max_workers=8) as pool:namespaces=list(pool.map(publish,range(8)))
                files=list(pathlib.Path(directory).iterdir());self.assertEqual(len(files),8)
                saved={path.name:path.read_bytes() for path in files}
                for path in files:
                    row=json.loads(path.read_text());span=row["weight_load_host_span"];rank=row["tp_rank"]
                    self.assertEqual(span["clock_identity"]["process_startticks"],3000+rank)
                    self.assertEqual(span["pid"],row["pid"]);self.assertEqual(span["tp_rank"],rank)
                    self.assertEqual(span["run_id"],"test-run");self.assertEqual(span["model_instance_id"],"test-run-life1")
                    self.assertEqual(span["stage_schema"],"kimi-weight-load-host-stages-v1")
                    ingestion=span["stages"]["weight_ingestion"];post=span["stages"]["postprocessing"]
                    self.assertEqual((ingestion["status"],post["status"]),("COMPLETE","COMPLETE"))
                    self.assertLessEqual(span["begin_ns"],ingestion["begin_ns"])
                    self.assertLessEqual(ingestion["begin_ns"],ingestion["end_ns"])
                    self.assertLessEqual(ingestion["end_ns"],post["begin_ns"])
                    self.assertLessEqual(post["begin_ns"],post["end_ns"])
                    self.assertLessEqual(post["end_ns"],span["end_ns"])
                    self.assertEqual(path.stat().st_mode&0o777,0o600)
                local.rank=0;exec(source,namespaces[0])
                self.assertEqual(saved,{path.name:path.read_bytes() for path in files})
                self.assertEqual(json.loads(namespaces[0]["logger"].warning.call_args.args[1])["event"],"SLLM_LOAD_EVIDENCE_MISSING")

    def test_strip_stage_instrumentation_restores_actual_previous_module_asts(self):
        root=pathlib.Path(__file__).resolve().parents[2]/"build/kimi-vllm0191-load-stages-v1"
        class Strip(ast.NodeTransformer):
            def visit_FunctionDef(self,node):
                return None if node.name=="_sllm_stage_endpoint" else self.generic_visit(node)
            def visit_Expr(self,node):
                if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=="_sllm_stage_endpoint":return None
                return self.generic_visit(node)
            def visit_Assign(self,node):
                if any(isinstance(x,ast.Attribute) and x.attr=="_sllm_load_stages" for x in node.targets):return None
                if any(isinstance(x,ast.Name) and x.id=="load_span" for x in node.targets) and isinstance(node.value,ast.Dict):
                    pairs=[(k,v) for k,v in zip(node.value.keys,node.value.values) if not (isinstance(k,ast.Constant) and k.value in ("stages","stage_schema"))]
                    node.value.keys=[k for k,v in pairs];node.value.values=[v for k,v in pairs]
                return self.generic_visit(node)
            def visit_For(self,node):
                if isinstance(node.target,ast.Name) and node.target.id=="stage" and 'load_span' in ast.unparse(node.iter):return None
                return self.generic_visit(node)
        import json
        manifest=json.loads((root/"build-manifest.json").read_text())
        import hashlib
        self.assertNotIn("_sllm_diagnostic_value_fingerprint",(root/"v1/worker/gpu_model_runner.py").read_text())
        self.assertNotIn("SLLM_DIAGNOSTIC_VALUE_FINGERPRINT",(root/"v1/worker/gpu_model_runner.py").read_text())
        for row in manifest["modules"]:
            for section in ("base","output","delta"):
                path=pathlib.Path(row[section]["path"])
                self.assertTrue(path.is_file());self.assertFalse(path.is_symlink())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row[section]["sha256"])
            before=ast.parse(pathlib.Path(row["base"]["path"]).read_text())
            after=Strip().visit(ast.parse(pathlib.Path(row["output"]["path"]).read_text()))
            self.assertEqual(ast.dump(before,include_attributes=False),ast.dump(after,include_attributes=False))
        patch=(pathlib.Path(__file__).resolve().parents[2]/"vllm_patch/sllm_load.patch").read_text()
        # The actual canonical SLLM producer is the complete new-file patch body.
        section=patch.split("+++ b/vllm/model_executor/model_loader/sllm_loader.py\n",1)[1].split("diff --git ",1)[0]
        canonical="".join(line[1:] for line in section.splitlines(True) if line.startswith("+"))
        self.assertEqual(ast.dump(ast.parse(canonical),include_attributes=False),ast.dump(ast.parse((root/"model_executor/model_loader/sllm_loader.py").read_text()),include_attributes=False))
        # Runner fragments are compared as real AST nodes, allowing the existing canonical FP-only branch to stay separate.
        fragments=[]
        for source in self.runner_sources():
            tree=ast.parse(LoadEvidenceTest().context()) if isinstance(source,str) else None
            if tree is not None:
                fragments.append([ast.dump(n,include_attributes=False) for n in ast.walk(tree) if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=="_sllm_load_stages" for t in n.targets)) or (isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=="stage")])
        overlay=ast.parse((root/"v1/worker/gpu_model_runner.py").read_text())
        expected=[ast.dump(n,include_attributes=False) for n in ast.walk(overlay) if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=="_sllm_load_stages" for t in n.targets)) or (isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=="stage")]
        self.assertEqual(fragments[0],expected)
