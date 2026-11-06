from pathlib import Path
import hashlib,json
base=Path('work/v4-weight-provenance')
api=json.loads((base/'official-model-info.json').read_bytes());normalized=json.loads((base/'expected-weight-files.json').read_bytes())
current=json.loads((base/'current-local-metadata.json').read_bytes());receipt=json.loads((base/'local-model-assets-receipt.json').read_bytes())
assert current['runtime_assets_all_match'] and current['headers_match_previous']
assert receipt['all_64_shard_sizes_match'] and receipt['header_index_tensor_coverage']
assert not receipt['payload_sha256_verified'] and not current['payload_content_sha256_verified']
assert len(normalized['files'])==64 and normalized['total_file_bytes']==receipt['serialized_file_bytes']
assert {r['file'] for r in normalized['files']}=={f'model-{i:05d}-of-00064.safetensors' for i in range(1,65)}
assert all(r['size']==r['lfs']['size'] for r in api['siblings'] if r.get('lfs'))
assert len((base/'expected-whole-files.sha256').read_text().splitlines())==64
matched=[r for r in current['nonweight_files'] if r.get('matches_fixed_revision')]
missing=[r['file'] for r in current['nonweight_files'] if not r['present']]
different=[r['file'] for r in current['nonweight_files'] if r['present'] and not r.get('matches_fixed_revision')]
assert len(matched)==26 and missing==[] and different==['.gitattributes']
core=[r for r in current['nonweight_files'] if r['runtime_asset']]
assert len(core)==16 and all(r['matches_fixed_revision'] for r in core)
extra=sum(r['bytes'] for r in current['nonweight_files'] if r['present'] and not r['runtime_asset'])
metadata_bytes=sum(r['bytes'] for r in receipt['assets'])+11305684+receipt['header_byte_count']+extra+len(receipt['provenance_note'].encode())
assert metadata_bytes<64*1024*1024
report=dict(schema=1,model=api['id'],revision=api['sha'],host=current['host'],model_path=current['model_path'],
 official_accessed_utc=json.loads((base/'official-model-info-receipt.json').read_bytes())['accessed_utc'],
 local_inspected_utc=current['time_utc'],official_metadata_bytes=(base/'official-model-info.json').stat().st_size,weight_files=64,
 total_weight_file_bytes=normalized['total_file_bytes'],weight_file_hashes_verified=0,
 hash_scope='Whole files, including 8-byte safetensors header length and JSON header; NOT tensor data area only',
 runtime_input_files_exact_hash_match=16,nonweight_files_exact_hash_match=26,nonweight_files_missing=missing,nonweight_files_differ=different,
 header_tensor_keys=receipt['tensor_count'],header_and_index_consistent=True,
 inspection_model_metadata_requested_bytes=metadata_bytes,requested_tensor_payload_bytes=0,
 kernel_readahead_bytes_not_measured=True,source_revision_content_verified=False,
 source_status='METADATA_MATCHES_FULL_WEIGHT_CONTENT_UNVERIFIED',gpu_execution=False,model_imported=False,
 scope='Fresh official metadata and bounded local metadata inspection, not full checkpoint equality or inference proof')
(base/'comparison.json').write_bytes((json.dumps(report,indent=2)+'\n').encode())
(base/'bundle-manifest.json').write_bytes((json.dumps(dict(files=[dict(path=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(base.iterdir()) if p.is_file() and p.name!='bundle-manifest.json']),indent=2)+'\n').encode())
print(json.dumps(report))
