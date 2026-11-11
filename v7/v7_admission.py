"""Validate the recorded GPU window before starting a V7 run."""
import time


def deadline_ns(admission, protocol, minimum_seconds):
    now = time.time_ns()
    checked = admission['checked_at_wall_ns']
    assert 0 <= now - checked < 120 * 10**9, 'Stale GPU admission'
    mode = admission.get('mode')
    resource = protocol.get('resource_id', 'h20/qjhs003')
    if mode in ('direct', 'user_authorized'):
        assert admission['id'] == protocol['direct_admission_id']
        assert protocol['booking_id'] is None
        assert admission['resource_id'] == resource
        start, end = admission['start_ms'], admission['end_ms']
        assert end - start <= 4 * 3600 * 1000
        if mode == 'user_authorized':
            assert protocol['admission_mode'] == mode
        else:
            for booking in admission['snapshot_bookings']:
                if booking['resource_id'] == admission['resource_id']:
                    assert booking['end_ms'] <= start or booking['start_ms'] >= end, 'Direct-use window overlaps a reservation'
    else:
        booking = admission['booking']
        assert booking['id'] == protocol['booking_id']
        assert booking['user'] == 'boyjiang' and booking['resource_id'] == resource
        start, end = booking['start_ms'], booking['end_ms']
    assert start * 10**6 <= now < end * 10**6, 'GPU window is inactive'
    remaining = end * 10**6 - now - 300 * 10**9
    assert remaining >= minimum_seconds * 10**9, 'Insufficient GPU window'
    return time.monotonic_ns() + remaining
