"""Package metadata from actual setup sources, without setup/build/CUDA execution."""
import ast
import builtins
import io
import os
from pathlib import Path
from typing import Dict
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]


def metadata(directory, profile=None):
    source = directory / "setup.py"
    tree = ast.parse(source.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.If) and any(isinstance(n, ast.Name) and n.id in ("CUDA_HOME", "ROCM_HOME") for n in ast.walk(node)):
            continue
        if isinstance(node, (ast.Assign, ast.If, ast.FunctionDef)) or (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "setup"):
            nodes.append(node)
    setup = mock.Mock()
    namespace = dict(__file__=str(source), os=os, io=io, Path=Path, Dict=Dict,
                     setup=setup, find_packages=lambda: ["sllm"],
                     CMakeExtension=lambda **kwargs: kwargs, cmake_build_ext=object,
                     CustomInstall=object,
                     open=lambda path, *args, **kwargs: builtins.open(directory / path, *args, **kwargs))
    environment = dict(os.environ)
    environment.pop("SLLM_BUILD_PROFILE", None)
    if profile is not None:
        environment["SLLM_BUILD_PROFILE"] = profile
    with mock.patch.dict(os.environ, environment, clear=True):
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return setup.call_args.kwargs


class V4PackagingContracts(unittest.TestCase):
    def test_default_service_metadata_preserves_legacy_pins(self):
        result = metadata(ROOT)
        self.assertEqual(result["version"], "0.8.0")
        self.assertIn("openai==1.52.0", result["install_requires"])
        self.assertIn("vllm==0.11.2", result["extras_require"]["worker"])

    def test_default_store_metadata_preserves_legacy_requirements(self):
        result = metadata(ROOT / "sllm_store")
        self.assertEqual(result["version"], "0.8.0")
        self.assertIn("torch>=2.7.0", result["install_requires"])

    def test_v4_service_identity_and_dependency_metadata_are_distinct(self):
        result = metadata(ROOT, "v4-pro")
        self.assertEqual(result["version"], "0.8.0+v4pro.1")
        self.assertIn("openai>=2.0.0", result["install_requires"])
        self.assertIn("fastapi==0.121.3", result["install_requires"])
        self.assertIn("pydantic>=2.12.0", result["install_requires"])
        self.assertIn("serverless-llm-store==0.8.0+v4pro.1", result["install_requires"])
        self.assertFalse(any(row.startswith("pydantic_core==") for row in result["install_requires"]))
        self.assertIn("vllm==0.20.1", result["extras_require"]["worker"])
        self.assertIn("torch==2.11.0", result["extras_require"]["worker"])

    def test_v4_store_identity_matches_target_runtime(self):
        result = metadata(ROOT / "sllm_store", "v4-pro")
        self.assertEqual(result["version"], "0.8.0+v4pro.1")
        self.assertIn("torch==2.11.0", result["install_requires"])
        self.assertIn("click>=8.4.2", result["install_requires"])
        self.assertIn("transformers==5.6.0", result["install_requires"])
        self.assertIn("vllm==0.20.1", result["extras_require"]["vllm"])

    def test_unknown_profile_fails_in_both_packages(self):
        for directory in (ROOT, ROOT / "sllm_store"):
            with self.subTest(directory=directory), self.assertRaisesRegex(ValueError, "SLLM_BUILD_PROFILE"):
                metadata(directory, "v4-por")


if __name__ == "__main__":
    unittest.main()
