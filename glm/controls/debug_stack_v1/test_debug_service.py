import ast
import contextlib
import copy
import hashlib
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
    def test_normal_store_and_service_branch_matches_pre_diagnostic_source(self):
        before = subprocess.check_output(['git', 'show', 'a9e17edb6f3d9393118ba4fb1c0d8acdbfdd1bf5:glm/controls/debug_stack_v1/debug_service.py'],
                                         cwd=HERE, text=True)
        old_main = next(n for n in ast.parse(before).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        old_work = next(n for n in old_main.body if isinstance(n, ast.Try))
        first = next(i for i,n in enumerate(old_work.body) if assigned(n, 'cpus'))
        new_main = next(n for n in ast.parse((HERE/'debug_service.py').read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        new_work = next(n for n in new_main.body if isinstance(n, ast.Try))
        self.assertEqual(ast.dump(ast.Module(body=old_work.body[first:], type_ignores=[])),
                         ast.dump(ast.Module(body=new_work.body[-1].orelse, type_ignores=[])))

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
        body = work.body[-1].orelse
        index = next(i for i,n in enumerate(body) if any(isinstance(call, ast.Call)
                     and isinstance(call.func, ast.Name) and call.func.id == 'fresh_host_snapshot' for call in ast.walk(n)))
        launch = Mock()
        gate = Mock(side_effect=RuntimeError('unhealthy or missing pre-pool monitor'))
        namespace = dict(vars(s), condition='store_disk_cold', record={}, fresh_host_snapshot=gate, launch=launch)
        with self.assertRaisesRegex(RuntimeError, 'pre-pool monitor'):
            exec(compile(ast.Module(body=body[index:index+3], type_ignores=[]), '<actual-pre-pool-CPU>', 'exec'), namespace)
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


class RayStartupOnly(unittest.TestCase):
    def test_ray_flag_without_matching_registration_refuses_before_launch(self):
        env = dict(SLLM_DEBUG_WEIGHT_FORMAT='safetensors', SLLM_DEBUG_QUERY='1',
                   SLLM_SUPPLEMENT_CONDITION='safetensors_disk_cold', SLLM_STARTUP_RAY_ONLY='1')
        with patch.dict(s.os.environ, env), patch.object(s, 'supplement_registration', return_value={}), \
             patch.object(s.subprocess, 'Popen') as launch, self.assertRaisesRegex(ValueError, 'registration differ'):
            s.main()
        launch.assert_not_called()

    def test_actual_main_ray_control_never_enters_store_or_business(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as patches:
            root = Path(directory)
            gold, restore = root/'gold.json', root/'restore.json'
            gold.write_text(json.dumps(GOLD))
            restore.write_text(json.dumps(dict(pool_bytes=768799145984, chunk_bytes=536870912)))
            registration = dict(startup_only=True, startup_stage='ray', condition='safetensors_disk_cold',
                                run_id='test', holder='h', runtime_reference=dict(reference_sha256={
                                    'native-gold.json':hashlib.sha256(gold.read_bytes()).hexdigest(),
                                    'store-restore-validation.json':hashlib.sha256(restore.read_bytes()).hexdigest()}))
            nodes = [dict(Alive=True, NodeManagerAddress='172.17.0.2', NodeID='head', Resources={}),
                     dict(Alive=True, NodeManagerAddress='172.17.0.3', NodeID='worker', Resources={'GPU':8})]
            ray = types.SimpleNamespace(init=Mock(), shutdown=Mock(), nodes=lambda:nodes,
                                        get_runtime_context=lambda:types.SimpleNamespace(get_node_id=lambda:'worker'))
            process = Mock(pid=123, poll=Mock(return_value=None))
            env = dict(SLLM_DEBUG_WEIGHT_FORMAT='safetensors', SLLM_DEBUG_QUERY='1',
                       SLLM_SUPPLEMENT_CONDITION='safetensors_disk_cold', SLLM_STARTUP_RAY_ONLY='1',
                       SLLM_PRIVATE_RAY_ADDRESS='172.17.0.2:26532')
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                if path.as_posix().startswith('/proc/'):
                    return 'boot' if path.name == 'boot_id' else '123 (process) '+' '.join(['0']*19+['9'])
                if path.name == 'ray-worker.log':
                    return 'Ray runtime started.'
                return original_read(path, *args, **kwargs)
            observed = {}
            def stop(processes, environment, logs, record, strict):
                observed.update(record=record.copy(), roles=set(processes), strict=strict)
                for log in logs:
                    log.close()
                record.update(success=False, ray_control_pass=record['status'] == 'Ray startup control completed')
                return []
            for patcher in [patch.dict(s.os.environ, env), patch.dict(sys.modules, ray=ray),
                    patch.object(s, 'GOLD', gold), patch.object(s, 'RESTORE', restore), patch.object(s, 'OUT', root/'out'),
                    patch.object(s, 'supplement_registration', return_value=registration),
                    patch.object(s.pathlib.Path, 'read_text', read), patch.object(s.socket, 'gethostbyname', return_value='172.17.0.3'),
                    patch.object(s.subprocess, 'Popen', return_value=process), patch.object(s.time, 'sleep'),
                    patch.object(s.signal, 'signal'), patch.object(s.signal, 'setitimer', create=True),
                    patch.object(s.signal, 'SIGALRM', 14, create=True), patch.object(s.signal, 'ITIMER_REAL', 0, create=True),
                    patch.object(s, 'fresh_host_snapshot', return_value={'healthy':True}), patch.object(s, 'stop_processes', side_effect=stop)]:
                patches.enter_context(patcher)
            for name in ('cpu_list', 'require_resident_pool', 'prepare_supplement', 'supplement_requests', 'observe_deleted'):
                patches.enter_context(patch.object(s, name, side_effect=AssertionError('forbidden model/Store path')))
            code = s.main()
            self.assertEqual(code, 0, observed['record'].get('stop_reason'))
            self.assertEqual(observed['roles'], {'ray-worker'})
            self.assertTrue(observed['strict'])
            self.assertEqual(observed['record']['pool_bytes'], 0)
            self.assertEqual(observed['record']['status'], 'Ray startup control completed')
            self.assertNotIn('requests', observed['record'])
            self.assertTrue(observed['record']['ray_only'])


class ShutdownEvidence(unittest.TestCase):
    class Process:
        def __init__(self, code, pid):
            self.code, self.returncode, self.waited, self.pid = code, None, 0, pid
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

    def run_cleanup(self, sllm_code=0, ray_code=0, unexpected_ray=False, ray_timeout=False):
        processes = {name:self.Process(sllm_code if name == 'sllm' else ray_code if name == 'ray-worker' else 0, 100+i)
                     for i,name in enumerate(('sllm','store','ray-worker'))}
        record = dict(status='supplement business and instance deletion completed',
                      child_births={name:dict(pid=p.pid, startticks=9) for name,p in processes.items()})
        original_read = Path.read_text
        def read(path, **kwargs):
            if path.as_posix().startswith('/proc/'):
                return '100 (helper) '+' '.join(['0']*19+['9'])
            return original_read(path, **kwargs)
        with tempfile.TemporaryDirectory() as temp, patch.object(s, 'OUT', Path(temp)), \
             patch.object(s.pathlib.Path, 'read_text', read), patch.object(s, 'write') as saved, \
             patch.object(s.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ray stop',30) if ray_timeout else None,
                          return_value=types.SimpleNamespace(returncode=0, stdout='Did not find any active Ray processes.', stderr='')) as stop:
            (Path(temp)/'sllm.log').write_text('Application shutdown complete.\nFinished server process [100]\n')
            (Path(temp)/'ray-worker.log').write_text('Some Ray subprocesses exited unexpectedly:' if unexpected_ray else 'Ray runtime started.')
            s.stop_processes(processes, {}, [], record, strict=True)
            saved.assert_called_once()
            self.assertNotIn('--force', stop.call_args.args[0])
        return record, processes

    def test_auxiliary_abnormal_exit_is_not_hidden_by_store_zero(self):
        for code, expected in [(0, True), (-15, False), (15, False), (17, False), (-9, False)]:
            with self.subTest(code=code):
                record, _ = self.run_cleanup(sllm_code=code)
                self.assertEqual(record['success'], expected)

    def test_ray_only_shutdown_pass_is_separate_from_full_service(self):
        for unexpected in (False, True):
            process = self.Process(1, 123)
            record = dict(ray_only=True, status='Ray startup control completed',
                          child_births={'ray-worker':dict(pid=123, startticks=9)})
            with self.subTest(unexpected=unexpected), tempfile.TemporaryDirectory() as temp, \
                 patch.object(s, 'OUT', Path(temp)), patch.object(s, 'write'), \
                 patch.object(s.pathlib.Path, 'read_text', return_value='123 (ray) '+' '.join(['0']*19+['9'])), \
                 patch.object(s.subprocess, 'run', return_value=types.SimpleNamespace(
                     returncode=0, stdout='Did not find any active Ray processes.', stderr='')):
                (Path(temp)/'ray-worker.log').write_text('Some Ray subprocesses exited unexpectedly:' if unexpected else 'Ray runtime started.')
                s.stop_processes({'ray-worker':process}, {}, [], record, strict=True)
                self.assertEqual(record['ray_control_pass'], not unexpected)
                self.assertFalse(record['success'])
                self.assertFalse(record['http_graceful_shutdown_evidence'])

    def test_ray_hook_one_is_conditional_and_http_exits_before_ray_signal(self):
        record, _ = self.run_cleanup(ray_code=1)
        self.assertTrue(record['success'])
        self.assertTrue(record['ray_cli_controlled_hook_evidence'])
        self.assertEqual(record['signal_requests']['sllm']['signal'], s.signal.SIGINT)
        self.assertLessEqual(record['signal_requests']['sllm']['exit_observed_ns'],
                             record['signal_requests']['ray-worker']['requested_ns'])
        failed, _ = self.run_cleanup(ray_code=1, unexpected_ray=True)
        self.assertFalse(failed['success'])
        self.assertFalse(failed['ray_cli_controlled_hook_evidence'])

    def test_ray_stop_timeout_still_waits_all_children_and_saves_failure(self):
        record, processes = self.run_cleanup(ray_timeout=True)
        self.assertFalse(record['success'])
        self.assertEqual(record['shutdown_errors'][0]['stage'], 'ray stop')
        self.assertTrue(all(p.waited == 1 for p in processes.values()))

    def test_matching_config_with_insufficient_rss_is_rejected(self):
        client = types.SimpleNamespace(get_server_config=lambda:dict(mem_pool_size=768799145984, chunk_size=536870912))
        with patch.dict(sys.modules, listener_control13=types.SimpleNamespace(listener=None),
                        store_numa_exec_v13=types.SimpleNamespace(audit_store_threads=None)), \
             patch.object(s.pathlib.Path, 'read_text', side_effect=['1 (store) '+' '.join(['0']*22), 'VmRSS: 1024 kB\n']):
            with self.assertRaisesRegex(RuntimeError, 'residency not established'):
                s.require_resident_pool(client, types.SimpleNamespace(pid=1), 768799145984, 536870912, {0})


if __name__ == '__main__':
    unittest.main()
