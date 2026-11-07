"""New40-ID schedule contracts; no model, cache operation or formal directory claim."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import v4_attempts as attempts
import v4_results as results
from test_v4_cache_evidence import scan

FIXTURE=Path(__file__).resolve().parents[1]/'fixtures/v4-attempts/preregistration-cpu.json'


def protocol40():
    value=json.loads(FIXTURE.read_bytes())
    value.update(schema=2,campaign='v4_4x10_20261107',denominator_per_condition=10)
    conditions=sorted({r['condition'] for r in value['attempts']})
    value['attempts']=[dict(attempt_id=f'v4n40_cpu_{condition}_{block:02}',
                            block=block,condition=condition) for block in range(1,11) for condition in conditions]
    return value


class FortyScheduleTests(unittest.TestCase):
    def parse(self, protocol):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'cpu-protocol.json'
            path.write_text(json.dumps(protocol))
            return attempts.frozen_protocol(path,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_exact_40_cohort_and_old12_both_parse(self):
        self.assertEqual(len(self.parse(protocol40())),40)
        self.assertEqual(len(self.parse(json.loads(FIXTURE.read_bytes()))),12)

    def test_longer_frozen_window_applies_only_to_new40_and_replays_the_same_deadline(self):
        value=protocol40();value['attempt_timeout_seconds']=3600
        self.assertTrue(all(row['attempt_timeout_seconds']==3600 for row in self.parse(value)))
        old=json.loads(FIXTURE.read_bytes());old['attempt_timeout_seconds']=3600
        with self.assertRaises(ValueError):self.parse(old)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);execution=root/'execution';execution.mkdir()
            protocol=root/'cpu-protocol.json';protocol.write_text(json.dumps(value))
            sha=hashlib.sha256(protocol.read_bytes()).hexdigest()
            source=dict(code_commit='a'*40,source_manifest_sha256='b'*64)
            now=[100]
            with mock.patch.object(attempts.time,'monotonic_ns',side_effect=lambda:now[0]):
                journal=attempts.AttemptJournal(execution,value['attempts'][0]['attempt_id'],
                    protocol,sha,'cpu-only',10**20,source,[execution])
                self.assertEqual(journal.record['attempt_deadline_ns'],100+3600_000_000_000)
                now[0]=1500_000_000_000
                target,files,vectors=scan('safetensors_inputs','cold',now[0]-20)
                other,other_files,other_vectors=scan('store_inputs','check',now[0]-40)
                identity={key:journal.identity[key] for key in ('run_id','boot_id','time_namespace','clock_id')}
                journal.startup({},target,files,vectors,other,other_files,other_vectors,identity)
                journal.fail(RuntimeError('CPU fixture'))
            rows,_,_=results.replay(protocol,sha,[execution])
            self.assertEqual(rows[0]['status'],'failed')

    def test_wrong_count_block_duplicate_or_reused_id_rejected(self):
        for kind in ('count','block','duplicate','oldid','denominator','campaign','fraction','schema','pair'):
            value=protocol40()
            if kind=='count':value['attempts'].pop()
            elif kind=='block':value['attempts'][0]['block']=11
            elif kind=='duplicate':value['attempts'][-1]=value['attempts'][0]
            elif kind=='oldid':value['attempts'][0]['attempt_id']='store_disk_cold_01'
            elif kind=='denominator':value['denominator_per_condition']=9
            elif kind=='fraction':value['denominator_per_condition']=10.0
            elif kind=='schema':value['schema']=True
            elif kind=='pair':value['attempts'][0]['condition']=value['attempts'][1]['condition']
            else:value['campaign']='foreign'
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.parse(value)

    def test_40_journal_keeps_failed_id_and_blocks_automatic_continuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);execution=root/'execution';execution.mkdir()
            protocol=root/'cpu-protocol.json';value=protocol40()
            protocol.write_text(json.dumps(value));sha=hashlib.sha256(protocol.read_bytes()).hexdigest()
            source=dict(code_commit='a'*40,source_manifest_sha256='b'*64)
            def start(index):
                return attempts.AttemptJournal(execution,value['attempts'][index]['attempt_id'],
                    protocol,sha,'cpu-only',10**20,source,[execution])
            with mock.patch.object(attempts.time,'monotonic_ns',return_value=100):
                with self.assertRaisesRegex(RuntimeError,'CPU fixture'):
                    with start(0):raise RuntimeError('CPU fixture preparation failure')
                for index in (0,1):
                    with self.assertRaisesRegex(ValueError,'blocks continuation'):start(index)
            rows,summary,_=results.replay(protocol,sha,[execution])
            self.assertEqual(len(rows),40)
            self.assertEqual(sum(row['consumed'] for row in rows),1)
            self.assertEqual(rows[0]['status'],'failed')
            self.assertTrue(all(row['status']=='unstarted' for row in rows[1:]))
            self.assertTrue(all(group['preregistered']==10 for group in summary['conditions'].values()))
            self.assertEqual(sum(group['failed'] for group in summary['conditions'].values()),1)
            self.assertFalse(summary['runtime_qualified'])


if __name__=='__main__':unittest.main()
