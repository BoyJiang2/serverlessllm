"""Validate joint Sleep receipts against the original workers and server events."""

from collections import defaultdict


RANKS = set(range(8))
GROUPS = {
    'K': [(0, 16, 61, 16)],
    'G': [(0, 64, 21, 64), (0, 64, 78, 64)],
    'D': [(0, 256, 31, 2), (0, 256, 30, 64), (0, 256, 30, 64),
          (1, 64, 31, 64), (2, 64, 30, 64), (3, 8, 31, 8),
          (4, 4, 30, 4), (4, 4, 30, 4)],
}
WINDOWS = {'D': [256, 256, 256, 128, 128, 128, 8, 8]}


def worker_identity(rows):
    assert len(rows) == 8 and {r['rank'] for r in rows} == RANKS, rows
    assert len({r['instance_id'] for r in rows}) == 8
    return {r['rank']: (r['pid'], r['process_start'], r['instance_id'], r['view_signature']) for r in rows}


def registration(status, model, rows, *, bound):
    ids = {row['instance_id'] for row in rows} if bound else set()
    assert all(row['bound'] is bound for row in rows), rows
    assert set(status['registered_gpu_ids']) == ids, status
    contexts = status['cache_context_meta']
    assert set(contexts) == set(map(str, ids)), contexts
    namespace = {ctx['model_name'] for ctx in contexts.values()}
    assert len(namespace) == int(bound), namespace
    for ctx in contexts.values():
        assert ctx['world_size'] == 1, ctx['world_size']
        groups = ctx['kv_cache_layout']['kernel_groups']
        seen = [(group['engine_group_idx'], group['tokens_per_block'], group['num_layers'], group['slots_per_block'])
                for group in groups]
        assert sorted(seen) == sorted(GROUPS[model]), seen
    return next(iter(namespace)) if bound else None


def verify_transfer(model, rows, completions, worker_rows, namespace, direction, *, tokens=512):
    """Require stream completion for each participating device and valid groups."""
    expected_ranks = {r['rank'] for r in worker_rows if r['writer']} if direction == 'D2H' else RANKS
    assert expected_ranks
    assert {r['device'] for r in rows} == expected_ranks, rows
    expected_event = 'MP_STORE_END' if direction == 'D2H' else 'MP_RETRIEVE_END'
    by_key = {r['transfer_key']: r for r in completions}
    assert len(by_key) == len(completions)
    assert set(by_key) == {r['transfer_key'] for r in rows}, by_key
    assert len({r['request_id'] for r in rows}) == 1
    groups_by_rank = defaultdict(list)
    observed_object_groups = defaultdict(set)
    for row in rows:
        rank = row['device']
        finish = by_key[row['transfer_key']]
        assert finish['event'] == expected_event and row['direction'] == direction
        assert finish['device'] == f'cuda:{rank}' and finish['model_name'] == namespace
        assert finish['request_id'] == row['request_id'] and finish['num_tokens'] == tokens
        assert row['enqueued_ns'] <= finish['ns'] and row['skip'] == 0
        assert row['object_group'] not in observed_object_groups[rank]
        observed_object_groups[rank].add(row['object_group'])
        masks = row['object_mask']
        assert len(masks) * 256 == tokens, row
        for group in row['groups']:
            signature = (group['engine_group'], group['logical_block'], group['layers'], group['slots_per_block'])
            window = WINDOWS[model][group['id']] if model in WINDOWS else 256
            blocks = window // group['logical_block']
            ids = group['block_ids']
            assert blocks > 0 and len(ids) == len(masks) * blocks
            retained = [any(ids[i * blocks:(i + 1) * blocks]) for i in range(len(masks))]
            assert any(allocated and valid for allocated, valid in zip(masks, retained)), (row, group)
            groups_by_rank[rank].append(signature)
        assert row['host_bytes'] > 0
    assert set(groups_by_rank) == expected_ranks
    for rank, signatures in groups_by_rank.items():
        assert sorted(signatures) == sorted(GROUPS[model]), (rank, signatures)
    return {str(rank): sum(r['host_bytes'] for r in rows if r['device'] == rank) for rank in sorted(expected_ranks)}
