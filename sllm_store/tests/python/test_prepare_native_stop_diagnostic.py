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


if __name__ == "__main__":
    unittest.main()
