import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import types
import typing
import unittest
from unittest.mock import patch

import contract
import native_index
import stream_writer

CONFIG = (Path(__file__).resolve().parents[2] / 'evidence/followup-kimi-k26/config.json').read_bytes()


class IndexTests(unittest.TestCase):
    def prepare(self, target):
        tensors = {'packed': dict(shape=[2], dtype='torch.int32', bytes=8, contiguous=True),
                   'scale': dict(shape=[3], dtype='torch.bfloat16', bytes=6, contiguous=True)}
        plan = contract.plan_rank(CONFIG, rank=0, tensor_parallel_size=8,
            revision=contract.REVISION, tensors=tensors, vllm_version='0.19.1', torch_version='2.10.0')
        records = stream_writer.write_records(target, [(n, r['bytes'], [bytes(r['bytes'])], None)
                                                      for n, r in tensors.items()])
        layouts = {n: dict(shape=r['shape'], stride=[1], dtype=r['dtype'],
                           storage_offset=0, storage_bytes=r['bytes']) for n, r in tensors.items()}
        return plan, records, layouts

    def digest(self, target):
        return hashlib.sha256((target / 'kimi_raw_state.json').read_bytes()).hexdigest()

    def test_roundtrip_and_actual_pinned_python_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'candidate'
            plan, records, layouts = self.prepare(target)
            state = native_index.publish_index(target, plan, records, layouts, CONFIG)
            loaded = native_index.read_index(target, self.digest(target))
            source = Path(__file__).parent / 'fixtures/native_torch.py'
            raw = source.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             'a4fb54b68a3895bf6015cbefe3bc7a7fd51e0af4f37cda72d1b49c9fabce29e9')
            node = next(n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == 'save_dict')
            namespace = dict(json=json, os=os, Dict=typing.Dict, Union=typing.Union,
                             torch=types.SimpleNamespace(Tensor=object),
                             save_tensors=lambda *args: {n: r['offset'] for n, r in records.items()})
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
            class TensorMetadata:
                def __init__(self, row):
                    self.row, self.shape = row, row['shape']
                def untyped_storage(self):
                    return types.SimpleNamespace(data_ptr=lambda: id(self), size=lambda: self.row['storage_bytes'])
                def stride(self):
                    return self.row['stride']
                @property
                def dtype(self):
                    return self.row['dtype']
            reference = Path(temp) / 'native-schema'
            namespace['save_dict']({n: TensorMetadata(r) for n, r in layouts.items()}, str(reference))
            self.assertEqual(loaded, json.loads((reference / 'tensor_index.json').read_text()))
            self.assertFalse(state['native_loader_verified'])
            self.assertFalse(state['runtime_admitted'])

    def test_views_missing_tensors_and_wrong_offsets_rejected(self):
        for kind in ['view', 'storage', 'stride', 'missing', 'offset']:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'candidate'
                plan, records, layouts = self.prepare(target)
                if kind == 'view': layouts['packed']['storage_offset'] = 1
                if kind == 'storage': layouts['packed']['storage_bytes'] = 12
                if kind == 'stride': layouts['packed']['stride'] = [2]
                if kind == 'missing': layouts.pop('scale')
                if kind == 'offset': records['packed']['offset'] = 8
                with self.assertRaises(ValueError):
                    native_index.publish_index(target, plan, records, layouts, CONFIG)
                self.assertFalse((target / 'tensor_index.json').exists())

    def test_corruption_of_payload_index_or_sidecar_rejected(self):
        for name in ['tensor.data_0', 'tensor_index.json', 'kimi_raw_state.json', 'config.json']:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'candidate'
                native_index.publish_index(target, *self.prepare(target), CONFIG)
                digest = self.digest(target)
                with (target / name).open('r+b') as stream:
                    stream.write(b'X')
                with self.assertRaises(ValueError):
                    native_index.read_index(target, digest)

    def test_existing_index_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'candidate'
            plan, records, layouts = self.prepare(target)
            native_index.publish_index(target, plan, records, layouts, CONFIG)
            original = (target / 'tensor_index.json').read_bytes()
            with self.assertRaises(FileExistsError):
                native_index.publish_index(target, plan, records, layouts, CONFIG)
            self.assertEqual((target / 'tensor_index.json').read_bytes(), original)

    def test_cleanup_failure_is_distinct_from_publication_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'candidate'
            args = self.prepare(target)
            with patch.object(Path, 'unlink', side_effect=PermissionError('fixture')):
                result = native_index.publish_index(target, *args, CONFIG)
            self.assertIn('tensor_index.json.partial', result['cleanup_pending'])
            self.assertTrue((target / 'tensor_index.json').exists())
            native_index.read_index(target, self.digest(target))

    def test_invalid_alias_declarations_rejected(self):
        for aliases in [{'packed': 'packed'}, {'packed': 'scale'}, {'packed': 'absent'},
                        {'packed': 'scale', 'scale': 'packed'}]:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'candidate'
                plan, records, layouts = self.prepare(target)
                with self.assertRaises(ValueError):
                    native_index.publish_index(target, plan, records, layouts, CONFIG, aliases=aliases)
                self.assertFalse((target / 'tensor_index.json').exists())

    def test_undeclared_overlap_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'candidate'
            tensors = {n: dict(shape=[2], dtype='torch.int32', bytes=8, contiguous=True) for n in ['a', 'b']}
            plan = contract.plan_rank(CONFIG, rank=0, tensor_parallel_size=8, revision=contract.REVISION,
                tensors=tensors, vllm_version='0.19.1', torch_version='2.10.0')
            records = stream_writer.write_records(target, [(n, 8, [bytes(8)], None) for n in tensors])
            layouts = {n: dict(shape=[2], stride=[1], dtype='torch.int32', storage_offset=0,
                              storage_bytes=8) for n in tensors}
            records['b'] = dict(records['a'])
            with self.assertRaises(ValueError):
                native_index.publish_index(target, plan, records, layouts, CONFIG)
            state = native_index.publish_index(target, plan, records, layouts, CONFIG, aliases={'b': 'a'})
            self.assertTrue(state['aliases_deduplicated'])
            self.assertEqual(state['written_payload_bytes'], 8)


if __name__ == '__main__':
    unittest.main()
