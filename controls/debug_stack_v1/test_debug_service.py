import ast
import copy
from pathlib import Path
import sys
import types
import unittest

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


if __name__ == '__main__':
    unittest.main()
