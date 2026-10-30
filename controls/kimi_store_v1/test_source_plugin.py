import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, call, patch

import source_plugin as plugin
import vllm_export_hook
from contract import REVISION
from loader_registry import FORMATS

ROOT = Path(__file__).parent.resolve()


class SourcePluginTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in (plugin.SPEC_ENV, plugin.SHA_ENV): os.environ.pop(name, None)
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
            with patch.object(plugin, '_runtime', return_value=(registry, base, object)), \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate) as factory:
                self.assertEqual(plugin.register(), FORMATS['export'])
                self.assertEqual(plugin.register(), FORMATS['export'])
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(factory.call_args.kwargs['torch_version'], '2.10.0+cu129')
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
            with patch.object(plugin, '_runtime', return_value=(registry, base, object)), \
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
