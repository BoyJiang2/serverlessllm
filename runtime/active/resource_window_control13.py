import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Additive booking-derived window; never rewrites an earlier authorization."""
import datetime
import hashlib
import json


def load_window(root):
    data = (root/'run-manifest.json').read_bytes()
    original = json.loads(data)
    window = json.loads((root/'resource-window-extension-v3.json').read_text())
    assert hashlib.sha256(data).hexdigest() == window['original_manifest_sha256']
    assert window['run_id'] == original['run_id']
    assert window['host'] == original['host'] == 'qjhs3'
    assert window['resource_id'] == original['resource_id'] == 'h20/qjhs003'
    assert window['window_authority'] == 'booking_snapshot_and_user_authorized_boyjiang'
    assert original['uid']==2147 and original['ssh_user']=='boyjiang'
    rows = window['bookings']
    now_ms=datetime.datetime.now(datetime.timezone.utc).timestamp()*1000
    assert rows[0]['start_ms']<=now_ms<rows[-1]['end_ms'], 'outside authorized booking window'
    assert [row['id'] for row in rows] == ['bk-20261026T1248-ae6148f9','bk-20261027T0011-576c391d']
    assert [row['user'] for row in rows]==['boyjiang','boyjiang'] and all(row['resource_id']=='h20/qjhs003' for row in rows)
    assert all(row['start_ms'] < row['end_ms'] for row in rows)
    assert all(left['end_ms']==right['start_ms'] for left,right in zip(rows,rows[1:]))
    deadline = datetime.datetime.fromisoformat(window['cleanup_deadline']).timestamp()
    assert deadline == rows[-1]['end_ms']/1000-600
    assert window['guard_timer'] == original['run_id']+'-deadline-v13.timer'
    return window


def require_guard_deadlines(source, cleanup_deadline):
    deadline=datetime.datetime.fromisoformat(cleanup_deadline)
    epoch=int(deadline.timestamp())
    calendar=deadline.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    guard=(source/'install_guard13.sh').read_text()
    observer=(source/'install_observers13.sh').read_text()
    assert "--on-calendar='"+calendar+"'" in guard, 'guard calendar differs from window'
    assert 'test "$(date -u +%s)" -lt '+str(epoch) in guard, 'guard epoch differs from window'
    assert 'remaining=$((%d-$(date +%%s)))'%epoch in observer, 'observer epoch differs from window'
