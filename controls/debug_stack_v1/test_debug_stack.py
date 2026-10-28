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
import debug_stack as d

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
    def test_formal_identity_never_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            active, run_root = Path(directory)/'active', Path(directory)/'run'
            active.mkdir(); run_root.mkdir()
            (active/'run-manifest.json').write_text('{"run_id": "sllm-glm51fp8-x"}')
            (active/'workspace.py').write_text('x = 1\n')
            with patch.object(d, 'ACTIVE', active):
                rows = d.deploy_source(run_root)
            self.assertEqual(sorted(p.name for p in (run_root/'source').iterdir()), ['debug_service.py', 'workspace.py'])
            self.assertEqual([r['path'] for r in rows], ['workspace.py', 'debug_service.py'])


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


if __name__ == '__main__':
    unittest.main()
