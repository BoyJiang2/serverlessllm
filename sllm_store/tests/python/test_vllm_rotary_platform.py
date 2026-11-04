"""Exercise the official optional rotary import guard without Torch imports."""
import ast
import copy
import pathlib
import sys
import textwrap
import types
import unittest
from unittest import mock


class RotaryPlatformTest(unittest.TestCase):
    def section(self):
        patch = (pathlib.Path(__file__).resolve().parents[2]
                 / "vllm_patch/sllm_load.patch").read_text()
        return patch.split("+++ b/vllm/model_executor/layers/rotary_embedding/common.py", 1)[1].split("diff --git ", 1)[0]

    def constructor(self, rocm, present, original=False):
        lines = [line[1:] for line in self.section().splitlines()
                 if line.startswith((" ", "+")) and not line.startswith("+++")]
        start = next(i for i, line in enumerate(lines) if line == "    def __init__(")
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "    @staticmethod")
        method = ast.parse(textwrap.dedent("\n".join(lines[start:end]))).body[0]
        if original:
            guard = next(node for node in ast.walk(method) if isinstance(node, ast.If))
            self.assertIsInstance(guard.test, ast.BoolOp)
            guard.test = copy.deepcopy(guard.test.values[1])
        tree = ast.Module(body=[ast.ClassDef(name="ApplyRotaryEmb", bases=[ast.Name(id="CustomOp", ctx=ast.Load())],
                                           keywords=[], body=[method], decorator_list=[])], type_ignores=[])
        ast.fix_missing_locations(tree)

        class CustomOp:
            def __init__(self, enforce_enable=False):
                self.enforce_enable = enforce_enable

        find_spec = mock.Mock(return_value=object() if present else None)
        namespace = {"CustomOp": CustomOp, "find_spec": find_spec,
                     "current_platform": types.SimpleNamespace(is_rocm=lambda: rocm)}
        exec(compile(tree, "<official-rotary-constructor>", "exec"), namespace)
        return namespace["ApplyRotaryEmb"], find_spec

    def namespace_modules(self, complete):
        names = ["flash_attn"]
        if complete:
            names += ["flash_attn.ops", "flash_attn.ops.triton", "flash_attn.ops.triton.rotary"]
        modules = {name: types.ModuleType(name) for name in names}
        for module in modules.values():
            module.__path__ = []
        return modules

    def test_cuda_skips_external_namespace_probe_and_import(self):
        constructor, find_spec = self.constructor(rocm=False, present=True)
        with mock.patch.dict(sys.modules, self.namespace_modules(complete=False)):
            layer = constructor()
        self.assertIsNone(layer.apply_rotary_emb_flash_attn)
        find_spec.assert_not_called()

    def test_original_cuda_namespace_condition_reproduces_missing_ops(self):
        constructor, find_spec = self.constructor(rocm=False, present=True, original=True)
        with mock.patch.dict(sys.modules, self.namespace_modules(complete=False)):
            with self.assertRaises(ModuleNotFoundError) as raised:
                constructor()
        self.assertEqual(raised.exception.name, "flash_attn.ops")
        find_spec.assert_called_once_with("flash_attn")

    def test_hip_existing_external_import_is_preserved(self):
        constructor, find_spec = self.constructor(rocm=True, present=True)
        modules = self.namespace_modules(complete=True)
        apply_rotary = object()
        modules["flash_attn.ops.triton.rotary"].apply_rotary = apply_rotary
        with mock.patch.dict(sys.modules, modules):
            layer = constructor(enforce_enable=True)
        self.assertIs(layer.apply_rotary_emb_flash_attn, apply_rotary)
        self.assertTrue(layer.enforce_enable)
        find_spec.assert_called_once_with("flash_attn")

    def test_hip_missing_package_keeps_existing_native_fallback_selection(self):
        constructor, find_spec = self.constructor(rocm=True, present=False)
        layer = constructor()
        self.assertIsNone(layer.apply_rotary_emb_flash_attn)
        find_spec.assert_called_once_with("flash_attn")

    def test_patch_only_changes_platform_guard_and_import(self):
        added = [line[1:] for line in self.section().splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        removed = [line[1:] for line in self.section().splitlines()
                   if line.startswith("-") and not line.startswith("---")]
        self.assertEqual(added, ["from vllm.platforms import current_platform",
                                '        if current_platform.is_rocm() and find_spec("flash_attn") is not None:'])
        self.assertEqual(removed, ['        if find_spec("flash_attn") is not None:'])
        self.assertNotIn("def forward_cuda", "\n".join(added + removed))


if __name__ == "__main__":
    unittest.main()
