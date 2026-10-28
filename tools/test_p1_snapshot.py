import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from p1_snapshot import snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[2]/'outputs'
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.source.mkdir()
        (self.source/'driver.py').write_bytes(b'print(1)\n')
        self.row = dict(path='driver.py', bytes=9, sha256=hashlib.sha256(b'print(1)\n').hexdigest())

    def seal(self):
        data = json.dumps(dict(files=[self.row])).encode()
        (self.source/'control-input-manifest.json').write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_copy_retains_source_and_rejects_overwrite(self):
        sha = self.seal()
        out = self.root/'archive'
        self.assertTrue(snapshot(self.source, out, sha)['source_retained'])
        self.assertEqual((out/'driver.py').read_bytes(), (self.source/'driver.py').read_bytes())
        with self.assertRaises(ValueError):
            snapshot(self.source, out, sha)

    def test_tamper_rejected_before_output_created(self):
        sha = self.seal()
        (self.source/'driver.py').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            snapshot(self.source, self.root/'archive', sha)
        self.assertFalse((self.root/'archive').exists())

    def test_escape_rejected(self):
        self.row['path'] = '../driver.py'
        with self.assertRaises(ValueError):
            snapshot(self.source, self.root/'archive', self.seal())


if __name__ == '__main__':
    unittest.main()
