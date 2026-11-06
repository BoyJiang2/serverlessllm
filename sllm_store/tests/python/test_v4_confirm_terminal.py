"""Compile actual async submission/confirmation functions with CPU fake workers."""
from pathlib import Path
import subprocess
import tempfile
import unittest

PREFIX = r"""
#include <chrono>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#define LOG(level) std::cerr
using MemCopyHandleList=std::vector<int>;
using MemCopyChunkList=std::vector<int>;
struct Model { int calls=0; int FreeHost() {return 0;} int WaitInGpu(const std::string&) {++calls;return 0;} };
class CheckpointStore {public:
 std::mutex model_info_mutex_;
 std::unordered_map<std::string,std::shared_ptr<Model>> model_map_;
 std::queue<std::future<int>> async_tasks_;
 std::unordered_map<std::string,std::unordered_map<std::string,std::shared_future<int>>> gpu_load_tasks_;
 int ClearMem();
 int mode=0; std::promise<void>* release=nullptr; std::shared_future<void> gate;
 int LoadModelFromMem(const std::string&,const std::string&,
   const std::unordered_map<std::string,MemCopyHandleList>&,
   const std::unordered_map<std::string,MemCopyChunkList>&) {
   if(release) gate.wait();
   if(mode==2) throw std::runtime_error("worker failed");
   return mode==1?-1:0;
 }
 int LoadModelFromMemAsync(const std::string&,const std::string&,
   const std::unordered_map<std::string,MemCopyHandleList>&,
   const std::unordered_map<std::string,MemCopyChunkList>&);
 int WaitModelInGpu(const std::string&,const std::string&);
};
"""
MAIN = r"""
int main() {
 bool ok=true;
 for(int mode: {0,1,2}) {
  CheckpointStore store;store.mode=mode;
  auto model=std::make_shared<Model>();store.model_map_["model"]=model;
  ok=ok && store.LoadModelFromMemAsync("model","replica",{}, {})==0;
  for(int retry=0;retry<6;++retry) {
   int status=store.WaitModelInGpu("model","replica");
   ok=ok && (mode==0?status==0:status!=0);
  }
  ok=ok && model->calls==0;
  if(mode!=0)ok=ok && store.ClearMem()!=0 && store.model_map_.size()==1;
  ok=ok && store.LoadModelFromMemAsync("model","replica",{}, {})!=0;
  ok=ok && store.WaitModelInGpu("model","unknown")!=0;
 }
 CheckpointStore store;auto model=std::make_shared<Model>();store.model_map_["model"]=model;
 std::promise<void> release;store.release=&release;store.gate=release.get_future().share();
 store.LoadModelFromMemAsync("model","pending",{},{});
 auto waiting=std::async(std::launch::async,[&]{return store.WaitModelInGpu("model","pending");});
 bool pending=waiting.wait_for(std::chrono::milliseconds(50))==std::future_status::timeout;
 // The metadata mutex must remain available while a confirmation waits.
 bool unlocked=store.model_info_mutex_.try_lock();if(unlocked)store.model_info_mutex_.unlock();
 bool refused=store.ClearMem()!=0;
 release.set_value();ok=ok && pending && unlocked && refused && waiting.get()==0;
 ok=ok && store.ClearMem()==0 && store.WaitModelInGpu("model","pending")==0;
 std::cout << (ok?"terminal contracts passed":"terminal contracts failed") << std::endl;
 return ok?0:1;
}
"""

class ConfirmTerminalTests(unittest.TestCase):
    def test_async_failure_exception_duplicate_and_repeated_confirm(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / "csrc/sllm_store/checkpoint_store.cpp").read_text()
        a = text.index("int CheckpointStore::LoadModelFromMemAsync(")
        b = text.index("\nint CheckpointStore::UnloadModelFromHost(", a)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(PREFIX + text[a:b] + MAIN)
            build = subprocess.run(["g++", "-std=c++17", "-pthread", str(path / "test.cpp"), "-o", str(path / "test")], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_failed_copy_does_not_erase_replica_before_confirm(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / "csrc/sllm_store/checkpoint_store.cpp").read_text()
        a = text.index("int CheckpointStore::LoadModelFromMem(")
        b = text.index("int CheckpointStore::LoadModelFromMemAsync(", a)
        self.assertNotIn("model->FreeGpu(replica_uuid)", text[a:b])

if __name__ == "__main__":
    unittest.main()
