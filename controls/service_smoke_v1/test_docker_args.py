import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import types
import unittest
from docker_args import docker_create_args, HOST, RUN_ALIAS

ROOT=Path(__file__).resolve().parents[2]
ACTIVE=ROOT/'runtime/active'
PACKET=ROOT/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313'


class MetadataPath:
    """Only stand in for remote existence checks; do not claim local mounts exist."""
    def __init__(self,path):self.path=PurePosixPath(path)
    def exists(self):return True
    def resolve(self):return self
    def is_relative_to(self,other):return self.path.is_relative_to(other.path)


class ArgRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        receipt=json.loads((PACKET/'executed-source-archive.json').read_text())
        cls.source_hashes={x['path']:x['sha256'] for x in receipt['files_checked_before_and_after']}
        for name in ('workspace.py','launch_control13.py','run-manifest.json','initial-ledger.json','formal_gate.py','attempt_state.py'):
            assert hashlib.sha256((ACTIVE/name).read_bytes()).hexdigest()==cls.source_hashes[name], name
        sys.path.insert(0,str(ACTIVE))
        loader=importlib.util.spec_from_file_location('frozen_workspace_for_args',ACTIVE/'workspace.py')
        cls.workspace=importlib.util.module_from_spec(loader);loader.loader.exec_module(cls.workspace)
        cls.manifest=json.loads((ACTIVE/'run-manifest.json').read_text())
        tree=ast.parse((ACTIVE/'launch_control13.py').read_text())
        cls.block=[];inside=False
        for node in tree.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='args' for t in node.targets):inside=True
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='dispatch_monotonic_ns' for t in node.targets):break
            if inside:cls.block.append(node)
        assert cls.block
        cls.specs=[]
        for name in ('formal-exact-index.json','normal-exact-index.json'):
            index=json.loads((PACKET/name).read_text());entries=index['files'] if isinstance(index,dict) else index
            for e in entries:
                if e['path'].endswith(('headformal13-dispatch.json','formal13-dispatch.json','headservice13a-dispatch.json','service13a-dispatch.json')):
                    path=PACKET/e.get('local_path',e.get('local'));raw=path.read_bytes()
                    assert hashlib.sha256(raw).hexdigest()==e['sha256']
                    cls.specs.append(json.loads(raw)['spec'])
        assert len(cls.specs)==4

    def compare(self,spec):
        mounts=self.workspace.container_mounts()
        ns=dict(spec=spec,run=self.manifest['run_id'],manifest=self.manifest,HOST=HOST,RUN_ALIAS=RUN_ALIAS,
                container_mounts=lambda:mounts,pathlib=types.SimpleNamespace(Path=MetadataPath,PurePosixPath=PurePosixPath))
        exec(compile(ast.Module(body=self.block,type_ignores=[]),'<frozen-C23-argv-block>','exec'),ns)
        actual=docker_create_args(spec,self.manifest['run_id'],mounts,self.manifest['image_id'])
        self.assertEqual(json.dumps(actual,separators=(',',':')).encode(),json.dumps(ns['args'],separators=(',',':')).encode())

    def test_four_observed_c23_specs_match_executed_source_baseline(self):
        for spec in self.specs:
            with self.subTest(name=spec['name']):self.compare(spec)

    def test_optional_monitor_and_export_branches_match_baseline(self):
        spec=copy.deepcopy(self.specs[0]);spec['host_monitor']=True;spec['raw_export_hook']=True
        spec['env']['SLLM_EXPORT_RAW_STORE']='/experiment/store/vllm/glm51-fp8'
        self.compare(spec)


if __name__=='__main__':unittest.main()
