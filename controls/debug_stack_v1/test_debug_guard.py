import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import debug_guard as g
import debug_stack as d

RUN = 'sllm-debug-20261028T120000Z-0123abcd'
NOW = 1793200000


def container(labels, created='2026-10-28T12:00:00.123456789Z', running=True, cid='c'*64):
    return dict(Id=cid, Name='/'+RUN+'-headservice13a', Created=created, Config=dict(Labels=labels),
                State=dict(Running=running, Status='running' if running else 'exited'))


def debug_labels(**extra):
    return dict({'sllm.purpose': 'DEBUG-UNREVIEWED', 'sllm.run_id': RUN}, **extra)


class Due(unittest.TestCase):
    def test_deadline_label_decides(self):
        self.assertEqual(g.due(container(debug_labels(**{'sllm.deadline_unix': str(NOW)})), NOW), (True, 'deadline label'))
        self.assertFalse(g.due(container(debug_labels(**{'sllm.deadline_unix': str(NOW+1)})), NOW)[0])

    def test_unlabelled_debug_containers_fall_back_to_maximum_age(self):
        created = g.created_unix('2026-10-28T12:00:00.5Z')
        self.assertFalse(g.due(container(debug_labels(), '2026-10-28T12:00:00.5Z'), created+g.MAX_AGE_SECONDS-1)[0])
        self.assertTrue(g.due(container(debug_labels(), '2026-10-28T12:00:00.5Z'), created+g.MAX_AGE_SECONDS)[0])

    def test_formal_and_foreign_containers_are_never_due(self):
        for labels in ({'sllm.purpose': 'DEBUG-UNREVIEWED', 'sllm.run_id': 'sllm-glm51fp8-20261027T011726Z-28c92313'},
                       {'sllm.run_id': RUN}, {}, {'sllm.purpose': 'DEBUG-UNREVIEWED', 'sllm.run_id': 'miles-alan'}):
            with self.subTest(labels=labels):
                self.assertFalse(g.due(container(dict(labels, **{'sllm.deadline_unix': '1'})), NOW)[0])


class Sweep(unittest.TestCase):
    def run_sweep(self, rows, dry_run, ps_returncode=0):
        self.calls = []

        def docker(*args, timeout=60):
            self.calls.append(args)
            if args[0] == 'ps':
                return types.SimpleNamespace(returncode=ps_returncode, stdout='\n'.join(r['Id'] for r in rows), stderr='cannot connect')
            if args[0] == 'inspect':
                return types.SimpleNamespace(returncode=0, stdout=json.dumps([next(r for r in rows if r['Id'] == args[1])]))
            return types.SimpleNamespace(returncode=0, stdout='')
        log, self.heartbeat = Path(self.id()+'.jsonl'), Path(self.id()+'.heartbeat.json')
        for path in (log, self.heartbeat):
            self.addCleanup(path.unlink, missing_ok=True)
        with patch.object(g, 'docker', side_effect=docker), patch.object(g, 'LOG', log), patch.object(g, 'HEARTBEAT', self.heartbeat):
            actions = g.sweep(dry_run, NOW)
        return actions, self.mutations()

    def mutations(self):
        return [c[0] for c in self.calls if c[0] in ('stop', 'rm')]

    def test_expired_running_container_is_stopped_then_removed(self):
        rows = [container(debug_labels(**{'sllm.deadline_unix': str(NOW-1)})),
                container(debug_labels(**{'sllm.deadline_unix': str(NOW+60)}), cid='d'*64)]
        actions, mutations = self.run_sweep(rows, dry_run=False)
        self.assertEqual(([a['container_id'] for a in actions], mutations), (['c'*64], ['stop', 'rm']))
        self.assertEqual(json.loads(self.heartbeat.read_text())['actions'], 1)

    def test_quiet_sweep_still_writes_heartbeat(self):
        self.run_sweep([], dry_run=False)
        self.assertEqual(json.loads(self.heartbeat.read_text())['actions'], 0)

    def test_dry_run_changes_nothing(self):
        actions, mutations = self.run_sweep([container(debug_labels(**{'sllm.deadline_unix': str(NOW-1)}))], dry_run=True)
        self.assertEqual((len(actions), mutations, self.heartbeat.exists()), (1, [], False))

    def test_unreachable_docker_fails_without_heartbeat(self):
        with self.assertRaises(RuntimeError):
            self.run_sweep([container(debug_labels(**{'sllm.deadline_unix': str(NOW-1)}))], dry_run=False, ps_returncode=1)
        self.assertEqual((self.mutations(), self.heartbeat.exists()), ([], False))


class Requirement(unittest.TestCase):
    """GPU runs need an active timer that runs exactly this version of the guard, root-owned and not writable,
    and a heartbeat showing that it completed a sweep recently."""
    def stat(self, uid=0, mode=0o100644):
        return types.SimpleNamespace(st_uid=uid, st_mode=mode)

    def problems(self, **change):
        facts = dict(timer_state='active', exec_start='{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I %s }' % d.GUARD_SCRIPT,
                     script_stat=self.stat(), parent_stat=self.stat(mode=0o40755), script_sha256='a'*64, expected_sha256='a'*64,
                     heartbeat_stat=self.stat(), heartbeat_age=30.0)
        facts.update(change)
        return d.guard_problems(**facts)

    def test_installed_guard_passes(self):
        self.assertEqual(self.problems(), [])

    def test_each_defect_is_reported(self):
        for change in (dict(timer_state='inactive'), dict(timer_state=''), dict(exec_start='/bin/true'),
                       dict(script_stat=None), dict(script_stat=self.stat(uid=2147)), dict(script_stat=self.stat(mode=0o100666)),
                       dict(parent_stat=self.stat(mode=0o40777)), dict(script_sha256='b'*64),
                       dict(heartbeat_stat=None), dict(heartbeat_stat=self.stat(uid=2147)), dict(heartbeat_age=None),
                       dict(heartbeat_age=d.GUARD_FRESH_SECONDS+1), dict(heartbeat_age=-1)):
            with self.subTest(change=change):
                self.assertEqual(len(self.problems(**change)), 1)


class Configuration(unittest.TestCase):
    def config(self, weight_format, query=True, gpu=True):
        restored = json.dumps(dict(pool_bytes=768799145984, chunk_bytes=536870912))
        with patch.object(d.pathlib.Path, 'read_text', return_value=restored):
            return d.configuration(weight_format, query, gpu)

    def test_debug_configurations_are_never_formal_equivalent(self):
        for weight_format in ('safetensors', 'serverless_llm'):
            with self.subTest(weight_format=weight_format):
                self.assertFalse(self.config(weight_format)['comparable_to_formal_measurements'])
        safetensors = self.config('safetensors')
        self.assertEqual(safetensors['pool_bytes'], 67108864)
        self.assertTrue(any('Store pool: 67108864 bytes instead of the 768799145984-byte pool' in s for s in safetensors['differs_from_formal']))
        self.assertTrue(any('created by this run' in s for s in self.config('serverless_llm')['differs_from_formal']))
        self.assertTrue(any('worker entry' in s for s in safetensors['differs_from_formal']))
        self.assertIsNone(self.config('safetensors', gpu=False)['pool_bytes'])


if __name__ == '__main__':
    unittest.main()
