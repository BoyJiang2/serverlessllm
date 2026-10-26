"""CPU regression clocks; never start Ray, Docker or model work."""
import ast,pathlib,subprocess,types,unittest
from unittest.mock import patch
from control_watchdog13 import supervise_child
from budget_control13 import require_budget

class Child:
    def __init__(self,clock,exit_at=None,term_delay=0,ignore_term=False,ignore_kill=False):
        self.clock=clock; self.exit_at=exit_at; self.term_delay=term_delay; self.ignore_term=ignore_term; self.ignore_kill=ignore_kill; self.code=None; self.terminated=False
    def poll(self):
        if self.exit_at is not None and self.clock[0]>=self.exit_at: self.code=0
        return self.code
    def terminate(self):
        self.terminated=True
        if not self.ignore_term: self.exit_at=self.clock[0]+self.term_delay
    def kill(self):
        if not self.ignore_kill:self.code=-9
    def wait(self,timeout):
        if self.poll() is not None:return self.code
        if self.exit_at is not None and self.exit_at-self.clock[0]<=timeout:self.clock[0]=self.exit_at;return self.poll()
        self.clock[0]+=timeout;raise subprocess.TimeoutExpired('synthetic',timeout)

class Watchdog(unittest.TestCase):
    def run_case(self,exit_at=None,jump=None,**kwargs):
        clock=[0]; child=Child(clock,exit_at,**kwargs)
        def sleep(seconds): clock[0]+=jump if jump is not None else seconds
        with patch('time.monotonic_ns',side_effect=lambda:int(clock[0]*1e9)),patch('time.sleep',side_effect=sleep):result=supervise_child(child,0,330,lambda:None)
        return result,child
    def test_success_before_work_limit(self):self.assertEqual(self.run_case(exit_at=1)[0]['status'],'passed including cleanup')
    def test_late_exit_is_failed(self):self.assertEqual(self.run_case(exit_at=331,jump=331)[0]['status'],'failed')
    def test_term_delay_inside_total(self):
        result,child=self.run_case(term_delay=10);self.assertEqual(result['status'],'failed');self.assertLessEqual(result['end_ns'],330*10**9);self.assertTrue(child.terminated)
    def test_ignored_term_killed_within_total(self):
        result,child=self.run_case(ignore_term=True);self.assertEqual(child.code,-9);self.assertLessEqual(result['end_ns'],330*10**9)
    def test_unkillable_is_never_passed(self):
        result,child=self.run_case(ignore_term=True,ignore_kill=True);self.assertEqual(result['status'],'failed');self.assertTrue(result['child_alive_at_end']);self.assertLessEqual(result['end_ns'],330*10**9)
    def test_actual_bootstrap_uses_identical_admission(self):
        source=pathlib.Path(__file__).with_name('bootstrap_control13.py')
        node=next(v.value for v in ast.walk(ast.parse(source.read_text())) if isinstance(v,ast.Expr) and isinstance(v.value,ast.Call) and isinstance(v.value.func,ast.Name) and v.value.func.id=='require_budget')
        expression=compile(ast.Expression(node),'actual bootstrap budget','eval')
        for remaining in (3600,3720,3721):
            self.assertEqual(eval(expression,dict(require_budget=require_budget,pool_diagnostic=False,label='service13a',deadline=remaining,time=types.SimpleNamespace(time=lambda:0))),3420)
        with self.assertRaises(RuntimeError):eval(expression,dict(require_budget=require_budget,pool_diagnostic=False,label='service13a',deadline=3599,time=types.SimpleNamespace(time=lambda:0)))
    def test_service_boundary(self):
        for value in (3600,3720,3721):self.assertEqual(require_budget('service13a',value),3420)
        with self.assertRaises(RuntimeError):require_budget('service13a',3599)

if __name__=='__main__':unittest.main()
