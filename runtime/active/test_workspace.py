"""Canonical path boundary regressions; no filesystem writes outside temp space."""
import ast,hashlib,json,pathlib,tempfile,unittest
from unittest.mock import patch
import workspace as w

class Workspace(unittest.TestCase):
    def test_actual_phase_trace_guard_accepts_only_run_alias(self):
        tree=ast.parse((w.SOURCE/'launch_control13.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=="spec.get('phase_trace')")
        code=compile(ast.Module(body=[node],type_ignores=[]),'actual phase trace guard','exec')
        for value,allowed in [('/r/events',True),('/experiment/events',False),('/r/../etc',False),('/r2/events',False)]:
            context=dict(spec={'phase_trace':True,'env':{'SLLM_EVENT_DIR':value}},pathlib=pathlib,RUN_ALIAS=w.RUN_ALIAS,HOST=w.HOST,args=[])
            with self.subTest(value=value):
                if allowed:exec(code,context)
                else:
                    with self.assertRaises(AssertionError):exec(code,context)
    def test_ray_socket_paths_fit_unix_limit(self):
        with patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':'/experiment'}):
            for role in ('ray13a','ray13b','service13a'):
                path=w.run_root()/('ray-head-'+role)/'session_2026-10-26_07-13-16_771823_4294967295'/'sockets/plasma_store'
                with self.subTest(role=role):self.assertLessEqual(len(path.as_posix().encode()),107)
    def test_cache_official_manifest_is_mounted_readonly(self):
        mounts={row['destination']:row for row in w.container_mounts()}
        self.assertEqual(mounts.get('/experiment/protocol/run-manifest.json'),dict(
            source=str(w.HOST/'protocol/run-manifest.json'),
            destination='/experiment/protocol/run-manifest.json',readonly=True))
    def test_host_and_container_paths(self):
        for value in (str(w.HOST),'/experiment'):
            with self.subTest(value=value),patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':value}):
                self.assertEqual(w.run_root(),pathlib.Path(str(w.RUN_ALIAS)) if value=='/experiment' else pathlib.Path(value)/'validation/controls'/w.manifest()['run_id'])
                self.assertFalse(w.run_root().is_relative_to(w.archive()))
    def test_old_root_and_arbitrary_override_rejected(self):
        for value in ('/tmp/test','/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57','/experiment/../archive'):
            with self.subTest(value=value),patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':value}),self.assertRaises(RuntimeError):w.workspace()
    def test_escaped_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for value in ('../archive','sllm-glm51fp8-20261026T092330Z-97af52ad/../archive','other-run'):
                (root/'run-manifest.json').write_text(json.dumps({'run_id':value}))
                with self.subTest(value=value),patch.object(w,'SOURCE',root),self.assertRaises(RuntimeError):w.run_root()

    def test_only_current_output_is_writable(self):
        rows=w.container_mounts();writable=[r for r in rows if not r['readonly']]
        self.assertEqual(writable,[dict(source=str(w.HOST/'validation/controls'/w.manifest()['run_id']),destination=str(w.RUN_ALIAS),readonly=False)])
        for row in rows:
            self.assertTrue(pathlib.PurePosixPath(row['source']).is_relative_to(w.HOST))
            self.assertNotIn('/sllm-glm51fp8-',row['source'].split('/glm5.1-fp8')[0])
        self.assertEqual(len(rows),len({row['destination'] for row in rows}))

    def test_frozen_gold_directory_alias_is_explicit(self):
        rows={row['destination']:row for row in w.container_mounts()}
        self.assertEqual(rows['/experiment/validation/native_repeat7']['source'],str(w.HOST/'validation/native-repeat7'))
        self.assertTrue(rows['/experiment/validation/native_repeat7']['readonly'])

    def test_configured_writes_use_current_run_output(self):
        prefix=str(w.RUN_ALIAS)+'/'
        for path in w.SOURCE.glob('*.json'):
            config=json.loads(path.read_text());env=config.get('env',{})
            if not env:continue
            self.assertEqual(env['SLLM_CONTROL_WORKSPACE'],'/experiment')
            for key,value in env.items():
                if key in ('HOME','HF_HOME','SLLM_EVENT_DIR','SLLM_SERVICE_WEIGHT_AUDIT_DIR') or key.endswith('_CACHE_DIR') or key in ('VLLM_CACHE_ROOT','CUDA_CACHE_PATH'):
                    with self.subTest(file=path.name,key=key):self.assertTrue(value.startswith(prefix))

    def test_historical_mapping_without_legacy_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            index=dict(old_path_compatibility_only='/retired/path',entries=[dict(historical_path='/retired/path/source/tree',actual_path=str(w.HOST/'code/ServerlessLLM'))])
            data=json.dumps(index).encode();(root/'INDEX.json').write_bytes(data)
            with patch.object(w,'workspace',return_value=root),patch.object(w,'manifest',return_value={'canonical_index_sha256':hashlib.sha256(data).hexdigest()}):
                self.assertEqual(w.historical_path('source/tree/sllm/server.py'),root/'code/ServerlessLLM/sllm/server.py')
                self.assertEqual(w.historical_path('retained.log'),root/'archive/original-run/retained.log')
                with self.assertRaises(RuntimeError):w.historical_path('../retired/path')
                (root/'INDEX.json').write_bytes(data+b' ')
                with self.assertRaisesRegex(RuntimeError,'mapping changed'):w.historical_path('retained.log')

    def test_generated_head_cannot_override_source_template(self):
        import materialize_control13 as m
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            (root/'run-manifest.json').write_bytes((w.SOURCE/'run-manifest.json').read_bytes())
            spec=json.loads((w.SOURCE/'headray13a.json').read_text());spec['argv'].append('--num-gpus=8')
            (root/'headray13a.json').write_text(json.dumps(spec))
            with self.assertRaises(AssertionError):m.validate_spec(root,spec)

class InputLayout(unittest.TestCase):
    """Synthetic file-layout fixtures only; no model or inference substitute."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=pathlib.Path(temporary.name)
        self.store=self.root/'store/tp8';self.store.mkdir(parents=True)
        ranks=[]
        for rank in range(8):
            directory=self.store/f'rank_{rank}';directory.mkdir()
            for part in range(10):(directory/f'tensor.data_{part}').write_bytes(b'x')
            ranks.append(dict(rank=rank,partition_bytes=[1]*10))
        ranks.append(dict(rank='inactive_mtp',partition_bytes=[1]))
        (self.store/'raw-disk-verification.json').write_text(json.dumps(dict(ranks=ranks)))
        references={}
        for relative in ('validation/native-gold.json','validation/native-weight-contract.json',
                         'validation/store-restore-validation.json','code/weight_contract.py','store/tp8/raw-disk-verification.json'):
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():path.write_bytes(b'{}')
            references[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        for patcher in (patch.object(w,'workspace',return_value=self.root),
                        patch.object(w,'manifest',return_value=dict(reference_sha256=references)),
                        patch('cache_contract13.frozen_paths',return_value=[None]*148)):
            patcher.start();self.addCleanup(patcher.stop)
    def test_complete_layout(self):
        self.assertEqual(w.verify_input_layout(),dict(checkpoint_inputs=148,store_ranks=8,store_parts=80))
    def test_checkpoint_failure_propagates(self):
        with patch('cache_contract13.frozen_paths',side_effect=RuntimeError('empty checkpoint')),self.assertRaises(RuntimeError):
            w.verify_input_layout()
    def test_missing_rank_rejected(self):
        (self.store/'rank_7').rename(self.store/'wrong_rank')
        with self.assertRaises(RuntimeError):w.verify_input_layout()
    def test_missing_part_rejected(self):
        (self.store/'rank_7/tensor.data_9').unlink()
        with self.assertRaises(RuntimeError):w.verify_input_layout()
    def test_truncated_part_rejected(self):
        (self.store/'rank_7/tensor.data_9').write_bytes(b'')
        with self.assertRaises(RuntimeError):w.verify_input_layout()
    def test_changed_store_receipt_rejected(self):
        (self.store/'raw-disk-verification.json').write_bytes(b'{}')
        with self.assertRaises(RuntimeError):w.verify_input_layout()
    def test_changed_gold_rejected(self):
        (self.root/'validation/native-gold.json').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):w.verify_input_layout()
    def test_changed_valid_restore_json_rejected(self):
        (self.root/'validation/store-restore-validation.json').write_text('{"pool_bytes":1}')
        with self.assertRaises(RuntimeError):w.verify_input_layout()
    def test_missing_weight_contract_rejected(self):
        (self.root/'validation/native-weight-contract.json').unlink()
        with self.assertRaises((OSError,RuntimeError)):w.verify_input_layout()
    def test_rank_directory_symlink_rejected(self):
        original=pathlib.Path.is_symlink
        with patch.object(pathlib.Path,'is_symlink',lambda p:p==self.store/'rank_7' or original(p)),self.assertRaises(RuntimeError):
            w.verify_input_layout()

class ExecutionScope(unittest.TestCase):
    def test_historical_mapping_is_host_only(self):
        with patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':'/experiment'}),self.assertRaisesRegex(RuntimeError,'host-only'):
            w.historical_path('run-manifest.json')
    def test_acceptance_is_host_only(self):
        from accept_control13 import accept
        with patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':'/experiment'}),self.assertRaisesRegex(RuntimeError,'host-only'):
            accept(pathlib.Path('/unused'),'sha','commit')
    def test_deployment_requires_full_r1_scope(self):
        from package_inventory13 import require_full_r1
        verdict=dict(verdict='approved',gate='R1',scope='full-runtime',code_commit='c'*40,manifest_sha256='a'*64)
        require_full_r1(verdict,'c'*40,'a'*64)
        for changes in ({'scope':'path-boundary'},{'gate':'R2'},{'verdict':'approved_for_path_boundary'},
                        {'scope':None},{'code_commit':'d'*40},{'manifest_sha256':'b'*64},
                        {'not_a_full_r1':True},{'approved_for_path_boundary':True}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                require_full_r1(dict(verdict,**changes),'c'*40,'a'*64)

if __name__=='__main__':unittest.main()
