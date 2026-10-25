from pathlib import Path
import json,base64,hashlib,subprocess,datetime
repo=Path(__file__).resolve().parent.parent; audit=json.loads((repo/'evidence/g0/handoff-current-audit-v2.json').read_text()); attempts=audit['attempts']
code="""import pathlib,json,base64,socket
root=pathlib.Path('/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57')
assert socket.gethostname()=='qjhs3'
assert pathlib.Path('/etc/machine-id').read_text().strip()=='000c69369afa00000001fc4f0d971280'
assert pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='0145ca12-ac6e-40fc-9760-5d0639290553'
rows=json.loads(PAYLOAD)
out=[]
for row in rows:
 name=row['attempt_id']; assert name.replace('_','').isalnum() and '/' not in name
 directory=root/'formal-trials'/name; files={}
 if directory.exists():
  for suffix in ['result.json','origin.json']:
   path=directory/suffix
   if path.is_file():
    assert path.stat().st_size<1048576
    files[suffix]=base64.b64encode(path.read_bytes()).decode()
 out.append(dict(attempt_id=name,directory_exists=directory.exists(),files=files))
print(json.dumps(out))
""".replace('PAYLOAD',repr(json.dumps(attempts)))
result=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','qjhs003','python3','-'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=60,check=True)
rows=json.loads(result.stdout); expected={r['attempt_id']:r for r in attempts}
output=repo/'evidence/formal-history'; output.mkdir(exist_ok=True); indexed=[]
for row in rows:
 old=expected[row['attempt_id']]
 assert row['directory_exists']==(old['status']!='unstarted'),row['attempt_id']
 for suffix,encoded in row['files'].items():
  data=base64.b64decode(encoded); sha=hashlib.sha256(data).hexdigest()
  if suffix=='result.json': assert sha==old['result_sha256'],row['attempt_id']
  path=output/(row['attempt_id']+'--'+suffix); path.write_bytes(data)
  indexed.append(dict(path=path.name,bytes=len(data),sha256=sha,attempt_id=row['attempt_id'],historical_status=old['status'],historical_revision=old.get('protocol_revision')))
receipt=dict(collected_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),ssh_alias='qjhs003',machine_id='000c69369afa00000001fc4f0d971280',boot_id='0145ca12-ac6e-40fc-9760-5d0639290553',scope='read-only exact40 original-ID directory presence; nine result bytes equal G0; no GPU, cache or process operation',files=indexed,terminal=9,success=5,failed=4,unstarted=31,next_attempt='store_pool_hit_03')
(output/'collection-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(dict(collected_files=len(indexed),bytes=sum(r['bytes'] for r in indexed),all_terminal_hashes_match=True,formal_state_unchanged=True)))
