"""Stage B1 observability beside a verified copy of the existing large-pool runner."""
import hashlib
from pathlib import Path
import sys

BASE = {
    "v11_run.py": "4f0801143942b8e34ef804d96e5e2604bf79498ebc09e68f68edc63d56e882f6",
    "v11_backend.py": "a683a40b12f8cf4ef1f36c2556eb1c1943a57df77d38c76332c7b93a1a8c7adf",
    "sf_admission.py": "078b7db65c57042d1fde4177ec2c7d2eaeb01fe0beb5aefab79a58ab41b025ef",
    "campaign.json": "d3111d7f320cfb9e12a09c6a49e15ecbd3b4215d072c189707e12174ecd3dee7",
}


def replace(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def main(base, dest):
    inputs = {name: (base / name).read_bytes() for name in BASE}
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()} == BASE
    runner = replace(
        inputs["v11_run.py"].decode(),
        "    args += [profile['image'], '-m', 'lmcache.v1.multiprocess.http_server',",
        """    args += ['--mount', 'type=bind,src=' + str(CAMPAIGN / 'lmcache_store_trace.py')
             + ',dst=/control/lmcache_store_trace.py,readonly']
    args += [profile['image'], '/control/lmcache_store_trace.py',""",
    )
    runner = replace(
        runner,
        "        (str(CAMPAIGN / 'v11_backend.py'), '/control/script.py'),",
        """        (str(CAMPAIGN / 'v11_backend.py'), '/control/script.py'),
        (str(CAMPAIGN / 'v11_hbm_receipt.py'),
         '/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/v11_hbm_receipt.py'),""",
    )
    backend = replace(inputs["v11_backend.py"].decode(), "        received = time.monotonic_ns()",
                      "        received = time.monotonic_ns()\n        received_wall = time.time_ns()")
    backend = replace(backend, "received_ns=received, response=response",
                      "received_ns=received, received_wall_ns=received_wall, response=response")
    backend = replace(
        backend,
        "    backend = VllmBackend('shared-' + data['name'], build_backend_config(data))",
        """    config = build_backend_config(data)
    assert not config.get('worker_extension_cls')
    config['worker_extension_cls'] = 'sllm_store.v11_hbm_receipt.V11HbmReceipt'
    backend = VllmBackend('shared-' + data['name'], config)""",
    )
    backend = replace(
        backend,
        "        if data.get('capture_before_shutdown'):",
        """        if data.get('capture_before_shutdown'):
            begin_ns = time.monotonic_ns()
            ranks = await backend.engine.collective_rpc('sllm_v11_hbm_receipt', timeout=30)
            end_ns = time.monotonic_ns()
            assert len(ranks) == 8 and {row['rank'] for row in ranks} == set(range(8))
            assert all(row['parameter_storage_bytes'] > 0 and row['runner_kv_storage_bytes'] > 0
                       and begin_ns <= row['started_ns'] <= row['finished_ns'] <= end_ns for row in ranks)
            write('backend-hbm-storage.json', dict(begin_ns=begin_ns, end_ns=end_ns, ranks=ranks))""",
    )
    for name, source in (("v11_run.py", runner), ("v11_backend.py", backend)):
        compile(source, name, "exec")
        inputs[name] = source.encode()
    for name in ("lmcache_store_trace.py", "v11_hbm_receipt.py"):
        inputs[name] = Path(__file__).with_name(name).read_bytes()
    dest.mkdir()
    for name, raw in inputs.items():
        (dest / name).write_bytes(raw)
    print(dest)


if __name__ == "__main__":
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
