"""Bounded stdlib regression of the component draft's actual cleanup AST."""
import ast
import contextlib
import copy
import io
import json
import pathlib
import sys
import types
import unittest


SOURCE = pathlib.Path(__file__).with_name("test_kimi_tensor_component.py")
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
CLEANUPS = [node.finalbody for node in ast.walk(TREE)
            if isinstance(node, ast.Try) and node.finalbody
            and isinstance(node.finalbody[0], ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "primary"
                    for target in node.finalbody[0].targets)]
if len(CLEANUPS) != 1:
    raise AssertionError("expected exactly one actual component cleanup block")
BODY = ast.parse("if primary_error is not None:\n    raise primary_error\n").body
CODE = compile(ast.fix_missing_locations(ast.Module(body=[ast.Try(
    body=BODY, handlers=[], orelse=[], finalbody=copy.deepcopy(CLEANUPS[0]))],
    type_ignores=[])), str(SOURCE), "exec")


class CleanupTests(unittest.TestCase):
    def test_actual_finally(self):
        scenarios = [
            ("success", True, (), False),
            ("sync_failure", True, ("sync",), False),
            ("unload_failure", True, ("unload",), False),
            ("unload_false", True, ("unload",), False),
            ("close_failure", True, ("close",), False),
            ("primary_and_sync", True, ("sync",), True),
            ("primary_and_remaining_cleanup", True, ("sync", "close"), True),
            ("not_loaded", False, (), False),
            ("not_loaded_primary", False, (), True),
            ("not_loaded_sync_failure", False, ("sync",), False),
        ]
        for name, loaded, failures, has_primary in scenarios:
            with self.subTest(name=name):
                events = []

                def operation(label, result=None):
                    def call(*args):
                        events.append(label)
                        if name == "unload_false" and label == "unload":
                            return False
                        if label in failures:
                            raise RuntimeError(label + " injected failure")
                        return result
                    return call

                def require(condition, message):
                    if not condition:
                        raise AssertionError(message)

                primary = ValueError("original failure") if has_primary else None
                namespace = {
                    "sys": sys, "json": json, "require": require,
                    "wait_server_failure_barrier": operation("barrier"),
                    "primary_error": primary, "load_attempted": loaded,
                    "actual": object(),
                    "restored": types.SimpleNamespace(clear=operation("clear")),
                    "gc": types.SimpleNamespace(collect=operation("collect")),
                    "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(
                        synchronize=operation("sync"))),
                    "client": types.SimpleNamespace(
                        unload_from_cpu=operation("unload", True),
                        channel=types.SimpleNamespace(close=operation("close"))),
                    "args": types.SimpleNamespace(archive="test-archive"),
                }
                stderr = io.StringIO()
                caught = None
                with contextlib.redirect_stderr(stderr):
                    try:
                        exec(CODE, namespace)
                    except Exception as error:
                        caught = error
                self.assertEqual(events, (["barrier"] if has_primary else [])
                                 + ["clear", "collect", "sync"]
                                 + (["unload"] if loaded and not has_primary else []) + ["close"])
                self.assertIsNone(namespace["actual"])
                if has_primary:
                    self.assertIs(caught, primary)
                elif failures:
                    self.assertIsInstance(caught, RuntimeError)
                else:
                    self.assertIsNone(caught)
                if failures:
                    errors = json.loads(stderr.getvalue())["cleanup_errors"]
                    self.assertEqual(len(errors), len(failures))
                    for failure, error in zip(failures, errors):
                        expected = ("official CPU unload failed" if name == "unload_false"
                                    else failure + " injected failure")
                        self.assertIn(expected, error)
                else:
                    self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
