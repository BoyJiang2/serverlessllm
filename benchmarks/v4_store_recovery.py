"""One guarded full Store recovery plus native-gold requests; default plans only."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import traceback

from sllm_store.v4_convert import export_engine_config
from sllm_store.v4_export_worker import WORKER_EXTENSION
from sllm_store.v4_state import file_digest, write_once
from v4_native_reference import inputs, run_reference


def recovery_config(model, store, identity):
    config = export_engine_config(model, store, identity)
    config['load_format'] = 'sllm_v4_grouped_store'
    config['worker_extension_cls'] = WORKER_EXTENSION
    return config


def validate_receipts(receipts, identity):
    if not isinstance(receipts, list) or len(receipts) != 8:
        raise ValueError('Eight recovery receipts required')
    ranks = set()
    for row in receipts:
        rank = row.get('rank')
        if (type(rank) is not int or rank not in range(8) or rank in ranks
                or row.get('identity') != identity
                or row.get('format') != 'sllm-v4-grouped-recovery-receipt-v1'
                or row.get('stage') != 'postprocessed'
                or row.get('completed_expert_groups') != 61
                or row.get('retained_remainder_cuda_tensors', 0) <= 0
                or row.get('grouped_contexts') != 0 or row.get('quarantined_loads') != 0):
            raise ValueError('Incomplete recovery, foreign rank or retained failure owners')
        ranks.add(rank)


def stop_daemon(daemon, deadline_ns, output):
    begin = time.monotonic_ns()
    timeout = min(90, (deadline_ns - begin) / 1e9 - 5)
    if timeout <= 0:
        raise TimeoutError('No guarded Store shutdown window remains')
    write_once(output/'daemon-shutdown-started.json', dict(pid=daemon.pid,
               begin_ns=begin, timeout_seconds=timeout, deadline_ns=deadline_ns))
    daemon.send_signal(signal.SIGINT)
    code = daemon.wait(timeout=timeout)
    write_once(output/'daemon-shutdown-finished.json', dict(pid=daemon.pid,
               exit_code=code, completed_ns=time.monotonic_ns()))
    if code != 0:
        raise RuntimeError('Store daemon did not exit normally')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--store', required=True)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--pool-config', type=Path, required=True)
    parser.add_argument('--guard', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--daemon', action='store_true')
    parser.add_argument('--diagnostic-pool', action='store_true')
    args = parser.parse_args()
    identity = json.loads(args.identity.read_bytes())
    pool = json.loads(args.pool_config.read_bytes())
    config = recovery_config(args.model, args.store, identity)
    chunk, full_size = pool['chunk_bytes'], pool['pool_bytes_for_all_groups']
    size = 128*1024**3 if args.diagnostic_pool else full_size
    if (chunk != 33554432 or any(type(value) is not int or value % chunk
            or not 0 < value <= 1024**4 - 64*1024**3 for value in (size, full_size))):
        raise ValueError('Invalid full-cohort pool sizing')
    if not args.execute:
        print(json.dumps(dict(engine_config=config, pool_bytes=size, chunk_bytes=chunk,
                              diagnostic_pool=args.diagnostic_pool, full_cohort_pool_bytes=full_size, gpu_or_model_started=False)))
        return
    if args.guard is None or args.output is None:
        parser.error('Execution requires guard and new output path')
    armed = json.loads(args.guard.read_bytes())
    if (armed.get('purpose') != 'store-recovery'
            or socket.gethostname() != armed['identity']['container_id'][:12]
            or not armed['armed_ns'] < time.monotonic_ns() < armed['deadline_ns']
            or os.environ.get('VLLM_PLUGINS') != 'sllm_v4_store'):
        raise ValueError('Current Store recovery guard and explicit plugin required')
    if args.daemon:
        from sllm_store.server import serve
        asyncio.run(serve('127.0.0.1', 8073, os.environ['STORAGE_PATH'], 8, chunk, size, False))
        return
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    write_once(output/'started.json', dict(identity=identity, engine_config=config,
               pool_bytes=size, full_cohort_pool_bytes=full_size, diagnostic_pool=args.diagnostic_pool, chunk_bytes=chunk, begin_ns=time.monotonic_ns()))
    daemon = None
    try:
        with (output/'daemon.log').open('x') as log:
            daemon = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                *sys.argv[1:], '--daemon'], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        import grpc
        from sllm_store.proto import storage_pb2, storage_pb2_grpc
        channel = grpc.insecure_channel('127.0.0.1:8073')
        ready = grpc.channel_ready_future(channel)
        try:
            while True:
                if daemon.poll() is not None or time.monotonic_ns() >= armed['deadline_ns']-90_000_000_000:
                    raise RuntimeError('Store daemon unavailable within guarded work window')
                try:
                    ready.result(timeout=1)
                    break
                except grpc.FutureTimeoutError:
                    pass
            actual = storage_pb2_grpc.StorageStub(channel).GetServerConfig(storage_pb2.GetServerConfigRequest(), timeout=5)
            if actual.mem_pool_size != size or actual.chunk_size != chunk:
                raise ValueError('Actual Store pool configuration differs')
            write_once(output/'daemon-ready.json', dict(pid=daemon.pid, mem_pool_size=actual.mem_pool_size,
                       chunk_size=actual.chunk_size, ready_ns=time.monotonic_ns()))
        finally:
            ready.cancel()
            channel.close()
        from vllm import LLM, SamplingParams
        def factory(**kwargs):
            engine = LLM(**kwargs)
            try:
                receipts = engine.collective_rpc('sllm_v4_recovery_receipt', timeout=60, args=(identity,))
                write_once(output/'recovery-receipts.json', dict(receipts=receipts, received_ns=time.monotonic_ns()))
                validate_receipts(receipts, identity)
            except BaseException as error:
                try:
                    engine.llm_engine.engine_core.shutdown(timeout=60)
                except BaseException as cleanup_error:
                    error.add_note('Engine shutdown also failed: ' + repr(cleanup_error))
                raise
            return engine
        requests = inputs(Path(__file__).resolve().parents[1])
        run_reference(config, requests, output/'requests', factory, SamplingParams)
        gold = Path(__file__).resolve().parents[1]/'docs/evidence/v4-native-reference-success/successful-run/native-gold.json'
        if file_digest(gold) != '9c67cea181bdfc22f56a063e5b0ffcb3bba1105d178241e8e62c6bcdd59bb4c1':
            raise ValueError('Native gold changed')
        actual = json.loads((output/'requests/candidate.json').read_bytes())['responses']
        if actual != json.loads(gold.read_bytes())['responses']:
            raise ValueError('Store output differs from frozen native gold')
        stop_daemon(daemon, armed['deadline_ns'], output)
        write_once(output/'candidate.json', dict(native_gold_parity=True, daemon_exit_code=0, diagnostic_pool=args.diagnostic_pool, pool_bytes=size,
            complete_sha256=file_digest(Path(args.store)/'COMPLETE.json'), completed_ns=time.monotonic_ns(),
            runtime_qualified=False, physical_cleanup_verified=False,
            scope='Offline Store recovery candidate; host release and review pending; no SLLM service qualification'))
    except BaseException as error:
        write_once(output/'failure.json', dict(error=repr(error), traceback=traceback.format_exc(),
                   daemon_pid=daemon.pid if daemon else None, failed_ns=time.monotonic_ns()))
        # Unknown copies/owners remain isolated with the daemon until the outer container exits.
        raise


if __name__ == '__main__':
    main()
