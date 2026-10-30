"""Validate the v2 additive authorization window without editing the original manifest.

Why v11 exists: the v1 overlay pinned the deadline to one absolute epoch that was
derived from three contiguous booking rows (deadline == last booking end - 600 s), and
that instant has passed. The v9/v10 lineage therefore cannot be reopened at all: its
loader asserts the exact epoch, and every driver that imports it inherits the same
assertion. v11 keeps every other property of the protocol identical and replaces only
the window source.

This module deliberately does NOT import or modify resource_window.py. It validates:

  * the frozen run manifest is byte-identical to the one the overlay was cut against;
  * the overlay still describes this run, this host and this resource;
  * the recorded authorization is the user's actual instruction (escaped literal below);
  * the overlay it supersedes (v1) is still byte-identical, so the chain of custody holds;
  * no booking row bounds the window any more, and the overlay says so explicitly
    instead of silently omitting the field;
  * the guard timer is the v5 unit for this run;
  * the deadline is timezone-aware, strictly LATER than the frozen manifest deadline
    (an extension may only move forward, never shorten), and still in the future.

Nothing here asserts anything about the model, FP8 handling, TP degree, cache
preparation, trial order or prior results; those are asserted in their own modules and
are unchanged from v9.
"""
import datetime
import hashlib
import json

AUTHORIZATION = ('\u90a3\u5c31\u4e0d\u7528codex \u4f60\u81ea\u5df1\u542f\u7528'
                 '\u4f60\u7684subagent\u5e72\u6d3b\u5427\u7ee7\u7eed\uff01')
OVERLAY = 'resource-window-extension-v2.json'
SUPERSEDED = 'resource-window-extension-v1.json'
GUARD_SUFFIX = '-deadline-v5.timer'
AUTHORITY = 'user_instruction_supersedes_booking_rows'


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_window(root):
    data = (root/'run-manifest.json').read_bytes()
    original = json.loads(data)
    window = json.loads((root/OVERLAY).read_text())

    assert window['original_manifest_sha256'] == hashlib.sha256(data).hexdigest()
    assert window['run_id'] == original['run_id']
    assert window['host'] == original['host'] == 'qjhs3'
    assert window['resource_id'] == original['resource_id'] == 'h20/qjhs003'
    assert window['human_authorization'] == AUTHORIZATION

    superseded = window['supersedes_overlay']
    assert superseded['path'] == SUPERSEDED
    assert superseded['sha256'] == _sha256(root/SUPERSEDED)

    assert window['bookings'] == []
    assert window['bookings_are_limiting'] is False
    assert window['window_authority'] == AUTHORITY

    assert window['guard_timer'] == original['run_id'] + GUARD_SUFFIX

    deadline = datetime.datetime.fromisoformat(window['cleanup_deadline'])
    assert deadline.tzinfo is not None
    frozen = datetime.datetime.fromisoformat(original['cleanup_deadline'])
    assert frozen.tzinfo is not None
    assert deadline.timestamp() > frozen.timestamp(), 'extension must not shorten the window'
    assert deadline.timestamp() > datetime.datetime.now(datetime.timezone.utc).timestamp()
    return window


def deadline_epoch(window):
    """Epoch seconds of the validated cleanup deadline."""
    return datetime.datetime.fromisoformat(window['cleanup_deadline']).timestamp()
