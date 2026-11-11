"""CPU validation of host load-call hooks in the actual target runtime."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sllm_store.v7_trace import register
from sllm_store.vllm_v4_loader import V4GroupedStoreLoader
from vllm.model_executor.model_loader.default_loader import DefaultModelLoader


class TraceTest(unittest.TestCase):
    def test_host_boundaries_and_registration_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, dict(SLLM_EVENT_DIR=directory, SLLM_EVENT_RUN_ID="v7-cpu")), \
                    mock.patch.object(DefaultModelLoader, "load_model", lambda *a: "native"), \
                    mock.patch.object(V4GroupedStoreLoader, "load_model", lambda *a: "grouped"), \
                    mock.patch("vllm.distributed.get_tensor_model_parallel_rank", return_value=2):
                register()
                first = DefaultModelLoader.load_model
                register()
                self.assertIs(DefaultModelLoader.load_model, first)
                self.assertEqual(first(None, None, None), "native")
                self.assertEqual(V4GroupedStoreLoader.load_model(None, None, None), "grouped")
            events = [json.loads(line) for p in Path(directory).glob("*.jsonl") for line in p.read_text().splitlines()]
            self.assertEqual([r["name"] for r in events], ["full_weight_load_begin", "full_weight_load_end"] * 2)
            self.assertEqual({r["rank"] for r in events}, {2})

    def test_conflicting_hook_is_rejected(self):
        def other(*args):
            return None
        other._sllm_trace_name = "other"
        with mock.patch.dict(os.environ, dict(SLLM_EVENT_DIR="/unused")), \
                mock.patch.object(DefaultModelLoader, "load_model", other):
            with self.assertRaises(ValueError):
                register()

    def test_disabled_tracing_leaves_methods_unchanged(self):
        before = DefaultModelLoader.load_model
        with mock.patch.dict(os.environ, dict(SLLM_EVENT_DIR="")):
            register()
        self.assertIs(before, DefaultModelLoader.load_model)


if __name__ == "__main__":
    unittest.main()
