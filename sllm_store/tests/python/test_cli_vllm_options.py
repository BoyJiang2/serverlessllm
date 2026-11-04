"""AST-isolated CLI tests: no Torch/vLLM imports, downloads or shared model paths."""
import ast
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
from typing import Optional
import unittest
from unittest.mock import patch

try:
    import click
    from click.testing import CliRunner
except ImportError:
    click = None

ENTRY = Path(__file__).resolve().parents[2] / "sllm_store" / "cli.py"


@unittest.skipIf(click is None, "host Click unavailable; do not install dependencies")
class VllmSaveOptionsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sllm-cli-options-")
        self.addCleanup(self.temporary.cleanup)
        self.input = Path(self.temporary.name) / "input"
        self.input.mkdir()
        self.storage = Path(self.temporary.name) / "store"
        self.llm_calls = []
        self.save_calls = []
        executor = types.SimpleNamespace(save_serverless_llm_state=lambda **kw: self.save_calls.append(kw))
        def fake_llm(**kwargs):
            self.llm_calls.append(kwargs)
            return types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=executor))
        def forbidden_download(*args, **kwargs):
            raise AssertionError("these tests must never download")
        modules = {
            "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
            "huggingface_hub": types.SimpleNamespace(snapshot_download=forbidden_download),
            "vllm": types.SimpleNamespace(LLM=fake_llm),
        }
        isolated_modules = patch.dict(sys.modules, modules)
        isolated_modules.start()
        self.addCleanup(isolated_modules.stop)
        tree = ast.parse(ENTRY.read_text())
        names = ("resolve_storage_path", "VllmModelDownloader", "save")
        selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
        save = next(node for node in selected if node.name == "save")
        # Keep real Click options, replacing only its unrelated root-group registration.
        save.decorator_list[0] = ast.parse("click.command()", mode="eval").body
        namespace = dict(os=os, shutil=shutil, sys=sys, Optional=Optional, click=click,
                         logger=logging.getLogger("cli-options-fixture"))
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), "isolated-cli", "exec"), namespace)
        self.downloader = namespace["VllmModelDownloader"]
        self.command = namespace["save"]

    def arguments(self):
        return ["--model", "fixture", "--backend", "vllm", "--local-model-path", str(self.input),
                "--storage-path", str(self.storage)]

    def test_cli_reaches_real_writer_with_bfloat16_trust_and_data_mode(self):
        result = CliRunner().invoke(self.command, self.arguments() + ["--dtype", "bfloat16",
            "--trust-remote-code", "--mm-encoder-tp-mode", "data", "--tensor-parallel-size", "8"])
        self.assertEqual(result.exit_code, 0, result.output)
        kwargs = self.llm_calls[0]
        self.assertEqual((kwargs["dtype"], kwargs["trust_remote_code"], kwargs["mm_encoder_tp_mode"]),
                         ("bfloat16", True, "data"))
        self.assertEqual(kwargs["tensor_parallel_size"], 8)
        self.assertEqual(kwargs["model"], str(self.input))
        self.assertEqual(self.save_calls[0]["path"], str(self.storage / "vllm" / "fixture"))

    def test_sdk_accepts_explicit_options(self):
        self.downloader().download_vllm_model("fixture", "bfloat16", tensor_parallel_size=8,
            storage_path=str(self.storage), local_model_path=str(self.input),
            trust_remote_code=True, mm_encoder_tp_mode="data")
        self.assertEqual(self.llm_calls[0]["dtype"], "bfloat16")
        self.assertIs(self.llm_calls[0]["trust_remote_code"], True)
        self.assertEqual(self.llm_calls[0]["mm_encoder_tp_mode"], "data")

    def test_legacy_sdk_and_cli_defaults_omit_version_specific_mm_kwarg(self):
        self.downloader().download_vllm_model("fixture", "float16", 1, str(self.storage), str(self.input))
        result = CliRunner().invoke(self.command, self.arguments())
        self.assertEqual(result.exit_code, 0, result.output)
        self.command.callback("fixture", "vllm", None, 1, str(self.input), str(self.storage))
        self.assertEqual(len(self.llm_calls), 3)
        for kwargs in self.llm_calls:
            self.assertEqual(kwargs["dtype"], "float16")
            self.assertFalse(kwargs.get("trust_remote_code", False))
            self.assertNotIn("mm_encoder_tp_mode", kwargs)
            self.assertEqual((kwargs["num_gpu_blocks_override"], kwargs["enforce_eager"], kwargs["max_model_len"]), (1, True, 1))

    def test_invalid_cli_options_do_not_reach_writer(self):
        for option, value in [("--dtype", "invalid"), ("--mm-encoder-tp-mode", "invalid")]:
            result = CliRunner().invoke(self.command, self.arguments() + [option, value])
            self.assertEqual(result.exit_code, 2)
        self.assertFalse(self.llm_calls)


if __name__ == "__main__":
    unittest.main()
