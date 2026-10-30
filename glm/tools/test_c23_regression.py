import json
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c23_regression as c


class C23History(unittest.TestCase):
    """Gate for any formal-code change: frozen C23 reproduces history, and the working tree equals frozen."""
    @classmethod
    def setUpClass(cls):
        cls.report = c.regression()

    def test_frozen_tree_reproduces_the_recorded_dispatch_specs(self):
        self.assertEqual(self.report['frozen_specs_equal_recorded'], {name: True for name in c.SPEC_NAMES})
        self.assertTrue(self.report['gate_sha256_consistent'])

    def test_formal13_mounts_equal_the_recorded_container(self):
        mounts = self.report['formal13_mounts']
        self.assertEqual((mounts['recorded'], mounts['derived'], mounts['missing'], mounts['extra']), (36, 36, [], []))

    def test_working_tree_keeps_spec_mounts_and_docker_argv(self):
        self.assertEqual(self.report['current_equals_frozen'], dict(specs=True, mounts=True, argv=True, argv_block=True))
        self.assertTrue(c.passed(self.report))


class Detection(unittest.TestCase):
    """The regression must fail when a template or the argv block changes."""
    def mutated(self, name, old, new):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tree = pathlib.Path(directory.name)/'active'
        shutil.copytree(c.REPO/'runtime/active', tree, ignore=shutil.ignore_patterns('__pycache__'))
        text = (tree/name).read_text(encoding='utf-8')
        self.assertIn(old, text)
        (tree/name).write_text(text.replace(old, new, 1), encoding='utf-8')
        return c.regression(current=tree)

    def test_changed_template_is_caught(self):
        report = self.mutated('headservice13a.json', '"cpus": 4', '"cpus": 5')
        self.assertFalse(report['current_equals_frozen']['specs'])
        self.assertFalse(c.passed(report))

    def test_changed_argv_block_is_caught(self):
        report = self.mutated('launch_control13.py', "'--shm-size=32g'", "'--shm-size=16g'")
        self.assertFalse(report['current_equals_frozen']['argv'])
        self.assertFalse(report['current_equals_frozen']['argv_block'])

    def test_mount_parser_reads_readonly_flags(self):
        rows = c.argv_mounts(['--mount=type=bind,src=/a,dst=/b,readonly', '--mount=type=bind,src=/c,dst=/r', '--gpus=all'])
        self.assertEqual(rows, {('/a', '/b', True), ('/c', '/r', False)})


if __name__ == '__main__':
    unittest.main()
