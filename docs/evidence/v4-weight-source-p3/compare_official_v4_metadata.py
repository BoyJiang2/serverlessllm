from pathlib import Path
import json
api=json.loads(Path('work/v4-weight-provenance/official-model-info.json').read_bytes())
old=json.loads(Path('work/expected-local-v4-assets.json').read_bytes())
local=json.loads(Path('outputs/local-model-assets-receipt.json').read_bytes())
rows={r['rfilename']:r for r in api['siblings']}
for item in old['weights']:
 r=rows[item['file']]
 assert r['size']==item['bytes'] and r['lfs']['sha256']==item['lfs_sha256_declared_not_payload_verified']
print('64 prior LFS size/hash declarations match fresh official API')
print('Metadata recheck planned logical bytes:',sum(r['bytes'] for r in old['assets'])+old['index']['size']+local['header_byte_count'])
print('Non-weight files:',[(r['rfilename'],r.get('size')) for r in api['siblings'] if not r['rfilename'].endswith('.safetensors')])
