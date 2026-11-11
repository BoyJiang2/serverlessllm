"""CPU checks for the real V7 metadata gate, without importing a GPU runtime."""
import ast
import copy
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "sllm_store/vllm_glm20_loader.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
namespace = {}
nodes = [n for n in tree.body if isinstance(n, ast.Assign)
         or isinstance(n, ast.FunctionDef) and n.name == "validate_raw_state"]
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
validate = namespace["validate_raw_state"]


class RawStateTest(unittest.TestCase):
    def setUp(self):
        self.expected = {"weight": ([2], [1], "torch.bfloat16", 4),
                         "alias": ([2], [1], "torch.bfloat16", 4)}
        row = dict(shape=[2], stride=[1], dtype="torch.bfloat16", bytes=4)
        self.metadata = dict(
            format=namespace["FORMAT"], rank=0, tensor_parallel_size=8,
            state="before_quantization_postprocessing",
            VLLM_USE_DEEP_GEMM_E8M0="0", source_revision=namespace["REVISION"],
            vllm_version=namespace["SOURCE_VERSIONS"][0],
            torch_version=namespace["SOURCE_VERSIONS"][1],
            tensors={"weight": row, "alias": dict(row, alias="weight")},
        )
        self.aliases = {"alias": "weight"}

    def test_known_source_and_matching_target_schema(self):
        validate(self.metadata, 0, self.expected, self.aliases)

    def test_source_identity_and_rank_are_not_relaxed(self):
        for key, value in [("rank", 1), ("source_revision", "other"),
                           ("vllm_version", "0.20.1"),
                           ("state", "postprocessed")]:
            with self.subTest(key=key):
                changed = dict(self.metadata, **{key: value})
                with self.assertRaises(ValueError):
                    validate(changed, 0, self.expected, self.aliases)

    def test_shape_stride_dtype_bytes_empty_and_alias_must_match(self):
        for key, value in [("shape", [4]), ("stride", [2]),
                           ("dtype", "torch.float32"), ("bytes", 8),
                           ("empty", True), ("alias", "missing")]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.metadata)
                changed["tensors"]["weight"][key] = value
                with self.assertRaises(ValueError):
                    validate(changed, 0, self.expected, self.aliases)

    def test_missing_and_extra_tensors_fail(self):
        for rows in [{}, dict(self.metadata["tensors"], extra={})]:
            with self.assertRaises(ValueError):
                validate(dict(self.metadata, tensors=rows), 0,
                         self.expected, self.aliases)

    def test_constructor_alias_disagreement_fails(self):
        with self.assertRaises(ValueError):
            validate(self.metadata, 0, self.expected, {})


if __name__ == "__main__":
    unittest.main()
