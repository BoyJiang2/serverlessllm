"""Cache initialization tests; no GPU/framework imports."""
import pathlib,tempfile,unittest,json,hashlib,copy
from unittest.mock import patch
import prepare_host13
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

class SeedPreparation(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        base=pathlib.Path(temporary.name);source=base/'sllm-glm51fp8-20261026T225201Z-02cd4cdc';old=source/'native-home';old.mkdir(parents=True)
        for directory in CACHE_DIRECTORIES:(old/directory).mkdir()
        from compilation_guard_v6 import MUTABLE_LOG
        log=old/MUTABLE_LOG;log.parent.mkdir(parents=True);log.write_bytes(b'previous mutable log')
        artifacts={}
        for i in range(583):
            name=f'inductor/cache{i}.so';data=str(i).encode();(old/name).write_bytes(data)
            artifacts[name]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        active=pathlib.Path(__file__).parent
        config=json.loads((active/'run-manifest.json').read_text());config['run_id']='sllm-glm51fp8-20261027T004500Z-12345678';config['jit_seed_runtime_versions']=dict(torch='t',vllm='v',cuda='c')
        original=copy.deepcopy(config);original['run_id']=source.name
        def save(path,value):
            path.write_text(json.dumps(value));return hashlib.sha256(path.read_bytes()).hexdigest()
        oldsha=save(source/'run-manifest.json',original)
        qsha=save(source/'normal-jit-qualification.json',dict(status='normal native lifecycle and warm artifact qualification',run_id=source.name,code_commit='a'*40,manifest_sha256='b'*64,artifacts=artifacts))
        cpusha=save(source/'service13a-cpu-import-preflight.json',config['jit_seed_runtime_versions'])
        seed=dict(source_run_id=source.name,source_code_commit='a'*40,source_manifest_sha256='b'*64,source_run_manifest_sha256=oldsha,qualification_sha256=qsha,source_cpu_import_sha256=cpusha,runtime_versions=config['jit_seed_runtime_versions'],artifacts=artifacts)
        metadata=base/'code';metadata.mkdir();config['jit_seed_contract_sha256']=save(metadata/'jit-seed-contract.json',seed)
        target=base/'sllm-glm51fp8-20261027T004600Z-23456789';target.mkdir()
        return config,seed,source,metadata,target,save

    def test_exact_immutable_seed_copied_without_shared_writes(self):
        config,seed,source,metadata,target,save=self.fixture()
        with patch.object(prepare_host13,'SOURCE',metadata):proof=prepare_home(target,config)
        self.assertEqual(proof['seed_files'],583);self.assertGreater(proof['cache_reused_bytes'],0)
        from compilation_guard_v6 import MUTABLE_LOG,artifact_snapshot
        self.assertEqual(artifact_snapshot(target/'native-home'),seed['artifacts'])
        self.assertEqual((target/'native-home'/MUTABLE_LOG).read_bytes(),b'')
        changed=target/'native-home/inductor/cache0.so';changed.write_bytes(b'current change')
        self.assertEqual((source/'native-home/inductor/cache0.so').read_bytes(),b'0')

    def test_changed_missing_extra_or_qualification_drift_rejects(self):
        for defect in ('asset','missing','extra','qualification','compatibility','seedhash','path'):
            config,seed,source,metadata,target,save=self.fixture()
            if defect=='asset':(source/'native-home/inductor/cache0.so').write_bytes(b'bad')
            elif defect=='missing':(source/'native-home/inductor/cache0.so').unlink()
            elif defect=='extra':(source/'native-home/inductor/extra.so').write_bytes(b'extra')
            elif defect=='qualification':(source/'normal-jit-qualification.json').write_bytes(b'{}')
            elif defect=='compatibility':config['image_id']='different-image'
            elif defect=='seedhash':config['jit_seed_contract_sha256']='wrong'
            else:
                seed['artifacts']['../escape']=seed['artifacts'].pop('inductor/cache0.so')
                config['jit_seed_contract_sha256']=save(metadata/'jit-seed-contract.json',seed)
            with patch.object(prepare_host13,'SOURCE',metadata),self.subTest(defect=defect),self.assertRaises((RuntimeError,ValueError)):prepare_home(target,config)
            self.assertFalse((target/'escape').exists())

    def test_seed_symlink_rejects_before_copy(self):
        config,seed,source,metadata,target,save=self.fixture();original=pathlib.Path.is_symlink
        bad=source/'native-home/inductor/cache0.so'
        with patch.object(prepare_host13,'SOURCE',metadata),patch.object(pathlib.Path,'is_symlink',new=lambda p:p==bad or original(p)),self.assertRaisesRegex(RuntimeError,'symlink'):prepare_home(target,config)

    def test_existing_seed_target_is_never_overwritten(self):
        config,seed,source,metadata,target,save=self.fixture();(target/'native-home').mkdir()
        with patch.object(prepare_host13,'SOURCE',metadata),self.assertRaises(FileExistsError):prepare_home(target,config)


if __name__=='__main__':unittest.main()
