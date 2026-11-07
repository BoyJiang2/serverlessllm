"""CPU-only campaign continuation gates; no formal directory claim or model call."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from v4_campaign import next_attempt
from v4_trial_review import review_trial


class CampaignContracts(unittest.TestCase):
    def test_only_reviewed_unchanged_success_can_advance_to_next_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'v4n40_cpu_1';base.mkdir();raw=base/'raw';raw.write_bytes(b'CPU evidence')
            review=dict(runtime_qualified=True,success=True,formal_id_consumed=True,source_head='a'*40,
                        attempt_id=base.name,input_sha256={'raw':hashlib.sha256(raw.read_bytes()).hexdigest()})
            path=base/'runtime-review.json';path.write_text(json.dumps(review))
            first=dict(attempt_id=base.name,consumed=True,status='evidence_complete_unqualified')
            second=dict(attempt_id='v4n40_cpu_2',consumed=False,status='unstarted')
            self.assertEqual(next_attempt([first,second],'a'*40,root),second)
            self.assertIsNone(next_attempt([first],'a'*40,root))
            for status in ['failed','timeout','incomplete']:
                with self.assertRaisesRegex(ValueError,'blocks continuation'):
                    next_attempt([dict(first,status=status),second],'a'*40,root)
            with self.assertRaisesRegex(ValueError,'out-of-order'):
                next_attempt([second,first],'a'*40,root)
            for key,value in [('runtime_qualified',False),('success',False),('formal_id_consumed',False),
                              ('source_head','b'*40),('attempt_id','foreign')]:
                path.write_text(json.dumps(dict(review,**{key:value})))
                with self.assertRaisesRegex(ValueError,'runtime review'):
                    next_attempt([first,second],'a'*40,root)
            path.write_text(json.dumps(review));raw.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'raw evidence changed'):
                next_attempt([first,second],'a'*40,root)

    def test_named_runtime_failure_requires_immutable_explicit_ack_and_never_counts_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'v4n40_store_pool_hit_01';base.mkdir();journal=root/'cpu-journal';journal.mkdir()
            failure=dict(artifact_kind='independent_runtime_review_failure',main_runner_normal_completion=True,
                         formal_id_consumed=True,engine_creations=2)
            (base/'failure.json').write_text(json.dumps(failure));(base/'candidate.json').write_text('{}')
            (journal/'07-result.json').write_text('{"cpu_fixture":true}')
            sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
            approved=dict(failure_sha256=sha(base/'failure.json'),candidate_sha256=sha(base/'candidate.json'),
                          journal_terminal_sha256=sha(journal/'07-result.json'),counts_as_success=False)
            row=dict(attempt_id=base.name,consumed=True,status='evidence_complete_unqualified',
                     directory=str(journal),journal_files=[{'path':'07-result.json'}])
            pending=dict(attempt_id='v4n40_cpu_next',consumed=False,status='unstarted')
            with self.assertRaises(FileNotFoundError):next_attempt([row,pending],'a'*40,root)
            self.assertEqual(next_attempt([row,pending],'a'*40,root,{base.name:approved}),pending)
            with self.assertRaisesRegex(ValueError,'blocks continuation'):
                next_attempt([dict(row,status='failed'),pending],'a'*40,root,{base.name:approved})
            with self.assertRaises(ValueError):
                next_attempt([row,pending],'a'*40,root,{base.name:dict(approved,counts_as_success=True)})
            (journal/'07-result.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'original evidence changed'):
                next_attempt([row,pending],'a'*40,root,{base.name:approved})

    def test_revoked_pilot_cannot_be_promoted_by_any_old_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'trial-cache-qualification-revoked.json').write_text('{}')
            with self.assertRaisesRegex(AssertionError,'revoked'):
                review_trial(root,publish=False)


if __name__=='__main__':unittest.main()
