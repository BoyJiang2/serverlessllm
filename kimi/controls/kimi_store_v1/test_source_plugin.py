import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import MagicMock, Mock, call, patch

import source_plugin as plugin
import vllm_export_hook
from contract import REVISION
from loader_registry import FORMATS

ROOT = Path(__file__).parent.resolve()


class SourcePluginTests(unittest.TestCase):
    def test_reference_only_uses_builtin_safetensors_without_store_output(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            Path(options['spec']['output_root']).rmdir()
            options['spec'].update(mode='reference',output_root=None)
            core=types.SimpleNamespace(shutdown=Mock())
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),self.completion(False)]))
            factory=Mock(return_value=engine)
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',side_effect=AssertionError('reference must not read Store receipts')):
                result=plugin._run_source_reference(factory,lambda **kwargs:kwargs,**options)
            self.assertEqual(factory.call_args.kwargs['load_format'],'safetensors')
            self.assertEqual(factory.call_args.kwargs['tensor_parallel_size'],8)
            self.assertIsNone(result['ranks']);self.assertFalse(result['export_performed'])
            self.assertTrue(result['engine_shutdown_returned'])
            self.assertEqual(len(result['business']['fixed_token_business']['token_ids']),32)
            self.assertFalse((Path(options['spec']['run_root'])/'export').exists())
            path=Path(options['spec']['run_root'])/'source-reference-events.jsonl'
            rows=[json.loads(line) for line in path.read_bytes().splitlines()]
            rows[-1]['payload']['ranks']={'completed_rank_count':8}
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaisesRegex(ValueError,'reference-only'):
                plugin.read_reference_events(path,options['identity'])
            for value in (0,1,None,'missing'):
                with self.subTest(export_performed=value):
                    changed=json.loads(json.dumps(rows))
                    if value=='missing':changed[-1]['payload'].pop('export_performed')
                    else:changed[-1]['payload']['export_performed']=value
                    path.write_text(''.join(json.dumps(row)+'\n' for row in changed))
                    with self.assertRaises(ValueError):plugin.read_reference_events(path,options['identity'])

    def test_reference_plugin_verifies_spec_without_registering_export_loader(self):
        with tempfile.TemporaryDirectory() as temp:
            spec,path=self.fixture(temp)
            Path(spec['output_root']).rmdir();spec.update(mode='reference',output_root=None)
            self.activate(spec,path)
            registry,base=self.registry();before=dict(registry._LOAD_FORMAT_TO_MODEL_LOADER)
            with patch.object(plugin,'_verify_code_bundle',return_value={}),\
                    patch.object(plugin,'_runtime',return_value=(registry,base,object)),\
                    patch.object(vllm_export_hook,'make_checkpoint_export_loader',side_effect=AssertionError('unexpected export factory')):
                self.assertEqual(plugin.register(),'safetensors')
                self.assertEqual(plugin.register(),'safetensors')
                plugin._registration=('old export',registry,object)
                with self.assertRaisesRegex(ValueError,'cannot reuse'):
                    plugin.register()
                plugin._registration=None
            self.assertEqual(registry._LOAD_FORMAT_TO_MODEL_LOADER,before)
            self.assertIsNone(plugin._registration)

    def worker_fixture(self,temp):
        from test_source_bundle import SourceBundleTests
        root,path,document=SourceBundleTests().source_worker_fixture(temp)
        return root,path,document,hashlib.sha256(path.read_bytes()).hexdigest()

    def test_bound_worker_defaults_to_vllm_factories_after_real_input_verification(self):
        for mode in ('reference','export'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root,path,document,digest=self.worker_fixture(temp)
                if mode=='reference':
                    spec_path=root/'source-spec.json';spec=json.loads(spec_path.read_bytes())
                    Path(spec['output_root']).rmdir();spec.update(mode='reference',output_root=None)
                    spec_path.write_bytes(json.dumps(spec).encode())
                    plan_path=root/'source-plan.json';plan=json.loads(plan_path.read_bytes())
                    plan['spec_sha256']=hashlib.sha256(spec_path.read_bytes()).hexdigest()
                    plan_path.write_bytes(json.dumps(plan).encode())
                    document['plan_sha256']=hashlib.sha256(plan_path.read_bytes()).hexdigest()
                    path.write_bytes(json.dumps(document).encode());digest=hashlib.sha256(path.read_bytes()).hexdigest()
                core=types.SimpleNamespace(shutdown=Mock())
                engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                    generate=Mock(side_effect=[self.completion(),self.completion(False)]))
                factory=Mock(return_value=engine);sampling=Mock(side_effect=lambda **kw:kw)
                module=types.ModuleType('vllm');module.LLM=factory;module.SamplingParams=sampling
                with patch.object(plugin,'__file__',str(root/'code/source_plugin.py')), \
                        patch.object(plugin,'_runtime') as runtime, \
                        patch.dict(sys.modules,{'vllm':module}), \
                        patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={'completed_rank_count':8}) as ranks:
                    result=plugin._run_source_worker(worker_input_path=path,worker_input_sha256=digest)
                runtime.assert_called_once();factory.assert_called_once();core.shutdown.assert_called_once()
                self.assertEqual(factory.call_args.kwargs['load_format'],'safetensors' if mode=='reference' else FORMATS['export'])
                self.assertEqual(factory.call_args.kwargs['tensor_parallel_size'],8)
                self.assertEqual(result['export_performed'],mode=='export')
                if mode=='reference':ranks.assert_not_called();self.assertIsNone(result['ranks'])
                self.assertEqual(len(result['business']['fixed_token_business']['token_ids']),32)
                self.assertFalse(result['runtime_admitted']);self.assertFalse(result['gold_frozen'])

    def test_bound_worker_default_runtime_refusals_and_import_budget_never_construct_engine(self):
        for kind in ('bad-input','one-factory','abi','runtime-budget'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,path,document,digest=self.worker_fixture(temp)
                factory=Mock();module=types.ModuleType('vllm');module.LLM=factory;module.SamplingParams=Mock()
                ticks=[100]
                def runtime():
                    if kind=='abi':raise ValueError('wrong frozen runtime ABI')
                    if kind=='runtime-budget':ticks[0]+=1000
                if kind=='bad-input':path.write_bytes(path.read_bytes()+b' ')
                with patch.object(plugin,'__file__',str(root/'code/source_plugin.py')), \
                        patch.object(plugin,'_runtime',side_effect=runtime) as checked, \
                        patch.object(plugin.time,'monotonic',side_effect=lambda:ticks[0]), \
                        patch.dict(sys.modules,{'vllm':module}):
                    kwargs=dict(worker_input_path=path,worker_input_sha256=digest)
                    if kind=='one-factory':kwargs['engine_factory']=factory
                    with self.assertRaises((ValueError,TimeoutError)):plugin._run_source_worker(**kwargs)
                factory.assert_not_called()
                if kind in ('bad-input','one-factory'):checked.assert_not_called()
                else:checked.assert_called_once()
                if kind=='runtime-budget':
                    rows=[json.loads(x) for x in (root/'source-reference-events.jsonl').read_bytes().splitlines()]
                    self.assertEqual(rows[-1]['event'],'failed')
                    self.assertEqual(rows[-1]['payload']['error_type'],'TimeoutError')

    def test_bound_worker_consumes_frozen_business_and_records_input_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root,path,document,digest=self.worker_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock())
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),self.completion(False)]))
            factory=Mock(return_value=engine)
            # Real metadata/file verifier on a tiny non-executable code fixture;
            # loaded module origin and all engine/rank/CUDA boundaries are substitutes.
            with patch.object(plugin,'__file__',str(root/'code/source_plugin.py')), \
                    patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={'completed_rank_count':8}):
                result=plugin._run_source_worker(factory,lambda **kw:kw,
                    worker_input_path=path,worker_input_sha256=digest)
                self.assertEqual(result['worker_input_sha256'],digest)
                self.assertEqual(factory.call_args.kwargs['kv_cache_memory_bytes'],document['kv_cache_memory_bytes'])
                self.assertEqual(engine.generate.call_args.args[0],[{'prompt_token_ids':document['prompt_token_ids']}])
                saved=plugin.read_reference_events(root/'source-reference-events.jsonl',
                    {k:result[k] for k in ('run_id','holder','code_commit','spec_sha256','source_manifest_sha256')})
                self.assertEqual(saved['records'][0]['payload']['worker_input_sha256'],digest)
                self.assertEqual(saved['records'][1]['payload']['worker_input_sha256'],digest)
                self.assertFalse(result['gold_frozen'])
                self.assertFalse(result['runtime_admitted'])
                with self.assertRaises(FileExistsError):
                    plugin._run_source_worker(factory,lambda **kw:kw,
                        worker_input_path=path,worker_input_sha256=digest)
            factory.assert_called_once()
            core.shutdown.assert_called_once()

    def test_bound_worker_rejects_wrong_module_future_anchor_and_consumed_budget_before_engine(self):
        for mode in ('module','future','budget'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root,path,document,digest=self.worker_fixture(temp)
                factory=Mock()
                if mode=='future':
                    document['batch_entered_utc']=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
                    path.write_bytes(json.dumps(document).encode())
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                module=str(ROOT/'source_plugin.py') if mode=='module' else str(root/'code/source_plugin.py')
                with patch.object(plugin,'__file__',module):
                    if mode=='budget':
                        with patch.object(plugin.time,'monotonic',side_effect=[100,200]):
                            with self.assertRaises(TimeoutError):
                                plugin._run_source_worker(factory,Mock(),worker_input_path=path,worker_input_sha256=digest)
                    else:
                        with self.assertRaises(ValueError if mode=='module' else TimeoutError):
                            plugin._run_source_worker(factory,Mock(),worker_input_path=path,worker_input_sha256=digest)
                factory.assert_not_called()
                self.assertFalse((root/'source-reference-events.jsonl').exists())

    def test_bound_worker_failure_journal_retains_input_digest_and_original_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root,path,document,digest=self.worker_fixture(temp)
            with patch.object(plugin,'__file__',str(root/'code/source_plugin.py')):
                with self.assertRaisesRegex(RuntimeError,'planned init failure'):
                    plugin._run_source_worker(Mock(side_effect=RuntimeError('planned init failure')),Mock(),
                        worker_input_path=path,worker_input_sha256=digest)
            rows=[json.loads(line) for line in (root/'source-reference-events.jsonl').read_bytes().splitlines()]
            self.assertEqual(rows[-1]['event'],'failed')
            self.assertEqual(rows[-1]['payload']['worker_input_sha256'],digest)
            self.assertEqual(rows[0]['payload']['prompt_token_ids'],document['prompt_token_ids'])

    def test_oversize_source_metadata_is_rejected_before_runtime_import(self):
        for kind in ('config','targets'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                spec,path=self.fixture(temp);self.activate(spec,path)
                file=(Path(spec['checkpoint_path'])/'config.json' if kind=='config'
                      else Path(spec['checkpoint_contract_path']))
                with file.open('ab') as stream:stream.write(b' ')
                with patch.object(plugin,'_runtime') as runtime:
                    with self.assertRaisesRegex(ValueError,'frozen metadata length'):
                        plugin.register()
                    runtime.assert_not_called()

    def reference_fixture(self, temp):
        spec,path=self.fixture(temp)
        identity=dict(run_id=spec['run_id'],holder=spec['holder'],code_commit=spec['code_commit'],
            spec_sha256='c'*64,source_manifest_sha256='d'*64)
        entered=datetime.now(timezone.utc)
        phases=('engine_initialize_and_export','fixed_token_business','natural_eos_business','worker_shutdown')
        schedule=dict(phase_deadlines_utc={name:(entered+timedelta(hours=1)).isoformat() for name in phases})
        return dict(spec=spec,identity=identity,prompt_token_ids=[1,2],kv_cache_memory_bytes=4096,
            schedule=schedule,entered=entered,started=time.monotonic())

    def completion(self, fixed=True):
        tokens=[10,11]*16 if fixed else [10,11]
        output=types.SimpleNamespace(token_ids=tokens,text='CPU fixture',
            logprobs=[{token:types.SimpleNamespace(logprob=-0.1)} for token in tokens],
            finish_reason='length' if fixed else 'stop',stop_reason=None)
        return [types.SimpleNamespace(finished=True,prompt_token_ids=[1,2],outputs=[output])]

    def test_reference_body_collects_ranks_then_two_distinct_business_requests_and_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp);events=[]
            core=types.SimpleNamespace(shutdown=Mock(side_effect=lambda **kw:events.append('shutdown')))
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),self.completion(False)]))
            factory=Mock(side_effect=lambda **kw:(events.append('engine') or engine))
            sampling=Mock(side_effect=lambda **kw:kw)
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',
                    side_effect=lambda *a:(events.append('ranks') or {'completed_rank_count':8})):
                result=plugin._run_source_reference(factory,sampling,**options)
            self.assertEqual(events,['engine','ranks','shutdown'])
            self.assertEqual(factory.call_args.kwargs['tensor_parallel_size'],8)
            self.assertEqual(factory.call_args.kwargs['moe_backend'],'marlin')
            self.assertNotIn('use_sequence_parallel_moe',factory.call_args.kwargs)
            self.assertIsNone(factory.call_args.kwargs['speculative_config'])
            self.assertFalse(factory.call_args.kwargs['language_model_only'])
            self.assertEqual(factory.call_args.kwargs['kv_cache_memory_bytes'],4096)
            self.assertTrue(sampling.call_args_list[0].kwargs['ignore_eos'])
            self.assertFalse(sampling.call_args_list[1].kwargs['ignore_eos'])
            self.assertEqual(sampling.call_args_list[0].kwargs['min_tokens'],32)
            self.assertEqual(len(result['business']['fixed_token_business']['token_ids']),32)
            self.assertTrue(result['engine_shutdown_returned'])
            self.assertFalse(result['gold_frozen'])
            self.assertFalse(result['resources_released'])
            self.assertFalse(result['full_service_verified'])
            saved=plugin.read_reference_events(Path(options['spec']['run_root'])/'source-reference-events.jsonl',
                options['identity'])
            self.assertEqual(saved['status'],'reference_returned')
            self.assertEqual(saved['records'][-1]['payload']['business'],result['business'])
            self.assertEqual(saved['records'][-1]['payload']['engine_options'],factory.call_args.kwargs)
            self.assertEqual(saved['records'][-1]['payload']['prompt_token_ids'],[1,2])
            self.assertEqual(saved['records'][0]['payload']['kv_cache_memory_bytes'],4096)
            self.assertEqual(saved['records'][0]['payload']['prompt_token_ids'],[1,2])
            self.assertFalse(saved['resources_released'])

    def test_reference_business_failure_still_shuts_down_and_preserves_original_error(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock(side_effect=OSError('cleanup failed')))
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=RuntimeError('business failed')))
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={}):
                with self.assertRaisesRegex(RuntimeError,'business failed') as caught:
                    plugin._run_source_reference(lambda **kw:engine,lambda **kw:kw,**options)
            core.shutdown.assert_called_once()
            self.assertIn('cleanup failed',caught.exception.__notes__[0])

    def test_reference_expired_budget_refuses_constructor_without_restarting_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            options['started']-=3601
            factory=Mock()
            with self.assertRaises(TimeoutError):
                plugin._run_source_reference(factory,Mock(),**options)
            factory.assert_not_called()

    def test_reference_constructor_failure_leaves_explicit_host_cleanup_obligation(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            with self.assertRaisesRegex(RuntimeError,'initialize failed') as caught:
                plugin._run_source_reference(Mock(side_effect=RuntimeError('initialize failed')),Mock(),**options)
            self.assertIn('partial processes',caught.exception.__notes__[0])
            self.assertFalse(caught.exception.source_reference_evidence['resources_released'])

    def test_reference_second_request_failure_preserves_first_raw_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock())
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),RuntimeError('natural EOS failed')]))
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={'fixture':True}):
                with self.assertRaisesRegex(RuntimeError,'natural EOS failed') as caught:
                    plugin._run_source_reference(lambda **kw:engine,lambda **kw:kw,**options)
            evidence=caught.exception.source_reference_evidence
            self.assertEqual(evidence['business']['fixed_token_business']['token_ids'],[10,11]*16)
            self.assertTrue(evidence['engine_shutdown_returned'])
            self.assertFalse(evidence['resources_released'])
            saved=plugin.read_reference_events(Path(options['spec']['run_root'])/'source-reference-events.jsonl',
                options['identity'])
            self.assertEqual(saved['status'],'failed')
            self.assertEqual(saved['records'][-1]['payload']['evidence']['business'],evidence['business'])

    def test_reference_journal_collision_never_restarts_or_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp);factory=Mock()
            path=Path(options['spec']['run_root'])/'source-reference-events.jsonl'
            path.write_bytes(b'original evidence')
            with self.assertRaises(FileExistsError):plugin._run_source_reference(factory,Mock(),**options)
            factory.assert_not_called()
            self.assertEqual(path.read_bytes(),b'original evidence')

    def test_reference_failure_log_error_does_not_replace_original_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp);original=plugin._write_reference_event
            def write(stream,identity,event,payload):
                if event=='failed':raise OSError('fixture evidence disk failure')
                return original(stream,identity,event,payload)
            with patch.object(plugin,'_write_reference_event',side_effect=write):
                with self.assertRaisesRegex(RuntimeError,'constructor failure') as caught:
                    plugin._run_source_reference(Mock(side_effect=RuntimeError('constructor failure')),Mock(),**options)
            self.assertIn('evidence disk failure',caught.exception.__notes__[-1])
            saved=plugin.read_reference_events(Path(options['spec']['run_root'])/'source-reference-events.jsonl',
                options['identity'])
            self.assertEqual(saved['status'],'incomplete')

    def test_reference_event_size_bound_precedes_disk_write(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp);path=Path(temp)/'event'
            with path.open('xb') as stream:
                with self.assertRaisesRegex(ValueError,'64KiB'):
                    plugin._write_reference_event(stream,options['identity'],'started',{'oversize':'x'*65536})
            self.assertEqual(path.stat().st_size,0)

    def test_real_buffered_flush_control_masks_error_but_unbuffered_journal_preserves_it(self):
        class FailingRaw(io.RawIOBase):
            def __init__(self,path):self.file=io.FileIO(path,'x');self.failed_writes=0
            def writable(self):return True
            def fileno(self):return self.file.fileno()
            def write(self,data):
                if b'"event": "failed"' in bytes(data):
                    self.failed_writes+=1
                    raise OSError('fixture ENOSPC')
                return self.file.write(data)
            def close(self):
                try:super().close()
                finally:self.file.close()
        for buffered_control in (True,False):
            with self.subTest(buffered_control=buffered_control),tempfile.TemporaryDirectory() as temp:
                options=self.reference_fixture(temp);handles=[];original=Path.open
                def open_journal(path,mode='r',*args,**kwargs):
                    if path.name!='source-reference-events.jsonl':return original(path,mode,*args,**kwargs)
                    raw=FailingRaw(path);handles.append(raw)
                    if buffered_control:return io.BufferedWriter(raw)
                    self.assertEqual(kwargs['buffering'],0)
                    return raw
                with patch.object(Path,'open',open_journal):
                    if buffered_control:
                        with self.assertRaisesRegex(OSError,'ENOSPC'):
                            plugin._run_source_reference(Mock(side_effect=RuntimeError('constructor failure')),Mock(),**options)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'constructor failure') as caught:
                            plugin._run_source_reference(Mock(side_effect=RuntimeError('constructor failure')),Mock(),**options)
                        self.assertIn('ENOSPC',caught.exception.__notes__[-1])
                self.assertTrue(handles[0].closed)
                self.assertEqual(handles[0].failed_writes,2 if buffered_control else 1)

    def test_reference_reader_rejects_partial_stale_oversized_and_duplicate_records(self):
        for kind in ('partial','stale','oversize','duplicate','clock'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                options=self.reference_fixture(temp);path=Path(temp)/'events'
                with path.open('xb') as stream:
                    plugin._write_reference_event(stream,options['identity'],'started',{})
                raw=path.read_bytes()
                if kind=='partial':path.write_bytes(raw[:-1])
                elif kind=='stale':
                    row=json.loads(raw);row['holder']='e'*32
                    path.write_bytes((json.dumps(row)+'\n').encode())
                elif kind=='oversize':path.write_bytes(b'x'*131073)
                elif kind=='duplicate':path.write_bytes(raw*3)
                else:
                    row=json.loads(raw);row['monotonic_seconds']=float('nan')
                    path.write_bytes((json.dumps(row)+'\n').encode())
                with self.assertRaises(ValueError):plugin.read_reference_events(path,options['identity'])

    def test_killed_cpu_only_child_keeps_incomplete_journal_without_completion_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            serial=dict(options);serial.pop('entered');serial.pop('started')
            script=('import json,sys,time; from datetime import datetime,timezone; import source_plugin as p; '
                'o=json.loads(sys.argv[1]);o.update(entered=datetime.now(timezone.utc),started=time.monotonic()); '
                'p._run_source_reference(lambda **kw:time.sleep(30),dict,**o)')
            env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
            child=subprocess.Popen([sys.executable,'-B','-X','utf8','-c',script,json.dumps(serial)],
                env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            path=Path(options['spec']['run_root'])/'source-reference-events.jsonl'
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and child.poll() is None:
                    if path.exists() and path.stat().st_size:break
                    time.sleep(0.05)
                self.assertIsNone(child.poll())
                self.assertTrue(path.exists())
                child.kill()
                child.communicate(timeout=5)
                saved=plugin.read_reference_events(path,options['identity'])
                self.assertEqual(saved['status'],'incomplete')
                self.assertFalse(saved['runtime_admitted'])
            finally:
                if child.poll() is None:child.kill()
                child.communicate(timeout=5)

    def test_reference_shutdown_failure_after_valid_business_is_failure_with_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            options=self.reference_fixture(temp)
            core=types.SimpleNamespace(shutdown=Mock(side_effect=OSError('shutdown failed')))
            engine=types.SimpleNamespace(llm_engine=types.SimpleNamespace(engine_core=core),
                generate=Mock(side_effect=[self.completion(),self.completion(False)]))
            with patch.object(vllm_export_hook,'collect_source_rank_receipts',return_value={}):
                with self.assertRaisesRegex(OSError,'shutdown failed') as caught:
                    plugin._run_source_reference(lambda **kw:engine,lambda **kw:kw,**options)
            evidence=caught.exception.source_reference_evidence
            self.assertEqual(len(evidence['business']),2)
            self.assertFalse(evidence['engine_shutdown_returned'])

    def test_reference_output_rejects_degenerate_nonfinite_eos_and_unfinished_results(self):
        for kind in ('degenerate','nonfinite','early','unfinished','prompt','length','missing_logprob'):
            with self.subTest(kind=kind):
                requests=self.completion();output=requests[0].outputs[0]
                if kind=='degenerate':output.token_ids=[10]*32
                elif kind=='nonfinite':output.logprobs[0][10].logprob=float('nan')
                elif kind=='early':output.token_ids=output.token_ids[:2]
                elif kind=='unfinished':requests[0].finished=False
                elif kind=='prompt':requests[0].prompt_token_ids=[3]
                elif kind=='length':output.finish_reason='stop'
                else:output.logprobs=None
                with self.assertRaises(ValueError):plugin._reference_output(requests,[1,2],fixed=True)
        requests=self.completion(False);requests[0].outputs[0].finish_reason='length'
        with self.assertRaises(ValueError):plugin._reference_output(requests,[1,2],fixed=False)

    def test_reference_rejects_unbounded_prompt_kv_or_identity_before_constructor(self):
        for kind in ('prompt','kv','identity'):
            with tempfile.TemporaryDirectory() as temp:
                options=self.reference_fixture(temp);factory=Mock()
                if kind=='prompt':options['prompt_token_ids']=[1]*897
                elif kind=='kv':options['kv_cache_memory_bytes']=True
                else:options['identity']['holder']='e'*32
                with self.assertRaises(ValueError):plugin._run_source_reference(factory,Mock(),**options)
                factory.assert_not_called()

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in (plugin.SPEC_ENV, plugin.SHA_ENV, plugin.BUNDLE_SHA_ENV) + plugin.NATIVE_ENV:
            os.environ.pop(name, None)
        state = patch.object(plugin, '_registration', None)
        state.start()
        self.addCleanup(state.stop)
        native_state = patch.object(plugin, '_native_registration', None)
        native_state.start()
        self.addCleanup(native_state.stop)

    def native_fixture(self, temp):
        source, _ = self.fixture(temp)
        run = Path(source['run_root'])
        config = run / 'config.json'
        config.write_bytes((Path(source['checkpoint_path']) / 'config.json').read_bytes())
        storage = Path(temp) / 'store'
        storage.mkdir()
        build = run / 'build.json'
        build.write_bytes(b'{}')  # Metadata only; cannot satisfy compiled build check.
        rows = []
        for rank in range(8):
            (storage / ('rank-' + str(rank))).mkdir()
            rows.append(dict(storage_path=str(storage), model_path='rank-' + str(rank),
                expected_sidecar_sha256='c' * 64, server_address='127.0.0.1:18073',
                build_receipt=str(build), expected_build_sha256=hashlib.sha256(build.read_bytes()).hexdigest(),
                max_allocation_bytes=1024, timeout_seconds=30))
        spec = dict(schema='kimi-native-worker-plugin-v1', node='qjhs003', mode='native', revision=REVISION,
            run_id='kimi-native-20261031T000000Z-01234567', holder='a' * 32, code_commit='b' * 40,
            run_root=str(run), config_path=str(config), checkpoint_contract_path=source['checkpoint_contract_path'],
            rank_options=rows, device_memory_budgets=[1024] * 8)
        return spec, run / 'native-spec.json'

    def activate_native(self, spec, path):
        raw = json.dumps(spec, sort_keys=True).encode('utf-8')
        path.write_bytes(raw)
        for name, value in zip(plugin.NATIVE_ENV, (str(path), hashlib.sha256(raw).hexdigest(), 'd' * 64)):
            os.environ[name] = value

    def test_native_real_registry_registers_deferred_without_touching_runtime_groups(self):
        import source_bundle
        import vllm_mixed_hook
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.native_fixture(temp)
            self.activate_native(spec, path)
            registry, base = self.registry()
            original = registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors']
            with patch.object(source_bundle, 'verify_native_run', return_value={}) as gate, \
                    patch.object(plugin, '_native_runtime', return_value=(registry, base, object(), object)), \
                    patch.object(vllm_mixed_hook, 'make_mixed_store_loader') as factory:
                self.assertEqual(plugin.register_native(), FORMATS['native'])
                candidate = registry._LOAD_FORMAT_TO_MODEL_LOADER[FORMATS['native']]
                self.assertEqual(plugin.register_native(), FORMATS['native'])
                self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER[FORMATS['native']], candidate)
                self.assertEqual(gate.call_count, 2)
                factory.assert_not_called()
                os.environ[plugin.NATIVE_ENV[2]] = 'e' * 64
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    plugin.register_native()
            self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors'], original)

    def test_native_code_gate_failure_precedes_runtime_and_cached_registration(self):
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.native_fixture(temp)
            self.activate_native(spec, path)
            with patch.object(plugin, '_native_runtime') as runtime:
                with self.assertRaises((ValueError, OSError)):
                    plugin.register_native()  # Real wrong layout, not a mocked rejection.
                runtime.assert_not_called()
            registry, base = self.registry()
            with patch.object(source_bundle, 'verify_native_run', return_value={}), \
                    patch.object(plugin, '_native_runtime', return_value=(registry, base, object(), object)):
                plugin.register_native()
            with patch.object(source_bundle, 'verify_native_run', side_effect=ValueError('changed code')), \
                    patch.object(plugin, '_native_runtime') as runtime:
                with self.assertRaisesRegex(ValueError, 'changed code'):
                    plugin.register_native()
                runtime.assert_not_called()

    def test_native_independent_schema_paths_and_rank_metadata_reject_before_runtime(self):
        for kind in ('schema', 'mode', 'run_id', 'duplicate', 'identity', 'budget', 'config', 'contract', 'escape', 'remote'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                spec, path = self.native_fixture(temp)
                if kind == 'schema': spec['schema'] = 'kimi-source-worker-plugin-v1'
                elif kind == 'mode': spec['mode'] = 'export'
                elif kind == 'run_id': spec['run_id'] = 'kimi-export-20261031T000000Z-01234567'
                elif kind == 'duplicate': spec['rank_options'][1] = spec['rank_options'][0]
                elif kind == 'identity': spec['rank_options'][0]['rank'] = 0
                elif kind == 'budget': spec['device_memory_budgets'][0] = True
                elif kind == 'escape': spec['rank_options'][0]['model_path'] = '../escape'
                elif kind == 'remote': spec['rank_options'][0]['server_address'] = 'qjhs001:18073'
                else:
                    target = Path(spec['config_path' if kind == 'config' else 'checkpoint_contract_path'])
                    data = target.read_bytes()
                    target.write_bytes(b'X' + data[1:])
                self.activate_native(spec, path)
                with patch.object(plugin, '_native_runtime') as runtime:
                    with self.assertRaises(ValueError): plugin.register_native()
                    runtime.assert_not_called()

    def test_native_disabled_partial_and_mixed_activation_refuse_before_runtime(self):
        with patch.object(plugin, '_native_runtime') as runtime:
            self.assertIsNone(plugin.register_native())
            os.environ[plugin.NATIVE_ENV[0]] = 'missing'
            with self.assertRaisesRegex(ValueError, 'independent'):
                plugin.register_native()
            os.environ[plugin.SPEC_ENV] = 'also-missing'
            with self.assertRaisesRegex(ValueError, 'separate'): plugin.register()
            runtime.assert_not_called()

    def test_native_entrypoint_discovery_and_spec_inheritance_in_fresh_cpu_children(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.native_fixture(temp)
            self.activate_native(spec, path)
            plugin.materialize_plugin_metadata(temp, mode='native')
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), temp)))
            script = ("import importlib.metadata,json,os; import source_plugin as p; "
                "entries=[e for e in importlib.metadata.entry_points(group='vllm.general_plugins') "
                "if e.name=='sllm_kimi_native_candidate']; assert len(entries)==1; "
                "s,c,t=p._load_native_spec(os.environ[p.NATIVE_ENV[0]],os.environ[p.NATIVE_ENV[1]]); "
                "assert entries[0].load() is p.register_native; "
                "from test_source_plugin import SourcePluginTests; from unittest.mock import patch; "
                "import source_bundle,vllm_mixed_hook; registry,base=SourcePluginTests().registry();\n"
                "with patch.object(source_bundle,'verify_native_run',return_value={}), "
                "patch.object(p,'_native_runtime',return_value=(registry,base,object(),object)), "
                "patch.object(vllm_mixed_hook,'make_mixed_store_loader') as factory:\n"
                " result=entries[0].load()(); assert result=='sllm_kimi_native_v1'; factory.assert_not_called()\n"
                "print(json.dumps({'run_id':s['run_id'],'holder':s['holder'],'ranks':len(s['rank_options'])}))")
            for _ in range(2):
                result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script], env=env,
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), dict(run_id=spec['run_id'], holder=spec['holder'], ranks=8))

    def fixture(self, temp):
        root = Path(temp)
        checkpoint, run = root / 'checkpoint', root / 'run'
        checkpoint.mkdir()
        run.mkdir()
        output = run / 'export'
        output.mkdir()
        (checkpoint / 'config.json').write_bytes((ROOT.parents[1] / 'evidence/followup-kimi-k26/config.json').read_bytes())
        contract = run / 'checkpoint-targets.json'
        contract.write_bytes((ROOT / 'checkpoint-targets.json').read_bytes())
        spec = dict(schema='kimi-source-worker-plugin-v1', node='qjhs003', mode='export',
            revision=REVISION, run_id='kimi-export-20261030T000000Z-01234567',
            holder='a' * 32, code_commit='b' * 40, checkpoint_path=str(checkpoint),
            run_root=str(run), output_root=str(output), checkpoint_contract_path=str(contract), chunk_bytes=8388608)
        path = run / 'source-spec.json'
        return spec, path

    def activate(self, spec, path):
        raw = json.dumps(spec, sort_keys=True).encode('utf-8')
        path.write_bytes(raw)
        os.environ[plugin.SPEC_ENV] = str(path)
        os.environ[plugin.SHA_ENV] = hashlib.sha256(raw).hexdigest()
        os.environ[plugin.BUNDLE_SHA_ENV] = 'd' * 64

    def registry(self):
        class Base: pass
        raw = ROOT / 'fixtures/vllm_loader_init.py'
        code = compile(raw.read_bytes(), str(raw), 'exec', dont_inherit=True)
        ns = dict(_LOAD_FORMAT_TO_MODEL_LOADER={'safetensors': object()}, BaseModelLoader=Base, logger=Mock())
        for name in ('register_model_loader', 'get_model_loader'):
            child = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)
            ns[name] = types.FunctionType(child, ns)
        return types.SimpleNamespace(**ns), Base

    def test_real_registry_registers_once_and_rejects_changed_identity_or_foreign_class(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            registry, base = self.registry()
            candidate = type('Candidate', (base,), {})
            builtin = registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors']
            with patch.object(plugin, '_verify_code_bundle', return_value={}), \
                    patch.object(plugin, '_runtime', return_value=(registry, base, object)), \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate) as factory:
                self.assertEqual(plugin.register(), FORMATS['export'])
                self.assertEqual(plugin.register(), FORMATS['export'])
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(factory.call_args.kwargs['torch_version'], '2.10.0+cu129')
                self.assertEqual(factory.call_args.kwargs['run_identity'],dict(
                    run_id=spec['run_id'],holder=spec['holder'],code_commit=spec['code_commit'],
                    spec_sha256=os.environ[plugin.SHA_ENV],source_manifest_sha256=os.environ[plugin.BUNDLE_SHA_ENV]))
                self.assertNotIn('rank_context', factory.call_args.kwargs)
                registry._LOAD_FORMAT_TO_MODEL_LOADER[FORMATS['export']] = type('Foreign', (base,), {})
                with self.assertRaisesRegex(ValueError, 'identity changed'): plugin.register()
                registry._LOAD_FORMAT_TO_MODEL_LOADER[FORMATS['export']] = candidate
                spec['holder'] = 'c' * 32
                self.activate(spec, path)
                with self.assertRaisesRegex(ValueError, 'identity changed'): plugin.register()
            self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors'], builtin)

    def test_bad_hash_identity_paths_and_bounds_fail_before_runtime_import(self):
        for kind in ('hash', 'node', 'mode', 'revision', 'chunk', 'outside', 'relative'):
            with tempfile.TemporaryDirectory() as temp:
                spec, path = self.fixture(temp)
                if kind in ('node', 'mode', 'revision'): spec[kind] = 'wrong'
                elif kind == 'chunk': spec['chunk_bytes'] = True
                elif kind == 'outside': spec['output_root'] = spec['checkpoint_path']
                elif kind == 'relative': spec['checkpoint_path'] = 'relative'
                self.activate(spec, path)
                if kind == 'hash': os.environ[plugin.SHA_ENV] = '0' * 64
                with patch.object(plugin, '_runtime') as runtime:
                    with self.assertRaises(ValueError): plugin.register()
                    runtime.assert_not_called()

    def test_cached_registration_does_not_accept_changed_config_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            registry, base = self.registry()
            candidate = type('Candidate', (base,), {})
            with patch.object(plugin, '_verify_code_bundle', return_value={}), \
                    patch.object(plugin, '_runtime', return_value=(registry, base, object)), \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate):
                plugin.register()
                config=Path(spec['checkpoint_path']) / 'config.json'
                original=config.read_bytes()
                config.write_bytes(b'X'+original[1:])  # Same length still must fail frozen SHA.
                with self.assertRaisesRegex(ValueError, 'config differs'): plugin.register()

    def test_disabled_or_partial_environment_cannot_register(self):
        with patch.object(plugin, '_runtime') as runtime:
            self.assertIsNone(plugin.register())
            os.environ[plugin.SPEC_ENV] = 'missing'
            with self.assertRaisesRegex(ValueError, 'both'): plugin.register()
            runtime.assert_not_called()

    def test_actual_cpu_torch_cannot_satisfy_cuda_runtime_contract(self):
        with patch.dict(sys.modules, vllm=types.SimpleNamespace(__version__='0.19.1')):
            with self.assertRaisesRegex(ValueError, 'exact vLLM'): plugin._runtime()

    def test_generated_entrypoint_loads_in_fresh_process_without_torch_or_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin.materialize_plugin_metadata(temp)
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), temp)))
            script = ("import importlib.metadata,json,sys; "
                "entries=[e for e in importlib.metadata.entry_points(group='vllm.general_plugins') "
                "if e.name=='sllm_kimi_source_candidate']; "
                "assert len(entries)==1; result=entries[0].load()(); "
                "print(json.dumps({'result':result,'torch_imported':'torch' in sys.modules}))")
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), dict(result=None, torch_imported=False))

    def test_fresh_child_receives_same_manifest_and_holder_without_model_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            script = ("import source_plugin as p,os,json; "
                "s,c,t=p._load_spec(os.environ[p.SPEC_ENV],os.environ[p.SHA_ENV]); "
                "print(json.dumps({'run_id':s['run_id'],'holder':s['holder'],'bytes':s['chunk_bytes']}))")
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                dict(run_id=spec['run_id'], holder=spec['holder'], bytes=spec['chunk_bytes']))

    def test_distribution_metadata_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = plugin.materialize_plugin_metadata(temp)
            before = (target / 'entry_points.txt').read_bytes()
            with self.assertRaises(FileExistsError): plugin.materialize_plugin_metadata(temp)
            self.assertEqual((target / 'entry_points.txt').read_bytes(), before)

    def test_growing_manifest_stream_has_a_hard_read_limit(self):
        payload = b'x' * 65537
        stream = Mock()
        stream.read.return_value = payload
        opened = MagicMock()
        opened.__enter__.return_value = stream
        with patch.object(Path, 'stat', return_value=types.SimpleNamespace(st_size=1)), \
                patch.object(Path, 'open', return_value=opened), \
                patch.object(plugin, '_runtime') as runtime:
            with self.assertRaisesRegex(ValueError, 'bounded'):
                plugin._load_spec(str(Path(tempfile.gettempdir()) / 'growing-spec.json'),
                    hashlib.sha256(payload).hexdigest())
            self.assertEqual(stream.read.call_args, call(65537))
            runtime.assert_not_called()

    def test_code_gate_failure_precedes_runtime_import_and_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            with patch.object(plugin, '_runtime') as runtime:
                # This checkout is not this spec's run_root/code: real gate refuses.
                with self.assertRaisesRegex(ValueError, 'another run'):
                    plugin.register()
                runtime.assert_not_called()
                self.assertIsNone(plugin._registration)

    def test_code_gate_is_rechecked_on_cached_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, path = self.fixture(temp)
            self.activate(spec, path)
            registry, base = self.registry()
            candidate = type('Candidate', (base,), {})
            with patch.object(plugin, '_runtime', return_value=(registry, base, object)) as runtime, \
                    patch.object(vllm_export_hook, 'make_checkpoint_export_loader', return_value=candidate), \
                    patch.object(plugin, '_verify_code_bundle', return_value={}) as code_gate:
                plugin.register()
                code_gate.side_effect = ValueError('source bytes changed')
                with self.assertRaisesRegex(ValueError, 'source bytes changed'):
                    plugin.register()
                self.assertEqual(runtime.call_count, 1)
                self.assertEqual(code_gate.call_count, 2)
