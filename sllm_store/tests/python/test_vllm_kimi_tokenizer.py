"""Exercise the canonical HF factory postimage carried by sllm_load.patch."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


def factory_source():
    patch = Path(__file__).resolve().parents[2] / "vllm_patch/sllm_load.patch"
    lines = patch.read_text().split("--- a/vllm/tokenizers/hf.py\n", 1)[1].splitlines(True)
    source = "".join(line[1:] for line in lines[1:] if line.startswith((" ", "+")) and not line.startswith("+++"))
    tree = ast.parse(source)
    factory = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CachedHfTokenizer")
    method = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == "from_pretrained")
    method.decorator_list = []
    return ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))


class TestKimiTokenizerFactory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        (self.path / "config.json").write_text(json.dumps({"model_type": "kimi_k25"}))
        self.mapping = {"tokenizer_class": "TikTokenTokenizer", "auto_map": {"AutoTokenizer": ["tokenization_kimi.TikTokenTokenizer", None]}}
        self.write_mapping(self.mapping)
        self.tokenizer = Mock()
        self.tokenizer.special_tokens_map = {"bos_token": "[BOS]"}
        self.auto = Mock()
        self.auto.from_pretrained.return_value = self.tokenizer
        self.official = Mock()
        self.official.from_pretrained.return_value = self.tokenizer
        self.loader = Mock(return_value=self.official)
        self.encoder = Mock(return_value={})
        self.cached = Mock(return_value=object())
        self.env = {"Path": Path, "json": json, "AutoTokenizer": self.auto,
                    "transformers_version": "5.5.1", "get_class_from_dynamic_module": self.loader,
                    "get_sentence_transformer_tokenizer_config": self.encoder,
                    "get_cached_tokenizer": self.cached, "HfTokenizer": object}
        exec(compile(factory_source(), "sllm_load.patch:hf.py", "exec"), self.env)
        self.factory = type("Factory", (), {"from_pretrained": classmethod(self.env["from_pretrained"])})

    def write_mapping(self, mapping):
        (self.path / "tokenizer_config.json").write_text(json.dumps(mapping))

    def call(self, **kwargs):
        return self.factory.from_pretrained(self.path, trust_remote_code=True, **kwargs)

    def test_target_arguments_and_shared_tail(self):
        kwargs = {"code_revision": "code", "local_files_only": True, "token": "token",
                  "force_download": False, "proxies": {"https": "proxy"}, "subfolder": "",
                  "use_fast": False}
        self.encoder.return_value = {"do_lower_case": True}
        result = self.factory.from_pretrained(self.path, "positional", trust_remote_code=True,
                                             revision="revision", download_dir="cache", **kwargs)
        self.loader.assert_called_once_with("tokenization_kimi.TikTokenTokenizer", str(self.path),
                                            revision="revision", cache_dir="cache",
                                            **{k: v for k, v in kwargs.items() if k not in ("use_fast", "subfolder")})
        self.official.from_pretrained.assert_called_once_with(self.path, "positional", trust_remote_code=True,
                                                             revision="revision", cache_dir="cache", **kwargs)
        self.auto.from_pretrained.assert_not_called()
        self.encoder.assert_called_once_with(self.path, "revision")
        self.tokenizer.add_special_tokens.assert_called_once_with({"bos_token": "[bos]"})
        self.cached.assert_called_once_with(self.tokenizer)
        self.assertIs(result, self.cached.return_value)

    def test_other_profiles_keep_auto(self):
        for trust, version, model in [(False, "5.5.1", "kimi_k25"), (True, "4.57.1", "kimi_k25"),
                                      (True, "5.5.1", "other"), (1, "5.5.1", "kimi_k25")]:
            with self.subTest(trust=trust, version=version, model=model):
                self.env["transformers_version"] = version
                (self.path / "config.json").write_text(json.dumps({"model_type": model}))
                self.auto.reset_mock()
                self.factory.from_pretrained(self.path, trust_remote_code=trust, subfolder=None)
                self.loader.assert_not_called()
                self.auto.from_pretrained.assert_called_once()

    def test_invalid_target_mapping_fails_closed(self):
        for mapping in [{}, {"tokenizer_class": "Other", "auto_map": self.mapping["auto_map"]},
                        {"tokenizer_class": "TikTokenTokenizer", "auto_map": {"AutoTokenizer": ["other--tokenization_kimi.TikTokenTokenizer", None]}}]:
            with self.subTest(mapping=mapping):
                self.write_mapping(mapping)
                with self.assertRaisesRegex(ValueError, "Unexpected local Kimi"):
                    self.call()
                self.loader.assert_not_called()
                self.auto.from_pretrained.assert_not_called()
                self.cached.assert_not_called()

    def test_dynamic_loader_failure_propagates(self):
        error = ImportError("reviewed local module import failed")
        self.loader.side_effect = error
        with self.assertRaises(ImportError) as caught:
            self.call()
        self.assertIs(caught.exception, error)
        self.auto.from_pretrained.assert_not_called()
        self.official.from_pretrained.assert_not_called()
        self.cached.assert_not_called()

    def test_official_constructor_failure_propagates(self):
        error = ValueError("official constructor rejected input")
        self.official.from_pretrained.side_effect = error
        with self.assertRaises(ValueError) as caught:
            self.call()
        self.assertIs(caught.exception, error)
        self.auto.from_pretrained.assert_not_called()
        self.cached.assert_not_called()

    def test_subfolder_source_and_original_constructor_arguments(self):
        sub = self.path / "nested"
        sub.mkdir()
        for name in ["config.json", "tokenizer_config.json"]:
            (sub / name).write_bytes((self.path / name).read_bytes())
        self.call(subfolder="nested", local_files_only=True)
        self.assertEqual(self.loader.call_args.args[1], str(sub))
        self.assertNotIn("subfolder", self.loader.call_args.kwargs)
        self.assertEqual(self.official.from_pretrained.call_args.args[0], self.path)
        self.assertEqual(self.official.from_pretrained.call_args.kwargs["subfolder"], "nested")


if __name__ == "__main__":
    unittest.main()
