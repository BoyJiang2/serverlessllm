import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import subprocess
import socket
import sys
import time
import threading
import types
from contextlib import ExitStack

import source_schedule as schedule_module
import test_source_bundle

from source_schedule import PHASES, load_source_plan, source_schedule


class SourceScheduleTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','anonymous input capture requires POSIX')
    def test_guard_capture_reads_anonymous_input_without_exposing_it(self):
        read_fd,write_fd=os.pipe()
        try:
            os.write(write_fd,b'CPU_ONLY_NOT_A_SECRET\n');os.close(write_fd);write_fd=None
            result=schedule_module._guard_capture([sys.executable,'-B','-c',
                'import sys;assert sys.stdin.buffer.read()==b"CPU_ONLY_NOT_A_SECRET\\n";print("accepted")'],
                3,dict(os.environ),stdin=read_fd)
        finally:
            os.close(read_fd)
            if write_fd is not None:os.close(write_fd)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout,'accepted\n');self.assertEqual(result.stderr,'')

    @unittest.skipUnless(os.name=='posix','host-only sudo input bridge requires POSIX')
    def test_p0_lifecycle_cli_limits_sudo_to_service_submission_and_retains_failure(self):
        for kind in ('success','bad-command','bad-input','failure'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'code').mkdir()
                source=root/'code/source_schedule.py';source.write_text('CPU fixture')
                worker=root/'p0-worker-input.json';worker.write_text('{}')
                captured=[]
                def capture(command,timeout,environment,*,stdin):
                    self.assertEqual(command[:5],['/usr/bin/sudo','-S','-p','','--'])
                    self.assertEqual(os.read(stdin,130),b'CPU_ONLY_NOT_A_SECRET\n')
                    self.assertNotIn('CPU_ONLY_NOT_A_SECRET',str(command))
                    captured.append(command)
                    return types.SimpleNamespace(returncode=0,stdout='created',stderr='')
                def lifecycle(*args,**kwargs):
                    command=['/usr/bin/systemd-run','--system','--no-ask-password','--uid=2147']
                    if kind=='bad-command':command[0]='/bin/sh'
                    kwargs['service_runner'](command,3,dict(PATH='/usr/bin'))
                    if kind=='failure':
                        error=RuntimeError('CPU failure');error.p0_lifecycle_receipt={'resources_released':False};raise error
                    return {'resources_released':False,'driver_exit_verification_owed':True}
                data=b'bad' if kind=='bad-input' else b'CPU_ONLY_NOT_A_SECRET\n'
                with patch.object(schedule_module,'P0_RUN_ROOT',str(root)), \
                        patch.object(schedule_module,'__file__',str(source)), \
                        patch.object(schedule_module.os,'getuid',return_value=2147), \
                        patch.object(schedule_module.sys,'stdin',types.SimpleNamespace(buffer=io.BytesIO(data))), \
                        patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO) as output, \
                        patch.object(schedule_module.sys,'stderr',new_callable=io.StringIO) as errors, \
                        patch.object(schedule_module,'_guard_capture',side_effect=capture), \
                        patch.object(schedule_module,'run_p0_lifecycle',side_effect=lifecycle) as run:
                    argv=['--worker-input',str(worker),'--worker-input-sha','a'*64,
                          '--gate-sha','b'*64,'--booking-sha','c'*64,'--sudo-stdin']
                    if kind=='success':schedule_module.p0_lifecycle_main(argv)
                    else:
                        with self.assertRaises((ValueError,RuntimeError)):schedule_module.p0_lifecycle_main(argv)
                    if kind=='bad-input':run.assert_not_called()
                    if kind=='failure':self.assertIn('"resources_released": false',errors.getvalue())
                    self.assertNotIn('CPU_ONLY_NOT_A_SECRET',output.getvalue()+errors.getvalue())
                self.assertEqual(len(captured),int(kind in ('success','failure')))

    @unittest.skipUnless(os.name=='posix','native pipe capture requires POSIX')
    def test_guard_capture_success_text_contract_uses_real_cpu_child(self):
        result=schedule_module._guard_capture([sys.executable,'-B','-c',
            'import sys;print("text \\u4e2d");print("error",file=sys.stderr)'],3,dict(os.environ))
        self.assertEqual(result.returncode,0)
        self.assertIsInstance(result.stdout,str);self.assertIsInstance(result.stderr,str)
        self.assertEqual(result.stdout.encode('utf-8'),b'text \xe4\xb8\xad\n')

    def test_p0_lifecycle_rejects_component_only_gate_before_creating_anything(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
            gate=dict(schema='kimi-p0-runtime-gate-v1',identity=target['identity'],
                plan_sha256=bound[2]['plan_sha256'],image_id=target['image_id'],
                verdict='PASS_CPU_NATURAL_GUARD_EXIT_ONLY',reviewer='independent',
                scope='P0A_TORCH_IPC_NO_MODEL_NO_STORE',resource_policy='P0_2CPU_4G_GPU4G_MEM8G_LOAD10P_DISK1G_MONITORED_V2')
            raw=json.dumps(gate).encode();(root/'p0-runtime-gate.json').write_bytes(raw)
            with tempfile.TemporaryFile() as events,patch.object(source_bundle,'verify_p0_worker_input',return_value=bound),\
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')),\
                    patch.object(schedule_module,'datetime',clock),\
                    patch.object(schedule_module.time,'monotonic',return_value=0),\
                    patch.object(schedule_module,'run_p0_setup') as setup,\
                    patch.object(schedule_module,'_guard_docker') as docker:
                with self.assertRaisesRegex(ValueError,'complete P0'):
                    schedule_module.run_p0_lifecycle(str(worker),'f'*64,gate_sha256=hashlib.sha256(raw).hexdigest(),
                        booking_sha256='e'*64,events=events)
                setup.assert_not_called();docker.assert_not_called()

    def test_p0_lifecycle_composes_success_and_monitor_failure_with_text_capture(self):
        import source_bundle
        import vllm_export_hook
        successes=('success','background','six-background','owned-gpu')
        for kind in (*successes,'monitor-failure','residual','foreign-gpu','pid-reuse','excess-gpu',
                     'low-memory','high-load','io-busy','missing-stats','missing-monitor'):
            fail_monitor=kind=='monitor-failure'
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
                inputs,receipt,schedule,identity=bound[2:]
                gate=dict(schema='kimi-p0-runtime-gate-v1',identity=identity,plan_sha256=inputs['plan_sha256'],
                    image_id=receipt['image_id'],verdict='PASS_COMPLETE_P0_SOURCE',reviewer='/root/CPU_FIXTURE_ONLY',
                    scope='P0A_TORCH_IPC_NO_MODEL_NO_STORE',resource_policy='P0_2CPU_4G_GPU4G_MEM8G_LOAD10P_DISK1G_MONITORED_V2')
                gate_raw=json.dumps(gate).encode();(root/'p0-runtime-gate.json').write_bytes(gate_raw)
                plan=json.loads((root/'p0-plan.json').read_text())
                booking=dict(id=plan['booking_id'],resource_id='h20/qjhs003',user='boyjiang',
                    start_ms=int(datetime.fromisoformat(plan['window_start']).timestamp()*1000),
                    end_ms=int(datetime.fromisoformat(plan['window_end']).timestamp()*1000))
                snapshot=dict(schema='kimi-p0-booking-snapshot-v1',captured_utc=inputs['batch_entered_utc'],
                    resource=dict(id='h20/qjhs003',active=True,gpu_count=8,cpu_booking_enabled=False),bookings=[booking])
                booking_raw=json.dumps(snapshot).encode();(root/'p0-booking-snapshot.json').write_bytes(booking_raw)
                service=dict(unit='fixture');service_raw=json.dumps(service).encode()
                (root/'source-watchdog-service-plan.json').write_bytes(service_raw)
                registration=dict(target=target,nonce='a'*32,host_pid_namespace=42,plan_sha256=inputs['plan_sha256'])
                setup=dict(registration_sha256='1'*64,service_plan_sha256=hashlib.sha256(service_raw).hexdigest())
                host=options['host_probe'].return_value
                gpu_ids=[target['gpu_uuid']]+['GPU-%08x-1234-1234-1234-123456789abc'%i for i in range(1,8)]
                calls=[]
                def capture(command,timeout,environment):
                    stdout=''.join(uuid+', 0, 0\n' for uuid in gpu_ids) if '--query-gpu=' in command[1] else ''
                    if kind=='foreign-gpu' and '--query-compute-apps=' in command[1] and 'monitor' in calls:
                        stdout=gpu_ids[-1]+', 987\n'
                    if kind in ('owned-gpu','pid-reuse','excess-gpu') and 'monitor' in calls:
                        if '--query-gpu=' in command[1]:stdout=stdout.replace(gpu_ids[0]+', 0, 0',gpu_ids[0]+(', 5000, 50' if kind=='excess-gpu' else ', 1024, 50'))
                        else:stdout=gpu_ids[0]+', 123\n'
                    return types.SimpleNamespace(returncode=0,stdout=stdout,stderr='')
                def docker(args,timeout):
                    if args[0]=='start':calls.append('start');text=target['container_id']+'\n'
                    elif args[0]=='logs':calls.append('logs');text='{"CPU_FIXTURE_ONLY":true}\n'
                    elif args[0]=='ps':
                        text=json.dumps(dict(ID='7'*64,Labels='sllm.kimi.run_id=old' if kind=='residual' else '')) if kind in ('background','residual','missing-stats') else ''
                        if kind=='six-background':text='\n'.join(json.dumps(dict(ID=str(i)*64,Labels='',Names='background-'+str(i),Image='sha256:'+'f'*64,Status='Up 12 hours',Command='CPU context',Mounts='/data/brick1',Ports='',Networks='bridge')) for i in range(1,7))
                    elif args[0]=='stats':
                        text='' if kind=='missing-stats' else json.dumps(dict(ID='7'*64,CPUPerc='0.1%',MemUsage='18GiB',BlockIO='1MB / 2MB',PIDs='2'))
                        if kind=='six-background':text='\n'.join(json.dumps(dict(ID=cid,CPUPerc='0.10%',MemUsage='17.9GiB / 1.781TiB',BlockIO='490MB / 64.1GB',PIDs='272')) for cid in args[5:])
                    else:self.fail('unexpected Docker mutation')
                    return types.SimpleNamespace(returncode=0,stdout=text,stderr='')
                original_stream=vllm_export_hook._metadata_stream
                def stream(path):
                    if str(path)=='/proc/meminfo':
                        return io.BytesIO(b'MemAvailable: 1 kB\n' if kind=='low-memory' and 'monitor' in calls else b'MemAvailable: 999999999 kB\n')
                    if str(path)=='/proc/diskstats':
                        sectors=int(ticks[0]*10000000) if kind=='io-busy' and 'monitor' in calls else 0
                        return io.BytesIO(('8 0 sda 0 0 %d 0 0 0 0 0 0 0 0\n'%sectors).encode())
                    return original_stream(path)
                monitor=dict(container_id=target['container_id'],process_inventory_complete=True,resource_monitoring_verified=True)
                member=dict(pid=123,start_ticks=42,uid=2147)
                monitor['process']=dict(member,cgroup='owned')
                monitor['current_processes']=[member]
                def monitored(*args,**kwargs):
                    calls.append('monitor')
                    if fail_monitor:
                        error=RuntimeError('CPU monitor fault');error.p0_monitor_receipt=monitor;raise error
                    try:context=kwargs['resource_probe'](monitor)
                    except BaseException as error:error.p0_monitor_receipt=monitor;raise
                    self.assertTrue(context['p0_budget_verified']);self.assertFalse(context['whole_node_exclusive'])
                    if kind=='background':
                        raw=(root/context['context_file']).read_bytes()
                        self.assertEqual(hashlib.sha256(raw).hexdigest(),context['context_sha256'])
                        self.assertEqual(json.loads(raw)['background_stats'][0]['ID'],'7'*64)
                    if kind=='missing-monitor':monitor['resource_monitoring_verified']=False
                    return monitor
                def recorded(label,result):
                    def run(*args,**kwargs):calls.append(label);return result
                    return run
                with tempfile.TemporaryFile() as events,ExitStack() as stack:
                    for manager in (
                        patch.object(source_bundle,'verify_p0_worker_input',return_value=bound),
                        patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')),
                        patch.object(schedule_module,'P0_RUN_ROOT',str(root)),
                        patch.object(schedule_module,'datetime',clock),
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]),
                        patch.object(schedule_module.time,'sleep',side_effect=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds)),
                        patch.object(schedule_module,'_watchdog_host',return_value=host),
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'),
                        patch.object(schedule_module,'_watchdog_pid',return_value=dict(member,cgroup='owned',boot_id=host['boot_id'],start_ticks=43 if kind=='pid-reuse' else 42)),
                        patch.object(schedule_module.os,'getloadavg',side_effect=lambda:(100 if kind=='high-load' and 'monitor' in calls else 0,0,0)),
                        patch.object(schedule_module.os,'cpu_count',return_value=8),
                        patch.object(vllm_export_hook,'_metadata_stream',side_effect=stream),
                        patch.object(schedule_module,'_guard_capture',side_effect=capture),
                        patch.object(schedule_module,'_guard_docker',side_effect=docker),
                        patch.object(schedule_module,'run_p0_setup',side_effect=recorded('setup',setup)),
                        patch.object(schedule_module,'load_watchdog_inputs',return_value=(None,root,registration,None,schedule,identity)),
                        patch.object(schedule_module,'read_watchdog_ready',return_value={}),
                        patch.object(schedule_module,'probe_watchdog_service',return_value=dict(service_invocation_id='b'*32)),
                        patch.object(schedule_module,'monitor_p0_target',side_effect=monitored),
                        patch.object(schedule_module,'verify_p0_component_output',return_value=dict(worker_output_verified=True)),
                        patch.object(schedule_module,'cleanup_guard_target',side_effect=recorded('cleanup',dict(container_id=target['container_id'],container_absence_verified=True))),
                        patch.object(schedule_module,'observe_p0_release',side_effect=recorded('observe',dict(container_release_observation_verified=True))),
                        patch.object(schedule_module,'await_p0_guard_exit',side_effect=recorded('guard_exit',dict(guard_resources_released=True)))):
                        stack.enter_context(manager)
                    args=dict(gate_sha256=hashlib.sha256(gate_raw).hexdigest(),booking_sha256=hashlib.sha256(booking_raw).hexdigest(),events=events)
                    if kind not in successes:
                        with self.assertRaises((RuntimeError,ValueError)) as caught:
                            schedule_module.run_p0_lifecycle(str(worker),'f'*64,**args)
                        if kind not in ('residual','missing-stats'):self.assertIn('guard_exit',caught.exception.p0_lifecycle_receipt)
                    else:
                        result=schedule_module.run_p0_lifecycle(str(worker),'f'*64,**args)
                        self.assertTrue(result['target_and_guard_release_verified'])
                        self.assertFalse(result['resources_released']);self.assertTrue(result['driver_exit_verification_owed'])
                        self.assertEqual((root/'p0-component.stdout.log').read_bytes(),b'{"CPU_FIXTURE_ONLY":true}\n')
                        terminal=json.loads((root/'p0-lifecycle-receipt.json').read_bytes())
                        self.assertTrue(terminal['target_and_guard_release_verified'])
                        events.seek(0)
                        self.assertTrue(all(len(line)<=8192 for line in events.readlines()))
                expected=[] if kind in ('residual','missing-stats') else ['setup','start','monitor']+(['logs'] if kind in successes else [])+['cleanup','observe','guard_exit']
                self.assertEqual(calls,expected)

    def source_registration_fixture(self,temp):
        root,worker_path,document=test_source_bundle.SourceBundleTests().source_worker_fixture(temp)
        import source_bundle
        digest=hashlib.sha256(worker_path.read_bytes()).hexdigest()
        _,_,_,receipt,schedule,identity=source_bundle.verify_source_worker_input(worker_path,digest)
        target,_=self.guard_fixture()
        target['identity']=identity
        registration=dict(schema='kimi-source-watchdog-registration-v2',target=target,
            plan_sha256=document['plan_sha256'],batch_entered_utc=document['batch_entered_utc'],
            host_pid_namespace=42,nonce='a'*32,worker_input_sha256=digest)
        return root,registration,receipt,schedule

    def test_source_startup_refuses_legacy_registration_without_worker_input_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root,registration,receipt,schedule=self.source_registration_fixture(temp)
            registration['schema']='kimi-source-watchdog-registration-v1'
            registration.pop('worker_input_sha256')
            path=root/'registration.json';raw=json.dumps(registration).encode();path.write_bytes(raw)
            with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')):
                with self.assertRaisesRegex(ValueError,'exact watchdog registration'):
                    schedule_module.load_watchdog_inputs(path,hashlib.sha256(raw).hexdigest())

    def test_source_registration_consumes_real_bound_input_and_rejects_changed_anchor_sha_or_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root,original,receipt,schedule=self.source_registration_fixture(temp)
            path=root/'registration.json'
            for mode in ('ok','anchor','input_sha','target','worker_changed'):
                with self.subTest(mode=mode):
                    registration=copy.deepcopy(original)
                    worker=root/'source-worker-input.json';raw_worker=worker.read_bytes()
                    if mode=='anchor':
                        registration['batch_entered_utc']=(datetime.fromisoformat(
                            registration['batch_entered_utc'])+timedelta(seconds=1)).isoformat()
                    elif mode=='input_sha':registration['worker_input_sha256']='0'*64
                    elif mode=='target':registration['target']['identity']['holder']='0'*32
                    elif mode=='worker_changed':worker.write_bytes(raw_worker+b' ')
                    raw=json.dumps(registration).encode();path.write_bytes(raw)
                    try:
                        with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')):
                            if mode=='ok':
                                _,_,actual,verified,bound_schedule,identity=schedule_module.load_watchdog_inputs(
                                    path,hashlib.sha256(raw).hexdigest())
                                self.assertEqual(actual,original)
                                self.assertEqual(verified['worker_input_sha256'],original['worker_input_sha256'])
                                self.assertEqual(bound_schedule,schedule)
                                self.assertEqual(identity,original['target']['identity'])
                                self.assertFalse(bound_schedule['runtime_admitted'])
                            else:
                                with self.assertRaises(ValueError):
                                    schedule_module.load_watchdog_inputs(path,hashlib.sha256(raw).hexdigest())
                    finally:worker.write_bytes(raw_worker)

    def test_docker_nanosecond_times_keep_same_microsecond_value_and_original_identity(self):
        self.assertEqual(schedule_module._utc('2026-10-31T00:00:00.123456789Z'),
                         datetime(2026,10,31,0,0,0,123456,tzinfo=timezone.utc))
        self.assertEqual(schedule_module._utc('2026-10-31T08:00:00.123456789+08:00'),
                         datetime(2026,10,31,0,0,0,123456,tzinfo=timezone.utc))
        target,info=self.guard_fixture()
        schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147)
        info['Created']=info['Created'].replace('000000000','000000001')
        with self.assertRaises(ValueError):schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147)

    def create_control(self,temp):
        plan,receipt,target,info=self.p0_fixture()
        root=Path(temp);(root/'code').mkdir();(root/'code/source_schedule.py').write_text('# CPU code-path fixture')
        worker=root/'p0-worker-input.json';worker.write_text('{}')
        receipt.update(run_root=str(root),image_id=target['image_id'],gpu_uuid=target['gpu_uuid'],
            worker_input_sha256=target['worker_input_sha256'],environment_sha256=target['environment_sha256'])
        entered=datetime.fromisoformat(plan['window_start']);ticks=[0.]
        schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
        command=schedule_module.p0_container_command(target['identity'],run_root=str(root),image_id=target['image_id'],
            gpu_uuid=target['gpu_uuid'],worker_input_sha256=target['worker_input_sha256'],environment_sha256=target['environment_sha256'])
        info['Config'].update(Cmd=command[command.index(target['image_id'])+1:],WorkingDir=str(root))
        info['Mounts']=[dict(Type='bind',Source=source,Destination=source,RW=writable)
            for source,writable in schedule_module._p0_mounts(str(root))]
        info['State']=dict(Running=False,Pid=0)
        info['Created']=entered.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+'.000000000Z'
        info['Name']='/'+target['identity']['run_id']+'-'+target['identity']['holder']
        fixed=schedule_module._p0_environment(target['gpu_uuid'])
        image=dict(Id=target['image_id'],Config=dict(Env=[row for row in info['Config']['Env'] if row.split('=',1)[0] not in fixed]))
        host=dict(uid=2147,boot_id=target['boot_id'],pid_namespace=42,cgroup='0::/user.slice\n')
        def docker(args,timeout):
            self.assertTrue(0<timeout<=10)
            value='' if args[0]=='ps' else json.dumps([image]) if args[:2]==['image','inspect'] else \
                target['container_id']+'\n' if args[0]=='create' else json.dumps([info])
            return types.SimpleNamespace(returncode=0,stdout=value,stderr='')
        clock=Mock(wraps=datetime);clock.now.side_effect=lambda tz:entered+timedelta(seconds=ticks[0])
        raw=json.dumps(plan).encode();(root/'p0-plan.json').write_bytes(raw)
        bound=(worker,root,dict(schema='kimi-p0-worker-input-v1',batch_entered_utc=entered.isoformat(),
            plan_sha256=hashlib.sha256(raw).hexdigest(),environment_sha256=target['environment_sha256']),receipt,schedule,target['identity'])
        options=dict(docker=Mock(side_effect=docker),host_probe=Mock(return_value=host))
        return root,worker,target,info,image,ticks,clock,bound,options

    @unittest.skipUnless(os.name=='posix','Linux metadata-create path; executed separately with existing WSL Python3.10')
    def test_p0_create_metadata_binds_pinned_image_env_and_stopped_target_without_start(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
            with (root/'events').open('xb',buffering=0) as events, \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
                result=schedule_module.create_p0_target(str(worker),'f'*64,events=events,**options)
            self.assertEqual(result['container_id'],target['container_id'])
            self.assertTrue(result['image_environment_verified']);self.assertTrue(result['stopped_target_verified'])
            self.assertTrue(result['cleanup_owed'])
            for key in ('runtime_abi_verified','runtime_admitted','resources_released'):self.assertFalse(result[key])
            self.assertEqual([c.args[0][0] for c in options['docker'].call_args_list],['ps','image','create','inspect'])
            self.assertEqual(result['target']['run_root'],str(root))
            rows=[json.loads(line) for line in (root/'events').read_text().splitlines()]
            self.assertEqual([r['event'] for r in rows],['create_prepared','create_verified'])

    @unittest.skipUnless(os.name=='posix','Linux metadata-create path; executed separately with existing WSL Python3.10')
    def test_p0_create_refuses_existing_name_image_env_wrong_bundle_host_or_expired_before_create(self):
        import source_bundle
        for kind in ('name','image','env','bundle','file','host','late','future_anchor','volumes'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
                verify=Mock(return_value=bound)
                if kind=='image':image['Id']='sha256:'+'0'*64
                elif kind=='env':image['Config']['Env'].append('LD_PRELOAD=/other.so')
                elif kind=='bundle':verify.side_effect=ValueError('changed bundle')
                elif kind=='file':(root/'other.py').write_text('# wrong entry')
                elif kind=='host':options['host_probe'].return_value['uid']=0
                elif kind=='late':ticks[0]=30.
                elif kind=='future_anchor':bound[2]['batch_entered_utc']=(datetime.fromisoformat(bound[2]['batch_entered_utc'])+timedelta(seconds=1)).isoformat()
                elif kind=='volumes':image['Config']['Volumes']={'/unregistered':{}}
                else:options['docker'].side_effect=lambda *args:types.SimpleNamespace(returncode=0,stdout='e'*64,stderr='')
                with (root/'events').open('xb',buffering=0) as events, \
                        patch.object(source_bundle,'verify_p0_worker_input',verify), \
                        patch.object(schedule_module,'__file__',str(root/'other.py' if kind=='file' else root/'code/source_schedule.py')), \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
                    with self.assertRaises((ValueError,TimeoutError)) as caught:
                        schedule_module.create_p0_target(str(worker),'f'*64,events=events,**options)
                self.assertFalse(caught.exception.p0_create_receipt['create_attempted'])
                self.assertFalse(caught.exception.p0_create_receipt['cleanup_owed'])
                self.assertFalse(any(c.args[0][0]=='create' for c in options['docker'].call_args_list))

    @unittest.skipUnless(os.name=='posix','Linux metadata-create path; executed separately with existing WSL Python3.10')
    def test_p0_create_timeout_or_partial_failure_retains_exact_recovery_identity_and_never_retries(self):
        import source_bundle
        for kind in ('timeout','late_id','nonzero','short_id','inspect','running','owner','stale','log'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
                original=options['docker'].side_effect
                def docker(args,timeout):
                    if args[0]=='create':
                        if kind in ('timeout','log'):raise subprocess.TimeoutExpired('owned-create',10)
                        if kind=='late_id':ticks[0]=30.
                        if kind=='nonzero':return types.SimpleNamespace(returncode=1,stdout='',stderr='error')
                        if kind=='short_id':return types.SimpleNamespace(returncode=0,stdout='short',stderr='')
                    elif args[0]=='inspect':
                        if kind=='inspect':return types.SimpleNamespace(returncode=1,stdout='',stderr='error')
                        if kind=='running':info['State']=dict(Running=True,Pid=123)
                        if kind=='owner':info['Config']['User']='0'
                        if kind=='stale':info['Created']='2020-01-01T00:00:00.000000000Z'
                    return original(args,timeout)
                options['docker'].side_effect=docker
                write=schedule_module._watchdog_event
                def event(*args):
                    if kind=='log' and args[3]=='create_failed':raise OSError('secondary journal')
                    return write(*args)
                with (root/'events').open('xb',buffering=0) as events, \
                        patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                        patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                        patch.object(schedule_module,'_watchdog_event',side_effect=event):
                    with self.assertRaises((ValueError,RuntimeError,TimeoutError,subprocess.TimeoutExpired)) as caught:
                        schedule_module.create_p0_target(str(worker),'f'*64,events=events,**options)
                state=caught.exception.p0_create_receipt
                self.assertTrue(state['cleanup_owed']);self.assertFalse(state['stopped_target_verified'])
                self.assertEqual(sum(c.args[0][0]=='create' for c in options['docker'].call_args_list),1)
                self.assertFalse(any(c.args[0][0] in ('start','stop','rm') for c in options['docker'].call_args_list))
                if kind in ('timeout','nonzero','short_id','log'):self.assertIsNone(state['container_id'])
                else:self.assertEqual(state['container_id'],target['container_id'])
                if kind=='log':self.assertEqual(caught.exception.p0_create_evidence_error['error_type'],'OSError')

    @unittest.skipUnless(os.name=='posix','Linux metadata-create path; executed separately with existing WSL Python3.10')
    def test_p0_create_cli_uses_existing_root_exclusive_journal_and_never_repeats_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
            args=['--worker-input',str(worker),'--worker-input-sha','f'*64]
            with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'create_p0_target',return_value=dict(runtime_admitted=False)) as create, \
                    patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO) as output:
                schedule_module.p0_create_main(args)
                self.assertEqual(json.loads(output.getvalue()),dict(runtime_admitted=False))
                create.assert_called_once();create.reset_mock()
                with self.assertRaises(FileExistsError):schedule_module.p0_create_main(args)
                create.assert_not_called()

    def registration_control(self,temp):
        import source_bundle
        root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
        with (root/'p0-create-events.jsonl').open('xb',buffering=0) as events, \
                patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                patch.object(schedule_module,'datetime',clock), \
                patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
            created=schedule_module.create_p0_target(str(worker),'f'*64,events=events,**options)
        options['docker'].reset_mock()
        digest=hashlib.sha256((root/'p0-create-events.jsonl').read_bytes()).hexdigest()
        return root,worker,created['target'],info,ticks,clock,bound,options,digest

    @unittest.skipUnless(os.name=='posix','Native WSL Python3.10 covers POSIX guard-input publication')
    def test_p0_guard_registration_roundtrips_created_target_into_existing_consumer_without_deployment(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root,worker,target,info,ticks,clock,bound,options,digest=self.registration_control(temp)
            with (root/'registration-events').open('xb',buffering=0) as events, \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                    patch.object(source_bundle,'verify_p0_run',return_value=bound[3]), \
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                    patch.object(schedule_module.secrets,'token_hex',return_value='9'*32), \
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
                result=schedule_module.materialize_p0_guard_inputs(str(worker),'f'*64,digest,events=events,**options)
                registration=schedule_module.load_source_plan(root/'registration.json',result['registration_sha256'])
                plan=schedule_module.load_source_plan(root/'source-watchdog-service-plan.json',result['service_plan_sha256'])
            self.assertEqual(registration['target'],target)
            self.assertEqual(registration['batch_entered_utc'],bound[2]['batch_entered_utc'])
            self.assertEqual(plan['command'][-3:], [str(root/'registration.json'),'--registration-sha',result['registration_sha256']])
            self.assertTrue(result['registration_materialized']);self.assertTrue(result['target_reverified'])
            self.assertEqual(result['pending_partial_cleanup'],[])
            for key in ('creation_cli_exit_verified','guard_deployed','runtime_abi_verified','runtime_admitted','resources_released'):
                self.assertFalse(result[key])
            self.assertEqual([c.args[0] for c in options['docker'].call_args_list],[['inspect',target['container_id']]]*2)
            self.assertFalse((root/'registration.json.partial').exists())

    @unittest.skipUnless(os.name=='posix','Native WSL Python3.10 covers POSIX guard-input publication')
    def test_p0_guard_registration_rejects_bad_creation_provenance_changed_target_host_or_clock(self):
        import source_bundle
        for kind in ('sha','failed_stage','identity','false_proof','future','expired','boot','running','created'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,worker,target,info,ticks,clock,bound,options,digest=self.registration_control(temp)
                path=root/'p0-create-events.jsonl'
                if kind=='sha':digest='0'*64
                elif kind in ('failed_stage','identity','false_proof'):
                    rows=[json.loads(line) for line in path.read_text().splitlines()]
                    if kind=='failed_stage':rows[1]['event']='create_failed'
                    elif kind=='identity':rows[0]['holder']='0'*32
                    else:rows[1]['payload']['stopped_target_verified']=False
                    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                elif kind=='future':ticks[0]=-1.
                elif kind=='expired':ticks[0]=60.
                elif kind=='boot':options['host_probe'].return_value['boot_id']='00000000-1234-1234-1234-123456789abc'
                elif kind=='running':info['State']=dict(Running=True,Pid=123)
                elif kind=='created':info['Created']=info['Created'].replace('000000000','000000001')
                with (root/'registration-events').open('xb',buffering=0) as events, \
                        patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                        patch.object(source_bundle,'verify_p0_run',return_value=bound[3]), \
                        patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
                    with self.assertRaises((ValueError,TimeoutError)):
                        schedule_module.materialize_p0_guard_inputs(str(worker),'f'*64,digest,events=events,**options)
                self.assertFalse((root/'registration.json').exists())
                self.assertFalse((root/'source-watchdog-service-plan.json').exists())

    @unittest.skipUnless(os.name=='posix','Native WSL Python3.10 covers POSIX guard-input publication')
    def test_p0_guard_registration_preserves_published_half_on_failure_and_refuses_overwrite(self):
        import source_bundle,native_index
        with tempfile.TemporaryDirectory() as temp:
            root,worker,target,info,ticks,clock,bound,options,digest=self.registration_control(temp)
            original=native_index._publish
            def publish(directory,name,data):
                if name=='source-watchdog-service-plan.json':raise OSError('controlled second publication failure')
                return original(directory,name,data)
            with (root/'registration-events').open('xb',buffering=0) as events, \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                    patch.object(source_bundle,'verify_p0_run',return_value=bound[3]), \
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                    patch.object(native_index,'_publish',side_effect=publish):
                with self.assertRaisesRegex(OSError,'controlled second') as caught:
                    schedule_module.materialize_p0_guard_inputs(str(worker),'f'*64,digest,events=events,**options)
                raw=(root/'registration.json').read_bytes()
                self.assertEqual(caught.exception.p0_registration_receipt['artifacts'],{'registration.json':hashlib.sha256(raw).hexdigest()})
                options['docker'].reset_mock()
                with self.assertRaises(FileExistsError):
                    schedule_module.materialize_p0_guard_inputs(str(worker),'f'*64,digest,events=events,**options)
                self.assertEqual(raw,(root/'registration.json').read_bytes())
                options['docker'].assert_not_called()
            self.assertFalse((root/'source-watchdog-service-plan.json').exists())

    @unittest.skipUnless(os.name=='posix','Native WSL Python3.10 covers POSIX guard-input publication')
    def test_p0_guard_registration_reports_retained_hardlink_and_cli_rejects_duplicate_journal(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root,worker,target,info,ticks,clock,bound,options,digest=self.registration_control(temp)
            original=Path.unlink
            def unlink(path,*args,**kwargs):
                if path.name=='registration.json.partial':raise OSError('controlled partial unlink')
                return original(path,*args,**kwargs)
            with (root/'registration-events').open('xb',buffering=0) as events, \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                    patch.object(source_bundle,'verify_p0_run',return_value=bound[3]), \
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                    patch.object(Path,'unlink',unlink):
                result=schedule_module.materialize_p0_guard_inputs(str(worker),'f'*64,digest,events=events,**options)
            self.assertEqual(result['pending_partial_cleanup'],['registration.json.partial'])
            self.assertEqual((root/'registration.json').stat().st_ino,(root/'registration.json.partial').stat().st_ino)
            args=['--worker-input',str(worker),'--worker-input-sha','f'*64,'--create-events-sha',digest]
            with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'materialize_p0_guard_inputs',return_value=dict(runtime_admitted=False)) as build, \
                    patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO):
                schedule_module.p0_register_main(args)
                build.assert_called_once();build.reset_mock()
                with self.assertRaises(FileExistsError):schedule_module.p0_register_main(args)
                build.assert_not_called()

    def setup_control(self,temp):
        root,worker,target,info,image,ticks,clock,bound,options=self.create_control(temp)
        create=schedule_module.create_p0_target;register=schedule_module.materialize_p0_guard_inputs
        submit=schedule_module.submit_p0_watchdog
        def create_call(*args,**kwargs):return create(*args,**kwargs,**options)
        def register_call(*args,**kwargs):return register(*args,**kwargs,**options)
        def ready_waiter(path,target,*,remaining,**kwargs):
            remaining()
            unit=target['identity']['run_id']+'-guard-'+target['identity']['holder']+'.service'
            host=dict(uid=2147,boot_id=target['boot_id'],pid=999,start_ticks=123,pid_namespace=42,
                cgroup='0::/system.slice/'+unit+'\n')
            ready=dict(host=host,container_id=target['container_id'],image_id=target['image_id'],
                container_created=target['container_created'],guard_fire_by_utc=kwargs['guard_fire_by_utc'],
                probe_endpoint='@sllm-kimi-'+kwargs['nonce'],runtime_admitted=False)
            with path.open('xb',buffering=0) as stream:
                schedule_module._watchdog_event(stream,target['identity'],kwargs['nonce'],'inputs_verified',dict(
                    registration_sha256=kwargs['registration_sha256'],plan_sha256=kwargs['plan_sha256'],runtime_admitted=False))
                schedule_module._watchdog_event(stream,target['identity'],kwargs['nonce'],'ready',ready)
            return schedule_module.read_watchdog_ready(path,target,**kwargs)
        def probe(plan,target,ready,schedule,**kwargs):
            return dict(host=ready['host'],identity=target['identity'],nonce=kwargs['nonce'],
                container_id=target['container_id'],image_id=target['image_id'],container_created=target['container_created'],
                guard_fire_by_utc=schedule['guard_fire_by_utc'],probe_exchange_verified=True,independent_service_verified=True,
                service_unit=plan['unit'],service_invocation_id='b'*32,service_submission_deadline_verified=False,
                guard_deployed=False,runtime_admitted=False,resources_released=False)
        def submit_call(*args,**kwargs):
            client=kwargs.pop('runner',lambda *a:types.SimpleNamespace(returncode=0,stdout='created',stderr=''))
            return submit(*args,**kwargs,**options,
                runner=client,
                ready_waiter=ready_waiter,service_probe=probe)
        # Only the process boundary is substituted; actual entry parsers and metadata functions run.
        def runner(command,timeout,environment):
            self.assertEqual(command[:6],['/usr/bin/python3','-B','-E','-s','-X','utf8'])
            entry,args=command[7],command[8:]
            with patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO) as output:
                if entry=='p0-create':schedule_module.p0_create_main(args)
                elif entry=='p0-register':schedule_module.p0_register_main(args)
                else:schedule_module.watchdog_main(args,service_submit_only=entry=='service-submit',service_probe_only=entry=='service-probe')
            return types.SimpleNamespace(returncode=0,stdout=output.getvalue(),stderr='')
        return root,worker,ticks,clock,bound,options,create_call,register_call,submit_call,probe,runner

    @unittest.skipUnless(os.name=='posix','Native WSL covers setup CLI integration and POSIX capture')
    def test_p0_setup_runs_metadata_entries_and_reprobes_guard_without_gpu_admission(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root,worker,ticks,clock,bound,options,create,register,submit,probe,runner=self.setup_control(temp)
            transport=Mock(side_effect=runner)
            with (root/'setup-events').open('xb',buffering=0) as events, \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                    patch.object(source_bundle,'verify_p0_run',return_value=bound[3]), \
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                    patch.object(schedule_module.secrets,'token_hex',return_value='9'*32), \
                    patch.object(schedule_module,'create_p0_target',side_effect=create), \
                    patch.object(schedule_module,'materialize_p0_guard_inputs',side_effect=register), \
                    patch.object(schedule_module,'submit_p0_watchdog',side_effect=submit), \
                    patch.object(schedule_module,'probe_watchdog_service',side_effect=probe):
                result=schedule_module.run_p0_setup(str(worker),'f'*64,events=events,runner=transport,host_probe=options['host_probe'])
            self.assertEqual([c.args[0][7] for c in transport.call_args_list],['p0-create','p0-register','service-submit','service-probe'])
            self.assertTrue(result['setup_cli_exit_verified']);self.assertTrue(result['guard_deployed'])
            self.assertEqual(result['phase_exitcodes'],dict(create=0,register=0,submit=0,probe=0))
            for key in ('runtime_admitted','runtime_abi_verified','resource_window_verified','resources_released'):self.assertFalse(result[key])
            self.assertEqual(len(result['artifacts']),8)
            self.assertFalse(any(c.args[0][0] in ('start','stop','rm') for c in options['docker'].call_args_list))

    @unittest.skipUnless(os.name=='posix','Native WSL covers service submission integration')
    def test_p0_setup_service_runner_does_not_invent_submit_cli_exit(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root,worker,ticks,clock,bound,options,create,register,submit,probe,runner=self.setup_control(temp)
            transport=Mock(side_effect=runner)
            client=Mock(return_value=types.SimpleNamespace(returncode=0,stdout='created',stderr=''))
            with (root/'setup-events').open('xb',buffering=0) as events, ExitStack() as stack:
                for manager in (
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound),
                    patch.object(source_bundle,'verify_p0_run',return_value=bound[3]),
                    patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')),
                    patch.object(schedule_module,'datetime',clock),
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]),
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'),
                    patch.object(schedule_module.secrets,'token_hex',return_value='9'*32),
                    patch.object(schedule_module,'create_p0_target',side_effect=create),
                    patch.object(schedule_module,'materialize_p0_guard_inputs',side_effect=register),
                    patch.object(schedule_module,'submit_p0_watchdog',side_effect=submit),
                    patch.object(schedule_module,'probe_watchdog_service',side_effect=probe)):
                    stack.enter_context(manager)
                result=schedule_module.run_p0_setup(str(worker),'f'*64,events=events,runner=transport,
                    host_probe=options['host_probe'],service_runner=client)
            client.assert_called_once()
            self.assertEqual([c.args[0][7] for c in transport.call_args_list],['p0-create','p0-register','service-probe'])
            self.assertIsNone(result['phase_exitcodes']['submit'])
            self.assertFalse(result['setup_cli_exit_verified']);self.assertFalse(result['service_submit_cli_exit_verified'])
            self.assertTrue(result['service_submit_function_verified']);self.assertTrue(result['guard_deployed'])
            self.assertEqual(result['systemd_client_returncode'],0)
            self.assertEqual((root/'p0-setup-systemd-client.stdout.log').read_bytes(),b'created')

    @unittest.skipUnless(os.name=='posix','Native WSL covers setup CLI integration and POSIX capture')
    def test_p0_setup_rejects_phase_failures_partial_json_stale_deadline_and_changed_service(self):
        import source_bundle,native_index
        for kind in ('existing','nonzero','partial','late','log','journal','capture_close','register','submit','invocation'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,worker,ticks,clock,bound,options,create,register,submit,probe,original=self.setup_control(temp)
                if kind=='existing':(root/'p0-setup-create.stdout.log').write_bytes(b'original')
                def runner(command,timeout,environment):
                    response=original(command,timeout,environment)
                    entry=command[7]
                    if kind=='nonzero' and entry=='p0-create':response.returncode=7
                    elif kind=='partial' and entry=='p0-create':response.stdout='{"runtime_admitted":fal'
                    elif kind=='late' and entry=='p0-create':ticks[0]=30.
                    elif kind=='capture_close' and entry=='p0-create':
                        error=OSError('controlled capture close after child exit0')
                        error.guard_capture_stdout=response.stdout.encode();error.guard_capture_stderr=b''
                        error.guard_capture_returncode=0;error.guard_capture_child_pid=321
                        error.guard_capture_child_exit_verified=True
                        raise error
                    elif kind=='journal' and entry=='p0-create':
                        path=root/'p0-create-events.jsonl'
                        rows=[json.loads(line) for line in path.read_text().splitlines()]
                        rows[-1]['payload']['container_id']='0'*64
                        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                    elif kind=='register' and entry=='p0-register':response.returncode=8
                    elif kind=='submit' and entry=='service-submit':response.returncode=9
                    elif kind=='invocation' and entry=='service-probe':
                        data=json.loads(response.stdout);data['service_invocation_id']='c'*32;response.stdout=json.dumps(data)
                    return response
                transport=Mock(side_effect=runner)
                original_publish=native_index._publish
                def publish(directory,name,data):
                    if kind=='log' and name=='p0-setup-create.stdout.log':raise OSError('controlled stdout publication')
                    return original_publish(directory,name,data)
                with (root/'setup-events').open('xb',buffering=0) as events, \
                        patch.object(native_index,'_publish',side_effect=publish), \
                        patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                        patch.object(source_bundle,'verify_p0_run',return_value=bound[3]), \
                        patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                        patch.object(schedule_module.secrets,'token_hex',return_value='9'*32), \
                        patch.object(schedule_module,'create_p0_target',side_effect=create), \
                        patch.object(schedule_module,'materialize_p0_guard_inputs',side_effect=register), \
                        patch.object(schedule_module,'submit_p0_watchdog',side_effect=submit), \
                        patch.object(schedule_module,'probe_watchdog_service',side_effect=probe):
                    with self.assertRaises((RuntimeError,ValueError,TimeoutError,OSError)) as caught:
                        schedule_module.run_p0_setup(str(worker),'f'*64,events=events,runner=transport,host_probe=options['host_probe'])
                state=caught.exception.p0_setup_receipt
                self.assertFalse(state['guard_deployed']);self.assertFalse(state['runtime_admitted'])
                expected=0 if kind=='existing' else 1 if kind in ('nonzero','partial','late','log','journal','capture_close') else 2 if kind=='register' else 3 if kind=='submit' else 4
                self.assertEqual(transport.call_count,expected)
                self.assertEqual((root/'p0-setup-create.stdout.log').exists(),kind!='log')
                if kind in ('late','log','journal','capture_close'):self.assertEqual(state['reported_container_id'],'e'*64)
                if kind in ('log','journal','capture_close'):self.assertIsNone(state['container_id'])
                if kind=='existing':self.assertEqual((root/'p0-setup-create.stdout.log').read_bytes(),b'original')
                if kind=='capture_close':self.assertEqual(state['phase_exitcodes']['create'],0)

    @unittest.skipUnless(os.name=='posix','Native WSL covers real owned CPU child capture, no Docker')
    def test_p0_setup_retains_real_nonzero_and_timeout_child_output_and_does_not_continue(self):
        import source_bundle
        for timeout_case in (False,True):
            with self.subTest(timeout=timeout_case),tempfile.TemporaryDirectory() as temp:
                root,worker,ticks,clock,bound,options,create,register,submit,probe,unused=self.setup_control(temp)
                # This executes a tiny real CPU child, never the project/Docker CLI.
                code='import sys,time; print("early",flush=True); print("diagnostic",file=sys.stderr,flush=True); '
                code+='time.sleep(5)' if timeout_case else 'sys.exit(7)'
                def runner(command,timeout,environment):
                    return schedule_module._guard_capture(['/usr/bin/python3','-B','-c',code],1,environment)
                transport=Mock(side_effect=runner)
                original_popen=subprocess.Popen;children=[];waits=[]
                def popen(*args,**kwargs):
                    child=original_popen(*args,**kwargs);children.append(child);original_wait=child.wait
                    def wait(*args,**kwargs):waits.append(kwargs.get('timeout'));return original_wait(*args,**kwargs)
                    child.wait=wait
                    return child
                with (root/'setup-events').open('xb',buffering=0) as events, \
                        patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                        patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.subprocess,'Popen',side_effect=popen), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
                    with self.assertRaises((RuntimeError,subprocess.TimeoutExpired)) as caught:
                        schedule_module.run_p0_setup(str(worker),'f'*64,events=events,runner=transport,host_probe=options['host_probe'])
                self.assertEqual(transport.call_count,1)
                self.assertEqual((root/'p0-setup-create.stdout.log').read_bytes(),b'early\n')
                self.assertEqual((root/'p0-setup-create.stderr.log').read_bytes(),b'diagnostic\n')
                self.assertEqual(caught.exception.p0_setup_receipt['phase_exitcodes'],dict(create=-9 if timeout_case else 7))
                options['docker'].assert_not_called()
                try:
                    if timeout_case:
                        self.assertGreater(waits[-1],0)
                        self.assertEqual(caught.exception.p0_setup_receipt['capture_cleanup']['create'],
                            dict(pid=children[0].pid,direct_child_exit_verified=True))
                    self.assertIsNotNone(children[0].returncode)
                finally:
                    # Reap only this test's known child even when the old implementation fails.
                    children[0].wait(timeout=2)

    @unittest.skipUnless(os.name=='posix','Native WSL capture-close control')
    def test_guard_capture_successful_child_close_failure_retains_output_and_exit_without_rekill(self):
        class Selector:
            def __init__(self):self.items={}
            def register(self,stream,event,index):self.items[index]=types.SimpleNamespace(fd=index,fileobj=stream,data=index)
            def get_map(self):return self.items
            def select(self,timeout):return [(v,None) for v in self.items.values()]
            def unregister(self,stream):
                key=next(k for k,v in self.items.items() if v.fileobj is stream);del self.items[key]
            def close(self):pass
        child=types.SimpleNamespace(pid=321,stdout=Mock(),stderr=Mock(),kill=Mock(),wait=Mock(return_value=0),returncode=0)
        child.stdout.close.side_effect=OSError('controlled close after real exit boundary')
        chunks={0:[b'[]',b''],1:[b'diagnostic',b'']}
        with patch.object(schedule_module.subprocess,'Popen',return_value=child), \
                patch.object(schedule_module.selectors,'DefaultSelector',Selector), \
                patch.object(schedule_module.os,'read',side_effect=lambda fd,size:chunks[fd].pop(0)):
            with self.assertRaises(OSError) as caught:schedule_module._guard_capture(['CPU-close-fixture'],10,{})
        self.assertEqual(caught.exception.guard_capture_stdout,b'[]')
        self.assertEqual(caught.exception.guard_capture_stderr,b'diagnostic')
        self.assertEqual(caught.exception.guard_capture_child_pid,321)
        self.assertTrue(caught.exception.guard_capture_child_exit_verified)
        self.assertEqual(caught.exception.guard_capture_returncode,0)
        child.kill.assert_not_called();child.wait.assert_called_once()

    def submit_control(self):
        plan,receipt,target,info=self.p0_fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
        # Use a Linux path for the pure renderer; filesystem is supplied only by the ready-wait test.
        rendered=schedule_module.watchdog_service_plan(target,'/data/owned/run/registration.json','a'*64,
                                                        schedule,now=entered)
        info['State']=dict(Running=False,Pid=0)
        host=dict(uid=2147,boot_id=target['boot_id'],pid_namespace=42,cgroup='0::/user.slice\n')
        ticks=[0.]
        clock=Mock(wraps=datetime);clock.now.side_effect=lambda tz:entered+timedelta(seconds=ticks[0])
        runner=Mock(return_value=types.SimpleNamespace(returncode=0,stdout='unit created',stderr=''))
        docker=Mock(side_effect=lambda *args:types.SimpleNamespace(returncode=0,stdout=json.dumps([info]),stderr=''))
        ready=Mock(return_value=dict(host=host))
        proof=dict(independent_service_verified=True,identity=target['identity'],container_id=target['container_id'],
            service_invocation_id='b'*32,runtime_admitted=False,resources_released=False)
        probe=Mock(return_value=proof)
        options=dict(nonce='1'*32,host_pid_namespace=42,plan_sha256='c'*64,
            runner=runner,docker=docker,host_probe=Mock(return_value=host),ready_waiter=ready,service_probe=probe)
        return rendered,target,schedule,info,ticks,clock,options

    def test_p0_submit_checks_original_deadline_stopped_target_and_live_service_without_compute(self):
        with tempfile.TemporaryDirectory() as temp:
            plan,target,schedule,info,ticks,clock,options=self.submit_control()
            with (Path(temp)/'events').open('xb',buffering=0) as events, \
                    patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                    patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'):
                result=schedule_module.submit_p0_watchdog(plan,target,schedule,events=events,**options)
            self.assertTrue(result['guard_deployed']);self.assertTrue(result['service_submission_deadline_verified'])
            self.assertFalse(result['runtime_admitted']);self.assertFalse(result['resources_released'])
            options['runner'].assert_called_once()
            argv,timeout,env=options['runner'].call_args.args
            self.assertEqual(argv,plan['command']);self.assertIn('--no-ask-password',argv)
            self.assertEqual(timeout,10)
            self.assertEqual(options['docker'].call_count,2)
            self.assertTrue(all(c.args[0]==['inspect',target['container_id']] for c in options['docker'].call_args_list))
            rows=[json.loads(line) for line in (Path(temp)/'events').read_text().splitlines()]
            self.assertEqual([row['event'] for row in rows],['submission_prepared','submission_verified'])

    def test_p0_submit_refuses_scope_tamper_wrong_host_busy_target_and_late_before_submission(self):
        for kind in ('scope','tamper','host','busy','late','future','log'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                plan,target,schedule,info,ticks,clock,options=self.submit_control()
                if kind=='scope':target['schema']='kimi-source-guard-target-v1'
                elif kind=='tamper':plan['deployment_performed']=0
                elif kind=='host':options['host_probe'].return_value['pid_namespace']=43
                elif kind=='busy':info['State']=dict(Running=True,Pid=123)
                elif kind=='late':ticks[0]=10.
                elif kind=='future':ticks[0]=-1.
                with (Path(temp)/'events').open('xb',buffering=0) as events, \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                        patch.object(schedule_module,'_watchdog_event',side_effect=OSError('controlled log') if kind=='log' else schedule_module._watchdog_event):
                    with self.assertRaises((ValueError,TimeoutError,OSError)) as caught:
                        schedule_module.submit_p0_watchdog(plan,target,schedule,events=events,**options)
                options['runner'].assert_not_called();options['service_probe'].assert_not_called()
                self.assertFalse(caught.exception.guard_submission_receipt['submission_attempted'])

    def test_p0_submission_failure_is_ambiguous_and_never_retried_or_compute_admitted(self):
        for kind in ('timeout','nonzero','ready','probe','late_return','started','log_failure'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                plan,target,schedule,info,ticks,clock,options=self.submit_control()
                if kind=='timeout':options['runner'].side_effect=subprocess.TimeoutExpired('owned-cli',10)
                elif kind=='nonzero':options['runner'].return_value.returncode=1
                elif kind=='ready':options['ready_waiter'].side_effect=TimeoutError('no ready')
                elif kind=='probe':options['service_probe'].return_value['independent_service_verified']=False
                elif kind=='late_return':
                    def late(*args):ticks[0]=10.;return types.SimpleNamespace(returncode=0,stdout='',stderr='')
                    options['runner'].side_effect=late
                elif kind=='started':
                    def started(*args,**kwargs):info['State']=dict(Running=True,Pid=123);return options['service_probe'].return_value
                    options['service_probe'].side_effect=started
                else:
                    options['runner'].side_effect=RuntimeError('owned submit error')
                original=schedule_module._watchdog_event
                def event(*args):
                    if kind=='log_failure' and args[3]=='submission_failed':raise OSError('secondary log')
                    return original(*args)
                with (Path(temp)/'events').open('xb',buffering=0) as events, \
                        patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'), \
                        patch.object(schedule_module,'_watchdog_event',side_effect=event):
                    with self.assertRaises((ValueError,RuntimeError,TimeoutError,subprocess.TimeoutExpired)) as caught:
                        schedule_module.submit_p0_watchdog(plan,target,schedule,events=events,**options)
                options['runner'].assert_called_once()
                receipt=caught.exception.guard_submission_receipt
                self.assertTrue(receipt['submission_attempted']);self.assertFalse(receipt['guard_deployed'])
                self.assertFalse(receipt['runtime_admitted'])
                if kind in ('timeout','nonzero','late_return','log_failure'):
                    self.assertEqual(receipt['submission_outcome'],'unknown')
                if kind=='log_failure':self.assertEqual(caught.exception.guard_submission_evidence_error['error_type'],'OSError')

    def test_p0_service_submit_cli_binds_bundle_and_service_sha_and_rejects_duplicate(self):
        plan,target,schedule,info,ticks,clock,options=self.submit_control()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'registration.json';args=['--registration',str(path),'--registration-sha','a'*64,'--service-plan-sha','b'*64]
            identity=target['identity']
            reg=dict(target=target,nonce='1'*32,host_pid_namespace=42,plan_sha256='c'*64)
            service=copy.deepcopy(plan);service['command'][-3]=str(path)
            inputs=(path,root,reg,{},schedule,identity)
            with patch.object(schedule_module,'load_watchdog_inputs',return_value=inputs) as verify, \
                    patch.object(schedule_module,'load_source_plan',return_value=service) as load, \
                    patch.object(schedule_module,'submit_p0_watchdog',return_value=dict(runtime_admitted=False)) as submit, \
                    patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO) as output:
                schedule_module.watchdog_main(args,service_submit_only=True)
                verify.assert_called_once_with(str(path),'a'*64)
                load.assert_called_once_with(root/'source-watchdog-service-plan.json','b'*64)
                submit.assert_called_once()
                self.assertEqual(json.loads(output.getvalue()),dict(runtime_admitted=False))
                submit.reset_mock()
                with self.assertRaises(FileExistsError):schedule_module.watchdog_main(args,service_submit_only=True)
                submit.assert_not_called()
                (root/'source-watchdog-submit-events.jsonl').unlink()
                service['command'][-1]='d'*64
                with self.assertRaises(ValueError):schedule_module.watchdog_main(args,service_submit_only=True)
                submit.assert_not_called()

    def test_p0_readiness_wait_uses_one_deadline_and_rejects_oversized_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            target,_=self.guard_fixture();path=Path(temp)/'ready';ticks=[0.]
            def remaining():
                if ticks[0]>=0.2:raise TimeoutError('CPU ready budget')
                return 0.2-ticks[0]
            def sleep(seconds):ticks[0]+=seconds
            with patch.object(schedule_module.time,'sleep',side_effect=sleep):
                with self.assertRaises(TimeoutError):schedule_module._await_watchdog_ready(path,target,remaining=remaining)
            path.write_bytes(b'x'*16385)
            with self.assertRaisesRegex(ValueError,'16KiB'):
                schedule_module._await_watchdog_ready(path,target,remaining=lambda:1)
            path.write_bytes(b'{}\n{}\n')
            with self.assertRaises(ValueError):
                schedule_module._await_watchdog_ready(path,target,remaining=lambda:1,
                    nonce='a'*32,registration_sha256='b'*64,plan_sha256='c'*64,guard_fire_by_utc='2026-10-31T00:00:00Z')

    def service_probe_control(self):
        target,schedule,now=self.service_plan_fixture()
        plan=schedule_module.watchdog_service_plan(target,'/data/run/reg','a'*64,schedule,now=now)
        unit=plan['unit'];stamp=(now.astimezone(timezone.utc)+timedelta(seconds=1)).strftime('%a %Y-%m-%d %H:%M:%S.%f UTC')
        host=dict(uid=2147,boot_id=target['boot_id'],pid=999,start_ticks=123,pid_namespace=42,
                  cgroup='0::/system.slice/'+unit+'\n')
        properties=dict(Id=unit,LoadState='loaded',ActiveState='active',SubState='running',MainPID='999',Transient='yes',
            ControlGroup='/system.slice/'+unit,User='2147',Type='exec',Restart='no',Slice='system.slice',
            WorkingDirectory='/data/run',KillMode='control-group',UMask='0077',MemoryMax=str(256*1024**2),
            CPUQuotaPerSecUSec='1s',TasksMax='64',TimeoutStartUSec='10s',TimeoutStopUSec='5s',
            RuntimeMaxUSec=str(plan['max_runtime_seconds'])+'s',ExecMainStartTimestamp=stamp,
            ActiveEnterTimestamp=stamp,InvocationID='b'*32)
        command=dict(argv=plan['command'][-10:],executable_device=1,executable_inode=2)
        proof=dict(host=host,identity=target['identity'],nonce='1'*32,container_id=target['container_id'],
            image_id=target['image_id'],container_created=target['container_created'],
            guard_fire_by_utc=schedule['guard_fire_by_utc'],probe_exchange_verified=True,runtime_admitted=False)
        def query(unit,timeout):
            self.assertTrue(0<timeout<=5)
            return types.SimpleNamespace(returncode=0,stdout=''.join(k+'='+v+'\n' for k,v in properties.items()),stderr='')
        options=dict(nonce='1'*32,host_pid_namespace=42,guard_fire_by_utc=schedule['guard_fire_by_utc'],
            query=Mock(side_effect=query),pid_probe=Mock(side_effect=lambda pid:copy.deepcopy(host)),
            command_probe=Mock(side_effect=lambda pid:copy.deepcopy(command)),active_probe=Mock(return_value=proof))
        clock=Mock(wraps=datetime);clock.now.return_value=now+timedelta(seconds=2)
        return plan,target,dict(host=host),schedule,properties,command,proof,options,clock

    def test_service_probe_checks_owned_unit_process_limits_and_active_exchange(self):
        plan,target,ready,schedule,properties,command,proof,options,clock=self.service_probe_control()
        with patch.object(schedule_module,'datetime',clock):
            result=schedule_module.probe_watchdog_service(plan,target,ready,schedule,**options)
        self.assertTrue(result['independent_service_verified'])  # Explicit systemd/proc/probe substitutes.
        for name in ('guard_deployed','runtime_admitted','resources_released','service_submission_deadline_verified'):
            self.assertFalse(result[name])
        self.assertEqual(options['query'].call_count,2)
        self.assertEqual(options['pid_probe'].call_count,2)
        self.assertEqual(options['command_probe'].call_count,2)
        options['active_probe'].assert_called_once()

    def test_service_probe_refuses_foreign_inactive_or_weakened_service_before_socket(self):
        for field,value in (('MainPID','1000'),('ControlGroup','/user.slice/other'),('User','root'),
            ('Type','simple'),('Restart','always'),('ActiveState','inactive'),('MemoryMax','infinity'),
            ('TasksMax','infinity'),('CPUQuotaPerSecUSec','infinity'),('TimeoutStopUSec','10s'),
            ('RuntimeMaxUSec','1d'),('InvocationID','unknown')):
            with self.subTest(field=field):
                plan,target,ready,schedule,properties,command,proof,options,clock=self.service_probe_control()
                properties[field]=value
                with patch.object(schedule_module,'datetime',clock),self.assertRaises(ValueError):
                    schedule_module.probe_watchdog_service(plan,target,ready,schedule,**options)
                options['active_probe'].assert_not_called()

    def test_service_probe_refuses_late_activation_wrong_command_cgroup_and_stale_proof(self):
        for kind in ('late','argv','cgroup','proof'):
            with self.subTest(kind=kind):
                plan,target,ready,schedule,properties,command,proof,options,clock=self.service_probe_control()
                if kind=='late':properties['ActiveEnterTimestamp']=(datetime.fromisoformat(plan['launch_before_utc'])+
                    timedelta(seconds=11)).strftime('%a %Y-%m-%d %H:%M:%S.%f UTC')
                elif kind=='argv':command['argv'][-1]='0'*64
                elif kind=='cgroup':ready['host']['cgroup']='0::/system.slice/foreign.service\n'
                else:proof['probe_exchange_verified']=False
                with patch.object(schedule_module,'datetime',clock),self.assertRaises(ValueError):
                    schedule_module.probe_watchdog_service(plan,target,ready,schedule,**options)

    def test_service_probe_refuses_unit_restart_pid_reuse_or_command_change_during_exchange(self):
        for kind in ('unit','pid','command'):
            with self.subTest(kind=kind):
                plan,target,ready,schedule,properties,command,proof,options,clock=self.service_probe_control()
                def exchange(*args,**kwargs):
                    if kind=='unit':properties['InvocationID']='c'*32
                    elif kind=='pid':ready['host']['start_ticks']+=1
                    else:command['executable_inode']+=1
                    return proof
                options['active_probe'].side_effect=exchange
                with patch.object(schedule_module,'datetime',clock),self.assertRaisesRegex(ValueError,'changed'):
                    schedule_module.probe_watchdog_service(plan,target,ready,schedule,**options)

    def test_service_probe_query_and_pid_time_consume_same_absolute_budget(self):
        for kind in ('query','pid'):
            with self.subTest(kind=kind):
                plan,target,ready,schedule,properties,command,proof,options,clock=self.service_probe_control()
                ticks=[0.0];original=options[kind if kind=='query' else 'pid_probe'].side_effect
                def delayed(*args):ticks[0]=5.0;return original(*args)
                options[kind if kind=='query' else 'pid_probe'].side_effect=delayed
                with patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]), \
                        self.assertRaises(TimeoutError):
                    schedule_module.probe_watchdog_service(plan,target,ready,schedule,**options)
                options['active_probe'].assert_not_called()
                options['command_probe'].assert_not_called()

    def test_service_probe_rejects_tampered_plan_and_duplicate_properties_before_pid_reads(self):
        for kind in ('plan','json_type','duplicate','missing','daemon'):
            with self.subTest(kind=kind):
                plan,target,ready,schedule,properties,command,proof,options,clock=self.service_probe_control()
                original=options['query'].side_effect
                if kind=='plan':plan['command'].insert(2,'--scope')
                elif kind=='json_type':plan['deployment_performed']=0
                elif kind=='missing':properties.pop('MemoryMax')
                else:
                    def query(*args):
                        response=original(*args)
                        if kind=='duplicate':response.stdout+='MainPID=999\n'
                        else:response.returncode=1
                        return response
                    options['query'].side_effect=query
                with patch.object(schedule_module,'datetime',clock),self.assertRaises((ValueError,RuntimeError)):
                    schedule_module.probe_watchdog_service(plan,target,ready,schedule,**options)
                options['pid_probe'].assert_not_called()
                options['active_probe'].assert_not_called()

    def test_service_duration_parser_and_systemctl_local_bounded_command(self):
        self.assertEqual(schedule_module._service_seconds('1h 2min 3s'),3723)
        for value in ('infinity','1.5s','1s garbage','1000000','1ms',''):
            with self.assertRaises(ValueError):schedule_module._service_seconds(value)
        with patch.object(schedule_module,'_guard_capture',return_value=object()) as capture:
            schedule_module._guard_systemctl('owned.service',2)
        argv,timeout,env=capture.call_args.args
        self.assertEqual(argv[:4],['/usr/bin/systemctl','--system','--no-pager','--timestamp=us+utc'])
        self.assertEqual(timeout,2)
        self.assertEqual(env,{'PATH':'/usr/bin:/bin','LC_ALL':'C','TZ':'UTC','SYSTEMD_COLORS':'0'})

    def test_command_reader_uses_bounded_raw_bytes_and_actual_executable_inode(self):
        original=Path
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);proc=root/'42';proc.mkdir()
            executable=root/'python3';executable.write_bytes(b'CPU inode fixture')
            os.link(executable,proc/'exe')
            command=proc/'cmdline';command.write_bytes(b'/usr/bin/python3\0-B\0script.py\0')
            def mapped(path):return root if str(path)=='/proc' else executable if str(path)=='/usr/bin/python3' else original(path)
            with patch.object(schedule_module,'Path',side_effect=mapped):
                result=schedule_module._watchdog_command(42)
                self.assertEqual(result['argv'],['/usr/bin/python3','-B','script.py'])
                self.assertEqual(result['executable_inode'],executable.stat().st_ino)
                for raw in (b'missing terminator',b'x'*4097):
                    command.write_bytes(raw)
                    with self.assertRaisesRegex(ValueError,'bounded watchdog'):
                        schedule_module._watchdog_command(42)
                command.write_bytes(b'python\0');(proc/'exe').unlink();(proc/'exe').write_bytes(b'other inode')
                with self.assertRaisesRegex(ValueError,'executable differs'):
                    schedule_module._watchdog_command(42)

    def service_plan_fixture(self):
        target,_=self.guard_fixture();plan,receipt=self.fixture()
        now=datetime.fromisoformat(plan['window_start'])
        return target,source_schedule(plan,receipt,now=now),now

    def test_service_plan_binds_unique_unit_and_original_cleanup_budget_without_execution(self):
        target,schedule,now=self.service_plan_fixture()
        with patch.object(schedule_module.subprocess,'Popen',side_effect=AssertionError('must not deploy')):
            result=schedule_module.watchdog_service_plan(target,'/data/owned/run/registration.json','a'*64,
                                                         schedule,now=now)
        command=result['command']
        self.assertEqual(command[:2],['/usr/bin/systemd-run','--system'])
        self.assertIn(target['identity']['run_id'],result['unit'])
        self.assertIn(target['identity']['holder'],result['unit'])
        self.assertIn('--property=Type=exec',command)
        self.assertIn('--property=Slice=system.slice',command)
        self.assertIn('--property=MemoryMax=256M',command)
        self.assertIn('--uid=2147',command)
        self.assertIn('--property=Restart=no',command)
        self.assertNotIn('--scope',command)
        self.assertNotIn('--pty',command)
        self.assertEqual(command[-10:],['/usr/bin/python3','-B','-E','-s','/data/owned/run/code/source_schedule.py',
                                       'guard','--registration','/data/owned/run/registration.json',
                                       '--registration-sha','a'*64])
        self.assertEqual(command[-4:],['--registration','/data/owned/run/registration.json','--registration-sha','a'*64])
        self.assertLessEqual(datetime.fromisoformat(result['launch_before_utc'])+timedelta(
            seconds=result['startup_grace_seconds']+result['max_runtime_seconds']+result['stop_grace_seconds']),
            datetime.fromisoformat(schedule['cleanup_complete_by_utc']))
        for name in ('deployment_performed','independent_service_verified','guard_deployed','runtime_admitted'):
            self.assertFalse(result[name])

    def test_service_plan_refuses_noncanonical_paths_shell_expansion_and_bad_sha(self):
        target,schedule,now=self.service_plan_fixture()
        for path in (None,'relative','//data/run/reg','/reg','/data/../run/reg','/data/./run/reg',
                     '/data/run a/reg','/data/$USER/reg','/data/%h/reg','/data/run\n/reg','D:/run/reg'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                schedule_module.watchdog_service_plan(target,path,'a'*64,schedule,now=now)
        for digest in (None,'a'*63,'A'*64):
            with self.assertRaises(ValueError):
                schedule_module.watchdog_service_plan(target,'/data/run/reg',digest,schedule,now=now)

    def test_service_plan_rejects_future_late_or_too_short_original_window(self):
        target,schedule,now=self.service_plan_fixture()
        for when in (now-timedelta(seconds=1),datetime.fromisoformat(schedule['guard_fire_by_utc'])):
            with self.assertRaises(TimeoutError):
                schedule_module.watchdog_service_plan(target,'/data/run/reg','a'*64,schedule,now=when)
        with self.assertRaises(ValueError):
            schedule_module.watchdog_service_plan(target,'/data/run/reg','a'*64,schedule,now=now.replace(tzinfo=None))
        schedule['cleanup_complete_by_utc']=(now+timedelta(seconds=25)).isoformat()
        with self.assertRaises(TimeoutError):
            schedule_module.watchdog_service_plan(target,'/data/run/reg','a'*64,schedule,now=now)

    def test_service_plan_new_holder_gets_separate_unit_and_later_render_reduces_runtime(self):
        target,schedule,now=self.service_plan_fixture()
        first=schedule_module.watchdog_service_plan(target,'/data/run/reg','a'*64,schedule,now=now)
        target['identity']['holder']='b'*32
        second=schedule_module.watchdog_service_plan(target,'/data/run/reg','a'*64,schedule,now=now+timedelta(seconds=1))
        self.assertNotEqual(first['unit'],second['unit'])
        self.assertEqual(first['max_runtime_seconds']-1,second['max_runtime_seconds'])

    def test_service_plan_fresh_cpu_process_imports_no_torch_or_vllm(self):
        target,schedule,now=self.service_plan_fixture()
        script=('import sys,json;from datetime import datetime;sys.path.insert(0,sys.argv[1]);'
            'import source_schedule as s;'
            'r=s.watchdog_service_plan(json.loads(sys.argv[2]),"/data/run/reg","a"*64,'
            'json.loads(sys.argv[3]),now=datetime.fromisoformat(sys.argv[4]));'
            'assert "torch" not in sys.modules and "vllm" not in sys.modules;'
            'print(json.dumps({"unit":r["unit"],"deployed":r["deployment_performed"]}))')
        result=subprocess.run([sys.executable,'-B','-E','-s','-c',script,str(Path(__file__).parent.resolve()),
            json.dumps(target),json.dumps(schedule),now.isoformat()],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(unit=target['identity']['run_id']+'-guard-'+
            target['identity']['holder']+'.service',deployed=False))

    def test_service_plan_cli_preserves_source_binding_and_never_enters_watchdog(self):
        target,_=self.guard_fixture();plan,receipt=self.fixture()
        for key in ('run_id','holder','code_commit','spec_sha256'):receipt[key]=target['identity'][key];plan[key]=receipt[key]
        receipt['manifest_sha256']=plan['source_manifest_sha256']=target['identity']['source_manifest_sha256']
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'registration.json'
            raw=json.dumps(plan).encode();(root/'source-plan.json').write_bytes(raw)
            registration=dict(schema='kimi-source-watchdog-registration-v2',target=target,
                plan_sha256=hashlib.sha256(raw).hexdigest(),batch_entered_utc=plan['window_start'],
                host_pid_namespace=42,nonce='a'*32,worker_input_sha256='9'*64)
            raw=json.dumps(registration).encode();path.write_bytes(raw)
            args=['--registration',str(path.resolve()),'--registration-sha',hashlib.sha256(raw).hexdigest()]
            import source_bundle
            with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(source_bundle,'verify_source_run',return_value=receipt) as verify, \
                    patch.object(source_bundle,'verify_source_worker_input',return_value=(root,
                        dict(plan_sha256=registration['plan_sha256'],batch_entered_utc=registration['batch_entered_utc']),
                        {},receipt,source_schedule(plan,receipt,now=datetime.fromisoformat(plan['window_start'])),
                        target['identity'])), \
                    patch.object(schedule_module,'watchdog_service_plan',return_value={'deployment_performed':False}) as render, \
                    patch.object(schedule_module,'source_watchdog',side_effect=AssertionError('unexpected launch')), \
                    patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO) as output:
                schedule_module.watchdog_main(args,service_plan_only=True)
                self.assertEqual(json.loads(output.getvalue()),{'deployment_performed':False})
                render.assert_called_once()
                render.reset_mock();verify.side_effect=ValueError('unreviewed source')
                with self.assertRaisesRegex(ValueError,'unreviewed source'):
                    schedule_module.watchdog_main(args,service_plan_only=True)
                render.assert_not_called()
            self.assertFalse((root/'source-watchdog-events.jsonl').exists())

    def probe_pair(self,kind='ok'):
        target,info=self.guard_fixture();client,server=socket.socketpair()
        host=dict(uid=2147,boot_id=target['boot_id'],pid=999,start_ticks=123,pid_namespace=42,cgroup='cpu-only')
        fire=datetime.now(timezone.utc)+timedelta(seconds=10);result={}
        class Connection:
            def settimeout(self,value):server.settimeout(value)
            def recv(self,count):return server.recv(count)
            def close(self):server.close()
            def sendall(self,raw):
                if kind=='replay':
                    row=json.loads(raw);row['challenge']='0'*32;raw=(json.dumps(row)+'\n').encode()
                server.sendall(raw)
        class Listener:
            def settimeout(self,value):pass
            def accept(self):return Connection(),None
        def serve():
            result['served']=schedule_module._serve_guard_probe(Listener(),target,host,'1'*32,fire,1,
                lambda:kind!='expired',peer_probe=lambda conn:(1000,2147))
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        def pid_probe(pid):
            if kind=='late_response':time.sleep(0.1)
            return dict(host,start_ticks=124) if kind=='reuse' else dict(host)
        try:
            kwargs=dict(peer_probe=lambda conn:(999,0 if kind=='foreign' else 2147),pid_probe=pid_probe)
            deadline=time.monotonic()+(0.05 if kind=='late_response' else 1)
            proof=schedule_module._exchange_guard_probe(client,target,'2'*32 if kind=='nonce' else '1'*32,
                                                        host,fire,deadline,**kwargs)
            return proof,result
        finally:
            client.close();thread.join(timeout=2);server.close()
            self.assertFalse(thread.is_alive())

    def test_active_probe_real_socket_exchange_preserves_nonadmission_scope(self):
        proof,result=self.probe_pair()
        self.assertTrue(result['served'])
        self.assertTrue(proof['probe_exchange_verified'])
        self.assertFalse(proof['independent_service_verified'])
        self.assertFalse(proof['guard_deployed'])
        self.assertFalse(proof['runtime_admitted'])

    def test_active_probe_refuses_replay_wrong_uid_nonce_reused_pid_and_expired_guard(self):
        for kind in ('replay','foreign','nonce','reuse','expired','late_response'):
            with self.subTest(kind=kind),self.assertRaises((ValueError,TimeoutError,OSError)):
                self.probe_pair(kind)

    def test_probe_slow_partial_message_does_not_reset_absolute_deadline(self):
        connection=Mock();connection.recv.side_effect=[b'{',b'"',b'x']
        with patch.object(schedule_module.time,'monotonic',side_effect=[0,0.4,0.8,1.2]):
            with self.assertRaises(TimeoutError):schedule_module._probe_receive(connection,1,4096)
        self.assertEqual(connection.recv.call_count,3)
        self.assertEqual([row.args[0] for row in connection.settimeout.call_args_list],[1,0.6,0.19999999999999996])

    def test_probe_oversize_is_rejected_before_retaining_more_than_cap(self):
        connection=Mock();connection.recv.side_effect=[b'x'*1024]*4+[b'x']
        with self.assertRaisesRegex(ValueError,'bound'):
            schedule_module._probe_receive(connection,time.monotonic()+1,4096)
        self.assertEqual(connection.recv.call_args.args[0],1)

    def test_unresponsive_probe_clients_do_not_extend_watchdog_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp)
            listener=Mock()
            def serve(*args,**kwargs):state['elapsed']+=1;return False
            with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']), \
                    patch.object(schedule_module,'_serve_guard_probe',side_effect=serve):
                schedule_module.source_watchdog(target,schedule,events=events,probe_factory=lambda nonce:listener,**options)
            self.assertEqual(state['elapsed'],150)
            listener.close.assert_called_once()
            self.assertEqual(sum(row[0]=='stop' for row in calls),1)

    def test_probe_listener_failure_triggers_only_owned_cleanup_and_no_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp)
            with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']):
                with self.assertRaisesRegex(OSError,'bind failed'):
                    schedule_module.source_watchdog(target,schedule,events=events,
                        probe_factory=Mock(side_effect=OSError('CPU bind failed')),**options)
            self.assertFalse(state['present'])
            self.assertNotIn('"event": "ready"',path.read_text())

    def test_probe_listener_is_closed_before_error_path_owned_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp)
            order=[];original=options['docker'];listener=Mock()
            listener.close.side_effect=lambda:order.append('probe_close')
            def docker(args,timeout):order.append(args[0]);return original(args,timeout)
            options['docker']=docker
            with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']), \
                    patch.object(schedule_module,'_serve_guard_probe',side_effect=RuntimeError('CPU probe loop failed')):
                with self.assertRaisesRegex(RuntimeError,'probe loop failed'):
                    schedule_module.source_watchdog(target,schedule,events=events,
                        probe_factory=lambda nonce:listener,**options)
            self.assertLess(order.index('probe_close'),order.index('stop'))
            listener.close.assert_called_once()
            self.assertFalse(state['present'])

    def test_ready_file_is_bounded_and_not_a_substitute_for_active_probe(self):
        for kind in ('ok','stale','partial','cleanup','no_endpoint','hash'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp)
                nonce='1'*32;host=options['host_probe']()
                with path.open('xb',buffering=0) as stream:
                    schedule_module._watchdog_event(stream,target['identity'],nonce,'inputs_verified',dict(
                        registration_sha256='2'*64,plan_sha256='3'*64,runtime_admitted=False))
                    schedule_module._watchdog_event(stream,target['identity'],nonce,'ready',dict(host=host,
                        container_id=target['container_id'],image_id=target['image_id'],
                        container_created=target['container_created'],guard_fire_by_utc=schedule['guard_fire_by_utc'],
                        probe_endpoint=None if kind=='no_endpoint' else '@sllm-kimi-'+nonce,runtime_admitted=False))
                    if kind=='cleanup':schedule_module._watchdog_event(stream,target['identity'],nonce,'cleanup_started',{})
                if kind=='partial':path.write_bytes(path.read_bytes()[:-1])
                kwargs=dict(nonce='9'*32 if kind=='stale' else nonce,registration_sha256='9'*64 if kind=='hash' else '2'*64,
                    plan_sha256='3'*64,guard_fire_by_utc=schedule['guard_fire_by_utc'])
                if kind=='ok':
                    row=schedule_module.read_watchdog_ready(path,target,**kwargs)
                    self.assertNotIn('probe_exchange_verified',row)
                else:
                    with self.assertRaises(ValueError):schedule_module.read_watchdog_ready(path,target,**kwargs)

    def watchdog_control(self,temp,kind='ok'):
        target,info=self.guard_fixture()
        plan,receipt=self.fixture()
        receipt.update({key:target['identity'][key] for key in ('run_id','holder','code_commit','spec_sha256')},
                       manifest_sha256=target['identity']['source_manifest_sha256'])
        plan.update({key:receipt[key] for key in ('run_id','holder','code_commit','spec_sha256')},
                    source_manifest_sha256=receipt['manifest_sha256'])
        base=datetime.fromisoformat(plan['window_start'])
        schedule=source_schedule(plan,receipt,now=base)
        state=dict(elapsed=0.0,wall=0.0,running=True,present=True)
        calls=[]
        def docker(args,timeout):
            calls.append(args)
            if kind=='daemon':raise RuntimeError('CPU daemon fixture unavailable')
            if args[0]=='inspect':
                row=copy.deepcopy(info);row['State']=dict(Running=state['running'],Pid=54321 if state['running'] else 0)
                if kind in ('late','late_tail') and len(calls)==1:state['elapsed']=181 if kind=='late' else 151
                if kind=='foreign':row['Config']['Labels']['sllm.kimi.holder']='0'*32
                text=json.dumps([row])
            elif args[0]=='ps':text=target['container_id'] if state['present'] else ''
            elif args[0]=='stop':
                if kind=='stop_error':raise RuntimeError('CPU fixture stop failed')
                state['running']=False;text=''
            else:state['present']=False;text=''
            return types.SimpleNamespace(returncode=0,stdout=text,stderr='')
        def sleep(seconds):
            state['elapsed']+=seconds
            if kind=='backward':state['wall']=-10
            elif kind=='forward':state['wall']=1000
            elif kind=='wait_error':raise RuntimeError('CPU fixture wait failed')
        host=dict(uid=2147,boot_id=target['boot_id'],pid=999,start_ticks=123,
                  pid_namespace=42,cgroup='0::/user.slice/cpu-control\n')
        if kind=='namespace':host['pid_namespace']=43
        clock=Mock(wraps=datetime)
        clock.now.side_effect=lambda *a:base+timedelta(seconds=state['elapsed']+state['wall'])
        path=Path(temp)/'watchdog-events.jsonl'
        options=dict(nonce='1'*32,host_pid_namespace=42,docker=docker,
            process_probe=lambda row:dict(pid=row['State']['Pid'],start_ticks=111),host_probe=lambda:host)
        return target,schedule,state,calls,sleep,clock,path,options

    def test_watchdog_uses_frozen_deadline_and_records_cleanup_without_release_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp)
            with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                    patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']), \
                    patch.object(schedule_module.time,'sleep',side_effect=sleep):
                result=schedule_module.source_watchdog(target,schedule,events=events,**options)
            self.assertEqual(state['elapsed'],150)
            self.assertEqual([v[0] for v in calls],['inspect','ps','inspect','stop','inspect','rm','ps'])
            rows=[json.loads(line) for line in path.read_bytes().splitlines()]
            self.assertEqual([row['event'] for row in rows],['ready','cleanup_started','cleanup_returned'])
            self.assertEqual(rows[0]['nonce'],'1'*32)
            self.assertEqual(rows[0]['payload']['container_id'],target['container_id'])
            self.assertEqual(rows[0]['payload']['image_id'],target['image_id'])
            self.assertTrue(result['container_absence_verified'])
            self.assertFalse(result['runtime_admitted'])
            self.assertFalse(result['resources_released'])

    def test_watchdog_rejects_wrong_namespace_and_initial_daemon_failure_without_mutation(self):
        for kind in ('namespace','daemon','foreign'):
            with tempfile.TemporaryDirectory() as temp:
                target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp,kind)
                with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']):
                    with self.assertRaises((ValueError,RuntimeError)):
                        schedule_module.source_watchdog(target,schedule,events=events,**options)
                self.assertNotIn('stop',[v[0] for v in calls])
                self.assertNotIn('"event": "ready"',path.read_text())

    def test_watchdog_clock_failure_and_late_execution_cleanup_are_failures(self):
        for kind in ('backward','late','late_tail','forward'):
            with tempfile.TemporaryDirectory() as temp:
                target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp,kind)
                with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']), \
                        patch.object(schedule_module.time,'sleep',side_effect=sleep):
                    with self.assertRaises(TimeoutError) as caught:
                        schedule_module.source_watchdog(target,schedule,events=events,**options)
                self.assertFalse(state['present'])
                self.assertFalse(caught.exception.guard_cleanup_receipt['resources_released'])
                rows=[json.loads(line) for line in path.read_bytes().splitlines()]
                self.assertEqual(rows[-1]['event'],'failed')
                if kind=='late':
                    self.assertNotIn('ready',[row['event'] for row in rows])
                    self.assertTrue(caught.exception.guard_cleanup_receipt['deadline_missed'])
                if kind=='late_tail':
                    self.assertNotIn('ready',[row['event'] for row in rows])
                    self.assertTrue(caught.exception.guard_cleanup_receipt['late_start'])
                    self.assertFalse(caught.exception.guard_cleanup_receipt['deadline_missed'])
                if kind=='forward':
                    self.assertTrue(caught.exception.guard_cleanup_receipt['deadline_missed'])
                    self.assertEqual(caught.exception.guard_cleanup_receipt['emergency_cleanup_budget_seconds'],5)

    def test_watchdog_ready_or_wait_failure_still_cleans_only_the_verified_target_once(self):
        for kind in ('ready_error','wait_error','cleanup_log_error','stop_error'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                target,schedule,state,calls,sleep,clock,path,options=self.watchdog_control(temp,kind)
                original=schedule_module._watchdog_event
                def event(stream,identity,nonce,name,payload):
                    if (kind=='ready_error' and name=='ready') or (kind=='cleanup_log_error' and name=='cleanup_started'):
                        raise OSError('CPU fixture journal failed')
                    return original(stream,identity,nonce,name,payload)
                with path.open('xb',buffering=0) as events,patch.object(schedule_module,'datetime',clock), \
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:state['elapsed']), \
                        patch.object(schedule_module.time,'sleep',side_effect=sleep), \
                        patch.object(schedule_module,'_watchdog_event',side_effect=event):
                    with self.assertRaises((OSError,RuntimeError)):
                        schedule_module.source_watchdog(target,schedule,events=events,**options)
                self.assertEqual(sum(row[0]=='stop' for row in calls),1)
                self.assertEqual(state['present'],kind=='stop_error')
                rows=[json.loads(line) for line in path.read_bytes().splitlines()]
                self.assertEqual(rows[-1]['event'],'failed')

    def test_watchdog_guard_cli_refuses_unbundled_source_before_docker_or_host_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'registration.json';path.write_text('{}')
            with patch.object(schedule_module,'_watchdog_host') as host, \
                    patch.object(schedule_module,'_guard_docker') as docker:
                with self.assertRaisesRegex(ValueError,'run source bundle'):
                    schedule_module.watchdog_main(['--registration',str(path.resolve()),'--registration-sha','0'*64])
            host.assert_not_called();docker.assert_not_called()

    def test_cpu_watchdog_child_reaches_deadline_after_its_launcher_exits(self):
        # All Docker/namespace/process inputs below are CPU stand-ins, never host admission.
        with tempfile.TemporaryDirectory() as temp:
            target,info=self.guard_fixture();path=Path(temp)/'events.jsonl';errors=Path(temp)/'child.err'
            script='''import json,os,sys,threading
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import source_schedule as s
threading.Timer(8,lambda:os._exit(124)).start()
target,info,path=json.loads(sys.argv[1]),json.loads(sys.argv[2]),Path(sys.argv[3])
state=dict(running=True,present=True)
def docker(args,timeout):
    if args[0]=='inspect':
        info['State']=dict(Running=state['running'],Pid=54321 if state['running'] else 0)
        text=json.dumps([info])
    elif args[0]=='ps':text=target['container_id'] if state['present'] else ''
    elif args[0]=='stop':state['running']=False;text=''
    else:state['present']=False;text=''
    return SimpleNamespace(returncode=0,stdout=text,stderr='')
now=datetime.now(timezone.utc)
schedule=dict(guard_fire_by_utc=(now+timedelta(seconds=2)).isoformat(),compute_max_seconds=2,
    phase_deadlines_utc=dict(worker_shutdown=(now+timedelta(seconds=5)).isoformat()))
host=dict(uid=2147,boot_id=target['boot_id'],pid=os.getpid(),start_ticks=1,pid_namespace=42,cgroup='cpu-only')
try:
    with path.open('xb',buffering=0) as events:
        s.source_watchdog(target,schedule,nonce='1'*32,host_pid_namespace=42,events=events,
            docker=docker,process_probe=lambda row:dict(pid=row['State']['Pid']),host_probe=lambda:host)
except BaseException:
    import traceback
    traceback.print_exc()
finally:
    os.close(2)
    (path.parent/'child.closed').write_text('CPU child files closed')
os._exit(0)
'''
            command=[sys.executable,'-B','-X','utf8','-c',script,json.dumps(target),json.dumps(info),str(path)]
            launcher=('import json,subprocess,sys; '
                'from datetime import datetime,timezone; '
                'err=open(sys.argv[2],"xb"); '
                'p=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=err); '
                'print(json.dumps(dict(pid=p.pid,utc=datetime.now(timezone.utc).isoformat())),flush=True)')
            env=dict(schedule_module.os.environ,PYTHONPATH=str(Path(schedule_module.__file__).parent),
                     PYTHONDONTWRITEBYTECODE='1')
            child_wait=None
            try:
                parent=subprocess.run([sys.executable,'-B','-X','utf8','-c',launcher,json.dumps(command),str(errors)],
                    env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(parent.returncode,0,parent.stderr)
                if os.name=='nt':
                    import ctypes
                    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
                    kernel.OpenProcess.restype=ctypes.c_void_p
                    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
                    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                    handle=kernel.OpenProcess(0x00100000,False,json.loads(parent.stdout)['pid'])
                    if handle:child_wait=(kernel,handle)
                    else:self.assertEqual(ctypes.get_last_error(),87)  # Already exited, invalid PID.
                stopped_parent=datetime.now(timezone.utc)
                rows=[];deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if path.exists():
                        data=path.read_bytes()
                        if data.endswith(b'\n'):
                            rows=[json.loads(line) for line in data.splitlines()]
                            if rows[-1]['event'] in ('cleanup_returned','failed'):break
                    time.sleep(0.05)
                self.assertTrue(rows,errors.read_text())
                self.assertEqual(rows[-1]['event'],'cleanup_returned',errors.read_text()+'\n'+json.dumps(rows))
                self.assertGreater(datetime.fromisoformat(rows[-1]['utc']),stopped_parent)
                self.assertFalse(rows[-1]['payload']['resources_released'])
            finally:
                # A marker can precede process exit and Windows inherited-handle release.
                if child_wait is not None:
                    kernel,handle=child_wait
                    try:self.assertEqual(kernel.WaitForSingleObject(handle,10000),0)
                    finally:kernel.CloseHandle(handle)
                deadline=time.monotonic()+10
                while errors.exists() and not (Path(temp)/'child.closed').exists() and time.monotonic()<deadline:
                    time.sleep(0.05)

    def p0_fixture(self):
        plan, receipt = self.fixture()
        target, info = self.guard_fixture()
        run_id = 'kimi-p0-20261031T000000Z-01234567'
        for key in ('holder','code_commit','spec_sha256'):
            plan[key] = receipt[key] = target['identity'][key]
        receipt['manifest_sha256'] = plan['source_manifest_sha256'] = target['identity']['source_manifest_sha256']
        plan['run_id'] = receipt['run_id'] = target['identity']['run_id'] = run_id
        plan['schema'] = 'kimi-p0-window-v1'
        plan['seconds'] = {name:30 for name in schedule_module.P0_PHASES}
        plan['seconds']['release_observe'] = 90
        receipt['component_max_seconds'] = plan['seconds']['component'] = 60
        receipt['gpu_uuid'] = target['gpu_uuid'] = 'GPU-12345678-1234-1234-1234-123456789abc'
        receipt['image_id'] = target['image_id']
        target['schema'] = 'kimi-p0-guard-target-v2'
        target['run_root'] = '/data/owned/run'
        target['worker_input_sha256'] = 'f'*64
        image_env=['PATH=/usr/local/bin:/usr/bin:/bin','LD_LIBRARY_PATH=/usr/local/nvidia/lib64']
        target['environment_sha256']=schedule_module.p0_image_environment_sha256(image_env,gpu_uuid=target['gpu_uuid'])
        receipt['environment_sha256']=target['environment_sha256']
        info['Config']['Labels']['sllm.kimi.run_id'] = run_id
        info['Config']['Labels']['sllm.purpose'] = schedule_module.P0_PURPOSE
        info['HostConfig'].update(DeviceRequests=[dict(Driver='nvidia',Count=0,
            DeviceIDs=[target['gpu_uuid']],Capabilities=[['gpu']],Options={})],Memory=4*1024**3,
            NanoCpus=2*10**9,PidsLimit=128,ShmSize=64*1024**2,NetworkMode='none',
            Privileged=False,IpcMode='private',PidMode='',Devices=[],CapAdd=[],Runtime='nvidia',
            MemorySwap=4*1024**3,CapDrop=['ALL'],SecurityOpt=['no-new-privileges=true'],
            Tmpfs={'/tmp':'rw,nosuid,nodev,size=16m'})
        command=schedule_module.p0_container_command(target['identity'],run_root=target['run_root'],
            image_id=target['image_id'],gpu_uuid=target['gpu_uuid'],worker_input_sha256=target['worker_input_sha256'],environment_sha256=target['environment_sha256'])
        info['Config']['Labels']['sllm.kimi.environment_sha256']=target['environment_sha256']
        info['Config'].update(Entrypoint=['python3'],Cmd=command[command.index(target['image_id'])+1:],
            WorkingDir=target['run_root'],Env=image_env+[command[i+1] for i,arg in enumerate(command) if arg=='--env'])
        info['Mounts']=[dict(Type='bind',Source=source,Destination=source,RW=writable)
            for source,writable in schedule_module._p0_mounts(target['run_root'])]
        return plan, receipt, target, info

    def test_p0_create_command_is_frozen_before_container_id_and_guard_checks_entry_mounts(self):
        _,_,target,info=self.p0_fixture()
        options=dict(run_root=target['run_root'],image_id=target['image_id'],gpu_uuid=target['gpu_uuid'],
                     worker_input_sha256=target['worker_input_sha256'],environment_sha256=target['environment_sha256'])
        command=schedule_module.p0_container_command(target['identity'],**options)
        self.assertEqual(command[0],'create')
        self.assertIn('--pull=never',command)
        self.assertNotIn(target['container_id'],command)
        self.assertNotIn('--registration-sha',command)
        self.assertIn('-E',command);self.assertIn('-s',command)
        base=['PATH=/usr/bin','LD_LIBRARY_PATH=/usr/local/nvidia/lib64']
        digest=schedule_module.p0_image_environment_sha256(base,gpu_uuid=target['gpu_uuid'])
        self.assertEqual(digest,schedule_module.p0_image_environment_sha256(base[::-1],gpu_uuid=target['gpu_uuid']))
        self.assertNotEqual(digest,schedule_module.p0_image_environment_sha256(base+['LD_PRELOAD=/unreviewed.so'],gpu_uuid=target['gpu_uuid']))
        with self.assertRaises(ValueError):schedule_module.p0_image_environment_sha256(base+['PATH=/extra'],gpu_uuid=target['gpu_uuid'])
        info['Config']['Env'].reverse()
        schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147)
        self.assertEqual(command[-4:],['--worker-input','/data/owned/run/p0-worker-input.json','--worker-input-sha','f'*64])
        for kind in ('entry','cmd','workdir','extra_mount','code_rw','input_rw','env','env_duplicate','env_extra','env_frozen_wrong','runtime','swap','caps','security','old_schema'):
            with self.subTest(kind=kind):
                candidate=copy.deepcopy(info);registration=copy.deepcopy(target)
                if kind=='entry':candidate['Config']['Entrypoint']=['bash']
                elif kind=='cmd':candidate['Config']['Cmd']=['-c','unapproved']
                elif kind=='workdir':candidate['Config']['WorkingDir']='/other'
                elif kind=='extra_mount':candidate['Mounts'].append(dict(Type='bind',Source='/etc',Destination='/etc',RW=True))
                elif kind=='code_rw':candidate['Mounts'][1]['RW']=True
                elif kind=='input_rw':candidate['Mounts'][4]['RW']=True
                elif kind=='env':candidate['Config']['Env'][0]='NVIDIA_VISIBLE_DEVICES=all'
                elif kind=='env_duplicate':candidate['Config']['Env'].append(candidate['Config']['Env'][0])
                elif kind=='env_extra':candidate['Config']['Env'].append('LD_PRELOAD=/unreviewed.so')
                elif kind=='env_frozen_wrong':
                    candidate['Config']['Env']=[row if not row.startswith('CUDA_VISIBLE_DEVICES=') else 'CUDA_VISIBLE_DEVICES=all'
                        for row in candidate['Config']['Env']]
                    registration['environment_sha256']=schedule_module._environment_digest(candidate['Config']['Env'],{})
                    candidate['Config']['Labels']['sllm.kimi.environment_sha256']=registration['environment_sha256']
                elif kind=='runtime':candidate['HostConfig']['Runtime']='runc'
                elif kind=='swap':candidate['HostConfig']['MemorySwap']=-1
                elif kind=='caps':candidate['HostConfig']['CapDrop']=[]
                elif kind=='security':candidate['HostConfig']['SecurityOpt']=[]
                else:registration['schema']='kimi-p0-guard-target-v1'
                with self.assertRaises(ValueError):
                    schedule_module.guard_target(registration,candidate,boot_id=target['boot_id'],uid=2147)
        for key,value in (('run_root','/data/run,src=/etc'),('run_root','/data/../root'),
                ('worker_input_sha256','mutable'),('image_id','vllm:latest')):
            candidate=dict(options);candidate[key]=value
            with self.assertRaises(ValueError):schedule_module.p0_container_command(target['identity'],**candidate)

    def test_p0_nonroot_cache_directory_is_explicit_and_bounded(self):
        _,_,target,_=self.p0_fixture()
        command=schedule_module.p0_container_command(target['identity'],run_root=target['run_root'],
            image_id=target['image_id'],gpu_uuid=target['gpu_uuid'],
            worker_input_sha256=target['worker_input_sha256'],environment_sha256=target['environment_sha256'])
        environment=dict(command[i+1].split('=',1) for i,arg in enumerate(command) if arg=='--env')
        for key,value in dict(HOME='/tmp',XDG_CACHE_HOME='/tmp',
                TORCHINDUCTOR_CACHE_DIR='/tmp/torchinductor',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1').items():
            self.assertEqual(environment.get(key),value)
        self.assertEqual(command[command.index('--tmpfs')+1],'/tmp:rw,nosuid,nodev,size=16m')

    def test_p0_monitor_records_running_and_exited_without_claiming_component_or_release(self):
        plan,receipt,target,info=self.p0_fixture()
        entered=datetime.fromisoformat(plan['window_start'])+timedelta(seconds=1)
        schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
        info['State'].update(Running=True,Pid=123,Status='running',OOMKilled=False,Error='',ExitCode=0)
        exited=copy.deepcopy(info)
        exited['State'].update(Running=False,Pid=0,Status='exited')
        guard=dict(independent_service_verified=True,identity=target['identity'],nonce='a'*32,
            container_id=target['container_id'],image_id=target['image_id'],
            container_created=target['container_created'],guard_fire_by_utc=schedule['guard_fire_by_utc'],
            service_invocation_id='b'*32,runtime_admitted=False)
        rows=iter([info,exited]);calls=[]
        resource=Mock(return_value=dict(p0_budget_verified=True,whole_node_exclusive=False))
        def docker(args,timeout):
            calls.append(args)
            if args[0]=='inspect':value=[next(rows)]
            elif args[0]=='stats':value=dict(ID=target['container_id'],CPUPerc='1.0%',MemUsage='1MiB / 4GiB')
            else:self.fail('monitor must not mutate Docker resources')
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(value).encode(),stderr=b'')
        with tempfile.TemporaryFile() as events,patch.object(schedule_module,'datetime',wraps=datetime) as clock,\
                patch.object(schedule_module.time,'sleep'):
            clock.now.return_value=entered
            result=schedule_module.monitor_p0_target(target,schedule,entered=entered,started=time.monotonic(),
                nonce='a'*32,service_invocation_id='b'*32,events=events,guard_probe=lambda timeout:guard,
                docker=docker,process_probe=lambda item:dict(pid=123,start_ticks=42,uid=2147,cgroup='owned'),
                resource_probe=resource)
            events.seek(0);records=[json.loads(line) for line in events]
        self.assertEqual([row['event'] for row in records],['component_sample','component_sample','component_exited'])
        self.assertEqual(result['sample_count'],2)
        self.assertTrue(result['resource_monitoring_verified']);self.assertEqual(resource.call_count,2)
        self.assertTrue(result['container_exited'])
        self.assertFalse(result['torch_cuda_ipc_verified']);self.assertFalse(result['resources_released'])
        self.assertEqual(result['process']['start_ticks'],42)
        self.assertEqual([row[0] for row in calls],['inspect','stats','inspect'])

    def test_p0_output_consumer_binds_bits_identity_events_and_explicit_limits(self):
        plan,receipt,target,_=self.p0_fixture()
        identity=target['identity']
        receipt.update(run_root='',worker_input_sha256='f'*64,max_payload_bytes=40)
        payload=dict(packed=bytes.fromhex('ffffffff000000806745230100000000'),
            scale=bytes.fromhex('0080c17f0000803f'),router=bytes.fromhex('000000800100c07f0000803f00000000'),empty=b'')
        before={name:hashlib.sha256(raw).hexdigest() for name,raw in payload.items()}
        after=dict(before,packed=hashlib.sha256(bytes.fromhex('df9b5713000000806745230100000000')).hexdigest())
        component=dict(run_id=receipt['run_id'],holder=receipt['holder'],gpu_uuid=receipt['gpu_uuid'],
            stage='returned',mode=schedule_module.P0_PURPOSE,payload_bytes=40,max_payload_bytes=40,
            allocator='torch',producer_pid=10,consumer_pid=11,child_start_attempted=True,child_exitcode=0,
            cleanup_errors=[],torch_cuda_ipc_verified=True,native_loader_verified=False,store_created=False,
            full_model_verified=False,resources_released=False,runtime_admitted=False,
            before=before,after=after,elapsed_seconds=1.25)
        inputs=dict(plan_sha256='9'*64,batch_entered_utc=plan['window_start'])
        schedule=schedule_module.p0_schedule(plan,receipt,now=datetime.fromisoformat(plan['window_start']))
        first=dict(identity,schema='kimi-p0-component-events-v1',stage='inputs_verified',runtime_admitted=False,
            observed_utc=plan['window_start'],details=dict(worker_input_sha256='f'*64,
            plan_sha256=inputs['plan_sha256'],gpu_uuid=receipt['gpu_uuid'],image_id=receipt['image_id'],
            original_guard_fire_by_utc=schedule['guard_fire_by_utc'],guard_verified=False))
        second=dict(first,stage='component_returned',details=dict(component=component,resources_released=False))
        output=dict(component,code_commit=receipt['code_commit'],spec_sha256=receipt['spec_sha256'],
            source_manifest_sha256=receipt['manifest_sha256'],guard_verified=False)
        with tempfile.TemporaryDirectory() as temp:
            receipt['run_root']=temp;path=Path(temp)/'p0-component-events.jsonl'
            def verify(document,rows):
                path.write_bytes(b''.join((json.dumps(row)+'\n').encode() for row in rows))
                return schedule_module.verify_p0_component_output(receipt,inputs,schedule,
                    (json.dumps(document)+'\n').encode())
            result=verify(output,[first,second])
            self.assertTrue(result['worker_output_verified'])
            self.assertFalse(result['runtime_admitted']);self.assertFalse(result['resources_released'])
            for kind in ('identity','bits','exit','pid','cleanup','limit','nonfinite','missing_event','event_identity','event_time','event_details','event_flag'):
                with self.subTest(kind=kind):
                    document=copy.deepcopy(output);rows=copy.deepcopy([first,second])
                    if kind=='identity':document['code_commit']='0'*40
                    elif kind=='bits':document['after']['scale']='0'*64
                    elif kind=='exit':document['child_exitcode']=137
                    elif kind=='pid':document['consumer_pid']=True
                    elif kind=='cleanup':document['cleanup_errors']=['unknown']
                    elif kind=='limit':document['max_payload_bytes']=41
                    elif kind=='nonfinite':document['elapsed_seconds']=float('nan')
                    elif kind=='missing_event':rows.pop()
                    elif kind=='event_identity':rows[1]['holder']='0'*32
                    elif kind=='event_time':rows[1]['observed_utc']='2027-01-01T00:00:00Z'
                    elif kind=='event_details':rows[1]['details']['component']['producer_pid']=99
                    elif kind=='event_flag':rows[1]['details']['resources_released']=0
                    with self.assertRaises(ValueError):verify(document,rows)
            verify(output,[first,second])
            encoded=(json.dumps(output)+'\n').encode()
            for raw in (encoded[:-1],b'x'*65537,encoded.replace(b'"producer_pid": 10',b'"producer_pid": 10, "producer_pid": 10')):
                with self.assertRaises(ValueError):
                    schedule_module.verify_p0_component_output(receipt,inputs,schedule,raw)
            for raw in (path.read_bytes()[:-1],b'x'*32769):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    schedule_module.verify_p0_component_output(receipt,inputs,schedule,encoded)

    def test_p0_monitor_rejects_missing_guard_before_inspect_and_retains_cleanup_obligation(self):
        plan,receipt,target,_=self.p0_fixture()
        entered=datetime.fromisoformat(plan['window_start'])+timedelta(seconds=1)
        schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
        docker=Mock()
        with tempfile.TemporaryFile() as events,patch.object(schedule_module,'datetime',wraps=datetime) as clock:
            clock.now.return_value=entered
            with self.assertRaisesRegex(ValueError,'guard proof') as caught:
                schedule_module.monitor_p0_target(target,schedule,entered=entered,started=time.monotonic(),
                    nonce='a'*32,service_invocation_id='b'*32,events=events,guard_probe=lambda timeout:{},docker=docker)
        docker.assert_not_called()
        self.assertTrue(caught.exception.p0_monitor_receipt['cleanup_owed'])

    def test_p0_release_observation_requires_full_60_seconds_and_retains_guard_cost(self):
        plan,receipt,target,_=self.p0_fixture()
        entered=datetime.fromisoformat(plan['window_start'])
        schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
        ticks=[0.]
        snapshot=dict(container_absent=True,original_processes_absent=True,cgroup_absent=True,
            owned_mounts_absent=True,owned_gpu_processes_absent=True)
        cleanup=dict(container_id=target['container_id'],container_absence_verified=True)
        monitor=dict(container_id=target['container_id'],process_inventory_complete=True,resource_monitoring_verified=True)
        samples=[]
        def sample(*args,**kwargs):
            if not samples:ticks[0]+=14
            samples.append(ticks[0]);return snapshot
        with tempfile.TemporaryFile() as events,patch.object(schedule_module,'datetime',wraps=datetime) as clock,\
                patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]),\
                patch.object(schedule_module.time,'sleep',side_effect=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds)):
            clock.now.side_effect=lambda zone:entered+timedelta(seconds=ticks[0])
            result=schedule_module.observe_p0_release(target,schedule,cleanup,monitor,
                host_pid_namespace=42,entered=entered,started=0,events=events,sample=sample)
        self.assertGreaterEqual(samples[-1]-samples[0],60)
        self.assertTrue(result['container_release_observation_verified'])
        self.assertFalse(result['resources_released']);self.assertTrue(result['guard_release_owed'])
        self.assertGreaterEqual(len(samples),2)

    def test_p0_guard_exit_binds_original_process_unit_and_terminal_journal(self):
        for kind in ('normal','restart','failed_log','pid_reused','collected','badexit','timeout'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                plan,receipt,target,_=self.p0_fixture();target['run_root']=temp
                entered=datetime.fromisoformat(plan['window_start'])
                schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
                service=schedule_module.watchdog_service_plan(target,str(Path(temp)/'registration.json'),'1'*64,schedule,now=entered)
                unit=service['unit'];cg=Path('/sys/fs/cgroup/system.slice')/unit
                host=dict(uid=2147,boot_id=target['boot_id'],pid=999,start_ticks=42,pid_namespace=5,
                    cgroup='0::/system.slice/'+unit+'\n')
                proof=dict(independent_service_verified=True,identity=target['identity'],nonce='a'*32,
                    container_id=target['container_id'],image_id=target['image_id'],container_created=target['container_created'],
                    service_unit=unit,service_invocation_id='b'*32,guard_fire_by_utc=schedule['guard_fire_by_utc'],host=host,runtime_admitted=False)
                terminal=datetime.fromisoformat(schedule['guard_fire_by_utc'])+timedelta(seconds=1)
                payload=dict(container_id=target['container_id'],container_absence_verified=True,deadline_missed=False,late_start=False,runtime_admitted=False)
                rows=[]
                for event,data in [('inputs_verified',dict(registration_sha256='1'*64,plan_sha256='2'*64,runtime_admitted=False)),
                        ('ready',dict(host=host)),('cleanup_started',dict(reason='deadline')),
                        ('failed' if kind=='failed_log' else 'cleanup_returned',payload)]:
                    rows.append(dict(target['identity'],schema='kimi-source-watchdog-events-v1',nonce='a'*32,
                        utc=terminal.isoformat(),event=event,payload=data))
                (Path(temp)/'source-watchdog-events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
                properties=dict(Id=unit,LoadState='loaded',ActiveState='inactive',MainPID='0',ControlGroup='',
                    InvocationID=('c' if kind=='restart' else 'b')*32,Result='success',ExecMainStatus='0',ExecMainCode='1')
                if kind=='collected':properties.update(LoadState='not-found',InvocationID='')
                if kind=='badexit':properties.update(Result='exit-code',ExecMainStatus='1')
                def stat(path,*args,**kwargs):
                    if path==cg:raise FileNotFoundError()
                    return original_stat(path,*args,**kwargs)
                original_stat=Path.stat
                query=Mock(return_value=types.SimpleNamespace(returncode=0,
                    stdout=''.join(key+'='+value+'\n' for key,value in properties.items()).encode(),stderr=b''))
                pid=Mock(side_effect=FileNotFoundError())
                if kind=='pid_reused':pid=Mock(return_value=dict(host,start_ticks=43,cgroup='0::/foreign\n'))
                with tempfile.TemporaryFile() as events,patch.object(Path,'stat',stat),\
                        patch.object(schedule_module,'_watchdog_host',return_value=dict(host,pid=100,cgroup='0::/user.slice\n')),\
                        patch.object(schedule_module,'load_watchdog_inputs',return_value=(None,Path(temp),
                            dict(target=target,nonce='a'*32,host_pid_namespace=5,plan_sha256='2'*64),None,schedule,None)),\
                        patch.object(schedule_module,'datetime',wraps=datetime) as clock:
                    clock.now.return_value=terminal+timedelta(seconds=1)
                    options=dict(nonce='a'*32,entered=entered,started=time.monotonic()-(terminal-entered).total_seconds(),
                        events=events,query=query,pid_probe=pid)
                    if kind=='timeout':options['started']-=1000
                    if kind in ('restart','failed_log','badexit','timeout'):
                        with self.assertRaises((ValueError,RuntimeError,TimeoutError)):
                            schedule_module.await_p0_guard_exit(service,target,schedule,proof,**options)
                    else:
                        result=schedule_module.await_p0_guard_exit(service,target,schedule,proof,**options)
                        self.assertTrue(result['guard_resources_released'])
                        self.assertFalse(result['resources_released'])
                        self.assertEqual(result['unit_exit_status'],None if kind=='collected' else 0)
                if kind=='timeout':query.assert_not_called()
                else:query.assert_called_once()

    def test_p0_process_inventory_reads_owned_leaf_and_refuses_missing_coverage(self):
        _,_,target,info=self.p0_fixture()
        info['State'].update(Running=True,Pid=123)
        main=dict(pid=123,start_ticks=1,uid=2147,cgroup='0::/system.slice/docker-'+target['container_id']+'.scope\n')
        self.assertEqual(str(schedule_module._p0_cgroup_directory(main,target['container_id'])),
            '/sys/fs/cgroup/system.slice/docker-'+target['container_id']+'.scope')
        with tempfile.TemporaryDirectory() as temp:
            group=Path(temp);path=group/'cgroup.procs';path.write_text('123\n124\n')
            def process(item):return dict(main,pid=item['State']['Pid'],start_ticks=item['State']['Pid'])
            with patch.object(schedule_module,'_p0_cgroup_directory',return_value=group),\
                    patch.object(schedule_module,'guard_process',side_effect=process):
                result=schedule_module.p0_process_snapshot(info)
                self.assertEqual([row['pid'] for row in result['members']],[123,124])
                for text in ('124\n','123\n123\n','0\n','123\n'*2000):
                    path.write_text(text)
                    with self.assertRaises(ValueError):schedule_module.p0_process_snapshot(info)
                path.write_text('123\n');(group/'nested').mkdir()
                with self.assertRaisesRegex(ValueError,'nested'):schedule_module.p0_process_snapshot(info)
        for name in ('0::/foreign\n','1:memory:/docker/'+target['container_id']+'\n',
                '0::/system.slice/../docker-'+target['container_id']+'.scope\n'):
            with self.assertRaises(ValueError):schedule_module._p0_cgroup_directory(dict(main,cgroup=name),target['container_id'])

    def test_p0_release_sampler_keeps_foreign_resources_and_unknown_ownership_separate(self):
        _,_,target,_=self.p0_fixture()
        main=dict(pid=123,start_ticks=1,uid=2147,cgroup='0::/system.slice/docker-'+target['container_id']+'.scope\n')
        monitor=dict(container_id=target['container_id'],process_inventory_complete=True,process=main,
            owned_processes=[{key:main[key] for key in ('pid','start_ticks','uid')}])
        host=dict(uid=2147,boot_id=target['boot_id'],pid_namespace=42)
        gpu_ids=[target['gpu_uuid']]+['GPU-%08x-1234-1234-1234-123456789abc'%i for i in range(1,8)]
        for kind in ('foreign','unknown','owned','reuse','live','cgroup','mount'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                group=Path(temp)/'owned'
                if kind=='cgroup':group.mkdir()
                def metadata(path):
                    raw=(target['container_id'] if kind=='mount' else 'foreign mount') if str(path).endswith('mountinfo') else 'Mlocked: 999 kB\nShmem: 888 kB\n'
                    return io.BytesIO(raw.encode())
                def capture(command,timeout,environment):
                    if command[1].startswith('--query-gpu='):raw=''.join(uuid+', 500\n' for uuid in gpu_ids)
                    else:raw=(gpu_ids[1] if kind=='foreign' else gpu_ids[0])+', '+('123' if kind in ('owned','reuse') else '99')+', 500\n'
                    return types.SimpleNamespace(returncode=0,stdout=raw,stderr='')
                def pid_probe(pid):
                    if kind=='live' and pid==123:return dict(main)
                    if kind=='reuse' and pid==123:return dict(main,start_ticks=999,cgroup='0::/foreign\n')
                    raise FileNotFoundError()
                with patch.object(schedule_module,'_watchdog_host',return_value=host),\
                        patch.object(schedule_module.socket,'gethostname',return_value='qjhs3'),\
                        patch.object(schedule_module,'_p0_cgroup_directory',return_value=group),\
                        patch.object(schedule_module,'_watchdog_pid',side_effect=pid_probe),\
                        patch.object(schedule_module,'_guard_docker',return_value=types.SimpleNamespace(returncode=0,stdout=b'',stderr=b'')),\
                        patch.object(schedule_module,'_guard_capture',side_effect=capture),\
                        patch('vllm_export_hook._metadata_stream',side_effect=metadata):
                    result=schedule_module.sample_p0_release(target,monitor,host_pid_namespace=42,timeout_seconds=10)
                if kind=='foreign':
                    self.assertTrue(result['owned_gpu_processes_absent'])
                    self.assertEqual(result['global_memory_context_kib']['Mlocked'],999)
                    self.assertEqual(result['gpu_context'][0]['memory_used_mib'],500)
                elif kind in ('unknown','owned','reuse'):
                    self.assertFalse(result['owned_gpu_processes_absent'])
                    self.assertEqual(result['owned_gpu_processes'],[])
                    self.assertTrue(result['unattributable_gpu_processes'])
                elif kind=='live':self.assertFalse(result['original_processes_absent'])
                elif kind=='cgroup':self.assertFalse(result['cgroup_absent'])
                elif kind=='mount':self.assertFalse(result['owned_mounts_absent'])
                self.assertFalse(result['resources_released'])

    def test_p0_release_observation_refuses_residue_clock_and_unproven_delete(self):
        for kind in ('residue','clock','delete','missing','short_budget','slow_initial_budget','wall_stall'):
            with self.subTest(kind=kind),tempfile.TemporaryFile() as events:
                plan,receipt,target,_=self.p0_fixture()
                entered=datetime.fromisoformat(plan['window_start'])
                schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
                cleanup=dict(container_id=target['container_id'],container_absence_verified=kind!='delete')
                monitor=dict(container_id=target['container_id'],process_inventory_complete=True,resource_monitoring_verified=True)
                snapshot=dict(container_absent=True,original_processes_absent=True,cgroup_absent=True,
                    owned_mounts_absent=True,owned_gpu_processes_absent=True)
                if kind=='residue':snapshot['owned_gpu_processes_absent']=False
                if kind=='missing':snapshot.pop('owned_mounts_absent')
                ticks=[0.]
                with patch.object(schedule_module,'datetime',wraps=datetime) as clock,\
                        patch.object(schedule_module.time,'monotonic',side_effect=lambda:ticks[0]),\
                        patch.object(schedule_module.time,'sleep',side_effect=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds)):
                    clock.now.side_effect=lambda zone:entered-timedelta(seconds=1) if kind=='clock' else entered+timedelta(seconds=0 if kind=='wall_stall' else ticks[0])
                    started=0
                    if kind=='short_budget':
                        started=59-(datetime.fromisoformat(schedule['phase_deadlines_utc']['release_observe'])-entered).total_seconds()
                    if kind=='slow_initial_budget':
                        started=73-(datetime.fromisoformat(schedule['phase_deadlines_utc']['release_observe'])-entered).total_seconds()
                    def sample(*args,**kwargs):
                        if kind=='slow_initial_budget' and ticks[0]==0:ticks[0]+=14
                        return snapshot
                    with self.assertRaises((ValueError,TimeoutError,RuntimeError)):
                        schedule_module.observe_p0_release(target,schedule,cleanup,monitor,
                            host_pid_namespace=42,entered=entered,started=started,events=events,sample=sample)

    def test_p0_monitor_rejects_oom_failed_exit_and_unstarted_target(self):
        plan,receipt,target,info=self.p0_fixture()
        entered=datetime.fromisoformat(plan['window_start'])+timedelta(seconds=1)
        schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
        guard=dict(independent_service_verified=True,identity=target['identity'],nonce='a'*32,
            container_id=target['container_id'],image_id=target['image_id'],container_created=target['container_created'],
            guard_fire_by_utc=schedule['guard_fire_by_utc'],service_invocation_id='b'*32,runtime_admitted=False)
        for change in (dict(OOMKilled=True),dict(ExitCode=137),dict(Status='created')):
            with self.subTest(change=change),tempfile.TemporaryFile() as events,\
                    patch.object(schedule_module,'datetime',wraps=datetime) as clock:
                clock.now.return_value=entered
                item=copy.deepcopy(info)
                item['State'].update(Running=False,Pid=0,Status='exited',OOMKilled=False,Error='',ExitCode=0)
                item['State'].update(change)
                with self.assertRaises(RuntimeError) as caught:
                    schedule_module.monitor_p0_target(target,schedule,entered=entered,started=time.monotonic(),
                        nonce='a'*32,service_invocation_id='b'*32,events=events,guard_probe=lambda timeout:guard,
                        docker=lambda args,timeout:types.SimpleNamespace(returncode=0,stdout=json.dumps([item]).encode(),stderr=b''))
                self.assertFalse(caught.exception.p0_monitor_receipt['resources_released'])

    def test_p0_monitor_rejects_deadline_process_reuse_foreign_stats_and_guard_change(self):
        for kind in ('deadline','reuse','stats','guard','resource-missing','resource-error'):
            with self.subTest(kind=kind),tempfile.TemporaryFile() as events,\
                    patch.object(schedule_module,'datetime',wraps=datetime) as clock,\
                    patch.object(schedule_module.time,'sleep'):
                plan,receipt,target,info=self.p0_fixture()
                entered=datetime.fromisoformat(plan['window_start'])+timedelta(seconds=1)
                clock.now.return_value=entered
                schedule=schedule_module.p0_schedule(plan,receipt,now=entered)
                info['State'].update(Running=True,Pid=123,Status='running',OOMKilled=False,Error='',ExitCode=0)
                proof=dict(independent_service_verified=True,identity=target['identity'],nonce='a'*32,
                    container_id=target['container_id'],image_id=target['image_id'],container_created=target['container_created'],
                    guard_fire_by_utc=schedule['guard_fire_by_utc'],service_invocation_id='b'*32,runtime_admitted=False)
                if kind=='guard':proof['service_invocation_id']='c'*32
                def docker(args,timeout):
                    value=[info] if args[0]=='inspect' else dict(ID='0'*64 if kind=='stats' else target['container_id'])
                    return types.SimpleNamespace(returncode=0,stdout=json.dumps(value).encode(),stderr=b'')
                process=Mock(side_effect=[dict(pid=123,start_ticks=1,uid=2147,cgroup='owned'),
                    dict(pid=123,start_ticks=2,uid=2147,cgroup='owned')])
                resource=Mock(side_effect=ValueError('resource snapshot failed')) if kind=='resource-error' else Mock(return_value={})
                with self.assertRaises((ValueError,TimeoutError)) as caught:
                    schedule_module.monitor_p0_target(target,schedule,entered=entered,
                        started=time.monotonic()-(1000 if kind=='deadline' else 0),nonce='a'*32,
                        service_invocation_id='b'*32,events=events,guard_probe=lambda timeout:proof,
                        docker=docker,process_probe=process,
                        resource_probe=resource if kind.startswith('resource-') else None)
                self.assertTrue(caught.exception.p0_monitor_receipt['cleanup_owed'])

    def test_p0_guard_rejects_missing_or_oversized_private_cache_mount(self):
        _,_,target,info=self.p0_fixture()
        for tmpfs in ({},{'/tmp':'rw,nosuid,nodev,size=1g'}):
            with self.subTest(tmpfs=tmpfs):
                candidate=copy.deepcopy(info)
                candidate['HostConfig']['Tmpfs']=tmpfs
                with self.assertRaises(ValueError):
                    schedule_module.guard_target(target,candidate,boot_id=target['boot_id'],uid=2147)

    def test_p0_guard_rejects_self_consistent_wrong_cache_environment(self):
        _,_,target,info=self.p0_fixture()
        values=dict(row.split('=',1) for row in info['Config']['Env'])
        values['TORCHINDUCTOR_CACHE_DIR']='/root/unregistered-cache'
        info['Config']['Env']=[key+'='+value for key,value in values.items()]
        target['environment_sha256']=schedule_module._environment_digest(info['Config']['Env'],{})
        info['Config']['Labels']['sllm.kimi.environment_sha256']=target['environment_sha256']
        with self.assertRaises(ValueError):
            schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147)

    def test_p0_budget_is_original_complete_batch_and_cannot_use_source_schema(self):
        plan, receipt, _, _ = self.p0_fixture()
        total = sum(plan['seconds'].values())
        entered = datetime.fromisoformat(plan['cleanup_complete_by'])-timedelta(seconds=total)
        result = schedule_module.p0_schedule(plan,receipt,now=entered)
        self.assertEqual(result['total_max_seconds'],total)
        self.assertEqual(set(result['phase_deadlines_utc']),set(schedule_module.P0_PHASES))
        self.assertFalse(result['runtime_admitted'])
        component = datetime.fromisoformat(result['guard_fire_by_utc'])
        self.assertEqual(component,datetime.fromisoformat(result['phase_deadlines_utc']['component']))
        with self.assertRaises(TimeoutError):
            schedule_module.phase_remaining_seconds(result,'component',entered=entered,
                elapsed=(component-entered).total_seconds(),now=component)
        with self.assertRaises(TimeoutError): schedule_module.p0_schedule(plan,receipt,now=entered+timedelta(seconds=1))
        with self.assertRaises(ValueError): schedule_module.source_schedule(plan,receipt,now=entered)
        for key, value in (('component',61), ('safety_margin',1800), ('release_observe',59)):
            altered=copy.deepcopy(plan);altered['seconds'][key]=value
            with self.assertRaises((ValueError,TimeoutError)):schedule_module.p0_schedule(altered,receipt,now=entered)

    def test_p0_target_requires_exact_one_gpu_caps_and_purpose(self):
        _,_,target,info = self.p0_fixture()
        self.assertEqual(schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147),target['identity'])
        for key,value in (('Memory',0),('Memory',True),('NanoCpus',0),('PidsLimit',0),
                ('ShmSize',1024**3),('NetworkMode','host'),('IpcMode','host'),('PidMode','host'),
                ('Privileged',True),('Devices',[{'PathOnHost':'/dev/nvidia0'}]),('CapAdd',['SYS_ADMIN']),
                ('DeviceRequests',[]),('DeviceRequests',[dict(Driver='nvidia',Count=-1,
                    DeviceIDs=[],Capabilities=[['gpu']],Options={})])):
            with self.subTest(key=key):
                candidate=copy.deepcopy(info);candidate['HostConfig'][key]=value
                with self.assertRaises(ValueError):schedule_module.guard_target(target,candidate,boot_id=target['boot_id'],uid=2147)
        info['Config']['Labels']['sllm.purpose']=schedule_module.GUARD_PURPOSE
        with self.assertRaises(ValueError):schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147)
        target['schema']='kimi-source-guard-target-v1'
        with self.assertRaises(ValueError):schedule_module.guard_target(target,info,boot_id=target['boot_id'],uid=2147)

    def test_p0_service_cli_binds_separate_spec_plan_and_manifest_before_render(self):
        plan,receipt,target,_ = self.p0_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'registration.json'
            target['run_root']=str(root)
            raw=json.dumps(plan).encode();(root/'p0-plan.json').write_bytes(raw)
            registration=dict(schema='kimi-p0-watchdog-registration-v1',target=target,
                plan_sha256=hashlib.sha256(raw).hexdigest(),batch_entered_utc=plan['window_start'],
                host_pid_namespace=42,nonce='a'*32)
            def write_registration():
                raw=json.dumps(registration).encode();path.write_bytes(raw)
                return ['--registration',str(path.resolve()),'--registration-sha',hashlib.sha256(raw).hexdigest()]
            import source_bundle
            with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(source_bundle,'verify_p0_run',return_value=receipt) as verify, \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=(root/'p0-worker-input.json',root,
                        dict(plan_sha256=registration['plan_sha256'],batch_entered_utc=registration['batch_entered_utc']),
                        receipt,schedule_module.p0_schedule(plan,receipt,now=datetime.fromisoformat(plan['window_start'])),target['identity'])), \
                    patch.object(source_bundle,'verify_source_run',side_effect=AssertionError('wrong scope')), \
                    patch.object(schedule_module,'watchdog_service_plan',return_value={'deployment_performed':False}) as render, \
                    patch.object(schedule_module,'source_watchdog',side_effect=AssertionError('unexpected launch')), \
                    patch.object(schedule_module.sys,'stdout',new_callable=io.StringIO) as output:
                schedule_module.watchdog_main(write_registration(),service_plan_only=True)
                self.assertEqual(json.loads(output.getvalue()),{'deployment_performed':False})
                self.assertEqual(verify.call_args.args[3],root/'p0-spec.json')
                render.assert_called_once();render.reset_mock()
                target['gpu_uuid']='GPU-00000000-1234-1234-1234-123456789abc'
                with self.assertRaisesRegex(ValueError,'GPU differs'):
                    schedule_module.watchdog_main(write_registration(),service_plan_only=True)
                render.assert_not_called()
                target['gpu_uuid']=receipt['gpu_uuid']
                target['environment_sha256']='0'*64
                with self.assertRaisesRegex(ValueError,'frozen worker input'):
                    schedule_module.watchdog_main(write_registration(),service_plan_only=True)
                render.assert_not_called()
                target['schema']='kimi-source-guard-target-v1'
                with self.assertRaisesRegex(ValueError,'scopes differ'):
                    schedule_module.watchdog_main(write_registration(),service_plan_only=True)
            self.assertFalse((root/'source-watchdog-events.jsonl').exists())

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
