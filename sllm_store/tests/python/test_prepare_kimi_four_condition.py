"""CPU-only formal preparation checks; never imports ML or activates services."""
import copy
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS))
import prepare_kimi_four_condition as p


class PreparationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        cls.spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA)
        cls.command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)

    def test_all_40_candidates_and_sequence(self):
        ids=[]
        for attempt in self.protocol['attempts']:
            spec,command=p.render(self.protocol,self.spec,self.command,attempt['attempt_id'],self.spec['native_source_pins'])
            ids.append(spec['run_id']);body=command['argv'][command['argv'].index('-c')+1]
            compile(body,'<cpu-generated>','exec')
            self.assertEqual(spec['status'],'CANDIDATE_NOT_FROZEN_NOT_EXECUTABLE')
            self.assertEqual(len(spec['role_bodies']),9)
            self.assertEqual(spec['config']['auto_scaling_config']['min_instances'],0)
            self.assertEqual(spec['config']['auto_scaling_config']['keep_alive'],3600)
            self.assertFalse(spec['config']['backend_config']['enable_prefix_caching'])
            self.assertEqual(spec['budgets']['sum'],6480)
            self.assertIn("for life in (1,):",body)
            self.assertLess(body.index("  construct_cache();"),body.index("event('parent_register_arithmetic_dispatch')"))
            self.assertNotIn("while True:\n   assert time.monotonic()<create_end",body)
            self.assertIn("store_stop_end=time.monotonic()+60;",body)
            self.assertIn("result['quiet_seconds']>=60",body)
            self.assertIn("assert result['admitted'] is True",body)
            self.assertIn("clock_identity=get_clock_identity()",body)
            self.assertNotIn('DIAGNOSTIC_NOT_L1',body)
            for request in spec['correctness']['requests']:
                self.assertNotIn('return_token_ids',request)
                self.assertNotIn('stream',request)
            for role in spec['requests'].values():
                self.assertTrue(role['env']['HF_HUB_OFFLINE']=='1')
        self.assertEqual(len(set(ids)),40)

    def test_duplicate_and_bad_request_rejected(self):
        protocol=copy.deepcopy(self.protocol);protocol['attempts'][1]['attempt_id']=protocol['attempts'][0]['attempt_id']
        with self.assertRaises(AssertionError):p.validate_protocol(protocol)
        protocol=copy.deepcopy(self.protocol);protocol['correctness']['requests'][0]['seed']=0
        with self.assertRaises(AssertionError):p.validate_protocol(protocol)

    def test_duplicate_store_registration_preserves_model(self):
        source=(p.base.ROOT/'sllm_store/csrc/sllm_store/checkpoint_store.cpp').read_text()
        branch=source.split('int64_t CheckpointStore::RegisterModelInfo(',1)[1].split('auto model = std::make_shared<Model>',1)[0]
        self.assertIn('model_map_.find(model_path) != model_map_.end()',branch)
        self.assertIn('auto model = model_map_.at(model_path);',branch)
        self.assertIn('return model->GetModelSize();',branch)
        self.assertNotIn('FreeHost',branch)
        self.assertNotIn('ClearMem',branch)

    def test_full_block_claims_and_budget(self):
        for number in range(1,11):
            entries=[dict(run_id=a['attempt_id'],command_path='/evidence/'+a['attempt_id']+'.json',command_sha256='a'*64) for a in self.protocol['attempts'] if a['block']==number]
            bundle=p.render_block(self.protocol,number,entries,p.SOURCE)
            self.assertEqual(bundle['full_max_seconds'],4*7200+120)
            self.assertIn('os.O_EXCL',bundle['block_python'])
            self.assertIn("claim(a['run_id']+'.claim')",bundle['block_python'])
            self.assertIn("'--wait'",bundle['block_python'])
            self.assertIn('RemainAfterExit=no',bundle['block_python'])
            self.assertIn("assert result.returncode==0",bundle['block_python'])

    def test_bad_image_pin_rejected(self):
        spec=copy.deepcopy(self.spec);spec['image']='sha256:'+'0'*64
        with self.assertRaises(AssertionError):p.render(self.protocol,spec,self.command,self.protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])

    def test_missing_and_identity_changed_asset_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=pathlib.Path(temporary)/'tiny';path.write_bytes(b'tiny')
            st=path.stat();row=dict(path=str(path),resolved=str(path),dev=st.st_dev,ino=st.st_ino,bytes=st.st_size,mtime_ns=st.st_mtime_ns,uid=st.st_uid)
            group=dict(root=temporary,files=[row])
            inventory=dict(roots=dict(safetensors=group,store=group))
            p.validate_inventory(inventory)
            row['ino']+=1
            with self.assertRaises(AssertionError):p.validate_inventory(inventory)
            path.unlink()
            with self.assertRaises(FileNotFoundError):p.validate_inventory(inventory)



import ast
import os
import types
from unittest import mock
import urllib.request

def functions_from(source):
    nodes=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef)]
    return compile(ast.Module(body=nodes,type_ignores=[]),'<real-consumer-functions>','exec')

class ActualConsumerTest(unittest.TestCase):
    def test_real_http_producer_and_parent_delays_preserve_endpoint(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);want=protocol['correctness']['requests'][0]
        clock={'boot_id':'boot','time_namespace':'time:[1]','monotonic_clock_implementation':'CLOCK_MONOTONIC','process_startticks':88}
        counter=[1000];calls=[];saved={}
        def now():
            counter[0]+=100
            return counter[0]
        response_value={'choices':[{'message':{'content':want['expected']['content']},'finish_reason':want['expected']['finish_reason']}],'usage':{'prompt_tokens':23,'completion_tokens':2,'total_tokens':25}}
        class Response:
            status=200
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def read(self,limit):return json.dumps(self.value).encode()
            def __exit__(self,*args):counter[0]+=900
        def urlopen(request,timeout):
            calls.append(request.full_url)
            return Response({} if request.full_url.endswith('/register') else response_value)
        namespace={'json':json,'urllib':types.SimpleNamespace(request=types.SimpleNamespace(Request=urllib.request.Request,urlopen=urlopen)),'time':types.SimpleNamespace(monotonic_ns=now),'HTTP_CLOCK':clock,'HTTP_PID':321}
        exec(functions_from(p.HTTP_BODY),namespace)
        row=namespace['finish_http']('/v1/chat/completions',{'request_id':'run-0'},30,want,{'model':'kimi'})
        self.assertEqual(calls,['http://127.0.0.1:8343/register','http://127.0.0.1:8343/v1/chat/completions'])
        self.assertTrue(row['child_gold_validation_pass'])
        self.assertGreater(row['received_ns'],row['json_parse_complete_ns'])
        primary=row['received_ns']-row['registration_command_ns'];business=row['received_ns']-row['business_trigger_ns'];endpoint=row['received_ns']
        # Simulate Docker finish polling, parent validation delay and slow evidence fsync.
        counter[0]+=10**10
        def save(name,value):
            saved[name]=copy.deepcopy(value)
            counter[0]+=10**10
        namespace.update(save=save,clock_identity=clock,run='run',s={'primary_http_endpoint':'received_ns','attempt':{'attempt_id':'run','block':1},'condition':'safetensors_disk_hot','source_commit':'a'*40,'protocol_sha256':'b'*64},cids={'head':'cid'})
        exec(p.HTTP_EVENTS+p.RESPONSE,namespace)
        complete=namespace['check_response'](row,want,0,1)
        self.assertEqual(complete['monotonic_ns'],endpoint)
        self.assertEqual(complete['primary_A_ns'],primary)
        self.assertEqual(complete['business_B_ns'],business)
        self.assertGreater(complete['parent_validation_end_ns'],endpoint)
        self.assertEqual(saved['event-command_register_begin.json']['monotonic_ns'],row['registration_command_ns'])
        bad=copy.deepcopy(row);bad['clock_identity']['time_namespace']='time:[other]'
        with self.assertRaises(AssertionError):namespace['check_response'](bad,want,0,1)

    def block_fixture(self,root,fail_at=None,expiry=30000):
        claims=root/'claims';claims.mkdir(mode=0o700);runs=root/'runs';runs.mkdir()
        entries=[];source='a'*40;protocol='b'*64
        for number in range(4):
            run='tiny-'+str(number)
            spec={'status':'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW','run_id':run,'source_commit':source,'protocol_sha256':protocol}
            sp=root/(run+'-spec.json');sp.write_text(json.dumps(spec));sha=p.hashlib.sha256(sp.read_bytes()).hexdigest()
            argv=['/usr/bin/systemd-run','--unit='+run,'--property=RemainAfterExit=yes','/usr/bin/python3','-I','-c','pass',str(sp),sha,'FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED']
            cp=root/(run+'-command.json');cp.write_text(json.dumps({'argv':argv,'spec_sha256':sha}))
            entries.append({'run_id':run,'command_path':str(cp),'command_sha256':p.hashlib.sha256(cp.read_bytes()).hexdigest()})
        bundle={'status':'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW','full_max_seconds':28920,'claims_root':str(claims),'runs_root':str(runs),'attempts':entries,'block_id':'tinyblock','source_commit':source,'protocol_sha256':protocol,'planned_attempts':entries}
        bp=root/'bundle.json';bp.write_text(json.dumps(bundle));called=[]
        real_stat=pathlib.Path.stat
        def fake_stat(path,*args,**kwargs):
            value=real_stat(path,*args,**kwargs)
            if path==claims:
                fields=list(value);fields[4]=0
                return os.stat_result(fields)
            return value
        def run(argv,timeout):
            index=len(called);called.append(argv)
            self.assertEqual(argv[1],'--wait');self.assertIn('--property=RemainAfterExit=no',argv)
            dest=runs/entries[index]['run_id'];dest.mkdir()
            failed=index==fail_at
            (dest/('controller-failed.json' if failed else 'controller-final.json')).write_text(json.dumps({'status':'FAILED_RETAINED_NO_RETRY' if failed else 'FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS'}))
            return types.SimpleNamespace(returncode=1 if failed else 0)
        def execute():
            with mock.patch('os.geteuid',return_value=0),mock.patch.object(pathlib.Path,'stat',fake_stat),mock.patch('time.time',return_value=100),mock.patch('subprocess.run',side_effect=run),mock.patch.object(sys,'argv',['block',str(bp),p.hashlib.sha256(bp.read_bytes()).hexdigest(),str(expiry)]):
                exec(compile(p.BLOCK_BODY,'<actual-block-body>','exec'),{})
        return execute,called,claims

    def test_actual_block_success_and_exclusive_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            execute,called,claims=self.block_fixture(pathlib.Path(directory));execute()
            self.assertEqual(len(called),4)
            final=json.loads((claims/'tinyblock-final.json').read_text())
            self.assertEqual(final['status'],'FOUR_CONDITION_BLOCK_RELEASE60_PASS')
            self.assertTrue(all(a['attempted'] and a['result_sha256'] for a in final['attempts']))
            with self.assertRaises(FileExistsError):execute()
            self.assertEqual(len(called),4)

    def test_actual_block_failure_stops_and_not_started_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            execute,called,claims=self.block_fixture(pathlib.Path(directory),fail_at=1)
            with self.assertRaises(AssertionError):execute()
            self.assertEqual(len(called),2)
            final=json.loads((claims/'tinyblock-final.json').read_text())
            self.assertEqual([a['status'] for a in final['attempts']][2:],['NOT_STARTED','NOT_STARTED'])
            self.assertFalse(final['attempts'][2]['attempted'])

    def test_actual_block_insufficient_window_launches_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            execute,called,claims=self.block_fixture(pathlib.Path(directory),expiry=28000)
            with self.assertRaises(AssertionError):execute()
            self.assertEqual(called,[])
            self.assertEqual(list(claims.iterdir()),[])

    def test_final_protocol_binding(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(protocol,p.SOURCE)
        protocol.update(status='FROZEN_PREREGISTRATION',final_deploy_commit=p.SOURCE)
        protocol['resource_budget'].update(per_complete_block_max_seconds=28920,parent_overhead_seconds=120)
        protocol['events'].update(primary_http_endpoint='received_ns',primary_start_event='same_http_client_registration_command_ns')
        p.validate_frozen_protocol(protocol,p.SOURCE)
        protocol['final_deploy_commit']='0'*40
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(protocol,p.SOURCE)

    def test_actual_structured_event_consumer_joins_and_rejects_missing(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        clock={'boot_id':'boot','time_namespace':'time:[1]','monotonic_clock_implementation':'CLOCK_MONOTONIC','process_startticks':88}
        model='kimi-k2.6-sf-reference';saved={}
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);(root/'head/ray').mkdir(parents=True);(root/'worker/ray').mkdir(parents=True)
            def row(event,pid,ns,**kwargs):
                return dict(event=event,model=model,pid=pid,monotonic_ns=ns,clock_identity={**clock,'process_startticks':77 if pid==10 else 88},**kwargs)
            route=row('router_instance_published',10,40,instance_id='instance')
            backend=[row('backend_init_begin',20,20),row('backend_init_ready',20,30)]
            for i,request in enumerate(protocol['correctness']['requests']):
                backend.append(row('backend_first_token_observed',20,50+i,request_id='run-'+str(i)))
                (root/('event-'+request['case']+'_http_begin.json')).write_text(json.dumps({'monotonic_ns':10}))
                (root/('event-'+request['case']+'_http_complete_correct.json')).write_text(json.dumps({'response_body_complete_ns':100}))
            hp=root/'head/ray/worker-router.err';wp=root/'worker/ray/worker-backend.err'
            hp.write_text('log '+json.dumps(route)+'\n');wp.write_text(''.join('log '+json.dumps(r)+'\n' for r in backend))
            namespace={'json':json,'pathlib':pathlib,'hashlib':p.hashlib,'base':root,'clock_identity':clock,'run':'run','s':{'config':{'model':model},'attempt':{'block':1},'source_commit':'a'*40,'protocol_sha256':'b'*64,'correctness':protocol['correctness']},'head_log_paths':lambda:[hp],'owned_host_pid':lambda pid,role:{'pid':pid,'startticks':77},'save':lambda name,value:saved.update({name:value})}
            exec(p.HTTP_EVENTS+p.STRUCTURED_EVENTS,namespace)
            namespace['collect_structured_events']({}, {}, {'actor':{'name':'instance'},'metadata':{'Pid':20}}, {'pid':20,'startticks':88})
            self.assertEqual(saved['structured-lifecycle-events.json']['instance_id'],'instance')
            wp.write_text(''.join(json.dumps(r)+'\n' for r in backend[:-1]))
            with self.assertRaises(AssertionError):
                namespace['collect_structured_events']({}, {}, {'actor':{'name':'instance'},'metadata':{'Pid':20}}, {'pid':20,'startticks':88})

    def test_committed_source_gate_uses_real_checks_with_mock_git(self):
        commit='a'*40;mode=['good'];calls=[]
        def git(argv,cwd):
            calls.append(argv[1])
            if argv[1]=='rev-parse':return (commit+'\n').encode()
            if argv[1]=='status':return b' M dirty.py\n' if mode[0]=='dirty' else b''
            if argv[1]=='symbolic-ref':return b'testbranch\n'
            if argv[1]=='ls-remote':return ((('b'*40) if mode[0]=='remote' else commit)+'\trefs/heads/testbranch\n').encode()
            if argv[1]=='show':
                relative=argv[2].split(':',1)[1]
                value=(p.base.ROOT/relative).read_bytes()
                return value+b'drift' if mode[0]=='blob' else value
            raise AssertionError(argv)
        with mock.patch.object(p.subprocess,'check_output',side_effect=git):
            self.assertEqual(p.committed_source(commit,{'sllm/logger.py'}),commit)
            for failure in ('dirty','remote','blob'):
                mode[0]=failure
                with self.assertRaises(AssertionError):p.committed_source(commit,{'sllm/logger.py'})
        self.assertIn('ls-remote',calls);self.assertIn('show',calls)

    def test_main_freezes_all_four_children_and_actual_bundle(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        protocol.update(status='FROZEN_PREREGISTRATION',final_deploy_commit=p.SOURCE)
        protocol['resource_budget'].update(per_complete_block_max_seconds=28920,parent_overhead_seconds=120)
        protocol['events'].update(primary_http_endpoint='received_ns',primary_start_event='same_http_client_registration_command_ns')
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ep=root/'prepared';ep.mkdir();pp=root/'protocol.json';pp.write_text(json.dumps(protocol));sha=p.hashlib.sha256(pp.read_bytes()).hexdigest()
            with mock.patch.object(p.base,'EVIDENCE',ep),mock.patch.object(p,'committed_source',return_value=p.SOURCE),mock.patch.object(sys,'argv',['prepare','--block','1','--source-commit',p.SOURCE,'--protocol',str(pp),'--protocol-sha',sha]),mock.patch('builtins.print'):
                p.main()
            files=list(ep.iterdir());self.assertEqual(len(files),10)
            bundle=json.loads(next(ep.glob('*-bundle.json')).read_text())
            self.assertEqual(bundle['status'],'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW')
            self.assertEqual(len(bundle['attempts']),4)
            for row in bundle['attempts']:
                command=json.loads(pathlib.Path(row['command_path']).read_text());spec=json.loads(pathlib.Path(command['argv'][-3]).read_text())
                self.assertEqual(spec['protocol_sha256'],sha);self.assertEqual(spec['source_commit'],p.SOURCE)
                self.assertEqual(spec['primary_http_endpoint'],'received_ns')
                self.assertEqual(spec['native_source_pins']['sllm_store/tests/file_cache_state.py'],spec['cache_helper_sha256'])
            launch=json.loads(next(ep.glob('*-root-command.json')).read_text())
            self.assertIn('--property=RuntimeMaxSec=28917',launch['argv'])

if __name__=='__main__':
    unittest.main()
