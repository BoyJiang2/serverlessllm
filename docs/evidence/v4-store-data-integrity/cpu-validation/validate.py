"""One-off CPU completion of a fully exported cohort; never resumes GPU loading."""
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

from sllm_store.v4_state import file_digest, validate_identity, write_once
from sllm_store.v4_grouped_state import publish_grouped_complete, worker_receipts
from sllm_store import v4_grouped_state

root = Path('/store')
origin = Path('/origin')
info = json.loads(Path('/control/info.json').read_bytes())
assert file_digest(Path(v4_grouped_state.__file__)) == info['validator_sha256']
started = json.loads((root/'CONVERSION_STARTED.json').read_bytes())
receipts = json.loads((root/'EXPORT_RECEIPTS.json').read_bytes())
shutdown = json.loads((root/'ENGINE_SHUTDOWN.json').read_bytes())
identity = started['identity']
validate_identity(identity)
assert receipts['identity'] == shutdown['identity'] == identity
assert identity['conversion_id'] == info['conversion_id']
assert started['started_unix_ns'] <= receipts['collected_unix_ns'] <= shutdown['completed_unix_ns']
worker_receipts(receipts['workers'], identity)
assert not (root/'CONVERSION_FAILED.json').exists() and not (root/'COMPLETE.json').exists()
result = json.loads((origin/'result.json').read_bytes())
release = json.loads((origin/'release/result.json').read_bytes())
guard = json.loads((origin/'guard/result.json').read_bytes())
assert result['status'] == 'failed_store_conversion_process'
assert result['container_exit_code'] == 137 and result['release_observation_passed']
assert result['weight_metadata_unchanged'] and not result['formal_id_consumed']
assert guard['reason'] == 'deadline' and guard['container_stopped']
assert guard['identity'] == json.loads((origin/'preparation.json').read_bytes())['identity']
assert release['native_release_observation_passed'] and release['coverage']['sample_span_seconds'] >= 60
begin = time.monotonic_ns()
inputs = {str(p):file_digest(p) for p in [root/'CONVERSION_STARTED.json',root/'EXPORT_RECEIPTS.json',
          root/'ENGINE_SHUTDOWN.json',origin/'result.json',origin/'guard/result.json',
          origin/'release/result.json',origin/'release/samples.jsonl']}
write_once('/output/started.json',dict(identity=identity,input_sha256=inputs,begin_ns=begin,
    validator_sha256=info['validator_sha256'],original_attempt_status=result['status']))
try:
    complete = publish_grouped_complete(root, identity, receipts['workers'])
    assert not ({'torch','vllm','ray','sllm_store._C'} & set(sys.modules))
    write_once('/output/candidate.json',dict(identity=identity,complete_sha256=file_digest(root/'COMPLETE.json'),
        completed_ns=time.monotonic_ns(),elapsed_seconds=(time.monotonic_ns()-begin)/1e9,
        full_payload_verified=True,ranks=len(complete['workers']),
        original_attempt_status_unchanged=result['status'],
        scope='Independent CPU data integrity completion; original GPU timeout remains failed; no restore/inference/IPC/service qualification',
        runtime_qualified=False,formal_id_consumed=False))
except BaseException as error:
    write_once('/output/failure.json',dict(error=repr(error),traceback=traceback.format_exc(),failed_ns=time.monotonic_ns()))
    raise
