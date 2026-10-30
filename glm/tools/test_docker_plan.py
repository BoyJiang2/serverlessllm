import copy
import unittest
from unittest.mock import Mock, patch
from docker_plan import IMAGE, ROOT, compile_spec, preflight


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.spec = dict(run_id='sllm-debug-20261028T080000Z-12345678', role='probe', image_id=IMAGE,
                         memory='2g', cpus=2, env={}, argv=['python3', '--version'],
                         mounts=[dict(source=str(ROOT/'checkpoint'), destination='/checkpoint', readonly=True)])

    def test_explicit_mount_and_identity_without_gpu_or_network(self):
        args = compile_spec(self.spec)
        self.assertIn('--pull=never', args)
        self.assertIn('--label=sllm.purpose=DEBUG-UNREVIEWED', args)
        self.assertIn('--mount=type=bind,src='+str(ROOT/'checkpoint')+',dst=/checkpoint,readonly', args)
        self.assertNotIn('--gpus=all', args)

    def test_wrong_image_and_formal_identity_rejected(self):
        for key, value in [('image_id', 'vllm/vllm-openai:latest'), ('run_id', 'sllm-glm51fp8-20261028T080000Z-12345678')]:
            spec = copy.deepcopy(self.spec); spec[key] = value
            with self.assertRaises(ValueError): compile_spec(spec)

    def test_asset_writes_and_unknown_privilege_rejected(self):
        self.spec['mounts'][0]['readonly'] = False
        with self.assertRaises(ValueError): compile_spec(self.spec)
        self.spec['privileged'] = True
        with self.assertRaises(ValueError): compile_spec(self.spec)

    def test_duplicate_and_escape_mount_rejected(self):
        self.spec['mounts'] *= 2
        with self.assertRaises(ValueError): compile_spec(self.spec)
        self.spec['mounts'] = [dict(source=str(ROOT/'../other'), destination='/x', readonly=True)]
        with self.assertRaises(ValueError): compile_spec(self.spec)

    def test_preflight_checks_uid_before_docker(self):
        with patch('docker_plan.os.getuid', return_value=0, create=True), patch('docker_plan.subprocess.check_output') as docker:
            with self.assertRaises(ValueError): preflight(self.spec)
            docker.assert_not_called()

    def test_preflight_rejects_wrong_image(self):
        with patch('docker_plan.os.getuid', return_value=2147, create=True), patch('docker_plan.subprocess.check_output', return_value='sha256:wrong'):
            with self.assertRaises(ValueError): preflight(self.spec)

    def test_preflight_image_only_probe_does_not_execute(self):
        self.spec['mounts'] = []
        with patch('docker_plan.os.getuid', return_value=2147, create=True), patch('docker_plan.subprocess.check_output', return_value=IMAGE) as docker:
            self.assertEqual(preflight(self.spec)['image_id'], IMAGE)
            self.assertEqual(docker.call_args.args[0][:3], ['docker', 'image', 'inspect'])

    def test_preflight_rejects_symlink_before_stat(self):
        path, resolved = Mock(), Mock()
        path.resolve.return_value = resolved
        with patch('docker_plan.os.getuid', return_value=2147, create=True), \
             patch('docker_plan.subprocess.check_output', return_value=IMAGE), \
             patch('docker_plan.Path', return_value=path):
            with self.assertRaises(ValueError): preflight(self.spec)
        path.stat.assert_not_called()

    def test_preflight_rejects_missing_mount(self):
        path = Mock()
        path.resolve.side_effect = FileNotFoundError('missing source')
        with patch('docker_plan.os.getuid', return_value=2147, create=True), \
             patch('docker_plan.subprocess.check_output', return_value=IMAGE), \
             patch('docker_plan.Path', return_value=path):
            with self.assertRaises(FileNotFoundError): preflight(self.spec)
        path.stat.assert_not_called()


if __name__ == '__main__': unittest.main()
