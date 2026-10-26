"""Canonical path boundary regressions; no filesystem writes outside temp space."""
import json,pathlib,tempfile,unittest
from unittest.mock import patch
import workspace as w

class Workspace(unittest.TestCase):
    def test_host_and_container_paths(self):
        for value in (str(w.HOST),'/experiment'):
            with self.subTest(value=value),patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':value}):
                self.assertEqual(w.run_root(),pathlib.Path(value)/'validation/controls'/w.manifest()['run_id'])
                self.assertFalse(w.run_root().is_relative_to(w.archive()))
    def test_old_root_and_arbitrary_override_rejected(self):
        for value in ('/tmp/test','/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57','/experiment/../archive'):
            with self.subTest(value=value),patch.dict(w.os.environ,{'SLLM_CONTROL_WORKSPACE':value}),self.assertRaises(RuntimeError):w.workspace()
    def test_escaped_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for value in ('../archive','sllm-glm51fp8-20261026T030041Z-5b688078/../archive','other-run'):
                (root/'run-manifest.json').write_text(json.dumps({'run_id':value}))
                with self.subTest(value=value),patch.object(w,'SOURCE',root),self.assertRaises(RuntimeError):w.run_root()

if __name__=='__main__':unittest.main()
