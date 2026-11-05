
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
int Model::WaitInGpu(const std::string& replica_uuid) {
  std::unique_lock<std::mutex> lock(mutex_);
  if (gpu_replicas_.find(replica_uuid) == gpu_replicas_.end()) {
    cv_.wait(lock, [this, replica_uuid] {
      return gpu_replicas_.find(replica_uuid) != gpu_replicas_.end();
    });
  }

  auto& gpu_replica = gpu_replicas_.at(replica_uuid);

  if (gpu_replica->state_ < MemoryState::LOADED) {
    gpu_replica->cv_.wait(lock, [&gpu_replica] {
      return gpu_replica->state_ == MemoryState::LOADED ||
             gpu_replica->state_ == MemoryState::INTERRUPTED;
    });
  }

  if (gpu_replica->state_ >= MemoryState::INTERRUPTED) {
    LOG(INFO) << "Model " << model_path_ << " is interrupted";
    return 1;
  }

  return 0;
}

int Model::FreeGpu(const std::string& replica_uuid) {
  std::unique_lock<std::mutex> lock(mutex_);
  if (gpu_replicas_.find(replica_uuid) == gpu_replicas_.end()) {
    LOG(ERROR) << "Model " << model_path_ << " replica " << replica_uuid
               << " is not registered";
    return -1;
  }

  auto& gpu_replica = gpu_replicas_.at(replica_uuid);
  if (gpu_replica->state_ == MemoryState::UNINITIALIZED) {
    LOG(WARNING) << "Model " << model_path_ << " replica " << replica_uuid
                 << " is not initialized";
    gpu_replicas_.erase(replica_uuid);
    return 0;
  }

  if (gpu_replica->state_ == MemoryState::LOADING) {
    LOG(INFO) << "Waiting for model " << model_path_ << " replica "
              << replica_uuid << " to be loaded";
    gpu_replica->cv_.wait(lock, [&gpu_replica] {
      return gpu_replica->state_ == MemoryState::LOADED ||
             gpu_replica->state_ == MemoryState::INTERRUPTED;
    });
  }

  gpu_replicas_.erase(replica_uuid);
  return 0;
}

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
