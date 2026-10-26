"""Small synthetic files test input closure; no model-sized allocations."""
import hashlib
import pathlib
import tempfile
import unittest

from store_cache_inputs import METADATA, store_inputs


class StoreInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.store = self.root / 'store'
        self.checkpoint = self.root / 'checkpoint'
        self.store.mkdir()
        self.checkpoint.mkdir()
        self.rows = []
        self.metadata = {}
        for name in METADATA:
            self.meta(name)
            (self.checkpoint / name).write_bytes(b'config')
        for rank in range(8):
            directory = self.store / f'rank_{rank}'
            directory.mkdir()
            for part in range(10):
                (directory / f'tensor.data_{part}').write_bytes(b'x')
            self.rows.append(dict(rank=rank, partition_bytes=[1] * 10))
            for name in ('raw_state.json', 'tensor_index.json'):
                self.meta(f'rank_{rank}/{name}')

    def meta(self, name):
        path = self.store / name
        path.write_bytes(b'official metadata')
        self.metadata[name] = dict(bytes=path.stat().st_size,
                                   sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def read(self):
        return store_inputs(self.store, self.checkpoint, self.rows, self.metadata)

    def test_all_80_parts_and_28_metadata_files_are_covered(self):
        paths = self.read()
        self.assertEqual(len(paths), 108)
        self.assertEqual(sum(path.name.startswith('tensor.data_') for path in paths), 80)
        self.assertTrue(all(self.checkpoint / name in paths for name in METADATA))

    def test_changed_rank_metadata_rejected(self):
        (self.store / 'rank_7/raw_state.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'metadata identity differs'):
            self.read()

    def test_truncated_partition_rejected(self):
        (self.store / 'rank_7/tensor.data_9').write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'partition identity differs'):
            self.read()

    def test_missing_part_rejected(self):
        (self.store / 'rank_0/tensor.data_9').unlink()
        with self.assertRaisesRegex(ValueError, 'ten partitions'):
            self.read()

    def test_extra_part_rejected(self):
        (self.store / 'rank_0/tensor.data_10').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, 'ten partitions'):
            self.read()

    def test_incomplete_frozen_metadata_rejected(self):
        del self.metadata['rank_0/raw_state.json']
        with self.assertRaisesRegex(ValueError, 'complete frozen Store metadata'):
            self.read()

    def test_checkpoint_tokenizer_is_not_optional(self):
        (self.checkpoint / 'tokenizer.json').unlink()
        with self.assertRaisesRegex(ValueError, 'checkpoint metadata missing'):
            self.read()


if __name__ == '__main__':
    unittest.main()
