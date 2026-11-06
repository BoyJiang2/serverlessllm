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
