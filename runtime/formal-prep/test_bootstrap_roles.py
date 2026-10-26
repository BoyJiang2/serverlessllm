"""Execute only the actual bootstrap prelude; no Ray, Store or model calls."""
import ast
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class BootstrapRoles(unittest.TestCase):
    def exercise(self,label,purpose='formal-continuation'):
        source=pathlib.Path(__file__).with_name('bootstrap_control13.py')
        body=ast.parse(source.read_text()).body
        stop=next(i for i,n in enumerate(body) if isinstance(n,ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id=='env' for t in n.targets))
        with tempfile.TemporaryDirectory() as temporary:
            control=pathlib.Path(temporary)
            (control/'run-manifest.json').write_text(json.dumps(dict(
                scope='formal continuation of original forty IDs',execution_purpose=purpose)))
            calls=[]
            modules=dict(workspace=types.SimpleNamespace(run_root=lambda:control,
                verify_input_layout=lambda:calls.append('layout')),
                formal_gate=types.SimpleNamespace(verify_normal_reference=lambda *a:calls.append('normal-reference'),
                    g1_reference_root=lambda *a:control,verify_gate=lambda:calls.append('formal-gate')))
            with patch.dict(sys.modules,modules),patch.object(sys,'argv',['bootstrap_control13.py',label]):
                exec(compile(ast.Module(body=body[:stop],type_ignores=[]),str(source),'exec'),{})
            self.assertTrue((control/('service-bootstrap-'+label)).is_dir())
            return calls

    def test_normal_control_reuses_existing_service_role(self):
        self.assertEqual(self.exercise('service13a'),['layout','normal-reference'])

    def test_formal_role_requires_current_gate_before_output(self):
        self.assertEqual(self.exercise('formal13'),['layout','formal-gate'])

    def test_unreviewed_role_rejected(self):
        with self.assertRaises(AssertionError):self.exercise('normal13')

    def test_wrong_purpose_rejected(self):
        with self.assertRaises(AssertionError):self.exercise('formal13','G1')


if __name__=='__main__':unittest.main()
