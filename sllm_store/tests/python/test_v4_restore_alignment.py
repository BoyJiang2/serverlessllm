"""Execute the real layout helpers without importing CUDA or model packages."""
import ast
import json
from pathlib import Path
import tarfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
tree = ast.parse((ROOT / 'sllm_store/utils.py').read_text())
names = {'calculate_device_memory', 'calculate_tensor_device_offsets'}
helpers = ast.Module(body=[node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name in names],
                     type_ignores=[])
namespace = {}
exec(compile(helpers, 'utils.py', 'exec'), namespace)
memory = namespace['calculate_device_memory']
offsets = namespace['calculate_tensor_device_offsets']


class RestoreAlignmentContracts(unittest.TestCase):
    def setUp(self):
        self.index = {'scalar': (0, 4), 'mhc': (4, 2752512),
                      'alias': (4, 2752512), 'tail': (2752516, 12)}
        self.devices = dict.fromkeys(self.index, 0)

    def test_legacy_layout_reproduces_unaligned_mhc_without_changing_default(self):
        locations, chunks = offsets(self.devices, self.index)
        self.assertEqual(locations[0]['mhc'], 4)
        self.assertEqual(locations[0]['alias'], 4)
        self.assertEqual(memory(self.devices, self.index), {0: 2752528})
        self.assertEqual(len(chunks[0]), 3)

    def test_owned_layout_aligns_unique_tensors_preserves_alias_and_bounds_copy(self):
        locations, chunks = offsets(self.devices, self.index, alignment=256)
        allocation = memory(self.devices, self.index, alignment=256)[0]
        self.assertEqual(locations[0]['mhc'], 256)
        self.assertEqual(locations[0]['alias'], 256)
        self.assertEqual(len(chunks[0]), 3)
        self.assertTrue(all(value % 256 == 0 for value in locations[0].values()))
        self.assertEqual(allocation, max(dest + size for _, size, dest, _ in chunks[0]))
        self.assertEqual([chunk[:2] for chunk in chunks[0]],
                         [self.index[name] for name in ('scalar', 'mhc', 'tail')])

    def test_separate_devices_have_independent_aligned_allocation_sizes(self):
        devices = dict(self.devices, tail=1)
        locations, chunks = offsets(devices, self.index, alignment=256)
        sizes = memory(devices, self.index, alignment=256)
        for device in sizes:
            self.assertEqual(sizes[device], max(dest + size for _, size, dest, _ in chunks[device]))
        self.assertEqual(locations[1]['tail'], 0)

    def test_missing_tensor_remains_rejected(self):
        for helper in (memory, offsets):
            with self.assertRaises(ValueError):
                helper({'missing': 0}, self.index, alignment=256)

    def test_all_exported_rank_groups_fit_and_align_without_changing_file_ranges(self):
        archive = ROOT.parent / 'docs/evidence/v4-store-data-integrity/store-metadata-only.tar.gz'
        count = 0
        with tarfile.open(archive) as files:
            for member in files.getmembers():
                if not member.name.endswith('/tensor_index.json'):
                    continue
                index = {name: value[:2] for name, value in
                         json.load(files.extractfile(member)).items()}
                devices = dict.fromkeys(index, 0)
                locations, chunks = offsets(devices, index, alignment=256)
                allocation = memory(devices, index, alignment=256)[0]
                self.assertTrue(all(value % 256 == 0 for value in locations[0].values()), member.name)
                self.assertEqual(allocation, max(dest + size for _, size, dest, _ in chunks[0]))
                self.assertEqual(sum(size for _, size, _, _ in chunks[0]),
                                 memory(devices, index)[0])
                count += 1
        self.assertEqual(count, 496)


if __name__ == '__main__':
    unittest.main()
