"""Read-only recomputation of raw G1 rank and business evidence; no runtime gate."""
import hashlib
import importlib.util
import json
import math
import pathlib
import sys

GOLD_SHA = '9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0'
CONTRACT_SHA = 'ed03317a0c8647069041df7c5b9a12910985a12cf9f4d871cd52ec78da7f6a95'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_path(control, value):
    path = pathlib.PurePosixPath(value)
    require(path.is_absolute() and path.parts[:2] == ('/', 'r') and '..' not in path.parts,
            'audit path is outside the original /r output mount')
    result = control.joinpath(*path.parts[2:]).resolve()
    require(result.is_relative_to(control.resolve()), 'audit path escaped run')
    return result


def check_response(result, expected, case):
    require(len(result['choices']) == 1, 'response choice count differs')
    choice = result['choices'][0]
    tokens = choice['token_ids']
    require(tokens == expected['token_ids'] and choice['message']['content'] == expected['text']
            and choice['finish_reason'] == expected['finish_reason'], 'business gold differs')
    require(tokens and choice['message']['content'].strip(), 'empty business output')
    require(len(choice['logprobs']) == len(tokens), 'logprob coverage differs')
    require(all(math.isfinite(row[str(token)]['logprob'])
                for token, row in zip(tokens, choice['logprobs'])), 'nonfinite logprob')
    if case['ignore_eos']:
        require(case['max_tokens'] == 32 and len(tokens) == 32, 'fixed32 coverage differs')
    else:
        require(choice['finish_reason'] == 'stop', 'natural EOS did not complete')


def check_rank(actual, reference, contract, weight_state):
    actual, reference = weight_state(actual), weight_state(reference)
    require(actual['tensors'] == reference['tensors'], 'raw rank weight/scale/derived hashes differ')
    require(actual['kv_runtime_excluded'] == reference['kv_runtime_excluded'], 'KV exclusions differ')
    counts = dict(weight_scale_derived_references=len(actual['tensors']),
                  dsa_workspace_references_excluded=len(actual['runtime_indices']),
                  kv_references_excluded=len(actual['kv_runtime_excluded']))
    require(list(counts.values()) == [contract['verified_weight_references'],
            contract['runtime_index_references_excluded'], contract['kv_runtime_references_excluded']],
            'raw rank coverage differs from frozen contract')
    return counts


def check(assets, control, code, manifest_sha):
    require(not sys.flags.optimize, 'optimized Python forbidden')
    receipts = []

    def load(path):
        require(path.resolve().is_relative_to(assets.resolve()), 'evidence escaped assets')
        data = path.read_bytes()
        receipts.append(dict(path=str(path.relative_to(assets)), bytes=len(data),
                             sha256=hashlib.sha256(data).hexdigest()))
        return json.loads(data)

    config = load(control/'run-manifest.json')
    deployment = load(control/'deployment.json')
    require(deployment['code_commit'] == code and deployment['input_manifest_sha256'] == manifest_sha
            and deployment['run_id'] == config['run_id'], 'deployment binding differs')
    require(digest(control/'control-input-manifest.json') == manifest_sha, 'source manifest differs')
    source_manifest = load(control/'control-input-manifest.json')
    manifest_row = next(row for row in source_manifest['files'] if row['path'] == 'run-manifest.json')
    require(digest(control/'run-manifest.json') == manifest_row['sha256'], 'run manifest differs')
    gold_path = assets/'validation/native-gold.json'
    require(digest(gold_path) == GOLD_SHA, 'frozen gold differs')
    gold = load(gold_path)
    require(len(gold['requests']) == len(gold['results']) == 3, 'business reference coverage differs')
    contract_path = assets/'code/weight_contract.py'
    require(digest(contract_path) == CONTRACT_SHA, 'weight contract source differs')
    spec = importlib.util.spec_from_file_location('pinned_weight_contract', contract_path)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    counts_path = assets/'validation/native-weight-contract.json'
    require(digest(counts_path) == config['reference_sha256']['native-weight-contract.json'],
            'reference coverage contract differs')
    contracts = {r['rank']: r for r in load(counts_path)['ranks']}
    require(set(contracts) == set(range(8)), 'reference rank coverage differs')
    # Gold spells native_repeat7; canonical host navigation spells native-repeat7.
    reference_dir = assets/'validation'/gold['repeat_evidence'][0].replace('native_repeat', 'native-repeat')
    audit_refs = gold['rank_audits']
    require(len(audit_refs) == 8 and {row['rank'] for row in audit_refs} == set(range(8)),
            'frozen gold rank hashes incomplete')
    references = {}
    for row in audit_refs:
        path = reference_dir/f'runtime-rank{row["rank"]}.json'
        require(digest(path) == row['sha256'], 'frozen reference rank hash differs')
        references[row['rank']] = load(path)
    ranks, responses, audits = [], [], set()
    for kind in ('serverless_llm', 'safetensors'):
        out = control/'service-bootstrap-service13a'/kind
        event_path = out/'model-events.jsonl'
        receipts.append(dict(path=str(event_path.relative_to(assets)), bytes=event_path.stat().st_size,
                             sha256=digest(event_path)))
        events = [json.loads(line) for line in event_path.read_text().splitlines()]
        matches = [e for e in events if e['name'] == 'all_native_service_rank_weights_match_gold']
        require(len(matches) == 2 and {e['cycle'] for e in matches} == {1, 2}, 'raw audit events incomplete')
        for event in matches:
            cycle = event['cycle']
            directory = audit_path(control, event['audit_directory'])
            require(directory not in audits, 'audit reused across lifecycle')
            audits.add(directory)
            require({p.name for p in directory.glob('runtime-rank*.json')} ==
                    {f'runtime-rank{rank}.json' for rank in range(8)}, 'raw rank files incomplete')
            for rank in range(8):
                counts = check_rank(load(directory/f'runtime-rank{rank}.json'), references[rank],
                                    contracts[rank], module.weight_state)
                ranks.append(dict(format=kind, cycle=cycle, rank=rank, **counts))
            for case, expected in zip(gold['requests'], gold['results']):
                path = out/f'cycle{cycle}-{case["name"]}.json'
                check_response(load(path), expected, case)
                responses.append(dict(format=kind, cycle=cycle, case=case['name']))
    require(len(ranks) == 32 and len(responses) == 12, 'raw evidence coverage incomplete')
    return dict(status='raw G1 weight and business recomputation passed',
                scope='offline raw evidence only; not full G1 acceptance or R2',
                run_id=config['run_id'], code_commit=code, manifest_sha256=manifest_sha,
                gold_sha256=GOLD_SHA, contract_sha256=CONTRACT_SHA,
                ranks=ranks, responses=responses, files=receipts,
                exclusions='Pinned DSA scratch values and KV runtime exclusions; opaque allocations excluded. '
                           'Weight/scale/derived hash equality follows the pinned postprocessing contract.')


if __name__ == '__main__':
    assets, control, code, manifest_sha = sys.argv[1:]
    print(json.dumps(check(pathlib.Path(assets), pathlib.Path(control), code, manifest_sha), indent=2))
