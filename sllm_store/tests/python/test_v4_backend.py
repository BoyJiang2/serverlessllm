"""CPU backend/async contracts with actual source and controlled engine outputs.

No real Torch, vLLM, regex, Ray, native extension or model is imported. The
pinned V4 encoder's encode path uses stdlib only; parser regex paths are unused.
"""

import ast
import asyncio
import copy
from dataclasses import dataclass, fields
import gc
import hashlib
import inspect
import json
import os
from pathlib import Path
import runpy
import time
import types
from typing import Any, Dict, List, Optional, Sequence, Union, cast
import unittest
from unittest import mock
import uuid


@dataclass
class EngineArgs:
    model: str = ""
    dtype: str = "auto"
    load_format: str = "auto"
    enforce_eager: bool = False
    enable_prefix_caching: bool = False
    tokenizer_mode: str = "auto"


class Sampling:
    def __init__(self, **kwargs):
        allowed = {"temperature", "top_p", "top_k", "min_tokens", "max_tokens",
                   "ignore_eos", "seed", "stop", "presence_penalty",
                   "frequency_penalty", "repetition_penalty"}
        if set(kwargs) - allowed:
            raise ValueError("unknown sampling argument")
        self.kwargs = kwargs


class Output:
    def __init__(self, request_id):
        self.request_id = request_id
        self.prompt_token_ids = [0, 7, 8]
        self.metrics = None
        self.outputs = [types.SimpleNamespace(text="4", token_ids=[19, 1],
                                             logprobs=None, finish_reason="stop",
                                             stop_reason=1)]


class Tokenizer:
    vocab_size = 129280

    def __init__(self):
        self.calls = []

    def get_added_vocab(self):
        return {"<｜begin▁of▁sentence｜>": 0}

    def encode(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return [0, 7, 8]  # Stand-in IDs: this is not a BPE equality test.


class Engine:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.fail_after_output = False
        self.abort_ids = []
        self.shutdown_count = 0
        self.inputs = None
        self.closed_ids = []

    def get_tokenizer(self):
        return self.tokenizer

    async def generate(self, inputs, sampling, request_id):
        self.inputs = inputs
        self.started.set()
        try:
            if self.block:
                await self.release.wait()
            yield Output(request_id)
            if self.fail_after_output:
                raise RuntimeError("engine stream failed")
        finally:
            self.closed_ids.append(request_id)

    async def abort(self, request_id):
        self.abort_ids.append(request_id)

    def shutdown(self):
        self.shutdown_count += 1


class V4BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(__file__).resolve().parents[3]
        self.fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        manifest = json.loads((self.fixtures / "vllm0201_backend_sources.json").read_text())
        for row in manifest["files"]:
            if row["fixture"]:
                data = (self.fixtures / row["fixture"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
        encoded_source = (self.fixtures / "vllm0201_v4_encoding.py.txt").read_text()
        # regex is used only by decoder functions; leave its import unexecuted.
        nodes = [node for node in ast.parse(encoded_source).body
                 if not isinstance(node, ast.Import)
                 or not any(alias.name == "regex" for alias in node.names)]
        encoder = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-v4-encoder>", "exec"), encoder)
        source = (self.fixtures / "vllm0201_v4_tokenizer.py.txt").read_text()
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "get_deepseek_v4_tokenizer"]
        namespace = dict(copy=copy, HfTokenizer=Tokenizer, Any=Any,
                         encode_messages=encoder["encode_messages"])
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-v4-tokenizer>", "exec"), namespace)
        tokenizer = namespace["get_deepseek_v4_tokenizer"](Tokenizer())
        self.engine = Engine(tokenizer)
        utils = runpy.run_path(str(root / "sllm/backends/backend_utils.py"))
        source = (root / "sllm/backends/vllm_backend.py").read_text()
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        factory = types.SimpleNamespace(from_engine_args=lambda _: self.engine)
        names = dict(asyncio=asyncio, gc=gc, inspect=inspect, os=os, time=time,
                     uuid=uuid, fields=fields, Any=Any, Dict=Dict, List=List,
                     Optional=Optional, Sequence=Sequence, Union=Union, cast=cast,
                     AsyncEngineArgs=EngineArgs, AsyncLLMEngine=factory,
                     RequestOutput=Output, EmbeddingRequestOutput=Output,
                     SamplingParams=Sampling, PoolingParams=object, PromptType=str,
                     TokensPrompt=lambda **kwargs: kwargs, Counter=object,
                     logger=mock.Mock(), torch=types.SimpleNamespace(
                         cuda=types.SimpleNamespace(is_available=lambda: False)),
                     BackendStatus=utils["BackendStatus"], SllmBackend=utils["SllmBackend"])
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<actual-backend>", "exec"), names)
        self.torch = names["torch"]
        self.cls, self.status = names["VllmBackend"], utils["BackendStatus"]
        self.config = dict(load_format="safetensors", tokenizer_mode="deepseek_v4",
                           pretrained_model_name_or_path="cpu-fixture", enforce_eager=True,
                           enable_prefix_caching=False)
        self.backend = self.cls("v4-pro", self.config)
        await self.backend.init_backend()
        self.frozen = json.loads((self.fixtures / "v4_business_request.json").read_text())

    def request(self, request_id="business-1"):
        return dict(model="v4-pro", messages=copy.deepcopy(self.frozen["messages"]),
                    request_id=request_id, **self.frozen["sampling"])

    async def cancel(self, *tasks):
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_v4_uses_pinned_tokenizer_encoding_and_preserves_request(self):
        request = self.request()
        before = copy.deepcopy(request)
        result = await self.backend.generate(request)
        self.assertEqual(result["choices"][0]["message"]["content"], "4")
        self.assertEqual(self.engine.inputs, {"prompt_token_ids": [0, 7, 8]})
        prompt, kwargs = self.engine.tokenizer.calls[-1]
        self.assertEqual(prompt, (self.fixtures / "v4_business_prompt.txt").read_text())
        self.assertEqual(kwargs, {"add_special_tokens": False})
        self.assertEqual(request, before)

    async def test_trace_contains_pending_request_before_first_token(self):
        self.engine.block = True
        task = asyncio.create_task(self.backend.generate(self.request()))
        try:
            await self.engine.started.wait()
            self.assertEqual(await self.backend.request_trace.request_count(), 1)
        finally:
            self.engine.release.set()
            await self.cancel(task)

    async def test_shutdown_cancels_pre_first_token_and_waits_for_task_exit(self):
        self.engine.block = True
        task = asyncio.create_task(self.backend.generate(self.request()))
        try:
            await self.engine.started.wait()
            await asyncio.wait_for(self.backend.shutdown(), 0.5)
            self.assertTrue(task.done())
            self.assertIn("business-1", self.engine.abort_ids)
            self.assertEqual(self.engine.shutdown_count, 1)
            self.assertEqual(await self.backend.request_trace.request_count(), 0)
        finally:
            await self.cancel(task)

    async def test_stream_failure_cleans_active_trace_and_propagates_error(self):
        self.engine.fail_after_output = True
        with self.assertRaisesRegex(RuntimeError, "engine stream failed"):
            await self.backend.generate(self.request())
        self.assertEqual(await self.backend.request_trace.request_count(), 0)

    async def test_trace_debug_completed_history_does_not_block_stop(self):
        self.backend.trace_debug = True
        await self.backend.generate(self.request())
        await asyncio.wait_for(self.backend.stop(), 0.2)
        self.assertIsNone(self.backend.engine)
        self.assertEqual(self.engine.shutdown_count, 1)

    async def test_token_ids_and_stop_reason_are_available_for_separate_validation(self):
        request = self.request()
        request["return_token_ids"] = True
        result = await self.backend.generate(request)
        self.assertNotIn("error", result)
        choice = result["choices"][0]
        self.assertEqual(choice["token_ids"], [19, 1])
        self.assertEqual(choice["stop_reason"], 1)
        self.assertEqual(choice["finish_reason"], "stop")

    async def test_duplicate_active_request_id_is_rejected(self):
        self.engine.block = True
        first = asyncio.create_task(self.backend.generate(self.request()))
        second = None
        try:
            await self.engine.started.wait()
            second = asyncio.create_task(self.backend.generate(self.request()))
            result = await asyncio.wait_for(asyncio.shield(second), 0.2)
            self.assertIn("error", result)
        finally:
            await self.cancel(*(task for task in (first, second) if task))

    async def test_legacy_messages_keep_existing_role_prefix_format(self):
        self.backend = self.cls("other-model", dict(self.config, tokenizer_mode="auto"))
        await self.backend.init_backend()
        await self.backend.generate(dict(messages=[{"role": "user", "content": "Hello"}]))
        self.assertEqual(self.engine.inputs, "user: Hello")

    async def test_legacy_debug_history_remains_available_and_aborted_on_shutdown(self):
        config = dict(self.config, tokenizer_mode="auto", trace_debug=True)
        self.backend = self.cls("other-model", config)
        await self.backend.init_backend()
        await self.backend.generate(self.request())
        self.assertEqual(await self.backend.get_current_tokens(), [[0, 7, 8, 19, 1]])
        await self.backend.shutdown()
        self.assertEqual(self.engine.abort_ids, ["business-1"])

    async def test_cancel_during_trace_update_closes_suspended_generator(self):
        blocked = asyncio.Event()
        release = asyncio.Event()
        original = self.backend.request_trace.update_status
        async def update(request_id, output):
            if isinstance(output, Output):
                blocked.set()
                await release.wait()
            await original(request_id, output)
        self.backend.request_trace.update_status = update
        task = asyncio.create_task(self.backend.generate(self.request()))
        try:
            await blocked.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(self.engine.closed_ids, ["business-1"])
            self.assertEqual(await self.backend.request_trace.request_count(), 0)
        finally:
            release.set()
            await self.cancel(task)

    async def test_concurrent_shutdown_waiters_share_completion_even_after_caller_cancel(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def shutdown():
            started.set()
            await release.wait()
            self.engine.shutdown_count += 1
        self.engine.shutdown = shutdown
        first = asyncio.create_task(self.backend.shutdown())
        await started.wait()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(self.backend.shutdown())
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            self.assertIs(self.backend.engine, self.engine)
            release.set()
            await asyncio.wait_for(second, 0.5)
            self.assertIsNone(self.backend.engine)
            self.assertEqual(self.engine.shutdown_count, 1)
        finally:
            release.set()
            await self.cancel(first, second)

    async def test_native_shutdown_failure_is_not_silently_treated_as_deleted(self):
        self.engine.shutdown = mock.Mock(side_effect=RuntimeError("shutdown failed"))
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
                await self.backend.shutdown()
        self.assertIs(self.backend.engine, self.engine)
        self.engine.shutdown.assert_called_once()

    async def test_abort_error_does_not_skip_engine_shutdown_or_report_success(self):
        self.engine.block = True
        self.engine.abort = mock.AsyncMock(side_effect=RuntimeError("abort failed"))
        task = asyncio.create_task(self.backend.generate(self.request()))
        try:
            await self.engine.started.wait()
            with self.assertRaisesRegex(RuntimeError, "Request abort failed"):
                await self.backend.shutdown()
            self.assertTrue(task.done())
            self.assertIsNone(self.backend.engine)
            self.assertEqual(self.engine.shutdown_count, 1)
        finally:
            await self.cancel(task)

    async def test_v4_shutdown_does_not_probe_frontend_cuda(self):
        self.torch.cuda.is_available = mock.Mock(side_effect=AssertionError("CUDA probe"))
        await self.backend.shutdown()
        self.torch.cuda.is_available.assert_not_called()


if __name__ == "__main__":
    unittest.main()
