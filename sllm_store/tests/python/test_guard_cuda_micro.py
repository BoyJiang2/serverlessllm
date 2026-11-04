"""Regression checks extract guard code without executing its root-only entry point."""
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch

TREE = ast.parse((Path(__file__).resolve().parents[1] / "guard_cuda_micro.py").read_text())
MAIN = next(node for node in TREE.body if isinstance(node, ast.Try))


def execute(nodes, namespace):
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "guard-extracted", "exec"), namespace)


@unittest.skipUnless(hasattr(os, "O_PATH"), "Linux directory-fd checks")
class GuardCudaMicroTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        root = base / "cgroup"
        scope = root / "system.slice" / ("docker-" + "c" * 64 + ".scope")
        scope.mkdir(parents=True)
        (scope / "cgroup.events").write_text("populated 1\nfrozen 0\n")
        (scope / "cgroup.kill").touch()
        oci = base / "config.json"
        expected = "system.slice:docker:" + "c" * 64
        oci.write_text(json.dumps({"linux": {"cgroupsPath": expected}}))
        self.ns = dict(os=os, pathlib=__import__("pathlib"), json=json, hashlib=hashlib, time=time,
                       base=base, oci=oci, cgroot=root, scope=scope, expected_cg=expected,
                       scopefd=None, oci_seen=False, scope_seen=False, raw_sha=None, last={}, emit=lambda *args, **kwargs: None)
        execute([n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in ("read", "atomic", "snapshot", "kill_scope")], self.ns)
        self.addCleanup(self.close_scope)

    def close_scope(self):
        if self.ns["scopefd"] is not None:
            os.close(self.ns["scopefd"])
            self.ns["scopefd"] = None

    def replace_scope(self):
        scope = self.ns["scope"]
        scope.rename(scope.with_name("old-generation"))
        scope.mkdir()
        (scope / "cgroup.events").write_text("populated 1\nfrozen 0\n")
        (scope / "cgroup.kill").touch()

    def test_rebuilt_scope_is_reauthenticated_before_kill(self):
        self.ns["snapshot"]()
        old_inode = os.fstat(self.ns["scopefd"]).st_ino
        self.replace_scope()
        self.ns["snapshot"]()
        self.assertNotEqual(os.fstat(self.ns["scopefd"]).st_ino, old_inode)
        self.ns["kill_scope"]()
        self.assertEqual((self.ns["scope"] / "cgroup.kill").read_text(), "1\n")
        self.assertEqual((self.ns["scope"].with_name("old-generation") / "cgroup.kill").read_text(), "")

    def test_rebuilt_scope_without_fresh_oci_is_rejected(self):
        self.ns["snapshot"]()
        self.replace_scope()
        self.ns["oci"].unlink()
        with self.assertRaisesRegex(ValueError, "fresh OCI"):
            self.ns["snapshot"]()
        self.assertEqual((self.ns["scope"] / "cgroup.kill").read_text(), "")

    def test_ancestor_current_churn_is_bounded_but_limits_and_scope_changes_are_kept(self):
        captured = []
        self.ns["emit"] = lambda kind, **data: captured.append((kind, data))
        self.ns["time"] = types.SimpleNamespace(monotonic=lambda: 100.0)
        parent = self.ns["scope"].parent
        (parent / "pids.events").write_text("max 0")
        for value in range(200):
            (parent / "pids.current").write_text(str(value))
            self.ns["snapshot"]()
        self.assertEqual(len([r for r in captured if r[0] == "cgroups"]), 1)
        (parent / "pids.events").write_text("max 1")
        self.ns["snapshot"]()
        (self.ns["scope"] / "pids.current").write_text("2")
        self.ns["snapshot"]()
        groups = [r[1]["selected"] for r in captured if r[0] == "cgroups"]
        self.assertEqual(len(groups), 3)
        self.assertNotIn("cgroup.procs", groups[-1][str(parent)])
        self.ns["time"] = types.SimpleNamespace(monotonic=lambda: 106.0)
        self.ns["snapshot"]()
        self.assertEqual(len([r for r in captured if r[0] == "cgroups"]), 4)

    def test_ack_directory_fsync_error_stays_in_cleanup_path(self):
        ack = next(n for n in MAIN.body if isinstance(n, ast.Try) and any(isinstance(x, ast.Constant) and x.value == "GO-ACK.json" for x in ast.walk(n)))
        self.ns.update(a=types.SimpleNamespace(cid="c" * 64), go_at=1.0, failures=set(), abort_at=None)
        with patch.object(os, "fsync", side_effect=[None, OSError("injected directory fsync failure")]):
            execute([ack], self.ns)
        self.assertTrue((self.ns["base"] / "GO-ACK.json").exists())
        self.assertIn("GO_ACK_publish", self.ns["failures"])
        self.assertIsNotNone(self.ns["abort_at"])

    def test_first_quiet_observation_after_cleanup_cutoff_fails(self):
        loop = next(n for n in MAIN.body if isinstance(n, ast.While) and "a.overall" in ast.unparse(n.test))
        triggered = next(n for n in loop.body if isinstance(n, ast.If) and ast.unparse(n.test) == "now >= trigger")
        cutoff = next(n for n in triggered.body if isinstance(n, ast.If) and "quiet is None" in ast.unparse(n.test))
        quiet = next(n for n in triggered.body if isinstance(n, ast.If) and ast.unparse(n.test) == "not live and empty")
        # The first cutoff check must precede the first assignment to quiet.
        body = triggered.body
        execute(body[body.index(cutoff):body.index(quiet)], dict(self.ns, quiet=None, now=66.0, go_at=0.0,
                a=types.SimpleNamespace(overall=65), failures=(failures := set())))
        self.assertIn("cleanup_exceeded_GO65", failures)


class BudgetAndObserverTest(unittest.TestCase):
    def test_budget_defaults_new_and_invalid(self):
        import math
        nodes = [n for n in TREE.body if isinstance(n, ast.Assert) and 'a.deadline' in ast.unparse(n)]
        self.assertEqual(len(nodes), 2)
        for deadline, kill, overall, valid in [(25,3,65,True),(60,3,100,True),
                (float('nan'),3,100,False),(60,3,float('inf'),False),
                (0,3,40,False),(60,2,100,False),(60,3,63,False),(60,3,101,False)]:
            ns = dict(a=types.SimpleNamespace(deadline=deadline,kill_after=kill,overall=overall),math=math)
            if valid: execute(nodes,ns)
            else:
                with self.assertRaises(AssertionError):execute(nodes,ns)

    def observer_namespace(self, base):
        import math
        import re
        import xml.etree.ElementTree as ET
        tree = ast.parse((Path(__file__).resolve().parents[1]/'s4a_root_controller.py').read_text())
        ns = dict(BASE=base,UUID='GPU-'+'0'*36,pathlib=__import__('pathlib'),math=math,
                  re=re,ET=ET,os=os,json=json,time=time)
        execute([n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in
                 ('save','gpu_states','observer')],ns)
        return ns

    def test_missing_or_duplicate_gpu_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            ns=self.observer_namespace(Path(d))
            uuids=['GPU-'+str(i)*36 for i in range(8)]
            ns['call']=lambda argv:'<nvidia_smi_log>'+''.join(
                '<gpu><uuid>'+u+'</uuid><fb_memory_usage><used>0 MiB</used></fb_memory_usage></gpu>'
                for u in uuids[:-1])+'</nvidia_smi_log>'
            with self.assertRaises(AssertionError):ns['gpu_states'](uuids)
            with self.assertRaises(AssertionError):ns['gpu_states']([uuids[0]]*8)

    def test_eight_gpu_joint_quiet_and_peaks(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);(base/'normal-exit.json').touch()
            ns=self.observer_namespace(base)
            clock=types.SimpleNamespace(now=100.0)
            ns['time']=types.SimpleNamespace(monotonic=lambda:clock.now,
                sleep=lambda seconds:setattr(clock,'now',clock.now+seconds))
            uuids=['GPU-'+str(i)*36 for i in range(8)]
            def query(argv):
                return '<nvidia_smi_log>'+''.join('<gpu><uuid>'+u+
                    '</uuid><fb_memory_usage><used>'+str(10 if i==7 and 110<=clock.now<120 else 0)+
                    ' MiB</used></fb_memory_usage><processes/></gpu>' for i,u in enumerate(uuids))+'</nvidia_smi_log>'
            ns['call']=query
            ns['observer'](base,uuids,175,2048)
            result=json.loads((base/'observer-final.json').read_text())
            self.assertEqual(result['quiet_seconds'],60)
            self.assertGreaterEqual(clock.now,180)
            self.assertEqual(result['peak_mib_by_uuid'][uuids[7]],10)
            self.assertEqual(len(result['peak_mib_by_uuid']),8)

    def test_single_schema_and_card_limit_abort(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);(base/'normal-exit.json').touch()
            ns=self.observer_namespace(base);clock=types.SimpleNamespace(now=100.0)
            ns['time']=types.SimpleNamespace(monotonic=lambda:clock.now,
                sleep=lambda seconds:setattr(clock,'now',clock.now+seconds))
            ns['call']=lambda argv:'<nvidia_smi_log><gpu><uuid>'+ns['UUID']+'</uuid><fb_memory_usage><used>0 MiB</used></fb_memory_usage><processes/></gpu></nvidia_smi_log>'
            ns['observer']()
            result=json.loads((base/'observer-final.json').read_text())
            self.assertEqual(set(result),{'gpu_uuid','quiet_seconds','peak_mib'})
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);ns=self.observer_namespace(base);calls=[]
            def query(argv):
                calls.append(1)
                return '<nvidia_smi_log><gpu><uuid>'+ns['UUID']+'</uuid><fb_memory_usage><used>'+('0' if len(calls)==1 else '2049')+' MiB</used></fb_memory_usage></gpu></nvidia_smi_log>'
            ns['call']=query
            with self.assertRaises(AssertionError):ns['observer']()
            self.assertTrue((base/'ABORT').exists())


if __name__ == "__main__":
    unittest.main()
