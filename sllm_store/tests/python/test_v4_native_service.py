"""HTTP observation and frozen response contracts only; no Ray/Store/model runtime."""
import asyncio
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import v4_native_service as service
from test_v4_state import identity


class HttpResponse:
    def __init__(self, status, body):
        self.status_code=status
        self.text=body
    def raise_for_status(self):
        if self.status_code!=200:raise RuntimeError('HTTP fixture failure')
    def json(self):return json.loads(self.text)


class NativeServiceContracts(unittest.TestCase):
    def test_registration_keeps_the_approved_profile(self):
        config=service.registration('/cpu/store',identity())
        backend=config['backend_config']
        self.assertEqual((config['num_gpus'],backend['tensor_parallel_size']),(8,8))
        self.assertEqual(backend['kv_cache_dtype'],'fp8')
        self.assertEqual((backend['seed'],backend['distributed_executor_backend']),(20261105,'mp'))
        self.assertEqual(config['auto_scaling_config'],dict(min_instances=0,max_instances=1))
        self.assertEqual(backend['model_loader_extra_config']['store_path'],'/cpu/store')

    def test_default_plan_does_not_start_service(self):
        output=io.StringIO()
        with mock.patch('sys.argv',['service','head']),mock.patch.object(service,'run_head') as run,contextlib.redirect_stdout(output):
            service.main()
        self.assertFalse(json.loads(output.getvalue())['gpu_or_model_started'])
        run.assert_not_called()

    def test_http_json_status_and_transport_failures_keep_original_observation(self):
        for kind in ('status','json','transport'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'raw.json'
                response=HttpResponse(500 if kind=='status' else 200,
                                      'not json' if kind=='json' else '{"error":"CPU fixture"}')
                client=mock.Mock(post=mock.AsyncMock(return_value=response))
                if kind=='transport':client.post.side_effect=RuntimeError('transport fixture')
                with mock.patch.object(service.time,'monotonic_ns',return_value=100):
                    with self.assertRaises((RuntimeError,json.JSONDecodeError)):
                        asyncio.run(service.observed_post(client,'/fixture',{'model':'cpu'},path,10**15))
                raw=json.loads(path.read_bytes())
                self.assertEqual(raw['received_ns'],100)
                self.assertEqual(raw['body'],None if kind=='transport' else response.text)
                self.assertEqual(raw['status'],None if kind=='transport' else response.status_code)

    def test_exact_responses_and_input_count_are_independently_checked(self):
        gold=json.loads((service.ROOT/'docs/evidence/v4-native-reference-success/successful-run/native-gold.json').read_bytes())
        for request in gold['requests']:
            purpose=request['purpose'];expected=gold['responses'][purpose]
            actual=copy.deepcopy(expected)
            actual['usage']={'prompt_tokens':len(request['input_tokens'])}
            self.assertTrue(all(service.validate_response(actual,expected,request).values()))
            for kind in ('tokens','text','input_count'):
                invalid=copy.deepcopy(actual)
                if kind=='tokens':invalid['choices'][0]['token_ids'][0]+=1
                elif kind=='text':invalid['choices'][0]['message']['content']='different'
                else:invalid['usage']['prompt_tokens']+=1
                with self.subTest(purpose=purpose,kind=kind),self.assertRaises(ValueError):
                    service.validate_response(invalid,expected,request)


if __name__=='__main__':unittest.main()
