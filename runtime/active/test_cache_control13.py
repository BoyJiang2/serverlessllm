"""Tiny synthetic vectors test cache acceptance; never claim real cache evidence."""
import copy,gzip,hashlib,json,pathlib,tempfile,unittest
from accept_control13 import validate_cache
from cache_contract13 import frozen_paths
from unittest.mock import patch

class CacheAcceptance(unittest.TestCase):
    def fixture(self,root):
        dest=root/'cache_g1hot13'; dest.mkdir()
        inputs=[dict(path=f'model-{i:05}.safetensors' if i<142 else f'metadata{i}.json',bytes=1) for i in range(148)]
        manifest=root/'cache-inputs13.json'; manifest.write_text(json.dumps(dict(files=inputs)))
        rows=[]; eviction=[]
        for i,item in enumerate(inputs):
            for hot,value in [(True,b'\x01'),(False,b'\x00')]:
                name=f'{i}-{hot}.gz'; (dest/name).write_bytes(gzip.compress(value))
                row=dict(file=item['path'],source_path='/experiment/checkpoint/'+item['path'],bytes=1,pages=1,resident_pages=int(hot),page_size=4096,vector=name,vector_sha256=hashlib.sha256(value).hexdigest())
                (rows if hot else eviction).append(row)
        placement=dict(node='node1',excluded_store_cpu_node='node0',reader_cpu_affinity=[1],required_bytes=148,margin_bytes=64*1024**3,capacity_proxy_bytes=64*1024**3+148)
        bootstrap=root/'service-bootstrap-service13a'; bootstrap.mkdir()
        (bootstrap/'store-numa-launch.json').write_text(json.dumps(dict(node='node0',target_cpu_affinity=[0])))
        (dest/'hot-placement-final.json').write_text(json.dumps(placement))
        idle=[dict(read_inflight=0,write_inflight=0,monotonic_ns=t) for t in (2,3)]
        initial=dict(files=rows,verification_begin_ns=4,verification_complete_ns=5,read_threads_joined_ns=1,read_idle_receipt=idle)
        (dest/'initial-scan.json').write_text(json.dumps(initial))
        reads=[dict(file=r['source_path'],bytes_read=r['bytes'],cpu_begin=1,cpu_end=1,mode='hot') for r in rows]
        (dest/'initial-reads.json').write_text(json.dumps(reads))
        (dest/'preparation.jsonl').write_text('\n'.join(json.dumps(r) for r in reads))
        summary=dict(input_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),mode='hot',kind='safetensors_inputs',cache_condition_passed=True,hot_workers=8,repair_passes=0,repair_history=[],hot_placement=placement,read_idle_receipt=idle,total_cache_admission_end_ns=100,total_cache_admission_begin_ns=0,verification_complete_ns=5,files=rows)
        (dest/'summary.json').write_text(json.dumps(summary)); (dest/'eviction-mincore.jsonl').write_text('\n'.join(json.dumps(r) for r in eviction))
        return dest,summary
    def test_positive(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d); self.fixture(root); self.assertEqual(validate_cache(root)['inputs'],148)
    def test_missing_file(self): self.bad(lambda s:s['files'].pop())
    def test_partial_residency(self): self.bad(lambda s:s['files'][0].update(resident_pages=0))
    def test_missing_pages(self): self.bad(lambda s:s['files'][0].update(pages=2))
    def test_over_budget(self): self.bad(lambda s:s.update(total_cache_admission_end_ns=601_000_000_000))
    def test_corrupt_vector(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d); dest,s=self.fixture(root); (dest/s['files'][0]['vector']).write_bytes(gzip.compress(b'\x00'))
            with self.assertRaises(RuntimeError): validate_cache(root)
    def test_initial_read_short(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d); dest,s=self.fixture(root); rows=[json.loads(line) for line in (dest/'preparation.jsonl').read_text().splitlines()]; rows[0]['bytes_read']=0
            (dest/'preparation.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            with self.assertRaises(RuntimeError):validate_cache(root)
    def test_read_idle_nonzero(self): self.bad(lambda s:s['read_idle_receipt'][-1].update(read_inflight=1))
    def test_missing_repair(self): self.bad(lambda s:s.update(repair_passes=1))
    def test_positive_repair_chain(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d); dest,s=self.fixture(root)
            initial=json.loads((dest/'initial-scan.json').read_text()); initial['files'][0]['resident_pages']=0
            vector=dest/initial['files'][0]['vector']; vector.write_bytes(gzip.compress(b'\x00')); initial['files'][0]['vector_sha256']=hashlib.sha256(b'\x00').hexdigest()
            (dest/'initial-scan.json').write_text(json.dumps(initial))
            repair=dest/'repair_1'; repair.mkdir(); final=copy.deepcopy(s['files'])
            for i,row in enumerate(final):
                row.update(vector=f'repair_1/{i}.gz',vector_sha256=hashlib.sha256(b'\x01').hexdigest()); (dest/row['vector']).write_bytes(gzip.compress(b'\x01'))
            selected=initial['files'][0]['source_path']
            record=dict(pass_number=1,begin_ns=6,selected_files=[selected],selected_bytes=1,prior_missing=[initial['files'][0]])
            (repair/'selection.json').write_text(json.dumps(record))
            record.update(reads=[dict(file=selected,bytes_read=1,cpu_begin=1,cpu_end=1)],read_threads_joined_ns=7)
            (repair/'reads.json').write_text(json.dumps(record))
            record.update(read_idle_receipt=[dict(read_inflight=0,write_inflight=0,monotonic_ns=t) for t in (8,9)],verification_begin_ns=10,verification_complete_ns=11,files=final,cache_condition_passed=True)
            (repair/'summary.json').write_text(json.dumps(record)); s.update(repair_history=[record],repair_passes=1,files=final,verification_complete_ns=11); (dest/'summary.json').write_text(json.dumps(s))
            self.assertEqual(validate_cache(root)['repair_passes'],1)

    def bad(self,change):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d); dest,s=self.fixture(root); change(s); (dest/'summary.json').write_text(json.dumps(s))
            with self.assertRaises(RuntimeError): validate_cache(root)

class InputClosure(unittest.TestCase):
    @patch("workspace.workspace")
    def test_sizes_and_metadata_identity(self,mock_workspace):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory); mock_workspace.return_value=base; rows=[]
            for i in range(148):
                name=f'model-{i:05}.safetensors' if i<142 else f'metadata{i}.json'
                (base/name).write_bytes(b'x'); row=dict(path=name,bytes=1)
                if i>=142: row.update(hash_kind='sha256',expected_hash=hashlib.sha256(b'x').hexdigest())
                else: row['expected_sha256']=hashlib.sha256(b'x').hexdigest()
                rows.append(row)
            evidence=base/'protocol'; evidence.mkdir()
            official=evidence/'run-manifest.json'; official.write_text(json.dumps(dict(weight_files=[dict(rfilename=r['path'],size=r['bytes'],lfs=dict(sha256=r['expected_sha256'])) for r in rows[:142]])))
            verified=base/'VERIFIED.json'; verified.write_text(json.dumps(dict(hashes=[[r['path'],r['bytes'],r['expected_sha256'],True] for r in rows[:142]])))
            manifest=base/'inputs.json'; manifest.write_text(json.dumps(dict(files=rows,source_manifest_sha256=hashlib.sha256(official.read_bytes()).hexdigest(),verified_manifest_sha256=hashlib.sha256(verified.read_bytes()).hexdigest())))
            self.assertEqual(len(frozen_paths(base,manifest)),148)
            (base/rows[-1]['path']).write_bytes(b'y')
            with self.assertRaisesRegex(RuntimeError,'metadata hash'): frozen_paths(base,manifest)
            (base/rows[-1]['path']).write_bytes(b'x')
            (base/rows[0]['path']).write_bytes(b'xx')
            with self.assertRaisesRegex(RuntimeError,'size differs'): frozen_paths(base,manifest)
    def test_incomplete_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory); manifest=base/'inputs.json'; manifest.write_text('{"files":[]}')
            with self.assertRaises(RuntimeError): frozen_paths(base,manifest)

if __name__=='__main__': unittest.main()
