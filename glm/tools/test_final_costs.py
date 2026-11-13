import unittest
import json
import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
from unittest.mock import patch

from final_costs import counters, pss_summary, span, historical_monitor
from historical_costs import loading_rank_pair, c23_controller_audit, finalize_controller


class CostsTests(unittest.TestCase):
    def test_controller_finalizer_uses_kernel_exit_and_retains_failed_exit(self):
        run = 'sllm-debug-20261109T110000Z-abcdef12'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'debug-service/cache-admission').mkdir(parents=True)
            def write(name, value):
                (root/name).write_text(json.dumps(value), encoding='utf8')
            write('boot', 'unused')
            (root/'boot').write_text('boot')
            unit = dict(Id=run+'-controller.service', Result='success', ExecMainCode='1', ExecMainStatus='0',
                        MainPID='0', InvocationID='a'*32, ExecMainStartTimestampMonotonic='1000000',
                        ExecMainExitTimestampMonotonic='200000000')
            identity = dict(run_id=run, holder='b'*32, boot_id='boot')
            write('controller-start.json', dict(identity, condition='store_disk_cold', controller_begin_ns=10**9,
                                                independent_controller_unit=dict(InvocationID='a'*32),
                                                resource_admission=dict(gpu_devices=[dict(uuid=f'GPU-{i}') for i in range(8)])))
            write('controller-result.json', dict(identity, success=True, release_observation_s=60,
                  all_owned_containers_removed_ns=119*10**9, controller_end_ns=190*10**9))
            write('stack.json', dict(identity, source=dict(code_commit='c'*40)))
            requests = [dict(name='case'+str(i), begin_ns=begin, response_complete_ns=end,
                             request_id='request'+str(i), gold_match=True)
                        for i,begin,end in [(1,4500000000,5*10**9),(2,6*10**9,7*10**9),(0,8*10**9,9*10**9)]]
            phase_names = ('device_setup_including_communication','communication_groups',
                           'weights_pipeline_including_allocation','weight_ingestion','quantization_postprocess',
                           'kv_cache_allocation','compile_graph_and_warmup')
            phase = dict(ranks={str(i):dict(begin_ns=4100000000,end_ns=4400000000) for i in range(8)},
                         min_begin_to_max_end_seconds=.3)
            metrics = dict(preparation_s=1, command_to_correct_business_response_s=1,
                           trigger_to_correct_business_response_s=.5)
            metrics.update({'phase_'+name+'_span_s':.3 for name in phase_names})
            worker = dict(identity, success=True, condition='store_disk_cold',
                  preparation_begin_ns=2*10**9, preparation_complete_ns=3*10**9, startup_command_ns=4*10**9,
                  business_trigger_ns=4500000000, correct_business_response_ns=5*10**9,
                  delete_complete_ns=49*10**9, idle_observation_seconds=60, metrics=metrics, requests=requests)
            write('debug-service/result.json', worker)
            gold = dict(requests=[dict(name='case'+str(i)) for i in range(3)],
                        results=[dict(token_ids=[1], text='ok', finish_reason='stop')]*3)
            write('gold.json', gold)
            for i in range(3):
                write('debug-service/case'+str(i)+'-response.json', dict(choices=[dict(token_ids=[1],
                      message=dict(content='ok'), finish_reason='stop', logprobs=[{'1':dict(logprob=-.5)}])]))
            write('debug-service/cache-admission/summary.json', dict(passed=True, condition='store_disk_cold',
                  files=[dict(pages=1,resident_pages=0)]))
            phases = dict(begin_ns=4*10**9,end_ns=5*10**9,request_id='request1',first_token_observed_ns=4800000000,
                          control_events=dict(router_instance_published=dict(monotonic_ns=4400000000)),
                          phases={name:phase for name in phase_names})
            write('debug-service/phases.json', phases)
            write('debug-service/measurement-progress.json', dict(requests=requests))
            released = dict(run_id=run, boot_id='boot',
                            gpu='\n'.join(f'{i}, GPU-{i}, 0 MiB' for i in range(8)), gpu_processes='',
                            containers={}, private_slice_memory_events='oom 0\noom_kill 0',
                            retained_cgroups={role:dict(absent=True) for role in ('head','worker')})
            for name,first in [('release60.jsonl',120),('debug-service/idle60.jsonl',50)]:
                extras = released if name == 'release60.jsonl' else {}
                (root/name).write_text(''.join(json.dumps(dict(extras, monotonic_ns=(first+i)*10**9))+'\n' for i in range(61)))
            def remote(argv, **kwargs):
                script = kwargs['input'].decode().replace(
                    "pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8/debug')/run", 'pathlib.Path('+repr(str(root))+')')
                script = script.replace('/proc/sys/kernel/random/boot_id', (root/'boot').as_posix()).replace(
                    '/data/brick1/boyjiang/glm5.1-fp8/validation/native-gold.json', (root/'gold.json').as_posix()).replace(
                    '9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0',
                    hashlib.sha256((root/'gold.json').read_bytes()).hexdigest())
                output = io.StringIO()
                with contextlib.redirect_stdout(output), patch('subprocess.check_output', return_value='\n'.join(
                        k+'='+v for k,v in unit.items())):
                    exec(compile(script, '<CPU-finalizer-fixture>', 'exec'), {})
                return type('Result', (), dict(stdout=output.getvalue().encode()))()
            with patch('subprocess.run', side_effect=remote), contextlib.redirect_stdout(io.StringIO()):
                finalize_controller(run)
                good = json.loads((root/'controller-final.json').read_text())
                self.assertTrue(good['success'], good.get('failure'))
                self.assertEqual(good['metrics']['controller_s'], 199)
                saved = (root/'controller-final.json').read_bytes()
                verified = io.StringIO()
                with contextlib.redirect_stdout(verified):
                    finalize_controller(run, verify_only=True)
                self.assertEqual(json.loads(verified.getvalue())['final_sha256'], hashlib.sha256(saved).hexdigest())
                self.assertEqual((root/'controller-final.json').read_bytes(), saved)
                write('controller-final.json', dict(good, metrics=dict(good['metrics'], controller_s=198)))
                with self.assertRaisesRegex(RuntimeError, 'independently recomputed'):
                    finalize_controller(run, verify_only=True)
                cases = [
                    ('debug-service/result.json', dict(worker, delete_complete_ns=55*10**9)),
                    ('controller-result.json', dict(identity, success=True, release_observation_s=60,
                         all_owned_containers_removed_ns=125*10**9, controller_end_ns=190*10**9)),
                    ('debug-service/result.json', dict(worker, metrics=dict(preparation_s=1))),
                    ('debug-service/phases.json', dict(phases, phases={name:dict(phase, ranks={str(i):{} for i in range(8)})
                                                                     for name in phase_names})),
                ]
                for name,invalid in cases:
                    original = (root/name).read_bytes()
                    (root/'controller-final.json').unlink()
                    write(name, invalid)
                    finalize_controller(run)
                    self.assertFalse(json.loads((root/'controller-final.json').read_text())['success'])
                    (root/name).write_bytes(original)
                (root/'controller-final.json').unlink()
                release = root/'release60.jsonl'
                original_release = release.read_bytes()
                rows = [json.loads(line) for line in original_release.splitlines()]
                rows[30]['gpu'] = rows[30]['gpu'].replace('GPU-0, 0 MiB', 'GPU-0, 1 MiB')
                release.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                finalize_controller(run)
                self.assertFalse(json.loads((root/'controller-final.json').read_text())['success'])
                release.write_bytes(original_release)
                (root/'controller-final.json').unlink()
                unit['ExecMainStatus'] = '1'
                finalize_controller(run)
                failed = json.loads((root/'controller-final.json').read_text())
                self.assertFalse(failed['success'])
                self.assertIsNone(failed['metrics'])
                (root/'controller-final.json').unlink()
                unit['ExecMainStatus'] = '0'
                (root/'release60.jsonl').write_text(''.join(json.dumps(dict(monotonic_ns=120*10**9+i*999000000))+'\n'
                                                           for i in range(61)))
                finalize_controller(run)
                short = json.loads((root/'controller-final.json').read_text())
                self.assertFalse(short['success'])
                self.assertIsNone(short['metrics'])

    def test_real_archive_cannot_split_shared_controller_into_conditions(self):
        result = c23_controller_audit()
        self.assertFalse(result['controller_recoverable_by_condition'])
        self.assertEqual(len(result['c23_attempts']), 26)
        self.assertTrue(all(r['controller_elapsed_s'] is None for r in result['c23_attempts']))
        self.assertEqual(result['shared_parents']['head']['child_returncode'], 1)
        self.assertGreaterEqual(result['shared_final_release_observation_s'], 60)

    def test_loading_pair_missing_duplicate_or_wrong_process(self):
        first = dict(name='weights_pipeline_including_allocation_begin', rank=0,
                     pid=12, container_hostname='owned', monotonic_ns=10)
        last = dict(first, name='weights_pipeline_including_allocation_end', monotonic_ns=20)
        pair, error = loading_rank_pair([first, last], 0, 1, 30)
        self.assertEqual(pair, (first, last))
        self.assertIsNone(error)
        self.assertNotIn('process_start_ticks', pair[0])
        self.assertIsNone(loading_rank_pair([first], 0, 1, 30)[0])
        self.assertIsNone(loading_rank_pair([first, first, last], 0, 1, 30)[0])
        self.assertIsNone(loading_rank_pair([first, dict(last, pid=13)], 0, 1, 30)[0])
        self.assertIsNone(loading_rank_pair([first, dict(last, monotonic_ns=9)], 0, 1, 30)[0])

    def test_historical_windows_and_missing_gpu(self):
        def row(t, value):
            gpu = None if value is None else '\n'.join(
                f'{i}, GPU-{i}, 100 MiB, {value+i} MiB, 0 %' for i in range(8))
            return json.dumps(dict(monotonic_ns=t, gpu=gpu,
                                   cgroup_memory_current=t, foreign_gpu_pids=[])).encode()+b'\n'
        out = historical_monitor([row(1, 0), row(2, None), row(3, 20), b'bad'],
                                 [dict(begin_ns=1, end_ns=2), dict(begin_ns=2, end_ns=3)])
        self.assertEqual(out['whole']['samples'], 3)
        self.assertEqual(out['whole']['missing_gpu'], 1)
        self.assertEqual(out['windows'][0]['summary']['gpu_peak_MiB'], list(range(8)))
        self.assertEqual(out['windows'][1]['summary']['gpu_peak_MiB'], list(range(20, 28)))
        self.assertEqual(out['malformed_line_numbers'], [4])
        missing = historical_monitor([row(2, None)], [])
        self.assertEqual(missing['whole']['gpu_peak_MiB'], [None]*8)
        with self.assertRaises(AssertionError):
            historical_monitor([row(3, 0), row(1, 0)], [])

    def test_counter_units_and_span(self):
        self.assertEqual(counters('Pss: 7 kB\nLocked: 0 kB\n'), dict(Pss=7, Locked=0))
        self.assertEqual(span(100, 1000000100), 1)
        with self.assertRaises(AssertionError):
            span(2, 1)

    def test_unreadable_is_not_zero(self):
        text = '00400000-7fff ---p 00000000 00:00 0 [rollup]\nRss: 9 kB\nPss: 7 kB\nLocked: 0 kB\nPss_Shmem: 1 kB\n'
        snapshot = dict(store_pid=8, processes=[dict(pid=8, smaps_rollup=text), dict(pid=9, smaps_rollup=None)])
        out = pss_summary(snapshot)
        self.assertIsNone(out['all_readable_sum'])
        self.assertEqual(out['store']['Pss_bytes'], 7*1024)
        snapshot['processes'].pop()
        self.assertEqual(pss_summary(snapshot)['all_readable_sum']['Rss_bytes'], 9*1024)


if __name__ == '__main__':
    unittest.main()
