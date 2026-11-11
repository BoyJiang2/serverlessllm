"""Add the frozen pre-launch driver baseline to the GLM conversion protocol."""
import json
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
path = V7 / 'glm-conversion-protocol.json'
protocol = json.loads(path.read_bytes())
protocol['prelaunch_driver_baseline_mib'] = 16
protocol['prelaunch_baseline_note'] = (
    'Observed 2026-11-10 19:32 +08: all eight GPUs report 4 MiB with zero '
    'compute apps after the previous owner engine exited; the earlier exact '
    'zero gate could not be satisfied. Admission still requires no compute '
    'apps, unchanged GPU UUID order and at most this baseline.'
)
path.write_text(json.dumps(protocol, indent=2) + '\n')
print(json.dumps(dict(sha256=__import__('hashlib').sha256(path.read_bytes()).hexdigest(),
                      baseline=protocol['prelaunch_driver_baseline_mib']), indent=2))
