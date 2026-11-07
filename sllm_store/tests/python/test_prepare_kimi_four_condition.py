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
            self.assertLess(body.index("construct_cache();"),body.index("event('parent_register_arithmetic_dispatch')"))
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
        exec(p.HTTP_EVENTS+p.SAMPLE_EVIDENCE+p.RESPONSE,namespace)
        complete=namespace['check_response'](row,want,0,1)
        self.assertEqual(complete['monotonic_ns'],endpoint)
        self.assertEqual(complete['primary_A_ns'],primary)
        self.assertEqual(complete['business_B_ns'],business)
        self.assertGreater(complete['parent_validation_end_ns'],endpoint)
        self.assertEqual(saved['event-command_register_begin.json']['monotonic_ns'],row['registration_command_ns'])
        bad=copy.deepcopy(row);bad['clock_identity']['time_namespace']='time:[other]'
        with self.assertRaises(AssertionError):namespace['check_response'](bad,want,0,1)

    def block_fixture(self,root,fail_at=None,expiry=30000,no_receipt_at=None,timeout_at=None,inline_code='pass',run_ids=None,block_id='tinyblock'):
        claims=root/'claims';claims.mkdir(mode=0o700);runs=root/'runs';runs.mkdir()
        entries=[];source='a'*40;protocol='b'*64
        for number in range(4):
            run=run_ids[number] if run_ids is not None else 'tiny-'+str(number)
            spec={'status':'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW','run_id':run,'source_commit':source,'protocol_sha256':protocol}
            sp=root/(run+'-spec.json');sp.write_text(json.dumps(spec));sha=p.hashlib.sha256(sp.read_bytes()).hexdigest()
            argv=['/usr/bin/systemd-run','--unit='+run,'--property=RemainAfterExit=yes','/usr/bin/python3','-I','-c',inline_code,str(sp),sha,'FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED']
            cp=root/(run+'-command.json');cp.write_text(json.dumps({'argv':argv,'spec_sha256':sha}))
            entries.append({'run_id':run,'command_path':str(cp),'command_sha256':p.hashlib.sha256(cp.read_bytes()).hexdigest()})
        bundle={'status':'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW','full_max_seconds':28920,'claims_root':str(claims),'runs_root':str(runs),'attempts':entries,'block_id':block_id,'source_commit':source,'protocol_sha256':protocol,'planned_attempts':entries}
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
            dest=runs/entries[index]['run_id']
            if index==timeout_at:raise p.subprocess.TimeoutExpired(argv,timeout)
            if index==no_receipt_at:return types.SimpleNamespace(returncode=2)
            dest.mkdir()
            failed=index==fail_at
            (dest/('controller-failed.json' if failed else 'controller-final.json')).write_text(json.dumps({'status':'FAILED_RETAINED_NO_RETRY' if failed else 'FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS'}))
            return types.SimpleNamespace(returncode=1 if failed else 0)
        def execute():
            with mock.patch('os.geteuid',return_value=0),mock.patch.object(pathlib.Path,'stat',fake_stat),mock.patch('time.time',return_value=100),mock.patch('subprocess.run',side_effect=run),mock.patch.object(sys,'argv',['block',str(bp),p.hashlib.sha256(bp.read_bytes()).hexdigest(),str(expiry)]):
                exec(compile(p.BLOCK_BODY,'<actual-block-body>','exec'),{})
        return execute,called,claims

    def test_actual_block_only_changes_outer_property_preserving_inline_code(self):
        inline_code="def systemd():\n return ['--property=RemainAfterExit=yes']\n"
        with tempfile.TemporaryDirectory() as directory:
            execute,called,_=self.block_fixture(pathlib.Path(directory),inline_code=inline_code)
            execute()
            self.assertEqual(len(called),4)
            for argv in called:
                self.assertIn('--property=RemainAfterExit=no',argv)
                self.assertNotIn('--property=RemainAfterExit=yes',argv)
                actual_inline=argv[argv.index('-c')+1]
                self.assertEqual(actual_inline.encode(),inline_code.encode())
                self.assertIn('--property=RemainAfterExit=yes',actual_inline)

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
        protocol['statistics']['success_rate_denominator']='launch_requested'
        for request in protocol['correctness']['requests']:request['logprobs']=1
        protocol['correctness']['observability']='logprobs=1 matches GLM observation compute; return_token_ids omitted from SamplingParams, actual token IDs from existing trace; explicit top_p=1 vs GLM omitted default.'
        protocol['resource_budget'].update(per_complete_block_max_seconds=28920,parent_overhead_seconds=120)
        protocol['events'].update(primary_http_endpoint='received_ns',primary_start_event='same_http_client_registration_command_ns')
        protocol['assets'].update(route='local_nvme',safetensors=p.SF_HOST,store_archive=p.STORE_ARCHIVE,store=p.STORE_ARCHIVE+'/vllm/kimi-k2.6-sf-reference',input_inventory_path='/tmp/new-local-manifest.json',input_inventory_sha256='c'*64)
        protocol['resource_budget']['runs_root']=p.RUNS_ROOT
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
            if argv[1]=='remote':return b'https://github.com/BoyJiang2/serverlessllm.git\n'
            if argv[1]=='config':return b'AUTHORIZATION: basic CPU_TEST_ONLY\n'
            if argv[1]=='show':
                relative=argv[2].split(':',1)[1]
                value=(p.base.ROOT/relative).read_bytes()
                return value+b'drift' if mode[0]=='blob' else value
            raise AssertionError(argv)
        class Response:
            status=200
            def geturl(self):return 'https://api.github.com/repos/BoyJiang2/serverlessllm/git/ref/heads/testbranch'
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,limit):return json.dumps({'ref':'refs/heads/testbranch','object':{'type':'commit','sha':'b'*40 if mode[0]=='remote' else commit}}).encode()
        with mock.patch.object(p.subprocess,'check_output',side_effect=git),mock.patch.object(p.urllib.request,'urlopen',return_value=Response()):
            self.assertEqual(p.committed_source(commit,{'sllm/logger.py'}),commit)
            for failure in ('dirty','remote','blob'):
                mode[0]=failure
                with self.assertRaises(AssertionError):p.committed_source(commit,{'sllm/logger.py'})
        self.assertNotIn('ls-remote',calls);self.assertIn('config',calls);self.assertIn('show',calls)

    def test_main_freezes_all_four_children_and_actual_bundle(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        protocol.update(status='FROZEN_PREREGISTRATION',final_deploy_commit=p.SOURCE)
        protocol['statistics']['success_rate_denominator']='launch_requested'
        for request in protocol['correctness']['requests']:request['logprobs']=1
        protocol['correctness']['observability']='logprobs=1 matches GLM observation compute; return_token_ids omitted from SamplingParams, actual token IDs from existing trace; explicit top_p=1 vs GLM omitted default.'
        protocol['resource_budget'].update(per_complete_block_max_seconds=28920,parent_overhead_seconds=120)
        protocol['events'].update(primary_http_endpoint='received_ns',primary_start_event='same_http_client_registration_command_ns')
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ep=root/'prepared';ep.mkdir()
            sf=root/'checkpoint';archive=root/'archive';store=archive/'vllm/kimi-k2.6-sf-reference';runs=root/'performance-runs'
            for path in (sf,store,runs):path.mkdir(parents=True)
            inventory={'roots':{}}
            for name,target in (('safetensors',sf),('store',store)):
                path=target/'tiny-weight';path.write_bytes(b'tiny')
                st=path.stat();row=dict(path=str(path),resolved=str(path),relative=path.name,dev=st.st_dev,ino=st.st_ino,bytes=st.st_size,mtime_ns=st.st_mtime_ns,uid=st.st_uid,payload=True)
                inventory['roots'][name]={'root':str(target),'files':[row]}
            ip=root/'local-manifest.json';ip.write_text(json.dumps(inventory));isha=p.hashlib.sha256(ip.read_bytes()).hexdigest()
            protocol['assets'].update(route='local_nvme',safetensors=str(sf),store_archive=str(archive),store=str(store),input_inventory_path=str(ip),input_inventory_sha256=isha)
            protocol['resource_budget']['runs_root']=str(runs)
            revision_root=root/'revision';revision_root.mkdir()
            protocol['execution_revision']=ExecutionRevisionTest().revision_protocol(revision_root)['execution_revision']
            pp=root/'protocol.json';pp.write_text(json.dumps(protocol));sha=p.hashlib.sha256(pp.read_bytes()).hexdigest()
            with mock.patch.object(p,'SF_HOST',str(sf)),mock.patch.object(p,'STORE_ARCHIVE',str(archive)),mock.patch.object(p,'RUNS_ROOT',str(runs)),mock.patch.object(p.base,'EVIDENCE',ep),mock.patch.object(p,'committed_source',return_value=p.SOURCE),mock.patch.object(sys,'argv',['prepare','--block','1','--source-commit',p.SOURCE,'--protocol',str(pp),'--protocol-sha',sha]),mock.patch('builtins.print'):
                p.main()
            files=list(ep.iterdir());self.assertEqual(len(files),10)
            bundle=json.loads(next(ep.glob('*-bundle.json')).read_text())
            self.assertEqual(bundle['status'],'FROZEN_EXECUTION_PLAN_REQUIRES_INDEPENDENT_REVIEW')
            self.assertEqual(bundle['runs_root'],str(runs));self.assertEqual(len(bundle['attempts']),4)
            self.assertEqual(bundle['block_id'],'kimi-four-v1-b01-retry02')
            self.assertTrue(all(row['run_id'].endswith('-retry02') for row in bundle['attempts']))
            for row in bundle['attempts']:
                command=json.loads(pathlib.Path(row['command_path']).read_text());spec=json.loads(pathlib.Path(command['argv'][-3]).read_text())
                self.assertEqual(spec['protocol_sha256'],sha);self.assertEqual(spec['source_commit'],p.SOURCE)
                self.assertEqual(spec['primary_http_endpoint'],'received_ns')
                self.assertEqual(spec['native_source_pins']['sllm_store/tests/file_cache_state.py'],spec['cache_helper_sha256'])
                self.assertEqual(spec['archive'],str(archive));self.assertEqual(spec['input_inventory'],{'path':str(ip),'sha256':isha})
                self.assertEqual(spec['runs_root'],str(runs))
                body=command['argv'][command['argv'].index('-c')+1]
                self.assertIn("base=pathlib.Path(s['runs_root'])/run",body)
                self.assertNotIn("/home/boyjiang/kimi-new/evidence/runs",body)
                # Execute the actual generated mount builder against only local tiny fixture paths.
                names={'validate_role_contracts','create_argv'}
                nodes=[node for node in ast.parse(body).body if isinstance(node,ast.FunctionDef) and node.name in names]
                runbase=runs/spec['run_id']
                namespace={'ast':ast,'hashlib':p.hashlib,'s':spec,'D':['docker'],'network':'owned','base':runbase,'control':runbase/'control'}
                exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual-mount-builder>','exec'),namespace)
                for role in ('head','worker'):
                    argv,mounts=namespace['create_argv'](role)
                    self.assertTrue(any(m['Source']==str(sf) and m['Target']=='/mnt/raid/models/Kimi-K2.6' and m['ReadOnly'] for m in mounts))
                    self.assertTrue(any(m['Source']==str(archive) and m['Target']=='/store' and m['ReadOnly'] for m in mounts))
                    self.assertTrue(any(m['Source']==str(runbase/role) and m['Target']=='/evidence' for m in mounts))
                selected='safetensors' if spec['condition'].startswith('safetensors') else 'store'
                self.assertTrue(all(pathlib.Path(r['path']).is_relative_to(pathlib.Path(inventory['roots'][selected]['root'])) for r in inventory['roots'][selected]['files']))
                self.assertIn("gb=base/('guard-'+role)",body);self.assertIn("ob=base/'observer'",body)
            launch=json.loads(next(ep.glob('*-root-command.json')).read_text())
            self.assertIn('--property=RuntimeMaxSec=28917',launch['argv'])
            self.assertIn('--unit=kimi-four-v1-b01-retry02-formal-controller.service',launch['argv'])


    def test_actual_block_nonzero_without_receipt_has_failed_launch_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            execute,called,claims=self.block_fixture(pathlib.Path(directory),no_receipt_at=1)
            with self.assertRaises(AssertionError):execute()
            self.assertEqual(len(called),2)
            final=json.loads((claims/'tinyblock-final.json').read_text());second=final['attempts'][1]
            self.assertEqual(second['status'],'CONTROLLER_FAILED_WITHOUT_RECEIPT')
            self.assertEqual(second['condition_terminal'],'CONTROLLER_FAILED_WITHOUT_RECEIPT')
            self.assertTrue(second['launch_requested']);self.assertFalse(second['runtime_evidence_created']);self.assertFalse(second['attempted'])
            self.assertEqual(second['returncode'],2);self.assertIsNone(second['launch_error'])
            self.assertEqual(second['evidence_missing'],['controller-final.json','controller-failed.json'])
            self.assertEqual(second['cleanup_result'],'NOT_OBSERVED_NO_CHILD_RECEIPT')
            self.assertEqual(final['success_rate_denominator'],'launch_requested')
            self.assertEqual(final['launch_requested_count'],2);self.assertEqual(final['runtime_evidence_created_count'],1)
            for later in final['attempts'][2:]:
                self.assertEqual(later['status'],'NOT_STARTED');self.assertEqual(later['condition_terminal'],'NOT_STARTED');self.assertFalse(later['launch_requested'])

    def test_actual_block_timeout_without_receipt_is_terminal_and_no_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            execute,called,claims=self.block_fixture(pathlib.Path(directory),timeout_at=1)
            with self.assertRaises(p.subprocess.TimeoutExpired):execute()
            self.assertEqual(len(called),2)
            final=json.loads((claims/'tinyblock-final.json').read_text());second=final['attempts'][1]
            self.assertEqual(second['status'],'TIMED_OUT_NO_RECEIPT');self.assertEqual(second['condition_terminal'],'TIMED_OUT_NO_RECEIPT')
            self.assertTrue(second['launch_requested']);self.assertFalse(second['runtime_evidence_created'])
            self.assertIsNone(second['returncode']);self.assertEqual(second['launch_error']['type'],'TimeoutExpired')
            self.assertEqual(second['cleanup_result'],'NOT_OBSERVED_NO_CHILD_RECEIPT')
            self.assertEqual([row['status'] for row in final['attempts'][2:]],['NOT_STARTED','NOT_STARTED'])
            self.assertFalse(any(row['launch_requested'] for row in final['attempts'][2:]))

class SampleEvidenceTest(unittest.TestCase):
    def setup_evidence(self,root):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        clock={'boot_id':'boot','time_namespace':'time:[1]','monotonic_clock_implementation':'CLOCK_MONOTONIC','process_startticks':88}
        def save(name,value):
            with (root/name).open('x') as stream:json.dump(value,stream)
        namespace={'json':json,'pathlib':pathlib,'hashlib':p.hashlib,'time':types.SimpleNamespace(monotonic_ns=lambda:1000),'save':save,'base':root,'clock_identity':clock,'run':'run','s':{'primary_http_endpoint':'received_ns','attempt':{'attempt_id':'run','block':1},'condition':'safetensors_disk_hot','source_commit':'a'*40,'protocol_sha256':'b'*64,'correctness':protocol['correctness']},'cids':{'head':'cid'},'children':[]}
        exec(p.HTTP_EVENTS+p.SAMPLE_EVIDENCE+p.RESPONSE,namespace)
        rows=[]
        for index,want in enumerate(protocol['correctness']['requests']):
            expected=want['expected']
            row={'status':200,'response':{'choices':[{'message':{'content':expected['content']},'finish_reason':expected['finish_reason']}],'usage':{'prompt_tokens':expected['prompt_tokens'],'completion_tokens':expected['completion_tokens'],'total_tokens':expected['prompt_tokens']+expected['completion_tokens']}},'path':'/v1/chat/completions','request_id':'run-'+str(index),'pid':321,'clock_identity':clock,'registration_command_ns':1 if index==0 else None,'register_complete_ns':2 if index==0 else None,'business_trigger_ns':3,'request_begin_ns':4,'response_body_complete_ns':5,'json_parse_complete_ns':6,'received_ns':7,'child_gold_validation_begin_ns':8,'child_gold_validation_complete_ns':9,'child_gold_validation_pass':True}
            output=root/(str(index)+'-head-http.stdout');raw=json.dumps(row).encode();output.write_bytes(raw)
            row['stdout_reference']={'path':str(output),'bytes':len(raw),'sha256':p.hashlib.sha256(raw).hexdigest()}
            rows.append((row,want,index))
        return namespace,rows

    def test_child_malformed_gold_keeps_received_and_returns_invalid_row(self):
        clock=[0]
        def now():
            clock[0]+=1
            return clock[0]
        namespace={'time':types.SimpleNamespace(monotonic_ns=now)}
        exec(functions_from(p.HTTP_BODY),namespace)
        namespace['perform_http']=lambda *args:{'status':200,'response':{}}
        want=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)['correctness']['requests'][0]
        row=namespace['finish_http']('/v1/chat/completions',{},30,want)
        self.assertEqual(row['received_ns'],2)
        self.assertFalse(row['child_gold_validation_pass'])
        self.assertEqual(row['child_gold_validation_error']['type'],'KeyError')
        self.assertGreater(row['child_gold_validation_complete_ns'],row['received_ns'])

    def test_cleanup_failure_keeps_metrics_and_normal_condition_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ns,rows=self.setup_evidence(root)
            for row,want,index in rows:ns['check_response'](row,want,index,1)
            for gate in ns['sample_gates']:ns['mark_sample_gate'](gate,True,'fixture proof')
            ns['condition_normal']=True;ns['condition_terminal'](True,'business/deletion normal')
            before=(root/'sample-metrics.json').read_bytes()
            result=ns['finalize_sample_evidence']({'normal':False,'status':'FAILED','quiet_seconds':None,'reason':'Store normal exit timeout'},'cleanup failed')
            self.assertFalse(result['primary_sample_qualified'])
            self.assertEqual(before,(root/'sample-metrics.json').read_bytes())
            metrics=json.loads(before)['requests'][0]
            self.assertEqual((metrics['primary_A_ns'],metrics['business_B_ns']),(6,4))
            self.assertTrue(json.loads((root/'condition-terminal.json').read_text())['normal'])
            self.assertFalse(json.loads((root/'cleanup-result.json').read_text())['normal'])

    def test_all_proven_success_qualifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ns,rows=self.setup_evidence(root)
            for row,want,index in rows:ns['check_response'](row,want,index,1)
            for gate in ns['sample_gates']:ns['mark_sample_gate'](gate,True,'fixture proof including exact trace IDs')
            ns['condition_normal']=True
            result=ns['finalize_sample_evidence']({'normal':True,'status':'NORMAL_QUIET60','quiet_seconds':60},'normal')
            self.assertTrue(result['primary_sample_qualified']);self.assertEqual(result['invalid_reasons'],[])
            self.assertEqual(len(json.loads((root/'sample-validity.json').read_text())['evidence']),3)

    def test_gold_failure_preserves_original_interval_and_invalidity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ns,rows=self.setup_evidence(root)
            row,want,index=rows[0];row['child_gold_validation_pass']=False;row['response']['choices'][0]['message']['content']='wrong'
            with self.assertRaises(AssertionError):ns['check_response'](row,want,index,1)
            ns['mark_sample_gate']('gold',False,'child text/usage/finish mismatch')
            result=ns['finalize_sample_evidence']({'normal':False,'status':'NOT_OBSERVED','quiet_seconds':None},'gold failed')
            self.assertFalse(result['primary_sample_qualified'])
            arithmetic=json.loads((root/'sample-metrics.json').read_text())['requests'][0]
            self.assertEqual((arithmetic['primary_A_ns'],arithmetic['business_B_ns']),(6,4))
            self.assertFalse(arithmetic['child_gold_validation_pass'])
            self.assertTrue(any('gold:FAIL' in reason for reason in result['invalid_reasons']))

    def test_missing_inputs_fail_closed_and_recover_stdout_on_child_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ns,rows=self.setup_evidence(root)
            row,want,index=rows[0];output=pathlib.Path(row['stdout_reference']['path'])
            row['child_gold_validation_pass']=False;output.write_text(json.dumps(row))
            ns['children']=[(None,types.SimpleNamespace(name=str(output)),None)]
            # Child finish failed before returning a row to the parent: recover only its existing stdout.
            result=ns['finalize_sample_evidence']({'normal':False,'status':'NOT_OBSERVED','quiet_seconds':None},'child process failed')
            self.assertFalse(result['primary_sample_qualified'])
            metrics=json.loads((root/'sample-metrics.json').read_text())['requests']
            self.assertEqual(metrics[0]['primary_A_ns'],6)
            self.assertEqual(metrics[1]['observation_status'],'NOT_OBSERVED')
            self.assertTrue(any('NOT_OBSERVED' in reason for reason in result['invalid_reasons']))
            pass_gates={name:{'status':'PASS'} for name in ('cache','gold','clock','ownership')}
            self.assertFalse(ns['sample_qualification'](pass_gates,{'normal':True},{'normal':True,'quiet_seconds':60},{})['primary_sample_qualified'])

    def test_generated_exception_diagnostic_failure_keeps_abort_and_original_error(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA)
        command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        spec,command=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        body=command['argv'][command['argv'].index('-c')+1]
        handler=next(node.handlers[0] for node in ast.parse(body).body if isinstance(node,ast.Try) and node.handlers and node.handlers[0].name=='error' and "sample-evidence-error.json" in ast.get_source_segment(body,node))
        actual=ast.Try(body=[ast.Raise(exc=ast.Name(id='original',ctx=ast.Load()),cause=None)],handlers=[copy.deepcopy(handler)],orelse=[],finalbody=[])
        program=compile(ast.fix_missing_locations(ast.Module(body=[actual],type_ignores=[])),'<actual-generated-error-handler>','exec')
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for role in ('guard-head','guard-worker','observer'):(root/role).mkdir()
            saved={};original=RuntimeError('original business failure')
            def finalize(*args):raise OSError('evidence disk failure')
            def save(name,value):
                if name=='sample-evidence-error.json':raise PermissionError('diagnostic save also fails')
                saved[name]=value
            def broken_print(*args,**kwargs):raise OSError('stderr unavailable')
            namespace={'original':original,'base':root,'sample_active_gate':None,'finalize_sample_evidence':finalize,'cleanup_started':False,'save':save,'print':broken_print,'json':json,'time':types.SimpleNamespace(monotonic=lambda:0),'end':100,'units':{},'props':lambda unit:{'MainPID':'0'},'started':0,'cids':{},'s':spec}
            with self.assertRaises(RuntimeError) as caught:exec(program,namespace)
            self.assertIs(caught.exception,original)
            self.assertTrue(all((root/role/'ABORT').exists() for role in ('guard-head','guard-worker','observer')))
            self.assertEqual(saved['controller-failed.json']['error'],'original business failure')
            self.assertEqual(saved['controller-failed.json']['error_type'],'RuntimeError')

class LocalRouteTest(unittest.TestCase):
    def test_actual_runtime_findmnt_gate_accepts_only_approved_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for name in ('sf','store','runs'):(root/name).mkdir()
            spec={'storage_route':{'route':'local_nvme'},'safetensors_host':str(root/'sf'),'archive':str(root/'store'),'runs_root':str(root/'runs')}
            result={'filesystems':[{'source':'/dev/nvme1n1','fstype':'xfs','target':str(root)}]}
            calls=[]
            def findmnt(argv,**kwargs):
                calls.append(argv)
                self.assertEqual(argv[:3],['/usr/bin/findmnt','--json','--target'])
                return json.dumps(result)
            namespace={'s':spec,'json':json,'pathlib':pathlib,'subprocess':types.SimpleNamespace(check_output=findmnt)}
            exec(p.STORAGE_ROUTE_CODE,namespace)
            self.assertEqual(set(namespace['storage_route_proof']),{'safetensors','store_archive','runs_root'})
            self.assertEqual(len(calls),3)
            result['filesystems'][0]['source']='qjhs0:/raid'
            with self.assertRaises(AssertionError):exec(p.STORAGE_ROUTE_CODE,namespace)
            result['filesystems'][0].update(source='/dev/nvme1n1',fstype='nfs4')
            with self.assertRaises(AssertionError):exec(p.STORAGE_ROUTE_CODE,namespace)
            self.assertEqual(list((root/'runs').iterdir()),[])

    def test_frozen_route_rejects_legacy_manifest_paths_and_wrong_pin(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        protocol.update(status='FROZEN_PREREGISTRATION',final_deploy_commit=p.SOURCE)
        protocol['statistics']['success_rate_denominator']='launch_requested'
        for request in protocol['correctness']['requests']:request['logprobs']=1
        protocol['correctness']['observability']='logprobs=1 matches GLM observation compute; return_token_ids omitted from SamplingParams, actual token IDs from existing trace; explicit top_p=1 vs GLM omitted default.'
        protocol['resource_budget'].update(per_complete_block_max_seconds=28920,parent_overhead_seconds=120,runs_root=p.RUNS_ROOT)
        protocol['events'].update(primary_http_endpoint='received_ns',primary_start_event='same_http_client_registration_command_ns')
        protocol['assets'].update(route='local_nvme',safetensors=p.SF_HOST,store_archive=p.STORE_ARCHIVE,store=p.STORE_ARCHIVE+'/vllm/kimi-k2.6-sf-reference',input_inventory_path='/tmp/new-local-manifest.json',input_inventory_sha256='c'*64)
        p.validate_frozen_protocol(protocol,p.SOURCE)
        changes=[('assets','route','nfs'),('assets','input_inventory_path',str(p.INVENTORY)),('assets','input_inventory_sha256',p.INVENTORY_SHA),('assets','safetensors','/mnt/raid/models/Kimi-K2.6'),('assets','store_archive','/home/boyjiang/kimi-new/store-conversion-20261105-once'),('resource_budget','runs_root','/home/boyjiang/kimi-new/evidence/runs')]
        for group,key,value in changes:
            bad=copy.deepcopy(protocol);bad[group][key]=value
            with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            manifest=pathlib.Path(directory)/'manifest.json';manifest.write_text('{}')
            with self.assertRaises(AssertionError):p.checked(manifest,'0'*64)
            with self.assertRaises(AssertionError):p.validate_inventory({'roots':{'safetensors':{'root':'/old/nfs'},'store':{'root':'/old/store'}}},{'safetensors':'/local/sf','store':'/local/store'})

class LogprobsAlignmentTest(unittest.TestCase):
    def test_actual_loop_payload_and_http_keep_logprobs_one_without_openai_flags(self):
        import textwrap
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        tree=ast.parse(textwrap.dedent(p.LOOP))
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple))
        # Execute the actual LOOP payload construction, then the actual HTTP consumer.
        statements=[n for n in loop.body if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='payload' for t in n.targets)) or (isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='payload')]
        make_payload=compile(ast.Module(body=statements,type_ignores=[]),'<actual-loop-payload>','exec')
        tick=[0];seen=[]
        def now():tick[0]+=1;return tick[0]
        class Response:
            status=200
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,limit):return json.dumps(self.value).encode()
        def urlopen(request,timeout):
            payload=json.loads(request.data)
            if request.full_url.endswith('/register'):return Response({})
            seen.append(payload)
            want=protocol['correctness']['requests'][len(seen)-1];expected=want['expected']
            return Response({'choices':[{'message':{'content':expected['content']},'finish_reason':expected['finish_reason'],'logprobs':{'observed':1}}],'usage':{'prompt_tokens':expected['prompt_tokens'],'completion_tokens':expected['completion_tokens'],'total_tokens':expected['prompt_tokens']+expected['completion_tokens']}})
        namespace={'json':json,'urllib':types.SimpleNamespace(request=types.SimpleNamespace(Request=urllib.request.Request,urlopen=urlopen)),'time':types.SimpleNamespace(monotonic_ns=now),'HTTP_CLOCK':{'boot_id':'boot'},'HTTP_PID':123}
        exec(functions_from(p.HTTP_BODY),namespace)
        illegal={'return_token_ids':True,'top_logprobs':1,'stream':False,'chat_template_kwargs':{'thinking':False}}
        for index,want in enumerate(protocol['correctness']['requests']):
            build={'want':{**want,**illegal},'s':{'config':{'model':'kimi'}},'run':'run','index':index}
            exec(make_payload,build);payload=build['payload']
            self.assertEqual(payload['logprobs'],1)
            self.assertFalse(set(illegal)&set(payload))
            for key in ('input_tokens','temperature','top_p','top_k','min_p','seed','n','max_tokens','ignore_eos'):
                self.assertEqual(payload[key],want[key])
            row=namespace['finish_http']('/v1/chat/completions',payload,30,want,{'model':'kimi'} if index==0 else None)
            self.assertTrue(row['child_gold_validation_pass'])
            # These are the only remaining kwargs after the backend's existing model/request/input pops.
            sampling={key:value for key,value in payload.items() if key not in ('model','request_id','input_tokens')}
            self.assertEqual(set(sampling),{'temperature','top_p','top_k','min_p','seed','n','max_tokens','ignore_eos','logprobs'})
        self.assertEqual([payload['logprobs'] for payload in seen],[1,1,1])
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        rendered,_=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        self.assertEqual([row['logprobs'] for row in rendered['actual_request_parameters']],[1,1,1])
        self.assertEqual(rendered['status'],'CANDIDATE_NOT_FROZEN_NOT_EXECUTABLE')

    def test_frozen_protocol_allows_only_logprobs_and_observability_delta(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        protocol.update(status='FROZEN_PREREGISTRATION',final_deploy_commit=p.SOURCE)
        protocol['statistics']['success_rate_denominator']='launch_requested'
        protocol['resource_budget'].update(per_complete_block_max_seconds=28920,parent_overhead_seconds=120,runs_root=p.RUNS_ROOT)
        protocol['events'].update(primary_http_endpoint='received_ns',primary_start_event='same_http_client_registration_command_ns')
        protocol['assets'].update(route='local_nvme',safetensors=p.SF_HOST,store_archive=p.STORE_ARCHIVE,store=p.STORE_ARCHIVE+'/vllm/kimi-k2.6-sf-reference',input_inventory_path='/tmp/new-local-manifest.json',input_inventory_sha256='c'*64)
        for request in protocol['correctness']['requests']:request['logprobs']=1
        protocol['correctness']['observability']='logprobs=1 actual compute; return_token_ids not sent, IDs via existing trace. Explicit top_p=1 vs GLM omitted default.'
        p.validate_frozen_protocol(protocol,p.SOURCE)
        for key,value in (('logprobs',0),('logprobs',True),('return_token_ids',True),('seed',0)):
            bad=copy.deepcopy(protocol);bad['correctness']['requests'][0][key]=value
            with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)
        bad=copy.deepcopy(protocol);del bad['correctness']['requests'][1]['logprobs']
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)
        bad=copy.deepcopy(protocol);bad['correctness']['requests'][2]['expected']['token_ids'][0]=0
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)
        bad=copy.deepcopy(protocol);bad['correctness']['observability']='all matched'
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)

class ControllerCostEventTest(unittest.TestCase):
    def test_actual_event_samples_clock_and_pid_before_delayed_write(self):
        node=next(n for n in ast.parse(p.CACHE_CODE).body if isinstance(n,ast.FunctionDef) and n.name=='event')
        function=compile(ast.Module(body=[node],type_ignores=[]),'<actual-event>','exec')
        current=[100];saved={}
        def now():
            current[0]+=1
            return current[0]
        def save(name,value):
            sampled=copy.deepcopy(value);write_begin=current[0];current[0]+=10**9
            saved[name]=(sampled,write_begin,current[0])
        namespace={'time':types.SimpleNamespace(monotonic=now,monotonic_ns=now,time=now),'os':types.SimpleNamespace(getpid=lambda:123),'save':save,'clock_identity':{'boot_id':'boot','process_startticks':17},'run':'run','s':{'source_commit':'a'*40,'protocol_sha256':'b'*64,'attempt':{'block':1,'attempt_id':'run'},'condition':'store_disk_cold'}}
        exec(function,namespace);namespace['event']('ray_join_begin')
        record,write_begin,write_end=saved['event-ray_join_begin.json']
        self.assertEqual(record['pid'],123);self.assertEqual(record['monotonic_ns'],102)
        self.assertLessEqual(record['monotonic_ns'],write_begin)
        self.assertLess(record['wall'],write_end)
        self.assertEqual(record['clock_identity']['process_startticks'],17)
        self.assertEqual(record['measurement'],'controller_observed_operation_boundary_not_pure_CUDA_or_IO')
        namespace['event']('ray_join_complete')
        complete=saved['event-ray_join_complete.json'][0]
        self.assertGreater(complete['monotonic_ns'],write_end)
        self.assertEqual(record['monotonic_ns'],102)

    def test_generated_ast_cost_endpoints_surround_actual_operations(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA)
        command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        spec,command=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        body=command['argv'][command['argv'].index('-c')+1]
        main=next(n for n in ast.parse(body).body if isinstance(n,ast.Try) and any(isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id=='create_arm_start' for x in ast.walk(n)))
        calls=sorted([n for n in ast.walk(main) if isinstance(n,ast.Call)],key=lambda n:(n.lineno,n.col_offset))
        def named(call,name):return isinstance(call.func,ast.Name) and call.func.id==name
        def constant(call,index,value):return len(call.args)>index and isinstance(call.args[index],ast.Constant) and call.args[index].value==value
        def position(name,predicate=lambda n:True):
            selected=[i for i,n in enumerate(calls) if named(n,name) and predicate(n)]
            self.assertEqual(len(selected),1,(name,len(selected)))
            return selected[0]
        def event(name):return position('event',lambda n:constant(n,0,name))
        def bracket(begin,operation,end):self.assertLess(event(begin),operation);self.assertLess(operation,event(end))
        bracket('common_environment_begin',position('create_arm_start',lambda n:constant(n,0,'head')),'common_environment_complete')
        bracket('common_environment_begin',position('execute',lambda n:constant(n,0,'worker') and constant(n,1,'imports')),'common_environment_complete')
        for role in ('head','worker'):
            bracket('ray_join_begin',position('official',lambda n:constant(n,0,role) and constant(n,1,'ray.scripts.scripts') and len(n.args)>2 and isinstance(n.args[2],ast.Name)),'ray_join_complete')
        store_start=position('official',lambda n:constant(n,1,'sllm_store.cli'))
        config=position('execute',lambda n:constant(n,1,'store_config'))
        bracket('store_pool_initialization_begin',store_start,'store_pool_initialization_complete')
        bracket('store_pool_initialization_begin',config,'store_pool_initialization_complete')
        initial=position('baseline_window',lambda n:constant(n,0,None))
        bracket('initial_baseline60_begin',initial,'initial_baseline60_complete')
        self.assertLess(event('store_pool_initialization_complete'),event('initial_baseline60_begin'))
        api=position('official',lambda n:constant(n,0,'head') and constant(n,1,'sllm.cli.clic') and isinstance(n.args[2],ast.List) and isinstance(n.args[2].elts[0],ast.Constant) and n.args[2].elts[0].value=='start')
        auth=position('save',lambda n:constant(n,0,'controller-initial-authenticated-identity.json'))
        bracket('api_startup_begin',api,'api_controller_authentication_complete')
        bracket('api_startup_begin',auth,'api_controller_authentication_complete')
        bracket('delete_begin',position('HTTP',lambda n:constant(n,0,'/delete')),'delete_acknowledged')
        deleted=position('baseline_window',lambda n:len(n.args)>0 and isinstance(n.args[0],ast.Subscript))
        bracket('delete_baseline60_begin',deleted,'delete_baseline60_complete')
        shutdown=position('execute',lambda n:constant(n,1,'controller_shutdown'))
        api_stop=position('stop_service',lambda n:constant(n,0,'api'))
        for operation in (shutdown,api_stop):bracket('controller_shutdown_and_api_stop_begin',operation,'controller_shutdown_and_api_stop_complete')
        self.assertLess(event('ordered_final_stop_begin'),event('controller_shutdown_and_api_stop_begin'))
        store_stop=position('stop_service',lambda n:constant(n,0,'store'))
        self.assertLess(event('controller_shutdown_and_api_stop_complete'),store_stop)
        for role in ('worker','head'):
            normal=position('normal_exit',lambda n:constant(n,0,role))
            self.assertLess(store_stop,normal);self.assertLess(normal,event('containers_normal_exit_complete'))
        self.assertLess(event('containers_normal_exit_complete'),event('release_quiet60_complete'))
        # End of release follows actual zero-GPU/gone proof and precedes the final source check.
        gpu_calls=[i for i,n in enumerate(calls) if isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='m' and n.func.attr=='gpu']
        self.assertLess(gpu_calls[-1],event('release_quiet60_complete'))
        source_checks=[i for i,n in enumerate(calls) if named(n,'checkfiles')]
        self.assertLess(event('release_quiet60_complete'),source_checks[-1])
        self.assertEqual(spec['budgets']['full'],7200);self.assertEqual(spec['budgets']['sum'],6480)
        self.assertIn("store_stop_end=time.monotonic()+60",body)

class ExecutionRevisionTest(unittest.TestCase):
    def revision_protocol(self,root):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        protocol['resource_budget']['runs_root']=str(root/'runs');(root/'runs').mkdir()
        mapping={row['attempt_id']:row['attempt_id']+('-retry02' if row['block'] in (1,2) else '') for row in protocol['attempts']}
        blocks={str(number):'kimi-four-v1-b%02d'%number+('-retry02' if number in (1,2) else '') for number in range(1,11)}
        history=[]
        for number in (1,2):
            old=next(row for row in protocol['attempts'] if row['block']==number and row['position']==1)
            receipt=root/('history-b%02d.json'%number);receipt.write_text(json.dumps({'status':'FAILED_RETAINED','run_id':old['attempt_id']}))
            history.append({'run_id':old['attempt_id'],'logical_attempt_id':old['attempt_id'],'block':number,'launch_requested':True,'primary_sample_qualified':False,'status':'FAILED_RETAINED','result_path':str(receipt),'result_sha256':p.hashlib.sha256(receipt.read_bytes()).hexdigest()})
        protocol['execution_revision']={'authorization_date':'2026-11-06','user_instruction':'修复完重新跑b01 然后是2~10','reason':'Explicit user-authorized whole-block rerun after two controller faults; historical partials retain failure.','rerun_blocks':[1,2],'suffix':'retry02','run_id_map':mapping,'block_run_ids':blocks,'historical_attempts':history,'historical_unstarted':[row['attempt_id'] for row in protocol['attempts'] if row['block'] in (1,2) and row['position']>1],'actual_launch_requests_at_authorization':2,'target_total_launch_requests_max':42,'stop_on_new_failure':True,'selection_rule':'fixed_40_mapped_runs_no_latency_selection'}
        return protocol

    def test_retry_revision_maps_all_runtime_names_without_replacing_logical_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);protocol=self.revision_protocol(root)
            spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
            for logical in protocol['attempts']:
                rendered,prepared=p.render(protocol,spec,command,logical['attempt_id'],spec['native_source_pins'])
                wanted=protocol['execution_revision']['run_id_map'][logical['attempt_id']]
                self.assertEqual(rendered['run_id'],wanted)
                self.assertEqual(rendered['attempt'],logical)
                self.assertEqual(rendered['logical_attempt_id'],logical['attempt_id'])
                self.assertEqual(rendered['planned_attempts'],protocol['attempts'])
                self.assertTrue(all(request['name']==wanted+'-'+role for role,request in rendered['requests'].items()))
                self.assertIn('--unit='+wanted+'-controller.service',prepared['argv'])
            self.assertEqual(len(protocol['attempts']),40)
            self.assertEqual(protocol['execution_revision']['actual_launch_requests_at_authorization'],2)

    def test_revision_rejects_unapproved_or_duplicate_ids_and_selected_used_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);protocol=self.revision_protocol(root)
            first=protocol['attempts'][0]['attempt_id'];third=protocol['attempts'][8]['attempt_id']
            for logical,value in ((first,first),(first,first+'-retry03'),(third,third+'-retry02'),(third,protocol['execution_revision']['run_id_map'][first])):
                bad=copy.deepcopy(protocol);bad['execution_revision']['run_id_map'][logical]=value
                with self.assertRaises(AssertionError):p.validate_protocol(bad)
            bad=copy.deepcopy(protocol);bad['execution_revision']['historical_attempts'][0]['primary_sample_qualified']=True
            with self.assertRaises(AssertionError):p.validate_protocol(bad)
            bad=copy.deepcopy(protocol);bad['execution_revision']['stop_on_new_failure']=False
            with self.assertRaises(AssertionError):p.validate_protocol(bad)
            used=pathlib.Path(protocol['resource_budget']['runs_root'])/protocol['execution_revision']['run_id_map'][first];used.mkdir()
            spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
            with self.assertRaises(AssertionError):p.render(protocol,spec,command,first,spec['native_source_pins'])
            self.assertEqual(list(used.iterdir()),[])

    def test_actual_block_new_claims_preserve_old_claims_and_failed_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);history=root/'history';history.mkdir();protocol=self.revision_protocol(history)
            block=root/'block';block.mkdir();revision=protocol['execution_revision']
            originals=[row for row in protocol['attempts'] if row['block']==1]
            ids=[revision['run_id_map'][row['attempt_id']] for row in originals]
            block_id=revision['block_run_ids']['1']
            execute,called,claims=ActualConsumerTest().block_fixture(block,run_ids=ids,block_id=block_id)
            old_claim=claims/'kimi-four-v1-b01.claim';old_claim.write_bytes(b'historical claim retained')
            old_run=block/'runs'/originals[0]['attempt_id'];old_run.mkdir();(old_run/'keep').write_bytes(b'failed original')
            bp=block/'bundle.json';bundle=json.loads(bp.read_text());bundle.update(execution_revision=revision,planned_attempts=protocol['attempts'],logical_block_id='kimi-four-v1-b01');bp.write_text(json.dumps(bundle))
            execute()
            self.assertEqual(len(called),4)
            self.assertEqual(old_claim.read_bytes(),b'historical claim retained')
            self.assertEqual((old_run/'keep').read_bytes(),b'failed original')
            self.assertTrue((claims/(block_id+'.claim')).exists())
            final=json.loads((claims/(block_id+'-final.json')).read_text())
            self.assertEqual([row['run_id'] for row in final['attempts']],ids)
            self.assertEqual(final['planned_attempts'],protocol['attempts'])
            self.assertEqual(final['execution_revision']['historical_attempts'],revision['historical_attempts'])
            self.assertEqual(final['execution_revision']['actual_launch_requests_at_authorization'],2)
            self.assertEqual(final['execution_revision']['target_total_launch_requests_max'],42)
            self.assertEqual(final['launch_requested_count'],4)


class DiagnosticValueTest(unittest.TestCase):
    def fingerprint_fixture(self):
        import hashlib
        def tensor(name,kind,storage,size,shape):
            return dict(name=name,kind=kind,device='cuda:0',storage_id=storage,storage_offset=0,bytes=size,dtype='torch.int32',shape=shape,stride=[1])
        tensors=[tensor('w','parameter',0,4,[1]),tensor('empty','parameter',1,0,[0]),tensor('alias','parameter',0,4,[1]),tensor('runtime','buffer',2,4,[1])]
        def row(t):return {k:t[k] for k in ('name','kind','dtype','shape','stride')}|{'numbytes':t['bytes'],'sha256':hashlib.sha256(b'' if t['bytes']==0 else b'abcd').hexdigest()}
        items={t['name']:row(t) for t in tensors};items['alias']['alias_of']='w'
        index={'w':[0,4,[1],[1],'torch.int32'],'empty':[8,0,[0],[1],'torch.int32']}
        fp={'schema':'kimi-load-boundary-value-fingerprint-v1','stage':'before_first_forward','status':'PASS','coverage_complete':True,'chunk_bytes':67108864,'budget_seconds':600,'elapsed_seconds':1,'index_path':'/store/vllm/kimi-k2.6-sf-reference/rank_0/tensor_index.json','index_sha256':'a'*64,'persistent':[items['w'],items['empty']],'runtime_buffers':[items['runtime']],'aliases':[items['alias']],'counts':dict(index_keys=2,persistent=2,runtime_buffers=1,aliases=1,registered_names=4,unique_hashed_ranges=2,hashed_bytes=8,chunks=2)}
        return {'tp_rank':0,'registered_tensors':tensors,'diagnostic_value_fingerprint':fp},index

    def test_actual_fingerprint_validator_complete_and_fail_closed(self):
        record,index=self.fingerprint_fixture();p.validate_diagnostic_fingerprint(record,index,'a'*64)
        # A different valid value hash is a diagnostic observation, not a capture failure.
        changed=copy.deepcopy(record);changed['diagnostic_value_fingerprint']['runtime_buffers'][0]['sha256']='b'*64
        p.validate_diagnostic_fingerprint(changed,index,'a'*64)
        for field,value in [('status','TIMEOUT'),('coverage_complete',False),('elapsed_seconds',601),('index_sha256','b'*64)]:
            bad=copy.deepcopy(record);bad['diagnostic_value_fingerprint'][field]=value
            with self.assertRaises(AssertionError):p.validate_diagnostic_fingerprint(bad,index,'a'*64)
        for count in ('unique_hashed_ranges','hashed_bytes','chunks'):
            bad=copy.deepcopy(record);bad['diagnostic_value_fingerprint']['counts'][count]=0
            with self.assertRaises(AssertionError):p.validate_diagnostic_fingerprint(bad,index,'a'*64)
        for group in ('persistent','runtime_buffers','aliases'):
            bad=copy.deepcopy(record);bad['diagnostic_value_fingerprint'][group].pop()
            with self.assertRaises(AssertionError):p.validate_diagnostic_fingerprint(bad,index,'a'*64)
        bad=copy.deepcopy(record);bad['diagnostic_value_fingerprint']['persistent'][1]['sha256']='c'*64
        with self.assertRaises(AssertionError):p.validate_diagnostic_fingerprint(bad,index,'a'*64)
        bad=copy.deepcopy(record);bad['diagnostic_value_fingerprint']['aliases'][0]['alias_of']='runtime'
        with self.assertRaises(AssertionError):p.validate_diagnostic_fingerprint(bad,index,'a'*64)
        bad=copy.deepcopy(record);bad['registered_tensors'][-1]['kind']='parameter';bad['diagnostic_value_fingerprint']['runtime_buffers'][0]['kind']='parameter'
        with self.assertRaises(AssertionError):p.validate_diagnostic_fingerprint(bad,index,'a'*64)

    def generated(self,root):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        overlay=p.base.ROOT/'sllm_store/build/kimi-vllm0191-diagnostic-values-v1/v1/worker/gpu_model_runner.py';before=copy.deepcopy(protocol)
        generated=p.render_diagnostic(protocol,spec,command,spec['native_source_pins'],overlay,p.base.digest(overlay))
        self.assertEqual(protocol,before)
        return protocol,generated

    def test_render_four_cases_formal_unchanged_budget_and_unique_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            protocol,generated=self.generated(pathlib.Path(directory))
            self.assertEqual([x['condition'] for x,_ in generated],['safetensors_disk_hot','safetensors_disk_hot','store_disk_cold','store_pool_hit'])
            self.assertEqual(len({x['run_id'] for x,_ in generated}),4)
            for spec,command in generated:
                body=command['argv'][command['argv'].index('-c')+1];compile(body,'<diag>','exec')
                self.assertNotIn('FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS',body)
                self.assertIn('DIAGNOSTIC_OBSERVATION_RELEASE60_COMPLETE_NOT_FORMAL',body)
                self.assertIn("logprobs=want['logprobs'])",body);self.assertIn('verify_diagnostic_records(records)',body)
                self.assertEqual(spec['budgets']['full'],7200);self.assertEqual(spec['budgets']['sum'],6480)
                self.assertEqual(len(spec['correctness']['requests']),5)
                self.assertEqual(len({x['case'] for x in spec['correctness']['requests']}),5)
                self.assertEqual([x['input_tokens'] for x in spec['correctness']['requests'][2:]],[protocol['correctness']['requests'][2]['input_tokens']]*3)
                self.assertNotIn("assert row.get('child_gold_validation_pass',True)",spec['role_bodies']['http'])
                self.assertIn("find_spec('flashinfer')",spec['role_bodies']['imports']);self.assertNotIn('import flashinfer',spec['role_bodies']['imports'])
                self.assertTrue(all(v['env']['SLLM_DIAGNOSTIC_VALUE_FINGERPRINT']=='1' for v in spec['requests'].values()))
            original=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
            _,normal=p.render(protocol,original,command,protocol['attempts'][0]['attempt_id'],original['native_source_pins'])
            self.assertEqual(normal['root_python_sha256'],'9909a51f90f44a25f7c91c5a30043a68a6530fca0213326be83a3d66407ccad2')
            used=copy.deepcopy(protocol);used['resource_budget']['runs_root']=str(pathlib.Path(directory)/'runs');(pathlib.Path(directory)/'runs/kimi-diagnostic-value-20261107-sf1').mkdir(parents=True)
            overlay=p.base.ROOT/'sllm_store/build/kimi-vllm0191-diagnostic-values-v1/v1/worker/gpu_model_runner.py'
            with self.assertRaises(AssertionError):p.render_diagnostic(used,original,command,original['native_source_pins'],overlay,p.DIAGNOSTIC_OVERLAY_SHA)

    def test_observed_gold_mismatch_continues_but_bad_http_or_top2_stops(self):
        saved={};namespace={'math':__import__('math'),'json':json,'pathlib':pathlib,'hashlib':p.hashlib,'record_http_sample':lambda *a:None,'validated_http_events':lambda *a:{},'save':lambda name,value:saved.update({name:value})}
        exec(p.DIAGNOSTIC_FUNCTIONS,namespace)
        want={'case':'fixed32','input_tokens':[9], 'logprobs':2,'max_tokens':32,'expected':{'token_ids':[1]*32,'content':'gold','finish_reason':'length'}}
        row={'status':200,'request_id':'diag-2','response':{'choices':[{'message':{'content':'changed'},'finish_reason':'length','logprobs':[{'2':{'rank':1,'logprob':-.1},'1':{'rank':2,'logprob':-2.}}]*32}],'usage':{'prompt_tokens':1,'completion_tokens':32,'total_tokens':33}}}
        namespace['diagnostic_response'](row,want,2,1)
        self.assertTrue(saved['diagnostic-response-fixed32.json']['gold_false'])
        self.assertEqual(namespace['diagnostic_observations']['fixed32']['actual_ids'],[2]*32)
        bad=copy.deepcopy(row);bad['status']=500
        with self.assertRaises(AssertionError):namespace['diagnostic_response'](bad,want,2,1)
        bad=copy.deepcopy(row);bad['response']['choices'][0]['logprobs'][0]={'1':{'rank':1,'logprob':-.1}}
        with self.assertRaises(AssertionError):namespace['diagnostic_response'](bad,want,2,1)
        complete=namespace['diagnostic_qualification']({}, {'normal':True},{'normal':True,'quiet_seconds':60},{})
        self.assertFalse(complete['primary_sample_qualified']);self.assertFalse(complete['diagnostic_observation_complete'])

    def test_actual_prepare_diagnostic_ten_json_and_block_inline_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
            overlay=p.base.ROOT/'sllm_store/build/kimi-vllm0191-diagnostic-values-v1/v1/worker/gpu_model_runner.py';out=root/'out';out.mkdir()
            with mock.patch.object(p.base,'EVIDENCE',out):
                files=p.prepare_diagnostic(protocol,spec,command,spec['native_source_pins'],p.SOURCE,p.PROTOCOL,p.PROTOCOL_SHA,overlay,p.base.digest(overlay))
            self.assertEqual(len(files),10)
            bundle=json.loads(next(out.glob('*-bundle.json')).read_text());self.assertFalse(bundle['primary_sample_qualified']);self.assertEqual(bundle['full_max_seconds'],28920)
            tree=ast.parse(bundle['block_python']);transform=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='argv' for t in n.targets) and isinstance(n.value,ast.List) and any(isinstance(x,ast.Constant) and x.value=='--wait' for x in n.value.elts))
            for entry in bundle['attempts']:
                cmd=json.loads(pathlib.Path(entry['command_path']).read_text());original=cmd['argv'][cmd['argv'].index('-c')+1];namespace={'argv':cmd['argv'],'expiry':999999.}
                exec(compile(ast.Module(body=[transform],type_ignores=[]),'<actual-diag-argv>','exec'),namespace)
                self.assertEqual(namespace['argv'][namespace['argv'].index('-c')+1],original)
                self.assertIn('--property=RemainAfterExit=yes',original)
            with mock.patch.object(p.base,'EVIDENCE',out):
                with self.assertRaises(AssertionError):p.prepare_diagnostic(protocol,spec,command,spec['native_source_pins'],p.SOURCE,p.PROTOCOL,p.PROTOCOL_SHA,overlay,p.base.digest(overlay))


    def test_diagnostic_actual_main_and_block_failure_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);out=root/'out';out.mkdir();overlay=p.base.ROOT/'sllm_store/build/kimi-vllm0191-diagnostic-values-v1/v1/worker/gpu_model_runner.py'
            protocol=json.loads((p.base.EVIDENCE/'kimi-four-condition-10block-preregistration-local-nvme-v4-retry02-20261106.json').read_text());protocol['final_deploy_commit']=p.SOURCE
            pp=root/'protocol.json';pp.write_text(json.dumps(protocol));sha=p.base.digest(pp)
            with mock.patch.object(p.base,'EVIDENCE',out),mock.patch.object(p,'validate_inventory'),mock.patch.object(p,'committed_source',return_value=p.SOURCE),mock.patch.object(sys,'argv',['prepare','--diagnostic','--diagnostic-overlay',str(overlay),'--diagnostic-overlay-sha',p.base.digest(overlay),'--source-commit',p.SOURCE,'--protocol',str(pp),'--protocol-sha',sha]),mock.patch('builtins.print'):
                p.main()
            bundle=json.loads(next(out.glob('*-bundle.json')).read_text());body=bundle['block_python']
            self.assertEqual(len(list(out.iterdir())),10);self.assertNotIn('FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS',body)
            for entry in bundle['attempts']:
                command=json.loads(pathlib.Path(entry['command_path']).read_text());spec=json.loads(pathlib.Path(command['argv'][-3]).read_text())
                active=[item for item in spec['overlays'] if item['target'].endswith('/v1/worker/gpu_model_runner.py')];self.assertEqual(len(active),1)
                self.assertEqual(active[0]['source'],str(overlay));self.assertEqual(active[0]['sha256'],p.DIAGNOSTIC_OVERLAY_SHA)
                self.assertEqual(spec['code_pins'][str(overlay)],active[0]['sha256'])
                for request in spec['requests'].values():
                    mounted=[item for item in request['mounts'] if item['Target']==active[0]['target']];self.assertEqual(len(mounted),1);self.assertEqual(mounted[0]['Source'],active[0]['source'])
                for expected,actual in zip(spec['correctness']['requests'],spec['actual_request_parameters']):
                    self.assertEqual(expected['logprobs'],2);self.assertEqual(actual['logprobs'],2)
                    self.assertTrue(all(expected[key]==actual[key] for key in actual))
            block=root/'block';block.mkdir();execute,called,claims=ActualConsumerTest().block_fixture(block,fail_at=1,inline_code="x='--property=RemainAfterExit=yes'")
            bp=block/'bundle.json';b=json.loads(bp.read_text());b['status']='FROZEN_DIAGNOSTIC_OBSERVATION_NOT_FORMAL';bp.write_text(json.dumps(b))
            for entry in b['attempts']:
                cp=pathlib.Path(entry['command_path']);cmd=json.loads(cp.read_text());sp=pathlib.Path(cmd['argv'][-3]);spec=json.loads(sp.read_text());spec['status']=b['status'];sp.write_text(json.dumps(spec));cmd['argv'][-2]=cmd['spec_sha256']=p.base.digest(sp);cp.write_text(json.dumps(cmd));entry['command_sha256']=p.base.digest(cp)
            bp.write_text(json.dumps(b));real_write=pathlib.Path.write_text
            def write(path,value,*args,**kwargs):
                if path.name=='controller-final.json':value=value.replace('FORMAL_SINGLE_CONDITION_CORRECT_RELEASE60_PASS','DIAGNOSTIC_OBSERVATION_RELEASE60_COMPLETE_NOT_FORMAL')
                return real_write(path,value,*args,**kwargs)
            with mock.patch.object(p,'BLOCK_BODY',body),mock.patch.object(pathlib.Path,'write_text',write):
                with self.assertRaises(AssertionError):execute()
            self.assertEqual(len(called),2)
            final=json.loads((claims/'tinyblock-final.json').read_text());self.assertFalse(final['primary_sample_qualified']);self.assertIn('NOT_FORMAL',final['status']);self.assertEqual(final['attempts'][2]['status'],'NOT_STARTED')
            self.assertEqual(called[0][called[0].index('-c')+1],"x='--property=RemainAfterExit=yes'")
            with mock.patch.object(p,'BLOCK_BODY',body):
                with self.assertRaises(FileExistsError):execute()
            self.assertEqual(len(called),2)
            low=root/'low';low.mkdir();late,none,late_claims=ActualConsumerTest().block_fixture(low,expiry=28000)
            path=low/'bundle.json';value=json.loads(path.read_text());value['status']='FROZEN_DIAGNOSTIC_OBSERVATION_NOT_FORMAL';path.write_text(json.dumps(value))
            with mock.patch.object(p,'BLOCK_BODY',body):
                with self.assertRaises(AssertionError):late()
            self.assertEqual(none,[]);self.assertEqual(list(late_claims.iterdir()),[])

    def test_actual_trace_counter_and_diagnostic_complete_never_qualifies_formal(self):
        with tempfile.TemporaryDirectory() as directory:
            _,generated=self.generated(pathlib.Path(directory));spec,command=generated[0];body=command['argv'][command['argv'].index('-c')+1]
            node=next(n for n in ast.walk(ast.parse(body)) if isinstance(n,ast.Assert) and isinstance(n.test,ast.Compare) and 'collections.Counter' in ast.unparse(n.test))
            check=compile(ast.Module(body=[node],type_ignores=[]),'<actual-diag-trace-check>','exec')
            context={'collections':__import__('collections'),'json':json,'tokens':{'tokens':[[1,2],[1,3]]},'history':[[1,2],[1,3]]};exec(check,context)
            context['tokens']['tokens'][-1]=[1,99]
            context.update(life=1,index=2)
            with self.assertRaises(AssertionError):exec(check,context)
            namespace={'math':__import__('math'),'json':json};exec(p.DIAGNOSTIC_FUNCTIONS,namespace)
            namespace['diagnostic_observations'].update({str(i):{'gold_match':i!=2} for i in range(5)})
            metrics={str(i):{'raw_interval_status':'NUMERIC','stdout_reference':{}} for i in range(5)};gates={k:{'status':'PASS'} for k in ('cache','clock','ownership')};gates['gold']={'status':'FAIL'}
            result=namespace['diagnostic_qualification'](gates,{'normal':True},{'normal':True,'quiet_seconds':60},metrics)
            self.assertTrue(result['diagnostic_observation_complete']);self.assertFalse(result['primary_sample_qualified']);self.assertFalse(result['gold_matches']['2'])


    def test_simulated_hash_fixture_from_real_registered_metadata_only(self):
        import inspect
        reference=p.base.EVIDENCE/'kimi-fingerprint-validator-metadata-only-reference-20261107'
        if not reference.is_dir():self.skipTest('server-only historical metadata fixture absent')
        index_path=pathlib.Path(p.STORE_ARCHIVE)/'vllm/kimi-k2.6-sf-reference/rank_0/tensor_index.json'
        raw=index_path.read_bytes();self.assertLess(len(raw),4<<20);index=json.loads(raw)
        node=next(n for n in ast.walk(ast.parse(inspect.getsource(p.validate_diagnostic_fingerprint))) if isinstance(n,ast.FunctionDef) and n.name=='signature')
        namespace={};exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-signature>','exec'),namespace);signature=namespace['signature']
        for path in reference.glob('*rank0.json'):
            record=json.loads(path.read_text());metadata={t['name']:t for t in record['registered_tensors']}
            keys={signature(metadata[name]):name for name in index if metadata[name]['bytes']>0}
            persistent=[];runtime=[];aliases=[]
            for name,tensor in metadata.items():
                row={key:tensor[key] for key in ('name','kind','dtype','shape','stride')};row['numbytes']=tensor['bytes']
                # SIMULATED nonempty hashes: no tensor value is read or validated by this fixture.
                row['sha256']='a'*64 if tensor['bytes'] else p.hashlib.sha256(b'').hexdigest()
                if name in index:persistent.append(row)
                elif tensor['bytes'] and signature(tensor) in keys:row['alias_of']=keys[signature(tensor)];aliases.append(row)
                else:runtime.append(row)
            self.assertEqual((len(persistent),len(runtime),len(aliases)),(1912,123,727))
            runtime_ranges={signature(metadata[x['name']]) for x in runtime if x['numbytes']}
            self.assertEqual(len(runtime_ranges),2);self.assertEqual(sum(key[3] for key in runtime_ranges),67118080)
            ranges={signature(t) for t in metadata.values() if t['bytes']}
            record['diagnostic_value_fingerprint']={'schema':'kimi-load-boundary-value-fingerprint-v1','stage':'before_first_forward','status':'PASS','coverage_complete':True,'error_type':None,'error':None,'chunk_bytes':67108864,'budget_seconds':600,'elapsed_seconds':0,'index_path':'/store/vllm/kimi-k2.6-sf-reference/rank_0/tensor_index.json','index_sha256':p.hashlib.sha256(raw).hexdigest(),'persistent':persistent,'runtime_buffers':runtime,'aliases':aliases,'counts':{'index_keys':len(index),'registered_names':len(metadata),'persistent':len(persistent),'runtime_buffers':len(runtime),'aliases':len(aliases),'unique_hashed_ranges':len(ranges),'hashed_bytes':sum(key[3] for key in ranges),'chunks':sum((key[3]+67108863)//67108864 for key in ranges)}}
            p.validate_diagnostic_fingerprint(record,index,p.hashlib.sha256(raw).hexdigest())


class SFHotRepeatabilityTest(unittest.TestCase):
    def test_actual_single_case_main_four_producers_and_no_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);out=root/'out';out.mkdir()
            protocol=json.loads((p.base.EVIDENCE/'kimi-four-condition-10block-preregistration-local-nvme-v4-retry02-20261106.json').read_text());protocol['final_deploy_commit']=p.SOURCE;protocol['resource_budget']['runs_root']=str(root/'runs')
            pp=root/'reference.json';pp.write_text(json.dumps(protocol));sha=p.base.digest(pp)
            with mock.patch.object(p,'RUNS_ROOT',str(root/'runs')),mock.patch.object(p.base,'EVIDENCE',out),mock.patch.object(p,'validate_inventory'),mock.patch.object(p,'committed_source',return_value=p.SOURCE),mock.patch.object(sys,'argv',['prepare','--sfhot-repeatability','--source-commit',p.SOURCE,'--protocol',str(pp),'--protocol-sha',sha]),mock.patch('builtins.print'):
                p.main()
            self.assertEqual(len(list(out.iterdir())),2);self.assertEqual(list(out.glob('*-bundle.json')),[])
            spec=json.loads(next(out.glob('*-spec.json')).read_text());cmd=json.loads(next(out.glob('*-command.json')).read_text());body=cmd['argv'][cmd['argv'].index('-c')+1]
            self.assertEqual(spec['run_id'],'kimi-sfhot-repeatability-20261107-once');self.assertEqual(spec['condition'],'safetensors_disk_hot');self.assertFalse(spec['fingerprint_required']);self.assertIn('NOT_APPLICABLE',spec['fingerprint_scope']);self.assertFalse(spec['primary_sample_qualified'])
            self.assertEqual(spec['budgets']['full'],7200);self.assertIn("store_stop_end=time.monotonic()+60",body);self.assertNotIn('verify_diagnostic_records',body);self.assertNotIn('diagnostic_value_fingerprint',body)
            self.assertEqual([r['logprobs'] for r in spec['correctness']['requests']],[1,1,1,2])
            original=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA)
            for role,req in spec['requests'].items():
                self.assertNotIn('SLLM_DIAGNOSTIC_VALUE_FINGERPRINT',req['env']);self.assertEqual(req['env'].get('SLLM_CAPTURE_LOAD_EVIDENCE'),original['requests'][role]['env'].get('SLLM_CAPTURE_LOAD_EVIDENCE'))
                active=next(m for m in req['mounts'] if m['Target'].endswith('/v1/worker/gpu_model_runner.py'));self.assertEqual(active['Source'],str(p.base.NEW_RUNNER));self.assertEqual(spec['code_pins'][active['Source']],p.base.NEW_RUNNER_SHA)
            self.assertEqual(next(x for x in spec['overlays'] if x['target'].endswith('/v1/worker/gpu_model_runner.py'))['sha256'],p.base.NEW_RUNNER_SHA)
            clock=[100];calls=[];seen=[]
            def now():clock[0]+=1;return clock[0]
            class Response:
                status=200
                def __init__(self,value):self.value=value
                def __enter__(self):return self
                def __exit__(self,*args):return False
                def read(self,limit):return json.dumps(self.value).encode()
            def request(req,timeout):
                calls.append(req.full_url)
                if req.full_url.endswith('/register'):return Response({})
                payload=json.loads(req.data);index=len(seen);seen.append(payload);want=spec['correctness']['requests'][index];ids=want['expected']['token_ids'][:]
                text=want['expected']['content']
                if index==3:ids[11]=12635;text=text.replace('光学','物理').replace('\\u5149\\u5b66','\\u7269\\u7406')
                entries=[{str(token):{'rank':1,'logprob':-.1},**({str(token+1):{'rank':2,'logprob':-2.}} if payload['logprobs']==2 else {})} for token in ids]
                return Response({'choices':[{'message':{'content':text},'logprobs':entries,'finish_reason':want['expected']['finish_reason']}],'usage':{'prompt_tokens':len(want['input_tokens']),'completion_tokens':len(ids),'total_tokens':len(want['input_tokens'])+len(ids)}})
            ns={'json':json,'urllib':types.SimpleNamespace(request=types.SimpleNamespace(Request=urllib.request.Request,urlopen=request)),'time':types.SimpleNamespace(monotonic_ns=now),'HTTP_CLOCK':{},'HTTP_PID':1}
            exec(functions_from(spec['role_bodies']['http']),ns)
            nodes=ast.parse(body);saved={};observe={'math':__import__('math'),'json':json,'record_http_sample':lambda *a:None,'validated_http_events':lambda *a:{},'diagnostic_observations':{},'save':lambda name,value:saved.update({name:value}),'s':spec}
            selected=[n for n in nodes.body if isinstance(n,ast.FunctionDef) and n.name in ('diagnostic_response','diagnostic_qualification')];exec(compile(ast.Module(body=selected,type_ignores=[]),'<actual-repeat-response>','exec'),observe)
            loop=next(n for n in ast.walk(nodes) if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and [getattr(x,'id',None) for x in n.target.elts]==['index','want'] and any(isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='payload' for t in x.targets) for x in n.body))
            stmts=[n for n in loop.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='payload' for t in n.targets) or isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='payload']
            builder=compile(ast.Module(body=stmts,type_ignores=[]),'<actual-repeat-payload>','exec')
            for index,want in enumerate(spec['correctness']['requests']):
                context={'want':want,'index':index,'run':spec['run_id'],'s':spec};exec(builder,context);payload=context['payload']
                self.assertEqual(payload['logprobs'],[1,1,1,2][index]);self.assertTrue(all(payload[k]==spec['actual_request_parameters'][index][k] for k in spec['actual_request_parameters'][index] if k!='case'))
                row=ns['finish_http']('/v1/chat/completions',payload,30,want,spec['config'] if index==0 else None);observe['diagnostic_response'](row,want,index,1)
            self.assertEqual(len(calls),5);self.assertTrue(calls[0].endswith('/register'));self.assertEqual(len(seen),4)
            last=observe['diagnostic_observations']['fixed32-repeat1'];self.assertFalse(last['strict_gold_match']);self.assertTrue(last['known_fixed32_variant_match'])
            metrics={x['case']:{'raw_interval_status':'NUMERIC','stdout_reference':{}} for x in spec['correctness']['requests']};gates={k:{'status':'PASS'} for k in ('cache','clock','ownership')}
            result=observe['diagnostic_qualification'](gates,{'normal':True},{'normal':True,'quiet_seconds':60},metrics);self.assertTrue(result['diagnostic_observation_complete']);self.assertFalse(result['primary_sample_qualified'])

    def test_repeatability_used_run_rejected_full_mode_still_requires_capture(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);cmd=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        with tempfile.TemporaryDirectory() as directory:
            protocol['resource_budget']['runs_root']=directory;(pathlib.Path(directory)/'kimi-sfhot-repeatability-20261107-once').mkdir()
            with self.assertRaises(AssertionError):p.render_diagnostic(protocol,spec,cmd,spec['native_source_pins'],repeatability=True)
        overlay=p.base.ROOT/'sllm_store/build/kimi-vllm0191-diagnostic-values-v1/v1/worker/gpu_model_runner.py'
        full=p.render_diagnostic(protocol,spec,cmd,spec['native_source_pins'],overlay,p.DIAGNOSTIC_OVERLAY_SHA)
        self.assertEqual(len(full),4);self.assertTrue(all(s['fingerprint_required'] for s,_ in full));self.assertTrue(all("verify_diagnostic_records(records)" in c['argv'][c['argv'].index('-c')+1] for _,c in full))

class MilestoneAssertionMessagesTest(unittest.TestCase):
    def rendered(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA)
        command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        args=(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        with mock.patch.object(p,'milestone_assert_messages',side_effect=lambda body:body):
            old_spec,old=p.render(*args)
        new_spec,new=p.render(*args)
        return old_spec,old,new_spec,new

    def test_actual_render_changes_only_assert_messages(self):
        old_spec,old,new_spec,new=self.rendered()
        old_body=old['argv'][old['argv'].index('-c')+1];new_body=new['argv'][new['argv'].index('-c')+1]
        self.assertEqual(p.hashlib.sha256(old_body.replace("+(['--head-control-only'] if role=='head' else [])",'').encode()).hexdigest(),'6ab0b94e38829447746d43e33cc2bc7f62f7abe47ec1283f0b1e76027905d571')
        trees=[ast.parse(old_body),ast.parse(new_body)]
        changes=[]
        for before,after in zip([n for n in ast.walk(trees[0]) if isinstance(n,ast.Assert)],[n for n in ast.walk(trees[1]) if isinstance(n,ast.Assert)]):
            if ast.dump(before)!=ast.dump(after):
                self.assertEqual(ast.dump(before.test),ast.dump(after.test));self.assertIsNone(before.msg);self.assertIsNotNone(after.msg);changes.append(after)
        self.assertGreaterEqual(len(changes),13)
        for tree in trees:
            for node in ast.walk(tree):
                if isinstance(node,ast.Assert):node.msg=None
        self.assertEqual(ast.dump(trees[0]),ast.dump(trees[1]))
        self.assertEqual(old_spec,new_spec);self.assertEqual(old_spec['role_bodies'],new_spec['role_bodies'])
        old['argv'][old['argv'].index('-c')+1]=new_body;old['root_python_sha256']=new['root_python_sha256']
        self.assertEqual(old,new)

    def test_actual_generated_failed_assertions_have_labels_and_actual_values(self):
        _,_,_,command=self.rendered();body=command['argv'][command['argv'].index('-c')+1]
        examples=[
            ('guard release',{'role':'worker','result':{'status':'FAILED','failures':['abort_requested'],'quiet_seconds':2}}),
            ('observer release',{'observed':{'quiet_seconds':2,'gaps':3}}),
            ('unit exit metadata',{'u':'example.service','p':{'Result':'success','ExecMainCode':'0','ExecMainStatus':'0'}}),
            ('normal_exit role',{'role':'worker','row':{'State':{'Running':False}}}),
            ('expected exit0/Pid0/noOOM/restart0',{'role':'worker','row':{'State':{'ExitCode':1,'OOMKilled':False,'Pid':0},'RestartCount':0,'Config':{'Env':['DO_NOT_PRINT']}}}),
            ('token history',{'life':1,'index':2,'tokens':{'tokens':[[1,2]]},'history':[[1,3]],'collections':__import__('collections'),'json':json}),
            ('pinned identity release',{'role':'worker','owner':{'pid':123,'startticks':456},'gone':lambda owner:False}),
        ]
        assertions=[node for node in ast.walk(ast.parse(body)) if isinstance(node,ast.Assert) and node.msg is not None]
        for label,context in examples:
            node=next(node for node in assertions if label in ast.unparse(node.msg))
            with self.subTest(label=label),self.assertRaises(AssertionError) as raised:
                exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-generated-assert>','exec'),context)
            self.assertIn(label,str(raised.exception));self.assertGreater(len(str(raised.exception)),len(label)+5)
            self.assertNotIn('DO_NOT_PRINT',str(raised.exception))
        with self.assertRaisesRegex(AssertionError,'template drift'):
            p.milestone_assert_messages('assert True')


class SampledLogprobsParserTest(unittest.TestCase):
    def actual_consumer(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA)
        command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        with tempfile.TemporaryDirectory() as directory:
            protocol['resource_budget']['runs_root']=directory
            spec,command=p.render_diagnostic(protocol,spec,command,spec['native_source_pins'],repeatability=True)[0]
        body=command['argv'][command['argv'].index('-c')+1];tree=ast.parse(body)
        selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='diagnostic_response']
        namespace={'math':__import__('math'),'record_http_sample':lambda *args:None,'validated_http_events':lambda *args:{},'save':lambda *args:None,'diagnostic_observations':{}}
        exec(compile(ast.Module(body=selected,type_ignores=[]),'<actual-sampled-consumer>','exec'),namespace)
        return spec,tree,namespace

    def test_actual_four_responses_sampled_ids_match_independent_trace(self):
        contract=p.base.EVIDENCE/'kimi-sfhot-logprobs-sampled-token-contract-readonly-review-20261107.json'
        facts=json.loads(contract.read_text());spec,tree,namespace=self.actual_consumer();history=[]
        for index,(source,want) in enumerate(zip(facts['actual_observations'],spec['correctness']['requests'])):
            path=pathlib.Path(source['raw_http']);self.assertEqual(p.base.digest(path),source['raw_http_sha256'])
            row=json.loads(path.read_text());namespace['diagnostic_response'](row,want,index,1)
            ids=namespace['diagnostic_observations'][want['case']]['actual_ids']
            self.assertEqual(ids,source['sampled_first_ids']);history.append(want['input_tokens']+ids)
        final=namespace['diagnostic_observations']['fixed32-repeat1']
        self.assertEqual(final['actual_ids'][-1],1852);self.assertFalse(final['strict_gold_match'])
        last=json.loads(pathlib.Path(facts['actual_observations'][-1]['raw_http']).read_text())['response']['choices'][0]['logprobs'][-1]
        self.assertEqual(last['1852']['rank'],2);self.assertEqual(last['1852']['logprob'],last['119304']['logprob'])
        trace_path=pathlib.Path(facts['trace']['path']);self.assertEqual(p.base.digest(trace_path),facts['trace']['sha256']);trace=json.loads(trace_path.read_text())
        node=next(node for node in ast.walk(tree) if isinstance(node,ast.Assert) and 'collections.Counter' in ast.unparse(node.test))
        check=compile(ast.Module(body=[node],type_ignores=[]),'<actual-cumulative-trace>','exec')
        context={'collections':__import__('collections'),'json':json,'tokens':trace,'history':history,'life':1,'index':3};exec(check,context)
        wrong=copy.deepcopy(history);wrong[-1][-1]=119304;context['history']=wrong
        with self.assertRaisesRegex(AssertionError,'token history'):exec(check,context)

    def test_sampled_outside_topk_and_malformed_metadata(self):
        _,_,namespace=self.actual_consumer()
        want={'case':'arithmetic','input_tokens':[9],'logprobs':2,'max_tokens':128,'expected':{'token_ids':[1852],'content':'red','finish_reason':'stop'}}
        row={'status':200,'request_id':'sampled-outside','response':{'choices':[{'message':{'content':'red'},'finish_reason':'stop','logprobs':[{'1852':{'rank':9,'logprob':-3.},'119304':{'rank':1,'logprob':-.1},'42':{'rank':2,'logprob':-.2}}]}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}}
        namespace['diagnostic_response'](row,want,0,1);self.assertEqual(namespace['diagnostic_observations']['arithmetic']['actual_ids'],[1852])
        bad_entries=[{}, {'1':{'rank':1,'logprob':-.1}}, {'-1':{'rank':1,'logprob':-.1},'2':{'rank':2,'logprob':-.2}}, {'01':{'rank':1,'logprob':-.1},'2':{'rank':2,'logprob':-.2}}, {'1':{'rank':True,'logprob':-.1},'2':{'rank':2,'logprob':-.2}}, {'1':{'rank':0,'logprob':-.1},'2':{'rank':2,'logprob':-.2}}, {'1':{'rank':1,'logprob':float('nan')},'2':{'rank':2,'logprob':-.2}}, {'1':{'rank':1},'2':{'rank':2,'logprob':-.2}}, {'1':{'rank':1,'logprob':'-.1'},'2':{'rank':2,'logprob':-.2}}]
        for entry in bad_entries:
            with self.subTest(entry=entry),self.assertRaises(AssertionError):
                bad=copy.deepcopy(row);bad['response']['choices'][0]['logprobs']=[entry];namespace['diagnostic_response'](bad,want,0,1)


class Fixed32LengthOnlyTest(unittest.TestCase):
    def consumers(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA)
        for row in protocol['correctness']['requests']:row['logprobs']=1
        protocol['correctness']['requests'][2]['validation_mode']='fixed32_length_only_v1'
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        spec,command=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        value={};clock=[100]
        def now():clock[0]+=1;return clock[0]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,limit):return json.dumps(value['response']).encode()
        client={'json':json,'math':__import__('math'),'urllib':types.SimpleNamespace(request=types.SimpleNamespace(Request=urllib.request.Request,urlopen=lambda *args,**kwargs:Response())),'time':types.SimpleNamespace(monotonic_ns=now),'HTTP_CLOCK':{},'HTTP_PID':1}
        exec(functions_from(spec['role_bodies']['http']),client)
        body=command['argv'][command['argv'].index('-c')+1];tree=ast.parse(body);saved={}
        parent={'json':json,'math':__import__('math'),'record_http_sample':lambda *args:None,'validated_http_events':lambda *args:{},'mark_sample_gate':lambda *args:None,'save':lambda name,row:saved.update({name:row})}
        selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('fixed32_observation','check_response')]
        exec(compile(ast.Module(body=selected,type_ignores=[]),'<actual-length-parent>','exec'),parent)
        return protocol,spec,tree,value,client,parent,saved

    def response(self,want,ids=None,text=None,finish=None):
        expected=want['expected'];ids=expected['token_ids'][:] if ids is None else ids
        return {'choices':[{'message':{'content':expected['content'] if text is None else text},'finish_reason':expected['finish_reason'] if finish is None else finish,'logprobs':[{str(token):{'rank':1,'logprob':-.1}} for token in ids]}],'usage':{'prompt_tokens':expected['prompt_tokens'],'completion_tokens':len(ids),'total_tokens':expected['prompt_tokens']+len(ids)}}

    def test_actual_http_parent_and_history_accept_length_and_report_strict_differences(self):
        protocol,spec,tree,value,client,parent,saved=self.consumers();history=[];tokens=[]
        self.assertEqual(spec['code_pins'][str(p.base.ROOT/'sllm_store/vllm_patch/sllm_load.patch')],p.DIAGNOSTIC_PATCH_SHA)
        self.assertEqual(next(row['sha256'] for row in spec['overlays'] if row['target'].endswith('/v1/worker/gpu_model_runner.py')),p.base.NEW_RUNNER_SHA)
        self.assertTrue(all('SLLM_DIAGNOSTIC_VALUE_FINGERPRINT' not in request['env'] for request in spec['requests'].values()))
        history_append=next(node for node in ast.walk(tree) if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and isinstance(node.value.func.value,ast.Name) and node.value.func.value.id=='history' and node.value.func.attr=='append')
        counter=next(node for node in ast.walk(tree) if isinstance(node,ast.Assert) and 'collections.Counter' in ast.unparse(node.test))
        context={'history':history,'collections':__import__('collections'),'json':json,'life':1}
        for index,want in enumerate(spec['correctness']['requests']):
            ids=want['expected']['token_ids'][:]
            if index==2:ids[11]=12635;ids[-1]=1852
            value['response']=self.response(want,ids,'different text' if index==2 else None)
            row=client['finish_http']('/v1/chat/completions',{'request_id':'length-'+str(index)},30,want)
            self.assertTrue(row['child_gold_validation_pass']);parent['check_response'](row,want,index,1)
            context.update(want=want,response=row,index=index);exec(compile(ast.Module(body=[history_append],type_ignores=[]),'<actual-new-history>','exec'),context)
            tokens.append(want['input_tokens']+ids);context['tokens']={'tokens':tokens};exec(compile(ast.Module(body=[counter],type_ignores=[]),'<actual-new-trace>','exec'),context)
        observation=saved['life1-response2.json']['acceptance_observation']
        self.assertTrue(observation['accepted']);self.assertFalse(observation['strict_reference_match']);self.assertFalse(observation['strict_reference_details']['text']);self.assertFalse(observation['strict_reference_details']['token_ids']);self.assertTrue(observation['strict_reference_details']['finish_reason'])
        self.assertLess(row['received_ns'],row['child_gold_validation_begin_ns'])
        context['tokens']=copy.deepcopy(context['tokens']);context['tokens']['tokens'][-1][-1]=999
        with self.assertRaisesRegex(AssertionError,'token history'):exec(compile(ast.Module(body=[counter],type_ignores=[]),'<actual-new-trace>','exec'),context)
        self.assertIn('fixed32 length-only accepted',ast.unparse(tree))

    def test_actual_http_and_parent_reject_invalid_length_finish_usage_and_business(self):
        _,spec,_,value,client,parent,_=self.consumers();want=spec['correctness']['requests'][2]
        for count,finish,bad_usage in ((31,'length',False),(33,'length',False),(32,'stop',False),(32,'length',True)):
            ids=(want['expected']['token_ids']+[2])[:count];response=self.response(want,ids,finish=finish)
            if bad_usage:response['usage']['total_tokens']=57
            value['response']=response;row=client['finish_http']('/v1/chat/completions',{'request_id':'invalid'},30,want)
            with self.subTest(count=count,finish=finish,bad_usage=bad_usage):
                self.assertFalse(row['child_gold_validation_pass'])
                with self.assertRaises(AssertionError):parent['check_response'](row,want,2,1)
        for index,want in enumerate(spec['correctness']['requests']):
            strict=copy.deepcopy(want);strict.pop('validation_mode',None);value['response']=self.response(strict,text='wrong')
            row=client['finish_http']('/v1/chat/completions',{'request_id':'strict'},30,strict)
            self.assertFalse(row['child_gold_validation_pass'])
            with self.assertRaises(AssertionError):parent['check_response'](row,strict,index,1)

    def test_frozen_mode_exception_and_only_b01_restart_mapping(self):
        protocol=json.loads((p.base.EVIDENCE/'kimi-four-condition-10block-preregistration-local-nvme-v4-retry02-20261106.json').read_text());protocol['final_deploy_commit']=p.SOURCE
        protocol['correctness']['requests'][2]['validation_mode']='fixed32_length_only_v1';protocol['correctness']['token_validation']=p.LENGTH_ONLY_TOKEN_VALIDATION;p.validate_frozen_protocol(protocol,p.SOURCE)
        bad=copy.deepcopy(protocol);bad['correctness']['requests'][0]['validation_mode']='fixed32_length_only_v1'
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)
        first=[row for row in protocol['attempts'] if row['block']==1]
        restart={'criterion':'fixed32_length_only_v1','suffix':'retry03','prior_formal_launches':4,'target_total_launch_requests_max':44,'retained_retry02_runs':[{'run_id':row['attempt_id']+'-retry02','primary_sample_qualified':qualified,'result_path':'/evidence/retained-'+str(index)+'.json','result_sha256':'a'*64} for index,(row,qualified) in enumerate(zip(first[:2],(True,False)))],'preserved_unstarted2':[row['attempt_id']+'-retry02' for row in first[2:]]}
        revision=protocol['execution_revision'];revision['length_only_b01_restart']=restart;revision['actual_launch_requests_at_authorization']=4;revision['target_total_launch_requests_max']=44
        for row in first:revision['run_id_map'][row['attempt_id']]=row['attempt_id']+'-retry03'
        revision['block_run_ids']['1']='kimi-four-v1-b01-retry03';p.validate_frozen_protocol(protocol,p.SOURCE)
        runs,blocks=p.execution_mapping(protocol);self.assertEqual(blocks['1'],'kimi-four-v1-b01-retry03');self.assertEqual(blocks['2'],'kimi-four-v1-b02-retry02')
        self.assertTrue(all(runs[row['attempt_id']]==row['attempt_id']+('-retry03' if row['block']==1 else '-retry02' if row['block']==2 else '') for row in protocol['attempts']))
        bad=copy.deepcopy(protocol);bad['correctness']['requests'][2].pop('validation_mode')
        with self.assertRaises(AssertionError):p.execution_mapping(bad)
        for key,wrong in (('suffix','retry04'),('prior_formal_launches',3),('target_total_launch_requests_max',45)):
            bad=copy.deepcopy(protocol);bad['execution_revision']['length_only_b01_restart'][key]=wrong
            with self.subTest(key=key),self.assertRaises(AssertionError):p.execution_mapping(bad)
        bad=copy.deepcopy(protocol);bad['execution_revision']['actual_launch_requests_at_authorization']=2
        with self.assertRaises(AssertionError):p.execution_mapping(bad)
        bad=copy.deepcopy(protocol);bad['correctness']['token_validation']='all gold must match'
        with self.assertRaises(AssertionError):p.validate_frozen_protocol(bad,p.SOURCE)
        revision['normal_guard_b01_restart']={'suffix':'retry04','criterion':'fixed32_length_only_v1','prior_formal_launches':5,'target_total_launch_requests_max':45,'retained_retry03_run':{'run_id':first[0]['attempt_id']+'-retry03','result_path':'/evidence/retained-retry03.json','result_sha256':'b'*64,'primary_sample_qualified':False},'preserved_unstarted3':[row['attempt_id']+'-retry03' for row in first[1:]]}
        revision['actual_launch_requests_at_authorization']=5;revision['target_total_launch_requests_max']=45
        for row in first:revision['run_id_map'][row['attempt_id']]=row['attempt_id']+'-retry04'
        revision['block_run_ids']['1']='kimi-four-v1-b01-retry04'
        runs,blocks=p.execution_mapping(protocol);self.assertEqual(blocks['1'],'kimi-four-v1-b01-retry04');self.assertEqual(blocks['2'],'kimi-four-v1-b02-retry02')
        self.assertTrue(all(runs[row['attempt_id']]==row['attempt_id']+('-retry04' if row['block']==1 else '-retry02' if row['block']==2 else '') for row in protocol['attempts']))
        bad=copy.deepcopy(protocol);bad['execution_revision'].pop('length_only_b01_restart')
        with self.assertRaises(AssertionError):p.execution_mapping(bad)
        bad=copy.deepcopy(protocol);bad['execution_revision']['normal_guard_b01_restart']['retained_retry03_run']['primary_sample_qualified']=True
        with self.assertRaises(AssertionError):p.execution_mapping(bad)
        bad=copy.deepcopy(protocol);bad['execution_revision']['normal_guard_b01_restart']['suffix']='retry05'
        with self.assertRaises(AssertionError):p.execution_mapping(bad)
        spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        with tempfile.TemporaryDirectory() as directory:
            protocol['resource_budget']['runs_root']=directory;(pathlib.Path(directory)/runs[first[0]['attempt_id']]).mkdir()
            with self.assertRaisesRegex(AssertionError,'identity already used'):p.render(protocol,spec,command,first[0]['attempt_id'],spec['native_source_pins'])


class HeadGuardActualArgvTest(unittest.TestCase):
    def test_actual_rendered_guard_start_flag_is_head_only(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        protocol['head_control_guard_policy']={'mode':'unknown_host_permission_warning_head_only_v1'}
        spec,command=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        body=command['argv'][command['argv'].index('-c')+1];fn=next(node for node in ast.parse(body).body if isinstance(node,ast.FunctionDef) and node.name=='create_arm_start')
        call=next(node for node in ast.walk(fn) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='systemd')
        for role in ('head','worker'):
            captured=[];ns={'role':role,'cid':'c'*64,'gb':pathlib.Path('/evidence')/('guard-'+role),'guard':pathlib.Path(spec['guard']),'unit':'guard.service','b':spec['budgets'][role+'_guard'],'systemd':lambda unit,argv,native:captured.append(argv)}
            execute=compile(ast.Expression(body=call),'<actual-guard-systemd>','eval');eval(execute,ns)
            self.assertEqual(captured[0].count('--head-control-only'),1 if role=='head' else 0)
            self.assertIn('--normal-exit-marker',captured[0])
        self.assertEqual(p.base.digest(pathlib.Path(spec['guard'])),spec['guard_sha256'])


    def test_policy_optin_requires_cpu_head_and_absent_policy_preserves_strict_body(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        _,strict=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        self.assertNotIn('--head-control-only',strict['argv'][strict['argv'].index('-c')+1])
        for kind in ('unknown_policy','visible_gpu','device_request'):
            changed=copy.deepcopy(spec);candidate=copy.deepcopy(protocol);candidate['head_control_guard_policy']={'mode':'unknown_host_permission_warning_head_only_v1'}
            if kind=='unknown_policy':candidate['head_control_guard_policy']['mode']='anything'
            elif kind=='visible_gpu':changed['requests']['head']['env']['NVIDIA_VISIBLE_DEVICES']='all'
            else:changed['requests']['head']['host_config']['DeviceRequests']=[{'Count':8}]
            with self.subTest(kind=kind),self.assertRaises(AssertionError):p.render(candidate,changed,command,candidate['attempts'][0]['attempt_id'],changed['native_source_pins'])


class CommittedSourceOfficialApiTest(unittest.TestCase):
    def gate(self, failure=None):
        commit='a'*40;branch='feature/test';calls=[];credential='Basic CPU_SECRET_SENTINEL'
        def git(argv,cwd):
            calls.append(argv)
            if argv[1]=='rev-parse':return (commit+'\n').encode()
            if argv[1]=='status':return b''
            if argv[1]=='symbolic-ref':return (branch+'\n').encode()
            if argv[1]=='remote':return ('https://github.com/'+('other/repository.git' if failure=='origin' else 'BoyJiang2/serverlessllm.git')+'\n').encode()
            if argv[1]=='config':
                self.assertEqual(argv[2:5],['--get-urlmatch','http.extraheader','https://github.com/BoyJiang2/serverlessllm.git'])
                return b'' if failure=='credential' else ('Authorization: '+credential+'\n').encode()
            if argv[1]=='show':return (p.base.ROOT/argv[2].split(':',1)[1]).read_bytes()
            raise AssertionError('unexpected Git operation')
        expected_url='https://api.github.com/repos/BoyJiang2/serverlessllm/git/ref/heads/feature%2Ftest'
        value={'ref':'refs/heads/'+branch,'object':{'type':'commit','sha':commit}}
        if failure=='ref':value['ref']='refs/heads/other'
        if failure=='sha':value['object']['sha']='b'*40
        if failure=='sha_shape':value['object']['sha']='a'*39
        if failure=='type':value['object']['type']='tag'
        raw=b'x'*65537 if failure=='size' else json.dumps(value).encode()
        test=self
        class Response:
            status=503 if failure=='status' else 200
            def geturl(self):return 'https://elsewhere.invalid/' if failure=='redirect' else expected_url
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,limit):test.assertEqual(limit,65537);return raw
        def get(request,timeout):
            self.assertEqual(request.full_url,expected_url);self.assertEqual(timeout,20)
            self.assertEqual(request.get_header('Authorization'),credential)
            self.assertEqual(request.get_header('X-github-api-version'),'2022-11-28')
            self.assertEqual(request.get_header('Cache-control'),'no-cache');self.assertEqual(request.get_method(),'GET')
            if failure=='timeout':raise TimeoutError('CPU forced network timeout')
            return Response()
        with mock.patch.object(p.subprocess,'check_output',side_effect=git),mock.patch.object(p.urllib.request,'urlopen',side_effect=get) as api,mock.patch('builtins.print') as output:
            if failure is None:self.assertEqual(p.committed_source(commit,{'sllm/logger.py'}),commit)
            else:
                with self.assertRaises((AssertionError,TimeoutError)) as error:p.committed_source(commit,{'sllm/logger.py'})
                self.assertNotIn(credential,str(error.exception))
            output.assert_not_called()
            self.assertEqual(api.call_count,0 if failure in ('origin','credential') else 1)
        self.assertFalse(any(argv[1]=='ls-remote' for argv in calls))
        return calls

    def test_actual_gate_exact_official_api_ref_and_local_declared_bytes(self):
        calls=self.gate();self.assertTrue(any(argv[1]=='show' for argv in calls))

    def test_actual_stdlib_redirect_does_not_forward_api_authorization(self):
        captured=[];original=p.urllib.request.Request
        def capture(*args,**kwargs):
            request=original(*args,**kwargs);captured.append(request);return request
        with mock.patch.object(p.urllib.request,'Request',side_effect=capture):self.gate()
        request=captured[0];self.assertEqual(request.get_header('Authorization'),'Basic CPU_SECRET_SENTINEL')
        redirected=p.urllib.request.HTTPRedirectHandler().redirect_request(request,None,302,'redirect',{},'https://elsewhere.invalid/destination')
        self.assertIsNone(redirected.get_header('Authorization'));self.assertEqual(redirected.get_header('Accept'),'application/vnd.github+json')

    def test_actual_gate_api_failures_stop_without_credential_output(self):
        for failure in ('ref','sha','sha_shape','type','timeout','size','status','redirect','origin','credential'):
            with self.subTest(failure=failure):self.gate(failure)


class FormalGuardFailureVisibilityTest(unittest.TestCase):
    def test_actual_healthy_sees_existing_abort_while_guard_unit_is_alive(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        spec,command=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins'])
        self.assertEqual(spec['guard_sha256'],p.FORMAL_GUARD_SHA)
        self.assertEqual(spec['code_pins'][spec['guard']],p.FORMAL_GUARD_SHA)
        self.assertEqual(p.base.digest(pathlib.Path(spec['guard'])),p.FORMAL_GUARD_SHA)
        body=command['argv'][command['argv'].index('-c')+1];node=next(node for node in ast.parse(body).body if isinstance(node,ast.FunctionDef) and node.name=='healthy')
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);(root/'guard-head').mkdir();(root/'observer').mkdir()
            namespace={'time':types.SimpleNamespace(monotonic=lambda:1),'end':100,'arms':{'head':{}},'units':{'head':'head.service','observer':'observer.service'},'base':root,'props':lambda unit:{'MainPID':'999'},'cids':{}}
            exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-formal-healthy>','exec'),namespace)
            namespace['healthy']()
            for path in (root/'guard-head/ABORT',root/'observer/ABORT'):
                path.touch()
                with self.assertRaises(AssertionError):namespace['healthy']()
                path.unlink()


class CacheConstructionFailureEvidenceTest(unittest.TestCase):
    def construct(self, directory, result, fail_health=False):
        events=[];group={'root':'/tiny-root','files':[{'path':'/tiny-root/file'}]}
        def action(*args,**kwargs):events.append('action');return result
        def save(name,value):events.append('save:'+name);(pathlib.Path(directory)/name).write_text(json.dumps(value))
        def frozen():events.append('frozen_files');return group
        def healthy():
            events.append('healthy')
            if fail_health:raise RuntimeError('guard marker detected')
        def query(*args,**kwargs):raise AssertionError('query must not run after failed construction/health')
        namespace={'s':{'condition':'safetensors_disk_cold'},'time':types.SimpleNamespace(monotonic=lambda:100),'cache':types.SimpleNamespace(evict_file_cache=action,warm_file_cache=action,query_cache_state=query),'save':save}
        exec(p.CACHE_CODE,namespace);namespace.update(frozen_files=frozen,healthy=healthy,event=lambda *args:None)
        return namespace['construct_cache'],events

    def test_actual_construction_rejection_saves_raw_return_before_assert_and_stops(self):
        result={'operation':'evict','pages':10,'resident_pages':1,'admitted':False,'files':[{'path':'/tiny-root/file','resident_pages':1}]}
        with tempfile.TemporaryDirectory() as directory:
            construct,events=self.construct(directory,result)
            with self.assertRaisesRegex(AssertionError,'condition=safetensors_disk_cold.*operation=.*evict.*resident_pages=1.*pages=10'):
                construct()
            self.assertEqual(json.loads((pathlib.Path(directory)/'cache-construction-result.json').read_text()),result)
            self.assertEqual(events,['frozen_files','action','save:cache-construction-result.json','healthy'])

    def test_actual_health_failure_still_has_original_construction_return(self):
        result={'operation':'evict','pages':10,'resident_pages':0,'admitted':True,'files':[]}
        with tempfile.TemporaryDirectory() as directory:
            construct,events=self.construct(directory,result,fail_health=True)
            with self.assertRaisesRegex(RuntimeError,'guard marker detected'):construct()
            self.assertEqual(json.loads((pathlib.Path(directory)/'cache-construction-result.json').read_text()),result)
            self.assertEqual(events,['frozen_files','action','save:cache-construction-result.json','healthy'])

    def test_actual_generated_failure_receipt_preserves_traceback_and_line(self):
        protocol=p.checked(p.PROTOCOL,p.PROTOCOL_SHA);spec=p.checked(p.base.EXIT60_BASE_SPEC,p.base.EXIT60_BASE_SPEC_SHA);command=p.checked(p.base.EXIT60_BASE_COMMAND,p.base.EXIT60_BASE_COMMAND_SHA)
        _,command=p.render(protocol,spec,command,protocol['attempts'][0]['attempt_id'],spec['native_source_pins']);body=command['argv'][command['argv'].index('-c')+1]
        handler=next(node for node in ast.walk(ast.parse(body)) if isinstance(node,ast.ExceptHandler) and node.name=='error' and any(isinstance(n,ast.Constant) and n.value=='controller-failed.json' for n in ast.walk(node)))
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);saved={}
            for name in ('guard-head','guard-worker','observer'):(root/name).mkdir()
            ns={'sample_active_gate':None,'cleanup_started':False,'finalize_sample_evidence':lambda *args:None,'save':lambda name,value:saved.update({name:value}),'base':root,'s':{'source_commit':'a'*40,'protocol_sha256':'b'*64,'attempt':{},'condition':'safetensors_disk_cold','planned_attempts':[]},'cids':{},'units':{'head':'head.service'},'props':lambda *args:{'MainPID':'0'},'time':types.SimpleNamespace(monotonic=lambda:1),'end':100,'started':0}
            failing={};exec(compile("def frozen_files():\n raise RuntimeError('asset identity fixture')\n",'<frozen-files-fixture>','exec'),failing)
            try:failing['frozen_files']()
            except RuntimeError as original:
                ns['error']=original
                with self.assertRaises(RuntimeError) as raised:exec(compile(ast.Module(body=handler.body,type_ignores=[]),'<actual-controller-except>','exec'),ns)
                self.assertIs(raised.exception,original)
            receipt=saved['controller-failed.json'];self.assertIn('asset identity fixture',receipt['traceback']);self.assertIn('<frozen-files-fixture>',receipt['traceback'])
            self.assertEqual(receipt['traceback_frames'][-1],{'filename':'<frozen-files-fixture>','lineno':2,'name':'frozen_files'})
            self.assertTrue(all((root/name/'ABORT').exists() for name in ('guard-head','guard-worker','observer')))


if __name__=='__main__':
    unittest.main()
