"""Keep compiled artifacts immutable; validate the one mutable autotune log."""
import pathlib
import re
import hashlib

from compilation_guard import artifact_snapshot as complete_snapshot

MUTABLE_LOG = '.cache/flashinfer/0.6.6/90a/flashinfer_jit.log'
AUTOTUNE_LINE = re.compile(r'^\d{4}-\d{2}-\d{2} .* - INFO - autotuner\.py:(262|268) - flashinfer\.jit: \[Autotuner\]: Autotuning process (starts \.\.\.|ends)$')


def artifact_snapshot(home):
    rows = complete_snapshot(home)
    assert MUTABLE_LOG in rows
    del rows[MUTABLE_LOG]
    return rows


def read_mutable_log(home):
    return (pathlib.Path(home)/MUTABLE_LOG).read_bytes()


def check_mutable_log_append(before, after):
    assert after.startswith(before), 'autotune log was truncated or rewritten'
    delta = after[len(before):].decode('utf-8')
    lines = delta.splitlines()
    assert all(AUTOTUNE_LINE.fullmatch(line) for line in lines), 'unexpected JIT log activity'
    return {'allowed_log':MUTABLE_LOG,'appended_bytes':len(after)-len(before),
            'before_bytes':len(before),'after_bytes':len(after),
            'before_sha256':hashlib.sha256(before).hexdigest(),
            'after_sha256':hashlib.sha256(after).hexdigest(),
            'scope':'only known autotuner begin/end notices; autotuning remains charged to warmup',
            'appended_text':delta}
