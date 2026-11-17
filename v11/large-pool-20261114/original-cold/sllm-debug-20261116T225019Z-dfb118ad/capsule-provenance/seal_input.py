import datetime
import hashlib
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
booking, start, end = sys.argv[2:]
raw = path.read_bytes()
review = json.loads(raw)
row, = review["proposed_attempts"]
assert review["verdict"] == "PASS" and row["condition"] == "store_disk_cold"
assert row["status"] == "not_started" and re.fullmatch(r"bk-[A-Za-z0-9-]+", booking)
assert re.fullmatch(r"sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", row["run_id"])
assert re.fullmatch(r"[0-9a-f]{32}", row["holder"])
now = datetime.datetime.now(datetime.timezone.utc)
begin, finish = (datetime.datetime.fromisoformat(value) for value in (start, end))
assert begin.utcoffset() is not None and finish.utcoffset() is not None
assert begin <= now and (finish - now).total_seconds() >= 3900
boot = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
print(json.dumps(dict(run_id=row["run_id"], holder=row["holder"], condition=row["condition"],
                      boot_id=boot, node="qjhs003", uid=2147,
                      review_sha256=hashlib.sha256(raw).hexdigest(),
                      authority_reference=f"Exclusive boyjiang booking {booking}, live API verified by operator",
                      window_start=start, window_end=end, prior_final_sha256={}), indent=2))
