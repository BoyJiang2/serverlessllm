"""Current booking boundary tests; no remote calls or resource allocation."""
import datetime,json,pathlib,tempfile,unittest
from unittest.mock import patch
from resource_window_control13 import load_window

root=pathlib.Path(__file__).resolve().parent
parse_time=datetime.datetime.fromisoformat

class Window(unittest.TestCase):
    def check_at(self,epoch,mutate=None):
        with tempfile.TemporaryDirectory() as directory:
            target=pathlib.Path(directory)
            (target/'run-manifest.json').write_bytes((root/'run-manifest.json').read_bytes())
            window=json.loads((root/'resource-window-extension-v3.json').read_text())
            if mutate: mutate(window)
            (target/'resource-window-extension-v3.json').write_text(json.dumps(window))
            with patch('resource_window_control13.datetime.datetime') as clock:
                clock.now.return_value.timestamp.return_value=epoch
                clock.fromisoformat.side_effect=parse_time
                return load_window(target)
    def test_exact_start_accepted(self):self.assertEqual(self.check_at(1793041200)['bookings'][0]['id'],'bk-20261026T1248-ae6148f9')
    def test_before_start_rejected(self):
        with self.assertRaisesRegex(AssertionError,'outside authorized'):self.check_at(1793041199)
    def test_exact_end_rejected(self):
        with self.assertRaisesRegex(AssertionError,'outside authorized'):self.check_at(1793080800)
    def test_different_guard_rejected(self):
        with self.assertRaises(AssertionError):self.check_at(1793041200,lambda w:w.update(cleanup_deadline='2026-10-27T13:49:00+08:00'))
    def test_wrong_booking_rejected(self):
        def change(w):w['bookings'][0]['id']='old-expired-booking'
        with self.assertRaises(AssertionError):self.check_at(1793041200,change)

if __name__=='__main__':unittest.main()
