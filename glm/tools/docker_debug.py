"""CPU-only debug lifecycle; this is never a formal or GPU service entry."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import uuid

from docker_plan import IMAGE, ROOT, compile_spec, preflight


def bounded_spec(spec, seconds):
    compile_spec(spec)
    if type(seconds) is not int or not 1 <= seconds <= 300:
        raise ValueError('CPU debug lifetime must be1-300 seconds')
    if spec['memory'] not in ('128m','256m','512m','1g','2g') or spec['cpus'] > 2:
        raise ValueError('lightweight debug capped at2CPU/2GiB')
    if set(spec['env'])-{'PYTHONDONTWRITEBYTECODE'}:
        raise ValueError('CPU debug environment permits only PYTHONDONTWRITEBYTECODE')
    for mount in spec['mounts']:
        source = PurePosixPath(mount['source'])
        destination = PurePosixPath(mount['destination'])
        if not mount['readonly'] or not source.is_relative_to(ROOT/'code') or not destination.is_relative_to('/debug/code'):
            raise ValueError('CPU debug permits readonly code mounts only; no model/cache inputs')
    result = copy.deepcopy(spec)
    result['argv'] = ['/usr/bin/timeout','--signal=TERM','--kill-after=10',str(seconds),*spec['argv']]
    return result


def inspect_owned(identity):
    cid = identity['container_id']
    if not re.fullmatch('[0-9a-f]{64}', cid):
        raise ValueError('full container ID required')
    result = subprocess.run(['docker','inspect',cid],text=True,capture_output=True,timeout=15)
    if result.returncode:
        raise RuntimeError('container inspect unavailable; absence is not proven')
    data = json.loads(result.stdout)[0]
    labels = data['Config']['Labels'] or {}
    if (data['Id'] != cid or data['Image'] != IMAGE or
        labels.get('sllm.run_id') != identity['run_id'] or
        labels.get('sllm.purpose') != 'DEBUG-UNREVIEWED' or
        labels.get('sllm.holder') != identity.get('holder') or not identity.get('holder') or
        data['Name'] != '/'+identity['run_id']+'-'+identity['role'] or
        data['Config']['User'] != '2147:2147'):
        raise ValueError('container ownership/identity mismatch; no mutation allowed')
    host = data['HostConfig']
    if host['Runtime'] != 'runc' or host['NetworkMode'] != 'none' or host.get('DeviceRequests') or host.get('Privileged'):
        raise ValueError('container execution policy mismatch')
    return data


def recover_identity(identity):
    if identity.get('container_id'):
        return identity
    name = identity['run_id']+'-'+identity['role']
    result = subprocess.run(['docker','inspect','--type=container',name],text=True,capture_output=True,timeout=15)
    if result.returncode:
        raise RuntimeError('creation outcome unknown; exact name inspect unavailable')
    recovered = dict(identity,container_id=json.loads(result.stdout)[0]['Id'])
    inspect_owned(recovered)  # Includes the unique token reserved before create.
    return recovered


def save(path, record):
    temporary = path.with_suffix('.tmp')
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(record,stream,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)


def up(spec, receipt, seconds):
    wrapped = bounded_spec(spec,seconds)
    preflight(wrapped)
    identity = dict(run_id=spec['run_id'],role=spec['role'],image_id=IMAGE,purpose='DEBUG-UNREVIEWED',
                    holder=uuid.uuid4().hex,
                    spec_sha256=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest(),
                    lifetime_seconds=seconds,status='creating')
    with receipt.open('x',encoding='utf-8') as stream:
        json.dump(identity,stream)
    cid = None
    try:
        args = compile_spec(wrapped)
        args[2:2] = ['--label=sllm.holder='+identity['holder'],'--pids-limit=128',
                     '--cap-drop=ALL','--security-opt=no-new-privileges=true']
        cid = subprocess.check_output(args,text=True,timeout=30).strip()
        if not re.fullmatch('[0-9a-f]{64}',cid):
            raise ValueError('Docker did not return full container ID')
        identity['container_id'] = cid
        save(receipt,identity)
        inspect_owned(identity)
        identity['status'] = 'created'
        save(receipt,identity)
        subprocess.run(['docker','start',cid],check=True,capture_output=True,timeout=30)
        data = inspect_owned(identity)
        identity['status'] = 'running' if data['State']['Running'] else 'exited'
        save(receipt,identity)
        return identity
    except Exception:
        # Cleanup only an ID whose ownership was independently rechecked.
        try:
            identity = recover_identity(identity)
            cid = identity['container_id']
            inspect_owned(identity)
            subprocess.run(['docker','rm','-f',cid],check=True,capture_output=True,timeout=30)
            identity['status'] = 'failed; exact-owned container removed'
        except Exception:
            identity['status'] = 'failed; cleanup unverified; inspect exact name and holder'
        save(receipt,identity)
        raise


def up_with_image(spec, receipt, seconds):
    bounded_spec(spec,seconds)  # Reject invalid work before any image I/O.
    if receipt.exists():
        raise FileExistsError('debug identity already registered')
    from restore_original_image import restore_with_receipt
    initialization=restore_with_receipt(receipt.with_name(spec['role']+'-image-init.json'))
    result=up(spec,receipt,seconds)
    result['image_initialization']=initialization
    return result


def down(identity, receipt):
    identity = recover_identity(identity)
    inspect_owned(identity)
    subprocess.run(['docker','rm','-f',identity['container_id']],check=True,capture_output=True,timeout=30)
    # A failed inspect alone could be a daemon/network error; list via the exact ID.
    remaining = subprocess.check_output(['docker','ps','-aq','--no-trunc','--filter','id='+identity['container_id']],text=True,timeout=15).strip()
    if remaining:
        raise RuntimeError('owned container remains')
    identity = dict(identity,status='released')
    save(receipt,identity)
    return identity


def receipt_path(spec, create=False):
    compile_spec(spec)
    if not hasattr(os,'getuid') or os.getuid() != 2147:
        raise ValueError('authorized Linux UID2147 required')
    directory = Path(str(ROOT))
    if directory.resolve(strict=True) != directory or directory.stat().st_uid != 2147:
        raise ValueError('workspace identity mismatch')
    for part in ('debug',spec['run_id']):
        directory = directory/part
        if create and not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
        if directory.resolve(strict=True) != directory or not directory.is_dir() or directory.stat().st_uid != 2147:
            raise ValueError('state directory symlink or owner mismatch')
    return directory/(spec['role']+'-identity.json')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['up','status','down'])
    parser.add_argument('spec',type=Path)
    parser.add_argument('--seconds',type=int,default=180)
    args=parser.parse_args()
    spec=json.loads(args.spec.read_text())
    receipt=receipt_path(spec,create=args.action=='up')
    if args.action=='up':
        result=up_with_image(spec,receipt,args.seconds)
    else:
        identity=json.loads(receipt.read_text())
        if identity['run_id']!=spec['run_id'] or identity['role']!=spec['role']:
            raise ValueError('receipt differs from requested debug identity')
        result=down(identity,receipt) if args.action=='down' else inspect_owned(recover_identity(identity))['State']
    print(json.dumps(result,indent=2))
