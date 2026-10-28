import hashlib
from pathlib import Path
import tempfile
import unittest
import urllib.request
from fetch_original_image import PublicRedirect, digest, main


class FetchTests(unittest.TestCase):
    def test_redirect_strips_registry_authorization(self):
        request = urllib.request.Request('https://registry-1.docker.io/v2/blob',
                                         headers={'Authorization': 'Bearer public-scoped-test', 'Range': 'bytes=5-'})
        result = PublicRedirect().redirect_request(request, None, 302, 'redirect', {}, 'https://cdn.example/blob')
        self.assertIsNone(result.get_header('Authorization'))
        self.assertEqual(result.get_header('Range'), 'bytes=5-')

    def test_plaintext_redirect_rejected(self):
        with self.assertRaises(ValueError):
            PublicRedirect().redirect_request(urllib.request.Request('https://registry-1.docker.io/v2/blob'),
                                               None, 302, 'redirect', {}, 'http://cdn.example/blob')

    def test_wrong_manifest_fails_before_network_or_output(self):
        output = Path(__file__).resolve().parents[2]/'outputs'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            path = Path(temporary)/'manifest.json'; path.write_bytes(b'{}')
            with self.assertRaises(ValueError): main(path, Path(temporary)/'result')
            self.assertFalse((Path(temporary)/'result').exists())
            self.assertEqual(digest(path), hashlib.sha256(b'{}').hexdigest())


if __name__ == '__main__': unittest.main()
