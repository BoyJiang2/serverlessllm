import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import MagicMock, Mock, call, patch

import source_plugin as plugin
import vllm_export_hook
from contract import REVISION
from loader_registry import FORMATS

ROOT = Path(__file__).parent.resolve()


class SourcePluginTests(unittest.TestCase):
    def reference_fixture(self, temp):
        spec,path=self.fixture(temp)
        identity=dict(run_id=spec['run_id'],holder=spec['holder'],code_commit=spec['code_commit'],
            spec_sha256='c'*64,source_manifest_sha256='d'*64)
        entered=datetime.now(timezone.utc)
        phases=('engine_initialize_and_export','fixed_token_business','natural_eos_business','worker_shutdown')
        schedule=dict(phase_deadlines_utc={name:(entered+timedelta(hours=1)).isoformat() for name in phases})
        return dict(spec=spec,identity=identity,prompt_token_ids=[1,2],kv_cache_memory_bytes=4096,
            schedule=schedule,entered=entered,started=time.monotonic())

    def completion(self, fixed=True):
        tokens=[10,11]*16 if fixed else [10,11]
        output=types.SimpleNamespace(token_ids=tokens,text='CPU fixture',
            logprobs=[{token:types.SimpleNamespace(logprob=-0.1)} for token in tokens],
            finish_reason='length' if fixed else 'stop',stop_reason=None)
        return [types.SimpleNamespace(finished=True,prompt_token_ids=[1,2],outputs=[output])]

    def test_reference_body_collects_ranks_then_two_distinct_business_requests_and_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp);events=[]
            core=types.SimpleNamespace(shutdown=Mock(side_effect=lambda **kw:events.append('shutdown')))
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),self.completion(False)]))
            factory=Mock(side_effect=lambda **kw:(events.append('engine') or engine))
            sampling=Mock(side_effect=lambda **kw:kw)
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',
                    side_effect=lambda *a:(events.append('ranks') or {'completed_rank_count':8})):
                result=plugin._run_source_reference(factory,sampling,**options)
            self.assertEqual(events,['engine','ranks','shutdown'])
            self.assertEqual(factory.call_args.kwargs['tensor_parallel_size'],8)
            self.assertEqual(factory.call_args.kwargs['moe_backend'],'marlin')
            self.assertNotIn('use_sequence_parallel_moe',factory.call_args.kwargs)
            self.assertIsNone(factory.call_args.kwargs['speculative_config'])
            self.assertFalse(factory.call_args.kwargs['language_model_only'])
            self.assertEqual(factory.call_args.kwargs['kv_cache_memory_bytes'],4096)
            self.assertTrue(sampling.call_args_list[0].kwargs['ignore_eos'])
            self.assertFalse(sampling.call_args_list[1].kwargs['ignore_eos'])
            self.assertEqual(sampling.call_args_list[0].kwargs['min_tokens'],32)
            self.assertEqual(len(result['business']['fixed_token_business']['token_ids']),32)
            self.assertTrue(result['engine_shutdown_returned'])
            self.assertFalse(result['gold_frozen'])
            self.assertFalse(result['resources_released'])
            self.assertFalse(result['full_service_verified'])

    def test_reference_business_failure_still_shuts_down_and_preserves_original_error(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock(side_effect=OSError('cleanup failed')))
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=RuntimeError('business failed')))
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={}):
                with self.assertRaisesRegex(RuntimeError,'business failed') as caught:
                    plugin._run_source_reference(lambda **kw:engine,lambda **kw:kw,**options)
            core.shutdown.assert_called_once()
            self.assertIn('cleanup failed',caught.exception.__notes__[0])

    def test_reference_expired_budget_refuses_constructor_without_restarting_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            options['started']-=3601
            factory=Mock()
            with self.assertRaises(TimeoutError):
                plugin._run_source_reference(factory,Mock(),**options)
            factory.assert_not_called()

    def test_reference_constructor_failure_leaves_explicit_host_cleanup_obligation(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            with self.assertRaisesRegex(RuntimeError,'initialize failed') as caught:
                plugin._run_source_reference(Mock(side_effect=RuntimeError('initialize failed')),Mock(),**options)
            self.assertIn('partial processes',caught.exception.__notes__[0])
            self.assertFalse(caught.exception.source_reference_evidence['resources_released'])

    def test_reference_second_request_failure_preserves_first_raw_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock())
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),RuntimeError('natural EOS failed')]))
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={'fixture':True}):
                with self.assertRaisesRegex(RuntimeError,'natural EOS failed') as caught:
                    plugin._run_source_reference(lambda **kw:engine,lambda **kw:kw,**options)
            evidence=caught.exception.source_reference_evidence
            self.assertEqual(evidence['business']['fixed_token_business']['token_ids'],[10,11]*16)
            self.assertTrue(evidence['engine_shutdown_returned'])
            self.assertFalse(evidence['resources_released'])

    def test_reference_shutdown_failure_after_valid_business_is_failure_with_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock(side_effect=OSError('shutdown failed')))
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),self.completion(False)]))
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={}):
                with self.assertRaisesRegex(OSError,'shutdown failed') as caught:
                    plugin._run_source_reference(lambda **kw:engine,lambda **kw:kw,**options)
            evidence=caught.exception.source_reference_evidence
            self.assertEqual(len(evidence['business']),2)
            self.assertFalse(evidence['engine_shutdown_returned'])

    def test_reference_output_rejects_degenerate_nonfinite_eos_and_unfinished_results(self):
        for kind in ('degenerate','nonfinite','early','unfinished','prompt','length','missing_logprob'):
            with self.subTest(kind=kind):
                requests=self.completion();output=requests[0].outputs[0]
                if kind=='degenerate':output.token_ids=[10]*32
                elif kind=='nonfinite':output.logprobs[0][10].logprob=float('nan')
                elif kind=='early':output.token_ids=output.token_ids[:2]
                elif kind=='unfinished':requests[0].finished=False
                elif kind=='prompt':requests[0].prompt_token_ids=[3]
                elif kind=='length':output.finish_reason='stop'
                else:output.logprobs=None
                with self.assertRaises(ValueError):plugin._reference_output(requests,[1,2],fixed=True)
        requests=self.completion(False);requests[0].outputs[0].finish_reason='length'
        with self.assertRaises(ValueError):plugin._reference_output(requests,[1,2],fixed=False)

    def test_reference_rejects_unbounded_prompt_kv_or_identity_before_constructor(self):
        for kind in ('prompt','kv','identity'):
            with tempfile.TemporaryDirectory() as temp:
                options=self.reference_fixture(temp);factory=Mock()
                if kind=='prompt':options['prompt_token_ids']=[1]*897
                elif kind=='kv':options['kv_cache_memory_bytes']=True
                else:options['identity']['holder']='e'*32
                with self.assertRaises(ValueError):plugin._run_source_reference(factory,Mock(),**options)
                factory.assert_not_called()

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in (plugin.SPEC_ENV, plugin.SHA_ENV, plugin.BUNDLE_SHA_ENV): os.environ.pop(name, None)
        state = patch.object(plugin, '_registration', None)
        state.start()
        self.addCleanup(state.stop)

    def fixture(self, temp):
        root = Path(temp)
        checkpoint, run = root / 'checkpoint', root / 'run'
        checkpoint.mkdir()
        run.mkdir()
        output = run / 'export'
        output.mkdir()
        (checkpoint / 'config.json').write_bytes((ROOT.parents[1] / 'evidence/followup-kimi-k26/config.json').read_bytes())
        contract = run / 'checkpoint-targets.json'
        contract.write_bytes((ROOT / 'checkpoint-targets.json').read_bytes())
        spec = dict(schema='kimi-source-worker-plugin-v1', node='qjhs003', mode='export',
            revision=REVISION, run_id='kimi-export-20261030T000000Z-01234567',
            holder='a' * 32, code_commit='b' * 40, checkpoint_path=str(checkpoint),
            run_root=str(run), output_root=str(output), checkpoint_contract_path=str(contract), chunk_bytes=8388608)
        path = run / 'source-spec.json'
        return spec, path

    def activate(self, spec, path):
        raw = json.dumps(spec, sort_keys=True).encode('utf-8')
        path.write_bytes(raw)
        os.environ[plugin.SPEC_ENV] = str(path)
        os.environ[plugin.SHA_ENV] = hashlib.sha256(raw).hexdigest()
        os.environ[plugin.BUNDLE_SHA_ENV] = 'd' * 64

    def registry(self):
        class Base: pass
        raw = ROOT / 'fixtures/vllm_loader_init.py'
        code = compile(raw.read_bytes(), str(raw), 'exec', dont_inherit=True)
        ns = dict(_LOAD_FORMAT_TO_MODEL_LOADER={'safetensors': object()}, BaseModelLoader=Base, logger=Mock())
        for name in ('register_model_loader', 'get_model_loader'):
            child = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)
            ns[name] = types.FunctionType(child, ns)
        return types.SimpleNamespace(**ns), Base

    def test_real_registry_registers_once_and_rejects_changed_identity_or_foreign_class(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            registry, base = self.registry()
            candidate = type('Candidate', (base,), {})
            builtin = registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors']
            with patch.object(plugin, '_verify_code_bundle', return_value={}), \
                    patch.object(plugin, '_runtime', return_value=(registry, base, object)), \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate) as factory:
                self.assertEqual(plugin.register(), FORMATS['export'])
                self.assertEqual(plugin.register(), FORMATS['export'])
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(factory.call_args.kwargs['torch_version'], '2.10.0+cu129')
                self.assertEqual(factory.call_args.kwargs['run_identity'],dict(
                    run_id=spec['run_id'],holder=spec['holder'],code_commit=spec['code_commit'],
                    spec_sha256=os.environ[plugin.SHA_ENV],source_manifest_sha256=os.environ[plugin.BUNDLE_SHA_ENV]))
                self.assertNotIn('rank_context', factory.call_args.kwargs)
                registry._LOAD_FORMAT_TO_MODEL_LOADER[FORMATS['export']] = type('Foreign', (base,), {})
                with self.assertRaisesRegex(ValueError, 'identity changed'): plugin.register()
                registry._LOAD_FORMAT_TO_MODEL_LOADER[FORMATS['export']] = candidate
                spec['holder'] = 'c' * 32
                self.activate(spec, path)
                with self.assertRaisesRegex(ValueError, 'identity changed'): plugin.register()
            self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors'], builtin)

    def test_bad_hash_identity_paths_and_bounds_fail_before_runtime_import(self):
        for kind in ('hash', 'node', 'mode', 'revision', 'chunk', 'outside', 'relative'):
            with tempfile.TemporaryDirectory() as temp:
                spec, path = self.fixture(temp)
                if kind in ('node', 'mode', 'revision'): spec[kind] = 'wrong'
                elif kind == 'chunk': spec['chunk_bytes'] = True
                elif kind == 'outside': spec['output_root'] = spec['checkpoint_path']
                elif kind == 'relative': spec['checkpoint_path'] = 'relative'
                self.activate(spec, path)
                if kind == 'hash': os.environ[plugin.SHA_ENV] = '0' * 64
                with patch.object(plugin, '_runtime') as runtime:
                    with self.assertRaises(ValueError): plugin.register()
                    runtime.assert_not_called()

    def test_cached_registration_does_not_accept_changed_config_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            registry, base = self.registry()
            candidate = type('Candidate', (base,), {})
            with patch.object(plugin, '_verify_code_bundle', return_value={}), \
                    patch.object(plugin, '_runtime', return_value=(registry, base, object)), \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate):
                plugin.register()
                (Path(spec['checkpoint_path']) / 'config.json').write_bytes(b'{}')
                with self.assertRaisesRegex(ValueError, 'config differs'): plugin.register()

    def test_disabled_or_partial_environment_cannot_register(self):
        with patch.object(plugin, '_runtime') as runtime:
            self.assertIsNone(plugin.register())
            os.environ[plugin.SPEC_ENV] = 'missing'
            with self.assertRaisesRegex(ValueError, 'both'): plugin.register()
            runtime.assert_not_called()

    def test_actual_cpu_torch_cannot_satisfy_cuda_runtime_contract(self):
        with patch.dict(sys.modules, vllm=types.SimpleNamespace(__version__='0.19.1')):
            with self.assertRaisesRegex(ValueError, 'exact vLLM'): plugin._runtime()

    def test_generated_entrypoint_loads_in_fresh_process_without_torch_or_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin.materialize_plugin_metadata(temp)
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), temp)))
            script = ("import importlib.metadata,json,sys; "
                "entries=[e for e in importlib.metadata.entry_points(group='vllm.general_plugins') "
                "if e.name=='sllm_kimi_source_candidate']; "
                "assert len(entries)==1; result=entries[0].load()(); "
                "print(json.dumps({'result':result,'torch_imported':'torch' in sys.modules}))")
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), dict(result=None, torch_imported=False))

    def test_fresh_child_receives_same_manifest_and_holder_without_model_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            script = ("import source_plugin as p,os,json; "
                "s,c,t=p._load_spec(os.environ[p.SPEC_ENV],os.environ[p.SHA_ENV]); "
                "print(json.dumps({'run_id':s['run_id'],'holder':s['holder'],'bytes':s['chunk_bytes']}))")
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                dict(run_id=spec['run_id'], holder=spec['holder'], bytes=spec['chunk_bytes']))

    def test_distribution_metadata_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = plugin.materialize_plugin_metadata(temp)
            before = (target / 'entry_points.txt').read_bytes()
            with self.assertRaises(FileExistsError): plugin.materialize_plugin_metadata(temp)
            self.assertEqual((target / 'entry_points.txt').read_bytes(), before)

    def test_growing_manifest_stream_has_a_hard_read_limit(self):
        payload = b'x' * 65537
        stream = Mock()
        stream.read.return_value = payload
        opened = MagicMock()
        opened.__enter__.return_value = stream
        with patch.object(Path, 'stat', return_value=types.SimpleNamespace(st_size=1)), \
                patch.object(Path, 'open', return_value=opened), \
                patch.object(plugin, '_runtime') as runtime:
            with self.assertRaisesRegex(ValueError, 'bounded'):
                plugin._load_spec(str(Path(tempfile.gettempdir()) / 'growing-spec.json'),
                    hashlib.sha256(payload).hexdigest())
            self.assertEqual(stream.read.call_args, call(65537))
            runtime.assert_not_called()

    def test_code_gate_failure_precedes_runtime_import_and_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            with patch.object(plugin, '_runtime') as runtime:
                # This checkout is not this spec's run_root/code: real gate refuses.
                with self.assertRaisesRegex(ValueError, 'another run'):
                    plugin.register()
                runtime.assert_not_called()
                self.assertIsNone(plugin._registration)

    def test_code_gate_is_rechecked_on_cached_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            registry, base = self.registry()
            candidate = type('Candidate', (base,), {})
            with patch.object(plugin, '_runtime', return_value=(registry, base, object)) as runtime, \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate), \
                    patch.object(plugin, '_verify_code_bundle', return_value={}) as code_gate:
                plugin.register()
                code_gate.side_effect = ValueError('source bytes changed')
                with self.assertRaisesRegex(ValueError, 'source bytes changed'):
                    plugin.register()
                self.assertEqual(runtime.call_count, 1)
                self.assertEqual(code_gate.call_count, 2)
