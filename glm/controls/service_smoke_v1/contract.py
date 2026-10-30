"""Draft non-timed native service smoke contract; not runtime admission."""
import re

SCOPE = 'native-service-smoke-v1'
PHASE_SECONDS = (
    ('bootstrap_and_business', 1800),
    ('instance_idle_observation', 60),
    ('store_shutdown', 240),
    ('whole_deployment_release', 120),
    ('evidence', 120),
)
MAX_SECONDS = sum(seconds for _, seconds in PHASE_SECONDS)
MODEL_REVISION = 'f396cf805182f4ca10fa675e1a99815b3ca384db'
IMAGE = 'sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'


def validate_scope(config):
    if config.get('scope') != SCOPE:
        raise ValueError('explicit non-timed smoke scope required')
    if not re.fullmatch(r'sllm-smoke-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', config.get('run_id', '')):
        raise ValueError('fresh smoke identity required; formal IDs cannot be used')
    required = dict(ssh_host='qjhs003', image_id=IMAGE, model_revision=MODEL_REVISION,
                    tensor_parallel_size=8, weight_format='serverless_llm', lifecycle_cycles=1,
                    full_rank_weight_audit=True, business_gold_required=True,
                    fixed_raw_tokens=32, natural_eos_separate=True, formal_ids_consumed=0)
    for key, expected in required.items():
        observed = config.get(key)
        if type(observed) is not type(expected) or observed != expected:
            raise ValueError('smoke constraint differs: '+key)


def budget(begin_monotonic_ns, wall_anchor_ns, authorization_end_ns):
    values = (begin_monotonic_ns, wall_anchor_ns, authorization_end_ns)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError('positive integer clock anchors required')
    required_ns = MAX_SECONDS*10**9
    if authorization_end_ns-wall_anchor_ns < required_ns:
        raise TimeoutError('complete smoke and cleanup budget does not fit authorization')
    end = begin_monotonic_ns
    deadlines = {}
    for phase, seconds in PHASE_SECONDS:
        end += seconds*10**9
        deadlines[phase+'_end_ns'] = end
    return dict(scope=SCOPE, begin_monotonic_ns=begin_monotonic_ns, wall_anchor_ns=wall_anchor_ns,
                authorization_end_ns=authorization_end_ns, deadlines=deadlines,
                guard_deadline_wall_ns=wall_anchor_ns+required_ns,
                maximum_seconds=MAX_SECONDS, runtime_admission=False)
