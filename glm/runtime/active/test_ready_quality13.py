"""Shared monitor health contract; CPU fixtures, no node operations."""
import ast
import json
import pathlib
import tempfile
import types
import unittest
from gpu_identity13 import require_monitor_sample,require_monitor_ready

class ReadyQuality(unittest.TestCase):
    def good(self):
        return dict(run_id='r',boot_id='b',observers_ok=True,foreign_gpu_pids=[],sample_begin_ns=0,last_sample_ns=1,sample_end_ns=2,history_ok=True,first_monitor_error=None)
    def test_exact_five_seconds_and_first_sample(self):
        require_monitor_sample(0,1,5_000_000_000)
        require_monitor_sample(5_000_000_000,5_000_000_001,5_000_000_002,0,1)
        r=self.good();require_monitor_ready(r,5_000_000_001,'r','b')
    def test_c7_slow_sample_fresh_mid_timestamp_is_rejected(self):
        r=self.good();r.update(sample_begin_ns=146885422339049,last_sample_ns=146901702225816,sample_end_ns=146905964141014)
        with self.assertRaises(RuntimeError):require_monitor_ready(r,146905964141014,'r','b')
    def test_time_order_future_and_threshold(self):
        for args in ((0,1,5_000_000_001),(2,1,3),(0,4,3),(0,1,2,0,0),(5_000_000_001,5_000_000_002,5_000_000_003,0,2),(5_000_000_000,5_000_000_003,5_000_000_004,0,1)):
            with self.subTest(args=args),self.assertRaises(RuntimeError):require_monitor_sample(*args)
        with self.assertRaises(RuntimeError):require_monitor_ready(self.good(),1,'r','b')
        with self.assertRaises(RuntimeError):require_monitor_ready(self.good(),5_000_000_002,'r','b')
    def test_prior_history_and_current_identity_fail_closed(self):
        for key,value in [('history_ok',False),('history_ok',1),('first_monitor_error',{'reason':'earlier gap'}),('observers_ok',False),('foreign_gpu_pids',[123]),('run_id','other'),('boot_id','old')]:
            r=self.good();r[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):require_monitor_ready(r,2,'r','b')
        for key in self.good():
            r=self.good();del r[key]
            with self.subTest(missing=key),self.assertRaises((RuntimeError,KeyError)):require_monitor_ready(r,2,'r','b')

    def test_actual_producer_retains_first_failure_after_good_sample(self):
        source=pathlib.Path(__file__).with_name('monitor_control13.py')
        node=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.Try) and any(isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='require_monitor_sample' for statement in n.body for call in ast.walk(statement)))
        scope=dict(require_monitor_sample=require_monitor_sample,previous_begin=0,previous_mid=0,first_monitor_error=None,observer_states=[dict(ok=True),dict(ok=True)],row=dict(sample_begin_ns=1_000_000_000,monotonic_ns=6_000_000_000,sample_end_ns=6_000_000_000))
        code=compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec')
        exec(code,scope);first=dict(scope['first_monitor_error'])
        scope.update(previous_begin=1_000_000_000,previous_mid=6_000_000_000,row=dict(sample_begin_ns=2_000_000_000,monotonic_ns=7_000_000_000,sample_end_ns=7_000_000_000))
        require_monitor_sample(2_000_000_000,7_000_000_000,7_000_000_000,1_000_000_000,6_000_000_000)
        exec(code,scope);self.assertEqual(scope['first_monitor_error'],first)
        scope.update(previous_begin=None,previous_mid=None,first_monitor_error=None,observer_states=[dict(ok=False),dict(ok=True)],row=dict(sample_begin_ns=0,monotonic_ns=0,sample_end_ns=0))
        exec(code,scope);first=dict(scope['first_monitor_error'])
        scope.update(previous_begin=0,previous_mid=0,observer_states=[dict(ok=True),dict(ok=True)],row=dict(sample_begin_ns=1,monotonic_ns=1,sample_end_ns=1))
        exec(code,scope);self.assertEqual(scope['first_monitor_error'],first)

    def test_actual_caller_boundaries_reject_known_c7_sample(self):
        active=pathlib.Path(__file__).resolve().parent;repo=active.parents[1]
        bad=self.good();bad.update(sample_begin_ns=146885422339049,last_sample_ns=146901702225816,sample_end_ns=146905964141014)
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            (root/'monitor-v3-ready.json').write_text(json.dumps(bad))
            manifest=dict(run_id='r',runtime_identity=dict(boot_id='b'));(root/'run-manifest.json').write_text(json.dumps(manifest))
            clock=types.SimpleNamespace(monotonic_ns=lambda:146905964141014)
            base=dict(require_monitor_ready=require_monitor_ready,json=json,time=clock,root=root,out=root,control=root,manifest=manifest,run_id='r',run='r',boot_id='b',RUN='r',expected=dict(boot_id='b'),monitor_required=True,monitor_ready=root/'monitor-v3-ready.json')
            paths=[(active/'supervise_control13.py','heartbeat'),(active/'validation_control13.py','host_gpu'),(active/'formal_trials.py','host_gpu')]
            for path,name in paths:
                tree=ast.parse(path.read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
                scope=dict(base);exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),scope)
                with self.subTest(path=str(path)),self.assertRaisesRegex(RuntimeError,'slow'):scope[name]()
            tree=ast.parse((active/'preflight_control13.py').read_text())
            node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='monitor_required')
            with self.assertRaisesRegex(RuntimeError,'slow'):exec(compile(ast.Module(body=[node],type_ignores=[]),'preflight-boundary','exec'),dict(base))
            tree=ast.parse((repo/'tools/run_control13.py').read_text());values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('REMOTE','RECONCILE')}
            fn=next(n for n in ast.parse(values['REMOTE']).body if isinstance(n,ast.FunctionDef) and n.name=='ready');scope=dict(base)
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'operator-ready','exec'),scope)
            with self.assertRaisesRegex(RuntimeError,'slow'):scope['ready']()
            node=next(n for n in ast.parse(values['RECONCILE']).body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='require_monitor_ready')
            scope=dict(base,r=bad)
            with self.assertRaisesRegex(RuntimeError,'slow'):exec(compile(ast.Module(body=[node],type_ignores=[]),'reconcile-boundary','exec'),scope)

if __name__=='__main__':unittest.main()
