"""V11 backend runner: same frozen requests as V7, plus a first-token timestamp.

Runs inside the backend container. Reuses the V7 helpers (`/control/v7_lib.py`) for
response shaping and gold checks, but drives the engine generator directly so that
`t3.5` (first token) is an observed event rather than an inference.
"""
import asyncio
import json
import os
import pathlib
import sys
import time
import traceback

sys.path.insert(0, '/control')
import v7_lib as lib

from vllm import SamplingParams
from vllm.inputs import TokensPrompt

O = pathlib.Path('/output')
write = lib.write


def check_response(item, response):
    return lib.check_response(item, response)


def assemble(final, model_name, return_token_ids, prompt_tokens):
    """Same response shape the V7 backend produced, built from the final output."""
    outputs = final.outputs
    choices = []
    for index, output in enumerate(outputs):
        choice = dict(index=index,
                      message=dict(role='assistant', content=output.text),
                      logprobs=output.logprobs,
                      finish_reason=output.finish_reason)
        if return_token_ids:
            choice['token_ids'] = list(output.token_ids)
            choice['stop_reason'] = getattr(output, 'stop_reason', None)
        choices.append(choice)
    completion = sum(len(output.token_ids) for output in outputs)
    return dict(id=final.request_id, object='chat.completion', created=0,
                model=model_name, choices=choices,
                usage=dict(prompt_tokens=prompt_tokens, completion_tokens=completion,
                           total_tokens=prompt_tokens + completion))


def build_inputs(backend, payload):
    input_tokens = payload.get('input_tokens')
    messages = payload.get('messages')
    if input_tokens is not None:
        return TokensPrompt(prompt_token_ids=input_tokens)
    if payload.get('prompt') is not None:
        return payload['prompt']
    if messages is not None:
        # Mirror the V7 backend: V4 uses its native chat template, the other
        # models fall back to a plain role-prefixed prompt.
        if getattr(backend, 'is_v4', False):
            tokenizer = backend.engine.get_tokenizer()
            tokens = tokenizer.apply_chat_template(
                messages, tokenize=True, enable_thinking=False, drop_thinking=True)
            if not isinstance(tokens, list):
                tokens = tokens['input_ids']
            return TokensPrompt(prompt_token_ids=tokens)
        return "\n".join(f"{m['role']}: {m['content']}"
                         for m in messages if 'content' in m)
    raise ValueError('request carries neither input_tokens, prompt nor messages')


def sampling_params(payload):
    fields = {key: payload[key] for key in
              ('temperature', 'seed', 'max_tokens', 'ignore_eos', 'logprobs')
              if key in payload}
    return SamplingParams(**fields)


async def run_requests_streaming(backend, data, phase, record_only=False):
    checks = []
    for item in data['requests']:
        name = phase + '-' + item['name']
        payload = dict(item['payload'], model='shared-' + data['name'],
                       request_id=os.environ.get('SLLM_REQUEST_PREFIX', data['name']) + '-' + name)
        if item['name'] == 'v7_added_arithmetic' and data['name'] in ('kimi', 'glm'):
            tokenizer = backend.engine.get_tokenizer()
            options = dict(tokenize=True, add_generation_prompt=True)
            options['thinking' if data['name'] == 'kimi' else 'enable_thinking'] = False
            tokens = tokenizer.apply_chat_template(payload['messages'], **options)
            if not isinstance(tokens, list):
                tokens = tokens['input_ids']
            assert tokens and all(type(token) is int for token in tokens)
            payload['input_tokens'] = tokens
            write(name + '-request-encoding.json',
                  dict(messages=payload['messages'], tokenizer=type(tokenizer).__name__,
                       options=options, input_tokens=tokens))
        prompt = build_inputs(backend, payload)
        params = sampling_params(payload)
        started = time.monotonic_ns()
        first = None
        final = None
        async for output in backend.engine.generate(prompt, params, payload['request_id']):
            if first is None and output.outputs and output.outputs[0].token_ids:
                first = time.monotonic_ns()
                write(name + '-first-token.json',
                      dict(first_token_ns=first, started_ns=started,
                           ttft_seconds=(first - started) / 1e9))
            final = output
        received = time.monotonic_ns()
        assert final is not None
        prompt_tokens = len(final.prompt_token_ids) if getattr(final, 'prompt_token_ids', None) \
            else len(payload.get('input_tokens') or [])
        response = assemble(final, payload['model'], payload.get('return_token_ids', False),
                            prompt_tokens)
        write(name + '-response.json',
              dict(started_ns=started, first_token_ns=first, received_ns=received, response=response))
        checks.append(check_response(item, response))
        write(name + '-checks.json', checks[-1])
        if not record_only:
            assert checks[-1]['passed'], checks[-1]
    write(phase + '-correctness-passed.json', checks)


def build_backend_config(data):
    """Copy the backend config, materialising the KV transfer config if given.

    JSON cannot carry a KVTransferConfig object, so B1 inputs provide a plain
    dict under `v11_kv_transfer_config` and it is built here.
    """
    config = dict(data['config'])
    raw = config.pop('v11_kv_transfer_config', None)
    if raw is not None:
        from vllm.config import KVTransferConfig
        config['kv_transfer_config'] = KVTransferConfig(**raw)
    return config


async def main():
    import sllm_store.server  # GLM's frozen native extension preload
    from sllm.backends.vllm_backend import VllmBackend
    from sllm_store.event_trace import process_identity
    import torch
    import vllm

    data = json.loads(pathlib.Path('/control/input.json').read_bytes())
    scenario = data.get('scenario', 'store')
    assert scenario in ('store', 'native', 'sleep_l1', 'sleep_l2'), scenario
    record_only = bool(data.get('record_only'))
    backend = VllmBackend('shared-' + data['name'], build_backend_config(data))
    write('constructed.json', dict(pid=os.getpid(), ns=time.monotonic_ns(),
                                   clock_identity=process_identity(),
                                   torch=torch.__version__, vllm=vllm.__version__))
    initialized = False
    try:
        await backend.init_backend()
        initialized = True
        write('ready.json', dict(ns=time.monotonic_ns()))
        await run_requests_streaming(backend, data, 'before', record_only)
        if scenario == 'sleep_l1':
            await asyncio.sleep(5)
            before = await sleep_receipt(backend)
            started = time.monotonic_ns()
            await backend.engine.sleep(level=1, mode='wait')
            write('sleep-returned.json', dict(started_ns=started, returned_ns=time.monotonic_ns()))
            assert await backend.engine.is_sleeping()
            sleeping = await sleep_receipt(backend)
            validate_sleep(before, sleeping, sleeping=True)
            write('sleep-observation-begin.json', dict(ns=time.monotonic_ns()))
            await asyncio.sleep(75)
            write('sleep-observation-end.json', dict(ns=time.monotonic_ns()))
            still = await sleep_receipt(backend)
            validate_sleep(before, still, sleeping=True)
            started = time.monotonic_ns()
            await backend.engine.wake_up()
            write('wake-returned.json', dict(started_ns=started, returned_ns=time.monotonic_ns()))
            assert not await backend.engine.is_sleeping()
            after = await sleep_receipt(backend)
            validate_sleep(before, after, sleeping=False)
            await run_requests_streaming(backend, data, 'after', record_only)
        elif scenario == 'sleep_l2':
            await sleep_l2_cycle(backend, before=None)
            await run_requests_streaming(backend, data, 'after', record_only)
    finally:
        if initialized:
            write('backend-shutdown-begin.json', dict(ns=time.monotonic_ns()))
            await backend.shutdown()
            write('normal-backend-shutdown.json', dict(ns=time.monotonic_ns()))
    write('backend-passed.json', dict(correctness=True, normal_shutdown=True, scenario=scenario))


async def sleep_receipt(backend):
    started = time.monotonic_ns()
    rows = await backend.engine.collective_rpc('sllm_v7_sleep_receipt', timeout=60)
    write('sleep-receipt.json', dict(started_ns=started, received_ns=time.monotonic_ns(), ranks=rows))
    assert len(rows) == 8 and {row['rank'] for row in rows} == set(range(8))
    assert all(not row['uncovered_cuda_parameters'] for row in rows)
    assert all(row['cuda_parameter_count'] > 0 for row in rows)
    return rows


def validate_sleep(before, current, *, sleeping):
    assert len(before) == len(current) == 8
    first = {row['rank']: row for row in before}
    assert set(first) == {row['rank'] for row in current}
    for row in current:
        prior = first[row['rank']]
        assert row['identity'] == prior['identity'], 'Worker identity changed'
        assert row['device_uuid'] == prior['device_uuid']
        assert row['parameter_signature'] == prior['parameter_signature']
        for tag, current_tag in row['tags'].items():
            expected = current_tag['bytes'] if sleeping and tag == 'weights' else 0
            assert current_tag['cpu_backup_bytes'] == expected


async def sleep_l2_cycle(backend, before):
    """Level 2: drop weights and KV, then reload weights from the native checkpoint."""
    started = time.monotonic_ns()
    await backend.engine.sleep(level=2, mode='wait')
    write('sleep2-returned.json', dict(started_ns=started, returned_ns=time.monotonic_ns(),
                                       level=2))
    assert await backend.engine.is_sleeping()
    wake = time.monotonic_ns()
    await backend.engine.wake_up(tags=['weights'])
    write('wake2-weights-returned.json', dict(started_ns=wake, returned_ns=time.monotonic_ns()))
    reload_started = time.monotonic_ns()
    await backend.engine.reload_weights()
    write('reload-weights-returned.json', dict(started_ns=reload_started,
                                              returned_ns=time.monotonic_ns()))
    kv = time.monotonic_ns()
    await backend.engine.wake_up(tags=['kv_cache'])
    write('wake2-kv-returned.json', dict(started_ns=kv, returned_ns=time.monotonic_ns()))
    assert not await backend.engine.is_sleeping()


if __name__ == '__main__':
    try:
        write('waiting.json', dict(pid=os.getpid(), ns=time.monotonic_ns()))
        until = time.monotonic() + 7100
        while not (O / 'start-model').exists():
            assert time.monotonic() < until, 'Backend activation deadline'
            time.sleep(.2)
        asyncio.run(main())
    except BaseException as error:
        write('failure.json', dict(error=repr(error), traceback=traceback.format_exc(),
                                   ns=time.monotonic_ns()))
        raise
