import copy
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import check_g1_raw13
from check_g1_raw13 import audit_path, check_rank, check_response


class RawEvidenceTests(unittest.TestCase):
    def test_response_gold_and_finite(self):
        expected = dict(token_ids=list(range(32)), text='finite response', finish_reason='length')
        result = {'choices': [dict(expected, message={'content': expected['text']},
                                   logprobs=[{str(t): {'logprob': -1.0}} for t in range(32)])]}
        case = dict(ignore_eos=True, max_tokens=32)
        check_response(result, expected, case)
        changed = copy.deepcopy(result)
        changed['choices'][0]['logprobs'][0]['0']['logprob'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            check_response(changed, expected, case)
        changed = copy.deepcopy(result)
        changed['choices'][0]['token_ids'][0] = 90
        with self.assertRaisesRegex(ValueError, 'gold differs'):
            check_response(changed, expected, case)

    def test_natural_eos_separate(self):
        expected = dict(token_ids=[1], text='42', finish_reason='stop')
        result = {'choices': [dict(expected, message={'content': '42'},
                                   logprobs=[{'1': {'logprob': -0.1}}])]}
        check_response(result, expected, dict(ignore_eos=False))
        expected['finish_reason'] = result['choices'][0]['finish_reason'] = 'length'
        with self.assertRaisesRegex(ValueError, 'natural EOS'):
            check_response(result, expected, dict(ignore_eos=False))

    def test_audit_confinement(self):
        root = pathlib.Path(__file__).resolve().parent
        self.assertEqual(audit_path(root, '/r/audits/cycle1'), root/'audits/cycle1')
        for path in ('/experiment/audits', '/r/../outside', 'relative'):
            with self.assertRaises(ValueError):
                audit_path(root, path)

    def test_rank_changes_and_coverage(self):
        row = dict(tensors={'scale': {'sha256': 'a'}}, runtime_indices={}, kv_runtime_excluded={})
        contract = dict(verified_weight_references=1, runtime_index_references_excluded=0,
                        kv_runtime_references_excluded=0)
        check_rank(row, row, contract, lambda v: v)
        changed = copy.deepcopy(row)
        changed['tensors']['scale']['sha256'] = 'b'
        with self.assertRaisesRegex(ValueError, 'hashes differ'):
            check_rank(changed, row, contract, lambda v: v)
        contract['verified_weight_references'] = 2
        with self.assertRaisesRegex(ValueError, 'coverage differs'):
            check_rank(row, row, contract, lambda v: v)

    def test_complete_raw_check_and_corrupted_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = pathlib.Path(temporary)
            control = assets/'validation/controls/fixture'
            control.mkdir(parents=True)
            def save(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding='utf-8')
                return hashlib.sha256(path.read_bytes()).hexdigest()
            contract = pathlib.Path(__file__).resolve().parents[1]/'runtime/historical-v11/weight_contract.py'
            (assets/'code').mkdir()
            (assets/'code/weight_contract.py').write_bytes(contract.read_bytes())
            tensors = {f'attribute:layer{i}.topk_indices_buffer':
                       dict(dtype='torch.int32', shape=[2048, 2048], bytes=2048*2048*4)
                       for i in range(234)}
            tensors['weight_scale'] = dict(dtype='torch.float32', shape=[1], bytes=4, sha256='original')
            rank_doc = dict(tensors=tensors, excluded={})
            rank_hashes = []
            for rank in range(8):
                sha = save(assets/f'validation/native-repeat7/runtime-rank{rank}.json', rank_doc)
                rank_hashes.append(dict(rank=rank, sha256=sha))
            counts_sha = save(assets/'validation/native-weight-contract.json', dict(ranks=[dict(
                rank=rank, verified_weight_references=1, runtime_index_references_excluded=234,
                kv_runtime_references_excluded=0) for rank in range(8)]))
            cases = [dict(name='fixed32', max_tokens=32, ignore_eos=True),
                     dict(name='business_arithmetic', max_tokens=128, ignore_eos=False),
                     dict(name='business_translation', max_tokens=128, ignore_eos=False)]
            expected = [dict(token_ids=list(range(32)), text='fixture fixed text', finish_reason='length'),
                        dict(token_ids=[42], text='42', finish_reason='stop'),
                        dict(token_ids=[7], text='Hello world.', finish_reason='stop')]
            gold_sha = save(assets/'validation/native-gold.json', dict(requests=cases, results=expected,
                rank_audits=rank_hashes, repeat_evidence=['native_repeat7', 'native_repeat8']))
            config_sha = save(control/'run-manifest.json', dict(run_id='fixture',
                reference_sha256={'native-weight-contract.json': counts_sha}))
            manifest_sha = save(control/'control-input-manifest.json', dict(files=[
                dict(path='run-manifest.json', sha256=config_sha)]))
            save(control/'deployment.json', dict(run_id='fixture', code_commit='fixture-code',
                                                input_manifest_sha256=manifest_sha))
            for kind in ('serverless_llm', 'safetensors'):
                out = control/'service-bootstrap-service13a'/kind
                out.mkdir(parents=True)
                events = []
                for cycle in (1, 2):
                    directory = control/f'weight-audits-service13a/{kind}-{cycle}'
                    for rank in range(8):
                        save(directory/f'runtime-rank{rank}.json', rank_doc)
                    events.append(dict(name='all_native_service_rank_weights_match_gold', cycle=cycle,
                                       audit_directory='/r/'+directory.relative_to(control).as_posix()))
                    for case, result in zip(cases, expected):
                        save(out/f'cycle{cycle}-{case["name"]}.json', {'choices': [dict(result,
                            message={'content': result['text']}, logprobs=[{str(t): {'logprob': -0.1}}
                            for t in result['token_ids']])]})
                (out/'model-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            with patch.object(check_g1_raw13, 'GOLD_SHA', gold_sha):
                result = check_g1_raw13.check(assets, control, 'fixture-code', manifest_sha)
                self.assertEqual((len(result['ranks']), len(result['responses'])), (32, 12))
                # Matching corrupted actual/reference must still fail the frozen gold hash.
                changed = copy.deepcopy(rank_doc)
                changed['tensors']['weight_scale']['sha256'] = 'wrong'
                save(assets/'validation/native-repeat7/runtime-rank0.json', changed)
                save(control/'weight-audits-service13a/serverless_llm-1/runtime-rank0.json', changed)
                with self.assertRaisesRegex(ValueError, 'frozen reference rank hash'):
                    check_g1_raw13.check(assets, control, 'fixture-code', manifest_sha)


if __name__ == '__main__':
    unittest.main()
