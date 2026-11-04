"""Run the actual patched runtime helper AST without Torch or vLLM imports."""
import ast
import contextlib
import pathlib
import textwrap
import types
import unittest
from unittest import mock


def section(module):
    patch = (pathlib.Path(__file__).resolve().parents[2]
             / "vllm_patch/sllm_load.patch").read_text()
    part = patch.split("+++ b/vllm/" + module, 1)[1].split("diff --git ", 1)[0]
    return "".join(line[1:] for line in part.splitlines(True)
                   if line.startswith((" ", "+")) and not line.startswith("+++"))


def function(source, name):
    lines = source.splitlines(True)
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("def " + name + "("))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith(("def ", "class ", "@"))), len(lines))
    return ast.parse("".join(lines[start:end])).body[0]


class RestoreRuntimeTest(unittest.TestCase):
    def test_standard_quant_then_attention_then_torchao_order(self):
        source = section("model_executor/model_loader/utils.py")
        methods = [function(source, name) for name in (
            "process_attention_weights_after_loading", "process_weights_after_loading")]
        events = []

        class Quant:
            def process_weights_after_loading(self, module):
                events.append("quant")

        class Attention:
            quant_method = None
            def process_weights_after_loading(self, dtype):
                events.append("attention")

        model = types.SimpleNamespace(named_modules=lambda: [
            ("quant", types.SimpleNamespace(quant_method=Quant())),
            ("attention", Attention())])
        namespace = dict(nn=types.SimpleNamespace(Module=object), ModelConfig=object,
                         torch=types.SimpleNamespace(device=object),
                         QuantizeMethodBase=Quant, Attention=Attention,
                         MLAAttention=type("MLAAttention", (), {}),
                         device_loading_context=lambda *a: contextlib.nullcontext(),
                         set_torchao_reload_attrs=lambda *a: events.append("torchao"))
        exec(compile(ast.Module(body=methods, type_ignores=[]), "<official-utils>", "exec"), namespace)
        namespace["process_weights_after_loading"](
            model, types.SimpleNamespace(dtype="bf16", quantization="torchao"), "cuda")
        self.assertEqual(events, ["quant", "attention", "torchao"])

    def test_marlin_runtime_helper_only_uses_existing_workspace_utility(self):
        source = section("model_executor/layers/quantization/compressed_tensors/compressed_tensors_moe.py")
        start = source.index("    def prepare_runtime_for_restored_weights(")
        stop = source.index("    def get_fused_moe_quant_config(", start)
        method = ast.parse(textwrap.dedent(source[start:stop])).body[0]
        utility = mock.Mock(return_value=object())
        namespace = dict(torch=types.SimpleNamespace(nn=types.SimpleNamespace(Module=object)),
                         marlin_make_workspace_new=utility)
        exec(compile(ast.Module(body=[method], type_ignores=[]), "<official-marlin>", "exec"), namespace)
        device = object()
        layer = types.SimpleNamespace(w13_weight_packed=types.SimpleNamespace(device=device))
        namespace["prepare_runtime_for_restored_weights"](object(), layer)
        utility.assert_called_once_with(device, 4)
        self.assertIs(layer.workspace, utility.return_value)
        self.assertEqual(len(method.body), 1)
        self.assertIn("self.prepare_runtime_for_restored_weights(layer)", source)

    def test_restore_initializes_runtime_then_native_attention_without_repack(self):
        tree = ast.parse(section("model_executor/model_loader/sllm_loader.py"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "load_model")
        body = method.body[-2].body
        runtime = next(n for n in body if isinstance(n, ast.For)
                       and ast.unparse(n.iter) == "model.modules()")
        attention = next(n for n in body if isinstance(n, ast.Expr)
                         and isinstance(n.value, ast.Call)
                         and ast.unparse(n.value.func) == "process_attention_weights_after_loading")
        self.assertLess(body.index(runtime), body.index(attention))
        events = []
        quant = types.SimpleNamespace(
            prepare_runtime_for_restored_weights=lambda layer: events.append("runtime"),
            process_weights_after_loading=mock.Mock(side_effect=AssertionError("repack")))
        model = types.SimpleNamespace(modules=lambda: [
            types.SimpleNamespace(quant_method=quant), object()])
        namespace = dict(model=model, model_config=object(), device_id=0,
                         torch=types.SimpleNamespace(device=lambda device: device),
                         process_attention_weights_after_loading=lambda *a: events.append("attention"))
        exec(compile(ast.Module(body=[runtime, attention], type_ignores=[]),
                     "<official-restore-runtime>", "exec"), namespace)
        self.assertEqual(events, ["runtime", "attention"])
        quant.process_weights_after_loading.assert_not_called()
        self.assertNotIn(".process_weights_after_loading(", ast.unparse(method))


if __name__ == "__main__":
    unittest.main()
