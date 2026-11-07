"""Raw-reference diagnostics retain the safety gate; no Torch/CUDA runtime."""
import ast
from pathlib import Path
import types
import unittest

from test_v4_grouped_loader import GroupTensor

ROOT=Path(__file__).resolve().parents[3]
node=next(n for n in ast.parse((ROOT/'sllm_store/sllm_store/vllm_v4_loader.py').read_text()).body
          if isinstance(n,ast.FunctionDef) and n.name=='assert_no_raw_references')
ns=dict(torch=types.SimpleNamespace(is_tensor=lambda t:isinstance(t,GroupTensor)),types=types)
exec(compile(ast.Module(body=[node],type_ignores=[]),'raw-reference-check','exec'),ns)


class RawReferenceDiagnostics(unittest.TestCase):
    def test_nested_and_slotted_retained_tensors_report_path_and_raw_name(self):
        tensor=GroupTensor([4],'torch.uint8')
        with self.assertRaisesRegex(ValueError,"model.hidden.*kept.*raw=expert"):
            ns['assert_no_raw_references'](types.SimpleNamespace(hidden={'kept':tensor}),{'expert':tensor})
        class Holder:
            __slots__=('cached',)
        value=Holder();value.cached=tensor
        with self.assertRaisesRegex(ValueError,'model.cached'):
            ns['assert_no_raw_references'](value,{'expert':tensor})

    def test_callable_globals_and_modules_are_not_model_state_but_cached_tensors_are(self):
        tensor=GroupTensor([4],'torch.uint8')
        loader_module=types.ModuleType('fixture_loader')
        loader_module._GROUPED_CONTEXTS={'fixture':types.SimpleNamespace(owners={'expert':tensor})}
        class JITCallable:
            def __init__(self):
                self.__globals__={'loader':loader_module,'direct_owner':tensor}
            def __call__(self):pass
        kernel=JITCallable()
        model=types.SimpleNamespace(compressor=types.SimpleNamespace(_fused_kernel=kernel),runtime=loader_module)
        ns['assert_no_raw_references'](model,{'expert':tensor})
        kernel.cached_scale=tensor
        with self.assertRaisesRegex(ValueError,'cached_scale'):
            ns['assert_no_raw_references'](model,{'expert':tensor})

    def test_independent_storage_is_accepted(self):
        ns['assert_no_raw_references'](types.SimpleNamespace(hidden=GroupTensor([4],'torch.uint8')),
                                       {'expert':GroupTensor([4],'torch.uint8')})


if __name__=='__main__':unittest.main()
