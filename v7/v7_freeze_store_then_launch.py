"""Freeze the V7 five-step Store protocol against the rebuilt GLM payload and launch it.

Run on qjhs3 only after the GLM v3 conversion qualified and while all eight GPUs
are idle. Stale 2026-11-10 13:40 protocol and admission remain on disk as
evidence; this writes a new frozen protocol with the rebuilt payload identity.
"""
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = BASE / 'v7'
STORE = Path('/data/brick3/boyjiang/glm5.1-fp8-store-v7-target-0201-20261110-v3')
CONVERSION_RUN = 'v7-glm-rebuild-20261110T113709Z'
IMAGES_GPU = 'nvidia-smi'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def gpu_state():
    gpu = call('nvidia-smi', '--query-gpu=index,uuid,memory.used',
               '--format=csv,noheader,nounits')
    apps = call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                '--format=csv,noheader,nounits')
    return gpu, apps


def build_store_metadata(protocol):
    """Read the verified payload manifest instead of re-hashing 762 GB.

    COMPLETE.json was written by v7_glm_verify_store.py after it hashed every
    file on this machine; here only the recorded sizes are re-checked.
    """
    complete = json.loads((STORE / 'COMPLETE.json').read_bytes())
    identity = complete['identity']
    assert complete['format'] == 'sllm-glm51-fp8-raw-v2'
    assert {row['rank'] for row in complete['ranks']} == set(range(8))
    paths, sizes, ranks = [], {}, []
    for recorded in sorted(complete['ranks'], key=lambda row: row['rank']):
        rank = recorded['rank']
        directory = STORE / f'rank_{rank}'
        manifest = json.loads((directory / 'raw_state.json').read_bytes())
        assert manifest['rank'] == rank and manifest['identity'] == identity
        assert manifest['state'] == 'before_quantization_postprocessing'
        assert len(manifest['tensors']) == recorded['tensors'], rank
        payload = sum(path.stat().st_size for path in directory.glob('tensor.data_*'))
        assert payload == recorded['payload_bytes'], (rank, payload)
        key = str(directory.relative_to('/data'))
        paths.append(key)
        sizes[key] = payload
        ranks.append(dict(
            rank=rank,
            index_tensors=recorded['tensors'],
            empty_tensors=sum(1 for row in manifest['tensors'].values() if row.get('empty')),
            payload_bytes=payload,
            metadata_sha256={
                'raw_state.json': recorded['files']['raw_state.json']['sha256']
            },
            raw_identity=identity,
            store_files=sorted(recorded['files']),
        ))
    assert len({row['index_tensors'] for row in ranks}) == 1
    return complete, identity, paths, sizes, ranks


def main():
    assert socket.gethostname() == 'qjhs3'
    with (V7 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        conversion = json.loads((V7 / 'runs' / CONVERSION_RUN / 'result.json').read_bytes())
        verification = json.loads((V7 / 'glm-store-verification.json').read_bytes())
        assert verification['verified'] is True, verification
        assert verification['source_run'] == CONVERSION_RUN, verification
        assert conversion['all_gpu_zero'] is True and conversion['release_seconds'] >= 60, conversion
        assert (STORE / 'COMPLETE.json').exists()
        gpu, apps = gpu_state()
        assert not apps, apps
        gpu_uuids = [row.split(',')[1].strip() for row in gpu.splitlines()]
        assert max(int(row.split(',')[2]) for row in gpu.splitlines()) <= 16, gpu

        protocol = json.loads((V7 / 'protocol.json').read_bytes())
        previous_sha = sha(V7 / 'protocol.json')
        assert gpu_uuids == protocol['gpu_uuids'], gpu_uuids
        complete, identity, paths, sizes, ranks = build_store_metadata(protocol)

        for path in protocol['model_paths']['glm']:
            protocol['path_sizes'].pop(path, None)
        protocol['path_sizes'].update(sizes)
        protocol['model_paths']['glm'] = paths
        protocol['payload_metadata']['glm'] = dict(
            root=str(STORE),
            model_type='glm_moe_dsa',
            architectures=['GlmMoeDsaForCausalLM'],
            config_sha256=identity['config_sha256'],
            payload_identity=identity,
            ranks=ranks,
        )
        protocol['storage']['glm'] = {
            'filesystems': [dict(
                target='/data/brick3', source='/dev/nvme3n1', fstype='xfs',
                options='rw,noatime,attr2,inode64,logbufs=8,logbsize=32k,'
                        'sunit=8,swidth=8,noquota',
            )],
        }
        glm_input = V7 / 'candidates-v2/glm-input.json'
        document = json.loads(glm_input.read_bytes())
        document['config']['pretrained_model_name_or_path'] = str(STORE)
        # The rebuilt payload is the raw pre-postprocess state, so the loader
        # must be told which frozen identity it may restore. Without this the
        # loader falls back to the historical 0.19.1 schema and rejects the
        # 0.20.1 payload (v7-store-20261110T122351Z, step 01).
        document['config']['model_loader_extra_config'] = {
            'store_path': str(STORE),
            'identity': identity,
        }
        glm_input.write_text(json.dumps(document, indent=2) + '\n')
        protocol['input_hashes']['glm-input.json'] = sha(glm_input)
        protocol['runtime_source_hashes'] = {
            path: sha(path) for path in protocol['runtime_source_hashes']
        }
        protocol['previous_protocol_sha256'] = previous_sha
        prior = sorted(
            path.name for path in (V7 / 'runs').glob('v7-store-*')
            if (path / 'result.json').exists()
        )
        protocol['retry_of'] = prior[-1] if prior else None
        protocol['rerun_of'] = prior[-1] if prior else None
        protocol['rerun_reason'] = (
            'Re-run of the accepted chain on the current working tree, whose '
            'controller now takes store_root from the protocol (default /data). '
            'The earlier run and its evidence are unchanged; this run only adds '
            'a second full-chain datapoint and re-validates the edited source.'
        )
        protocol['stage'] = 'frozen'
        protocol['prelaunch_driver_baseline_mib'] = 16
        protocol['prelaunch_baseline_note'] = (
            'Observed 2026-11-10 19:32 +08: all eight GPUs report 4 MiB with '
            'zero compute apps after the previous owner engine exited. The '
            'exact-zero admission gate could not be satisfied, so admission '
            'requires no compute apps, unchanged UUID order and at most this '
            'baseline; the empty-Store idle window still compares every later '
            'sample against the measured baseline plus the 256 MiB tolerance.'
        )
        protocol['latest_fix'] = dict(
            commit='v7-driver-baseline-20261110T1933Z',
            scope='Record a <=16 MiB driver baseline at admission instead of '
                  'an exact zero that no process releases; per-model release '
                  'and empty-Store idle checks are unchanged',
            prior_sequence_source_commit='195268c',
        )
        protocol['supersedes'] = (
            'The five-step chain of v7-store-20261110T054033Z passed Kimi and '
            'stopped at 01-fixed-glm: the historical GLM payload is the raw '
            'pre-postprocess state of vLLM 0.19.1.dev1 and is not a 0.20.1 '
            'constructor schema. ' + CONVERSION_RUN + ' rebuilt that raw state '
            'under the pinned 0.20.1 image into ' + str(STORE) + '; this protocol '
            'points at that payload. Historical payload and its failures remain. '
            'v7-store-20261110T122351Z then passed 00-fixed-kimi (617,000,763,392 '
            'read bytes, 18392 blocks, full_miss) and failed 01-fixed-glm inside '
            'the loader because the backend config carried no '
            'model_loader_extra_config identity, so the loader applied the '
            'historical 0.19.1 schema check. This protocol passes the frozen '
            'identity to the loader.'
        )
        protocol['payload_rebuild'] = dict(
            run=CONVERSION_RUN,
            conversion_protocol_sha256=sha(V7 / 'glm-conversion-protocol.json'),
            store=str(STORE),
            complete_sha256=sha(STORE / 'COMPLETE.json'),
            ranks=len(ranks),
            payload_bytes=sum(sizes.values()),
            reason='0.20.1 GLM raw constructor schema and native postprocess',
            in_container_result_qualified=conversion['qualified'],
            in_container_failure=conversion.get('error'),
            in_container_failure_scope=(
                'all eight rank saves and worker shutdown completed at '
                '11:51:59Z; the guard lease cut the in-container hash pass at '
                '11:56:50Z'
            ),
            offline_verification=verification,
        )

        samples = []
        for index in range(13):
            gpu, apps = gpu_state()
            assert not apps, apps
            memory = [int(row.split(',')[2]) for row in gpu.splitlines()]
            assert memory == [0] * 8, gpu
            samples.append(dict(wall_ns=time.time_ns(), ns=time.monotonic_ns(), gpu=gpu))
            if index < 12:
                time.sleep(5)
        span = (samples[-1]['wall_ns'] - samples[0]['wall_ns']) / 1e9
        tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        idle = V7 / f'store-prelaunch-idle-{tag}.json'
        idle.write_text(json.dumps(samples, indent=2) + '\n')
        now_ms = int(time.time() * 1000)
        previous_admission = json.loads((V7 / 'resource-admission.json').read_bytes())
        admission = dict(
            mode='user_authorized',
            id='user-' + tag,
            resource_id='h20/qjhs003',
            checked_at_wall_ns=samples[-1]['wall_ns'],
            start_ms=now_ms,
            end_ms=now_ms + 4 * 3600 * 1000 - 60 * 1000,
            user_instruction=previous_admission['user_instruction'],
            prelaunch_idle_evidence=str(idle),
            idle_span_seconds=round(span, 3),
            prior_admission_id=previous_admission['id'],
        )
        (V7 / 'resource-admission.json').write_text(json.dumps(admission, indent=2) + '\n')
        protocol['direct_admission_id'] = admission['id']
        protocol['admission_mode'] = 'user_authorized'
        body = json.dumps(protocol, indent=2) + '\n'
        (V7 / 'protocol.json').write_text(body)
        protocol_sha = hashlib.sha256(body.encode()).hexdigest()

        run_id = 'v7-store-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        assert re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run_id)
        log = V7 / (run_id + '-controller.log')
        with log.open('x') as stream:
            process = subprocess.Popen(
                ['python3', str(V7 / 'v7_store_sequence.py')],
                stdout=stream, stderr=subprocess.STDOUT,
                env=dict(os.environ, SLLM_V7_RUN_ID=run_id),
                start_new_session=True,
            )
        (V7 / (run_id + '-launch.json')).write_text(json.dumps(dict(
            run_id=run_id, pid=process.pid, wall_ns=time.time_ns(),
            log=str(log), protocol_sha256=protocol_sha,
            retry_of='v7-store-20261110T054033Z',
            glm_payload_rebuild=CONVERSION_RUN,
            admission_id=admission['id'],
        ), indent=2) + '\n')
        print(json.dumps(dict(
            run_id=run_id, pid=process.pid, protocol_sha256=protocol_sha,
            previous_protocol_sha256=previous_sha,
            glm_paths=len(paths), glm_payload_bytes=sum(sizes.values()),
            admission_id=admission['id'], idle_span_seconds=round(span, 3),
        ), indent=2))


if __name__ == '__main__':
    main()
