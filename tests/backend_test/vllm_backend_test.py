# ---------------------------------------------------------------------------- #
#  ServerlessLLM                                                               #
#  Copyright (c) ServerlessLLM Team 2024                                       #
#                                                                              #
#  Licensed under the Apache License, Version 2.0 (the "License");             #
#  you may not use this file except in compliance with the License.            #
#                                                                              #
#  You may obtain a copy of the License at                                     #
#                                                                              #
#                  http://www.apache.org/licenses/LICENSE-2.0                  #
#                                                                              #
#  Unless required by applicable law or agreed to in writing, software         #
#  distributed under the License is distributed on an "AS IS" BASIS,           #
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.    #
#  See the License for the specific language governing permissions and         #
#  limitations under the License.                                              #
# ---------------------------------------------------------------------------- #
import asyncio
import json
import os
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from vllm import (
    CompletionOutput,
    EmbeddingOutput,
    EmbeddingRequestOutput,
    RequestOutput,
)

from sllm.backends.vllm_backend import (
    BackendStatus,
    VllmBackend,
)


@pytest.fixture
def model_name():
    return "test-model"


@pytest.fixture
def backend_config():
    return {
        "pretrained_model_name_or_path": "test-model",
        "trace_debug": False,
        "load_format": "sharded_state",
        "torch_dtype": None,
    }


async def generate(
    inputs, sampling_params, request_id
) -> AsyncIterator[RequestOutput]:
    prompt = inputs
    tokens = [1, 2, 3]

    yield RequestOutput(
        request_id,
        prompt,
        tokens,
        None,
        [CompletionOutput(0, "test", [4], 0.5, None)],
        True,
    )
    await asyncio.sleep(1)
    yield RequestOutput(
        request_id,
        prompt,
        tokens,
        None,
        [CompletionOutput(0, "test output", [4, 5], 0.5, None)],
        True,
    )
    await asyncio.sleep(1)
    yield RequestOutput(
        request_id,
        prompt,
        tokens,
        None,
        [CompletionOutput(0, "test output result", [4, 5, 6], 0.5, None)],
        True,
    )


# Mock the encode method
async def encode(
    inputs, pooling_params, request_id
) -> AsyncIterator[EmbeddingRequestOutput]:
    # Simulate the behavior of real encode with multiple embeddings
    yield EmbeddingRequestOutput(
        request_id=request_id,
        outputs=EmbeddingOutput(embedding=[0.1, 0.2, 0.3]),
        prompt_token_ids=[101, 102, 103],
        finished=True,
    )
    await asyncio.sleep(1)
    yield EmbeddingRequestOutput(
        request_id=request_id,
        outputs=EmbeddingOutput(embedding=[0.4, 0.5, 0.6]),
        prompt_token_ids=[104, 105, 106],
        finished=True,
    )


@pytest.fixture
def async_llm_engine():
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine"
    ) as MockAsyncLLMEngine:
        async_llm_engine_obj = MockAsyncLLMEngine.return_value
        async_llm_engine_obj.from_engine_args.return_value = (
            async_llm_engine_obj
        )
        async_llm_engine_obj.abort = AsyncMock()
        async_llm_engine_obj.generate.side_effect = generate
        async_llm_engine_obj.encode.side_effect = encode
        yield async_llm_engine_obj


@pytest.fixture
def vllm_backend(model_name, backend_config, async_llm_engine):
    yield VllmBackend(model_name, backend_config)


def test_init(vllm_backend, backend_config):
    assert vllm_backend.backend_config == backend_config
    assert vllm_backend.status == BackendStatus.UNINITIALIZED


@pytest.mark.asyncio
async def test_init_backend(vllm_backend, async_llm_engine):
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()
    assert vllm_backend.status == BackendStatus.RUNNING


@pytest.mark.asyncio
async def test_generate_without_init(vllm_backend):
    request_data = {
        "model": "test-model",
        "prompt": "user: Hello",
        "request_id": "test-request-id",
    }
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=AsyncMock(),
    ):
        response = await vllm_backend.generate(request_data)
    assert "error" in response


@pytest.mark.asyncio
async def test_generate(vllm_backend, async_llm_engine):
    request_data = {
        "model": "test-model",
        "prompt": "user: Hello",
        "request_id": "test-request-id",
    }
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()
    response = await vllm_backend.generate(request_data)
    assert "error" not in response
    assert "model" in response and response["model"] == "test-model"
    assert "id" in response and response["id"] == "test-request-id"


@pytest.mark.asyncio
async def test_shutdown(model_name, backend_config, async_llm_engine):
    # Open trace debug to avoid clean the finished request in record map
    backend_config["trace_debug"] = True
    vllm_backend = VllmBackend(model_name, backend_config)
    request_data = {
        "model": "test-model",
        "prompt": "user: Hello",
        "request_id": "test-request-id",
    }
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()
    await vllm_backend.generate(request_data)
    await vllm_backend.shutdown()
    assert vllm_backend.status == BackendStatus.DELETING
    # Since open trace debug, will try to abort the already done task
    assert async_llm_engine.abort.call_count == 1


@pytest.mark.asyncio
async def test_stop(vllm_backend, async_llm_engine):
    request_data = {
        "model": "test-model",
        "prompt": "user: Hello",
        "request_id": "test-request-id",
    }
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()
    await vllm_backend.generate(request_data)
    with patch(
        "sllm.backends.vllm_backend.VllmBackend.shutdown",
        new_callable=AsyncMock,
    ) as mock_shutdown:
        await vllm_backend.stop()
        assert vllm_backend.status == BackendStatus.STOPPING
        # stop will not call abort
        assert async_llm_engine.abort.call_count == 0
        # stop will call shutdown after stop done
        assert mock_shutdown.call_count == 1


@pytest.mark.asyncio
async def test_get_current_tokens(model_name, backend_config, async_llm_engine):
    # Open trace debug to avoid clean the finished request in record map
    backend_config["trace_debug"] = True
    vllm_backend = VllmBackend(model_name, backend_config)
    request_data = [
        {
            "model": "test-model",
            "prompt": "user: Hello",
            "request_id": f"test-request-id{i}",
        }
        for i in range(3)
    ]
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()
    for request in request_data:
        await vllm_backend.generate(request)
    current_tokens = await vllm_backend.get_current_tokens()
    assert len(current_tokens) == 3


@pytest.mark.asyncio
async def test_resume_kv_cache(vllm_backend):
    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()
    vllm_backend.generate = AsyncMock()
    request_datas = [[1, 2, 3], [4, 5, 6]]
    await vllm_backend.resume_kv_cache(request_datas)
    assert vllm_backend.generate.call_count == len(request_datas)


@pytest.mark.asyncio
async def test_encode(backend_config, async_llm_engine, model_name):
    backend_config["enforce_eager"] = True
    backend_config["enable_prefix_caching"] = False
    vllm_backend = VllmBackend(model_name, backend_config)

    request_data = {"model": "test-model", "input": ["Hi, How are you?"]}

    with patch(
        "sllm.backends.vllm_backend.AsyncLLMEngine.from_engine_args",
        return_value=async_llm_engine,
    ):
        await vllm_backend.init_backend()

    result = await vllm_backend.encode(request_data)

    assert "error" not in result
    assert "data" in result
    assert len(result["data"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks,trace_debug,event_count",
    [
        ([[], [4], [4, 5]], True, 1),
        ([[], []], True, 0),
        ([[4], [4, 5]], False, 0),
    ],
)
async def test_first_token_timing(vllm_backend, chunks, trace_debug, event_count):
    vllm_backend.status = BackendStatus.RUNNING
    vllm_backend.trace_debug = trace_debug
    outputs = [
        SimpleNamespace(
            request_id="timing-request",
            prompt_token_ids=[1, 2],
            metrics=None,
            outputs=[
                SimpleNamespace(
                    text="result",
                    token_ids=tokens,
                    logprobs=None,
                    finish_reason="length",
                )
            ],
        )
        for tokens in chunks
    ]

    async def stream(*args):
        for output in outputs:
            yield output

    vllm_backend.engine = SimpleNamespace(generate=stream)
    before_trace = []
    update = vllm_backend.request_trace.update_status

    async def record_update(request_id, output):
        before_trace.append(log.call_count)
        await update(request_id, output)

    with patch("sllm.backends.vllm_backend.logger.info") as log, patch(
        "sllm.backends.vllm_backend.time.monotonic_ns", return_value=123456
    ), patch.object(vllm_backend.request_trace, "update_status", record_update):
        response = await vllm_backend.generate({"request_id": "timing-request"})
    assert log.call_count == event_count
    if event_count:
        assert json.loads(log.call_args.args[0]) == {
            "event": "backend_first_token_observed",
            "request_id": "timing-request",
            "pid": os.getpid(),
            "monotonic_ns": 123456,
        }
        assert before_trace == [0, 1, 1]
    assert response["id"] == "timing-request"
    assert response["choices"][0]["message"]["content"] == "result"
    assert response["choices"][0]["finish_reason"] == "length"
    assert response["usage"] == {
        "prompt_tokens": 2,
        "completion_tokens": len(chunks[-1]),
        "total_tokens": 2 + len(chunks[-1]),
    }
    assert await vllm_backend.request_trace.request_count() == int(trace_debug)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_token", [False, True])
async def test_first_token_timing_engine_error(vllm_backend, after_token):
    vllm_backend.status = BackendStatus.RUNNING
    vllm_backend.trace_debug = True
    error = RuntimeError("engine failed")

    async def stream(*args):
        if after_token:
            yield SimpleNamespace(outputs=[SimpleNamespace(token_ids=[4])])
        raise error

    vllm_backend.engine = SimpleNamespace(generate=stream)
    with patch("sllm.backends.vllm_backend.logger.info") as log:
        with pytest.raises(RuntimeError) as caught:
            await vllm_backend.generate({"request_id": "failed-request"})
    assert caught.value is error
    assert log.call_count == int(after_token)


@pytest.mark.asyncio
async def test_first_token_timing_concurrent_requests(vllm_backend):
    vllm_backend.status = BackendStatus.RUNNING
    vllm_backend.trace_debug = True

    async def stream(inputs, sampling_params, request_id):
        for tokens in ([], [4], [4, 5]):
            await asyncio.sleep(0)
            yield SimpleNamespace(
                request_id=request_id,
                prompt_token_ids=[1],
                metrics=None,
                outputs=[
                    SimpleNamespace(
                        text="result",
                        token_ids=tokens,
                        logprobs=None,
                        finish_reason="length",
                    )
                ],
            )

    vllm_backend.engine = SimpleNamespace(generate=stream)
    with patch("sllm.backends.vllm_backend.logger.info") as log:
        results = await asyncio.gather(
            *[
                vllm_backend.generate({"request_id": request_id})
                for request_id in ("request-a", "request-b")
            ]
        )
    events = [json.loads(call.args[0]) for call in log.call_args_list]
    assert sorted(event["request_id"] for event in events) == [
        "request-a",
        "request-b",
    ]
    assert all(
        event["event"] == "backend_first_token_observed"
        and isinstance(event["monotonic_ns"], int)
        for event in events
    )
    assert [response["id"] for response in results] == ["request-a", "request-b"]
