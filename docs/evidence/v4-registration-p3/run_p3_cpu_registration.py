"""Run the current P3 CPU contracts only; no environment installation."""
import importlib
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import unittest

root = Path(sys.argv[1]).resolve()
receipt = Path(sys.argv[2])
sys.path.insert(0, str(root / "sllm_store"))
loader = unittest.TestLoader()
base = root / "sllm_store/tests/python"
suite = loader.discover(str(base), pattern="test_v4_*.py")
extra = ["test_s4a_blocking_confirm", "test_confirm_model_error",
         "test_confirm_model_event_loop", "test_ipc_close_status"]
for name in extra:
    suite.addTests(loader.loadTestsFromModule(importlib.import_module(name)))
path = root / "tests/backend_test/test_native_ready_lifecycle.py"
spec = importlib.util.spec_from_file_location("p3_native_ready_cpu_audit", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
suite.addTests(loader.loadTestsFromModule(module))
head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
print("Source HEAD:", head, flush=True)
print("CPU contracts only; passing does not establish native Store, RPC, GPU, capacity or physical release qualification.", flush=True)
expected = suite.countTestCases()
result = unittest.TextTestRunner(verbosity=2).run(suite)
diff = subprocess.check_output(["git", "-C", str(root), "diff", "--binary", "HEAD", "--", "benchmarks", "sllm", "sllm_store", "tests"])
report = dict(source_head=head, working_diff_sha256=hashlib.sha256(diff).hexdigest(), python=platform.python_version(), selected_tests=expected,
              tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
              skipped=result.skipped, cpu_suite_passed=result.wasSuccessful(),
              v4_runtime_qualified=False,
              scope="Actual source AST and full Model C++ with temporary buffered files and CUDA/pinned-memory substitutes; no GPU/Ray/Store runtime or native extension build",
              known_gaps=["monolithic TP8 capacity rejection; grouped loader implemented; hardware capacity qualification pending",
                          "native pipeline failure propagation is CPU-only; CUDA/IPC and pinned memory remain simulated",
                          "host terminal/pool tests do not qualify production O_DIRECT, pinned memory or RPC",
                          "residency observations are not leases; concurrent native/external eviction must be controlled",
                          "grouped registration uses CPU RPC stand-ins; real worker Ray transport not exercised",
                          "conversion RPC/queue and shutdown are CPU substitutes; actual secure transport is unqualified",
                          "trace hooks and application boundaries are CPU substitutes; actual vLLM/Ray event propagation is unqualified",
                          "event window checks clock/process identity only; no cache/resource/attempt admission",
                          "cache/timing checks are offline consistency only; real mincore provenance and resource admission are unqualified",
                          "attempt journal is bookkeeping only; no external-operation cancellation, complete resource gate or formal PASS",
                          "GLM-field exports are unqualified derived data; missing legacy evidence remains unknown",
                          "registration binding and loader switching use actual Controller/StoreManager source with transport substitutes; no real Ray/HTTP",
                          "no full weights, native token gold, ABI, physical release or complete service validation"])
receipt.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report), flush=True)
sys.exit(0 if result.wasSuccessful() and result.testsRun == expected else 1)
