"""Freeze the phase limits for the fixed-pool, single-active-worker B2 attempt."""
import json
import math
import sys
from pathlib import Path

p, parent = map(Path, sys.argv[1:])
GIB = 2**30
b1_path = parent / 'replay-b1-host-capacity-20261120.json'
history_path = parent / 'historical-host-phase-envelope.json'
glm_path = p / 'glm53-l2-memory.json'
b1 = json.loads(b1_path.read_bytes())['observed_max_drops']
old = json.loads(history_path.read_bytes())
g = json.loads(glm_path.read_bytes())
assert g['qualified'] and g['release_seconds'] >= 60 and len(g['groups_by_rank']) == 8
assert len(old) == 49
def old_peak(model, kind):
    return max((row['entry_available_bytes'] - row['min_available_bytes']) / GIB
               for row in old if row['model'] == model and row['mode']['kind'] == kind)
def b1_peak(name, mode):
    return b1[f'{name}:{mode}']['drop_bytes'] / GIB
operating = {
    'safetensors_disk_cold': {
        'K': old_peak('K', 'load'),
        'G': max(b1_peak('glm53', 'sf_cold'), g['full_first_load_host_drop_bytes'] / GIB),
        'D': max(b1_peak('v4', 'sf_cold'), old_peak('D', 'load')),
    },
    'store_inplace_cumem': {
        'K': 70.148,
        'G': max(b1_peak('glm53', 'store'), g['full_first_load_host_drop_bytes'] / GIB),
        'D': max(b1_peak('v4', 'store'), old_peak('D', 'load')),
    },
    'checkpoint_wake': {
        'K': 0.833,
        'G': max(g['memory']['wake_through_rebind']['host_available_loss_bytes'] / GIB, old_peak('G', 'wake')),
        'D': max(8.303, old_peak('D', 'wake')),
    },
}
basis = {
    'pool': f'{b1_path}; retain prior qualified 1750 GiB pool budget and 1871 GiB startup check',
    'safetensors_disk_cold': {
        'K': f'{history_path}; native initial load with pinned Kimi artifact',
        'G': f'{b1_path}; {glm_path}; larger of current 5.3 native and dual-pool first load pressure',
        'D': f'{b1_path}; {history_path}; current V4 first load',
    },
    'store_inplace_cumem': {
        'K': '70.148 GiB B1 maximum one-backend envelope; exact joint Kimi in-place first load needs calibration',
        'G': f'{b1_path}; {glm_path}; dual-pool B1 Store and standalone GLM-5.3 first load are proxies',
        'D': f'{b1_path}; {history_path}; dual-pool B1 Store and native V4 first load are proxies',
    },
    'checkpoint_wake': {
        'K': 'Kimi meta sleep-kv-L2-wna16-meta-20261119T195718Z host trace, prewake quiet to sampled minimum through the postwake probe',
        'G': f'{glm_path}; {history_path}; qualified GLM-5.3 full wake/rebind and historical GLM-5.1 as cross-run check',
        'D': f'{history_path}; 8.303 GiB across nine same-model checkpoint wake phases, mapped weights to KV-ready',
    },
}
profile = dict(node='qjhs003', sleep_level=2, pool_gib=dict(weights=864, host_kv=813),
               pool_budget_bytes=1750*GIB, runtime_floor_bytes=50*GIB, runtime_stop_bytes=55*GIB,
               operating_allowance_gib=8, observed_or_proxy_gib=operating,
               backend_headroom_bytes={key: {model:(math.ceil(peak)+8)*GIB for model,peak in values.items()}
                                       for key,values in operating.items()}, basis=basis)
(p/'phase-capacity.json').write_text(json.dumps(profile,indent=2)+'\n')
for mode, vals in profile['backend_headroom_bytes'].items():
    print(mode, {k:50+v//GIB for k,v in vals.items()})
