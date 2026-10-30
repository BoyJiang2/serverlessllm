import json
from pathlib import Path
import unittest

from contract import validate_config
from memory_floors import derive_memory_floors, HEADER_SHA
from checkpoint_contract import INDEX_SHA256


class MemoryFloorTests(unittest.TestCase):
    def artifact(self):
        return json.loads((Path(__file__).parent / 'mixed-memory-floors.json').read_bytes())

    def config(self):
        return (Path(__file__).resolve().parents[2] / 'evidence/followup-kimi-k26/config.json').read_bytes()

    def test_branch_partition_replication_and_auxiliary_arithmetic(self):
        d = self.artifact()
        c = validate_config(self.config())['text_config']
        source = d['source_branch_payload_bytes']
        self.assertEqual(sum(source.values()), 595148192736)
        self.assertEqual(d['header_summary_sha256'], HEADER_SHA)
        self.assertEqual(d['index_sha256'], INDEX_SHA256)
        mla = c['num_hidden_layers'] * c['hidden_size'] * (
            c['q_lora_rank'] + c['kv_lora_rank'] + c['qk_rope_head_dim']) * 2
        self.assertEqual(d['replicated_mla_source_bytes'], mla)
        self.assertEqual(d['text_rank_floor_bytes'],
            (source['language_model'] - d['excluded_shape_bytes'] + mla * 7) // 8)
        self.assertEqual(d['visual_rank_floor_bytes'], source['vision_tower'] + source['mm_projector'])
        self.assertEqual(d['raw_rank_floor_bytes'], d['text_rank_floor_bytes'] + d['visual_rank_floor_bytes'])
        scratch = (c['num_hidden_layers'] - c['first_k_dense_replace']) * 2 * c['n_routed_experts'] * (
            c['hidden_size'] + c['moe_intermediate_size'] // 8) * 4
        self.assertEqual(d['cpu_initial_aux_8rank_bytes'], 8 * scratch)

    def test_store_pool_and_file_cache_are_separate_categories(self):
        d = self.artifact()
        self.assertEqual(d['store_8rank_payload_floor_bytes'], d['raw_rank_floor_bytes'] * 8)
        self.assertEqual(d['cpu_text_8rank_floor_bytes'], d['text_rank_floor_bytes'] * 8)
        self.assertEqual(d['mixed_gpu_load_floor_bytes'],
            d['raw_rank_floor_bytes'] + d['visual_rank_floor_bytes'])
        # A hypothetical all-resident sum exceeds the historical nominal host;
        # ordinary cache is reclaimable, so this is deliberately not an OOM test.
        all_resident = sum(d[n] for n in ('cpu_text_8rank_floor_bytes',
            'store_8rank_payload_floor_bytes', 'store_full_file_cache_floor_bytes', 'sf_full_file_cache_bytes'))
        self.assertGreater(all_resident, 1923 * 2**30)

    def test_unknown_peak_cannot_be_reported_as_admitted(self):
        d = self.artifact()
        self.assertFalse(d['upper_bounds_known'])
        self.assertFalse(d['peak_memory_verified'])
        self.assertFalse(d['runtime_admitted'])

    def test_unbound_metadata_rejected_before_parse(self):
        with self.assertRaisesRegex(ValueError, 'frozen official'):
            derive_memory_floors(self.config(), b'{}', b'{}')
