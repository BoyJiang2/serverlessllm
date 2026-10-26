"""Separate the pinned GLM DSA runtime indices from checkpoint/derived weights."""


def weight_state(document):
    weights, runtime_indices = {}, {}
    for name,row in document['tensors'].items():
        if name.startswith('attribute:') and name.endswith('.topk_indices_buffer'):
            assert row['dtype'] == 'torch.int32' and row['shape'] == [2048,2048]
            assert row['bytes'] == 2048*2048*4
            runtime_indices[name] = row
        else:
            weights[name] = row
    assert len(runtime_indices) == 234, 'unexpected GLM DSA workspace reference coverage'
    return {'tensors':weights,'runtime_indices':runtime_indices,'kv_runtime_excluded':document['excluded']}
