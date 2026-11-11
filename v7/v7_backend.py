"""Existing backend/gold runner extended for V7 Store and native Sleep L1."""
import asyncio, dataclasses, json, math, os, pathlib, time, traceback

O = pathlib.Path('/output')

def write(name, value):
    pending = O / (name + '.pending')
    with pending.open('x') as f:
        json.dump(value, f, indent=2, default=lambda x: dataclasses.asdict(x) if dataclasses.is_dataclass(x) else repr(x))
        f.write('\n')
    os.link(pending, O / name)
    pending.unlink()

async def main():
    # GLM's frozen native extension requires its bundled glog preload.
    import sllm_store.server
    from sllm_store.client import SllmStoreClient
    from sllm.backends.vllm_backend import VllmBackend
    from sllm_store.event_trace import process_identity
    import torch, vllm
    data = json.loads(pathlib.Path('/control/input.json').read_bytes())
    scenario = data.get('scenario', 'store')
    assert scenario in ('store', 'sleep_l1', 'native')
    record_only = bool(data.get('record_only'))
    if scenario == 'sleep_l1':
        assert data['config']['load_format'] == 'safetensors'
        assert data['config']['enable_sleep_mode'] is True
        assert data['config']['worker_extension_cls'] == 'sllm_store.v7_sleep_worker.V7SleepWorkerExtension'
    backend = VllmBackend('shared-' + data['name'], data['config'])
    write('constructed.json', dict(pid=os.getpid(), ns=time.monotonic_ns(), clock_identity=process_identity(), torch=torch.__version__, vllm=vllm.__version__, engine_args=dataclasses.asdict(backend.engine_args)))
    if os.environ.get('SLLM_CONSTRUCT_ONLY') == '1':
        assert not torch.cuda.is_initialized()
        write('construct-only-passed.json', dict(cuda_initialized=False))
        return
    if scenario == 'store':
        client = SllmStoreClient()
        config = client.get_server_config()
        assert config['mem_pool_size'] == 1024**4 and config['chunk_size'] == 32*1024**2
        client.channel.close()
        write('server-configuration.json', config)
    initialized = False
    try:
        await backend.init_backend()
        initialized = True
        write('ready.json', dict(ns=time.monotonic_ns()))
        await run_requests(backend, data, 'before', record_only=record_only)
        if scenario == 'sleep_l1':
            await asyncio.sleep(5)
            before = await sleep_receipt(backend, 'before')
            started = time.monotonic_ns()
            await backend.engine.sleep(level=1, mode='wait')
            write('sleep-returned.json', dict(started_ns=started, returned_ns=time.monotonic_ns()))
            assert await backend.engine.is_sleeping()
            sleeping = await sleep_receipt(backend, 'sleeping')
            validate_sleep_receipts(before, sleeping, sleeping=True)
            # The independent host observer must prove the physical memory
            # window. This hold alone is not a release qualification.
            write('sleep-observation-begin.json', dict(ns=time.monotonic_ns()))
            await asyncio.sleep(75)
            write('sleep-observation-end.json', dict(ns=time.monotonic_ns()))
            still_sleeping = await sleep_receipt(backend, 'sleeping-end')
            validate_sleep_receipts(before, still_sleeping, sleeping=True)
            started = time.monotonic_ns()
            await backend.engine.wake_up()
            write('wake-returned.json', dict(started_ns=started, returned_ns=time.monotonic_ns()))
            assert not await backend.engine.is_sleeping()
            after = await sleep_receipt(backend, 'after')
            validate_sleep_receipts(before, after, sleeping=False)
            await run_requests(backend, data, 'after', record_only=record_only)
            await asyncio.sleep(5)
    finally:
        if initialized:
            write('backend-shutdown-begin.json', dict(ns=time.monotonic_ns()))
            await backend.shutdown()
            write('normal-backend-shutdown.json', dict(ns=time.monotonic_ns()))
    write('backend-passed.json', dict(correctness=True, normal_shutdown=True,
                                     scenario=scenario, host_release_qualification='external_required'))


async def sleep_receipt(backend, phase):
    started = time.monotonic_ns()
    rows = await backend.engine.collective_rpc('sllm_v7_sleep_receipt', timeout=60)
    write('sleep-' + phase + '-receipt.json', dict(started_ns=started, received_ns=time.monotonic_ns(), ranks=rows))
    assert len(rows) == 8 and {r['rank'] for r in rows} == set(range(8))
    assert all(not r['uncovered_cuda_parameters'] for r in rows)
    assert all(r['cuda_parameter_count'] > 0 for r in rows)
    return rows


def validate_sleep_receipts(before, current, *, sleeping):
    assert len(before) == len(current) == 8
    first = {r['rank']: r for r in before}
    assert set(first) == {r['rank'] for r in current} == set(range(8))
    for row in current:
        prior = first[row['rank']]
        assert row['identity'] == prior['identity'], 'Worker identity changed'
        assert row['device_uuid'] == prior['device_uuid'], 'Worker device changed'
        assert row['parameter_signature'] == prior['parameter_signature']
        assert prior['tags']['weights']['bytes'] > 0 and prior['tags']['kv_cache']['bytes'] > 0
        assert row['tags']['weights']['bytes'] > 0 and row['tags']['kv_cache']['bytes'] > 0
        assert all(tag['cpu_backup_bytes'] == 0 for tag in prior['tags'].values())
        # Native sleep calls empty_cache: unused allocator blocks can disappear.
        # Active model parameters must retain their addresses/layout, but the
        # complete allocation dictionary need not be identical.
        for tag, current_tag in row['tags'].items():
            expected = current_tag['bytes'] if sleeping and tag == 'weights' else 0
            assert current_tag['cpu_backup_bytes'] == expected


async def run_requests(backend, data, phase, record_only=False):
    checks = []
    for item in data['requests']:
        name = phase + '-' + item['name']
        payload = dict(item['payload'], model='shared-' + data['name'], request_id=os.environ.get('SLLM_REQUEST_PREFIX', data['name']) + '-' + name)
        if item['name'] == 'v7_added_arithmetic' and data['name'] in ('kimi', 'glm'):
            tokenizer = backend.engine.get_tokenizer()
            options = dict(tokenize=True, add_generation_prompt=True)
            options['thinking' if data['name'] == 'kimi' else 'enable_thinking'] = False
            tokens = tokenizer.apply_chat_template(payload['messages'], **options)
            if not isinstance(tokens, list):
                tokens = tokens['input_ids']
            assert tokens and all(type(token) is int for token in tokens)
            payload['input_tokens'] = tokens
            write(name + '-request-encoding.json', dict(messages=payload['messages'],
                  tokenizer=type(tokenizer).__name__, options=options, input_tokens=tokens))
        started = time.monotonic_ns()
        response = await backend.generate(payload)
        # Retain the complete backend response before interpreting correctness.
        write(name + '-response.json', dict(started_ns=started, received_ns=time.monotonic_ns(), response=response))
        checks.append(check_response(item, response))
        write(name + '-checks.json', checks[-1])
        if not record_only:
            assert checks[-1]['passed'], checks[-1]
    write(phase + '-correctness-passed.json', checks)


def check_response(item, response):
    assert 'error' not in response, response
    choice = response['choices'][0]
    ids = choice.get('token_ids')
    if ids is None:
        ids = [int(next(iter(row))) for row in choice['logprobs']]
    if item['payload'].get('logprobs'):
        assert len(choice['logprobs']) == len(ids)
        for token, row in zip(ids, choice['logprobs'], strict=True):
            entry = row[token] if token in row else row[str(token)]
            assert math.isfinite(entry['logprob'] if isinstance(entry, dict) else entry.logprob)
    if item.get('validation') == 'exact_numeric_answer':
        assert item['expected_content'] == '42'
        result = dict(text=choice['message']['content'].strip() == '42',
                      completion=response['usage']['completion_tokens'] == len(ids),
                      nonempty=bool(ids), finish=choice['finish_reason'] == 'stop')
        return dict(name=item['name'], checks=result, passed=all(result.values()))
    expected = item['expected']
    result = dict(text=choice['message']['content'] == expected['content'], tokens=ids == expected['token_ids'], finish=choice['finish_reason'] == expected['finish_reason'], prompt=response['usage']['prompt_tokens'] == expected['prompt_tokens'], completion=response['usage']['completion_tokens'] == expected['completion_tokens'], actual_ids_count=len(ids) == expected['completion_tokens'])
    passed = all(result[k] for k in ('finish', 'prompt', 'completion', 'actual_ids_count')) and (item['length_only'] or all(result.values()))
    matched = 'historical' if passed else None
    alternative = item.get('expected_native_0201')
    if alternative is not None:
        # The historical gold was frozen on vLLM 0.19.1.dev1. The unified
        # 0.20.1 engine has its own native reference for the same token request;
        # both are recorded and the response must match one of them exactly.
        native = dict(
            text=choice['message']['content'] == alternative['content'],
            tokens=ids == alternative['token_ids'],
            finish=choice['finish_reason'] == alternative['finish_reason'],
            prompt=response['usage']['prompt_tokens'] == alternative['prompt_tokens'],
            completion=response['usage']['completion_tokens'] == alternative['completion_tokens'],
            actual_ids_count=len(ids) == alternative['completion_tokens'],
        )
        result['native_0201'] = native
        if not passed and all(native.values()):
            passed = True
            matched = 'native_0201'
        return dict(name=item['name'], checks=result, length_only=item['length_only'],
                    passed=passed, matched_reference=matched,
                    historical_gold_status='matched' if all(
                        result[k] for k in ('text', 'tokens', 'finish', 'prompt', 'completion')
                    ) else 'differs_on_this_engine_version')
    return dict(name=item['name'], checks=result, length_only=item['length_only'], passed=passed,
                matched_reference=matched)

if __name__ == '__main__':
    try:
        write('waiting.json', dict(pid=os.getpid(), ns=time.monotonic_ns()))
        until = time.monotonic() + 7100
        while not (O / 'start-model').exists():
            assert time.monotonic() < until, 'Backend activation deadline'
            time.sleep(.2)
        asyncio.run(main())
    except BaseException as error:
        write('failure.json', dict(error=repr(error), traceback=traceback.format_exc(), ns=time.monotonic_ns()))
        raise
