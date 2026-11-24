#!/usr/bin/env python3
"""Audit recorded B1 Store restores only. No Docker, GPU or payload reads."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

BASE = '/data/brick{}/boyjiang/pd-1p1d-baselines/runs'
ROOTS = {role: Path(BASE.format(brick)) for role, brick in (('P', 2), ('D', 1))}
HOSTS = {'P': 'qjhs003', 'D': 'qjhs002'}
RUN_ID = r'PD-V6-B1-[A-Z0-9-]+-\d{8}T\d{6}Z'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
NATIVE = re.compile(r'Confirm model (pd-v6-store/[KGD]/\S+) replica (' + UUID + r') success\b')
V4_CLIENT = re.compile(r'\(Worker_TP([0-7]) pid=\d+\).*?confirm_model_loaded: '
                       r'(pd-v6-store/D/groups/\w+/rank_[0-7]), (' + UUID + r')\b')
CAPACITY = 256 * 1024 * 1024
PREFIX = {'K': 'kimi', 'G': 'glm53'}
FORMATS = {'K': 'sllm_kimi26_b2_postprocessed_inplace',
           'G': 'sllm_glm53_b2_inplace', 'D': 'sllm_v4_b2_inplace'}
COMPLETE_SHA = {'G': '91c01dcb07ee8b69e7ac393bcd1e0a639c051fd76ec64841255b654efb01175d',
                'D': '3333fe091f345128e72ce2763484ea33f95cbca0f820552027ce2c5af35c8e21'}
OVERLAY = Path('/home/boyjiang/pd-v6-b1-closureplan50.P1Se3muW/runtime/b1-v6/loader-overlay')
V4_LOADER_SHA = '03255470c3372f4cb7cb94490c1cceadd06b7a38dce6c6e3ca6782c286afc6a4'
PLANNER_SHA = {'v4_store_windows.py': '7368d87b361f335d5872b9724025753a23fbe2f47907b602c0bc49b074014ff8',
               'b2_store_staging_contract.py': 'abe38ad90b3a804ae783f4507ae38ffa7dd5d1506857bfffe70bf42caacba438'}


def need(ok, detail):
    if not ok:
        raise ValueError(detail)


def read(path):
    return json.loads(path.read_text())


def file_ref(path, role):
    info = path.stat()
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(256 * 1024), b''):
            sha.update(block)
    need(path.stat() == info, f'evidence changed during read: {path}')
    return {'host': HOSTS[role], 'path': str(path), 'bytes': info.st_size, 'sha256': sha.hexdigest()}


def metadata(path, digest, role):
    ref = file_ref(path, role)
    need(ref['sha256'] == digest, f'metadata SHA mismatch: {path}')
    return read(path), ref


def native_counts(path, regex=NATIVE):
    with path.open() as stream:
        return Counter(m.groups() for line in stream if (m := regex.search(line)))


def expected_v4(root, rank, completion, role):
    from v4_store_windows import plan_group

    manifest, _ = metadata(root / f'rank_{rank}/grouped_state.json',
                           completion['ranks'][str(rank)], role)
    expected = {}
    need(manifest['rank'] == rank and manifest['identity'] == completion['identity'],
         f'V4 rank identity: {rank}')
    groups = ['remainder', *(f'expert_{i:04}' for i in range(61))]
    need([group['id'] for group in manifest['groups']] == groups, f'V4 group order: {rank}')
    for group in manifest['groups']:
        name = group['id']
        directory = root / f'groups/{name}/rank_{rank}'
        raw, _ = metadata(directory / 'raw_state.json', group['manifest_sha256'], role)
        need(raw['rank'] == rank and raw['identity'] == completion['identity'] and
             set(raw['tensors']) == set(group['tensors']), f'V4 group identity: {name}/{rank}')
        files = raw['files']
        index, _ = metadata(directory / 'tensor_index.json', files['tensor_index.json']['sha256'], role)
        payload_bytes = sum(row['bytes'] for name, row in files.items() if name.startswith('tensor.data_'))
        batches = plan_group(raw['tensors'], index, payload_bytes, CAPACITY)
        expected[f'pd-v6-store/D/groups/{name}/rank_{rank}'] = (
            len(batches), sum(span.size for batch in batches for span in batch))
    return expected


def verify_rank(path, rank, model, role, artifact, completion, time_bounds, native, seen):
    trace = [json.loads(line) for line in path.read_text().splitlines()]
    expected_path = f'pd-v6-store/{model}/rank_{rank}'
    start, raw, end = trace[0], trace[-2], trace[-1]
    detail = f'{path} rank {rank}'
    times = [row['ns'] for row in trace]
    need(time_bounds[0] <= times[0] < times[-1] <= time_bounds[1] and
         all(a < b for a, b in zip(times, times[1:])), f'trace time ordering: {detail}')
    need(start['status'] == 'host_confirmed' and start['rank'] == rank and
         start['model_path'] == expected_path and bool(re.fullmatch(UUID, start['host_replica'])) and
         start['staging_bytes'] == CAPACITY, f'host confirmation: {detail}')
    if model == 'K':
        index, ref = metadata(artifact / f'rank_{rank}/tensor_index.json', start['index_sha256'], role)
        total = sum(row[1] for row in index.values())
        windows = (total + CAPACITY - 1) // CAPACITY
        need(end['status'] == 'complete' and end['rank'] == rank and
             end['marlin_conversion'] == 'already_processed' and
             end['serialized_storage_unchanged'] is True, f'Kimi terminal: {detail}')
    else:
        from b2_store_staging_contract import plan, read_rank

        state, index, payload = read_rank(artifact, rank, completion)
        batches = plan(state, index, rank, completion['identity'], payload, CAPACITY)
        total = sum(span.size for batch in batches for span in batch)
        windows = len(batches)
        ref = file_ref(artifact / f'rank_{rank}/tensor_index.json', role)
        need(start['store_sha256'] == COMPLETE_SHA[model] and start['rank_payload_bytes'] == payload and
             end['status'] == 'native_postprocess_complete' and end['kernel_cumem_allocations'] > 0,
             f'GLM manifest/terminal: {detail}')
    need(len(trace) == windows * 3 + 3 and raw['status'] == 'raw_complete' and
         raw['batches'] == windows and raw['copied_bytes'] == total and
         raw['raw_storage_unchanged'] is True, f'raw completion: {detail}')
    for i in range(windows):
        dispatch, confirm, copied = trace[1 + 3*i:4 + 3*i]
        replica = dispatch['replica']
        pair = (expected_path, replica)
        need(dispatch['status'] == 'dispatch' and confirm['status'] == 'confirmed' and
             copied['status'] == 'batch_copied' and
             dispatch['batch'] == confirm['batch'] == copied['batch'] == i and
             dispatch['model_path'] == confirm['model_path'] == expected_path and
             confirm['replica'] == replica and native[pair] == 1 and replica not in seen and
             copied['copied_bytes'] == min((i + 1) * CAPACITY, total),
             f'Store confirmation/copy: {detail}, batch {i}, replica {replica}')
        seen.add(replica)
    return {'rank': rank, 'native_confirmed_batches': windows, 'copied_tensor_bytes': total,
            'trace_window_monotonic_ns': [times[0], times[-1]], 'rank_trace': file_ref(path, role),
            'tensor_index': ref}


def verify_v4(path, role, artifact, completion, native, seen):
    client = native_counts(path, V4_CLIENT)
    ranks = []
    for rank in range(8):
        expected = expected_v4(artifact, rank, completion, role)
        counts = Counter()
        for (tp_rank, group_path, replica), count in client.items():
            if int(tp_rank) != rank:
                continue
            pair = (group_path, replica)
            need(group_path in expected and count == native[pair] == 1 and replica not in seen,
                 f'V4 group/UUID/native confirmation mismatch: {path}, {rank}, {pair}')
            counts[group_path] += 1
            seen.add(replica)
        need(counts == {key: value[0] for key, value in expected.items()},
             f'V4 group-rank count mismatch: {path}, {rank}; got {sum(counts.values())}, '
             f'expected {sum(x[0] for x in expected.values())}')
        ranks.append({'rank': rank, 'native_confirmed_batches': sum(counts.values()),
                      'planner_tensor_bytes': sum(value[1] for value in expected.values()),
                      'groups': {key.rsplit('/', 2)[1]: {'native_confirmed_batches': counts[key],
                                  'planner_tensor_bytes': value[1]} for key, value in expected.items()}})
    return ranks


def verify_local(job, role):
    for name, sha in PLANNER_SHA.items():
        need(file_ref(OVERLAY / name, role)['sha256'] == sha, f'unexpected planner SHA: {name}')
    sys.path.insert(0, str(OVERLAY))
    records = []
    for run in job:
        root = ROOTS[role] / run['run_id']
        server = root / 'sidecars/store.log'
        server_ref = file_ref(server, role)
        native = native_counts(server)
        seen = set()
        for segment in run['segments']:
            model, step = segment['model'], segment['step']
            planned = run['plans'][role]['store_workers'][model]
            target = segment['target'][role]
            ack = target['receipt']
            worker = root / f'{model}/output/{step:02}'
            launch = read(worker / 'launch.json')
            need(target['ok'] is True and ack['stage'] == 'started' and ack['role'] == role and
                 ack['engine_id'] == f"{run['run_id']}-{role}-{model}" and
                 ack['container_id'] == launch['container_id'] and
                 launch['mode'] == 'store' and launch['run_id'] == run['run_id'] and
                 launch['role'] == role and ack['started_ns'] <= launch['started_ns'] <= ack['finished_ns'] and
                 f'{worker}:/output' in launch['argv'] and read(worker / 'worker.json') == planned,
                 f'Store activation identity: {worker}')
            info = planned['store_artifact']
            need(info['logical'] == f'/data/pd-v6-store/{model}' and info['load_format'] == FORMATS[model],
                 f'Store artifact identity: {worker}')
            artifact = Path(info['path'])
            completion = manifest_ref = None
            if model in COMPLETE_SHA:
                completion, manifest_ref = metadata(artifact / 'COMPLETE.json', COMPLETE_SHA[model], role)
            extra_refs = {'store_complete': manifest_ref} if manifest_ref else {}
            if model == 'D':
                mount, = [Path(bind['source']) for bind in planned['mounts_ro'] if bind['target'] == '/pd-store']
                need(f'{mount}:/pd-store:ro' in launch['argv'], f'V4 loader mount: {worker}')
                for name, expected in PLANNER_SHA.items():
                    need(file_ref(mount / name, role)['sha256'] == expected, f'V4 mounted planner: {mount / name}')
                loader = file_ref(mount / 'v4_b2_store_loader.py', role)
                need(loader['sha256'] == V4_LOADER_SHA, f'V4 mounted loader SHA: {mount}')
                extra_refs['mounted_v4_loader'] = loader
                with (worker / 'worker.log').open() as stream:
                    need(any('Initializing a V1 LLM engine' in line and
                             f'load_format={FORMATS[model]},' in line for line in stream),
                         f'V4 engine did not log active Store load format: {worker}')
                ranks = verify_v4(worker / 'worker.log', role, artifact, completion, native, seen)
                byte_key = 'planner_tensor_bytes'
                scope = 'Worker/client and Store/native UUID join. Native confirmation is GPU staging. Destination completion follows the accepted fail-closed loader; byte values are planner-derived.'
            else:
                bounds = ack['started_ns'], ack['finished_ns']
                ranks = [verify_rank(worker / f'{PREFIX[model]}-b2-first-store-rank-{rank}.jsonl',
                                     rank, model, role, artifact, completion, bounds, native, seen)
                         for rank in range(8)]
                byte_key = 'copied_tensor_bytes'
                scope = 'Host confirm, Store/native GPU staging UUID join, observed loader destination copies and rank terminal events.'
            refs = {name: file_ref(worker / name, role) for name in ('launch.json', 'worker.json', 'worker.log')}
            records.append({'run_id': run['run_id'], 'role': role, 'step': step, 'model': model,
                            'container_id': ack['container_id'], 'engine_id': ack['engine_id'],
                            'artifact_root': str(artifact), 'files': refs | extra_refs | {'store_server_log': server_ref},
                            'scope': scope, 'ranks': ranks, byte_key: sum(row[byte_key] for row in ranks),
                            'native_confirmed_batches': sum(row['native_confirmed_batches'] for row in ranks)})
        need(file_ref(server, role) == server_ref, f'Store log changed during audit: {server}')
    return records


def collect(run_id):
    need(re.fullmatch(RUN_ID, run_id), f'invalid run ID: {run_id}')
    root = ROOTS['P'] / run_id
    path = root / 'result.json'
    result = read(path)
    need(result['run_id'] == run_id and result['baseline'] == 'B1' and result['passed'] is True and
         not result['errors'] and len(result['segments']) == result['expected_segments'], f'B1 run not accepted: {run_id}')
    plans = {role: read(root / f'plan-{role}.json') for role in ROOTS}
    refs = [file_ref(path, 'P')]
    for role, plan in plans.items():
        need(plan['run_id'] == run_id and plan['role'] == role and plan['baseline'] == 'B1',
             f'plan identity: {run_id}, {role}')
        refs.append(file_ref(root / f'plan-{role}.json', 'P'))
    segments = []
    for i, segment in enumerate(result['segments']):
        model = segment['model']
        directory = root / f'{i:02}-{model}'
        path = directory / 'segment-result.json'
        need(i == segment['step'] and str(directory) == segment['receipt'] and read(path) == segment,
             f'segment identity: {directory}')
        if segment['action'] == 'start_store_new_worker':
            target = directory / 'transition/target-start.json'
            segments.append({'step': i, 'model': model, 'target': read(target)})
            refs.extend(file_ref(p, 'P') for p in (path, target))
    need(bool(segments), f'no Store start in {run_id}')
    return {'run_id': run_id, 'plans': plans, 'segments': segments}, refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_ids', nargs='*')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--role-local', choices=['D'])
    args = parser.parse_args()
    if args.role_local:
        print(json.dumps(verify_local(json.load(sys.stdin), args.role_local)))
        return
    need(args.run_ids and len(args.run_ids) == len(set(args.run_ids)), 'provide unique B1 run IDs')
    jobs, all_refs = zip(*(collect(run_id) for run_id in args.run_ids))
    p = verify_local(jobs, 'P')
    command = 'python3 -B -c ' + shlex.quote(Path(__file__).read_text()) + ' --role-local D'
    remote = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             '172.31.0.4', command], input=json.dumps(jobs), capture_output=True, text=True)
    need(remote.returncode == 0, f'D audit failed: {remote.stderr}')
    d = json.loads(remote.stdout)
    need([(x['run_id'], x['step'], x['model']) for x in p] ==
         [(x['run_id'], x['step'], x['model']) for x in d], 'missing D/P records')
    for left, right in zip(p, d):
        bytes_key = 'planner_tensor_bytes' if left['model'] == 'D' else 'copied_tensor_bytes'
        need(left[bytes_key] == right[bytes_key] and
             left['native_confirmed_batches'] == right['native_confirmed_batches'],
             f'P/D byte or confirmation coverage differs: {left["run_id"]}, {left["step"]}')
    output = {'schema': 'v6-b1-store-restore-evidence-v1',
              'source': file_ref(Path(__file__), 'P'),
              'planners': [file_ref(OVERLAY / name, 'P') for name in PLANNER_SHA],
              'accepted_run_sources': dict(zip(args.run_ids, all_refs)),
              'starts': [item for paired in zip(p, d) for item in paired]}
    encoded = json.dumps(output, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        with args.out.open('x') as stream:
            stream.write(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
