"""Bind conditional target NAME coverage to the entire immutable source index."""
import hashlib
import json
import os
from pathlib import Path
import stat

from contract import CONFIG_SHA256, MODEL, REVISION, validate_config
from expert_mapping import parse_expert_key
from nonexpert_mapping import describe

INDEX_SHA256 = '00a982d005d6e39b25cfcb379a7e6d57f393fc0346e70892ea4271c259c0d1e3'
TARGET_CONTRACT_SHA256 = '52a4262b0f607f3dbb975a2408bafa0cfda1cb5a813a6992ae61e25daddc42ae'
METADATA_BYTES = {'config': 5416, 'index': 23597438, 'targets': 95834}


def read_checkpoint_metadata(path, kind):
    """Bound frozen metadata length; callers must still validate its digest."""
    size = METADATA_BYTES[kind]
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular frozen metadata file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(size + 1)
    finally:
        os.close(fd)
    if len(raw) != size:
        raise ValueError('frozen metadata length differs: ' + kind)
    return raw


def encode(document):
    return (json.dumps(document, sort_keys=True, indent=2) + '\n').encode('utf-8')


def build_contract(index_raw, config_raw):
    validate_config(config_raw)
    if hashlib.sha256(index_raw).hexdigest() != INDEX_SHA256:
        raise ValueError('source index differs from frozen official bytes')
    index = json.loads(index_raw)['weight_map']
    targets, categories = {}, {}
    for name in sorted(index):
        if name.endswith(('.weight_packed', '.weight_scale', '.weight_shape')):
            target, category = parse_expert_key(name)['target'], 'expert'
        else:
            rule = describe(name)
            target, category = rule['target'], rule['category']
        targets[target] = targets.get(target, 0) + 1
        categories[category] = categories.get(category, 0) + 1
    if len(index) != 208550 or len(targets) != 1428:
        raise ValueError('unexpected full-index target coverage')
    root = Path(__file__).parent
    return dict(model=MODEL, source_revision=REVISION, config_sha256=CONFIG_SHA256,
                source_index_sha256=INDEX_SHA256, source_name_count=len(index),
                target_source_counts=targets, source_categories=categories,
                mapper_sha256={name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                               for name in ('expert_mapping.py', 'nonexpert_mapping.py')},
                conditional_profile=dict(tp=8, pp=1, use_mla=True, expert_parallel=False,
                                         sequence_parallel_moe=False, encoder_tp_mode='data'),
                name_coverage_only=True, actual_tp_mapping_verified=False,
                runtime_admitted=False)


def load_targets(contract_raw):
    if hashlib.sha256(contract_raw).hexdigest() != TARGET_CONTRACT_SHA256:
        raise ValueError('unreviewed target contract bytes')
    document = json.loads(contract_raw)
    if document['source_revision'] != REVISION or document['source_index_sha256'] != INDEX_SHA256:
        raise ValueError('unfrozen target source identity')
    return set(document['target_source_counts'])
