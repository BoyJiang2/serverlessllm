from pathlib import Path
import subprocess,hashlib,json,sys
root=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
path=root/'sllm_store/csrc/sllm_store/model.cpp';source=path.read_text()
a=source.index('int Model::WaitInGpu(');b=source.index('\nint Model::FreeHost()',a)
body=source[a:b]
prefix=r"""
#include <chrono>
#include <condition_variable>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#define LOG(level) std::cerr
using namespace std::chrono_literals;
enum class MemoryState {UNINITIALIZED,UNALLOCATED,LOADING,LOADED,INTERRUPTED};
struct GpuReplica {MemoryState state_=MemoryState::INTERRUPTED;std::condition_variable cv_;};
class Model {public:
 std::mutex mutex_;std::condition_variable cv_;std::string model_path_="cpu-fixture";
 std::unordered_map<std::string,std::shared_ptr<GpuReplica>> gpu_replicas_;
 int WaitInGpu(const std::string& replica_uuid);
 int FreeGpu(const std::string& replica_uuid);
};
"""
main=r"""
int main() {
 Model model;model.gpu_replicas_["failed-replica"]=std::make_shared<GpuReplica>();
 int before=model.WaitInGpu("failed-replica");
 int erased=model.FreeGpu("failed-replica");
 std::promise<void> entered;auto began=entered.get_future();
 auto future=std::async(std::launch::async,[&] {
   entered.set_value();return model.WaitInGpu("failed-replica");
 });
 began.wait();bool blocked=future.wait_for(200ms)==std::future_status::timeout;
 // End the CPU probe cleanly; real UUIDs are not reinserted by the failure path.
 {std::lock_guard<std::mutex> guard(model.mutex_);
  model.gpu_replicas_["failed-replica"]=std::make_shared<GpuReplica>();}
 model.cv_.notify_all();int after=future.get();
 std::cout<<"{\"failed_confirm_before_erase\":"<<before
          <<",\"free_return\":"<<erased
          <<",\"late_confirm_waited_after_erase\":"<<(blocked?"true":"false")
          <<",\"confirm_after_probe_reinsert\":"<<after<<"}"<<std::endl;
 return before==1 && erased==0 && blocked && after==1 ? 0 : 1;
}
"""
fixture=out/'failed_confirm_fixture.cpp';fixture.write_text(prefix+body+main)
binary=out/'failed-confirm-cpu'
build=subprocess.run(['g++','-std=c++17','-O0','-pthread',str(fixture),'-o',str(binary)],capture_output=True,text=True,timeout=30)
assert build.returncode==0,build.stderr
run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
(out/'failed-confirm.stdout').write_text(run.stdout);(out/'failed-confirm.stderr').write_text(run.stderr)
assert run.returncode==0,run.stdout+run.stderr
result=dict(scope='CPU extracted WaitInGpu/FreeGpu only, no CUDA or native extension build',source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),extracted_body_sha256=hashlib.sha256(body.encode()).hexdigest(),fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),observation=json.loads(run.stdout))
(out/'failed-confirm-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
