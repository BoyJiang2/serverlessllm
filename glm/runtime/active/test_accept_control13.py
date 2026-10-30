"""Synthetic metadata failure cases, never substitutes for real G1 evidence."""
import json,pathlib,tempfile,unittest
from accept_control13 import monitor_summary,validate_release,validate_store_teardown
from budget_control13 import require_budget

class EvidenceTests(unittest.TestCase):
    def releases(self):
        return [dict(second=n,monotonic_ns=n*10**9,gpu_processes='',own_running='',cgroup={'pids.current':'0','memory.stat':'anon 0\nfile 1\nshmem 0\nunevictable 0'},processes=[],
                     gpu='\n'.join(f'{i}, GPU-{i}, 0 MiB' for i in range(8))) for n in range(61)]
    def test_release_valid(self): validate_release(self.releases(),'allstopped','run',[0]*8)
    def test_release_failures(self):
        for field,value in [('gpu_processes','999, 1 MiB'),('own_running','run-worker'),('cgroup',{'pids.current':'1','memory.stat':'anon 0\nfile 1\nshmem 0\nunevictable 0'}),('gpu','0, GPU-0, 100 MiB')]:
            with self.subTest(field=field):
                rows=self.releases(); rows[30][field]=value
                with self.assertRaises(RuntimeError): validate_release(rows,'allstopped','run',[0]*8)
    def test_release_interval_missing(self):
        with self.assertRaises(RuntimeError): validate_release(self.releases()[:-1],'allstopped','run',[0]*8)
    def monitor(self,rows,tail=b''):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'monitor.jsonl'
            path.write_bytes(b''.join(json.dumps(row).encode()+b'\n' for row in rows)+tail)
            return monitor_summary(path)
    def samples(self): return [dict(monotonic_ns=n*10**9,sample_begin_ns=n*10**9,sample_end_ns=n*10**9+100,observers=[{'ok':True},{'ok':True}]) for n in range(3)]
    def test_monitor_valid_stream(self): self.assertEqual(self.monitor(self.samples())['samples'],3)
    def test_monitor_gap(self):
        rows=self.samples(); rows[-1]['monotonic_ns']=8*10**9
        with self.assertRaises(RuntimeError): self.monitor(rows)
    def test_observer_loss(self):
        rows=self.samples(); rows[1]['observers'][0]['ok']=False
        with self.assertRaises(RuntimeError): self.monitor(rows)
    def test_only_final_partial_allowed(self):
        self.assertIsNotNone(self.monitor(self.samples(),b'{cut')['partial_final_row'])
        with self.assertRaises(RuntimeError): self.monitor(self.samples(),b'{cut\n{}\n')
    def test_slow_sample_rejected(self):
        rows=self.samples();rows[-1]['sample_end_ns']=rows[-1]['sample_begin_ns']+6_000_000_000
        with self.assertRaises(RuntimeError):self.monitor(rows)
    def test_window_budget_boundaries(self):
        with self.assertRaises(RuntimeError): require_budget('ray13a',509)
        with self.assertRaises(RuntimeError): require_budget('service13a',3599)
        self.assertEqual(require_budget('ray13a',510),330)
        self.assertEqual(require_budget('service13a',3600),3420)

class StoreTeardownEvidence(unittest.TestCase):
    def fixture(self):
        common=dict(run_id='r',boot_id='b',container_id='c',pid_namespace='pid:[42]',namespace_pid=12)
        rows=[dict(common,stage=stage,monotonic_ns=stamp) for stage,stamp in [('interrupt_begin',1000),('interrupt_sent',2000),('wait_begin',3000),('wait_end',4000),('children_reaped',5000)]]
        rows[0]['signal']=2;rows[1].update(signal=2,sent_ns=1900,grace_deadline_ns=240000001900);rows[3]['returncode']=0;rows[4].update(namespace_pid=None,children=[dict(pid=12,returncode=0)])
        launch=dict(pid=12,pid_namespace='pid:[42]');service=dict(container_id='c')
        server=dict(path='/experiment/code/Store-source-v7/sllm_store/server.py',serve_code_filename='/experiment/code/Store-source-v7/sllm_store/server.py',serve_source_code_match=True)
        return rows,launch,service,dict(run_id='r',pid=12,modules={'sllm_store.server':server})

    def test_exact_store_zero_exit_and_execution_proof(self):
        rows,launch,service,imports=self.fixture();validate_store_teardown(rows,launch,service,'r','b',imports)

    def test_nonzero_forced_missing_and_identity_changes_rejected(self):
        for kind in ('nonzero','kill','missing_tail','pid','cid','boot','namespace','grace_reset','late','cpu_proof','bytecode','filename'):
            rows,launch,service,imports=self.fixture()
            if kind=='nonzero':rows[3]['returncode']=1
            elif kind=='kill':rows[2]['stage']='kill_sent'
            elif kind=='missing_tail':rows.pop()
            elif kind=='pid':rows[3]['namespace_pid']=99
            elif kind=='cid':rows[2]['container_id']='foreign'
            elif kind=='boot':rows[2]['boot_id']='old'
            elif kind=='namespace':rows[2]['pid_namespace']='pid:[99]'
            elif kind=='grace_reset':rows[1]['grace_deadline_ns']+=1000
            elif kind=='late':rows[3]['monotonic_ns']=rows[1]['grace_deadline_ns']+1;rows[4]['monotonic_ns']=rows[3]['monotonic_ns']+1
            elif kind=='cpu_proof':imports['pid']=99
            elif kind=='bytecode':imports['modules']['sllm_store.server']['serve_source_code_match']=False
            else:imports['modules']['sllm_store.server']['serve_code_filename']='old/source.py'
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):validate_store_teardown(rows,launch,service,'r','b',imports)

if __name__=='__main__': unittest.main()
