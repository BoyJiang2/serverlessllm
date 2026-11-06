"""Regression checks extract guard code without executing its root-only entry point."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
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
                a=types.SimpleNamespace(overall=65,normal_exit_marker=False), cleanup_cutoff=65, normal_trigger=None, failures=(failures := set())))
        self.assertIn("cleanup_exceeded_GO65", failures)


class BudgetAndObserverTest(unittest.TestCase):
    def test_budget_defaults_new_and_invalid(self):
        import math
        nodes = [n for n in TREE.body if isinstance(n, ast.Assert) and 'a.deadline' in ast.unparse(n)]
        self.assertEqual(len(nodes), 2)
        for deadline, kill, overall, valid in [(25,3,65,True),(60,3,100,True),(1800,3,1840,True),
                (float('nan'),3,100,False),(60,3,float('inf'),False),
                (0,3,40,False),(60,2,100,False),(60,3,63,False),(60,3,101,False)]:
            ns = dict(a=types.SimpleNamespace(deadline=deadline,kill_after=kill,overall=overall),math=math)
            if valid: execute(nodes,ns)
            else:
                with self.assertRaises(AssertionError):execute(nodes,ns)

    def observer_namespace(self, base):
        import math
        import re
        import subprocess
        tree = ast.parse((Path(__file__).resolve().parents[1]/'s4a_root_controller.py').read_text())
        ns = dict(BASE=base,UUID='GPU-'+'0'*36,pathlib=__import__('pathlib'),math=math,
                  re=re,os=os,json=json,time=time,subprocess=subprocess)
        wanted = ('NVIDIA_SMI','GPU_QUERY_TIMEOUT','MAX_CONSECUTIVE_GPU_QUERY_FAILURES','SAMPLE_INTERVAL_SECONDS')
        constants = [n for n in tree.body if isinstance(n,ast.Assign)
                     and any(getattr(t,'id',None) in wanted for t in n.targets)]
        execute(constants + [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in
                 ('save','gpu_states','observer')],ns)
        return ns

    def test_missing_or_duplicate_gpu_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            ns=self.observer_namespace(Path(d))
            uuids=['GPU-'+str(i)*36 for i in range(8)]
            ns['call']=lambda argv, timeout=None:'\n'.join(u+', 0' for u in uuids[:-1])
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
            def query(argv, timeout=None):
                if '--query-gpu=uuid,memory.used' not in argv: return ''
                return '\n'.join(u+', '+str(10 if i==7 and 110<=clock.now<120 else 0)
                                 for i,u in enumerate(uuids))
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
            ns['call']=lambda argv, timeout=None:(ns['UUID']+', 0'
                if '--query-gpu=uuid,memory.used' in argv else '')
            ns['observer']()
            result=json.loads((base/'observer-final.json').read_text())
            self.assertEqual(set(result),{'gpu_uuid','quiet_seconds','peak_mib','monitor'})
            self.assertEqual(result['monitor']['peak_kind'],'SAMPLED_LOWER_BOUND')
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);ns=self.observer_namespace(base);calls=[]
            def query(argv, timeout=None):
                if '--query-gpu=uuid,memory.used' not in argv: return ''
                calls.append(1)
                return ns['UUID']+', '+('0' if len(calls)==1 else '2049')
            ns['call']=query
            with self.assertRaises(AssertionError):ns['observer']()
            self.assertTrue((base/'ABORT').exists())

    def test_slow_query_is_a_gap_not_a_failure(self):
        # The observer used to abort the whole run when one nvidia-smi query
        # exceeded the call cap. Missing evidence must be recorded as a gap and
        # must restart the quiet window instead of failing the experiment.
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);(base/'normal-exit.json').touch()
            ns=self.observer_namespace(base);clock=types.SimpleNamespace(now=100.0)
            ns['time']=types.SimpleNamespace(monotonic=lambda:clock.now,
                sleep=lambda seconds:setattr(clock,'now',clock.now+seconds))
            state={'n':0}
            def query(argv, timeout=None):
                if '--query-gpu=uuid,memory.used' not in argv: return ''
                state['n']+=1
                if state['n']==3:
                    clock.now+=4.0
                    raise subprocess.TimeoutExpired(argv, timeout or 0)
                return ns['UUID']+', 0'
            ns['call']=query
            ns['observer'](base,[ns['UUID']],175,2048)
            self.assertFalse((base/'ABORT').exists())
            self.assertEqual(json.loads((base/'observer-final.json').read_text())['quiet_seconds'],60)
            lines=[json.loads(line) for line in (base/'gpu-observer.jsonl').read_text().splitlines()]
            gaps=[line for line in lines if 'sample_error' in line]
            self.assertEqual(len(gaps),1)
            self.assertEqual(gaps[0]['sample_error']['consecutive'],1)
            monitor=json.loads((base/'observer-final.json').read_text())['monitor']
            self.assertEqual(lines[-1]['monitor_summary'],monitor)
            self.assertEqual(monitor['gaps'],1)
            self.assertEqual(monitor['peak_coverage'],'SAMPLED_WITH_QUERY_GAPS')
            self.assertGreaterEqual(monitor['max_query_seconds'],4)
            self.assertGreaterEqual(monitor['max_memory_interval_seconds'],4)

    def test_sustained_monitor_blackout_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);(base/'normal-exit.json').touch()
            ns=self.observer_namespace(base);state={'n':0}
            def query(argv, timeout=None):
                if '--query-gpu=uuid,memory.used' not in argv: return ''
                state['n']+=1
                if state['n']>1: raise subprocess.TimeoutExpired(argv, timeout or 0)
                return ns['UUID']+', 0'
            ns['call']=query
            with self.assertRaises(AssertionError):ns['observer'](base,[ns['UUID']],175,2048)
            self.assertTrue((base/'ABORT').exists())

    def test_returned_duplicate_and_negative_memory_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            ns=self.observer_namespace(Path(d));u=ns['UUID']
            for memory in (u+', 2049\n'+u+', 0',u+', -1',u+', nan'):
                with self.subTest(memory=memory):
                    ns['call']=lambda argv,timeout=None:memory
                    with self.assertRaises((AssertionError,ValueError)):ns['gpu_states']([u])

    def test_over_limit_memory_cannot_be_hidden_by_apps_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);ns=self.observer_namespace(base);count={'memory':0,'apps':0}
            def query(argv,timeout=None):
                if '--query-gpu=uuid,memory.used' in argv:
                    count['memory']+=1
                    return ns['UUID']+', '+('0' if count['memory']==1 else '2049')
                count['apps']+=1
                if count['apps']>1:raise subprocess.TimeoutExpired(argv,timeout)
                return ''
            ns['call']=query
            with self.assertRaisesRegex(AssertionError,'memory exceeds limit'):ns['observer']()
            self.assertEqual(count['apps'],1)
            self.assertTrue((base/'ABORT').exists())
            rows=[json.loads(s) for s in (base/'gpu-observer.jsonl').read_text().splitlines()]
            self.assertEqual(rows[-1]['memory_mib_by_uuid'][ns['UUID']],2049)

    def test_partial_memory_is_retained_and_apps_gap_resets_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);(base/'normal-exit.json').touch();ns=self.observer_namespace(base)
            clock=types.SimpleNamespace(now=100.0);count={'memory':0,'apps':0}
            ns['time']=types.SimpleNamespace(monotonic=lambda:clock.now,
                sleep=lambda s:setattr(clock,'now',clock.now+s))
            def query(argv,timeout=None):
                if '--query-gpu=uuid,memory.used' in argv:
                    count['memory']+=1
                    return ns['UUID']+', '+('17' if count['memory']==3 else '0')
                count['apps']+=1
                if count['apps']==3:
                    clock.now+=4
                    raise subprocess.TimeoutExpired(argv,timeout)
                return ''
            ns['call']=query;ns['observer']()
            row=json.loads((base/'observer-final.json').read_text())
            self.assertEqual(row['peak_mib'],17)
            self.assertEqual(row['monitor']['gaps'],1)
            self.assertGreater(row['monitor']['memory_samples'],row['monitor']['samples'])
            self.assertGreaterEqual(clock.now,165)

    def test_slow_success_is_reported_and_late_query_cannot_pass(self):
        for late in (False,True):
            with self.subTest(late=late),tempfile.TemporaryDirectory() as d:
                base=Path(d);(base/'normal-exit.json').touch();ns=self.observer_namespace(base)
                clock=types.SimpleNamespace(now=100.0);count={'memory':0}
                ns['time']=types.SimpleNamespace(monotonic=lambda:clock.now,
                    sleep=lambda s:setattr(clock,'now',clock.now+s))
                def query(argv,timeout=None):
                    if '--query-gpu=uuid,memory.used' not in argv:return ''
                    count['memory']+=1
                    if count['memory']==2:clock.now+=180 if late else 10
                    return ns['UUID']+', 0'
                ns['call']=query
                if late:
                    with self.assertRaisesRegex(AssertionError,'exceeded deadline'):ns['observer']()
                    self.assertFalse((base/'observer-final.json').exists())
                    self.assertTrue((base/'ABORT').exists())
                else:
                    ns['observer']()
                    row=json.loads((base/'observer-final.json').read_text())['monitor']
                    self.assertEqual(row['gaps'],0)
                    self.assertEqual(row['max_query_seconds'],10)
                    self.assertGreaterEqual(row['max_memory_interval_seconds'],10)
                    self.assertEqual(row['peak_kind'],'SAMPLED_LOWER_BOUND')


class NormalCompletionTest(unittest.TestCase):
    def validator(self, base, owner=0):
        import math, stat
        ns = dict(os=os,stat=stat,json=json,math=math)
        execute([n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='normal_completion'],ns)
        original = os.fstat
        def ownership(fd):
            row=original(fd)
            return types.SimpleNamespace(st_mode=row.st_mode,st_uid=owner,st_nlink=row.st_nlink,st_size=row.st_size)
        return ns['normal_completion'], ownership

    def marker(self, base, **changes):
        row=dict(cid='c'*64,nonce='nonce',go_monotonic=10.0,completion_monotonic=20.0,exit_code=0)
        row.update(changes);path=base/'normal-exit.json';path.write_text(json.dumps(row));path.chmod(0o600)
        return path

    def test_valid_and_spoofed_or_invalid_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);validate,ownership=self.validator(base)
            path=self.marker(base)
            with patch.object(os,'fstat',side_effect=ownership):
                self.assertEqual(validate(path,'c'*64,'nonce',10.0,30.0,100),20.0)
            for change in [dict(cid='d'*64),dict(nonce='wrong'),dict(go_monotonic=9.0),dict(go_monotonic=True),
                           dict(completion_monotonic=float('nan')),dict(completion_monotonic=float('inf')),
                           dict(completion_monotonic=31.0),dict(completion_monotonic=9.0),dict(completion_monotonic='20'),
                           dict(exit_code=True),dict(exit_code=1)]:
                path=self.marker(base,**change)
                with self.subTest(change=change),patch.object(os,'fstat',side_effect=ownership),self.assertRaises(ValueError):
                    validate(path,'c'*64,'nonce',10.0,30.0,100)

    def test_marker_permissions_owner_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);validate,ownership=self.validator(base);path=self.marker(base)
            path.chmod(0o644)
            with patch.object(os,'fstat',side_effect=ownership),self.assertRaises(ValueError):validate(path,'c'*64,'nonce',10,30,100)
            path.chmod(0o600);_,wrong_owner=self.validator(base,owner=123)
            with patch.object(os,'fstat',side_effect=wrong_owner),self.assertRaises(ValueError):validate(path,'c'*64,'nonce',10,30,100)
            os.link(path,base/'alias')
            with patch.object(os,'fstat',side_effect=ownership),self.assertRaises(ValueError):validate(path,'c'*64,'nonce',10,30,100)
            (base/'alias').unlink();path.rename(base/'real');path.symlink_to(base/'real')
            with patch.object(os,'fstat',side_effect=ownership),self.assertRaises(OSError):validate(path,'c'*64,'nonce',10,30,100)
            path.unlink();os.mkfifo(path,0o600)
            with patch.object(os,'fstat',side_effect=ownership),self.assertRaises(ValueError):validate(path,'c'*64,'nonce',10,30,100)

    def loop(self, base, enabled=True, invalid=False, live_until=0, empty_after=0, foreign=False, aborted=False, reappear=None, scan_failure=False, abort_publish_failure=False):
        import signal
        clock=types.SimpleNamespace(now=1.0);signals=[];kills=[];events=[]
        (base/'normal-exit.json').touch()
        if aborted:(base/'ABORT').touch()
        def scan():
            if scan_failure:ns['failures'].add('proc_discovery')
            return [dict(fd=999,row={'pid':999},term=False,kill=False)] if clock.now<live_until else []
        def snapshot():
            if foreign:raise ValueError('foreign scope generation without fresh OCI')
            return clock.now>=empty_after and not (reappear is not None and reappear[0]<=clock.now<reappear[1])
        def validate(*args):
            if invalid:raise ValueError('invalid marker')
            return 1.0
        def kill():
            kills.append(clock.now)
            if foreign:raise ValueError('scope inode mismatch')
        def atomic(name,payload):
            if abort_publish_failure:raise RuntimeError('CPU marker publication failed')
            (base/name).write_bytes(payload)
        ns=dict(time=types.SimpleNamespace(monotonic=lambda:clock.now,sleep=lambda s:setattr(clock,'now',clock.now+s)),
                a=types.SimpleNamespace(normal_exit_marker=enabled,deadline=25.0,overall=65.0,kill_after=3,cid='c'*64),
                base=base,go_at=0.0,normal_at=None,normal_trigger=None,quiet=None,abort_at=None,nonce='nonce',
                failures=set(),pins={},scan=scan,snapshot=snapshot,normal_completion=validate,kill_scope=kill,
                signal=signal,os=os,atomic=atomic,max_cycle=0.0,oci_seen=True,scope_seen=True,raw_sha='hash',
                emit=lambda kind,**data:events.append(kind))
        loop=next(n for n in MAIN.body if isinstance(n,ast.While) and 'a.overall' in ast.unparse(n.test))
        with patch.object(signal,'pidfd_send_signal',side_effect=lambda *args:signals.append(args)):execute([loop],ns)
        ns.update(clock=clock,signals=signals,kills=kills,events=events)
        return ns

    def test_normal_release_starts_after_actual_death_not_marker_time(self):
        with tempfile.TemporaryDirectory() as d:
            ns=self.loop(Path(d),live_until=5,empty_after=7)
            self.assertFalse(ns['failures']);self.assertGreaterEqual(ns['normal_trigger'],7)
            self.assertGreaterEqual(ns['clock'].now-ns['quiet'],60)
            self.assertGreaterEqual(ns['clock'].now,67)
            self.assertFalse(ns['signals']);self.assertFalse(ns['kills'])

    def test_reappearing_owned_resources_reset_quiet_and_keep_cleanup_margin(self):
        with tempfile.TemporaryDirectory() as d:
            ns=self.loop(Path(d),reappear=(10,12))
            self.assertFalse(ns['failures']);self.assertTrue(ns['kills'])
            self.assertGreaterEqual(ns['quiet'],12)
            self.assertGreaterEqual(ns['clock'].now-ns['quiet'],60)
        with tempfile.TemporaryDirectory() as d:
            ns=self.loop(Path(d),reappear=(50,52))
            self.assertIn('normal_cleanup_exceeded',ns['failures'])

    def test_default_off_keeps_deadline_even_with_marker(self):
        with tempfile.TemporaryDirectory() as d:
            ns=self.loop(Path(d),enabled=False)
            self.assertIsNone(ns['normal_at']);self.assertIsNone(ns['normal_trigger'])
            self.assertGreaterEqual(ns['quiet'],25)
            self.assertGreaterEqual(ns['clock'].now,85)
            self.assertNotIn('normal_completion_authenticated',ns['events'])

    def test_invalid_abort_and_foreign_scope_never_become_normal_pass(self):
        for kwargs,expected in [(dict(invalid=True),'normal_marker_invalid'),(dict(aborted=True),'abort_requested'),(dict(foreign=True),'capture')]:
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as d:
                ns=self.loop(Path(d),**kwargs)
                self.assertIn(expected,ns['failures']);self.assertIsNone(ns['normal_trigger'])
                if kwargs.get('foreign'):self.assertIsNone(ns['quiet']);self.assertIn('scope_kill',ns['failures'])


class ProcessDiscoveryTest(unittest.TestCase):
    def namespace(self, normal=True, uid='0', denied=False, exe='/usr/bin/runc'):
        calls=[];events=[];cid='c'*64
        def read(path):
            calls.append(('read',str(path)))
            if path.name=='status':return ('Uid: '+(' '.join([uid]*4))+'\n').encode()
            if path.name=='cmdline':
                if exe=='/usr/bin/containerd-shim-runc-v2':return b'/usr/bin/containerd-shim-runc-v2\0-namespace\0moby\0-id\0'+cid.encode()+b'\0'
                return b'/usr/bin/runc\0--root\0/run/containerd/runc/moby\0create\0'+cid.encode()+b'\0'
            if path.name=='cgroup':return b'0::/system.slice/runtime.scope\n'
            raise AssertionError(path)
        def readlink(path):
            calls.append(('readlink',str(path)))
            if denied:raise PermissionError(13,'CPU fixture denied',str(path))
            return exe
        ns=dict(pathlib=__import__('pathlib'),os=types.SimpleNamespace(readlink=readlink,fsdecode=os.fsdecode),
                a=types.SimpleNamespace(normal_exit_marker=normal,cid=cid),read=read,runc_root=Path('/run/containerd/runc/moby').resolve(),
                startticks=lambda pid:77,pins={},failures=set(),select=types.SimpleNamespace(select=lambda *args:([],[],[])),
                emit=lambda kind,**data:events.append((kind,data)))
        execute([node for node in TREE.body if isinstance(node,ast.FunctionDef) and node.name in ('paired','identity','scan')],ns)
        return ns,calls,events

    def test_normal_nonroot_is_filtered_before_unreadable_exe(self):
        ns,calls,_=self.namespace(uid='2147',denied=True)
        self.assertIsNone(ns['identity'](123));self.assertEqual(calls,[('read','/proc/123/status')])
        ns,calls,_=self.namespace(normal=False,uid='2147',denied=True)
        with self.assertRaises(PermissionError):ns['identity'](123)
        self.assertEqual(calls,[('readlink','/proc/123/exe')])

    def test_root_permission_failure_is_retained_with_process_context(self):
        ns,_,events=self.namespace(denied=True)
        with patch.object(Path,'iterdir',return_value=iter([Path('/proc/123')])):self.assertEqual(ns['scan'](),[])
        self.assertEqual(ns['failures'],{'proc_discovery'})
        kind,row=events[0];self.assertEqual(kind,'proc_discovery_error')
        self.assertEqual((row['pid'],row['stage'],row['errno'],row['filename']),(123,'identity',13,'/proc/123/exe'))

    def test_readable_root_identity_is_same_as_legacy_and_foreign_is_rejected(self):
        for exe in ('/usr/bin/runc','/usr/bin/containerd-shim-runc-v2'):
            old,_,_=self.namespace(normal=False,exe=exe);new,_,_=self.namespace(exe=exe)
            self.assertEqual(old['identity'](123),new['identity'](123))
            self.assertEqual(new['identity'](123),{'pid':123,'startticks':77,'exe':exe,'cgroup':'0::/system.slice/runtime.scope'})
        foreign,_,_=self.namespace(exe='/usr/bin/python3');self.assertIsNone(foreign['identity'](123))

    def test_owned_runtime_uid_is_rechecked_after_precheck(self):
        ns,calls,_=self.namespace();original=ns['read'];statuses=[b'Uid: 0 0 0 0\n',b'Uid: 2147 2147 2147 2147\n']
        def changing(path):return statuses.pop(0) if path.name=='status' else original(path)
        ns['read']=changing;self.assertIsNone(ns['identity'](123));self.assertEqual(statuses,[])
        legacy,calls,_=self.namespace(normal=False);self.assertIsNotNone(legacy['identity'](123))
        self.assertEqual(sum(path.endswith('/status') for kind,path in calls if kind=='read'),1)

    def test_unknown_uid_metadata_is_not_silently_ignored(self):
        ns,_,events=self.namespace(uid='unknown')
        with patch.object(Path,'iterdir',return_value=iter([Path('/proc/123')])):ns['scan']()
        self.assertIn('proc_discovery',ns['failures']);self.assertEqual(events[0][1]['pid'],123)


class NormalFailureCleanupTest(unittest.TestCase):
    def test_existing_failure_enters_abort_cleanup_immediately_only_when_opted_in(self):
        with tempfile.TemporaryDirectory() as directory:
            ns=NormalCompletionTest().loop(Path(directory),scan_failure=True)
            self.assertEqual(ns['abort_at'],1.0);self.assertIsNone(ns['normal_trigger'])
            self.assertIn('proc_discovery',ns['failures']);self.assertIn('ABORT',ns['events'])
            self.assertTrue((Path(directory)/'ABORT').exists())
            self.assertGreaterEqual(ns['clock'].now-ns['quiet'],60);self.assertLess(ns['clock'].now,65)
        with tempfile.TemporaryDirectory() as directory:
            ns=NormalCompletionTest().loop(Path(directory),enabled=False,scan_failure=True)
            self.assertIsNone(ns['abort_at']);self.assertGreaterEqual(ns['quiet'],25)
            self.assertGreaterEqual(ns['clock'].now,85)

    def test_failed_abort_retains_failure_and_has_relative_cleanup_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            ns=NormalCompletionTest().loop(Path(directory),scan_failure=True,empty_after=10000)
            self.assertIn('abort_cleanup_exceeded',ns['failures']);self.assertIsNone(ns['quiet'])
            self.assertGreaterEqual(ns['clock'].now,ns['abort_at']+100)
            self.assertLess(ns['clock'].now,102)
            self.assertTrue(ns['kills']);self.assertIsNone(ns['normal_trigger'])

    def test_abort_publication_failure_does_not_prevent_scoped_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            ns=NormalCompletionTest().loop(Path(directory),scan_failure=True,abort_publish_failure=True,empty_after=5)
            self.assertIn('ABORT_publish',ns['failures']);self.assertEqual(ns['abort_at'],1.0)
            self.assertTrue(ns['kills']);self.assertIsNone(ns['normal_trigger'])
            self.assertGreaterEqual(ns['clock'].now-ns['quiet'],60)


if __name__ == "__main__":
    unittest.main()
