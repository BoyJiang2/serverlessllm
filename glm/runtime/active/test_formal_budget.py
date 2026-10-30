"""Pure clock/OS timer fixtures; no container, model, cache or signal delivery."""
import sys,time,types,unittest,json,pathlib,tempfile,datetime,ast
from unittest.mock import patch
import budget_control13 as budget

class FormalBudget(unittest.TestCase):
    def test_fixed_origin_derives_distinct_work_supervisor_and_head_deadlines(self):
        row=budget.formal_batch_budget(1000000000,1000*1000000000,20000*1000000000)
        self.assertEqual(row['head_end_ns'],(1+14400)*1000000000)
        self.assertEqual(row['head_end_ns']-row['total_end_ns'],10*1000000000)
        self.assertEqual(row['total_end_ns']-row['evidence_end_ns'],355*1000000000)
        self.assertEqual(row['evidence_end_ns']-row['work_end_ns'],180*1000000000)
        self.assertEqual(row['initialization_end_ns'],901*1000000000)

    def test_resource_cap_keeps_release_reserve_and_never_uses_wall_again(self):
        row=budget.formal_batch_budget(1000000000,1000*1000000000,5000*1000000000)
        self.assertEqual(row['head_end_ns'],3821*1000000000)
        frozen=dict(row)
        with patch.object(time,'time',return_value=-9999):
            self.assertEqual(row,frozen)

    def test_insufficient_window_and_nonfinite_anchors_reject(self):
        with self.assertRaises(TimeoutError):budget.formal_batch_budget(1,1000*1000000000,1100*1000000000)
        for value in (float('nan'),float('inf')):
            with self.assertRaises(ValueError):budget.formal_batch_budget(1,value,5000*1000000000)

    def test_delayed_worker_or_observe_timer_consumes_original_deadline(self):
        observed=[];fake=types.SimpleNamespace(ITIMER_REAL=0,setitimer=lambda kind,seconds:observed.append(seconds))
        with patch.dict(sys.modules,signal=fake):
            for stamp in (100,200,300):
                with patch.object(time,'monotonic_ns',return_value=stamp*1000000000):budget.arm_formal_timer(1000*1000000000)
        self.assertEqual(observed,[900,800,700])

    def test_expired_timer_cannot_disable_budget_with_zero(self):
        observed=[];fake=types.SimpleNamespace(ITIMER_REAL=0,setitimer=lambda *args:observed.append(args))
        with patch.dict(sys.modules,signal=fake),patch.object(time,'monotonic_ns',return_value=100):
            for end in (99,100):
                with self.assertRaises(TimeoutError):budget.arm_formal_timer(end)
        self.assertEqual(observed,[])

    def test_next_round_requires_both_full_budgets(self):
        prereg=dict(attempt_timeout_seconds=1200,post_delete_observation_seconds=60)
        with patch.object(time,'monotonic_ns',return_value=1000000000),patch.object(time,'time',return_value=100):
            self.assertTrue(budget.formal_attempt_fits(prereg,1441*1000000000,1540))
            self.assertFalse(budget.formal_attempt_fits(prereg,1441*1000000000-1,1540))
            self.assertFalse(budget.formal_attempt_fits(prereg,1441*1000000000,1539.999))

class BatchDispatch(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=pathlib.Path(temporary.name)
        config=dict(run_id='r',scope='formal continuation of original forty IDs',execution_purpose='formal-continuation',runtime_identity=dict(boot_id='b'),cleanup_deadline=datetime.datetime.fromtimestamp(5000,datetime.timezone.utc).isoformat())
        dispatch=dict(run_id='r',boot_id='b',code_commit='c',manifest_sha256='m',spec=dict(name='headformal13'),dispatch_monotonic_ns=1000000000,dispatch_utc=datetime.datetime.fromtimestamp(1000,datetime.timezone.utc).isoformat(),batch_budget=budget.formal_batch_budget(1000000000,1000*1000000000,5000*1000000000))
        for name,value in [('run-manifest.json',config),('deployment.json',dict(run_id='r',code_commit='c',input_manifest_sha256='m')),('headformal13-dispatch.json',dispatch)]:
            (root/name).write_text(json.dumps(value))
        self.window=dict(cleanup_deadline=config['cleanup_deadline'])
        window_patch=patch.dict(sys.modules,resource_window_control13=types.SimpleNamespace(load_window=lambda root:self.window));window_patch.start();self.addCleanup(window_patch.stop)
        original=pathlib.Path.read_text
        patcher=patch.object(pathlib.Path,'read_text',lambda p,*a,**kw:'b' if p==pathlib.Path('/proc/sys/kernel/random/boot_id') else original(p,*a,**kw));patcher.start();self.addCleanup(patcher.stop)
        return root,dispatch

    def test_actual_dispatch_rederivation_and_normal_limit(self):
        root,dispatch=self.fixture();self.assertEqual(budget.load_formal_batch(root,'formal13'),dispatch['batch_budget'])
        normal=budget.formal_batch_budget(1000000000,1000*1000000000,20000*1000000000,'normal')
        self.assertEqual(normal['head_end_ns'],3901*1000000000)

    def test_wrong_boot_role_code_or_extended_deadline_rejects(self):
        for kind in ('boot','role','code','extension'):
            root,dispatch=self.fixture()
            if kind=='boot':dispatch['boot_id']='old'
            elif kind=='role':dispatch['spec']['name']='headservice13a'
            elif kind=='code':dispatch['code_commit']='other'
            else:dispatch['batch_budget']['work_end_ns']+=1
            (root/'headformal13-dispatch.json').write_text(json.dumps(dispatch))
            with self.subTest(kind=kind),self.assertRaises(ValueError):budget.load_formal_batch(root,'formal13')

    def test_changed_wall_anchor_or_validated_window_rejects(self):
        for kind in ('wall','window'):
            root,dispatch=self.fixture()
            if kind=='wall':dispatch['batch_budget']['wall_anchor_ns']+=1000000000
            else:self.window['cleanup_deadline']=datetime.datetime.fromtimestamp(5001,datetime.timezone.utc).isoformat()
            (root/'headformal13-dispatch.json').write_text(json.dumps(dispatch))
            with self.subTest(kind=kind),self.assertRaises(ValueError):budget.load_formal_batch(root,'formal13')

class AbsoluteWatchdog(unittest.TestCase):
    def exercise(self,now=100,exit_at=None,cleanup=25,ignore_kill=False):
        from control_watchdog13 import supervise_child
        from test_watchdog13 import Child
        clock=[now];child=Child(clock,exit_at=exit_at,ignore_term=True,ignore_kill=ignore_kill)
        with patch.object(time,'monotonic_ns',side_effect=lambda:int(clock[0]*1e9)),patch.object(time,'sleep',side_effect=lambda value:clock.__setitem__(0,clock[0]+value)):
            result=supervise_child(child,int(now*1e9),14400,lambda:None,absolute_end_ns=1000*1000000000,cleanup_seconds=cleanup)
        return result,child,clock[0]

    def test_delayed_start_does_not_extend_original_deadline(self):
        result,child,end=self.exercise(now=900)
        self.assertEqual(result['absolute_end_ns'],1000*1000000000)
        self.assertEqual(result['status'],'failed');self.assertLessEqual(end,1000)

    def test_late_zero_and_head_timeout_cannot_pass(self):
        result,child,end=self.exercise(now=991,exit_at=990,cleanup=10)
        self.assertEqual(result['status'],'failed')
        result,child,end=self.exercise(now=900,cleanup=10,ignore_kill=True)
        self.assertEqual(result['status'],'failed');self.assertLessEqual(end,1000)

    def test_already_expired_absolute_deadline_still_requests_owned_cleanup(self):
        result,child,end=self.exercise(now=1001)
        self.assertEqual(result['status'],'failed');self.assertTrue(child.terminated)

class ActualDriverBudget(unittest.TestCase):
    def loop(self):
        tree=ast.parse(pathlib.Path(__file__).with_name('formal_trials.py').read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        return next(n for n in run.body if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='attempt')

    def test_next_attempt_admission_before_mkdir_uses_both_deadlines(self):
        loop=self.loop();stop=next(i for i,n in enumerate(loop.body) if isinstance(n,ast.Expr) and ast.unparse(n).startswith('target.mkdir('))
        actual=ast.fix_missing_locations(ast.Module(body=[ast.For(target=loop.target,iter=loop.iter,body=loop.body[:stop+1],orelse=[])],type_ignores=[]))
        for work,wall_end,wall_now,expected in [(2440000000000,11440,10000,True),(2439999999999,11440,10000,False),(2440000000000,11439,10000,False),(2439999999999,11440,9000,False)]:
            with self.subTest(work=work,wall_end=wall_end,wall_now=wall_now),tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory);out=root/'out';out.mkdir();base=root/'formal-trials';base.mkdir();calls=[]
                prereg=dict(attempt_timeout_seconds=1200,post_delete_observation_seconds=60,attempts=[dict(attempt_id='original_03',condition='store_pool_hit')])
                scope=dict(prereg=prereg,base=base,out=out,initial={'original_03':dict(status='unstarted')},formal_attempt_fits=budget.formal_attempt_fits,work_end_ns=work,deadline=wall_end,json=json,time=time,require_helper_only=lambda *a:calls.append('validated'),host_gpu=lambda:None,helper=None,run_id='r',boot_id='b')
                with patch.object(time,'monotonic_ns',return_value=1000000000000),patch.object(time,'time',return_value=wall_now):exec(compile(actual,'actual-before-ID-mkdir','exec'),scope)
                self.assertEqual((base/'original_03').exists(),expected);self.assertEqual(bool(calls),expected)

    def test_finally_restores_original_budget_and_preserves_timeout_result(self):
        trial=next(n for n in self.loop().body if isinstance(n,ast.Try))
        for stamp,expired in [(1100,False),(2050,True)]:
            with self.subTest(stamp=stamp),tempfile.TemporaryDirectory() as directory:
                target=pathlib.Path(directory);calls=[];now=stamp*1000000000
                def arm(end):
                    calls.append(end)
                    if end<=now:raise TimeoutError('expired original work budget')
                scope=dict(sys=sys,time=types.SimpleNamespace(monotonic_ns=lambda:now),FORMAL_ATTEMPT_EVIDENCE_SECONDS=180,FORMAL_EMERGENCY_CLEANUP_SECONDS=25,batch_budget=dict(total_end_ns=2535*1000000000,evidence_end_ns=2180*1000000000),work_end_ns=2000*1000000000,arm_formal_timer=arm,pathlib=types.SimpleNamespace(Path=lambda name:types.SimpleNamespace(read_text=lambda:'oom_kill 0')),record=dict(status='timeout',error='original attempt timeout'),target=target,json=json,name='original_03',print=lambda *a,**kw:None)
                code=compile(ast.Module(body=trial.finalbody,type_ignores=[]),'actual-finally-budget','exec')
                if expired:
                    with self.assertRaises(TimeoutError):exec(code,scope)
                else:exec(code,scope)
                saved=json.loads((target/'result.json').read_text());self.assertEqual(saved['error'],'original attempt timeout')
                self.assertEqual(calls[-1],2000*1000000000)
                self.assertLessEqual(calls[0],now+180*1000000000)

    def test_full_evidence_interval_preserves_cleanup_and_exception_chain(self):
        trial=next(n for n in self.loop().body if isinstance(n,ast.Try))
        with tempfile.TemporaryDirectory() as directory:
            target=pathlib.Path(directory);clock=[2000*1000000000];calls=[]
            def arm(end):
                calls.append(end)
                if end<=clock[0]:raise TimeoutError('expired original work budget')
            def dump(value,stream,**kw):
                json.dump(value,stream,**kw);clock[0]+=180*1000000000
            scope=dict(sys=sys,time=types.SimpleNamespace(monotonic_ns=lambda:clock[0]),FORMAL_ATTEMPT_EVIDENCE_SECONDS=180,FORMAL_EMERGENCY_CLEANUP_SECONDS=25,batch_budget=dict(total_end_ns=2535*1000000000,evidence_end_ns=2180*1000000000),work_end_ns=2000*1000000000,arm_formal_timer=arm,pathlib=types.SimpleNamespace(Path=lambda name:types.SimpleNamespace(read_text=lambda:'oom_kill 0')),record=dict(status='timeout',error='primary'),target=target,json=types.SimpleNamespace(dump=dump,dumps=json.dumps),name='original_03',print=lambda *a,**kw:None)
            try:raise ValueError('primary')
            except ValueError as primary:
                with self.assertRaises(TimeoutError) as caught:exec(compile(ast.Module(body=trial.finalbody,type_ignores=[]),'actual-evidence-advance','exec'),scope)
                self.assertIs(caught.exception.__cause__,primary)
            self.assertEqual(2535*1000000000-clock[0],355*1000000000)
            self.assertEqual(json.loads((target/'result.json').read_text())['error'],'primary')
            self.assertEqual(scope['evidence_complete_ns']-scope['now_ns'],180*1000000000)

    def test_late_success_is_rejected_before_status_update(self):
        trial=next(n for n in self.loop().body if isinstance(n,ast.Try))
        check=next(n for n in trial.body if isinstance(n,ast.If) and ast.unparse(n.test)=='time.monotonic_ns() >= attempt_end_ns')
        success=next(n for n in trial.body if isinstance(n,ast.Expr) and ast.unparse(n).startswith("record.update(status='success'"))
        for stamp,allowed in [(999,True),(1000,False),(1001,False)]:
            scope=dict(time=types.SimpleNamespace(monotonic_ns=lambda:stamp),attempt_end_ns=1000,record=dict(success=False))
            code=compile(ast.Module(body=[check,success],type_ignores=[]),'actual-late-success','exec')
            if allowed:exec(code,scope)
            else:
                with self.assertRaises(TimeoutError):exec(code,scope)
            self.assertEqual(scope['record']['success'],allowed)

if __name__=='__main__':unittest.main()
