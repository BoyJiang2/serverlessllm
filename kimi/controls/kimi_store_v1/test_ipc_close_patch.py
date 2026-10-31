"""Compile the actual source tail with CPU CUDA stubs; never link CUDA."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parent
BASE = ROOT / 'fixtures/native_model_base.cpp'
PATCH = ROOT / 'native_patch/model.cpp'


def source_tail(path):
    source = path.read_text(encoding='utf-8')
    marker = source.index('  lock.lock();\n  futures.clear();', source.index('int Model::ToGpu('))
    start = source.index('  if (error) {', marker)
    end = source.index('\nint Model::WaitInHost()', start)
    return source, source[start:end]


def source_host_read(path):
    source = path.read_text(encoding='utf-8')
    host = source.index('int Model::ToHost(')
    marker = '        size_t copied = 0;'
    if marker not in source[host:source.index('int Model::ToGpu(')]:
        marker = '        int fd = file_descriptors[partition_id];'
    start = source.index(marker, host)
    end = source.index('        host_ptr_vector_->enqueue', start)
    return source[start:end]


PREFIX = r'''
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>
enum class MemoryState { LOADING, LOADED, INTERRUPTED };
std::vector<std::string> events;
std::vector<int> closed;
int fail_device=-1, fail_close=-1;
using cudaError_t=int;
constexpr int cudaSuccess=0;
int cudaSetDevice(int d) { events.push_back("device"); return d==fail_device ? 7 : 0; }
int cudaIpcCloseMemHandle(void* p) {
  int id=static_cast<int>(reinterpret_cast<uintptr_t>(p));
  closed.push_back(id); events.push_back("close"); return id==fail_close ? 9 : 0;
}
const char* cudaGetErrorString(int) { return "CPU fixture error"; }
std::ostringstream log_stream;
#define LOG(level) log_stream
struct CV {
  int count=0;
  void notify_all() { ++count; events.push_back("notify"); }
};
struct Replica {
  MemoryState state_=MemoryState::LOADING;
  std::map<int,std::vector<void*>> device_ptrs_;
  CV cv_;
};
int complete(bool error, std::shared_ptr<Replica> gpu_replica) {
  std::string model_path_="fixture", replica_uuid="fixture";
'''

MAIN = r'''
int main() {
  struct Case { const char* name; bool copy_error; int device_error; int close_error; };
  std::vector<Case> cases={{"success",false,-1,-1},{"close_failure",false,-1,2},
    {"device_failure",false,0,-1},{"copy_failure",true,-1,-1}};
  std::cout << "{";
  bool all=true;
  for (size_t i=0;i<cases.size();++i) {
    auto c=cases[i]; events.clear(); closed.clear(); fail_device=c.device_error; fail_close=c.close_error;
    auto r=std::make_shared<Replica>();
    r->device_ptrs_={{0,{reinterpret_cast<void*>(1),reinterpret_cast<void*>(2)}},
                     {1,{reinterpret_cast<void*>(3)}}};
    int ret=complete(c.copy_error,r);
    bool failed=c.copy_error || c.device_error>=0 || c.close_error>=0;
    bool ok=ret==(failed ? -1 : 0) && r->state_==(failed ? MemoryState::INTERRUPTED : MemoryState::LOADED)
      && r->cv_.count==1;
    if (CHECK_PATCH_ORDER) ok=ok && !events.empty() && events.back()=="notify"
      && closed.size()==(c.device_error==0 ? 1U : 3U);
    all=all && ok;
    if(i) std::cout << ",";
    std::cout << "\"" << c.name << "\":" << (ok ? "true" : "false");
  }
  std::cout << "}" << std::endl;
  return all ? 0 : 1;
}
'''


READ_PREFIX = r'''
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fcntl.h>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
std::ostringstream logs;
#define LOG(level) logs
std::string mode;
bool interrupted=false;
ssize_t controlled_pread(int fd,void* buffer,size_t count,off_t offset) {
  if(mode=="eintr" && !interrupted) { interrupted=true; errno=EINTR; return -1; }
  if(mode=="short") count=std::min(count,size_t(512));
  return ::pread(fd,buffer,count,offset);
}
#define pread controlled_pread
int read_all(std::vector<int>& file_descriptors, std::vector<size_t>& partition_sizes_,
             std::vector<std::filesystem::path>& partition_paths_, size_t chunk_size) {
  size_t total=0; for(auto size:partition_sizes_) total+=size;
  std::string model_path_="CPU read control";
  size_t partition_id=0,file_offset=0;
  for(size_t chunk_idx=0,done=0;done<total;++chunk_idx) {
    size_t size=std::min(chunk_size,total-done);
    void* pointer=nullptr;
    if(posix_memalign(&pointer,4096,chunk_size)!=0) return -1;
    std::vector<char*> host_buffers(chunk_idx+1,static_cast<char*>(pointer));
'''
READ_SUFFIX = r'''
    std::cout.write(static_cast<char*>(pointer),size);
    free(pointer); done+=size;
  }
  return 0;
}
int main(int argc,char** argv) {
  if(argc<4) return 2;
  mode=argv[1]; size_t chunk_size=std::stoul(argv[2]);
  std::vector<int> descriptors; std::vector<size_t> sizes;
  std::vector<std::filesystem::path> paths;
  for(int n=3;n<argc;++n) {
    int fd=open(argv[n],O_RDONLY); struct stat st;
    if(fd<0 || fstat(fd,&st)!=0) return 2;
    descriptors.push_back(fd); sizes.push_back(st.st_size); paths.emplace_back(argv[n]);
  }
  if(mode=="truncated" && truncate(paths.back().c_str(),0)!=0) return 2;
  int code=read_all(descriptors,sizes,paths,chunk_size);
  for(int fd:descriptors) close(fd);
  return code==0 ? 0 : 1;
}
'''


class IpcClosePatchTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'Linux pread CPU control required')
    def test_host_read_fragment_supports_multi_partition_short_reads_and_eintr(self):
        import stream_writer
        compiler = shutil.which('g++')
        if compiler is None:
            self.skipTest('existing CPU g++ unavailable')
        results = {}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for label, path in (('baseline', BASE), ('candidate', PATCH)):
                source, binary = root / (label + '.cpp'), root / label
                source.write_text(READ_PREFIX + source_host_read(path) + READ_SUFFIX, encoding='utf-8')
                built = subprocess.run([compiler, '-std=c++17', '-O0', str(source), '-o', str(binary)],
                    capture_output=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stderr.decode())
                results[label] = {}
                for mode, chunk in (('normal',4096),('normal',16384),('normal',6144),
                                    ('short',16384),('eintr',16384),('truncated',16384)):
                    directory = root / (label + '-' + mode + '-' + str(chunk))
                    payloads = (struct.pack('<2i',-1,-2147483648),
                        struct.pack('<4H',0x8000,0x7fc1,0,0x3f80),
                        struct.pack('<2I',0x80000000,0x7fc00001),b'\x00' * 8)
                    entries = [(str(n),8,[data],None) for n,data in enumerate(payloads)]
                    stream_writer.write_records(directory, entries, partition_bytes=8)
                    paths = [directory / ('tensor.data_' + str(n)) for n in range(4)]
                    expected = b''.join(p.read_bytes() for p in paths)
                    run = subprocess.run([str(binary), mode, str(chunk), *map(str,paths)],
                        capture_output=True, timeout=5)
                    results[label][(mode,chunk)] = run.returncode
                    if mode != 'truncated' and label == 'candidate':
                        self.assertEqual(run.returncode, 0, str(results))
                        self.assertEqual(hashlib.sha256(run.stdout).digest(), hashlib.sha256(expected).digest())
            self.assertEqual(results['baseline'][('normal',4096)], 0)
            for key in (('normal',16384),('short',16384),('eintr',16384)):
                self.assertEqual(results['baseline'][key], 1)
            self.assertEqual(results['candidate'][('truncated',16384)], 1)

    def test_source_hashes_and_changes_limited_to_host_read_and_close_tail(self):
        import native_record_call
        ledger = json.loads((ROOT / 'native_patch/ledger.json').read_bytes())
        self.assertEqual(hashlib.sha256(BASE.read_bytes()).hexdigest(), ledger['base_sha256'])
        self.assertEqual(hashlib.sha256(PATCH.read_bytes()).hexdigest(), ledger['candidate_sha256'])
        self.assertEqual(native_record_call.PATCH_SHA, ledger['candidate_sha256'])
        old, old_tail = source_tail(BASE)
        new, new_tail = source_tail(PATCH)
        old = old.replace(old_tail, '<TAIL>').replace(source_host_read(BASE), '<HOST_READ>')
        new = new.replace(new_tail, '<TAIL>').replace(source_host_read(PATCH), '<HOST_READ>')
        self.assertEqual(old, new.replace('#include <cerrno>\n', ''))
        self.assertFalse(ledger['compiled_native_store'])
        self.assertFalse(ledger['gpu_verified'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'old-build.json'
            raw = json.dumps(dict(schema='kimi-independent-native-build-v1', compiled_native_store=True,
                model_source_sha256='c057a1aa51f1c63be8671099d8a0a75da865d8fadffec667df4696fa4cf8287c')).encode()
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'compiled IPC-close'):
                native_record_call._bound_runtime(object(),path,hashlib.sha256(raw).hexdigest())

    def test_actual_source_fragment_fails_before_patch_and_passes_fault_controls_after(self):
        compiler = shutil.which('g++')
        if compiler is None:
            self.skipTest('CPU C++ compiler unavailable; native patch not behavior-tested here')
        results = {}
        with tempfile.TemporaryDirectory() as temp:
            for label, path in (('baseline', BASE), ('candidate', PATCH)):
                source = Path(temp) / (label + '.cpp')
                binary = Path(temp) / (label + '.exe')
                _, tail = source_tail(path)
                source.write_text('constexpr bool CHECK_PATCH_ORDER=' + ('true' if label == 'candidate' else 'false')
                    + ';\n' + PREFIX + tail + MAIN, encoding='utf-8')
                compiled = subprocess.run([compiler, '-std=c++17', '-O0', str(source), '-o', str(binary)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
                results[label] = dict(exit=run.returncode, cases=json.loads(run.stdout))
        self.assertEqual(results['baseline']['exit'], 1)
        self.assertTrue(results['baseline']['cases']['success'])
        self.assertFalse(results['baseline']['cases']['close_failure'])
        self.assertFalse(results['baseline']['cases']['device_failure'])
        self.assertEqual(results['candidate']['exit'], 0)
        self.assertTrue(all(results['candidate']['cases'].values()))


if __name__ == '__main__':
    unittest.main()
