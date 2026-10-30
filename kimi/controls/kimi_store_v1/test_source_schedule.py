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
import types

import source_schedule as schedule_module
import test_source_bundle

from source_schedule import PHASES, load_source_plan, source_schedule


class SourceScheduleTests(unittest.TestCase):
    def guard_fixture(self):
        identity=dict(run_id='kimi-export-20261031T000000Z-01234567',holder='a'*32,
            code_commit='b'*40,spec_sha256='c'*64,source_manifest_sha256='d'*64)
        registration=dict(schema='kimi-source-guard-target-v1',identity=identity,
            boot_id='12345678-1234-1234-1234-123456789abc',uid=2147,container_id='e'*64,
            image_id='sha256:'+'f'*64,container_created='2026-10-31T00:00:00.000000000Z')
        labels={'sllm.kimi.'+key:value for key,value in identity.items()}
        labels['sllm.purpose']=schedule_module.GUARD_PURPOSE
        info=dict(Id=registration['container_id'],Image=registration['image_id'],Created=registration['container_created'],
            Config=dict(Labels=labels,User='2147:2147'),HostConfig=dict(RestartPolicy=dict(Name='no')),
            State=dict(Running=True,Pid=54321))
        return registration,info

    def test_guard_identity_refuses_foreign_short_id_boot_owner_image_and_created(self):
        original,info=self.guard_fixture()
        for kind in ('cid','holder','image','created','user','restart','boot','uid'):
            registration=copy.deepcopy(original);candidate=copy.deepcopy(info)
            boot,uid=registration['boot_id'],2147
            if kind=='cid':registration['container_id']='e'*12
            elif kind=='holder':candidate['Config']['Labels']['sllm.kimi.holder']='9'*32
            elif kind=='image':candidate['Image']='sha256:'+'9'*64
            elif kind=='created':candidate['Created']='2026-10-31T00:00:01Z'
            elif kind=='user':candidate['Config']['User']='0'
            elif kind=='restart':candidate['HostConfig']['RestartPolicy']['Name']='always'
            elif kind=='boot':boot='00000000-1234-1234-1234-123456789abc'
            else:uid=0
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                schedule_module.guard_target(registration,candidate,boot_id=boot,uid=uid)

    def guard_cleanup(self,kind='ok'):
        registration,info=self.guard_fixture();calls=[]
        def docker(args,timeout):
            calls.append((args,timeout))
            row=copy.deepcopy(info)
            if args[0]=='inspect' and len(calls)>1:
                row['State']=dict(Running=kind=='still_running',Pid=54321 if kind=='still_running' else 0)
            if kind=='foreign':row['Config']['Labels']['sllm.kimi.holder']='0'*32
            return types.SimpleNamespace(returncode=1 if kind=='daemon' else 0,
                stdout=json.dumps([row]) if args[0]=='inspect' else ('e'*64 if kind=='remains' and args[0]=='ps' else ''),
                stderr='')
        options=dict(boot_id=registration['boot_id'],uid=2147,timeout_seconds=30,docker=docker,
                     process_probe=lambda row:dict(pid=row['State']['Pid'],start_ticks=111))
        return registration,calls,options

    def test_guard_cleanup_stops_then_inspects_removes_and_verifies_only_exact_container(self):
        registration,calls,options=self.guard_cleanup()
        receipt=schedule_module.cleanup_guard_target(registration,**options)
        self.assertEqual([row[0][0] for row in calls],['inspect','stop','inspect','rm','ps'])
        self.assertTrue(all(row[0][-1] in (registration['container_id'],'id='+registration['container_id']) for row in calls))
        self.assertTrue(all(0<row[1]<=30 for row in calls))
        self.assertTrue(receipt['container_absence_verified'])
        self.assertFalse(receipt['resources_released'])
        self.assertFalse(receipt['release_observation_verified'])
        self.assertFalse(receipt['guard_deployed'])
        self.assertFalse(receipt['runtime_admitted'])

    def test_guard_cleanup_does_not_mutate_foreign_or_unreachable_targets(self):
        for kind in ('foreign','daemon'):
            registration,calls,options=self.guard_cleanup(kind)
            with self.subTest(kind=kind),self.assertRaises((ValueError,RuntimeError)):
                schedule_module.cleanup_guard_target(registration,**options)
            self.assertEqual([row[0][0] for row in calls],['inspect'])

    def test_guard_cleanup_requires_actual_stop_and_absence_evidence(self):
        for kind in ('still_running','remains'):
            registration,calls,options=self.guard_cleanup(kind)
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):
                schedule_module.cleanup_guard_target(registration,**options)
            if kind=='still_running':self.assertNotIn('rm',[row[0][0] for row in calls])

    def test_guard_cleanup_invalid_identity_refused_before_any_docker_command(self):
        registration,calls,options=self.guard_cleanup()
        registration['container_id']='--all'
        with self.assertRaises(ValueError):schedule_module.cleanup_guard_target(registration,**options)
        self.assertEqual(calls,[])

    def test_guard_cleanup_does_not_restart_whole_timeout_for_each_command(self):
        registration,calls,options=self.guard_cleanup()
        with patch.object(schedule_module.time,'monotonic',side_effect=[0,1,20,21,31]):
            with self.assertRaises(TimeoutError):schedule_module.cleanup_guard_target(registration,**options)
        self.assertEqual([row[0][0] for row in calls],['inspect','stop'])
        self.assertEqual(calls[1][1],9)

    def test_guard_docker_pins_local_socket_without_changing_global_environment(self):
        with patch.dict(schedule_module.os.environ,DOCKER_HOST='ssh://foreign',DOCKER_CONTEXT='foreign'), \
                patch.object(schedule_module,'_guard_capture') as run:
            schedule_module._guard_docker(['inspect','e'*64],2)
            self.assertEqual(run.call_args.args[0][:3],['/usr/bin/docker','--host','unix:///var/run/docker.sock'])
            self.assertNotIn('DOCKER_HOST',run.call_args.args[2])
            self.assertEqual(schedule_module.os.environ['DOCKER_HOST'],'ssh://foreign')

    def test_guard_process_requires_stable_pid_owner_and_exact_container_cgroup(self):
        registration,info=self.guard_fixture()
        for kind in ('ok','reuse','owner','cgroup'):
            ticks=iter((111,112 if kind=='reuse' else 111))
            def read(path,*args,**kwargs):
                if path.name=='stat':return '54321 (name with ) spaces) R '+'0 '*18+str(next(ticks))+' 0'
                return '0::/system.slice/docker-'+('9'*64 if kind=='cgroup' else info['Id'])+'.scope\n'
            with self.subTest(kind=kind),patch.object(Path,'read_text',read), \
                    patch.object(Path,'stat',return_value=types.SimpleNamespace(st_uid=0 if kind=='owner' else 2147)):
                if kind=='ok':
                    row=schedule_module.guard_process(info)
                    self.assertEqual((row['pid'],row['start_ticks']),(54321,111))
                else:
                    with self.assertRaises(ValueError):schedule_module.guard_process(info)

    def test_guard_capture_limits_pipe_bytes_and_reaps_only_its_cli_child(self):
        class Selector:
            def __init__(self):self.items={}
            def register(self,stream,event,index):
                self.items[index]=types.SimpleNamespace(fd=index,fileobj=stream,data=index)
            def get_map(self):return self.items
            def select(self,timeout):return [(v,None) for v in self.items.values()]
            def unregister(self,stream):
                key=next(k for k,v in self.items.items() if v.fileobj is stream);del self.items[key]
            def close(self):self.items.clear()
        for oversized in (False,True):
            child=types.SimpleNamespace(stdout=Mock(),stderr=Mock(),kill=Mock(),wait=Mock(return_value=0))
            chunks={0:([b'x'*4096]*17 if oversized else [b'[]',b'']),1:[b'']}
            def read(fd,size):self.assertEqual(size,4096);return chunks[fd].pop(0)
            with self.subTest(oversized=oversized), \
                    patch.object(schedule_module.subprocess,'Popen',return_value=child), \
                    patch.object(schedule_module.selectors,'DefaultSelector',Selector), \
                    patch.object(schedule_module.os,'read',side_effect=read):
                if oversized:
                    with self.assertRaisesRegex(ValueError,'64KiB'):
                        schedule_module._guard_capture(['cpu-fixture'],10,{})
                    child.kill.assert_called_once()
                    child.wait.assert_called_once()
                else:
                    self.assertEqual(schedule_module._guard_capture(['cpu-fixture'],10,{}).stdout,'[]')
                    child.kill.assert_not_called()
                child.stdout.close.assert_called_once()
                child.stderr.close.assert_called_once()

    def test_guard_capture_selector_initialization_and_close_failures_preserve_original_error(self):
        child=types.SimpleNamespace(stdout=Mock(),stderr=Mock(),kill=Mock(),wait=Mock(return_value=0))
        child.stdout.close.side_effect=OSError('fixture pipe close failed')
        with patch.object(schedule_module.subprocess,'Popen',return_value=child), \
                patch.object(schedule_module.selectors,'DefaultSelector',side_effect=RuntimeError('fixture selector EMFILE')):
            with self.assertRaisesRegex(RuntimeError,'selector EMFILE') as caught:
                schedule_module._guard_capture(['cpu-fixture'],10,{})
        child.kill.assert_called_once()
        child.wait.assert_called_once()
        child.stdout.close.assert_called_once()
        child.stderr.close.assert_called_once()
        self.assertIn('pipe close failed',caught.exception.__notes__[-1])

    def test_remaining_uses_stricter_wall_or_monotonic_clock(self):
        plan,receipt=self.fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        schedule=source_schedule(plan,receipt,now=entered)
        for elapsed,wall,expected in ((10,5,20),(5,10,20),(29.5,29,0.5)):
            self.assertEqual(schedule_module.phase_remaining_seconds(schedule,'preflight',
                entered=entered,elapsed=elapsed,now=entered+timedelta(seconds=wall)),expected)

    def test_remaining_refuses_equality_overrun_and_backward_wall_clock(self):
        plan,receipt=self.fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        schedule=source_schedule(plan,receipt,now=entered)
        for elapsed,wall in ((30,0),(0,30),(31,5),(5,31),(1,-1)):
            with self.assertRaises(TimeoutError):
                schedule_module.phase_remaining_seconds(schedule,'preflight',entered=entered,
                    elapsed=elapsed,now=entered+timedelta(seconds=wall))

    def test_later_phase_does_not_restart_batch_clock(self):
        plan,receipt=self.fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        schedule=source_schedule(plan,receipt,now=entered)
        self.assertEqual(schedule_module.phase_remaining_seconds(schedule,'image_restore',
            entered=entered,elapsed=45,now=entered+timedelta(seconds=40)),15)
        with self.assertRaises(TimeoutError):
            schedule_module.phase_remaining_seconds(schedule,'image_restore',entered=entered,
                elapsed=60,now=entered+timedelta(seconds=40))

    def test_remaining_rejects_nonfinite_or_ambiguous_inputs(self):
        plan,receipt=self.fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        schedule=source_schedule(plan,receipt,now=entered)
        for value in (True,-1,float('nan'),float('inf'),'1'):
            with self.assertRaises(ValueError):
                schedule_module.phase_remaining_seconds(schedule,'preflight',entered=entered,
                    elapsed=value,now=entered)
        for phase,start,end in (('other',entered,entered),
                ('preflight',entered.replace(tzinfo=None),entered),
                ('preflight',entered,entered.replace(tzinfo=None))):
            with self.assertRaises(ValueError):
                schedule_module.phase_remaining_seconds(schedule,phase,entered=start,elapsed=1,now=end)

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
