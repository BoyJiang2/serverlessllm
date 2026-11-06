"""Actual C++ host completion methods and CPU RPC handlers with controlled workers."""
import ast
import asyncio
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock
import uuid

import test_confirm_model_event_loop as rpc_fixture


PREFIX = r'''
#include <atomic>
#include <chrono>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#define LOG(level) std::cerr
enum class MemoryState { UNALLOCATED, LOADED };
struct Pinned { int num_chunks() { return 1; } };
struct Model {
 std::mutex mutex_; MemoryState state_=MemoryState::UNALLOCATED;
 std::shared_ptr<Pinned> pinned_mem_;
 bool IsHostLoaded();
 int FreeHost() { std::lock_guard<std::mutex> guard(mutex_);
   state_=MemoryState::UNALLOCATED;pinned_mem_.reset();return 0; }
};
class CheckpointStore {public:
 std::mutex model_info_mutex_;
 std::unordered_map<std::string,std::shared_ptr<Model>> model_map_;
 using Tasks=std::unordered_map<std::string,std::unordered_map<std::string,std::shared_future<int>>>;
 Tasks cpu_load_tasks_,gpu_load_tasks_;
 std::shared_future<void> gate; int mode=0; std::atomic<int> calls{0};
 int LoadModelFromDisk(const std::string& path) {
  ++calls;if(gate.valid())gate.wait();
  if(mode==2)throw std::runtime_error("read failed");
  if(mode==1)return -1;
  auto model=model_map_.at(path);std::lock_guard<std::mutex> lock(model->mutex_);
  model->state_=MemoryState::LOADED;model->pinned_mem_=std::make_shared<Pinned>();return 0;
 }
 int LoadModelFromDiskAsync(const std::string&,const std::string& = "");
 int WaitModelInCpu(const std::string&,const std::string&);
 int ClearMem();int UnloadModelFromHost(const std::string&);
};
'''
MAIN = r'''
int main() {
 bool ok=true;
 for(int mode:{0,1,2}) {
  CheckpointStore store;store.mode=mode;store.model_map_["m"]=std::make_shared<Model>();
  ok=ok && store.LoadModelFromDiskAsync("m","read")==0;
  for(int n=0;n<6;++n)ok=ok && ((store.WaitModelInCpu("m","read")==0)==(mode==0));
  ok=ok && store.WaitModelInCpu("m","unknown")!=0 && store.WaitModelInCpu("m","")!=0;
  ok=ok && store.LoadModelFromDiskAsync("m","read")!=0;
  if(mode) {
   ok=ok && store.LoadModelFromDiskAsync("m","retry")!=0;
   ok=ok && store.LoadModelFromDiskAsync("m")!=0;
   ok=ok && store.ClearMem()!=0 && store.UnloadModelFromHost("m")!=0;
   ok=ok && store.model_map_.size()==1;
  } else {
   ok=ok && store.UnloadModelFromHost("m")==0;
   ok=ok && store.WaitModelInCpu("m","read")!=0;
   ok=ok && store.LoadModelFromDiskAsync("m","again")==0;
   ok=ok && store.WaitModelInCpu("m","again")==0;
   ok=ok && store.ClearMem()==0 && store.WaitModelInCpu("m","again")!=0;
  }
 }
 CheckpointStore store;store.model_map_["m"]=std::make_shared<Model>();
 std::promise<void> release;store.gate=release.get_future().share();
 ok=ok && store.LoadModelFromDiskAsync("missing","x")!=0;
 ok=ok && store.LoadModelFromDiskAsync("m")==0;
 ok=ok && store.LoadModelFromDiskAsync("m","one")==0;
 ok=ok && store.LoadModelFromDiskAsync("m","two")==0;
 auto confirm=std::async(std::launch::async,[&]{return store.WaitModelInCpu("m","two");});
 bool pending=confirm.wait_for(std::chrono::milliseconds(50))==std::future_status::timeout;
 bool unlocked=store.model_info_mutex_.try_lock();if(unlocked)store.model_info_mutex_.unlock();
 bool refused=store.ClearMem()!=0 && store.UnloadModelFromHost("m")!=0;
 release.set_value();ok=ok && pending && unlocked && refused && confirm.get()==0;
 ok=ok && store.calls==1 && store.WaitModelInCpu("m","one")==0;
 // Native LRU eviction must invalidate historical success as residency evidence.
 store.model_map_["m"]->FreeHost();ok=ok && store.WaitModelInCpu("m","one")!=0;
 std::promise<int> copy;store.gpu_load_tasks_["m"]["g"]=copy.get_future().share();
 ok=ok && store.UnloadModelFromHost("m")!=0;copy.set_value(-1);
 ok=ok && store.UnloadModelFromHost("m")!=0;
 std::cout<<(ok?"host terminal contracts passed":"host terminal contracts failed")<<std::endl;
 return ok?0:1;
}
'''


class NativeHostTests(unittest.TestCase):
    def test_actual_async_confirm_clear_unload_and_residency_methods(self):
        root = Path(__file__).resolve().parents[2] / "csrc/sllm_store"
        source = (root / "checkpoint_store.cpp").read_text()
        a = source.index("int CheckpointStore::LoadModelFromDiskAsync(")
        b = source.index("\nint CheckpointStore::LoadModelFromMem(", a)
        methods = source[a:b]
        a = source.index("int CheckpointStore::ClearMem()")
        b = source.index("\nModelPtr CheckpointStore::GetModelPtr", a)
        methods += source[a:b]
        source = (root / "model.cpp").read_text()
        a, b = source.index("bool Model::IsHostLoaded()"), source.index("int Model::WaitInHost()")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(PREFIX + source[a:b] + methods + MAIN)
            build = subprocess.run(["g++", "-std=c++17", "-pthread", str(path / "test.cpp"),
                                    "-o", str(path / "test")], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_cpu_wait_and_unload_release_the_gil(self):
        path = Path(__file__).resolve().parents[2] / "csrc/sllm_store/checkpoint_store_py.cpp"
        text = path.read_text()
        for name in ("wait_model_in_cpu", "unload_model_from_host"):
            section = text.split(f'.def("{name}"', 1)[1].split('.def(', 1)[0]
            self.assertIn("py::call_guard<py::gil_scoped_release>()", section)


class CpuRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_cpu_submission_identity_reaches_the_native_worker(self):
        path = Path(__file__).resolve().parents[2] / "sllm_store/server.py"
        cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "LoadModelAsync")
        proto = types.SimpleNamespace(DEVICE_TYPE_CPU=0, DEVICE_TYPE_GPU=1,
            LoadModelResponse=rpc_fixture.FakeProto.ConfirmModelResponse)
        namespace = dict(storage_pb2=proto, grpc=rpc_fixture.FakeGrpc,
                         logger=types.SimpleNamespace(error=lambda *a: None, info=lambda *a: None))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        load = mock.Mock(return_value=0)
        owner = types.SimpleNamespace(registration_required=True,
            storage=types.SimpleNamespace(load_model_from_disk_async=load))
        for token in ("cpu-submission", ""):
            response = await namespace["LoadModelAsync"](owner,
                types.SimpleNamespace(model_path="m", replica_uuid=token, target_device_type=0),
                types.SimpleNamespace(set_code=mock.Mock()))
            load.assert_called_with("m", token)
            self.assertEqual(response.model_path, "m")

    async def test_cpu_confirm_waits_off_loop_and_checks_uuid(self):
        handler, _ = rpc_fixture.confirm_model()
        codes, beats = [], []
        storage = types.SimpleNamespace(wait_model_in_cpu=lambda *args: time.sleep(0.15) or 0)
        request = types.SimpleNamespace(model_path="m", replica_uuid="read-1", target_device_type=0)
        context = types.SimpleNamespace(set_code=codes.append)
        task = asyncio.create_task(handler(types.SimpleNamespace(storage=storage), request, context))
        while not task.done():
            beats.append(time.monotonic())
            await asyncio.sleep(0.01)
        self.assertEqual((await task).model_path, "m")
        self.assertGreaterEqual(len(beats), 5)
        self.assertFalse(codes)
        request.replica_uuid = ""
        response = await handler(types.SimpleNamespace(storage=storage), request, context)
        self.assertEqual(response.model_path, "")
        self.assertEqual(codes[-1], "INVALID_ARGUMENT")

    async def test_failed_cpu_confirm_returns_internal_without_retrying_a_failed_copy(self):
        handler, _ = rpc_fixture.confirm_model()
        wait = mock.Mock(return_value=1)
        codes = []
        request = types.SimpleNamespace(model_path="m", replica_uuid="failed-read", target_device_type=0)
        response = await handler(types.SimpleNamespace(storage=types.SimpleNamespace(wait_model_in_cpu=wait)),
                                 request, types.SimpleNamespace(set_code=codes.append))
        self.assertEqual(response.model_path, "")
        self.assertEqual(codes, ["INTERNAL"])
        wait.assert_called_once_with("m", "failed-read")


class CpuClientTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "sllm_store/client.py"
        cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef))
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                   and n.name in ("load_cpu_confirmed", "confirm_cpu_model_loaded")]
        proto = types.SimpleNamespace(LoadModelRequest=lambda **k: types.SimpleNamespace(**k),
            ConfirmModelRequest=lambda **k: types.SimpleNamespace(**k),
            DeviceType=types.SimpleNamespace(DEVICE_TYPE_CPU=0))
        namespace = dict(uuid=uuid, storage_pb2=proto)
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
        client = type("Client", (), {m.name: namespace[m.name] for m in methods})
        self.client = client()
        self.client.stub = types.SimpleNamespace(
            LoadModelAsync=mock.Mock(return_value=types.SimpleNamespace(model_path="m")),
            ConfirmModel=mock.Mock(return_value=types.SimpleNamespace(model_path="m")))

    def test_same_submission_id_connects_acceptance_and_terminal_confirmation(self):
        token = self.client.load_cpu_confirmed("m")
        load = self.client.stub.LoadModelAsync.call_args.args[0]
        confirm = self.client.stub.ConfirmModel.call_args.args[0]
        self.assertEqual(load.replica_uuid, token)
        self.assertEqual(confirm.replica_uuid, token)
        self.assertEqual(confirm.target_device_type, 0)
        self.assertNotEqual(token, self.client.load_cpu_confirmed("m"))

    def test_mismatched_or_failed_load_and_confirm_never_return_success(self):
        for call in (self.client.stub.LoadModelAsync, self.client.stub.ConfirmModel):
            call.return_value = types.SimpleNamespace(model_path="wrong")
            with self.assertRaises(RuntimeError):
                self.client.load_cpu_confirmed("m")
            call.return_value = types.SimpleNamespace(model_path="m")
        self.client.stub.ConfirmModel.side_effect = RuntimeError("transport failed")
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            self.client.load_cpu_confirmed("m")
