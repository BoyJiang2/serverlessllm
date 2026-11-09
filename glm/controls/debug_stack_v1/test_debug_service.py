import ast
import copy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import debug_service as s

ACTIVE = HERE.parents[1]/'runtime/active'
GOLD = dict(engine_config=dict(model='/models/glm', load_format='serverless_llm', tensor_parallel_size=8, max_model_len=2048),
            requests=[dict(name='fixed32', input_tokens=[1, 2], max_tokens=32, ignore_eos=True),
                      dict(name='arithmetic', input_tokens=[3, 4], max_tokens=64, ignore_eos=False)],
            results=[dict(token_ids=[5], text='x', finish_reason='length'), dict(token_ids=[6, 7], text='ok', finish_reason='stop')])


def formal_run_statements():
    tree = ast.parse((ACTIVE/'validation_control13.py').read_text())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run').body


def assigned(node, name):
    return isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)


class FormalEquivalence(unittest.TestCase):
    def test_model_config_equals_validation_control13(self):
        body = formal_run_statements()
        start = next(i for i, n in enumerate(body) if assigned(n, 'model'))
        end = next(i for i, n in enumerate(body) if assigned(n, 'config'))
        block = ast.Module(body=body[start:end+1], type_ignores=[])
        for weight_format in ('serverless_llm', 'safetensors'):
            namespace = dict(gold=copy.deepcopy(GOLD), os=types.SimpleNamespace(environ={'SLLM_SERVICE_WEIGHT_FORMAT': weight_format}))
            exec(compile(block, 'validation_control13.run', 'exec'), namespace)
            with self.subTest(weight_format=weight_format):
                self.assertEqual(s.model_config(copy.deepcopy(GOLD), weight_format), namespace['config'])

    def test_gold_request_equals_validation_control13_except_id(self):
        request = next(n for n in ast.walk(ast.Module(body=formal_run_statements(), type_ignores=[]))
                       if assigned(n, 'request') and isinstance(n.value, ast.Dict))
        for number in (0, 1):
            namespace = dict(model='glm51-fp8', case=GOLD['requests'][number], cycle=1)
            exec(compile(ast.Module(body=[request], type_ignores=[]), 'validation_control13.run', 'exec'), namespace)
            ours = s.gold_request(GOLD, number)
            with self.subTest(number=number):
                self.assertEqual({k: v for k, v in ours.items() if k != 'request_id'},
                                 {k: v for k, v in namespace['request'].items() if k != 'request_id'})
                self.assertTrue(ours['request_id'].startswith('debug-'))

    def test_store_grace_matches_formal_budget(self):
        budget = (ACTIVE/'budget_control13.py').read_text()
        self.assertIn('STORE_SHUTDOWN_GRACE=%d' % s.STORE_GRACE_SECONDS, budget)


class Helpers(unittest.TestCase):
    def test_pool_choice(self):
        restored = dict(pool_bytes=768799145984, chunk_bytes=536870912)
        self.assertEqual(s.pool_for('serverless_llm', restored), (768799145984, 536870912))
        self.assertEqual(s.pool_for('safetensors', restored), (67108864, 33554432))
        with self.assertRaises(ValueError):
            s.pool_for('fp16', restored)

    def test_gold_match(self):
        result = dict(choices=[dict(token_ids=[6, 7], message=dict(content='ok'), finish_reason='stop')])
        self.assertTrue(s.matches_gold(result, GOLD['results'][1]))
        result['choices'][0]['token_ids'] = [6, 8]
        self.assertFalse(s.matches_gold(result, GOLD['results'][1]))

    def test_cpu_list(self):
        self.assertEqual(s.cpu_list('0-3,8,10-11\n'), {0, 1, 2, 3, 8, 10, 11})


class SupplementRequests(unittest.TestCase):
    def setUp(self):
        self.gold = copy.deepcopy(GOLD)
        self.gold['requests'].append(dict(name='translation', input_tokens=[8], max_tokens=64, ignore_eos=False))
        self.gold['results'].append(dict(token_ids=[9], text='translated', finish_reason='stop'))

    def response(self, number):
        expected = self.gold['results'][number]
        return dict(choices=[dict(token_ids=expected['token_ids'], message=dict(content=expected['text']),
                                  finish_reason=expected['finish_reason'],
                                  logprobs=[{str(t): dict(logprob=-0.1)} for t in expected['token_ids']])])

    def run_requests(self, responses):
        record = dict(preparation_begin_ns=1, preparation_complete_ns=1000000001)
        phases = dict(phases={'weights_pipeline_including_allocation':dict(min_begin_to_max_end_seconds=2.0)})
        module = types.SimpleNamespace(summarize=lambda *args: phases)
        with patch.dict(sys.modules, phase_summary=module), patch.dict(s.os.environ, SLLM_EVENT_DIR='/unused'), \
             patch.object(s, 'http', side_effect=[{'status':'ok'}, *responses]) as http, \
             patch.object(s, 'write') as write:
            s.supplement_requests(self.gold, 'store_disk_cold', record)
        return record, http, write

    def test_same_run_metrics_and_arithmetic_translation_fixed32_order(self):
        record, http, _ = self.run_requests([self.response(n) for n in (1, 2, 0)])
        self.assertEqual([r['name'] for r in record['requests']], ['arithmetic', 'translation', 'fixed32'])
        self.assertEqual(record['metrics']['preparation_s'], 1.0)
        self.assertEqual(record['metrics']['phase_weights_pipeline_including_allocation_span_s'], 2.0)
        self.assertEqual(record['correct_business_response_ns'], record['requests'][0]['response_complete_ns'])
        self.assertEqual(len(http.call_args_list), 4)

    def test_wrong_gold_and_nonfinite_logprob_fail(self):
        wrong = self.response(1)
        wrong['choices'][0]['token_ids'] = [999]
        with self.assertRaisesRegex(RuntimeError, 'gold mismatch'):
            self.run_requests([wrong])
        invalid = self.response(1)
        invalid['choices'][0]['logprobs'][0]['6']['logprob'] = float('nan')
        with self.assertRaisesRegex(RuntimeError, 'invalid logprobs'):
            self.run_requests([invalid])

    def test_bad_condition_is_rejected_before_native_imports(self):
        with self.assertRaisesRegex(ValueError, 'unknown supplement'):
            s.prepare_supplement('not-a-condition', {})


if __name__ == '__main__':
    unittest.main()
