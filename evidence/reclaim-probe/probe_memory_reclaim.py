"""Independent GPU allocation/release probe; contains no model or inference."""
import datetime
import gc
import json
import pathlib
import time

import torch

root = pathlib.Path('/experiment')
manifest = json.loads((root/'run-manifest.json').read_text())
deadline = datetime.datetime.fromisoformat(manifest['cleanup_deadline']).timestamp()
assert time.time() < deadline-300
assert torch.cuda.device_count() == 8
out = root/'memory-reclaim-probe1'
out.mkdir(exist_ok=False)
size = 100*1024**3
rows = []
for device in range(8):
    assert time.time() < deadline-120
    with torch.cuda.device(device):
        free_before, total = torch.cuda.mem_get_info(device)
        assert free_before >= size+4*1024**3, 'probe admission failed; no intentional OOM'
        begin = time.monotonic_ns()
        value = torch.empty(size, dtype=torch.uint8, device=f'cuda:{device}')
        value.zero_()
        torch.cuda.synchronize(device)
        allocated_free, _ = torch.cuda.mem_get_info(device)
        del value
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize(device)
        free_after, _ = torch.cuda.mem_get_info(device)
        row = {'device':device,'allocation_bytes':size,'total_bytes':total,
               'free_before_bytes':free_before,'free_while_allocated_bytes':allocated_free,
               'free_after_bytes':free_after,'begin_monotonic_ns':begin,
               'end_monotonic_ns':time.monotonic_ns()}
        rows.append(row)
        (out/f'device-{device}.json').write_text(json.dumps(row,indent=2))
        assert free_after >= free_before-64*1024**2, 'probe allocation not reclaimed'
        print(json.dumps(row),flush=True)
(out/'result.json').write_text(json.dumps({
    'status':'eight GPU allocations and releases passed','run_id':manifest['run_id'],
    'scope':'100 GiB touched sequentially on each GPU after whole service teardown; no model or inference; outside B',
    'limit':'native instance-delete residuals with auxiliaries retained are assessed separately by the 60-second A4 observations',
    'devices':rows},indent=2))
