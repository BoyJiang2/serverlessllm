import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import debug_plan as p

ROOT = HERE.parents[1]
ACTIVE = ROOT/'runtime/active'
PACKET = ROOT/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313'
# launch_control13.py is pinned by tools/test_c23_regression.py, which compares it with the C23 commit directly.
FROZEN = ('workspace.py', 'materialize_control13.py', 'headservice13a.json', 'service13a-template.json', 'run-manifest.json')
RUN = 'sllm-debug-20261028T120000Z-0123abcd'
HOLDER = 'f'*32
IP = '172.17.0.2'
DEADLINE = 1793200000


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrozenDerivation(unittest.TestCase):
    def test_ray_stage_reuses_application_entry_and_cannot_be_selected_for_model_run(self):
        args = dict(run_id=RUN, holder=HOLDER, seconds=300, head_ip=IP, deadline_unix=DEADLINE,
                    supplement_condition='safetensors_disk_cold', supplement_start_sha='a'*64)
        normal = p.plan(**args)
        ray = p.plan(**args, startup_only=True, startup_stage='ray')
        self.assertEqual(ray['head'], normal['head'])
        expected = json.loads(json.dumps(normal['worker']['spec']))
        expected['env']['SLLM_STARTUP_RAY_ONLY'] = '1'
        self.assertEqual(ray['worker']['spec'], expected)
        with self.assertRaises(ValueError):
            p.plan(**args, startup_stage='ray')
        with self.assertRaises(ValueError):
            p.plan(**args, startup_only=True, startup_stage='invalid')

    def test_startup_control_changes_only_worker_entry_and_keeps_identity_and_gpu_plan(self):
        args = dict(run_id=RUN, holder=HOLDER, seconds=300, head_ip=IP, deadline_unix=DEADLINE,
                    supplement_condition='safetensors_disk_cold', supplement_start_sha='a'*64)
        normal = p.plan(**args)
        control = p.plan(**args, startup_only=True)
        self.assertEqual(control['head'], normal['head'])
        expected = dict(normal['worker']['spec'], argv=p.lifetime_wrapper(300, 'worker')+['/bin/sleep', '30'])
        self.assertEqual(control['worker']['spec'], expected)
        self.assertEqual(control['worker']['docker_argv'],
                         p.debug_args(expected, RUN, HOLDER, control['source'], DEADLINE))
        for condition in (None, 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit'):
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                p.plan(**dict(args, supplement_condition=condition), startup_only=True)

    @classmethod
    def setUpClass(cls):
        receipt = json.loads((PACKET/'executed-source-archive.json').read_text())
        hashes = {x['path']: x['sha256'] for x in receipt['files_checked_before_and_after']}
        for name in FROZEN:
            assert hashlib.sha256((ACTIVE/name).read_bytes()).hexdigest() == hashes[name], name
        sys.path.insert(0, str(ACTIVE))
        cls.workspace = load('frozen_workspace_for_debug', ACTIVE/'workspace.py')
        cls.materialize = load('frozen_materialize_for_debug', ACTIVE/'materialize_control13.py')
        manifest = json.loads((ACTIVE/'run-manifest.json').read_text())
        manifest.update(scope='G1 independent untimed controls only; zero formal IDs', execution_purpose='G1',
                        monitor_diagnostic_trace=False, grpc_shutdown_probe=False)
        cls.g1 = manifest

    def test_nonformal_mounts_equal_frozen_rule(self):
        with patch.object(self.workspace, 'manifest', return_value=self.g1):
            frozen = self.workspace.container_mounts()
        ours = p.nonformal_mounts(p.HOST/'code/experiment', p.HOST/'validation/controls'/self.g1['run_id'])
        self.assertEqual(json.dumps(ours).encode(), json.dumps(frozen).encode())

    def test_specs_equal_frozen_expected_spec(self):
        info = json.dumps([dict(State=dict(Running=True),
                                Config=dict(Labels={'sllm.run_id': self.g1['run_id'], 'sllm.uid': '2147'}),
                                NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress=IP))))])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'run-manifest.json').write_text(json.dumps(self.g1))
            with patch.object(self.materialize, 'call', return_value=info):
                head = self.materialize.expected_spec(root, 'headservice13a')
                worker = self.materialize.expected_spec(root, 'service13a')
        self.assertEqual(p.head_spec(), head)
        self.assertEqual(p.worker_spec(IP), worker)

    def test_dropped_switches_equal_formal13_rule(self):
        tree = ast.parse((ACTIVE/'materialize_control13.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'expected_spec')
        loops = [n for n in ast.walk(function) if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple)
                 and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.iter.elts)]
        loops.sort(key=lambda n: n.lineno)
        self.assertEqual(tuple(e.value for e in loops[0].iter.elts), p.FORMAL_DROPPED_ENV)

    def test_supplement_shares_actual_formal_head_event_environment(self):
        config = dict(self.g1, execution_purpose='formal-continuation')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'run-manifest.json').write_text(json.dumps(config))
            with patch.object(self.materialize, 'validate_scope', return_value=False):
                formal = self.materialize.expected_spec(root, 'headservice13a')
        plan = p.plan(RUN, HOLDER, 1800, IP, deadline_unix=DEADLINE,
                      supplement_condition='safetensors_disk_cold', supplement_start_sha='a'*64)
        for role in ('head', 'worker'):
            self.assertEqual(plan[role]['spec']['env']['SLLM_EVENT_DIR'], formal['env']['SLLM_EVENT_DIR'])


class DebugOverlay(unittest.TestCase):
    def setUp(self):
        self.plan = p.plan(RUN, HOLDER, 3600, IP, deadline_unix=DEADLINE)

    def test_only_whitelisted_fields_change(self):
        for role, base in (('head', p.head_spec()), ('worker', p.worker_spec(IP))):
            spec = self.plan[role]['spec']
            with self.subTest(role=role):
                self.assertEqual({k: v for k, v in base.items() if k not in ('argv', 'env')},
                                 {k: v for k, v in spec.items() if k not in ('argv', 'env')})
                self.assertEqual({k: v for k, v in base['env'].items() if k not in p.FORMAL_DROPPED_ENV},
                                 {k: v for k, v in spec['env'].items() if k not in p.DEBUG_ENV_ADDED})
                self.assertEqual(spec['argv'][:5], ['/usr/bin/timeout', '--foreground', '--signal=TERM',
                                                    '--kill-after='+p.KILL_AFTER[role], '3600s'])
        self.assertEqual(self.plan['head']['spec']['env'], p.head_spec()['env'])
        self.assertEqual(self.plan['head']['spec']['argv'][5:], p.head_spec()['argv'])
        self.assertEqual(self.plan['worker']['spec']['argv'][5:], ['/experiment/.venv/bin/python', '-u', p.DEBUG_ENTRY])
        self.assertEqual({k: self.plan['worker']['spec']['env'][k] for k in p.DEBUG_ENV_ADDED},
                         dict(SLLM_DEBUG_WEIGHT_FORMAT='safetensors', SLLM_DEBUG_QUERY='1'))

    def test_worker_outlives_store_grace(self):
        budget = (ACTIVE/'budget_control13.py').read_text()
        grace = int(next(line.split('=')[1] for line in budget.splitlines() if line.startswith('STORE_SHUTDOWN_GRACE=')))
        self.assertGreater(int(p.KILL_AFTER['worker'].rstrip('s')), grace)

    def test_weight_format_and_query_guards(self):
        plan = p.plan(RUN, HOLDER, 3600, IP, 'serverless_llm', False)
        self.assertEqual(plan['worker']['spec']['env']['SLLM_DEBUG_WEIGHT_FORMAT'], 'serverless_llm')
        self.assertEqual(plan['worker']['spec']['env']['SLLM_DEBUG_QUERY'], '0')
        for bad in (('fp16', True), ('safetensors', 'yes')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                p.plan(RUN, HOLDER, 3600, IP, *bad)

    def test_docker_args_are_frozen_builder_plus_debug_labels(self):
        source = p.debug_run_root(RUN)/'source'
        for role in ('head', 'worker'):
            args = self.plan[role]['docker_argv']
            base = p.docker_create_args(self.plan[role]['spec'], RUN, p.nonformal_mounts(source, p.debug_run_root(RUN)), p.IMAGE)
            with self.subTest(role=role):
                self.assertEqual(args[:2]+args[6:], base)
                self.assertEqual(args[2:6], ['--pull=never', '--label=sllm.purpose=DEBUG-UNREVIEWED', '--label=sllm.holder='+HOLDER,
                                             '--label=sllm.deadline_unix=%d' % DEADLINE])
                self.assertIn('--name='+RUN+'-'+self.plan[role]['spec']['name'], args)
                argv = self.plan[role]['spec']['argv']
                self.assertEqual(args[-len(argv)-1:-len(argv)+1], ['--entrypoint='+argv[0], p.IMAGE])
        self.assertIn('--runtime=runc', self.plan['head']['docker_argv'])
        self.assertNotIn('--gpus=all', self.plan['head']['docker_argv'])
        self.assertIn('--gpus=all', self.plan['worker']['docker_argv'])

    def test_debug_mounts_differ_only_in_code_tree_and_run_root(self):
        formal_like = p.nonformal_mounts(p.HOST/'code/experiment', p.HOST/'validation/controls/x')
        debug = p.nonformal_mounts(p.debug_run_root(RUN)/'source', p.debug_run_root(RUN))
        changed = [(a['destination'], b['source']) for a, b in zip(formal_like, debug) if a != b]
        self.assertEqual([d for d, _ in changed],
                         ['/experiment/code/experiment', '/experiment/code/Store-source-v7/sllm_store/server.py', '/r'])
        self.assertTrue(all(s.startswith(str(p.debug_run_root(RUN))+'/') or s == str(p.debug_run_root(RUN)) for _, s in changed))
        self.assertTrue(all(m['readonly'] for m in debug if m['destination'] != '/r'))

    def test_no_formal_gate_environment(self):
        gate = {'SLLM_FORMAL_GATE_FILE', 'SLLM_FORMAL_GATE_SHA256', 'SLLM_FORMAL_CODE_COMMIT', 'SLLM_FORMAL_SOURCE_MANIFEST_SHA256'}
        for role in ('head', 'worker'):
            self.assertFalse(gate & set(self.plan[role]['spec']['env']))

    def test_identity_holder_and_lifetime_guards(self):
        for bad in ('sllm-glm51fp8-20261028T120000Z-0123abcd', 'sllm-debug-x'):
            with self.subTest(run=bad), self.assertRaises(ValueError):
                p.plan(bad, HOLDER, 3600, IP)
        with self.assertRaises(ValueError):
            p.plan(RUN, 'not-a-holder', 3600, IP)
        for seconds in (299, 7201, 3600.0):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                p.plan(RUN, HOLDER, seconds, IP)

    def test_guard_deadline_label(self):
        for bad in (0, -1, 1793200000.0, '1793200000'):
            with self.subTest(deadline=bad), self.assertRaises(ValueError):
                p.plan(RUN, HOLDER, 3600, IP, deadline_unix=bad)
        before = int(p.time.time())
        default = p.plan(RUN, HOLDER, 3600, IP)['deadline_unix']
        self.assertTrue(before+3600+p.DEADLINE_GRACE <= default <= int(p.time.time())+3600+p.DEADLINE_GRACE)
        self.assertGreater(p.DEADLINE_GRACE, int(p.KILL_AFTER['worker'].rstrip('s')))


if __name__ == '__main__':
    unittest.main()
