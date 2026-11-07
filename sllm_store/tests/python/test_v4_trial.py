"""Local HTTP, synthetic Ray and journal integration; no GPU or model payload."""
import asyncio
import base64
import copy
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import tarfile
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from unittest import mock

import test_v4_attempts as fixture
from test_v4_cache_evidence import scan
import v4_trial as trial


class TrialContracts(unittest.TestCase):
    def test_paired_requests_reject_recreated_engine_and_intervening_unload(self):
        from v4_evidence import validate_single_engine_requests
        rows=[]
        identity=dict(container_hostname='cpu',pid=11,process_start_ticks=22)
        for name,when,request in [('backend_engine_create_begin',1,None),('backend_engine_ready',2,None),
                ('backend_first_token_observed',3,'business'),('backend_generation_complete',4,'business'),
                ('backend_first_token_observed',6,'workload32'),('backend_generation_complete',7,'workload32')]:
            rows.append(dict(identity,name=name,monotonic_ns=when,request_id=request))
        self.assertTrue(validate_single_engine_requests(rows,['business','workload32'])['single_engine_warm32'])
        for kind in ['second_engine','different_process','pid_reuse','unload','wrong_order']:
            changed=copy.deepcopy(rows)
            if kind=='second_engine':changed.append(dict(changed[0],pid=99,monotonic_ns=5))
            elif kind=='different_process':changed[-1]['pid']=99
            elif kind=='pid_reuse':changed[-1]['process_start_ticks']=999
            elif kind=='unload':changed.append(dict(identity,name='backend_shutdown_begin',monotonic_ns=5))
            else:changed[-2]['monotonic_ns']=3
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                validate_single_engine_requests(changed,['business','workload32'])

    def test_candidate_plans_all_conditions_and_cannot_claim_a_formal_id(self):
        path=trial.ROOT/'docs/evidence/v4-trial-entry/protocol-candidate.json'
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        for condition in trial.CONDITIONS:
            spec=trial.trial_spec(path,sha,condition,'v4n40_pilot_cpu')
            self.assertFalse(spec['formal_id_consumed'])
            self.assertEqual(spec['pool_bytes'],33554432 if condition.startswith('safetensors_') else 890937278464)
        protocol=json.loads(path.read_bytes());row=protocol['attempts'][0]
        with self.assertRaisesRegex(ValueError,'not frozen'):
            trial.trial_spec(path,sha,row['condition'],row['attempt_id'],row['attempt_id'])
        for field,value in (('max_model_len',4096),('head_cpu',4.0)):
            with tempfile.TemporaryDirectory() as directory:
                changed=copy.deepcopy(protocol);changed[field]=value;p=Path(directory)/'protocol.json'
                p.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    trial.trial_spec(p,hashlib.sha256(p.read_bytes()).hexdigest(),row['condition'],'v4n40_pilot_cpu')

    def test_pilot_amendment_changes_only_native_keep_alive_and_formal_requires_validation(self):
        path=trial.ROOT/'docs/evidence/v4-formal-4x10/protocol.json';sha=hashlib.sha256(path.read_bytes()).hexdigest()
        draft=dict(schema=1,frozen=False,base_preregistration_sha256=sha,keep_alive_seconds=3600)
        with tempfile.TemporaryDirectory() as directory:
            amendment=Path(directory)/'cpu-amendment.json';amendment.write_text(json.dumps(draft))
            digest=hashlib.sha256(amendment.read_bytes()).hexdigest()
            spec=trial.trial_spec(path,sha,'store_pool_hit','v4n40_pilot_cpu',amendment_path=amendment,amendment_sha=digest)
            self.assertEqual(spec['keep_alive_seconds'],3600)
            config=dict(model='cpu',auto_scaling_config={'min_instances':0,'max_instances':1},backend_config={})
            kept=trial.target_registration(config,'store_pool_hit',3600)
            self.assertEqual(kept['auto_scaling_config'],dict(min_instances=0,max_instances=1,keep_alive=3600))
            self.assertNotIn('keep_alive',config['auto_scaling_config'])
            with self.assertRaisesRegex(ValueError,'frozen warm-engine'):
                trial.trial_spec(path,sha,'store_pool_hit','v4n40_store_pool_hit_01','v4n40_store_pool_hit_01',amendment,digest)
            amendment.write_text(json.dumps(dict(draft,keep_alive_seconds=1)))
            with self.assertRaises(ValueError):
                trial.trial_spec(path,sha,'store_pool_hit','v4n40_pilot_cpu',amendment_path=amendment,amendment_sha=hashlib.sha256(amendment.read_bytes()).hexdigest())

    def test_http_error_and_invalid_json_keep_original_bytes_before_validation(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body=b'\xfforiginal failure' if self.path=='/error' else b'not json'
                self.send_response(500 if self.path=='/error' else 200)
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                for route in ('/error','/json'):
                    path=Path(directory)/(route[1:]+'.json')
                    with self.assertRaises((ValueError,json.JSONDecodeError)):
                        trial.post_raw(f'http://127.0.0.1:{server.server_port}',route,{},path,
                                       time.monotonic_ns()+30_000_000_000)
                    raw=json.loads(path.read_bytes())
                    self.assertGreaterEqual(raw['received_ns'],raw['sent_ns'])
                    expected=b'\xfforiginal failure' if route=='/error' else b'not json'
                    self.assertEqual(base64.b64decode(raw['body_base64']),expected)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_sf_reclaims_only_frozen_store_inputs_before_counterpart_and_target(self):
        inventory={'store_inputs':{'/frozen/store':{'bytes':1}},'safetensors_inputs':{'/frozen/sf':{'bytes':1}}}
        for condition in trial.CONDITIONS:
            with mock.patch.object(trial,'checked_json',return_value=inventory),mock.patch.object(trial,'run_cache') as run:
                trial.cache_on_worker({'condition':condition,'run_id':'cpu','cache_inventory_sha256':'cpu'},10**30)
                operations=[(c.args[1],c.args[2],c.args[3].name) for c in run.call_args_list]
                expected=[('store_inputs','cold','cache-reclaim')] if condition.startswith('safetensors_') else []
                kind,mode=trial.CONDITIONS[condition];other='store_inputs' if kind=='safetensors_inputs' else 'safetensors_inputs'
                if mode=='hot':expected += [(kind,'cold','cache-target-reset')]
                expected += [(other,'check','cache-other'),(kind,mode,'cache-target')]
                self.assertEqual(run.call_args.kwargs['reader_cpus'],[0,1,120,121] if mode=='hot' else None)
                self.assertEqual(operations,expected)

    def test_root_sampler_archive_is_bounded_and_never_overwrites_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=root/'cache.tar';output=root/'output';output.mkdir()
            def make(entries):
                with tarfile.open(archive,'w') as bundle:
                    for name,data in entries:
                        member=tarfile.TarInfo(name);member.size=len(data);bundle.addfile(member,io.BytesIO(data))
            make([('cache-target/summary.json',b'{}'),('trial-cache-complete.json',b'{"sampler":{}}')])
            trial.unpack_cache_archive(archive,output)
            self.assertEqual(json.loads((output/'trial-cache-complete.json').read_bytes()),{'sampler':{}})
            with self.assertRaises(FileExistsError):trial.unpack_cache_archive(archive,output)
            make([('../outside',b'bad')])
            with self.assertRaisesRegex(ValueError,'unexpected cache archive'):trial.unpack_cache_archive(archive,output)
            self.assertFalse((root/'outside').exists())

    def test_pool_fill_and_fresh_confirmation_surround_cache_scan_in_order(self):
        order=[]
        async def load(*args):order.append('confirm');return True
        async def cache(*args):order.append('cache');return {'complete_ns':time.monotonic_ns()}
        model='cpu-model';manager=types.SimpleNamespace(
            load_to_host=types.SimpleNamespace(remote=mock.AsyncMock(side_effect=load)),
            get_grouped_host_receipt=types.SimpleNamespace(remote=mock.AsyncMock(return_value=
                {'receipts':{str(i):f'cpu-{i}' for i in range(496)}})),
            get_store_info=types.SimpleNamespace(remote=mock.AsyncMock(return_value=
                [{model:([str(i) for i in range(496)],496)},{model:1},0])))
        worker=types.SimpleNamespace(remote=mock.AsyncMock(side_effect=cache))
        ray=types.SimpleNamespace(get_actor=lambda name:manager,remote=lambda **options:lambda fn:worker,
                                 available_resources=lambda:{'GPU':8})
        controller=types.SimpleNamespace(register=types.SimpleNamespace(remote=mock.AsyncMock()),
                                         delete=types.SimpleNamespace(remote=mock.AsyncMock()))
        with mock.patch.dict('sys.modules',ray=ray),mock.patch.object(trial,'wait_file',side_effect=cache),mock.patch.object(trial,'write_once') as write:
            asyncio.run(trial.prepare_service_trial(controller,{'model':model},
                {'trial':{'condition':'store_pool_hit','run_id':'cpu-pool'}},time.monotonic_ns()+600_000_000_000,'192.0.2.1'))
            self.assertEqual(order,['confirm','cache','confirm'])
            self.assertTrue(write.call_args.args[1]['pool_confirmed'])
            controller.delete.remote.assert_awaited_once_with(model)

    def test_transport_and_monitor_timeouts_keep_original_error_and_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            opener=types.SimpleNamespace(open=mock.Mock(side_effect=urllib.error.URLError(TimeoutError('CPU timeout'))))
            with mock.patch.object(trial.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises(TimeoutError):
                    trial.post_raw('http://192.0.2.1','/fixture',{},root/'raw.json',time.monotonic_ns()+1800_000_000_000)
            self.assertGreater(opener.open.call_args.kwargs['timeout'],1700)
            self.assertIn('CPU timeout',json.loads((root/'raw.json').read_bytes())['transport_error'])
            (root/'trial-abort.json').write_text(json.dumps(dict(error='CPU monitor deadline',is_timeout=True)))
            with self.assertRaises(TimeoutError):trial.wait_json(root/'missing.json',time.monotonic_ns()+10_000_000_000)

    def test_host_driver_feeds_actual_receive_times_into_real_journal(self):
        case=fixture.AttemptJournalContracts('test_origin_is_durable_before_preparation_and_deadline_includes_it')
        case.setUp()
        try:
            journal=case.start()
            with tempfile.TemporaryDirectory() as directory:
                base=Path(directory);output=base/'output';output.mkdir()
                identity={key:journal.identity[key] for key in ('run_id','boot_id','time_namespace','clock_id')}
                inventory={kind:{'/frozen/input':{'bytes':4097}} for kind in ('store_inputs','safetensors_inputs')}
                path=base/'cache-inventory.json';path.write_text(json.dumps(inventory))
                for name,kind,mode,begin in [('cache-target','store_inputs','cold',140),
                                              ('cache-other','safetensors_inputs','check',120)]:
                    summary,_,vectors=scan(kind,mode,begin);summary['identity']=identity
                    for row in summary['files']:row['mincore_visibility']=dict(euid=os.geteuid(),owner_uid=os.geteuid(),cap_effective='0',user_namespace=os.readlink('/proc/self/ns/user'))
                    dest=output/name;dest.mkdir();(dest/'summary.json').write_text(json.dumps(summary))
                    for filename,flags in vectors.items():(dest/filename).write_bytes(gzip.compress(flags))
                ready=dict(store_validation_complete_ns=110,store_registration_reused=True,
                           address='192.0.2.1',model='cpu-model',registration={'model':'cpu-model'})
                (output/'store-daemon.log').write_text('CPU fixture prefill precedes startup\n')
                (base/'READY.json').write_text(json.dumps({'roles':{'worker':{'container_id':'c'*64}}}))
                (output/'trial-cache-request.json').write_text(json.dumps(dict(run_id='cpu-run',condition='store_disk_cold',deadline_ns=900_000_000_100)))
                (output/'trial-service-ready.json').write_text(json.dumps(ready))
                (output/'store-pre-model-baseline-checked.json').write_text('{}')
                events=case.business_events(journal);source=next(events.glob('*.jsonl'))
                rows=[json.loads(line) for line in source.read_text().splitlines()]
                for row in rows:
                    if 'request_id' in row:row['request_id']='cpu-run-business'
                backend = next(row for row in rows if row['name']=='backend_first_token_observed')
                for name,when in [('backend_first_token_observed',226),('backend_generation_complete',228)]:
                    rows.append(dict(backend,name=name,request_id='cpu-run-workload32',monotonic_ns=when))
                for rank in range(8):
                    for group in range(61):
                        rows.append(dict(rows[0],pid=10000+rank,rank=rank,sequence=group+1,
                            name='grouped_expert_postprocess_host_end',group_id=f'expert_{group:04d}',monotonic_ns=187))
                dest=output/'events';dest.mkdir();(dest/'process-cpu.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
                gold=json.loads((trial.ROOT/'docs/evidence/v4-native-reference-success/successful-run/native-gold.json').read_bytes())['responses']
                def post(url,route,payload,path,deadline):
                    if route=='/register':actual={};sent,received,later=171,175,175
                    elif route=='/delete':actual={};sent,received,later=245,250,260
                    else:
                        purpose='business' if payload['request_id'].endswith('-business') else 'workload32'
                        actual=copy.deepcopy(gold[purpose]);actual['id']=payload['request_id']
                        actual['usage']={'prompt_tokens':30 if purpose=='business' else 43}
                        sent,received,later=(180,200,220) if purpose=='business' else (225,230,240)
                        if purpose=='business':
                            with (output/'store-daemon.log').open('a') as stream:
                                for i in range(496):
                                    stream.write(f'Loading model group{i} size 1 to host\nFinished loading model group{i} from disk\n')
                    case.now=later
                    path.write_text(json.dumps(dict(status=200,body=json.dumps(actual),sent_ns=sent,received_ns=received)))
                    return actual,sent,received
                case.now=170
                preparation=dict(preparation_begin_ns=100,container_deadline_ns=1080_000_000_100,
                    deadline_ns=1200_000_000_100,trial=dict(condition='store_disk_cold',run_id='cpu-run',
                    attempt_timeout_seconds=1200,cache_inventory_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                with mock.patch.object(trial.subprocess,'run') as helper,mock.patch.object(trial,'unpack_cache_archive'),mock.patch.object(trial,'post_raw',side_effect=post):
                    trial.drive_trial(base,preparation,journal)
                    self.assertIn('0:0',helper.call_args.args[0])
                    self.assertIn('CUDA_VISIBLE_DEVICES=',helper.call_args.args[0])
                self.assertEqual(journal.record['correct_business_response_ns'],200)
                self.assertEqual(journal.record['delete_complete_ns'],250)
                self.assertTrue((output/'trial-host-finished.json').exists())
                journal.fail(RuntimeError('CPU fixture terminal'))
                self.assertEqual(case.collect()[0]['status'],'failed')
        finally:case.doCleanups()


if __name__=='__main__':unittest.main()
