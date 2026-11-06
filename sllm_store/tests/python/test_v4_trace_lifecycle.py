"""GLM event boundaries on actual backend/router/grouped-loader CPU fixtures."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from sllm_store import event_trace as trace
import test_v4_backend as backend_fixture
import test_v4_grouped_loader as grouped_fixture
import test_v4_startup as startup_fixture


def setup_trace(case):
    temporary = tempfile.TemporaryDirectory()
    case.addCleanup(temporary.cleanup)
    patch = mock.patch.dict(os.environ, SLLM_EVENT_DIR=temporary.name, SLLM_EVENT_RUN_ID="cpu-lifecycle")
    patch.start()
    case.addCleanup(patch.stop)
    trace._reset_process()
    return Path(temporary.name)


def rows(path):
    return [json.loads(line) for file in path.glob("*.jsonl") for line in file.read_text().splitlines()]


class BackendTraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = setup_trace(self)
        self.h = backend_fixture.V4BackendTests("runTest")
        await self.h.asyncSetUp()
        self.addCleanup(self.h.doCleanups)

    async def asyncTearDown(self):
        await self.h.backend.shutdown()

    async def test_first_token_means_first_nonempty_ids_and_is_emitted_once(self):
        async def generate(inputs, sampling, request_id):
            empty = backend_fixture.Output(request_id)
            empty.outputs[0].token_ids = []
            empty.outputs[0].text = ""
            yield empty
            yield backend_fixture.Output(request_id)
            yield backend_fixture.Output(request_id)
        self.h.engine.generate = generate
        trigger = time.monotonic_ns()
        response = await self.h.backend.generate(self.h.request("trace-business"))
        received = time.monotonic_ns()
        data = rows(self.path)
        first = [r for r in data if r["name"] == "backend_first_token_observed"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["request_id"], "trace-business")
        self.assertTrue(trigger <= first[0]["monotonic_ns"] <= received)
        self.assertEqual(response["choices"][0]["message"]["content"], "4")
        self.assertEqual(len([r for r in data if r["name"] == "backend_generation_complete"]), 1)
        self.assertEqual(len([r for r in data if r["name"] == "backend_engine_ready"]), 1)

    async def test_stream_failure_retains_first_token_but_has_no_complete_event(self):
        self.h.engine.fail_after_output = True
        with self.assertRaisesRegex(RuntimeError, "stream failed"):
            await self.h.backend.generate(self.h.request("failed-business"))
        names = [r["name"] for r in rows(self.path)]
        self.assertIn("backend_first_token_observed", names)
        self.assertIn("backend_generation_failed", names)
        self.assertNotIn("backend_generation_complete", names)
        self.assertFalse(self.h.backend._active_requests)

    async def test_empty_stream_never_fabricates_a_first_token(self):
        async def empty(*args):
            if False:
                yield
        self.h.engine.generate = empty
        with self.assertRaisesRegex(RuntimeError, "without output"):
            await self.h.backend.generate(self.h.request("empty-business"))
        names = [r["name"] for r in rows(self.path)]
        self.assertNotIn("backend_first_token_observed", names)
        self.assertIn("backend_generation_failed", names)

    async def test_constructor_failure_and_repeated_shutdown_do_not_emit_false_ready(self):
        self.h.namespace["AsyncLLMEngine"].from_engine_args = mock.Mock(side_effect=RuntimeError("create failed"))
        other = self.h.cls("v4-pro", self.h.config)
        with self.assertRaisesRegex(RuntimeError, "create failed"):
            await other.init_backend()
        await self.h.backend.shutdown()
        await self.h.backend.shutdown()
        names = [r["name"] for r in rows(self.path)]
        self.assertEqual(names.count("backend_engine_ready"), 1)
        self.assertEqual(names.count("backend_engine_create_failed"), 1)
        self.assertEqual(names.count("backend_shutdown_begin"), 1)
        self.assertEqual(names.count("backend_shutdown_end"), 1)


class RouterTraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = setup_trace(self)
        self.h = startup_fixture.StartupContracts("runTest")
        await self.h.asyncSetUp()
        self.addCleanup(self.h.doCleanups)

    async def asyncTearDown(self):
        await self.h.asyncTearDown()

    async def test_publication_event_occurs_only_after_the_ready_pool_move(self):
        namespace = self.h.router._start_instance.__globals__
        def observe(name, **values):
            self.assertTrue(self.h.instance.ready)
            self.assertIn("cold", self.h.router.ready_inference_instances)
            self.assertNotIn("cold", self.h.router.starting_inference_instances)
            trace.record_event(name, **values)
        with mock.patch.dict(namespace, record_event=observe):
            await self.h.router._start_instance("cold")
        data = rows(self.path)
        self.assertEqual([r["name"] for r in data], ["router_instance_published"])
        self.assertEqual(data[0]["instance_id"], "cold")

    async def test_trace_write_failure_restores_nonready_owner_for_existing_cleanup(self):
        namespace = self.h.router._start_instance.__globals__
        def failure(*args, **kwargs):
            raise OSError("trace storage failed")
        with mock.patch.dict(namespace, record_event=failure):
            with self.assertRaisesRegex(OSError, "trace storage failed"):
                await self.h.router._start_instance("cold")
        self.assertFalse(self.h.instance.ready)
        self.assertNotIn("cold", self.h.router.ready_inference_instances)
        self.assertIs(self.h.router.starting_inference_instances["cold"], self.h.instance)
        self.assertIs(self.h.instance.backend_instance, self.h.actor)


class GroupedTraceTests(unittest.TestCase):
    def setUp(self):
        self.path = setup_trace(self)
        self.h = grouped_fixture.GroupedLoaderTests("runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.h.populate()

    def test_group_copy_and_native_postprocess_have_separate_host_boundaries(self):
        self.h.restore()
        data = rows(self.path)
        copies = [r for r in data if r["name"] == "grouped_store_copy_end"]
        native = [r for r in data if r["name"] == "grouped_expert_postprocess_host_begin"]
        self.assertEqual(len(copies), 62)
        self.assertEqual(len(native), 61)
        self.assertEqual(copies[0]["group_id"], "remainder")
        for copy, post in zip(copies[1:], native):
            self.assertEqual(copy["group_id"], post["group_id"])
            self.assertLessEqual(copy["monotonic_ns"], post["monotonic_ns"])
        self.assertFalse(self.h.loader.grouped_restore_contexts())

    def test_failed_group_records_failure_without_a_postprocess_end(self):
        self.h.fail_at = "model.layers.2.experts"
        with self.assertRaisesRegex(RuntimeError, "postprocess failed"):
            self.h.restore()
        data = [r for r in rows(self.path) if r.get("group_id") == "expert_0002"]
        names = [r["name"] for r in data]
        self.assertIn("grouped_expert_postprocess_host_failed", names)
        self.assertNotIn("grouped_expert_postprocess_host_end", names)
        self.assertTrue(self.h.loader.grouped_restore_contexts()[0]["failed"])
