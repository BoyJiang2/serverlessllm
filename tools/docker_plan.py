"""Compile a debug-only Docker spec. Never execute or grant formal admission."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import os

IMAGE = 'sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'
ROOT = PurePosixPath('/data/brick1/boyjiang/glm5.1-fp8')


def compile_spec(spec):
    required = {'run_id', 'role', 'image_id', 'memory', 'cpus', 'env', 'argv', 'mounts'}
    if set(spec) != required or spec['image_id'] != IMAGE:
        raise ValueError('exact schema and historical image ID required')
    if not re.fullmatch(r'sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', spec['run_id']):
        raise ValueError('debug run identity required')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', spec['role']):
        raise ValueError('invalid role')
    if not re.fullmatch(r'[1-9][0-9]*[mg]', spec['memory']):
        raise ValueError('finite memory limit required')
    if type(spec['cpus']) not in (int, float) or not 0 < spec['cpus'] <= 240:
        raise ValueError('finite CPU limit required')
    if not isinstance(spec['argv'], list) or not spec['argv'] or not all(isinstance(x, str) and x and '\0' not in x for x in spec['argv']):
        raise ValueError('explicit command argv required')
    args = ['docker', 'create', '--pull=never', '--network=none', '--runtime=runc', '--restart=no',
            '--user=2147:2147', '--ulimit=core=0', '--memory='+spec['memory'],
            '--cpus='+str(spec['cpus']), '--name='+spec['run_id']+'-'+spec['role'],
            '--label=sllm.run_id='+spec['run_id'], '--label=sllm.purpose=DEBUG-UNREVIEWED']
    destinations = set()
    for mount in spec['mounts']:
        if set(mount) != {'source', 'destination', 'readonly'} or type(mount['readonly']) is not bool:
            raise ValueError('invalid mount schema')
        source, dest = PurePosixPath(mount['source']), PurePosixPath(mount['destination'])
        if not source.is_relative_to(ROOT) or source == ROOT or '..' in source.parts:
            raise ValueError('mount source outside dedicated subtree')
        if not dest.is_absolute() or '..' in dest.parts or dest == PurePosixPath('/') or str(dest) in destinations:
            raise ValueError('invalid or duplicate mount destination')
        if any(c in str(source)+str(dest) for c in ',\n\0'):
            raise ValueError('mount delimiter forbidden')
        if not mount['readonly'] and not source.is_relative_to(ROOT/'debug'/spec['run_id']):
            raise ValueError('writes restricted to owned debug directory')
        destinations.add(str(dest))
        args += ['--mount=type=bind,src='+str(source)+',dst='+str(dest)+(',readonly' if mount['readonly'] else '')]
    if not isinstance(spec['env'], dict):
        raise ValueError('environment must be explicit dictionary')
    for key, value in sorted(spec['env'].items()):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str) or '\0' in value:
            raise ValueError('invalid environment')
        args += ['--env='+key+'='+value]
    return args+['--entrypoint='+spec['argv'][0], IMAGE, *spec['argv'][1:]]


def preflight(spec):
    compile_spec(spec)
    if not hasattr(os, 'getuid') or os.getuid() != 2147:
        raise ValueError('preflight requires the authorized Linux UID2147')
    observed = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'],
                                       text=True, timeout=15).strip()
    if observed != IMAGE:
        raise ValueError('image identity mismatch')
    checked = []
    for mount in spec['mounts']:
        path = Path(mount['source'])
        resolved = path.resolve(strict=True)
        if resolved != path or not resolved.is_relative_to(Path(str(ROOT))):
            raise ValueError('mount symlink or escape')
        stat = path.stat()
        checked.append(dict(source=str(path), device=stat.st_dev, inode=stat.st_ino,
                            size=stat.st_size, mode=stat.st_mode))
    return dict(image_id=observed, mounts=checked,
                scope='Metadata only; no model file content read, no resource or formal admission')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('spec', type=Path)
    parser.add_argument('--check', action='store_true', help='Linux metadata/image inspection only; never starts containers')
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    print(json.dumps(dict(status='PLAN_ONLY', purpose='DEBUG-UNREVIEWED',
                          docker_argv=compile_spec(spec),
                          preflight=preflight(spec) if args.check else None), indent=2))
