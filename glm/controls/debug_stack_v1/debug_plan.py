"""Debug-only GPU stack plan derived from the frozen C23 templates and mount rule; never executes."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import time

HERE = Path(__file__).resolve().parent
ACTIVE = HERE.parents[1]/'runtime/active'
HOST = PurePosixPath('/data/brick1/boyjiang/glm5.1-fp8')
RUN_ALIAS = PurePosixPath('/r')
IMAGE = 'sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'
RUN_PATTERN = r'sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}'
PURPOSE = 'DEBUG-UNREVIEWED'
# formal13 drops exactly these switches from the service template (materialize_control13.expected_spec).
FORMAL_DROPPED_ENV = ('SLLM_VALIDATE_FULL_MODEL', 'SLLM_VERIFY_WEIGHTS', 'SLLM_SERVICE_WEIGHT_AUDIT_DIR',
                      'SLLM_EXPORT_RAW_STORE', 'VLLM_ALLOW_INSECURE_SERIALIZATION', 'SLLM_POOL_DIAGNOSTIC',
                      'SLLM_NORMAL_FLAGS_SMOKE')
MIN_LIFETIME, MAX_LIFETIME = 300, 7200
# The worker must outlive the Store's 240 s SIGINT grace (budget_control13.STORE_SHUTDOWN_GRACE).
KILL_AFTER = dict(head='60s', worker='300s')
# The host guard (debug_guard.py) removes a container once lifetime + kill grace + teardown margin have passed.
DEADLINE_GRACE = 600
DEBUG_ENTRY = '/experiment/code/experiment/debug_service.py'
DEBUG_ENV_ADDED = ('SLLM_DEBUG_WEIGHT_FORMAT', 'SLLM_DEBUG_QUERY')
WEIGHT_FORMATS = ('safetensors', 'serverless_llm')


def _load_docker_args():
    # One argv builder: the frozen-C23 extraction already pinned by service_smoke_v1's regression test.
    spec = importlib.util.spec_from_file_location('service_smoke_docker_args', HERE.parent/'service_smoke_v1/docker_args.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.docker_create_args


docker_create_args = _load_docker_args()


def debug_run_root(run_id):
    return HOST/'debug'/run_id


def nonformal_mounts(source, run_root):
    """workspace.container_mounts() for a non-formal manifest, with the code tree and writable run root as inputs."""
    root = HOST
    old = root/'archive/original-run'
    source = PurePosixPath(source)
    readonly = [(root/'checkpoint', 'checkpoint'), (root/'store/tp8', 'store/vllm/glm51-fp8'),
                (source, 'code/experiment'), (root/'code/ServerlessLLM', 'code/ServerlessLLM'),
                (root/'code/Store-source-v7', 'code/Store-source-v7'), (root/'code/weight_contract.py', 'code/weight_contract.py')]
    readonly += [(source/'store_server13.py', 'code/Store-source-v7/sllm_store/server.py')]
    readonly += [(old/name, name) for name in ('.venv', 'serving-deps', 'runtime-metadata', 'buildtools')]
    readonly += [(root/'protocol/run-manifest.json', 'protocol/run-manifest.json')]
    readonly += [(root/'validation'/name, 'validation/'+name)
                 for name in ('native-gold.json', 'native-weight-contract.json', 'store-restore-validation.json')]
    readonly += [(root/'validation'/('native-repeat'+n), 'validation/native_repeat'+n) for n in ('7', '8')]
    rows = [dict(source=s.as_posix(), destination='/experiment/'+d, readonly=True) for s, d in readonly]
    rows.append(dict(source=PurePosixPath(run_root).as_posix(), destination=str(RUN_ALIAS), readonly=False))
    return rows


def head_spec(active=ACTIVE):
    return json.loads((Path(active)/'headservice13a.json').read_text())


def worker_spec(head_ip, active=ACTIVE):
    spec = json.loads((Path(active)/'service13a-template.json').read_text())
    spec['env']['SLLM_PRIVATE_RAY_ADDRESS'] = spec['env']['SLLM_PRIVATE_RAY_ADDRESS'].replace('PENDING_OBSERVED_HEAD_IP', head_ip)
    return spec


def lifetime_wrapper(seconds, role):
    if type(seconds) is not int or not MIN_LIFETIME <= seconds <= MAX_LIFETIME:
        raise ValueError('debug lifetime must be %d-%d whole seconds' % (MIN_LIFETIME, MAX_LIFETIME))
    # --foreground: TERM reaches only the entry, which then stops its children in the formal order
    # (Store gets SIGINT and its grace); without it timeout signals the whole process group.
    return ['/usr/bin/timeout', '--foreground', '--signal=TERM', '--kill-after='+KILL_AFTER[role], str(seconds)+'s']


def overlay(spec, role, seconds, weight_format='safetensors', query=True, supplement_condition=None, supplement_start_sha=None):
    """Only argv, the formal-dropped switches and the two debug switches may differ from the frozen derivation."""
    result = copy.deepcopy(spec)
    if role == 'head':
        result['argv'] = lifetime_wrapper(seconds, role)+spec['argv']
    elif role == 'worker':
        if weight_format not in WEIGHT_FORMATS or type(query) is not bool:
            raise ValueError('weight format must be safetensors or serverless_llm; query must be bool')
        # The formal supervisor needs the root guard and monitor heartbeat; the debug entry does not.
        result['argv'] = lifetime_wrapper(seconds, role)+['/experiment/.venv/bin/python', '-u', DEBUG_ENTRY]
        for key in FORMAL_DROPPED_ENV:
            result['env'].pop(key, None)
        result['env'].update(SLLM_DEBUG_WEIGHT_FORMAT=weight_format, SLLM_DEBUG_QUERY='1' if query else '0')
        if supplement_condition is not None:
            choices = ('safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit')
            if supplement_condition not in choices or not query or weight_format != (
                    'safetensors' if supplement_condition.startswith('safetensors_') else 'serverless_llm'):
                raise ValueError('supplement condition and format/query differ')
            result['env']['SLLM_SUPPLEMENT_CONDITION'] = supplement_condition
            if not re.fullmatch('[0-9a-f]{64}', supplement_start_sha or ''):
                raise ValueError('supplement controller registration SHA required')
            result['env']['SLLM_SUPPLEMENT_START_SHA'] = supplement_start_sha
    else:
        raise ValueError('role must be head or worker')
    return result


def debug_args(spec, run_id, holder, source, deadline_unix):
    if not re.fullmatch(RUN_PATTERN, run_id):
        raise ValueError('debug run identity required; formal identities are rejected')
    if not re.fullmatch('[0-9a-f]{32}', holder):
        raise ValueError('32-hex holder token required')
    if type(deadline_unix) is not int or deadline_unix <= 0:
        raise ValueError('integer guard deadline required')
    args = docker_create_args(spec, run_id, nonformal_mounts(source, debug_run_root(run_id)), IMAGE)
    args[2:2] = ['--pull=never', '--label=sllm.purpose='+PURPOSE, '--label=sllm.holder='+holder,
                 '--label=sllm.deadline_unix=%d' % deadline_unix]
    return args


def plan(run_id, holder, seconds, head_ip, weight_format='safetensors', query=True, active=ACTIVE, deadline_unix=None, supplement_condition=None, supplement_start_sha=None, startup_only=False, startup_stage='sleep'):
    """The worker half needs the head's observed bridge IP, so `up` re-plans after the head starts."""
    source = debug_run_root(run_id)/'source'
    head = overlay(head_spec(active), 'head', seconds)
    worker = overlay(worker_spec(head_ip, active), 'worker', seconds, weight_format, query, supplement_condition, supplement_start_sha)
    if supplement_condition is not None:
        # C23's formal derivation gives both Ray nodes the same event directory.
        head['env']['SLLM_EVENT_DIR'] = worker['env']['SLLM_EVENT_DIR']
    if startup_stage not in ('sleep', 'ray') or (startup_stage != 'sleep' and not startup_only):
        raise ValueError('Ray startup stage requires startup-only control')
    if startup_only:
        if supplement_condition != 'safetensors_disk_cold' or not query or weight_format != 'safetensors':
            raise ValueError('startup control requires the reviewed safetensors cold plan')
        if startup_stage == 'sleep':
            worker['argv'] = lifetime_wrapper(seconds, 'worker')+['/bin/sleep', '30']
        else:
            worker['env']['SLLM_STARTUP_RAY_ONLY'] = '1'
    if deadline_unix is None:
        deadline_unix = int(time.time())+seconds+DEADLINE_GRACE
    return dict(run_id=run_id, purpose=PURPOSE, image_id=IMAGE, lifetime_seconds=seconds, deadline_unix=deadline_unix,
                run_root=str(debug_run_root(run_id)), source=str(source),
                head=dict(spec=head, docker_argv=debug_args(head, run_id, holder, source, deadline_unix)),
                worker=dict(spec=worker, docker_argv=debug_args(worker, run_id, holder, source, deadline_unix)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Print the debug stack plan; nothing is executed.')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--holder', required=True)
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--head-ip', default='PENDING_OBSERVED_HEAD_IP')
    args = parser.parse_args()
    print(json.dumps(plan(args.run_id, args.holder, args.seconds, args.head_ip), indent=2))
