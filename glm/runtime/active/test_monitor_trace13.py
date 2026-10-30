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
        self.assertEqual(end['error'],'TimeoutError')
        self.assertNotIn(b'controlled query failure',scope['trace_output'].getvalue())
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
        self.assertIsNone(begin['previous_trace_io_ns'])
        self.assertIn('event_ns',begin)
        self.assertNotIn('begin_ns',begin)
        self.assertEqual(end['begin_ns'],6_000_000_000)
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

    def test_observer_expected_birth_is_not_reported_as_observed(self):
        scope,_=recorder()
        stat='17 (observer) '+' '.join(['S']+['0']*18+['200'])
        class Proc:
            def __truediv__(self,name):return self
            def joinpath(self,name):
                return types.SimpleNamespace(read_text=lambda:stat if name=='stat' else 'own-group')
        scope.update(pathlib=types.SimpleNamespace(Path=lambda value:Proc()),
                     registration=dict(observers=[dict(pid=17,start_ticks='100',cgroup='own-group',unit='observer.service')]),
                     observer_states=[])
        tree=ast.parse(pathlib.Path(__file__).with_name('monitor_control13.py').read_text())
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For)
                  and isinstance(n.target,ast.Name) and n.target.id=='registered')
        exec(compile(ast.Module(body=[loop],type_ignores=[]),'<actual-observer-loop>','exec'),scope)
        events=self.events(scope)
        self.assertIsNone(events[1]['start_ticks'])
        self.assertEqual(events[1]['expected_start_ticks'],'100')
        self.assertEqual(events[3]['start_ticks'],'200')
        self.assertEqual(events[3]['expected_start_ticks'],'100')
        self.assertFalse(scope['observer_states'][0]['ok'])

    def test_action_error_survives_end_trace_write_failure(self):
        class FailSecondWrite(io.BytesIO):
            calls=0
            def write(self,value):
                self.calls+=1
                if self.calls==2:raise OSError('controlled recorder failure')
                return super().write(value)
        scope,_=recorder(FailSecondWrite());original=TimeoutError('original action error')
        def failed():raise original
        with self.assertRaises(TimeoutError) as caught:scope['measured']('gpu.query-gpu',failed)
        self.assertIs(caught.exception,original)
        self.assertEqual(len(self.events(scope)),1)
        self.assertEqual(self.events(scope)[0]['outcome'],'pending')

    def test_successful_action_cannot_hide_end_trace_write_failure(self):
        class FailSecondWrite(io.BytesIO):
            calls=0
            def write(self,value):
                self.calls+=1
                if self.calls==2:raise OSError('controlled recorder failure')
                return super().write(value)
        scope,_=recorder(FailSecondWrite())
        with self.assertRaises(OSError):scope['measured']('proc.stat',lambda:'value')

    def test_action_error_survives_child_counter_failure(self):
        scope,_=recorder();original=TimeoutError('original action error')
        def failed():raise original
        calls=[]
        def usage(who):
            calls.append(who)
            if len(calls)==2:raise OSError('counter unavailable')
            return types.SimpleNamespace(ru_utime=0.,ru_stime=0.)
        with patch.dict(sys.modules,{'resource':types.SimpleNamespace(RUSAGE_CHILDREN=1,getrusage=usage)}), \
                self.assertRaises(TimeoutError) as caught:
            scope['measured']('gpu.query-gpu',failed,child_cpu=True)
        self.assertIs(caught.exception,original)

    def test_error_payload_is_not_copied(self):
        scope,_=recorder()
        def failed():raise ValueError('unlogged-payload-'+'x'*100000)
        with self.assertRaises(ValueError):scope['measured']('helper.derive',failed)
        self.assertEqual(self.events(scope)[1]['error'],'ValueError')
        self.assertNotIn(b'unlogged-payload',scope['trace_output'].getvalue())
        self.assertLess(len(scope['trace_output'].getvalue()),4096)

    def test_disabled_cgroup_scan_remains_lazy(self):
        scope,_=recorder();scope['trace_output']=None;visited=[]
        def scan(pattern):
            visited.append(True)
            yield pathlib.Path('/own/cgroup.procs')
        scope['cgroup']=types.SimpleNamespace(rglob=scan)
        tree=ast.parse(pathlib.Path(__file__).with_name('monitor_control13.py').read_text())
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                        and any(isinstance(t,ast.Name) and t.id=='paths' for t in n.targets))
        conditional=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
                         and any(isinstance(t,ast.Name) and t.id=='paths'
                                 for a in n.body if isinstance(a,ast.Assign) for t in a.targets))
        exec(compile(ast.Module(body=[assignment,conditional],type_ignores=[]),'<actual-scan-prelude>','exec'),scope)
        self.assertFalse(visited)
        self.assertEqual(len(list(scope['paths'])),1)
        self.assertTrue(visited)



class GPUQuerySplit(unittest.TestCase):
    def query(self,slow=None,fail=False,duplicate=False):
        scope,clock=recorder();source=pathlib.Path(__file__).with_name('monitor_control13.py')
        node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='query_gpu')
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        calls=[]
        def command(argv,**kwargs):
            calls.append(argv);self.assertEqual(kwargs['timeout'],15)
            memory='memory.total' in argv[1]
            if slow==('memory' if memory else 'utilization'):clock.now+=6_000_000_000
            if fail and not memory:raise __import__('subprocess').CalledProcessError(7,argv)
            if memory:return '0, GPU-a, 100 MiB, 2 MiB\n1, GPU-b, 100 MiB, 3 MiB\n'
            return 'GPU-a, 20 %\nGPU-a, 10 %\n' if duplicate else 'GPU-b, 20 %\nGPU-a, 10 %\n'
        scope['subprocess']=types.SimpleNamespace(check_output=command)
        fake=types.SimpleNamespace(RUSAGE_CHILDREN=0,getrusage=lambda *args:types.SimpleNamespace(ru_utime=0,ru_stime=0))
        error=None;result=None
        with patch.dict(sys.modules,dict(resource=fake)):
            try:result=scope['query_gpu'](['GPU-a','GPU-b'])
            except BaseException as failure:error=failure
        records=[json.loads(line) for line in scope['trace_output'].getvalue().splitlines()]
        return result,error,records,clock,calls

    def test_uuid_join_preserves_output_schema_and_query_provenance(self):
        result,error,records,clock,calls=self.query()
        self.assertIsNone(error);self.assertEqual(result,'0, GPU-a, 100 MiB, 2 MiB, 10 %\n1, GPU-b, 100 MiB, 3 MiB, 20 %\n')
        end=[e for e in records if e['event']=='end']
        self.assertEqual([e['operation'] for e in end],['gpu.query-memory','gpu.query-utilization'])
        self.assertEqual([e['argv'] for e in end],calls);self.assertEqual([e['returncode'] for e in end],[0,0])

    def test_each_split_delay_remains_visible_and_fails_five_seconds(self):
        from gpu_identity13 import require_monitor_sample
        for stage in ('memory','utilization'):
            result,error,records,clock,calls=self.query(slow=stage)
            end=next(e for e in records if e['event']=='end' and e['operation']=='gpu.query-'+stage)
            self.assertEqual(end['duration_ns'],6_000_000_000)
            with self.assertRaises(RuntimeError):require_monitor_sample(0,0,clock.now)

    def test_query_error_preserves_returncode_no_empty_success(self):
        result,error,records,clock,calls=self.query(fail=True)
        self.assertIsNone(result);self.assertIsInstance(error,__import__('subprocess').CalledProcessError)
        self.assertEqual(records[-1]['returncode'],7);self.assertEqual(records[-1]['outcome'],'error')

    def test_duplicate_uuid_is_rejected(self):
        result,error,*_=self.query(duplicate=True)
        self.assertIsNone(result);self.assertIsInstance(error,RuntimeError)

if __name__=='__main__':unittest.main()
