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

import source_schedule as schedule_module
import test_source_bundle

from source_schedule import PHASES, load_source_plan, source_schedule


class SourceScheduleTests(unittest.TestCase):
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
            registration=dict(schema='kimi-source-watchdog-registration-v1',target=target,
                plan_sha256=hashlib.sha256(raw).hexdigest(),batch_entered_utc=plan['window_start'],
                host_pid_namespace=42,nonce='a'*32)
            raw=json.dumps(registration).encode();path.write_bytes(raw)
            args=['--registration',str(path.resolve()),'--registration-sha',hashlib.sha256(raw).hexdigest()]
            import source_bundle
            with patch.object(schedule_module,'__file__',str(root/'code/source_schedule.py')), \
                    patch.object(source_bundle,'verify_source_run',return_value=receipt) as verify, \
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
            try:
                parent=subprocess.run([sys.executable,'-B','-X','utf8','-c',launcher,json.dumps(command),str(errors)],
                    env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(parent.returncode,0,parent.stderr)
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
                self.assertEqual(rows[-1]['event'],'cleanup_returned',errors.read_text())
                self.assertGreater(datetime.fromisoformat(rows[-1]['utc']),stopped_parent)
                self.assertFalse(rows[-1]['payload']['resources_released'])
            finally:
                # The bounded child closes stderr before this TemporaryDirectory is removed on Windows.
                deadline=time.monotonic()+10
                while errors.exists() and not (Path(temp)/'child.closed').exists() and time.monotonic()<deadline:
                    time.sleep(0.05)

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
