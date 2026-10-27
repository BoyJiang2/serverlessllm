"""Read preserved tensor audit metadata, never import torch or load model payloads."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent
EARLY = ROOT.parent/'outputs'
SUFFIXES = ('.W_UV', '.W_UK_T', '.kv_b_proj.weight', '.kv_b_proj.weight_scale_inv',
            '.experts.w13_weight', '.experts.w13_weight_scale_inv',
            '.experts.w2_weight', '.experts.w2_weight_scale_inv')


def selected(tensors):
    return {k:v for k,v in tensors.items()
            if ((k.startswith('attribute:') and k.endswith(SUFFIXES[:2]))
                or (k.startswith('parameter:') and k.endswith(SUFFIXES[2:])))}


def check_raw_tensor(before, after, tensors):
    assert set(after) == {'shape','stride','dtype','bytes','sha256'}
    assert {k:before[k] for k in after} == after
    if 'alias' in before:
        assert tensors[before['alias']]['sha256'] == before['sha256']
    return 'alias' in before


def main():
    tar_path = EARLY/'store-evidence/store-metadata-review.tar.gz'
    tar_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    assert tar_sha == '2b765c90ae154e75b2fb61556f45315fac1bc1c964104a2276745d267554037b'
    inputs, ranks, examples = [], [], []
    origins = (EARLY/'native-reference/native_repeat7', EARLY/'native-reference/native_repeat8',
               EARLY/'store-evidence/store-validation-restoredraw3')
    with tarfile.open(tar_path) as archive:
        for rank in range(8):
            documents = []
            for origin in origins:
                path = origin/f'runtime-rank{rank}.json'
                data = path.read_bytes()
                inputs.append(dict(path=str(path.resolve()), bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
                document = json.loads(data)
                assert document['rank'] == rank
                documents.append(selected(document['tensors']))
            reference = documents[0]
            assert len(reference) == 924 and all(d == reference for d in documents[1:])
            member = f'store/vllm/glm51-fp8/rank_{rank}/raw_state.json'
            raw_data = archive.extractfile(member).read()
            raw = json.loads(raw_data)
            assert raw['rank'] == rank and raw['state'] == 'before_quantization_postprocessing'
            assert raw['VLLM_USE_DEEP_GEMM_E8M0'] == '0' and raw['tensor_parallel_size'] == 8
            count, alias_count = 0, 0
            for name, value in reference.items():
                if not name.startswith('parameter:'):
                    continue
                before = raw['tensors'][name.removeprefix('parameter:')]
                alias_count += check_raw_tensor(before,value,raw['tensors'])
                count += 1
            assert count == 768
            assert alias_count == 312
            counts = {suffix: sum(k.endswith(suffix) for k in reference) for suffix in SUFFIXES}
            assert list(counts.values()) == [78,78,234,234,75,75,75,75]
            canonical = json.dumps(reference,sort_keys=True,separators=(',', ':')).encode()
            ranks.append(dict(rank=rank, selected_postprocess_references=924,
                              selected_raw_parameters_unchanged=768, counts=counts,
                              selected_raw_alias_references=alias_count,
                              selected_logical_metadata_sha256=hashlib.sha256(canonical).hexdigest(),
                              raw_state_member=member,raw_state_sha256=hashlib.sha256(raw_data).hexdigest(),
                              two_native_and_component_restore_equal=True))
            if rank == 0:
                for suffix in SUFFIXES:
                    name = next(k for k in reference if k.endswith(suffix))
                    examples.append(dict(name=name,postprocess=reference[name],
                                         before=raw['tensors'].get(name.removeprefix('parameter:')) if name.startswith('parameter:') else None))
    output = dict(scope='Supplemental metadata recheck of preserved A2/native repeat7+8 and A3/component restore. Not a new payload or C23 weight audit.',
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  tar=dict(path=str(tar_path.resolve()),sha256=tar_sha), inputs=inputs,ranks=ranks,rank0_examples=examples,
                  coverage='8ranks;924 selected parameter/attribute references perrank,768 raw FP8/scale parameter references (456canonical+312aliases) unchanged and156 derivedMLA references equal across two native and component restore records. C17 full5814-reference audit remains separate.',
                  limits='Only logical tensor bytes were hashed by original GPU audit; shape/stride/dtype also compared. No opaque allocations, storage padding or hidden kernel layout claim.')
    (ROOT/'evidence/fp8/transform-tensor-coverage.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(dict(ranks=len(ranks),postprocess_references_perrank=924,unchanged_raw_parameters_perrank=768))


if __name__ == '__main__':
    main()
