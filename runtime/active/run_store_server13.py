import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
import argparse
import asyncio
import hashlib
import importlib
import json
import pathlib
import types

def verify_store_imports(expected,package_root=pathlib.Path('/experiment/code/Store-source-v7/sllm_store')):
    # server imports torch and preloads bundled libglog before native extensions.
    names=('sllm_store','sllm_store.server','sllm_store._C','sllm_store._checkpoint_store')
    if set(expected)!=set(names):raise RuntimeError('unexpected Store import set')
    rows={}
    for name in names:
        module=importlib.import_module(name)
        path=pathlib.Path(module.__file__).resolve()
        if path.parent!=package_root.resolve():raise RuntimeError('Store import origin differs: '+name)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=expected[name]:raise RuntimeError('Store import hash differs: '+name)
        rows[name]=dict(path=str(path),sha256=digest)
        if name=='sllm_store.server':
            compiled=compile(path.read_bytes(),str(path),'exec')
            expected_code=next(value for value in compiled.co_consts if isinstance(value,types.CodeType) and value.co_name=='serve')
            actual_code=module.serve.__code__
            if actual_code!=expected_code or actual_code.co_filename!=str(path):raise RuntimeError('Store serve bytecode differs from reviewed source')
            rows[name].update(serve_code_filename=actual_code.co_filename,serve_source_code_match=True,serve_bytecode_sha256=hashlib.sha256(actual_code.co_code).hexdigest())
    return rows

if __name__=='__main__':
    from workspace import run_root,manifest
    parser = argparse.ArgumentParser()
    parser.add_argument('--storage-path', required=True)
    parser.add_argument('--pool-bytes', type=int, required=True)
    parser.add_argument('--chunk-bytes', type=int, default=32*1024*1024)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--identity-receipt',required=True)
    args = parser.parse_args()
    assert args.pool_bytes > 0
    assert args.chunk_bytes > 0 and args.chunk_bytes % 4096 == 0
    output=pathlib.Path(args.identity_receipt).resolve()
    assert output.is_relative_to(run_root()) and output.name=='runtime-imports.json'
    config=manifest()
    modules=verify_store_imports(config['store_import_sha256'])
    import torch,vllm
    versions={name:dict(path=module.__file__,version=str(module.__version__),sha256=hashlib.sha256(pathlib.Path(module.__file__).read_bytes()).hexdigest()) for name,module in [('torch',torch),('vllm',vllm)]}
    receipt=dict(run_id=config['run_id'],pid=os.getpid(),modules=modules,backend_reference=versions,python=sys.version,cuda=torch.version.cuda,
                 scope='actual imports in Store process before pool creation; not per-Ray-worker import proof')
    with output.open('x') as stream:json.dump(receipt,stream,indent=2)
    serve=importlib.import_module('sllm_store.server').serve
    asyncio.run(serve(host=args.host, port=8073, storage_path=args.storage_path,
                     num_thread=4, chunk_size=args.chunk_bytes, mem_pool_size=args.pool_bytes,
                     registration_required=True))
