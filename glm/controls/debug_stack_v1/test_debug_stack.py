import datetime
import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]/'tools'))
import debug_stack as d
import deploy_tree

UTC = datetime.timezone.utc
STATE = dict(run_id='sllm-debug-20261028T120000Z-0123abcd', holder='a'*32)


def info(role='head', **change):
    name = 'headservice13a' if role == 'head' else 'service13a'
    labels = {'sllm.run_id': STATE['run_id'], 'sllm.purpose': 'DEBUG-UNREVIEWED', 'sllm.holder': STATE['holder']}
    row = dict(Config=dict(Labels=labels), Image=d.plan.IMAGE, Name='/'+STATE['run_id']+'-'+name)
    labels.update(change.pop('labels', {}))
    row.update(change)
    return row


class SharedState(unittest.TestCase):
    def check(self, ps, gpu_pids='', gpu_required=True):
        with patch.object(d.os, 'getuid', return_value=2147, create=True), \
             patch.object(d, 'docker', return_value=ps), \
             patch.object(d.subprocess, 'check_output', return_value=gpu_pids):
            d.refuse_shared_state(gpu_required)

    def test_refuses_formal_debug_and_busy_gpu(self):
        with self.assertRaisesRegex(RuntimeError, 'formal run'):
            self.check('sllm-glm51fp8-20261027T011726Z-28c92313|')
        with self.assertRaisesRegex(RuntimeError, 'another debug'):
            self.check('sllm-debug-20261028T110000Z-00000000|DEBUG-UNREVIEWED')
        with self.assertRaisesRegex(RuntimeError, 'GPU occupied'):
            self.check('|', gpu_pids='1234\n')
        self.check('|\nmiles-alan|', gpu_pids='1234\n', gpu_required=False)
        self.check('|')

    def test_requires_uid(self):
        with patch.object(d.os, 'getuid', return_value=0, create=True), self.assertRaisesRegex(RuntimeError, 'UID'):
            d.refuse_shared_state(False)


class Booking(unittest.TestCase):
    now = datetime.datetime(2026, 10, 29, 6, 0, tzinfo=UTC)

    def test_booking_rules(self):
        with self.assertRaises(ValueError):
            d.booking_window(None, '2026-10-29T16:00:00+08:00', 3600, self.now)
        with self.assertRaisesRegex(ValueError, 'timezone'):
            d.booking_window('bk', '2026-10-29T16:00:00', 3600, self.now)
        with self.assertRaisesRegex(ValueError, 'margin'):
            d.booking_window('bk', '2026-10-29T15:05:00+08:00', 3600, self.now)
        window = d.booking_window('bk', '2026-10-29T15:10:00+08:00', 3600, self.now)
        self.assertEqual(window['booking_id'], 'bk')

    def test_borrow_is_explicit_capped_and_exclusive(self):
        record = d.gpu_authority(True, None, None, d.BORROW_MAX_SECONDS, self.now)
        self.assertEqual(record['mode'], 'borrowed without booking')
        with self.assertRaisesRegex(ValueError, 'capped'):
            d.gpu_authority(True, None, None, d.BORROW_MAX_SECONDS+1, self.now)
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            d.gpu_authority(True, 'bk', None, 600, self.now)
        self.assertEqual(d.gpu_authority(False, 'bk', '2026-10-29T15:10:00+08:00', 3600, self.now)['booking_id'], 'bk')
        with self.assertRaises(ValueError):
            d.gpu_authority(False, None, None, 600, self.now)


class Source(unittest.TestCase):
    """--source-tree accepts only an intact deploy_tree push and runs from a verified per-run copy."""
    @classmethod
    def setUpClass(cls):
        cls.tree, cls.receipt = deploy_tree.build('HEAD', 'debug')

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.host = Path(directory.name)
        (self.host/'code').mkdir()
        patcher = patch.object(d, 'HOST', self.host)
        patcher.start()
        self.addCleanup(patcher.stop)

    def push(self, tree=None, receipt=None, name=None):
        tree = dict(self.tree) if tree is None else tree
        receipt = dict(self.receipt) if receipt is None else receipt
        root = self.host/'code'/(name or 'source-debug-%s-%s' % (receipt['code_commit'][:12], receipt['tree_sha256'][:12]))
        for path, data in tree.items():
            (root/path).parent.mkdir(parents=True, exist_ok=True)
            (root/path).write_bytes(data)
        (root/d.SOURCE_RECEIPT).write_text(json.dumps(receipt))
        return root

    def test_intact_push_is_copied_byte_for_byte(self):
        root = self.push()
        receipt = d.verified_source(root)
        run_root = self.host/'debug'/STATE['run_id']
        run_root.mkdir(parents=True)
        provenance = d.copy_source(root, run_root, receipt)
        self.assertEqual((provenance['code_commit'], provenance['tree_sha256'], provenance['files']),
                         (self.receipt['code_commit'], self.receipt['tree_sha256'], len(self.tree)))
        self.assertEqual({p: (run_root/'source'/p).read_bytes() for p in self.tree}, self.tree)
        self.assertFalse((run_root/'source'/'run-manifest.json').exists())

    def test_changed_or_extra_files_are_refused(self):
        root = self.push()
        (root/'workspace.py').write_bytes(self.tree['workspace.py']+b'\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            d.verified_source(root)
        (root/'workspace.py').write_bytes(self.tree['workspace.py'])
        (root/'stray.py').write_bytes(b'x = 1\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            d.verified_source(root)

    def test_consistent_tree_with_formal_identity_is_refused(self):
        tree = dict(self.tree, **{'run-manifest.json': b'{"run_id": "sllm-glm51fp8-x"}'})
        rows = deploy_tree.inventory(tree)
        receipt = dict(self.receipt, files=rows, tree_sha256=deploy_tree.sha256(json.dumps(rows, sort_keys=True).encode()))
        with self.assertRaisesRegex(ValueError, 'formal run identity'):
            d.verified_source(self.push(tree, receipt))

    def test_location_name_and_kind_must_match(self):
        with self.assertRaisesRegex(ValueError, 'deploy_tree push'):
            d.verified_source(self.host/'elsewhere'/self.push().name)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            d.verified_source(self.push(name='source-debug-%s-%s' % ('0'*12, self.receipt['tree_sha256'][:12])))
        formal = dict(self.receipt, kind='formal', code_commit='1'*40)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            d.verified_source(self.push(receipt=formal))


class Ownership(unittest.TestCase):
    def test_actual_worker_call_uses_sleep_only_for_explicit_startup_control(self):
        tree = ast.parse((HERE/'debug_stack.py').read_text())
        up = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'up')
        statement = next(node for node in ast.walk(up) if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == 'worker' for target in node.targets))
        for startup in (False, True):
            args = Mock(seconds=300, weight_format='safetensors', no_query=False, condition='safetensors_disk_cold')
            namespace = dict(vars(d), args=args, state={'startup_only':startup}, condition=args.condition,
                             run_id=STATE['run_id'], holder=STATE['holder'], active=d.plan.ACTIVE,
                             deadline=12345, run_root=Path('/unused'), ip='172.17.0.2', sha256=lambda _: 'a'*64)
            exec(compile(ast.Module(body=[statement], type_ignores=[]), '<actual worker call>', 'exec'), namespace)
            argv = namespace['worker']['spec']['argv']
            self.assertEqual(argv[-2:] == ['/bin/sleep','30'], startup)
            self.assertEqual(any('debug_service.py' in part for part in argv), not startup)

    def test_startup_control_cannot_reuse_full_model_review(self):
        source = {'tree_sha256':'x', 'code_commit':'c'}
        tools = {p.relative_to(d.TREE).as_posix():d.sha256(p) for p in d.TOOLS}
        review = dict(scope='glm-startup-monitor-control-v1', verdict='PASS', tool_sha256=tools,
                      source_tree_sha256='x', code_commit='c', work_seconds=180, total_seconds=900)
        d.verify_supplement_review(review, source, True)
        with self.assertRaises(RuntimeError):
            d.verify_supplement_review(review, source)
        with self.assertRaises(RuntimeError):
            d.verify_supplement_review(dict(review, scope='glm-controller-supplement-v1'), source, True)
        with self.assertRaises(RuntimeError):
            d.verify_supplement_review(review, source, True, 'ray')
        d.verify_supplement_review(dict(review, scope='glm-ray-startup-monitor-control-v1'), source, True, 'ray')

    def test_actual_monitor_retains_rejected_sample_without_ready(self):
        tree = ast.parse((HERE/'debug_stack.py').read_text())
        measure = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'measure')
        monitor = next(node for node in measure.body if isinstance(node, ast.FunctionDef) and node.name == 'monitor')
        rules = {}
        source = ast.parse((d.TREE/'runtime/active/gpu_identity13.py').read_text())
        rule = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'require_monitor_sample')
        exec(compile(ast.Module(body=[rule], type_ignores=[]), '<actual monitor rule>', 'exec'), rules)
        snapshot = dict(sample_begin_ns=1, last_sample_ns=6_000_000_002, sample_end_ns=6_000_000_003,
                        stage_timings_ns=[['GPU devices', 1, 6_000_000_002]])
        foreign = dict(sample_begin_ns=1, last_sample_ns=2, sample_end_ns=3, gpu='same', foreign_gpu_pids=[123])
        healthy = dict(foreign, foreign_gpu_pids=[])
        for sample, failure in [(snapshot,None),(None,OSError(5, 'injected GPU query failure')),
                                (foreign,[foreign,healthy])]:
            with self.subTest(sample=sample, failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                errors = []
                retry = dict(role='worker', path='/sys/fs/cgroup/worker/io.stat', errno=3)
                def rejected_collect(state, *, cgroup_retries, previous_mid_ns):
                    cgroup_retries.append(retry)
                    raise failure
                sampler = Mock(return_value=sample, side_effect=(
                    rejected_collect if isinstance(failure, OSError) else failure))
                namespace = dict(vars(d), root=root, state={}, stopped=Mock(is_set=Mock(return_value=False)),
                                 monitor_errors=errors, supplement_sample=sampler,
                                 gpu_memory_by_uuid=lambda _: {'gpu':0}, baseline={'gpu':0},
                                 require_monitor_sample=rules['require_monitor_sample'])
                exec(compile(ast.Module(body=[monitor], type_ignores=[]), '<actual monitor>', 'exec'), namespace)
                namespace['monitor']()
                receipt = json.loads((root/'controller-monitor-rejected.json').read_text())
                self.assertFalse(receipt['healthy'])
                self.assertEqual(receipt['snapshot'], sample)
                self.assertEqual(receipt['stage'], 'validate' if sample is not None else 'collect')
                self.assertEqual(receipt['cgroup_read_retries'], [retry] if isinstance(failure, OSError) else [])
                if sample is foreign:
                    self.assertIn('unattributed GPU processes',receipt['error'])
                    sampler.assert_called_once()
                self.assertTrue(errors)
                self.assertFalse((root/'supplement-monitor-ready.json').exists())
                self.assertEqual((root/'controller-monitor.jsonl').read_bytes(), b'')

    def test_up_head_call_propagates_supplement_inputs(self):
        tree = ast.parse((HERE/'debug_stack.py').read_text())
        up = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'up')
        statement = next(node for node in ast.walk(up) if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == 'head_plan' for target in node.targets))
        code = compile(ast.Module(body=[statement], type_ignores=[]), '<actual up head call>', 'exec')
        for condition in (None, 'safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit'):
            args = Mock(seconds=1800, weight_format='serverless_llm' if condition and condition.startswith('store_') else 'safetensors',
                        no_query=False)
            namespace = dict(vars(d), args=args, state={}, condition=condition, run_id=STATE['run_id'], holder=STATE['holder'],
                             active=d.plan.ACTIVE, deadline=12345, run_root=Path('/unused'), sha256=lambda _: 'a'*64)
            with self.subTest(condition=condition):
                exec(code, namespace)
                expected = (d.plan.worker_spec('x')['env']['SLLM_EVENT_DIR'] if condition
                            else d.plan.head_spec()['env'].get('SLLM_EVENT_DIR'))
                self.assertEqual(namespace['head_plan']['spec']['env'].get('SLLM_EVENT_DIR'), expected)

    def test_actual_supplement_head_env_must_include_shared_router_trace(self):
        state = dict(STATE, condition='safetensors_disk_cold')
        head = info(State=dict(Running=True, Pid=123))
        head['Config']['Env'] = []
        with patch.object(d, 'docker', side_effect=['c'*64, '']), patch.object(d, 'write'), \
             patch.object(d, 'inspect', return_value=head), self.assertRaisesRegex(RuntimeError, 'event directory differs'):
            d.create_and_start(['docker','create'], 'head', state, Path('/unused'))

    def test_owned_requires_every_identity_field(self):
        self.assertTrue(d.owned(info(), STATE, 'head'))
        self.assertTrue(d.owned(info('worker'), STATE, 'worker'))
        self.assertFalse(d.owned(info(), STATE, 'worker'))
        for change in (dict(labels={'sllm.holder': 'b'*32}), dict(labels={'sllm.purpose': 'formal'}),
                       dict(Image='sha256:'+'0'*64), dict(Name='/other')):
            with self.subTest(change=change):
                self.assertFalse(d.owned(info(**json.loads(json.dumps(change))), STATE, 'head'))

    def test_foreign_container_is_never_removed(self):
        state = dict(STATE, head_container_id='c'*64)
        with patch.object(d, 'inspect', return_value=info(labels={'sllm.holder': 'b'*32})), \
             patch.object(d, 'docker') as docker, self.assertRaisesRegex(RuntimeError, 'not touching'):
            d.remove_owned(state, 'head')
        docker.assert_not_called()

    def test_docker_stop_success_does_not_prove_normal_exit(self):
        for code, normal in [(0, True), (137, False), (17, False)]:
            state = dict(STATE, worker_container_id='c'*64)
            before = info('worker', State=dict(Running=True, ExitCode=0, OOMKilled=False, Error=''))
            after = info('worker', State=dict(Running=False, ExitCode=code, OOMKilled=False, Error=''))
            with self.subTest(code=code), patch.object(d, 'inspect', side_effect=[before, after]), \
                 patch.object(d.subprocess, 'run', return_value=type('R', (), dict(returncode=0, stderr=b''))()), \
                 patch.object(d, 'docker', return_value=''):
                self.assertEqual(d.remove_owned(state, 'worker'), 'removed')
            self.assertEqual(state['exit_evidence']['worker']['normal_exit_verified'], normal)

    def test_cleanup_failure_does_not_skip_other_owned_role(self):
        state = dict(STATE)
        with patch.object(d, 'load_state', return_value=state), patch.object(d, 'write'), \
             patch.object(d, 'remove_owned', side_effect=[RuntimeError('worker failed'), 'removed']) as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'cleanup incomplete'):
                d.down(type('Args', (), dict(run_id=STATE['run_id']))())
        self.assertEqual(cleanup.call_count, 2)
        self.assertIn('worker', state['cleanup_errors'])


class HeadReadiness(unittest.TestCase):
    def run_wait(self, logs, states):
        clock = iter(range(1000))
        outputs = iter(logs)
        with patch.object(d, 'inspect', side_effect=[dict(State=dict(Running=s)) for s in states]), \
             patch.object(d.subprocess, 'run', side_effect=lambda *a, **k: type('R', (), dict(stdout=next(outputs)))()):
            return d.wait_head_ready('c'*64, seconds=10, sleep=lambda n: None, clock=lambda: next(clock))

    def test_waits_for_ray_runtime_mark(self):
        self.assertGreater(self.run_wait(['starting', 'still starting', 'x\nRay runtime started.\n'], [True]*3), 0)

    def test_stopped_head_or_timeout_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'stopped'):
            self.run_wait(['starting'], [False])
        with self.assertRaises(TimeoutError):
            self.run_wait(['starting']*20, [True]*20)


class CommandLine(unittest.TestCase):
    def test_entry_points_parse_without_side_effects(self):
        for script, args in (('debug_stack.py', ['--help']), ('debug_stack.py', ['up', '--help']), ('debug_plan.py', ['--help'])):
            result = subprocess.run([sys.executable, str(HERE/script), *args], capture_output=True, text=True, timeout=60)
            with self.subTest(script=script, args=args):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)


class ControllerClock(unittest.TestCase):
    def finish(self, state, boot_id='boot-a', end_ns=3000000000):
        with patch.object(d, 'load_state', return_value=state), \
             patch.object(d, 'remove_owned', return_value='absent') as cleanup, \
             patch.object(d.pathlib.Path, 'read_text', return_value=boot_id), \
             patch.object(d.time, 'monotonic_ns', return_value=end_ns), \
             patch.object(d, 'write'):
            result = d.down(type('Args', (), dict(run_id=STATE['run_id']))())
        self.assertEqual(cleanup.call_count, 2)
        return result

    def test_same_boot_and_first_endpoint_preserved(self):
        state = dict(STATE, controller_begin_ns=1000000000, controller_boot_id='boot-a')
        result = self.finish(state)
        self.assertEqual(result['controller_elapsed_s'], 2.0)
        self.assertTrue(result['controller_timing_valid'])
        self.assertEqual(self.finish(result, end_ns=5000000000)['controller_end_ns'], 3000000000)

    def test_missing_cross_boot_and_reversed_clock_do_not_block_cleanup(self):
        for extra in ({}, dict(controller_begin_ns=1, controller_boot_id='old-boot'),
                      dict(controller_begin_ns=4000000000, controller_boot_id='boot-a')):
            with self.subTest(extra=extra):
                result = self.finish(dict(STATE, **extra))
                self.assertIsNone(result['controller_elapsed_s'])
                self.assertFalse(result['controller_timing_valid'])
                self.assertEqual(result['status'], 'released')


class SupplementAdmission(unittest.TestCase):
    def test_actual_release_loop_counts_from_first_sample_not_pre_sample_delay(self):
        tree = ast.parse((HERE/'debug_stack.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'measure')
        main = next(n for n in function.body if isinstance(n, ast.Try) and n.finalbody)
        release = next(n for n in main.finalbody if isinstance(n, ast.Try) and any(
                       isinstance(value, ast.Constant) and value.value == 'release60.jsonl' for value in ast.walk(n)))
        rows = [dict(monotonic_ns=t, retained_cgroups={'head':dict(absent=True), 'worker':dict(absent=True)})
                for t in (10**9, 60950000000, 61000000000)]
        with tempfile.TemporaryDirectory() as temp, patch.object(d.time, 'sleep'), \
             patch.object(d.time, 'monotonic_ns', return_value=0):
            sample = Mock(side_effect=rows)
            namespace = dict(vars(d), root=Path(temp), state={}, baseline={}, result={},
                             supplement_sample=sample, require_supplement_release=Mock())
            exec(compile(ast.Module(body=release.body, type_ignores=[]), '<actual-release-loop-CPU>', 'exec'), namespace)
            self.assertEqual(sample.call_count, 3)
            self.assertEqual(namespace['result']['release_observation_s'], 60)

    def test_actual_measure_work_fragment_does_not_call_up_after_monitor_failure(self):
        tree = ast.parse((HERE/'debug_stack.py').read_text())
        measure = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'measure')
        work = next(n for n in measure.body if isinstance(n, ast.Try) and any(
                    isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'up'
                    for call in ast.walk(n)))
        start = Mock()
        namespace = dict(vars(d), root=Path('/unused'), monitor_errors=['first sample failed'], state=STATE, boot='boot', up=start)
        with patch.object(sys, 'path', [str(d.TREE/'runtime/active'), *sys.path]), \
             self.assertRaisesRegex(RuntimeError, 'before container startup'):
            exec(compile(ast.Module(body=work.body, type_ignores=[]), '<actual-measure-work-CPU>', 'exec'), namespace)
        start.assert_not_called()

    def test_first_monitor_gate_rejects_failure_and_unhealthy_receipt(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(sys, 'path', [str(d.TREE/'runtime/active'), *sys.path]):
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, 'before container startup'):
                d.await_first_monitor(root, ['sample failed'], 'run', 'boot')
            now = d.time.monotonic_ns()
            row = dict(sample_begin_ns=now, last_sample_ns=now, sample_end_ns=now, run_id='run', boot_id='boot',
                       observers_ok=True, history_ok=True, first_monitor_error=None, foreign_gpu_pids=[])
            path = root/'supplement-monitor-ready.json'
            path.write_text(json.dumps(row))
            self.assertEqual(d.await_first_monitor(root, [], 'run', 'boot'), row)
            row['observers_ok'] = False
            path.write_text(json.dumps(row))
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
                d.await_first_monitor(root, [], 'run', 'boot')

    def test_monitor_cgroup_disappearance_and_pid_reuse_remain_distinct(self):
        cid = 'c'*64
        base = d.pathlib.Path('/sys/fs/cgroup/my.slice')/('docker-'+cid+'.scope')
        original_read, original_stat = Path.read_text, Path.stat
        for mode in ('cgroup_exit', 'pid_reuse', 'member_exit', 'transient_cgroup',
                     'transient_esrch', 'late_success', 'shared_deadline'):
            gone, stat_reads, cgroup_reads = [False], [0], [0]
            now, retries = [0], []
            worker_cid = 'd'*64
            worker_base = base.with_name('docker-'+worker_cid+'.scope')
            def stat(path, **kwargs):
                if path in (base, worker_base):
                    if gone[0]:
                        raise FileNotFoundError(2, 'cgroup removed')
                    return type('Stat', (), dict(st_ino=123))()
                return original_stat(path, **kwargs)
            def read(path, **kwargs):
                if path.suffix == '.json':
                    return original_read(path, **kwargs)
                if path in (Path('/proc/123/stat'), Path('/proc/124/stat')):
                    stat_reads[0] += 1
                    tick = 2 if mode == 'pid_reuse' and stat_reads[0] > 1 else 1
                    return '123 (init) '+' '.join(['0']*19+[str(tick)])
                if path == Path('/proc/123/cgroup'):
                    return '0::'+base.as_posix().removeprefix('/sys/fs/cgroup')+'\n'
                if path == Path('/proc/124/cgroup'):
                    return '0::'+worker_base.as_posix().removeprefix('/sys/fs/cgroup')+'\n'
                if path == base/'cgroup.procs' and mode == 'member_exit':
                    return '999'
                if path == Path('/proc/999/stat'):
                    raise ProcessLookupError(3, 'member exited', str(path))
                if path == base/'memory.current' and mode == 'cgroup_exit':
                    gone[0] = True
                    raise FileNotFoundError(2, 'cgroup exited between reads')
                if path == worker_base/'memory.current' and mode == 'shared_deadline':
                    raise ProcessLookupError(3, 'worker cgroup read interrupted')
                if path == base/'io.stat' and mode in ('transient_cgroup', 'transient_esrch', 'late_success', 'shared_deadline'):
                    cgroup_reads[0] += 1
                    if cgroup_reads[0] == 1:
                        if mode == 'transient_cgroup':
                            raise FileNotFoundError(2, 'cgroup file briefly unavailable')
                        raise ProcessLookupError(3, 'cgroup read interrupted')
                    if mode == 'late_success':
                        now[0] = 2_501_000_000
                    if mode == 'shared_deadline':
                        now[0] = 2_490_000_000
                    return ''
                return ''
            initial = info(State=dict(Pid=123, Running=True))
            rechecks = [initial, initial] if mode == 'late_success' else [initial, None]
            if mode == 'shared_deadline':
                worker = info('worker', State=dict(Pid=124, Running=True))
                rechecks = [initial, worker, worker]
            with self.subTest(mode=mode), patch.object(d.pathlib.Path, 'stat', stat), \
                 patch.object(d.pathlib.Path, 'read_text', read), \
                 patch.object(d.pathlib.Path, 'resolve', lambda path: path), \
                 patch.object(d.time, 'monotonic_ns', side_effect=lambda: now[0]), \
                 patch.object(d.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0]+round(delay*1e9))), \
                 patch.object(d, 'inspect', side_effect=rechecks), \
                 patch.object(d.subprocess, 'check_output', side_effect=['', '']):
                if mode == 'pid_reuse':
                    with self.assertRaisesRegex(RuntimeError, 'PID reused'):
                        d.supplement_sample(dict(STATE, head_container_id=cid))
                elif mode == 'cgroup_exit':
                    row = d.supplement_sample(dict(STATE, head_container_id=cid))
                    self.assertTrue(row['retained_cgroups']['head']['absent'])
                    self.assertTrue(row['containers']['head']['disappeared_during_sample'])
                elif mode in ('transient_cgroup', 'transient_esrch'):
                    row = d.supplement_sample(dict(STATE, head_container_id=cid))
                    self.assertGreaterEqual(cgroup_reads[0], 2)
                    self.assertEqual(row['cgroup_read_retries'][0]['errno'], 2 if mode == 'transient_cgroup' else 3)
                    self.assertFalse(row['containers']['head'].get('disappeared_during_sample', False))
                elif mode == 'late_success':
                    with self.assertRaisesRegex(RuntimeError, 'remains unverified'):
                        d.supplement_sample(dict(STATE, head_container_id=cid), cgroup_retries=retries)
                    self.assertEqual(retries[0]['path'], str(base/'io.stat'))
                    self.assertEqual(retries[0]['deadline_ns'], 2_500_000_000)
                elif mode == 'shared_deadline':
                    with self.assertRaisesRegex(RuntimeError, 'remains unverified'):
                        d.supplement_sample(dict(STATE, head_container_id=cid, worker_container_id=worker_cid),
                                            cgroup_retries=retries)
                    self.assertEqual([(r['role'], r['deadline_ns']) for r in retries],
                                     [('head', 2_500_000_000), ('worker', 2_500_000_000)])
                else:
                    row = d.supplement_sample(dict(STATE, head_container_id=cid))
                    self.assertEqual(row['processes_disappeared_during_sample'][0]['pid'], 999)
                    self.assertEqual(row['processes_disappeared_during_sample'][0]['errno'], 3)
                    self.assertFalse(row['containers']['head'].get('disappeared_during_sample', False))

    def test_monitor_requires_same_container_exit_before_accepting_missing_process(self):
        before = info(State=dict(Pid=123, Running=True))
        stopped = info(State=dict(Pid=0, Running=False))
        original_read = Path.read_text
        def read(path, **kwargs):
            if path.suffix == '.json':
                return original_read(path, **kwargs)
            if path.as_posix().endswith('/123/stat'):
                raise FileNotFoundError(2, 'exited during sample')
            return 'boot' if path.name == 'boot_id' else ''
        for final in (stopped, None):
            with self.subTest(final=final), patch.object(d, 'inspect', side_effect=[before, final]), \
                 patch.object(d.pathlib.Path, 'read_text', read), \
                 patch.object(d.subprocess, 'check_output', side_effect=['', '']):
                row = d.supplement_sample(dict(STATE, head_container_id='c'*64))
            self.assertTrue(row['containers']['head']['disappeared_during_sample'])
            self.assertEqual(row['containers']['head']['memory_observation'], 'missing; not zero')
        with patch.object(d, 'inspect', side_effect=[before, before]), \
             patch.object(d.time, 'monotonic', side_effect=[0, 3]), \
             patch.object(d.pathlib.Path, 'read_text', read), self.assertRaisesRegex(RuntimeError, 'remains unverified'):
            d.supplement_sample(dict(STATE, head_container_id='c'*64))
        with patch.object(d, 'inspect', side_effect=[before, before, stopped]), \
             patch.object(d.pathlib.Path, 'read_text', read), \
             patch.object(d.subprocess, 'check_output', side_effect=['', '']):
            row = d.supplement_sample(dict(STATE, head_container_id='c'*64))
        self.assertTrue(row['containers']['head']['rechecks'])

    def test_idle_gpu_does_not_bypass_host_numa_or_io_admission(self):
        gib = 1024**3
        sample = dict(hostname='qjhs3', uid=2147, cgroup_filesystem=dict(fstype='cgroup2', options='rw,nosuid'),
                      host_memory_bytes=dict(MemTotal=1923*gib, MemAvailable=1850*gib,
                      SwapTotal=0, AnonPages=3*gib, Mlocked=0, Unevictable=0, Shmem=0),
                      numa_reclaimable_bytes={'node0':950*gib, 'node1':950*gib},
                      gpu_devices=[dict(index=i, uuid='GPU-'+str(i), name='H20-3e', total_MiB=143771, used_MiB=0) for i in range(8)],
                      gpu_processes='', loadavg1=.2, read_sectors=[100, 100], read_inflight=[0, 0],
                      filesystems=[dict(fstype='xfs', source='/dev/nvme1n1')]*2)
        d.check_supplement_resources(sample)
        for kind in ('locked', 'numa', 'memory', 'gpu', 'io', 'local_events'):
            bad = copy.deepcopy(sample)
            if kind == 'locked': bad['host_memory_bytes']['Mlocked'] = 2*gib
            if kind == 'numa': bad['numa_reclaimable_bytes']['node1'] = 700*gib
            if kind == 'memory': bad['host_memory_bytes']['MemAvailable'] = 1600*gib
            if kind == 'gpu': bad['gpu_devices'][7]['used_MiB'] = 1
            if kind == 'io': bad['read_sectors'][1] += 1
            if kind == 'local_events': bad['cgroup_filesystem']['options'] += ',memory_localevents'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                d.check_supplement_resources(bad)

    def receipt(self):
        source = dict(code_commit='a'*40, tree_sha256='b'*64)
        review = dict(scope='glm-controller-supplement-v1', verdict='PASS', code_commit=source['code_commit'],
                      source_tree_sha256=source['tree_sha256'],
                      tool_sha256={p.relative_to(d.TREE).as_posix():d.sha256(p) for p in d.TOOLS},
                      work_seconds=d.SUPPLEMENT_WORK_SECONDS, total_seconds=d.SUPPLEMENT_TOTAL_SECONDS)
        return review, source

    def test_full_current_review_and_all_bindings_required(self):
        review, source = self.receipt()
        d.verify_supplement_review(review, source)
        self.assertTrue(all('\\' not in key for key in review['tool_sha256']))
        windows_keys = {key.replace('/', '\\'):value for key, value in review['tool_sha256'].items()}
        with self.assertRaisesRegex(RuntimeError, 'R1 not approved'):
            d.verify_supplement_review(dict(review, tool_sha256=windows_keys), source)
        for key, value in [('verdict', 'FAIL'), ('code_commit', 'c'*40), ('tool_sha256', {}),
                           ('source_tree_sha256', 'd'*64), ('work_seconds', 3600), ('total_seconds', 7200)]:
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'R1 not approved'):
                d.verify_supplement_review(dict(review, **{key:value}), source)

    def test_no_r1_rejected_before_resource_or_docker_calls(self):
        with patch.object(d, 'verified_source', return_value=dict(tree_sha256='b'*64)), \
             patch.object(d, 'controller_unit', return_value=(1, {})), \
             patch.object(d.pathlib.Path, 'read_text', side_effect=['boot', '{"verdict":"FAIL"}']), \
             patch.object(d, 'refuse_shared_state') as resource, patch.object(d, 'docker') as docker:
            with self.assertRaisesRegex(RuntimeError, 'R1 not approved'):
                d.measure(type('Args', (), dict(source_tree='unused', review='unused', controller_unit='unused'))())
        resource.assert_not_called()
        docker.assert_not_called()

    def test_independent_unit_requires_exact_deadline_pid_and_private_cgroup(self):
        unit = STATE['run_id']+'-controller.service'
        slice_cgroup = '/'+STATE['run_id']+'.slice'
        cgroup = slice_cgroup+'/'+unit
        properties = dict(Id=unit, User='2147', MainPID='123', KillMode='control-group', SendSIGKILL='yes',
                          RuntimeMaxUSec='59min 30s', TimeoutStopUSec='30s', NRestarts='0', InvocationID='a'*32,
                          Transient='yes', Restart='no', RemainAfterExit='yes', ActiveState='active', SubState='running',
                          Slice=STATE['run_id']+'.slice', ControlGroup=cgroup,
                          ExecStart=str(d.HERE/'debug_stack.py')+' measure', ExecMainStartTimestampMonotonic='1')
        self.assertEqual(d.verify_controller_unit(properties, unit, 123, cgroup, 'a'*32, slice_cgroup), 1000)
        for key, value in [('MainPID','124'), ('RuntimeMaxUSec','infinity'), ('KillMode','process'),
                           ('Slice','system.slice'), ('ControlGroup','/other'), ('RemainAfterExit','no')]:
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'deadline/identity differs'):
                d.verify_controller_unit(dict(properties, **{key:value}), unit, 123, cgroup, 'a'*32, slice_cgroup)

    def test_release_requires_eight_devices_memory_and_owned_cgroups(self):
        raw = '\n'.join(f'{i}, GPU-{i}, 0 MiB' for i in range(8))
        baseline = d.gpu_memory_by_uuid(raw)
        row = dict(gpu=raw, gpu_processes='', private_slice_memory_events='oom 0\noom_kill 0\n',
                   containers={'head':dict(absent=True), 'worker':dict(absent=True)},
                   retained_cgroups={role:dict(absent=True) for role in ('head', 'worker')})
        d.require_supplement_release(row, baseline)
        # Child cgroups may already be gone and Docker PID1 may report OOMKilled=false.
        with self.assertRaisesRegex(RuntimeError, 'OOM'):
            d.require_supplement_release(dict(row, private_slice_memory_events='oom 1\noom_kill 1\n'), baseline)
        with self.assertRaisesRegex(RuntimeError, 'eight-rank'):
            d.gpu_memory_by_uuid('\n'.join(raw.splitlines()[:-1]))
        with self.assertRaisesRegex(RuntimeError, 'baseline not released'):
            d.require_supplement_release(dict(row, gpu=raw.replace('GPU-7, 0', 'GPU-7, 20')), baseline)
        retained = dict(absent=False, values={'cgroup.events':'populated 0\n', 'cgroup.procs':'',
                       'memory.stat':'anon 4096\nshmem 0\nunevictable 0\n'})
        with self.assertRaisesRegex(RuntimeError, 'memory retained'):
            d.require_supplement_release(dict(row, retained_cgroups={'head':dict(absent=True), 'worker':retained}), baseline)

    def test_root_probe_rejects_writable_stale_or_unarmed_evidence(self):
        run = STATE['run_id']
        path = d.pathlib.Path('/run')/run/'guard-verification.json'
        birth = dict(pid=999999999, stat='0 (probe) '+' '.join(['0']*20))
        proof = dict(run_id=run, holder='a'*32, boot_id='boot', completed_unix=100,
                     installer_sha256='installer', guard_sha256='guard',
                     host_probe_final=dict(Result='timeout'),
                     slice_probe_final=dict(Result='success', ExecMainStatus='0'),
                     container_final=dict(Id='c'*64, State=dict(Running=False, ExitCode=137, OOMKilled=False)),
                     main_slice=dict(ControlGroup='/'+run+'.slice', MemoryMax='1825361100800', MemorySwapMax='0', TasksMax='8192'),
                     deadline_next_monotonic_usec=3200000000, host=dict(process=birth), setsid_child=birth,
                     slice_sentinel=dict(process=birth), container_process=birth)
        file_mode = [0o100444]
        def stat(p, **kwargs):
            if p == path:
                return type('Stat', (), dict(st_uid=0, st_mode=file_mode[0]))()
            if p == path.parent:
                return type('Stat', (), dict(st_uid=0, st_mode=0o40755))()
            raise FileNotFoundError(2, 'absent probe process', str(p))
        outputs = lambda: iter(['{ argv[]=/usr/bin/systemctl kill --kill-who=all --signal=SIGKILL '+run+'.slice ; }',
                                'active', json.dumps(dict(data=3200000000))])
        with patch.object(d.pathlib.Path, 'stat', stat), patch.object(d.pathlib.Path, 'is_symlink', return_value=False), \
             patch.object(d.pathlib.Path, 'read_text', side_effect=lambda: json.dumps(proof)), \
             patch.object(d.time, 'time', return_value=101), \
             patch.object(d, 'sha256', side_effect=lambda p: 'installer' if p.name.endswith('.sh') else 'guard'), \
             patch.object(d, 'inspect', return_value=None), \
             patch.object(d.subprocess, 'check_output', side_effect=outputs()):
            self.assertEqual(d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice'), proof)
            file_mode[0] = 0o100644
            with self.assertRaisesRegex(RuntimeError, 'root ownership'):
                d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice')
            file_mode[0] = 0o100444
            proof['completed_unix'] = -100
            with self.assertRaisesRegex(RuntimeError, 'probe evidence differs'):
                d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice')
            proof['completed_unix'] = 100
            proof['deadline_delay_seconds'] = 900
            proof['deadline_next_monotonic_usec'] = 900000000
            startup_outputs = ['{ argv[]=/usr/bin/systemctl kill --kill-who=all --signal=SIGKILL '+run+'.slice ; }',
                               'active', json.dumps(dict(data=900000000))]
            with patch.object(d.subprocess, 'check_output', side_effect=startup_outputs):
                d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice', startup_only=True)
            proof['deadline_next_monotonic_usec'] = 901000000
            with patch.object(d.subprocess, 'check_output', side_effect=startup_outputs[:2]+[json.dumps(dict(data=901000000))]), \
                    self.assertRaisesRegex(RuntimeError, 'total budget'):
                d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice', startup_only=True)
            proof['deadline_next_monotonic_usec'] = 900000000
            proof['deadline_delay_seconds'] = 3240
            with patch.object(d.subprocess, 'check_output', side_effect=startup_outputs), self.assertRaisesRegex(RuntimeError, 'total budget'):
                d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice', startup_only=True)
            with patch.object(d.subprocess, 'check_output', side_effect=['wrong-slice']):
                with self.assertRaisesRegex(RuntimeError, 'not armed'):
                    d.verify_root_probe(path, run, 'boot', 1, '/'+run+'.slice')


if __name__ == '__main__':
    unittest.main()
