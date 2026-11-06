"""Preparation-only regressions; no Docker, model, Store or GPU execution."""
import ast
import copy
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "prepare_native_stop_diagnostic.py"
loader = importlib.util.spec_from_file_location("prepare_native_stop", SOURCE)
prep = importlib.util.module_from_spec(loader)
loader.loader.exec_module(prep)


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.spec = prep.checked_json(prep.BASE_SPEC, prep.BASE_SPEC_SHA)
        self.command = prep.checked_json(prep.BASE_COMMAND, prep.BASE_COMMAND_SHA)

    def render(self):
        return prep.render(self.spec, self.command, "7" * 40, "a" * 64)

    def test_actual_render_roles_gold_budget_and_consumed_runner(self):
        sb, cb = self.render()
        spec, command = json.loads(sb), json.loads(cb)
        self.assertEqual(spec["role_bodies"], self.spec["role_bodies"])
        for key in ("role_body_sha256", "gold", "config", "budgets", "assets"):
            self.assertEqual(spec[key], self.spec[key])
        self.assertEqual(spec["budgets"]["full"], 7200)
        self.assertEqual(spec["source_commit"], "7" * 40)
        self.assertEqual(spec["native_source_pins"][prep.BACKEND], prep.NEW_BACKEND)
        runner = next(o for o in spec["overlays"] if o["target"] == prep.RUNNER_TARGET)
        self.assertEqual(runner["sha256"], prep.NEW_RUNNER_SHA)
        mounts = spec["requests"]["worker"]["mounts"]
        actual = next(m for m in mounts if m["Target"] == prep.RUNNER_TARGET)
        self.assertEqual(actual["Source"], str(prep.NEW_RUNNER))
        self.assertTrue(actual["ReadOnly"])
        self.assertEqual(command["argv"][-2], hashlib.sha256(sb).hexdigest())
        body = command["argv"][command["argv"].index("-c") + 1]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), command["root_python_sha256"])
        old_body = self.command["argv"][self.command["argv"].index("-c") + 1]
        old = next(n for n in ast.parse(old_body).body if isinstance(n, ast.FunctionDef) and n.name == "stop_service")
        new = next(n for n in ast.parse(body).body if isinstance(n, ast.FunctionDef) and n.name == "stop_service")
        old_source = ast.get_source_segment(old_body, old)
        new_source = ast.get_source_segment(body, new)
        self.assertTrue(new_source.endswith(old_source.split("\n", 1)[1]))
        self.assertEqual(body.replace(new_source, old_source), old_body)
        self.assertIn("m.process_stop_snapshot(owner,deadline)", new_source)

    def test_legacy_producer_drift_rejected(self):
        for change in ("source", "sha256"):
            spec = copy.deepcopy(self.spec)
            runner = next(o for o in spec["overlays"] if o["target"] == prep.RUNNER_TARGET)
            runner[change] = "unexpected"
            with self.assertRaises(AssertionError):
                prep.render(spec, self.command, "7" * 40, "a" * 64)

    def test_wrong_actual_mount_rejected(self):
        mounts = self.spec["requests"]["worker"]["mounts"]
        next(m for m in mounts if m["Target"] == prep.RUNNER_TARGET)["Source"] = "/wrong"
        with self.assertRaises(AssertionError):
            self.render()

    def test_role_body_drift_rejected(self):
        self.spec["role_bodies"]["http"] += "\npass"
        with self.assertRaises(AssertionError):
            self.render()

    def test_unexpected_native_pin_rejected(self):
        def actual(path):
            if str(path) in self.spec["code_pins"]:
                return self.spec["code_pins"][str(path)]
            relative = str(pathlib.Path(path).relative_to(prep.ROOT))
            return "wrong" if relative == "sllm/controller.py" else prep.NEW_BACKEND
        with mock.patch.object(prep, "digest", side_effect=actual):
            with self.assertRaises(AssertionError):
                prep.validate_input_pins(self.spec)

    def test_dirty_source_and_wrong_commit_rejected(self):
        with mock.patch.object(prep.subprocess, "check_output", return_value=b"0" * 40 + b"\n"):
            with self.assertRaises(AssertionError):
                prep.committed_helper("7" * 40)
        with mock.patch.object(prep.subprocess, "check_output", side_effect=[b"7" * 40 + b"\n", b" M tracked.py\n"]):
            with self.assertRaises(AssertionError):
                prep.committed_helper("7" * 40)

    def test_output_collision_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "candidate.json"
            prep.write_new(path, b"original")
            with self.assertRaises(FileExistsError):
                prep.write_new(path, b"changed")
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)



class PoolGraceTest(unittest.TestCase):
    def setUp(self):
        self.spec = prep.checked_json(prep.POOL_SPEC, prep.POOL_SPEC_SHA)
        self.command = prep.checked_json(prep.POOL_COMMAND, prep.POOL_COMMAND_SHA)
        sb, cb = prep.render_pool_grace(self.spec, self.command, '7' * 40, prep.HELPER_SHA)
        self.new_spec, self.new_command = json.loads(sb), json.loads(cb)
        self.body = self.new_command['argv'][self.new_command['argv'].index('-c') + 1]

    def final_nodes(self):
        block = next(n for n in ast.parse(self.body).body if isinstance(n, ast.Try))
        first = next(i for i,n in enumerate(block.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'stop_started' for t in n.targets))
        last = next(i for i,n in enumerate(block.body[first:], first) if isinstance(n, ast.Assert) and ast.unparse(n.test) == 'time.monotonic() < stop_end')
        # The first such assert follows both Ray stops; include the second after both normal exits.
        last = next(i for i,n in enumerate(block.body[last+1:], last+1) if isinstance(n, ast.Assert) and ast.unparse(n.test) == 'time.monotonic() < stop_end')
        return block.body[first:last+1]

    def environment(self, folder):
        import types
        clock = types.SimpleNamespace(now=1000.0)
        clock.monotonic = lambda: clock.now
        base = pathlib.Path(folder)
        for role in ('head', 'worker'):
            d = base / ('guard-' + role);d.mkdir()
            (d / 'GO.json').write_text(json.dumps({'cid':role, 'nonce':role, 'monotonic':0}))
        rows = {}
        for role in ('head', 'worker', 'observer'):
            b = self.new_spec['budgets']['observer' if role == 'observer' else role + '_guard']
            rows[role] = {'MainPID':'1', 'ActiveEnterTimestampMonotonic':'0', 'RuntimeMaxUSec':str(b['native']) + 's'}
        calls = []
        def execute(*a, **kw):
            calls.append(('controller', kw['seconds']));clock.now += kw['seconds']
        def stop(name, role, deadline):
            calls.append((name, deadline-clock.now));clock.now = deadline
        def official(*a, **kw):
            calls.append((kw['name'], kw['seconds']));clock.now += kw['seconds']
        def normal(role):
            calls.append(('normal_'+role,17));clock.now += 17
        return {'time':clock,'started':0,'end':7198,'s':copy.deepcopy(self.new_spec),'props':lambda u:rows[u],
                'units':{r:r for r in rows},'re':__import__('re'),'json':json,'base':base,
                'cids':{'head':'head','worker':'worker'},'arms':{'head':{'nonce':'head'},'worker':{'nonce':'worker'}},
                'execute':execute,'stop_service':stop,'official':official,'normal_exit':normal,
                'controller_initial':{},'rows_fixture':rows,'calls':calls}

    def run_final(self, env, gate_only=False):
        nodes = self.final_nodes()
        if gate_only:
            nodes = nodes[:next(i for i,n in enumerate(nodes) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == 'execute')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]), '<actual-generated-final>', 'exec'), env)

    def test_actual_final_slot_and_service_caps(self):
        with tempfile.TemporaryDirectory() as folder:
            e = self.environment(folder);self.run_final(e)
            self.assertEqual(e['calls'], [('controller',125),('api',30),('store',300),('ray-worker-stop',40),('ray-head-stop',40),('normal_worker',17),('normal_head',17)])
            self.assertEqual(e['time'].now,1569)
            self.assertEqual(e['stop_end'],1600)
            self.assertEqual(self.new_spec['budgets']['sum'],6540)
            self.assertEqual(self.new_spec['budgets']['phase_max'][-2:], [720,180])

    def test_actual_gate_rejects_insufficient_slots_before_shutdown(self):
        for kind in ('guard_go','overall','native','observer','outer','global'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                e=self.environment(folder)
                if kind=='guard_go':
                    p=e['base']/'guard-worker/GO.json';x=json.loads(p.read_text());x['monotonic']=-4800;p.write_text(json.dumps(x))
                elif kind=='overall':e['s']['budgets']['worker_guard']['overall']=1700
                elif kind=='native':
                    e['s']['budgets']['worker_guard']['native']=1700;e['rows_fixture']['worker']['RuntimeMaxUSec']='1700s'
                elif kind=='observer':e['s']['budgets']['observer']['duration']=1700
                elif kind=='outer':e['end']=1900
                else:e['time'].now=6001
                with self.assertRaises(AssertionError):self.run_final(e)
                self.assertEqual(e['calls'],[])

    def test_actual_both_normal_exits_inside_aggregate(self):
        with tempfile.TemporaryDirectory() as folder:
            e=self.environment(folder)
            e['normal_exit']=lambda role:setattr(e['time'],'now',e['time'].now+40)
            with self.assertRaises(AssertionError):self.run_final(e)
            self.assertEqual(e['time'].now,1615)

    def test_actual_gate_identity_and_runtime_drift(self):
        for kind in ('nonce','native','dead'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                e=self.environment(folder)
                if kind=='nonce':e['arms']['worker']['nonce']='wrong'
                elif kind=='native':e['rows_fixture']['worker']['RuntimeMaxUSec']='6481s'
                else:e['rows_fixture']['worker']['MainPID']='0'
                with self.assertRaises(AssertionError):self.run_final(e,True)
                self.assertEqual(e['calls'],[])

    def test_only_authorized_root_changes_and_preserved_roles(self):
        old=self.command['argv'][self.command['argv'].index('-c')+1]
        old_gate=' assert time.monotonic()-started<6360;stop_end=time.monotonic()+240\n'
        changed=self.body.replace(prep.GRACE_GATE,old_gate)
        changed=changed.replace("store_stop_end=time.monotonic()+300;assert store_stop_end<stop_end,'full Store stop slot unavailable';stop_service('store','worker',store_stop_end)", "stop_service('store','worker',min(stop_end,time.monotonic()+30))")
        changed=changed.replace("normal_exit('worker');normal_exit('head');assert time.monotonic()<stop_end;", "normal_exit('worker');normal_exit('head');")
        changed=changed.replace("assert time.monotonic()<deadline,'Store stop deadline exceeded';healthy();now=", "assert time.monotonic()<deadline;healthy();now=")
        self.assertEqual(changed,old)
        for key in ('role_bodies','role_body_sha256','gold','config','assets','code_pins','native_source_pins','overlays'):
            self.assertEqual(self.new_spec[key],self.spec[key])
        for key in ('head_guard','worker_guard','observer'):
            self.assertEqual(self.new_spec['budgets'][key],self.spec['budgets'][key])
        for role in self.spec['requests']:
            req=copy.deepcopy(self.new_spec['requests'][role]);req['name']=self.spec['requests'][role]['name']
            self.assertEqual(req,self.spec['requests'][role])
        self.assertIn("Store stop deadline exceeded",self.body)

    def test_exact_existing_build_provenance_and_pin_drift(self):
        prep.validate_pool_pins(self.spec)
        bad=copy.deepcopy(self.spec);bad['code_pins'][str(prep.POOL_BUILD/'_checkpoint_store.cpython-312-x86_64-linux-gnu.so')]='0'*64
        with self.assertRaises(AssertionError):prep.validate_pool_pins(bad)

    def test_render_binds_actual_body_and_new_identity(self):
        self.assertEqual(self.new_command['argv'][-3],str(prep.GRACE_SPEC))
        self.assertEqual(self.new_command['root_python_sha256'],hashlib.sha256(self.body.encode()).hexdigest())
        self.assertEqual(self.new_spec['run_id'],prep.GRACE_RUN)
        self.assertEqual(self.new_command['argv'][-1],self.command['argv'][-1])


if __name__ == "__main__":
    unittest.main()
