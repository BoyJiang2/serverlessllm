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
print("CPU contracts only; passing does not resolve the known failed-confirm defect or Store capacity.", flush=True)
expected = suite.countTestCases()
result = unittest.TextTestRunner(verbosity=2).run(suite)
diff = subprocess.check_output(["git", "-C", str(root), "diff", "--binary", "HEAD"])
report = dict(source_head=head, working_diff_sha256=hashlib.sha256(diff).hexdigest(), python=platform.python_version(), selected_tests=expected,
              tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
              skipped=result.skipped, cpu_suite_passed=result.wasSuccessful(),
              v4_runtime_qualified=False,
              scope="Actual source AST / stdlib and C++ CUDA-call stubs only; no GPU/Ray/Store runtime or native extension build",
              known_gaps=["monolithic TP8 capacity rejection; grouped loader implemented; hardware capacity qualification pending",
                          "native terminal confirmation patch is CPU-only; no real IPC/copy failure validation",
                          "no full weights, native token gold, ABI, physical release or complete service validation"])
receipt.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report), flush=True)
sys.exit(0 if result.wasSuccessful() and result.testsRun == expected else 1)
