from pathlib import Path
import hashlib
import json
import os
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch

from loader_registry import FORMATS, register_candidate_loader
from marlin_aux import PROFILE
import test_checkpoint_export
import test_cpu_tensor_io
import test_vllm_native_hook
import vllm_export_hook as hook


class ExportLifecycleTests(unittest.TestCase):
    def identity(self):
        return dict(run_id='kimi-export-20261031T000000Z-01234567',holder='a'*32,
            code_commit='b'*40,spec_sha256='c'*64,source_manifest_sha256='d'*64)

    def fixture(self, temp, events, context=None, identity=None):
        root = Path(temp) / 'checkpoint'
        root.mkdir()
        output = Path(temp) / 'output'
        output.mkdir()
        native_loader, config, model_config, namespace = test_vllm_native_hook.NativeLifecycleTests().setup_fixture(root, events)
        base = type(native_loader).__bases__[0]
        default = type(test_checkpoint_export.CheckpointExportTests().fixture(root)[0])
        config.load_config.load_format = FORMATS['export']
        config.load_config.model_loader_extra_config = {}
        if context is None:
            context = dict(rank=0, tp_size=8, pp_size=1, device_id=0)
        with patch.object(hook, 'load_targets', return_value={'packed'}), \
                patch.object(hook, '_checkpoint_manifest', return_value=(root, set(), [])):
            cls = hook.make_checkpoint_export_loader(base, default, checkpoint_path=root, output_root=output,
                config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=b'fixture', profile=PROFILE,
                vllm_version='0.19.1', torch_version='2.10.0', rank_context=lambda: dict(context),
                chunk_bytes=7, run_identity=identity)
        return cls(config.load_config), config, model_config, base, output

    def test_bound_completion_is_published_only_after_backend_postprocess(self):
        events=[]
        with tempfile.TemporaryDirectory() as temp:
            loader,config,model_config,_,output=self.fixture(temp,events,identity=self.identity())
            def convert(directory,*args,**kwargs):
                args[1].packed.data[0]=-1
                directory.mkdir()
                (directory/'tensor_index.json').write_bytes(b'{}')
                (directory/'kimi_raw_state.json').write_bytes(b'{}')
                self.assertFalse((directory/'source-complete.json').exists())
                return dict(native_index_sha256=hashlib.sha256(b'{}').hexdigest())
            with patch.object(hook,'_require_rank_device'), \
                    patch.object(hook,'convert_checkpoint_rank',side_effect=convert):
                loader.load_model(config,model_config)
            row=json.loads((output/'rank-0/source-complete.json').read_bytes())
            self.assertEqual(events,['initialize','quant_postprocess','attention_postprocess'])
            self.assertEqual(row['run_id'],self.identity()['run_id'])
            self.assertEqual(row['status'],'base_load_model_returned')
            self.assertFalse(row['runtime_admitted'])
            self.assertFalse(row['tp_mapping_verified'])

    def test_bound_export_failure_never_publishes_success(self):
        with tempfile.TemporaryDirectory() as temp:
            loader,config,model_config,_,output=self.fixture(temp,[],identity=self.identity())
            with patch.object(hook,'_require_rank_device'), \
                    patch.object(hook,'convert_checkpoint_rank',side_effect=RuntimeError('export failed')):
                with self.assertRaises(RuntimeError):loader.load_model(config,model_config)
            self.assertFalse((output/'rank-0/source-complete.json').exists())
            self.assertEqual(loader.lifecycle_status,'failed')

    def test_postprocess_failure_keeps_export_but_has_no_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            loader,config,model_config,_,output=self.fixture(temp,[],identity=self.identity())
            def convert(directory,*args,**kwargs):
                directory.mkdir()
                (directory/'tensor_index.json').write_bytes(b'{}')
                return dict(native_index_sha256=hashlib.sha256(b'{}').hexdigest())
            # Existing fixed Base fixture rejects packed[0] != -1 during postprocess.
            with patch.object(hook,'_require_rank_device'), \
                    patch.object(hook,'convert_checkpoint_rank',side_effect=convert):
                with self.assertRaises(AssertionError):loader.load_model(config,model_config)
            self.assertTrue((output/'rank-0/tensor_index.json').exists())
            self.assertFalse((output/'rank-0/source-complete.json').exists())
            self.assertEqual(loader.lifecycle_status,'failed')

    def rank_receipts(self,root):
        for rank in range(8):
            directory=root/('rank-'+str(rank));directory.mkdir()
            for name in ('tensor_index.json','kimi_raw_state.json'):
                (directory/name).write_bytes(b'{}')
            row=dict(self.identity(),schema='kimi-source-rank-completion-v1',rank=rank,device_id=rank,
                status='base_load_model_returned',sidecar_sha256=hashlib.sha256(b'{}').hexdigest(),
                index_sha256=hashlib.sha256(b'{}').hexdigest(),load_model_elapsed_seconds=1.0,
                runtime_admitted=False,tp_mapping_verified=False)
            (directory/'source-complete.json').write_text(json.dumps(row),encoding='utf-8')

    def test_collect_all_eight_metadata_receipts_without_payload_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.rank_receipts(root)
            receipt=hook.collect_source_rank_receipts(root,self.identity())
            self.assertEqual(receipt['completed_rank_count'],8)
            self.assertTrue(receipt['metadata_verified'])
            self.assertFalse(receipt['checkpoint_payload_verified'])
            self.assertFalse(receipt['native_loader_verified'])
            self.assertFalse(receipt['runtime_admitted'])

    def test_collection_rejects_missing_stale_corrupt_duplicate_or_partial(self):
        for failure in ('missing','stale','corrupt','duplicate','partial','index_partial','sidecar_partial',
                        'config_partial','oversize','nan'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.rank_receipts(root)
                directory=root/'rank-7';path=directory/'source-complete.json'
                row=json.loads(path.read_bytes())
                if failure=='missing':path.unlink()
                elif failure=='stale':row['holder']='e'*32;path.write_text(json.dumps(row))
                elif failure=='corrupt':(directory/'kimi_raw_state.json').write_bytes(b'changed')
                elif failure=='duplicate':row['device_id']=0;path.write_text(json.dumps(row))
                elif failure=='partial':(directory/'source-complete.json.partial').write_bytes(b'x')
                elif failure=='index_partial':(directory/'tensor_index.json.partial').write_bytes(b'x')
                elif failure=='sidecar_partial':(directory/'kimi_raw_state.json.partial').write_bytes(b'x')
                elif failure=='config_partial':(directory/'config.json.partial').write_bytes(b'x')
                elif failure=='oversize':path.write_bytes(b'x'*4097)
                else:row['load_model_elapsed_seconds']=float('nan');path.write_text(json.dumps(row))
                with self.assertRaises((ValueError,FileNotFoundError)):
                    hook.collect_source_rank_receipts(root,self.identity())

    def test_nonregular_metadata_fd_refused_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'metadata';path.write_bytes(b'{}')
            from types import SimpleNamespace
            with patch.object(hook.os,'fstat',return_value=SimpleNamespace(st_mode=0)), \
                    patch.object(hook.os,'fdopen') as wrapper:
                with self.assertRaisesRegex(ValueError,'regular metadata'):
                    hook._metadata_stream(path)
                wrapper.assert_not_called()

    @unittest.skipUnless(hasattr(os,'mkfifo'),'FIFO probe requires POSIX; descriptor rejection tested separately')
    def test_fifo_receipt_does_not_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.rank_receipts(root)
            path=root/'rank-0/source-complete.json';path.unlink();os.mkfifo(path)
            with self.assertRaisesRegex(ValueError,'regular metadata'):
                hook.collect_source_rank_receipts(root,self.identity())

    def test_archive_pending_cleanup_prevents_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            loader,config,model_config,_,output=self.fixture(temp,[],identity=self.identity())
            def convert(directory,source,model,*args,**kwargs):
                directory.mkdir();model.packed.data[0]=-1
                return dict(cleanup_pending=['config.json.partial'])
            with patch.object(hook,'_require_rank_device'), \
                    patch.object(hook,'convert_checkpoint_rank',side_effect=convert):
                with self.assertRaisesRegex(ValueError,'cleanup pending'):
                    loader.load_model(config,model_config)
            self.assertEqual(loader.lifecycle_status,'failed')
            self.assertFalse((output/'rank-0/source-complete.json').exists())

    def test_completion_partial_unlink_failure_is_not_loader_success(self):
        with tempfile.TemporaryDirectory() as temp:
            loader,config,model_config,_,output=self.fixture(temp,[],identity=self.identity())
            def convert(directory,source,model,*args,**kwargs):
                directory.mkdir();model.packed.data[0]=-1
                (directory/'tensor_index.json').write_bytes(b'{}')
                (directory/'kimi_raw_state.json').write_bytes(b'{}')
                return dict(native_index_sha256=hashlib.sha256(b'{}').hexdigest())
            original=Path.unlink
            def fail_partial(path,*args,**kwargs):
                if path.name=='source-complete.json.partial':raise OSError('fixture unlink failure')
                return original(path,*args,**kwargs)
            with patch.object(hook,'_require_rank_device'), \
                    patch.object(hook,'convert_checkpoint_rank',side_effect=convert), \
                    patch.object(Path,'unlink',fail_partial):
                with self.assertRaisesRegex(OSError,'partial cleanup pending'):
                    loader.load_model(config,model_config)
            self.assertEqual(loader.lifecycle_status,'failed')
            self.assertTrue((output/'rank-0/source-complete.json.partial').exists())
            with self.assertRaisesRegex(ValueError,'regular owned'):
                hook.collect_source_rank_receipts(output,self.identity())

    def test_export_precedes_one_backend_postprocess_and_source_config_is_separate(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, base, output = self.fixture(temp, events)
            def convert(directory, source, model, model_config, **kwargs):
                events.append('convert')
                self.assertEqual(directory, output / 'rank-0')
                self.assertEqual(source.load_config.load_format, 'safetensors')
                self.assertEqual(config.load_config.load_format, FORMATS['export'])
                self.assertIsNot(source.load_config, config.load_config)
                self.assertEqual(kwargs['device'], 'cuda:0')
                model.packed.data[0] = -1
                return {'archive': {'store_created': False}}
            # GPU/device/conversion boundaries mocked; exact Base/utility ordering executes.
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'convert_checkpoint_rank', side_effect=convert):
                loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize', 'convert', 'quant_postprocess', 'attention_postprocess'])
            self.assertEqual(loader.weights_status, 'exported')
            self.assertEqual(loader.lifecycle_status, 'returned')
            self.assertFalse(loader.conversion_receipt['archive']['store_created'])
            self.assertTrue(loader.discard_worker_required)
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_model(config, model_config)

    def test_rank_context_mismatch_refused_before_initializer(self):
        for field, value in (('rank', 8), ('device_id', True), ('tp_size', 4), ('pp_size', 2)):
            context = dict(rank=0, tp_size=8, pp_size=1, device_id=0)
            context[field] = value
            events = []
            with tempfile.TemporaryDirectory() as temp:
                loader, config, model_config, _, _ = self.fixture(temp, events, context)
                with self.assertRaisesRegex(ValueError, 'context required'):
                    loader.load_model(config, model_config)
            self.assertFalse(events)

    def test_conversion_failure_has_no_postprocess_and_is_terminal(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, _, _ = self.fixture(temp, events)
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'convert_checkpoint_rank', side_effect=RuntimeError('fixture export failure')):
                with self.assertRaisesRegex(RuntimeError, 'export failure'):
                    loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize'])
            self.assertEqual(loader.lifecycle_status, 'failed')
            self.assertEqual(loader.weights_status, 'failed')

    def test_default_context_refuses_uninitialized_cuda_without_importing_vllm(self):
        with patch.object(torch.cuda, 'is_initialized', return_value=False):
            with self.assertRaisesRegex(ValueError, 'already be initialized'):
                hook.current_rank_context()

    def test_source_registry_idempotence_and_foreign_collision_without_builtin_changes(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, _, base, _ = self.fixture(temp, events)
            raw = Path(__file__).parent / 'fixtures/vllm_loader_init.py'
            code = compile(raw.read_bytes(), str(raw.resolve()), 'exec', dont_inherit=True)
            namespace = dict(_LOAD_FORMAT_TO_MODEL_LOADER={'safetensors': object()}, BaseModelLoader=base, logger=Mock())
            for name in ('register_model_loader', 'get_model_loader'):
                child = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)
                namespace[name] = types.FunctionType(child, namespace)
            registry = types.SimpleNamespace(**namespace)
            original = registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors']
            name = register_candidate_loader(registry, type(loader), mode='export')
            self.assertEqual(name, FORMATS['export'])
            self.assertEqual(register_candidate_loader(registry, type(loader), mode='export'), name)
            self.assertIsInstance(registry.get_model_loader(config.load_config), type(loader))
            foreign = type('Foreign', (type(loader),), {})
            with self.assertRaisesRegex(ValueError, 'another loader'):
                register_candidate_loader(registry, foreign, mode='export')
            self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors'], original)
            self.assertFalse(events)


if __name__ == '__main__':
    unittest.main()
