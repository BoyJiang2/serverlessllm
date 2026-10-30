"""Import-origin/hash checks with synthetic modules; no framework or GPU imports."""
import hashlib,pathlib,tempfile,types,unittest
from unittest.mock import patch
from run_store_server13 import verify_store_imports

class ImportIdentity(unittest.TestCase):
    def fixture(self,root):
        names=('sllm_store','sllm_store.server','sllm_store._C','sllm_store._checkpoint_store')
        modules={};expected={}
        for number,name in enumerate(names):
            path=root/str(number);path.write_bytes(name.encode())
            modules[name]=types.SimpleNamespace(__file__=str(path));expected[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        server=pathlib.Path(modules['sllm_store.server'].__file__)
        server.write_text('async def serve():\n    return 1\n')
        scope={};exec(compile(server.read_bytes(),str(server),'exec'),scope)
        modules['sllm_store.server'].serve=scope['serve']
        expected['sllm_store.server']=hashlib.sha256(server.read_bytes()).hexdigest()
        return modules,expected
    def test_exact_selected_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root)
            with patch('run_store_server13.importlib.import_module',side_effect=modules.get):
                self.assertEqual({k:v['sha256'] for k,v in verify_store_imports(expected,root).items()},expected)
    def test_server_preloads_native_dependencies_before_direct_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root);loaded=[]
            def loader(name):
                if name in ('sllm_store._C','sllm_store._checkpoint_store') and 'sllm_store.server' not in loaded:
                    raise ImportError('libglog.so.1 requires the existing server preload')
                loaded.append(name)
                return modules[name]
            with patch('run_store_server13.importlib.import_module',side_effect=loader):
                self.assertEqual(set(verify_store_imports(expected,root)),set(expected))
    def test_changed_binary_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root);pathlib.Path(modules['sllm_store._checkpoint_store'].__file__).write_bytes(b'changed binary metadata')
            with patch('run_store_server13.importlib.import_module',side_effect=modules.get),self.assertRaisesRegex(RuntimeError,'hash differs'):
                verify_store_imports(expected,root)
    def test_wrong_source_tree_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root);modules['sllm_store'].__file__=str(root/'bundled/__init__.py')
            with patch('run_store_server13.importlib.import_module',side_effect=modules.get),self.assertRaisesRegex(RuntimeError,'origin differs'):
                verify_store_imports(expected,root)
    def test_stale_loaded_bytecode_rejected_despite_matching_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root)
            modules['sllm_store.server'].serve=lambda:2
            with patch('run_store_server13.importlib.import_module',side_effect=modules.get),self.assertRaisesRegex(RuntimeError,'bytecode differs'):
                verify_store_imports(expected,root)

    def test_bytecode_filename_must_match_actual_module_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root);module=modules['sllm_store.server']
            module.serve.__code__=module.serve.__code__.replace(co_filename='historical-unreviewed.py')
            with patch('run_store_server13.importlib.import_module',side_effect=modules.get),self.assertRaisesRegex(RuntimeError,'bytecode differs'):
                verify_store_imports(expected,root)

    def test_event_trace_exact_source_and_loaded_function_match(self):
        from run_store_server13 import verify_event_trace
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);path=root/'event_trace.py'
            path.write_text('def record_event(name, **values):\n    return name, values\n')
            scope={};exec(compile(path.read_bytes(),str(path),'exec'),scope)
            module=types.SimpleNamespace(__file__=str(path),record_event=scope['record_event'])
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            with patch('run_store_server13.importlib.import_module',return_value=module):
                self.assertTrue(verify_event_trace(digest,root)['source_code_match'])
                with self.assertRaisesRegex(RuntimeError,'source hash'):verify_event_trace('wrong',root)
                module.record_event=lambda *args:None
                with self.assertRaisesRegex(RuntimeError,'bytecode'):verify_event_trace(digest,root)

    def test_overlay_is_exact_single_statement_change_and_frozen_hash(self):
        source=pathlib.Path(__file__).resolve().parent
        patched=(source/'store_server13.py').read_bytes()
        new=b'await asyncio.shield(server.wait_for_termination())'
        old=b'await server.wait_for_termination()'
        self.assertEqual(patched.count(new),1)
        base=patched.replace(new,old)
        self.assertEqual(hashlib.sha256(base).hexdigest(),'c0de1b5843767c8399a016a21a2b536259bcf13056a2f8b36255a811009f303c')
        self.assertEqual(len(patched)-len(base),16)
        import json,workspace
        config=json.loads((source/'run-manifest.json').read_text())
        self.assertEqual(config['store_import_sha256']['sllm_store.server'],hashlib.sha256(patched).hexdigest())
        mounts=workspace.container_mounts()
        overlay=[r for r in mounts if r['destination']=='/experiment/code/Store-source-v7/sllm_store/server.py']
        self.assertEqual(len(overlay),1);self.assertTrue(overlay[0]['readonly'])
        self.assertEqual(overlay[0]['source'],str(workspace.HOST/'code/experiment/store_server13.py'))
        parent=next(r for r in mounts if r['destination']=='/experiment/code/Store-source-v7')
        self.assertLess(mounts.index(parent),mounts.index(overlay[0]))

    def test_missing_overlay_or_old_hash_cannot_fall_back(self):
        for missing in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory);modules,expected=self.fixture(root)
                if missing:pathlib.Path(modules['sllm_store.server'].__file__).unlink()
                else:expected['sllm_store.server']='0'*64
                with patch('run_store_server13.importlib.import_module',side_effect=modules.get),self.assertRaises((FileNotFoundError,RuntimeError)):
                    verify_store_imports(expected,root)

    def test_unexpected_module_never_imported(self):
        with patch('run_store_server13.importlib.import_module') as loader,self.assertRaisesRegex(RuntimeError,'import set'):
            verify_store_imports({'other':'0'*64})
        loader.assert_not_called()

if __name__=='__main__':unittest.main()
