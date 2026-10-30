import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import subprocess
import sys

import source_schedule as schedule_module
import test_source_bundle

from source_schedule import PHASES, load_source_plan, source_schedule


class SourceScheduleTests(unittest.TestCase):
    def fixture(self):
        # Arithmetic fixture only. These maxima are not production model estimates.
        receipt=dict(run_id='fixture', holder='holder', code_commit='a'*40,
                     spec_sha256='b'*64, manifest_sha256='c'*64)
        plan=dict(schema='kimi-source-window-v1', run_id=receipt['run_id'],holder=receipt['holder'],
            code_commit=receipt['code_commit'],spec_sha256=receipt['spec_sha256'],
            source_manifest_sha256=receipt['manifest_sha256'],resource_id='h20/qjhs003',
            booking_id='bk-fixture',window_start='2026-10-31T00:00:00+08:00',
            window_end='2026-10-31T11:00:00+08:00',cleanup_complete_by='2026-10-31T10:50:00+08:00',
            seconds={name:30 for name in PHASES})
        plan['seconds']['release_observe']=60
        return plan,receipt

    def test_full_budget_exact_boundary_fits_but_one_second_late_does_not(self):
        plan,receipt=self.fixture()
        total=sum(plan['seconds'].values())
        now=datetime.fromisoformat(plan['cleanup_complete_by'])-timedelta(seconds=total)
        result=source_schedule(plan,receipt,now=now)
        self.assertEqual(result['total_max_seconds'],total)
        self.assertTrue(result['timing_fits'])
        self.assertFalse(result['runtime_admitted'])
        self.assertFalse(result['booking_verified'])
        self.assertEqual(datetime.fromisoformat(result['phase_deadlines_utc']['safety_margin']),
                         datetime.fromisoformat(plan['cleanup_complete_by']))
        with self.assertRaises(TimeoutError): source_schedule(plan,receipt,now=now+timedelta(seconds=1))

    def test_outer_3600_cannot_hide_actual_3721_second_total(self):
        plan,receipt=self.fixture()
        plan['seconds']['engine_initialize_and_export']+=3721-sum(plan['seconds'].values())
        deadline=datetime.fromisoformat(plan['cleanup_complete_by'])
        with self.assertRaises(TimeoutError):
            source_schedule(plan,receipt,now=deadline-timedelta(seconds=3600))
        self.assertEqual(source_schedule(plan,receipt,now=deadline-timedelta(seconds=3721))['total_max_seconds'],3721)

    def test_not_started_expired_naive_or_too_late_cleanup_refused(self):
        plan,receipt=self.fixture()
        for now in (datetime.fromisoformat(plan['window_start'])-timedelta(seconds=1),
                    datetime.fromisoformat(plan['window_end']),datetime(2026,10,31)):
            with self.assertRaises((ValueError,TimeoutError)):source_schedule(plan,receipt,now=now)
        plan['cleanup_complete_by']='2026-10-31T10:51:00+08:00'
        with self.assertRaises(ValueError):source_schedule(plan,receipt,now=datetime.fromisoformat(plan['window_start']))

    def test_identity_mismatch_missing_phase_and_bool_budget_refused(self):
        original,receipt=self.fixture()
        for kind in ('holder','hash','missing','bool','observe','naive'):
            plan=copy.deepcopy(original)
            if kind=='holder':plan['holder']='other'
            elif kind=='hash':plan['source_manifest_sha256']='d'*64
            elif kind=='missing':del plan['seconds']['image_restore']
            elif kind=='bool':plan['seconds']['preflight']=True
            elif kind=='observe':plan['seconds']['release_observe']=59
            else:plan['window_start']='2026-10-31T00:00:00'
            with self.assertRaises(ValueError):source_schedule(plan,receipt,now=datetime.fromisoformat(original['window_start']))

    def test_guard_fires_early_enough_for_full_shutdown_observation_and_evidence(self):
        plan,receipt=self.fixture()
        result=source_schedule(plan,receipt,now=datetime.fromisoformat(plan['window_start']))
        gap=datetime.fromisoformat(result['cleanup_complete_by_utc'])-datetime.fromisoformat(result['guard_fire_by_utc'])
        self.assertEqual(gap.total_seconds(),30+60+30+30)
        self.assertLessEqual(datetime.fromisoformat(result['phase_deadlines_utc']['natural_eos_business']),
                             datetime.fromisoformat(result['guard_fire_by_utc']))

    def test_plan_external_hash_and_size_are_enforced(self):
        plan,receipt=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'plan.json';raw=json.dumps(plan).encode();path.write_bytes(raw)
            self.assertEqual(load_source_plan(path,hashlib.sha256(raw).hexdigest()),plan)
            with self.assertRaises(ValueError):load_source_plan(path,'0'*64)
            path.write_bytes(b'x'*65537)
            with self.assertRaises(ValueError):load_source_plan(path,hashlib.sha256(path.read_bytes()).hexdigest())

    def cli_fixture(self, temp, future):
        helper=test_source_bundle.SourceBundleTests()
        repo,source,tree,unused_manifest,commit,initial=helper.fixture(temp)
        spec,spec_path,spec_sha,code,manifest,receipt=helper.run_fixture(temp,repo,commit)
        plan,_=self.fixture()
        plan.update(run_id=spec['run_id'],holder=spec['holder'],code_commit=commit,
            spec_sha256=spec_sha,source_manifest_sha256=receipt['manifest_sha256'])
        start=datetime.now(timezone.utc)+(timedelta(days=1) if future else -timedelta(minutes=1))
        plan.update(window_start=start.isoformat(),window_end=(start+timedelta(hours=2)).isoformat(),
                    cleanup_complete_by=(start+timedelta(hours=2)-timedelta(minutes=10)).isoformat())
        raw=json.dumps(plan).encode();path=Path(temp)/'source-plan.json';path.write_bytes(raw)
        return [sys.executable,'-B',str(Path(schedule_module.__file__)),'--code',str(code),
            '--manifest',str(manifest),'--manifest-sha',receipt['manifest_sha256'],
            '--spec',str(spec_path),'--spec-sha',spec_sha,'--plan',str(path),
            '--plan-sha',hashlib.sha256(raw).hexdigest()]

    def test_real_cli_joins_code_spec_and_plan_but_does_not_grant_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            command=self.cli_fixture(temp,future=False)
            result=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            proof=json.loads(result.stdout)
            self.assertTrue(proof['timing_fits'])
            self.assertFalse(proof['runtime_admitted'])
            self.assertFalse(proof['guard_verified'])

    def test_real_cli_refuses_future_window_without_clock_override(self):
        with tempfile.TemporaryDirectory() as temp:
            command=self.cli_fixture(temp,future=True)
            result=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('has not started',result.stderr)

    def test_observation_can_reserve_overhead_without_shortening_sixty_seconds(self):
        plan,receipt=self.fixture()
        plan['seconds']['release_observe']=90
        result=source_schedule(plan,receipt,now=datetime.fromisoformat(plan['window_start']))
        self.assertEqual(result['minimum_release_observe_seconds'],60)
        self.assertEqual(result['total_max_seconds'],sum(plan['seconds'].values()))
        self.assertEqual(result['total_max_seconds'],330)

    def test_long_booking_does_not_extend_one_batch_lifetime(self):
        plan,receipt=self.fixture()
        now=datetime.fromisoformat(plan['window_start'])
        result=source_schedule(plan,receipt,now=now)
        self.assertEqual(datetime.fromisoformat(result['cleanup_complete_by_utc'])-now,timedelta(seconds=300))
        self.assertLess(datetime.fromisoformat(result['cleanup_complete_by_utc']),
                        datetime.fromisoformat(result['window_cleanup_deadline_utc']))
        self.assertEqual(datetime.fromisoformat(result['guard_fire_by_utc'])-now,timedelta(seconds=150))

    def test_cli_counts_verification_time_instead_of_resetting_budget_after_it(self):
        plan,receipt=self.fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        clock=Mock(wraps=datetime)
        clock.now.side_effect=[entered,entered+timedelta(seconds=31)]
        argv=['source_schedule','--code','code','--manifest','manifest','--manifest-sha','x',
              '--spec','spec','--spec-sha','x','--plan','plan','--plan-sha','x']
        with patch.object(sys,'argv',argv), \
                patch.object(test_source_bundle.bundle,'verify_source_run',return_value=receipt), \
                patch.object(schedule_module,'load_source_plan',return_value=plan), \
                patch.object(schedule_module,'datetime',clock), \
                patch.object(schedule_module.time,'monotonic',side_effect=[0,31]):
            with self.assertRaisesRegex(TimeoutError,'consumed preflight'):
                schedule_module.main()
