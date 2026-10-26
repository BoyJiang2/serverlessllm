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
        return modules,expected
    def test_exact_selected_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);modules,expected=self.fixture(root)
            with patch('run_store_server13.importlib.import_module',side_effect=modules.get):
                self.assertEqual({k:v['sha256'] for k,v in verify_store_imports(expected,root).items()},expected)
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
    def test_unexpected_module_never_imported(self):
        with patch('run_store_server13.importlib.import_module') as loader,self.assertRaisesRegex(RuntimeError,'import set'):
            verify_store_imports({'other':'0'*64})
        loader.assert_not_called()

if __name__=='__main__':unittest.main()
