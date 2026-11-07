"""Exercise controller command/entry construction without running its top-level launch."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "benchmarks/v4_run_once.py"
TREE = ast.parse(SOURCE.read_text())
BODY = next(node for node in TREE.body if isinstance(node, ast.Try)).body


def context(store):
    return dict(Path=Path, STORE=store, CONVERT=store, RECOVER=False, DIAGNOSTIC=False,
                overlays=('v4_convert.py','v4_grouped_state.py','vllm_v4_loader.py','v4_export_worker.py') if store else (), MODE="store-conversion" if store else "native-reference",
                ROOT=Path("/source"), MODEL=Path("/weights"), IMAGE="sha256:fixed",
                STORAGE=Path("/store"), store=Path("/store/conversion"),
                base=Path("/job"), cache=Path("/cache"), output=Path("/job/output"),
                indices=list(range(8)), uuids=["GPU-fixture-" + str(i) for i in range(8)])


def assignment(name):
    return next(i for i, node in enumerate(BODY) if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))


class OneShotControllerTests(unittest.TestCase):
    def test_both_wrappers_compile_and_select_only_the_intended_entry(self):
        i = assignment("wrapper")
        for store in (False, True):
            ns = context(store)
            exec(compile(ast.Module(body=BODY[i:i+3], type_ignores=[]), str(SOURCE), "exec"), ns)
            generated = ast.parse(ns["wrapper"])
            path = generated.body[-1].value.args[0].value
            self.assertEqual(path, "/workspace/benchmarks/" +
                             ("v4_store_conversion.py" if store else "v4_native_reference.py"))
            compile(generated, "gpu_entry", "exec")  # Do not execute the GPU precheck.

    def test_exact_container_command_preserves_native_limits_and_store_overlay(self):
        start, stop = assignment("command"), assignment("cid")
        for store in (False, True):
            ns = context(store)
            exec(compile(ast.Module(body=BODY[start:stop], type_ignores=[]), str(SOURCE), "exec"), ns)
            command = ns["command"]
            self.assertEqual(command[command.index("--entrypoint") + 1], "/opt/sllm-v4/bin/python")
            self.assertEqual(command[command.index("--memory") + 1], str(1024**4))
            self.assertEqual(command[command.index("--cpus") + 1], "8")
            self.assertIn("NVIDIA_VISIBLE_DEVICES=" + ",".join(ns["uuids"]), command)
            self.assertIn("CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7", command)
            self.assertIn("type=bind,src=/weights,dst=/weights,readonly", command)
            if store:
                self.assertIn("VLLM_PLUGINS=sllm_v4_store", command)
                self.assertIn("STORAGE_PATH=/store", command)
                self.assertIn("SLLM_EVENT_DIR=/output/events", command)
                self.assertIn("SLLM_EVENT_RUN_ID=job", command)
                self.assertIn("type=bind,src=/store,dst=/store", command)
                self.assertIn("type=bind,src=/source/sllm_store/sllm_store/v4_convert.py,dst=/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/v4_convert.py,readonly", command)
            else:
                self.assertIn("VLLM_PLUGINS=", command)
                self.assertFalse(any(value.startswith("STORAGE_PATH=") for value in command))

    def test_recovery_uses_read_only_store_and_recovery_entry(self):
        ns=context(True)
        ns.update(RECOVER=True,CONVERT=False,MODE="store-recovery")
        i=assignment("wrapper")
        exec(compile(ast.Module(body=BODY[i:i+3],type_ignores=[]),str(SOURCE),"exec"),ns)
        self.assertIn("v4_store_recovery.py",ns["wrapper"])
        self.assertIn("--pool-config",ns["wrapper"])
        ns["DIAGNOSTIC"]=True
        exec(compile(ast.Module(body=BODY[i:i+3],type_ignores=[]),str(SOURCE),"exec"),ns)
        self.assertIn("--diagnostic-pool",ns["wrapper"])
        exec(compile(ast.Module(body=BODY[assignment("command"):assignment("cid")],
                                type_ignores=[]),str(SOURCE),"exec"),ns)
        self.assertIn("type=bind,src=/store,dst=/store,readonly",ns["command"])
        for name in ns["overlays"]:
            self.assertIn("type=bind,src=/source/sllm_store/sllm_store/"+name+
                          ",dst=/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/"+name+",readonly",ns["command"])

    def test_store_stop_thresholds_include_allocated_logical_disk_and_host_memory(self):
        node = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == "store_limits")
        ns = dict(Path=Path, shutil=NS(disk_usage=lambda path: NS(free=128*1024**3)))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), ns)
        file = NS(resolve=lambda: NS(relative_to=lambda directory: None), is_file=lambda: True)
        directory = NS(resolve=lambda: "/store", rglob=lambda pattern: [file])
        for size, blocks, free, memory, expected in (
                (1, 1, 128*1024**3, 64*1024**3, True),
                (1024**4+1, 1, 128*1024**3, 64*1024**3, False),
                (1, 1024**4//512+1, 128*1024**3, 64*1024**3, False),
                (1, 1, 128*1024**3-1, 64*1024**3, False),
                (1, 1, 128*1024**3, 64*1024**3-1024, False)):
            file.stat = lambda: NS(st_size=size, st_blocks=blocks)
            ns["shutil"].disk_usage = lambda path: NS(free=free)
            with mock.patch.object(Path, "read_text", return_value="MemAvailable: " + str(memory//1024) + " kB"):
                self.assertEqual(ns["store_limits"](directory, "/store")["within_limits"], expected)

    def test_guard_exit_reobserves_stale_running_state_and_rejects_still_running(self):
        import json
        index=next(i for i,n in enumerate(BODY) if isinstance(n,ast.With)
                   and 'telemetry.jsonl' in ast.unparse(n.items[0].context_expr))
        nodes=BODY[index:index+3]  # Real telemetry loop, fresh inspect and stopped assertion.
        for running in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                states=[dict(State=dict(Pid=123,Running=True)),
                        dict(State=dict(Pid=123 if running else 0,Running=running))]
                ns=dict(base=Path(directory),inspected=mock.Mock(side_effect=states),
                        group=None,capture_group=mock.Mock(),sample_processes=lambda:[],
                        time=NS(monotonic_ns=lambda:100),gpu_snapshot=lambda:{},STORE=False,
                        json=json,guard=NS(poll=lambda:0),subprocess=subprocess,origin=0)
                if running:
                    with self.assertRaises(AssertionError):
                        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
                else:
                    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
                self.assertEqual(ns['inspected'].call_count,2)

    def test_nvidia_query_timeout_records_unknown_instead_of_aborting_model(self):
        nodes=[n for n in ast.walk(TREE) if isinstance(n,ast.Try)
               and any(isinstance(child,ast.Call) and isinstance(child.func,ast.Name)
                       and child.func.id=='gpu_snapshot' for statement in n.body for child in ast.walk(statement))]
        node=next(n for n in nodes if len(n.body)==1 and isinstance(n.body[0],ast.Assign))
        ns=dict(sample={},subprocess=subprocess,
                gpu_snapshot=mock.Mock(side_effect=subprocess.TimeoutExpired(['nvidia-smi'],5)))
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),ns)
        self.assertIsNone(ns['sample']['gpu'])
        self.assertIn('nvidia-smi',ns['sample']['gpu_observation_error'])

    def test_go_requires_matching_stage_source_and_recorded_notification(self):
        import json
        start, stop = assignment("go"), assignment("recheck")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            valid = dict(purpose="native-reference", source_head="a"*40,
                         authorization="explicit fixture only", notified_before_gpu=True)
            ns = dict(Path=Path, base=base, MODE="native-reference", HEAD="a"*40, STORE=False,
                      json=json)
            for changed in (dict(purpose="store-conversion"), dict(source_head="b"*40),
                            dict(authorization=""), dict(authorization=True),
                            dict(notified_before_gpu=False)):
                (base/"GO.json").write_text(json.dumps(dict(valid, **changed)))
                with self.assertRaises(AssertionError):
                    exec(compile(ast.Module(body=BODY[start:stop], type_ignores=[]),
                                 str(SOURCE), "exec"), ns)
            (base/"GO.json").write_text(json.dumps(valid))
            with mock.patch.object(Path, "read_text", return_value="MemAvailable: " +
                                   str((1024**4+64*1024**3)//1024) + " kB"):
                exec(compile(ast.Module(body=BODY[start:stop], type_ignores=[]),
                             str(SOURCE), "exec"), ns)

    def test_default_controller_is_a_no_claim_no_gpu_plan(self):
        import json
        base = ROOT.parent / "evidence/store-conversion-controller-cpu-plan"
        self.assertFalse(base.exists())
        result = subprocess.run([sys.executable, str(SOURCE), str(base), "a"*40,
                                 "store-conversion"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertFalse(plan["gpu_or_model_started"])
        self.assertFalse(plan["store_claimed"])
        self.assertEqual(plan["container_seconds"], 1080)
        self.assertEqual(plan["total_seconds"], 1200)
        self.assertFalse(base.exists())


if __name__ == "__main__":
    unittest.main()
