"""Frozen metadata arithmetic, not a peak estimate or runtime admission."""
from collections import Counter, defaultdict
import hashlib
import json

from checkpoint_contract import INDEX_SHA256
from contract import validate_config, REVISION

HEADER_SHA = 'f01064b0e7463602b875b57e04fe79335775713fc56e3cd1049b3f61a6d35d4c'


def derive_memory_floors(config_raw, index_raw, header_raw):
    config = validate_config(config_raw)['text_config']
    if (hashlib.sha256(index_raw).hexdigest() != INDEX_SHA256
            or hashlib.sha256(header_raw).hexdigest() != HEADER_SHA):
        raise ValueError('frozen official index and complete header summary required')
    index = json.loads(index_raw)['weight_map']
    summary = json.loads(header_raw)
    if summary['revision'] != REVISION or len(index) != summary['tensor_count']:
        raise ValueError('source revision or tensor coverage differs')
    counts, roots = Counter(index.values()), defaultdict(set)
    for name, shard in index.items():
        roots[shard].add(name.partition('.')[0])
    rows = summary['rows']
    if len(rows) != 64 or len({r['shard'] for r in rows}) != 64 or set(counts) != {r['shard'] for r in rows}:
        raise ValueError('complete unique shard coverage required')
    totals = Counter()
    for row in rows:
        shard = row['shard']
        if counts[shard] != row['tensor_count'] or len(roots[shard]) != 1:
            raise ValueError('shard names and grouped header totals do not agree')
        root = next(iter(roots[shard]))
        if root not in ('language_model', 'vision_tower', 'mm_projector'):
            raise ValueError('unknown source branch')
        totals[root] += sum(row['dtype_bytes'].values())
    if sum(totals.values()) != summary['payload_bytes']:
        raise ValueError('payload totals differ')
    tp = 8
    mla = config['num_hidden_layers'] * config['hidden_size'] * (
        config['q_lora_rank'] + config['kv_lora_rank'] + config['qk_rope_head_dim']) * 2
    # Three expert projections, each source shape metadata is two int32 elements.
    excluded_shapes = (config['num_hidden_layers'] - config['first_k_dense_replace']) * config['n_routed_experts'] * 3 * 2 * 4
    text_rank = (totals['language_model'] - excluded_shapes + mla * (tp - 1)) // tp
    visual = totals['vision_tower'] + totals['mm_projector']
    scratch_rank = (config['num_hidden_layers'] - config['first_k_dense_replace']) * 2 * config['n_routed_experts'] * (
        config['hidden_size'] + config['moe_intermediate_size'] // tp) * 4
    raw_rank = text_rank + visual
    return dict(scope='conditional metadata lower bounds; not actual peak or upper limits',
        revision=REVISION, config_sha256=hashlib.sha256(config_raw).hexdigest(),
        index_sha256=INDEX_SHA256, header_summary_sha256=HEADER_SHA,
        source_branch_payload_bytes=dict(totals), excluded_shape_bytes=excluded_shapes,
        replicated_mla_source_bytes=mla, text_rank_floor_bytes=text_rank,
        visual_rank_floor_bytes=visual, raw_rank_floor_bytes=raw_rank,
        cpu_text_8rank_floor_bytes=tp * text_rank,
        cpu_initial_aux_8rank_bytes=tp * scratch_rank,
        store_8rank_payload_floor_bytes=tp * raw_rank,
        sf_full_file_cache_bytes=summary['file_bytes'],
        store_full_file_cache_floor_bytes=tp * raw_rank,
        mixed_gpu_load_floor_bytes=raw_rank + visual,
        upper_bounds_known=False, peak_memory_verified=False, runtime_admitted=False,
        assumptions=['TP8 PP1 noEP MLA Marlin, ideal text partition except mandatory MLA A replication',
          'Vision and projector BF16 are replicated by encoder data mode on all ranks',
          'Other replicated text weights, extra buffers, allocator granularity and overhead omitted',
          'Cache amounts mean hypothetical complete residency, not observed residency'])
