"""Exercise the actual RPC statement without creating a pool or service."""
import ast
import contextlib
import pathlib
import types
import unittest


class BootstrapConfig(unittest.TestCase):
    def test_pool_rpc_does_not_replace_run_manifest(self):
        tree = ast.parse(pathlib.Path(__file__).with_name('bootstrap_control13.py').read_text())
        statement = next(node for node in ast.walk(tree) if isinstance(node, ast.With)
                         and ast.unparse(node.items[0].context_expr).startswith('grpc.insecure_channel('))
        rpc = types.SimpleNamespace(mem_pool_size=716, chunk_size=512)
        manifest = {'full_a4_proofs': {'serverless_llm': {'path': 'frozen-proof.json'}}}
        namespace = dict(config=manifest,
                         grpc=types.SimpleNamespace(insecure_channel=lambda address: contextlib.nullcontext()),
                         storage_pb2_grpc=types.SimpleNamespace(StorageStub=lambda channel:
                             types.SimpleNamespace(GetServerConfig=lambda request, timeout: rpc)),
                         storage_pb2=types.SimpleNamespace(GetServerConfigRequest=lambda: None))
        exec(compile(ast.Module(body=[statement], type_ignores=[]), '<actual-pool-rpc>', 'exec'), namespace)
        self.assertIs(namespace['config'], manifest)
        self.assertEqual(namespace['config']['full_a4_proofs']['serverless_llm']['path'], 'frozen-proof.json')


if __name__ == '__main__':
    unittest.main()
