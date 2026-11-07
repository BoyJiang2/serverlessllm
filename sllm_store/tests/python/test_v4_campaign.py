"""CPU-only campaign continuation gates; no formal directory claim or model call."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import v4_campaign as campaign
from v4_campaign import next_attempt
from v4_trial_review import review_trial


class CampaignContracts(unittest.TestCase):
    def test_sealed_prestartup_failure_advances_without_promoting_it_or_other_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'v4n40_safetensors_disk_hot_01';base.mkdir()
            journal=root/'journal';journal.mkdir();(journal/'01-result.json').write_text('{"status":"failed"}')
            (base/'failure.json').write_text('{"error":"cache admission"}');(base/'raw').write_bytes(b'original')
            sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
            audit=dict(artifact_kind='failed_prestartup_cache_admission_audit',attempt_id=base.name,
                journal_status='failed',runtime_qualified=False,counts_as_success=False,model_startup_reached=False,
                safe_post_failure_selected_resource_cleanup_verified=True,source_head='c'*40,
                failure_sha256=sha(base/'failure.json'),journal_terminal_sha256=sha(journal/'01-result.json'),
                input_sha256={'raw':sha(base/'raw')})
            report=root/'audit.json';report.write_text(json.dumps(audit))
            approved=dict(classification='prestartup_cache_admission_failed',counts_as_success=False,source_head='c'*40,
                audit_review_path='audit.json',audit_review_sha256=sha(report),failure_sha256=audit['failure_sha256'],
                journal_terminal_sha256=audit['journal_terminal_sha256'])
            row=dict(attempt_id=base.name,consumed=True,status='failed',directory=str(journal),
                     journal_files=[dict(path='01-result.json')])
            pending=dict(attempt_id='v4n40_cpu_next',consumed=False,status='unstarted')
            with mock.patch.object(campaign,'ROOT',root):
                with self.assertRaisesRegex(ValueError,'blocks continuation'):next_attempt([row,pending],'a'*40,root)
                self.assertEqual(next_attempt([row,pending],'a'*40,root,{base.name:approved}),pending)
                self.assertEqual(row['status'],'failed')
                for key,value in [('counts_as_success',True),('source_head','d'*40)]:
                    with self.assertRaises(ValueError):
                        next_attempt([row,pending],'a'*40,root,{base.name:dict(approved,**{key:value})})
                for key,value in [('status','timeout'),('attempt_id','v4n40_unknown')]:
                    with self.assertRaises(ValueError):
                        changed=dict(row,**{key:value});next_attempt([changed,pending],'a'*40,root,{changed['attempt_id']:approved})
                (base/'raw').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'failed raw evidence changed'):
                    next_attempt([row,pending],'a'*40,root,{base.name:approved})

    def test_prior_source_success_requires_exact_sealed_review_and_unchanged_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'v4n40_safetensors_disk_cold_01';base.mkdir()
            raw=base/'raw';raw.write_bytes(b'original')
            sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
            review=dict(runtime_qualified=True,success=True,formal_id_consumed=True,source_head='a'*40,
                attempt_id=base.name,input_sha256={'raw':sha(raw)})
            path=base/'runtime-review.json';path.write_text(json.dumps(review))
            first=dict(attempt_id=base.name,consumed=True,status='evidence_complete_unqualified')
            second=dict(attempt_id='v4n40_cpu_next',consumed=False,status='unstarted')
            prior={base.name:dict(source_head='a'*40,review_sha256=sha(path))}
            with self.assertRaises(ValueError):next_attempt([first,second],'b'*40,root)
            self.assertEqual(next_attempt([first,second],'b'*40,root,prior_qualified=prior),second)
            raw.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'raw evidence changed'):
                next_attempt([first,second],'b'*40,root,prior_qualified=prior)
            raw.write_bytes(b'original');path.write_text(json.dumps(dict(review,source_head='b'*40)))
            with self.assertRaisesRegex(ValueError,'SHA differs'):
                next_attempt([first,second],'b'*40,root,prior_qualified=prior)

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
