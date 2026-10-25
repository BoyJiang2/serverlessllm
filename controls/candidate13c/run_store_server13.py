import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
import argparse
import asyncio

from sllm_store.server import serve

parser = argparse.ArgumentParser()
parser.add_argument('--storage-path', required=True)
parser.add_argument('--pool-bytes', type=int, required=True)
parser.add_argument('--chunk-bytes', type=int, default=32*1024*1024)
parser.add_argument('--host', default='127.0.0.1')
args = parser.parse_args()
assert args.pool_bytes > 0
assert args.chunk_bytes > 0 and args.chunk_bytes % 4096 == 0
asyncio.run(serve(host=args.host, port=8073, storage_path=args.storage_path,
                  num_thread=4, chunk_size=args.chunk_bytes, mem_pool_size=args.pool_bytes,
                  registration_required=True))
