"""Cache initialization tests; no GPU/framework imports."""
import pathlib,tempfile,unittest
from prepare_host13 import prepare_home
from compilation_guard import CACHE_DIRECTORIES

class CachePreparation(unittest.TestCase):
    def test_all_guarded_cache_groups_start_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);proof=prepare_home(root)
            self.assertEqual({p.name for p in (root/'native-home').iterdir()},set(CACHE_DIRECTORIES))
            self.assertTrue(all(not list((root/'native-home'/name).iterdir()) for name in CACHE_DIRECTORIES))
            self.assertEqual(proof['cache_reused_bytes'],0)

    def test_prior_generated_absolute_paths_are_not_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);old=root/'old-cache';old.mkdir()
            generated=old/'compiled.py';data=b"cache_key='/experiment/native-home/inductor/a.best_config'\n";generated.write_bytes(data)
            run=root/'new-run';run.mkdir();prepare_home(run)
            self.assertEqual(list((run/'native-home').rglob('*.py')),[])
            self.assertEqual(generated.read_bytes(),data)

    def test_existing_home_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);prepare_home(root)
            path=root/'native-home/inductor/evidence';path.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):prepare_home(root)
            self.assertEqual(path.read_bytes(),b'keep')

if __name__=='__main__':unittest.main()
