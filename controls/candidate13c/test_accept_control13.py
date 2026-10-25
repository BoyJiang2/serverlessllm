"""Synthetic metadata failure cases, never substitutes for real G1 evidence."""
import copy,json,pathlib,tempfile,unittest
from accept_control13 import monitor_summary,validate_release
from budget_control13 import require_budget

class EvidenceTests(unittest.TestCase):
    def releases(self):
        return [dict(second=n,monotonic_ns=n*10**9,gpu_processes='',own_running='',cgroup={'pids.current':'0'},
                     gpu='\n'.join(f'{i}, GPU-{i}, 0 MiB' for i in range(8))) for n in range(61)]
    def test_release_valid(self): validate_release(self.releases(),'allstopped','run',[0]*8)
    def test_release_failures(self):
        for field,value in [('gpu_processes','999, 1 MiB'),('own_running','run-worker'),('cgroup',{'pids.current':'1'}),('gpu','0, GPU-0, 100 MiB')]:
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
    def samples(self): return [dict(monotonic_ns=n*10**9,observers=[{'ok':True},{'ok':True}]) for n in range(3)]
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
    def test_window_budget_boundaries(self):
        with self.assertRaises(RuntimeError): require_budget('ray13a',509)
        with self.assertRaises(RuntimeError): require_budget('service13a',3599)
        self.assertEqual(require_budget('ray13a',510),330)
        self.assertEqual(require_budget('service13a',3600),3420)

if __name__=='__main__': unittest.main()
