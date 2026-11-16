import hashlib
import json
import pathlib
import subprocess
import tempfile
from unittest.mock import patch
import v11_run_b2 as runner

probe = '''
import importlib.util
import hashlib
import os
import pathlib
import sys
import torch
assert not torch.cuda.is_available()
for key in sys.argv[1:]:
    path, expected = key.split("=", 1)
    assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == expected, path
import vllm
assert vllm.__version__ == "0.20.1"
from sllm_store.v7_sleep_worker import V7SleepWorkerExtension
assert hasattr(V7SleepWorkerExtension, "sllm_kimi_checkpoint_diagnostic")
print("No CUDA. Fixed image, Python import, input and overlay hashes match.")
'''
def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

for arm in ("baseline", "candidate"):
    runner.verify_artifacts(arm)
    with tempfile.TemporaryDirectory(dir=runner.CAMPAIGN) as temp:
        d = pathlib.Path(temp)
        (d / "b2-inputs").mkdir()
        (d / "L2" / "worker-K").mkdir(parents=True)
        native = runner.native_input(d, "K", 16)
        with patch.object(runner, "call", lambda *args, **kwargs: "cpu-dry-run"):
            cid, args = runner.worker_container(d, d / "L2", "K", ["TEST"] * 8, native, arm)
        mounts = [args[i+1] for i,arg in enumerate(args) if arg == "--mount"]
        checks = ["/control/input.json=" + digest(native)]
        checks.extend(row["target"] + "=" + row[arm + "_sha256"] for row in runner.MANIFEST["overlays"])
        required_mounts = ["/control/input.json", "/control/script.py",
                           "/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/v7_sleep_worker.py"]
        for dst in required_mounts:
            assert sum(f",dst={dst}," in item for item in mounts) == 1, dst
        checks.append("/control/script.py=" + digest(runner.CAMPAIGN / "v11_sleep_backend.py"))
        checks.append(required_mounts[-1] + "=" + digest(runner.CAMPAIGN / "v7_sleep_worker.py"))
        cmd=["docker", "run", "--rm", "--network", "none", "-e", "NVIDIA_VISIBLE_DEVICES=void"]
        for mount in mounts:
            cmd.extend(("--mount", mount))
        cmd += ["--entrypoint", "/opt/sllm-v4/bin/python", args[-2], "-c", probe, *checks]
        print(arm, flush=True)
        subprocess.run(cmd, check=True)
