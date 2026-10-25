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
    assert window['window_authority'] == 'booking_snapshot_and_user_authorized_azure'
    rows = window['bookings']
    assert [row['id'] for row in rows] == ['bk-20261025T1009-fd1a95b3','bk-20261025T1210-5c71ac5d']
    assert all(row['user']=='azure' and row['resource_id']=='h20/qjhs003' for row in rows)
    assert all(row['start_ms'] < row['end_ms'] for row in rows)
    assert all(left['end_ms']==right['start_ms'] for left,right in zip(rows,rows[1:]))
    deadline = datetime.datetime.fromisoformat(window['cleanup_deadline']).timestamp()
    assert deadline == rows[-1]['end_ms']/1000-600
    assert window['guard_timer'] == original['run_id']+'-deadline-v6.timer'
    return window
