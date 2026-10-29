"""Copy the debug-stack tooling to qjhs003 as one hash-named tree; never starts anything."""
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile

REPO = pathlib.Path(__file__).resolve().parents[2]
REMOTE_ROOT = '/data/brick1/boyjiang/glm5.1-fp8/code'
TOOL_FILES = ('controls/debug_stack_v1/debug_plan.py', 'controls/debug_stack_v1/debug_stack.py',
              'controls/debug_stack_v1/debug_service.py', 'controls/debug_stack_v1/debug_guard.py',
              'controls/debug_stack_v1/install_debug_guard.sh', 'controls/service_smoke_v1/docker_args.py',
              'tools/restore_original_image.py', 'tools/docker_plan.py')
VERIFY = """import hashlib,json,pathlib
root=pathlib.Path(%r)
receipt=json.loads((root/'deployment.json').read_text())
bad=[r['path'] for r in receipt['files'] if hashlib.sha256((root/r['path']).read_bytes()).hexdigest()!=r['sha256']]
assert not bad,'hash mismatch: %%s'%%bad
assert not (root/'runtime/active/run-manifest.json').exists()
print('verified',len(receipt['files']),'files under',root)
"""


def inventory():
    # The formal run identity stays behind: debug trees must never carry it.
    names = list(TOOL_FILES)+['runtime/active/'+p.name for p in sorted((REPO/'runtime/active').iterdir())
                              if p.is_file() and p.name != 'run-manifest.json']
    return [dict(path=name, sha256=hashlib.sha256((REPO/name).read_bytes()).hexdigest()) for name in names]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO, text=True).strip()


def main():
    rows = inventory()
    tag = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12]
    destination = REMOTE_ROOT+'/debug-stack-'+tag
    receipt = dict(tag=tag, destination=destination, base_commit=git('rev-parse', 'HEAD'),
                   uncommitted=git('status', '--porcelain', '--', *[r['path'] for r in rows]).splitlines(),
                   files=rows, scope='debug tooling only; no formal admission; nothing started')
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        for name, data, mode in [(r['path'], (REPO/r['path']).read_bytes(), 0o644) for r in rows]+[
                ('deployment.json', json.dumps(receipt, indent=2).encode(), 0o444)]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    subprocess.run(['ssh', '-o', 'BatchMode=yes', 'qjhs003',
                    f'set -e; test ! -e {destination}; mkdir -m 700 {destination}; tar -C {destination} -xf -'],
                   input=archive.getvalue(), check=True)
    subprocess.run(['ssh', '-o', 'BatchMode=yes', 'qjhs003', 'python3', '-'], input=(VERIFY % destination).encode(), check=True)
    print(json.dumps(dict(destination=destination, tag=tag, uncommitted=receipt['uncommitted']), indent=2))


if __name__ == '__main__':
    main()
