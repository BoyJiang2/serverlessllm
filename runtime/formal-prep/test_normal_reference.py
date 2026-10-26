"""Normal-flags controls cannot use absent or partial G1 evidence."""
import hashlib,json,pathlib,tempfile,unittest
from formal_gate import verify_normal_reference

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()

class NormalReference(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name);run='sllm-glm51fp8-20261026T000000Z-12345678';self.reference=self.root/'validation/controls'/run
        self.acceptance=dict(status='G1 acceptance passed; no formal samples',run_id=run,code_commit='a'*40,
                            manifest_sha256='b'*64,ray_only_controls=2,native_lifecycles_per_format=2,pool_initializations=1)
        self.config=dict(g1_reference_run_id=run,g1_reference_code_commit='a'*40,g1_reference_manifest_sha256='b'*64,
                         g1_acceptance_sha256=save(self.reference/'G1-acceptance.json',self.acceptance),full_a4_proofs={})
        for kind in ('serverless_llm','safetensors'):
            name=kind+'/model-result.json'
            proof=dict(status='A4 native HTTP lifecycle checks passed',cycles=2,weight_format=kind,
                       weight_audit_performed=True,rank_coverage=[dict(cycle=cycle,rank=rank) for cycle in (1,2) for rank in range(8)])
            self.config['full_a4_proofs'][kind]=dict(path=name,sha256=save(self.reference/name,proof))

    def test_complete_bound_reference(self):
        self.assertEqual(verify_normal_reference(self.config,self.root),self.acceptance)

    def test_g1_not_passed_rejected(self):
        self.acceptance['status']='failed'
        self.config['g1_acceptance_sha256']=save(self.reference/'G1-acceptance.json',self.acceptance)
        with self.assertRaisesRegex(ValueError,'actual complete G1'):
            verify_normal_reference(self.config,self.root)

    def test_different_g1_manifest_rejected(self):
        self.config['g1_reference_manifest_sha256']='c'*64
        with self.assertRaisesRegex(ValueError,'actual complete G1'):
            verify_normal_reference(self.config,self.root)

    def test_reference_run_escape_rejected(self):
        self.config['g1_reference_run_id']='../other-run'
        with self.assertRaisesRegex(ValueError,'invalid G1 reference run'):
            verify_normal_reference(self.config,self.root)

    def test_missing_rank_rejected(self):
        row=self.config['full_a4_proofs']['serverless_llm'];path=self.reference/row['path'];proof=json.loads(path.read_text())
        proof['rank_coverage'].pop();row['sha256']=save(path,proof)
        with self.assertRaisesRegex(ValueError,'eight-rank coverage'):
            verify_normal_reference(self.config,self.root)

    def test_unaudited_proof_rejected(self):
        row=self.config['full_a4_proofs']['serverless_llm'];path=self.reference/row['path'];proof=json.loads(path.read_text())
        proof['weight_audit_performed']=False;row['sha256']=save(path,proof)
        with self.assertRaisesRegex(ValueError,'full audited A4'):
            verify_normal_reference(self.config,self.root)

    def test_reference_escape_rejected(self):
        self.config['full_a4_proofs']['serverless_llm']['path']='../../escape.json'
        with self.assertRaisesRegex(ValueError,'escaped evidence root'):
            verify_normal_reference(self.config,self.root)

    def test_changed_proof_bytes_rejected(self):
        row=self.config['full_a4_proofs']['safetensors'];path=self.reference/row['path'];path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'changed frozen input'):
            verify_normal_reference(self.config,self.root)

if __name__=='__main__':unittest.main()
