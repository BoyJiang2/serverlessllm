"""Synthetic metadata rejection checks; no remote access or resource operations."""
import pathlib,tempfile,unittest
from package_inventory13 import reviewed_files
from preflight_control13 import validate_ancestor_values
from accept_control13 import validate_native_idle

class Package(unittest.TestCase):
    def test_bytecode_excluded_but_unexpected_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for name in ('a.py','control-input-manifest.json','REVIEW-PROMPT.md'):(root/name).write_text('x')
            cache=root/'__pycache__';cache.mkdir();(cache/'a.pyc').write_bytes(b'ignored local bytecode')
            rows=[dict(path='a.py')];self.assertEqual(len(reviewed_files(root,rows)),3)
            (root/'unexpected.py').write_text('x')
            with self.assertRaises(RuntimeError):reviewed_files(root,rows)
    def test_ancestor_root_missing_interface_is_explicit(self):validate_ancestor_values({'memory.max':None,'memory.high':None},True)
    def test_nonroot_missing_limit_rejected(self):
        with self.assertRaises(RuntimeError):validate_ancestor_values({'memory.max':None,'memory.high':'max'},False)
    def test_ancestor_high_rejected(self):
        with self.assertRaises(RuntimeError):validate_ancestor_values({'memory.max':'max','memory.high':'1024'},False)
    def test_nonroot_missing_swap_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'swap limit'):validate_ancestor_values({'memory.max':'max','memory.high':'max'},False)
    def test_own_slice_swap_must_be_zero(self):
        for swap in ('max','1','-1'):
            with self.subTest(swap=swap),self.assertRaises(RuntimeError):validate_ancestor_values({'memory.max':'max','memory.high':'max','memory.swap.max':swap},False,True)
        validate_ancestor_values({'memory.max':'max','memory.high':'max','memory.swap.max':'0'},False,True)
    def test_parent_max_swap_is_not_effective_child_permission(self):validate_ancestor_values({'memory.max':'max','memory.high':'max','memory.swap.max':'max'},False)

class NativeIdle(unittest.TestCase):
    def helper(self):return dict(host_pid=9001,namespace_pid=1124,pid_namespace='pid:[42]',start_ticks='333',boot_id='b',container_id='c'*64,cgroup='0::/own/container\n',uid=2147)
    def rows(self):
        gpu='\n'.join(f'{i}, GPU-{i}, 143771 MiB, 1769 MiB, 0 %' for i in range(8))
        raw='9001, python, 322 MiB'
        return [dict(second=n,monotonic_ns=n*10**9,delete_ack_ns=0,memory_current=1,pids_current=3,shm_used_bytes=0,memory_stat='anon 0\nfile 1\nshmem 0\nunevictable 0',cpu_stat='usage_usec 0',gpu=gpu,gpu_processes=raw,host_gpu_snapshot=dict(run_id='r',boot_id='b',observers_ok=True,last_sample_ns=n*10**9,sample_begin_ns=n*10**9,registered_store_helper=self.helper(),gpu=gpu,gpu_processes=raw,gpu_pids=[9001],foreign_gpu_pids=[])) for n in range(61)]
    def test_valid_persistent_helper(self):validate_native_idle(self.rows(),[1769]*8,self.helper(),'r','b')
    def test_missing_rows(self):
        with self.assertRaises(RuntimeError):validate_native_idle(self.rows()[:-1],[1769]*8,self.helper(),'r','b')
    def test_retained_engine(self):
        rows=self.rows();rows[-1]['host_gpu_snapshot']['gpu_pids']=[9001,9002]
        with self.assertRaises(RuntimeError):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')
    def test_engine_at_first_and59_rejected(self):
        for second in (0,59):
            rows=self.rows();rows[second]['host_gpu_snapshot']['gpu_pids'].append(9002)
            with self.subTest(second=second),self.assertRaises(RuntimeError):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')
    def test_native_raw_pid_mismatch_rejected(self):
        rows=self.rows();rows[0]['gpu_processes']='99999, python, 122000 MiB'
        with self.assertRaisesRegex(RuntimeError,'GPU fields differ'):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')
    def test_host_raw_pid_mismatch_rejected(self):
        rows=self.rows();rows[0]['gpu_processes']=rows[0]['host_gpu_snapshot']['gpu_processes']='99999, python, 122000 MiB'
        with self.assertRaisesRegex(RuntimeError,'NVML PID list differs'):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')
    def test_predelete_sample_rejected(self):
        rows=self.rows();rows[0]['host_gpu_snapshot']['sample_begin_ns']=-1
        with self.assertRaisesRegex(RuntimeError,'predates delete'):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')
    def test_unequal_host_gpu_memory_rejected(self):
        rows=self.rows();rows[0]['gpu']='changed-memory'
        with self.assertRaisesRegex(RuntimeError,'GPU fields differ'):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')
    def test_negative_resource(self):
        rows=self.rows();rows[1]['memory_current']=-1
        with self.assertRaises(RuntimeError):validate_native_idle(rows,[1769]*8,self.helper(),'r','b')

if __name__=='__main__':unittest.main()
