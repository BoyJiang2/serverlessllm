"""C23 regression: the formal spec, mounts and Docker argv a source tree derives, compared with what C23 recorded.

Each tree is evaluated in its own isolated Python process, so its own workspace / materialize_control13 /
launch_control13 run unmodified. `frozen` is runtime/active rebuilt from the C23 commit by deploy_tree; `current`
is the working tree. A change to formal code must keep current == frozen, and frozen must keep reproducing the
recorded dispatch specs and the mounts docker inspect recorded for the real formal13 container.
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import deploy_tree

PACKET = REPO/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313'
C23_COMMIT = 'f176e05607d04999ab28e8ea8cd49b41270cb528'
MOUNTS_FIXTURE = HERE/'fixtures/c23-formal13-mounts.json'
SPEC_NAMES = ('headformal13', 'formal13', 'headservice13a', 'service13a')

DERIVE = r'''
import ast, json, pathlib, sys, types
from unittest.mock import patch
tree, root, specs_path = map(pathlib.Path, sys.argv[1:4])
sys.path.insert(0, str(tree))
import workspace, materialize_control13 as m


class MetadataPath:
    """Stands in for the remote existence checks only; claims nothing about local mounts."""
    def __init__(self, path): self.path = pathlib.PurePosixPath(path)
    def exists(self): return True
    def resolve(self): return self
    def is_relative_to(self, other): return self.path.is_relative_to(other.path)


manifest = json.loads((tree/'run-manifest.json').read_text())
specs = {}
for name, recorded in json.loads(specs_path.read_text()).items():
    address = recorded['env'].get('SLLM_PRIVATE_RAY_ADDRESS', '')
    info = json.dumps([dict(State=dict(Running=True), Config=dict(Labels={'sllm.run_id': manifest['run_id'], 'sllm.uid': '2147'}),
                            NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress=address.split(':')[0] or '172.17.0.2'))))])
    with patch.object(m, 'call', return_value=info):
        specs[name] = m.expected_spec(root, name)
mounts = workspace.container_mounts()
body = ast.parse((tree/'launch_control13.py').read_text()).body
assigns = lambda node, name: isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets)
start = next(i for i, node in enumerate(body) if assigns(node, 'args'))
end = next(i for i, node in enumerate(body) if assigns(node, 'dispatch_monotonic_ns'))
block = ast.Module(body=body[start:end], type_ignores=[])
code = compile(block, 'launch_control13.py', 'exec')
argv = {}
for name, spec in specs.items():
    namespace = dict(spec=spec, run=manifest['run_id'], manifest=manifest, HOST=workspace.HOST, RUN_ALIAS=workspace.RUN_ALIAS,
                     container_mounts=lambda: mounts, pathlib=types.SimpleNamespace(Path=MetadataPath, PurePosixPath=pathlib.PurePosixPath))
    exec(code, namespace)
    argv[name] = namespace['args']
print(json.dumps(dict(specs=specs, mounts=mounts, argv=argv, argv_block=ast.dump(block))))
'''


def recorded():
    """The dispatch specs, deployment record and formal admission C23 used, each hash-checked against the packet index."""
    specs, deployment = {}, None
    for index_name in ('formal-exact-index.json', 'normal-exact-index.json'):
        index = json.loads((PACKET/index_name).read_text(encoding='utf-8'))
        for entry in (index['files'] if isinstance(index, dict) else index):
            raw = (PACKET/entry.get('local_path', entry.get('local'))).read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('packet file differs from its index: '+entry['path'])
            name = pathlib.PurePosixPath(entry['path']).name
            if name.endswith('-dispatch.json') and name[:-len('-dispatch.json')] in SPEC_NAMES:
                label = name[:-len('-dispatch.json')]
                spec = json.loads(raw)['spec']
                if specs.setdefault(label, spec) != spec:
                    raise ValueError('conflicting recorded specs for '+label)
            elif name == 'deployment.json':
                if deployment not in (None, raw):
                    raise ValueError('conflicting recorded deployment.json')
                deployment = raw
    if set(specs) != set(SPEC_NAMES) or deployment is None:
        raise ValueError('packet lacks a recorded spec or deployment.json')
    return specs, deployment, (PACKET/'formal-admission.json').read_bytes()


def derive(tree, specs, deployment, admission):
    tree = pathlib.Path(tree)
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)/'run'
        root.mkdir()
        (root/'run-manifest.json').write_bytes((tree/'run-manifest.json').read_bytes())
        (root/'deployment.json').write_bytes(deployment)
        (root/'formal-admission.json').write_bytes(admission)
        (pathlib.Path(directory)/'specs.json').write_text(json.dumps(specs))
        result = subprocess.run([sys.executable, '-I', '-c', DERIVE, str(tree), str(root), str(pathlib.Path(directory)/'specs.json')],
                                cwd=tree, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('derivation failed in %s:\n%s' % (tree, result.stderr[-3000:]))
    return json.loads(result.stdout)


def argv_mounts(argv):
    rows = set()
    for item in argv:
        if item.startswith('--mount='):
            fields = dict(part.partition('=')[::2] for part in item[len('--mount='):].split(','))
            rows.add((fields['src'], fields['dst'], 'readonly' in fields))
    return rows


def frozen_tree(directory):
    for path, data in deploy_tree.commit_files(C23_COMMIT).items():
        (directory/path).parent.mkdir(parents=True, exist_ok=True)
        (directory/path).write_bytes(data)
    return directory


def regression(current=REPO/'runtime/active'):
    specs, deployment, admission = recorded()
    with tempfile.TemporaryDirectory() as directory:
        frozen = derive(frozen_tree(pathlib.Path(directory)), specs, deployment, admission)
    now = derive(current, specs, deployment, admission)
    fixture = json.loads(MOUNTS_FIXTURE.read_text(encoding='utf-8'))
    recorded_mounts = {(m['source'], m['destination'], m['readonly']) for m in fixture['mounts']}
    derived_mounts = argv_mounts(frozen['argv']['formal13'])
    return dict(
        frozen_specs_equal_recorded={name: frozen['specs'][name] == specs[name] for name in SPEC_NAMES},
        formal13_mounts=dict(recorded=len(recorded_mounts), derived=len(derived_mounts),
                             missing=sorted(recorded_mounts-derived_mounts), extra=sorted(derived_mounts-recorded_mounts)),
        gate_sha256_consistent=specs['formal13']['env']['SLLM_FORMAL_GATE_SHA256'] == hashlib.sha256(admission).hexdigest(),
        current_equals_frozen={key: now[key] == frozen[key] for key in ('specs', 'mounts', 'argv', 'argv_block')})


def passed(report):
    return (all(report['frozen_specs_equal_recorded'].values()) and report['gate_sha256_consistent']
            and not report['formal13_mounts']['missing'] and not report['formal13_mounts']['extra']
            and all(report['current_equals_frozen'].values()))


if __name__ == '__main__':
    report = regression()
    print(json.dumps(dict(report, passed=passed(report)), indent=2))
    sys.exit(0 if passed(report) else 1)
