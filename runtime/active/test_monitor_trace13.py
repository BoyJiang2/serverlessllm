"""Exercise actual recorder functions without importing the live monitor entry."""
import ast
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from accept_control13 import monitor_summary


class Clock:
    now=0
    cpu=0
    def monotonic_ns(self):return self.now
    def thread_time_ns(self):return self.cpu


def recorder(stream=None):
    source=pathlib.Path(__file__).with_name('monitor_control13.py')
    tree=ast.parse(source.read_text())
    names={'trace_output','trace_sequence','trace_io_ns','TRACE_LIMIT'}
    body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('trace_event','measured')
          or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
    clock=Clock();scope=dict(json=json,time=clock)
    exec(compile(ast.Module(body=body,type_ignores=[]),str(source),'exec'),scope)
    scope['trace_output']=stream if stream is not None else io.BytesIO()
    return scope,clock


class MonitorTrace(unittest.TestCase):
    def events(self,scope):
        return [json.loads(line) for line in scope['trace_output'].getvalue().splitlines()]

    def test_disabled_trace_preserves_result_without_records(self):
        scope,_=recorder();scope['trace_output']=None
        value=object()
        self.assertIs(scope['measured']('proc.status',lambda:value),value)
        self.assertEqual(scope['trace_sequence'],0)

    def test_delays_located_without_weakening_actual_acceptance(self):
        for name in ('proc.cmdline','gpu.query-gpu','ready.rename'):
            with self.subTest(operation=name):
                scope,clock=recorder()
                def slow():
                    clock.now+=6_000_000_000
                    return 'unlogged-value'
                self.assertEqual(scope['measured'](name,slow,pid=17,start_ticks='123'),'unlogged-value')
                begin,end=self.events(scope)
                self.assertEqual((end['operation'],end['duration_ns']),(name,6_000_000_000))
                self.assertEqual((end['pid'],end['start_ticks']),(17,'123'))
                self.assertIsNone(begin['end_ns'])
                self.assertEqual(begin['outcome'],'pending')
                self.assertNotIn(b'unlogged-value',scope['trace_output'].getvalue())
                rows=[dict(monotonic_ns=b+100,sample_begin_ns=b,sample_end_ns=b+d,
                           observers=[dict(ok=True),dict(ok=True)])
                      for b,d in ((10_000_000_000,1000),(11_000_000_000,6_000_000_000),(18_000_000_000,1000))]
                with tempfile.TemporaryDirectory() as directory:
                    path=pathlib.Path(directory)/'monitor.jsonl'
                    path.write_text('\n'.join(json.dumps(row) for row in rows))
                    with self.assertRaisesRegex(RuntimeError,'slow or inconsistent monitor sample'):
                        monitor_summary(path)

    def test_error_propagates_and_never_becomes_empty_gpu_data(self):
        scope,clock=recorder()
        def failed():
            clock.now+=10_000_000_000
            raise TimeoutError('controlled query failure')
        with self.assertRaises(TimeoutError):scope['measured']('gpu.query-compute-apps',failed)
        begin,end=self.events(scope)
        self.assertIsNone(begin['end_ns'])
        self.assertEqual(end['outcome'],'error')
        self.assertIn('controlled query failure',end['error'])
        self.assertEqual(end['duration_ns'],10_000_000_000)
        self.assertNotIn('gpu_pids',end)

    def test_trace_io_delay_is_distinct_from_operation_delay(self):
        scope,clock=recorder()
        class SlowOutput(io.BytesIO):
            def write(self,value):
                clock.now+=6_000_000_000
                return super().write(value)
        scope['trace_output']=SlowOutput()
        scope['measured']('proc.stat',lambda:'value')
        begin,end=self.events(scope)
        self.assertEqual(end['duration_ns'],0)
        self.assertEqual(end['begin_trace_io_ns'],6_000_000_000)
        scope['measured']('next.operation',lambda:None)
        self.assertEqual(self.events(scope)[2]['previous_trace_io_ns'],6_000_000_000)

    def test_budget_exhaustion_stops_before_action(self):
        scope,_=recorder();scope['TRACE_LIMIT']=1;called=[]
        with self.assertRaisesRegex(RuntimeError,'finite monitor diagnostic trace'):
            scope['measured']('proc.status',lambda:called.append(True))
        self.assertFalse(called)

    def test_child_cpu_is_separate_from_parent_cpu(self):
        scope,clock=recorder()
        usage=iter([types.SimpleNamespace(ru_utime=1.,ru_stime=1.),
                    types.SimpleNamespace(ru_utime=2.,ru_stime=1.)])
        resource=types.SimpleNamespace(RUSAGE_CHILDREN=1,getrusage=lambda who:next(usage))
        def action():clock.now+=6_000_000_000;clock.cpu+=100_000_000
        with patch.dict(sys.modules,{'resource':resource}):
            scope['measured']('gpu.query-gpu',action,child_cpu=True)
        end=self.events(scope)[1]
        self.assertEqual((end['thread_cpu_ns'],end['child_cpu_ns']),(100_000_000,1_000_000_000))


if __name__=='__main__':unittest.main()
