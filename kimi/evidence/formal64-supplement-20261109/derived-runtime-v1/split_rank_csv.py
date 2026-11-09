"""Split the already derived32 finite rank slots; never reads runtime or model files."""
import csv, hashlib, json
from pathlib import Path
p=Path(__file__).resolve().parent
with (p/'all-32-rank-slots.csv').open() as f:
    reader=csv.DictReader(f);columns=reader.fieldnames;rows=list(reader)
assert len(rows)==32
for condition in ['safetensors_disk_hot','store_disk_cold','safetensors_disk_cold','store_pool_hit']:
    selected=[r for r in rows if r['condition']==condition]
    assert len(selected)==8 and [int(r['expected_rank']) for r in selected]==list(range(8))
    with (p/(condition+'-endpoints.csv')).open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(selected)
files=[{'path':str(f),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted(p.iterdir()) if f.is_file()]
with (p/'DELIVERY-MANIFEST.json').open('x') as f:json.dump({'status':'CPU_PARTIAL_ENDPOINT_ARTIFACTS_COMPLETE','complete_block_n':0,'files':files},f,indent=2);f.write('\n')
print(hashlib.sha256((p/'DELIVERY-MANIFEST.json').read_bytes()).hexdigest())
