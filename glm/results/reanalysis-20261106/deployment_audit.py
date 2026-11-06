"""Verify archived deployment patch identities offline, without importing packages."""
import hashlib
import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p: json.loads(p.read_text(encoding='utf-8'))
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
binding=read(R/'deployment-binding.json')
control=read(R/'inputs/C23-control-input-manifest.json')
manifest=read(R/'inputs/C23-run-manifest.json')
history=read(R/'inputs/historical-review-manifest-v11.json')
assert sha(R/'inputs/historical-review-manifest-v11.json')==control['external_source_reference']['sha256']==binding['history_manifest']['sha256']
assert sha(R/'inputs/canonical-INDEX.json')==manifest['canonical_index_sha256']==binding['canonical_index_sha256']
assert sha(R/'inputs/C23-control-input-manifest.json')==binding['history_manifest']['pinned_by_control_manifest']
assert sha(R/'inputs/C23-run-manifest.json')==binding['run_manifest_sha256']
hist={x['path']:x for x in history['files']}
patches=read(R/'patch-inventory.json')
expected={(x['repository'],row['path']):row['sha256'] for x in patches for row in x['ledger_files']}
assert len(binding['files'])==len(expected) and not binding['errors']
for row in binding['files']:
    assert row['status']=='MATCH'
    assert expected[row['repository'],row['path']]==row['current_sha256']==hist[row['historical_path']]['sha256']
for patch in patches:
    key='selected_sllm_commit' if patch['repository']=='sllm' else 'selected_vllm_commit'
    assert patch['official_base_commit']==manifest[key]
    # Patch identity is independently frozen in its original ledger, not its name.
    path=R/'inputs'/Path(patch['patch_path']).name
    assert sha(path)==patch['patch_sha256']
archive=read(R/'inputs/C23-executed-source-archive.json')
proof=read(R/'inputs/C23-formal13-cpu-import-preflight.json')
assert sha(R/'inputs/C23-executed-source-archive.json')==binding['archive_receipt_sha256']
assert sha(R/'inputs/C23-formal13-cpu-import-preflight.json')==binding['preflight_sha256']
server=next(x for x in archive['files_checked_before_and_after'] if x['path']=='store_server13.py')
assert sha(R/'inputs/C23-store-server.py')==server['sha256']==manifest['store_import_sha256']['sllm_store.server']==proof['modules']['sllm_store.server']['sha256']
assert proof['modules']['sllm_store.server']['serve_source_code_match'] is True
for name,row in proof['modules'].items():assert row['sha256']==manifest['store_import_sha256'][name]
old=(R/'inputs/v5-store-server.py').read_text();new=(R/'inputs/C23-store-server.py').read_text()
assert old.replace('await server.wait_for_termination()','await asyncio.shield(server.wait_for_termination())')==new
result=dict(gate='PASS_FOR_DECLARED_PATCHSET',verified_patch_target_files=len(expected),overlay_changed_files=1,ordered_transforms=['official selected SLLM/vLLM commits -> hash-checked v5 integration patches','v5 Store server -> archived C23 shield(wait_for_termination) overlay'],source_manifest_sha256=archive['manifest_sha256'],actual_store_import_modules=proof['modules'],limits=binding['limits']+['This checks declared integration patchset, not an exhaustive Git diff of every repository file or every GPU rank mapped instruction'],input_sha256={p.name:sha(p) for p in (R/'deployment-binding.json',R/'inputs/historical-review-manifest-v11.json',R/'inputs/C23-run-manifest.json',R/'inputs/C23-formal13-cpu-import-preflight.json')},script_sha256=sha(Path(__file__)))
(R/'deployment-gate.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
