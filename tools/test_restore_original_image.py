from pathlib import Path
import subprocess
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from restore_original_image import IMAGE, ensure_image, restore_with_receipt


class RestoreTests(unittest.TestCase):
    def test_exact_image_skips_backup_and_load(self):
        with patch('restore_original_image.subprocess.run',return_value=Mock(returncode=0,stdout=IMAGE+'\n')), \
             patch('restore_original_image.BACKUP') as backup:
            result=ensure_image()
            backup.open.assert_not_called()
            self.assertFalse(result['load_performed'])
            self.assertFalse(result['anchor_created'])

    def test_daemon_failure_is_not_missing_image(self):
        with patch('restore_original_image.subprocess.run',return_value=Mock(returncode=1,stderr='Cannot connect')) as docker:
            with self.assertRaises(RuntimeError):ensure_image()
            self.assertEqual(docker.call_count,1)

    def test_wrong_backup_hash_cannot_load(self):
        output=Path(__file__).resolve().parents[2]/'outputs'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            backup=Path(temporary)/'backup.tar';backup.write_bytes(b'wrong archive')
            with patch('restore_original_image.BACKUP',backup), \
                 patch('restore_original_image.subprocess.run',return_value=Mock(returncode=1,stderr='No such image')) as docker:
                with self.assertRaises(ValueError):ensure_image()
                self.assertEqual(docker.call_count,1)

    def test_failed_init_is_preserved_and_duplicate_rejected(self):
        output=Path(__file__).resolve().parents[2]/'outputs'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            receipt=Path(temporary)/'init.json'
            with patch('restore_original_image.ensure_image',side_effect=subprocess.TimeoutExpired('load',600)):
                with self.assertRaises(subprocess.TimeoutExpired):restore_with_receipt(receipt)
                self.assertIn('failed',receipt.read_text())
                failed=json.loads(receipt.read_text())
                self.assertGreaterEqual(failed['end_ns'],failed['begin_ns'])
            with patch('restore_original_image.ensure_image') as init:
                with self.assertRaises(FileExistsError):restore_with_receipt(receipt)
                init.assert_not_called()

    def test_invalid_debug_request_cannot_restore_image(self):
        from docker_debug import up_with_image
        spec=dict(run_id='sllm-debug-20261028T120000Z-12345678',role='cpu',image_id=IMAGE,
                  memory='1700g',cpus=2,env={},mounts=[],argv=['python3','--version'])
        with patch('restore_original_image.restore_with_receipt') as restore:
            with self.assertRaises(ValueError):up_with_image(spec,Path('unused'),90)
            restore.assert_not_called()


if __name__=='__main__':unittest.main()
