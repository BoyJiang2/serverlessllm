import ast
import copy
import json
from pathlib import Path
import sys
import types
import unittest
import subprocess
import tempfile
from unittest.mock import Mock, patch

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
    def test_actual_pre_pool_fragment_does_not_launch_store_after_monitor_failure(self):
        tree = ast.parse((HERE/'debug_service.py').read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        work = next(n for n in main.body if isinstance(n, ast.Try))
        index = next(i for i,n in enumerate(work.body) if any(isinstance(call, ast.Call)
                     and isinstance(call.func, ast.Name) and call.func.id == 'fresh_host_snapshot' for call in ast.walk(n)))
        launch = Mock()
        gate = Mock(side_effect=RuntimeError('unhealthy or missing pre-pool monitor'))
        namespace = dict(vars(s), condition='store_disk_cold', record={}, fresh_host_snapshot=gate, launch=launch)
        with self.assertRaisesRegex(RuntimeError, 'pre-pool monitor'):
            exec(compile(ast.Module(body=work.body[index:index+3], type_ignores=[]), '<actual-pre-pool-CPU>', 'exec'), namespace)
        launch.assert_not_called()

    def test_pre_pool_gate_requires_healthy_receipt_and_exact_worker(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(sys, 'path', [str(ACTIVE), *sys.path]):
            root = Path(temp)
            (root/'stack.json').write_text(json.dumps(dict(run_id='run', holder='holder', worker_container_id='c'*64)))
            row = dict(sample_begin_ns=100, last_sample_ns=150, sample_end_ns=160, run_id='run', boot_id='boot',
                       observers_ok=True, history_ok=True, first_monitor_error=None, foreign_gpu_pids=[],
                       registered_store_helper=None, worker_identity=dict(container_id='c'*64))
            path = root/'supplement-monitor-ready.json'
            path.write_text(json.dumps(row))
            record = dict(run_id='run', boot_id='boot', holder='holder')
            with patch.object(s, 'RUN', root), patch.object(s.socket, 'gethostname', return_value='c'*12), \
                 patch.object(s.time, 'monotonic_ns', return_value=200):
                self.assertEqual(s.fresh_host_snapshot(record, 100, require_helper=False), row)
                row['worker_identity']['container_id'] = 'd'*64
                path.write_text(json.dumps(row))
                with patch.object(s.time, 'monotonic', side_effect=[0, 0, 16]), patch.object(s.time, 'sleep'):
                    with self.assertRaisesRegex(TimeoutError, 'bound host monitor'):
                        s.fresh_host_snapshot(record, 100, require_helper=False)
                row['observers_ok'] = False
                path.write_text(json.dumps(row))
                with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
                    s.fresh_host_snapshot(record, 100, require_helper=False)

    def test_auxiliary_baseline_is_same_run_identity_not_empty_gpu(self):
        launch = dict(pid=12, pid_namespace='pid:[123]', start_ticks='9', boot_id='boot', monotonic_ns=10)
        helper = dict(host_pid=101, namespace_pid=12, pid_namespace='pid:[123]', start_ticks='9', boot_id='boot',
                      container_id='c'*64, cgroup='0::/own\n', uid=2147)
        snapshot = dict(sample_begin_ns=100, last_sample_ns=150, sample_end_ns=160, run_id='run', boot_id='boot',
                        observers_ok=True, history_ok=True, first_monitor_error=None, foreign_gpu_pids=[],
                        registered_store_helper=helper, gpu_pids=[101],
                        worker_identity=dict(container_id='c'*64, cgroup='0::/own\n'),
                        gpu='\n'.join(f'{i}, GPU-{i}, 143771 MiB, {100+i} MiB, 0 %' for i in range(8)))
        record = dict(run_id='run', boot_id='boot', resident_pool_identity=launch)
        with patch.object(sys, 'path', [str(ACTIVE), *sys.path]), \
             patch.object(s, 'fresh_host_snapshot', return_value=snapshot), patch.object(s, 'write'), \
             patch.object(s.time, 'monotonic_ns', return_value=200):
            s.auxiliary_baseline(record)
            self.assertEqual(record['auxiliary_gpu_baseline_MiB'], list(range(100, 108)))
            snapshot['gpu_pids'] = [101, 999]
            with self.assertRaisesRegex(RuntimeError, 'unknown process retained'):
                s.auxiliary_baseline(record)

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

    def test_connected_store_with_wrong_config_is_rejected(self):
        client = types.SimpleNamespace(get_server_config=lambda:dict(mem_pool_size=64*1024**2, chunk_size=32*1024**2))
        with patch.dict(sys.modules, listener_control13=types.SimpleNamespace(listener=None),
                        store_numa_exec_v13=types.SimpleNamespace(audit_store_threads=None)):
            with self.assertRaisesRegex(RuntimeError, 'configuration differs'):
                s.require_resident_pool(client, types.SimpleNamespace(pid=1), 768799145984, 536870912, {0})


class ShutdownEvidence(unittest.TestCase):
    class Process:
        def __init__(self, code):
            self.code, self.returncode, self.waited = code, None, 0
        def poll(self):
            return self.returncode
        def terminate(self):
            pass
        def send_signal(self, signum):
            pass
        def wait(self, timeout):
            self.waited += 1
            self.returncode = self.code
            return self.code

    def test_auxiliary_abnormal_exit_is_not_hidden_by_store_zero(self):
        for code, expected in [(0, True), (-15, True), (17, False), (-9, False)]:
            processes = {n:self.Process(code if n == 'sllm' else 0) for n in ('sllm', 'store', 'ray-worker')}
            record = dict(status='supplement business and instance deletion completed')
            with self.subTest(code=code), patch.object(s.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)), \
                 patch.object(s, 'write') as saved:
                s.stop_processes(processes, {}, [], record, strict=True)
            self.assertEqual(record['success'], expected)
            saved.assert_called_once()

    def test_ray_stop_timeout_still_waits_all_children_and_saves_failure(self):
        processes = {n:self.Process(0) for n in ('sllm', 'store', 'ray-worker')}
        record = dict(status='supplement business and instance deletion completed')
        with patch.object(s.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ray stop', 30)), \
             patch.object(s, 'write') as saved:
            s.stop_processes(processes, {}, [], record, strict=True)
        self.assertFalse(record['success'])
        self.assertEqual(record['shutdown_errors'][0]['stage'], 'ray stop')
        self.assertTrue(all(p.waited == 1 for p in processes.values()))
        saved.assert_called_once()

    def test_matching_config_with_insufficient_rss_is_rejected(self):
        client = types.SimpleNamespace(get_server_config=lambda:dict(mem_pool_size=768799145984, chunk_size=536870912))
        with patch.dict(sys.modules, listener_control13=types.SimpleNamespace(listener=None),
                        store_numa_exec_v13=types.SimpleNamespace(audit_store_threads=None)), \
             patch.object(s.pathlib.Path, 'read_text', side_effect=['1 (store) '+' '.join(['0']*22), 'VmRSS: 1024 kB\n']):
            with self.assertRaisesRegex(RuntimeError, 'residency not established'):
                s.require_resident_pool(client, types.SimpleNamespace(pid=1), 768799145984, 536870912, {0})


if __name__ == '__main__':
    unittest.main()
