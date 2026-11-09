import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
