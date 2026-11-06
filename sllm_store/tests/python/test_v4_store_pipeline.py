"""Full actual Model source + Store read/Confirm methods; CPU memory/CUDA stubs."""
from pathlib import Path
import subprocess
import tempfile
import unittest


CUDA = r'''
#pragma once
#include <atomic>
#include <cstring>
#include <thread>
inline std::atomic<int> copies{0}, closes{0};
inline std::atomic<bool> block_copy{false},release_copy{false};
inline bool fail_copy=false, fail_device=false;
using cudaError_t=int;
constexpr int cudaSuccess=0,cudaMemcpyHostToDevice=1;
inline int cudaSetDevice(int) { return fail_device?1:0; }
inline const char* cudaGetErrorString(int) { return "CPU injected CUDA failure"; }
inline int cudaGetLastError() { return 1; }
inline int cudaIpcCloseMemHandle(void*) { ++closes;return 0; }
inline int cudaMemcpy(void* dst,const void* src,size_t size,int) {
 if(fail_copy)return 1;std::memcpy(dst,src,size);++copies;
 while(block_copy && !release_copy)std::this_thread::yield();return 0;
}
'''
PINNED = r'''
#pragma once
#include <cstdlib>
#include <memory>
#include <vector>
struct PinnedMemoryPool { int failure=0; };
class PinnedMemory {public:
 std::vector<char*> buffers;
 ~PinnedMemory() {for(auto p:buffers)std::free(p);}
 int Allocate(size_t size,std::shared_ptr<PinnedMemoryPool> pool) {
  if(pool->failure)return pool->failure;
  for(size_t i=0;i<(size+4095)/4096;++i) {
   void* ptr=nullptr;if(posix_memalign(&ptr,4096,4096))return -1;
   buffers.push_back(static_cast<char*>(ptr));
  }return 0;
 }
 size_t num_chunks() {return buffers.size();}
 size_t chunk_size() {return 4096;}
 std::vector<char*>& get() {return buffers;}
};
'''
HOOKS = r'''
#pragma once
#include <unistd.h>
#include <fcntl.h>
#include <atomic>
#include <chrono>
#include <stdexcept>
#include <thread>
inline std::atomic<bool> block_second{false},second_entered{false},release_second{false};
inline int read_mode=0;inline bool fail_open=false;
inline int controlled_open(const char* path,int flags) {
 if(fail_open){errno=EIO;return -1;}return ::open(path,flags & ~O_DIRECT);
}
inline ssize_t controlled_pread(int fd,void* ptr,size_t size,off_t offset) {
 if(offset==4096) {
  second_entered=true;
  while(block_second && !release_second)std::this_thread::yield();
  if(read_mode==1)return 0;
  if(read_mode==2)throw std::runtime_error("injected read exception");
 }
 return ::pread(fd,ptr,size,offset);
}
'''
STORE = r'''
#include "model.h"
#include <iostream>
#include <atomic>
class CheckpointStore {public:
 std::mutex model_info_mutex_;
 std::unordered_map<std::string,std::shared_ptr<Model>> model_map_;
 std::unordered_map<std::string,std::chrono::time_point<std::chrono::system_clock>> model_last_access_time_;
 std::shared_ptr<PinnedMemoryPool> memory_pool_=std::make_shared<PinnedMemoryPool>();
 std::unordered_map<std::string,std::unordered_map<std::string,std::shared_future<int>>> cpu_load_tasks_;
 std::unordered_map<std::string,std::unordered_map<std::string,std::shared_future<int>>> gpu_load_tasks_;
 int num_thread_=1;
 ModelPtr GetModelPtr(const std::string& name) {return model_map_.at(name);}
 int LoadModelFromDisk(const std::string&);
 int LoadModelFromDiskAsync(const std::string&,const std::string& = "");
 int WaitModelInCpu(const std::string&,const std::string&);
 int ClearMem();
};
'''
MAIN = r'''
#include "fixture_hooks.h"
#include <algorithm>
#include <functional>
#include <sys/resource.h>
bool until(std::function<bool()> ready) {
 auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(1);
 while(!ready() && std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
 return ready();
}
int main(int argc,char** argv) {
 struct rlimit core_limit{0,0};setrlimit(RLIMIT_CORE,&core_limit);
 auto model=std::make_shared<Model>("model");
 if(model->Initialize(argv[1])!=0)return 9;
 CheckpointStore store;store.model_map_["model"]=model;
 std::string mode=argv[2];std::vector<char> output(8192);
 auto gpu=[&]{
  if(mode=="dispatch_exception")return model->ToGpu("replica",{{0,{output.data()}}},
    {{0,{MemCopyChunk{0,8192,0,0}}}},{{1,{MemCopyHandle{"fake"}}}});
  if(mode=="copy_exception")return model->ToGpu("replica",{{0,{output.data()}}},
    {{0,{MemCopyChunk{0,8192,0,1}}}},{{0,{MemCopyHandle{"fake0"},MemCopyHandle{"fake1"}}}});
  return model->ToGpu("replica",{{0,{output.data()}}},
    {{0,{MemCopyChunk{0,8192,0,0}}}},{{0,{MemCopyHandle{"fake"}}}});
 };
 if(mode=="missing_read") {
  int ret=gpu();return ret!=0 && closes==1?0:10;
 }
 if(mode=="allocation_failure")store.memory_pool_->failure=-1;
 if(mode=="pool_exhausted")store.memory_pool_->failure=2;
 if(mode=="open_failure")fail_open=true;
 if(mode=="short_read" || mode=="copy_pending")read_mode=1;
 if(mode=="copy_pending")block_copy=true;
 if(mode=="read_exception")read_mode=2;
 if(mode=="copy_failure")fail_copy=true;
 if(mode=="device_failure")fail_device=true;
 bool producer_fault=read_mode || fail_open || store.memory_pool_->failure;
 bool pipeline=mode=="overlap" || read_mode;
 block_second=pipeline;
 if(store.LoadModelFromDiskAsync("model","read")!=0)return 11;
 if(pipeline && !until([]{return second_entered.load();}))return 12;
 if(!pipeline && producer_fault)store.WaitModelInCpu("model","read");
 auto copy=std::async(std::launch::async,gpu);
 bool overlapped=true;
 if(pipeline) {
  overlapped=until([]{return copies.load()>0;});
  release_second=true;
 }
 auto read=std::async(std::launch::async,[&]{return store.WaitModelInCpu("model","read");});
 bool cancelled_retained=true;
 if(mode=="copy_pending") {
  if(read.wait_for(std::chrono::seconds(1))!=std::future_status::ready)std::_Exit(15);
  cancelled_retained=model->WaitInGpu("replica")!=0 && model->FreeHost()!=0 && model->TryFreeHost()<0;
  release_copy=true;
 }
 if(read.wait_for(std::chrono::seconds(1))!=std::future_status::ready ||
    copy.wait_for(std::chrono::seconds(1))!=std::future_status::ready) {
  std::cerr<<"pipeline did not terminate"<<std::endl;std::_Exit(13);
 }
 int cpu=read.get(),device=copy.get();
 bool failed=producer_fault || fail_copy || fail_device || mode=="dispatch_exception" || mode=="copy_exception";
 bool ok=cancelled_retained && (producer_fault?cpu!=0:cpu==0) && (failed?device!=0:device==0);
 for(int retry=0;retry<4;++retry)ok=ok && ((model->WaitInGpu("replica")!=0)==failed);
 if(!failed)ok=ok && std::all_of(output.begin(),output.end(),[](char c){return c==0x5a;});
 if(pipeline)ok=ok && overlapped;
 if(!fail_device)ok=ok && closes==1;
 if(failed)ok=ok && store.ClearMem()!=0 && store.model_map_.size()==1;
 else ok=ok && store.ClearMem()==0 && store.model_map_.empty();
 std::cout<<mode<<": "<<(ok?"passed":"failed")<<std::endl;
 return ok?0:14;
}
'''


class StorePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="sllm-pipeline-cpu-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.path = Path(cls.temporary.name)
        source = Path(__file__).resolve().parents[2] / "csrc/sllm_store"
        for name in ("model.h", "types_and_defs.h", "concurrent_vector.h",
                     "concurrent_queue.h", "memory_state.h", "error_handling.h"):
            (cls.path / name).write_bytes((source / name).read_bytes())
        (cls.path / "glog").mkdir()
        (cls.path / "glog/logging.h").write_text("#pragma once\n#include <iostream>\n#define LOG(level) std::cerr\n")
        (cls.path / "cuda_runtime.h").write_text(CUDA)
        (cls.path / "pinned_memory.h").write_text(PINNED)
        (cls.path / "fixture_hooks.h").write_text(HOOKS)
        (cls.path / "model.cpp").write_text('#include "fixture_hooks.h"\n#define open controlled_open\n#define pread controlled_pread\n'
                                          + (source / "model.cpp").read_text())
        text = (source / "checkpoint_store.cpp").read_text()
        a, b = text.index("int CheckpointStore::LoadModelFromDisk("), text.index("int CheckpointStore::LoadModelFromMem(")
        c, d = text.index("int CheckpointStore::ClearMem()"), text.index("int CheckpointStore::UnloadModelFromHost(")
        (cls.path / "main.cpp").write_text(STORE + text[a:b] + text[c:d] + MAIN)
        (cls.path / "model").mkdir()
        (cls.path / "model/tensor.data_0").write_bytes(b"\x5a" * 8192)
        build = subprocess.run(["g++", "-std=c++17", "-pthread", "-I", str(cls.path),
            str(cls.path / "model.cpp"), str(cls.path / "main.cpp"), "-o", str(cls.path / "test")],
            capture_output=True, text=True, timeout=30)
        if build.returncode:
            raise AssertionError(build.stderr)

    def run_case(self, mode):
        run = subprocess.run([str(self.path / "test"), str(self.path), mode],
                             capture_output=True, text=True, timeout=4)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_gpu_without_a_cpu_read_fails_without_dereferencing_missing_host_state(self):
        self.run_case("missing_read")

    def test_copy_overlaps_the_blocked_final_disk_read(self):
        self.run_case("overlap")

    def test_allocation_open_short_read_and_exception_failures_unblock_consumers(self):
        for mode in ("allocation_failure", "pool_exhausted", "open_failure", "short_read", "read_exception"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_copy_and_device_failures_cannot_become_successful_confirmations(self):
        for mode in ("copy_failure", "device_failure", "dispatch_exception", "copy_exception"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_cancelled_copy_keeps_raw_owner_until_its_consumer_stops(self):
        self.run_case("copy_pending")
