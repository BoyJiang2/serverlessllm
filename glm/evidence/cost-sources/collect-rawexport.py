from pathlib import Path
import json,hashlib,datetime
base=Path('/data/brick1/boyjiang/glm5.1-fp8/archive/original-run')
rows=[]
for p in sorted((base/'events-rawexport1').glob('process-7a17075bd029-*.jsonl')):
 b=p.read_bytes();assert len(b)<262144
 rows.append(dict(path=str(p),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),raw_utf8=b.decode()))
assert len(rows)==8
metadata=[]
for name,fields in [('rawexport1-dispatch.json',('run_id','uid','dispatch_monotonic_ns','dispatch_utc','boot_id')),('rawexport1-identity.json',('run_id','container_id','pid','cgroup','dispatch_monotonic_ns','dispatch_utc','docker_create_complete_ns','docker_start_complete_ns','launch_utc'))]:
 p=base/name;b=p.read_bytes();x=json.loads(b)
 metadata.append(dict(path=str(p),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),selected_fields={k:x[k] for k in fields}))
print(json.dumps(dict(collected_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope='Readonly original conversion event bytes and selected launch identity fields; no code or model executed',files=rows,metadata=metadata),indent=2))
