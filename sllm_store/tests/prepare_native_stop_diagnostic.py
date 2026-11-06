"""Prepare one native Store-stop diagnostic candidate; never execute its command."""
import argparse
import ast
import copy
import hashlib
import json
import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path("/home/boyjiang/kimi-new/ServerlessLLM")
EVIDENCE = pathlib.Path("/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104")
BASE_SPEC = EVIDENCE / "native-qjhs000-load-evidence-files-concrete-spec-v4-20261105.json"
BASE_COMMAND = EVIDENCE / "native-qjhs000-load-evidence-files-manual-root-command-v4-20261105.json"
BASE_SPEC_SHA = "6d65177058fc887134ec6217f42834225f8956fde9e9eca0cbb039b3b249a4a8"
BASE_COMMAND_SHA = "4c3d149fe6b00a22f34fde96e8515af247fa93209fd6e6514e71de9c3fd2e84b"
RUN = "kimi-native-qjhs000-20261106-store-stop-diagnostic-once"
SPEC_PATH = EVIDENCE / "native-qjhs000-store-stop-diagnostic-concrete-spec-20261106.json"
COMMAND_PATH = EVIDENCE / "native-qjhs000-store-stop-diagnostic-manual-root-command-20261106.json"
HELPER = ROOT / "sllm_store/tests/gpu512_once_controller.py"
HELPER_SHA = "e06c11849d15fdefa828916e210451635ec399b942524eadcc5ab94e84652e66"
BACKEND = "sllm/backends/vllm_backend.py"
OLD_BACKEND = "8e4411465342708ea6c1c6904056f7cb6dbab4d57a74add8149a3abcc345779b"
NEW_BACKEND = "1265299b95d61303c4d0a43c5ab5aefe91261c9e5ac0c5e1cb4dd2c2853deb9d"
RUNNER_TARGET = "/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu_model_runner.py"
OLD_RUNNER = ROOT / "sllm_store/build/kimi-vllm0191-load-evidence-v3/v1/worker/gpu_model_runner.py"
OLD_RUNNER_SHA = "f57d591e0f8b4dcd1ed3fb6cbbe8cf83d75deacdceb80a2d4a10c31b91ea7863"
NEW_RUNNER = ROOT / "sllm_store/build/kimi-vllm0191-load-evidence-files-v1/v1/worker/gpu_model_runner.py"
NEW_RUNNER_SHA = "f7a958706ee01c2c0c1d8c424fa9bfefa4ac80bb20a2eab9a8f70d0a99f351eb"
MANIFEST = NEW_RUNNER.parents[2] / "build-manifest.json"
MANIFEST_SHA = "4707bbb0c3dbf32f633f8740746f38177eb4a51cdc89f908a92d1fa5abd54b7f"
PATCH = ROOT / "sllm_store/vllm_patch/sllm_load.patch"
PATCH_SHA = "9d16dba24979862b8470a1859d7b343e6ad3ee430cc2dcac32bc2185777ed3f4"


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def checked_json(path, expected):
    assert digest(path) == expected, ("frozen input changed", str(path))
    return json.loads(pathlib.Path(path).read_text())


def committed_helper(source_commit):
    assert re.fullmatch("[0-9a-f]{40}", source_commit)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    assert git("rev-parse", "HEAD").decode().strip() == source_commit
    assert not git("status", "--porcelain=v1", "--untracked-files=no").strip(), "tracked source is dirty"
    script = pathlib.Path(__file__).resolve()
    assert git("show", source_commit + ":" + script.relative_to(ROOT).as_posix()) == script.read_bytes(), "generator is not committed"
    relative = HELPER.relative_to(ROOT).as_posix()
    committed = git("show", source_commit + ":" + relative)
    assert HELPER.read_bytes() == committed, "helper differs from committed source"
    function = next(
        node for node in ast.parse(committed).body
        if isinstance(node, ast.FunctionDef) and node.name == "process_stop_snapshot"
    )
    assert [arg.arg for arg in function.args.args] == ["owner", "deadline"]
    assert hashlib.sha256(committed).hexdigest() == HELPER_SHA, "unreviewed helper identity"
    return HELPER_SHA


def validate_input_pins(spec):
    assert spec["native_source_pins"][BACKEND] == OLD_BACKEND
    for path, expected in spec["code_pins"].items():
        assert digest(path) == expected, ("code pin drift", path)
    for relative, expected in spec["native_source_pins"].items():
        allowed = NEW_BACKEND if relative == BACKEND else expected
        assert digest(ROOT / relative) == allowed, ("native pin drift", relative)
    for overlay in spec["overlays"]:
        assert digest(overlay["source"]) == overlay["sha256"]
    for asset in spec["assets"]:
        path = pathlib.Path(asset["path"])
        assert path.stat().st_size == asset["bytes"] and digest(path) == asset["sha256"]
    for path, expected in [
        (NEW_RUNNER, NEW_RUNNER_SHA), (MANIFEST, MANIFEST_SHA), (PATCH, PATCH_SHA),
        (pathlib.Path(spec["guard"]), spec["guard_sha256"]),
        (pathlib.Path(spec["observer"]), spec["observer_sha256"]),
    ]:
        assert digest(path) == expected, ("required pin drift", str(path))


def store_stop_source(original):
    assert original.startswith("def stop_service(name,role,deadline):\n")
    old_body = original.split("\n", 1)[1]
    prefix, finish = old_body.split(";finish(item,deadline)", 1)
    assert finish == "\n if owner:assert gone(owner)"
    # Keep the original owner authentication and SIGINT argv, then observe only.
    branch = "\n".join(" " + line for line in prefix.splitlines())
    return (
        "def stop_service(name,role,deadline):\n"
        " if name=='store':\n" + branch + "\n"
        "  stop_started=time.monotonic();captures=set()\n"
        "  while item[0].poll() is None:\n"
        "   assert time.monotonic()<deadline;healthy();now=time.monotonic()\n"
        "   for at in (10,20):\n"
        "    if now<deadline and now-stop_started>=at and at not in captures:\n"
        "     captures.add(at)\n"
        "     try:record=m.process_stop_snapshot(owner,deadline)\n"
        "     except Exception as error:record={'status':'DIAGNOSTIC_ERROR','error_type':type(error).__name__,'error':str(error)}\n"
        "     record={'elapsed':time.monotonic()-stop_started,'owner':owner,'snapshot':record}\n"
        "     try:save('store-stop-diagnostic-'+str(at)+'.json',record)\n"
        "     except Exception as error:\n"
        "      try:print(json.dumps({'event':'STORE_STOP_DIAGNOSTIC_SAVE_FAILED','at':at,'error_type':type(error).__name__}),flush=True)\n"
        "      except Exception:pass\n"
        "   time.sleep(.5)\n"
        "  finish(item,deadline)\n"
        "  if owner:assert gone(owner)\n"
        "  return\n" + old_body
    )


def render(spec, command, source_commit, helper_sha):
    assert len(spec["role_bodies"]) == 9
    for name, body in spec["role_bodies"].items():
        assert hashlib.sha256(body.encode()).hexdigest() == spec["role_body_sha256"][name]
    old_run = spec["run_id"]
    spec = copy.deepcopy(spec)
    command = copy.deepcopy(command)
    spec["run_id"] = RUN
    spec["source_commit"] = source_commit
    spec["helper_sha256"] = helper_sha
    spec["native_source_pins"][BACKEND] = NEW_BACKEND
    assert spec["helper_sha256"] != "f293c9b94b45d33c1cbe8a301a203ab03e63817e4f5f012bc17d1a1add2bd8cf"
    found = 0
    for overlay in spec["overlays"]:
        if overlay["target"] == RUNNER_TARGET:
            assert overlay["source"] == str(OLD_RUNNER) and overlay["sha256"] == OLD_RUNNER_SHA
            overlay.update(source=str(NEW_RUNNER), sha256=NEW_RUNNER_SHA)
            found += 1
    assert found == 1
    replaced = 0
    for role, request in spec["requests"].items():
        assert request["name"] == old_run + "-" + role
        request["name"] = RUN + "-" + role
        assert request["cmd"] == ["-I", "-c", spec["role_bodies"]["pid1"]]
        for mount in request["mounts"]:
            if mount["Target"] == RUNNER_TARGET:
                assert mount["Source"] == str(OLD_RUNNER) and mount["ReadOnly"] is True
                mount["Source"] = str(NEW_RUNNER)
                replaced += 1
    assert replaced and spec["requests"]["worker"]["env"]["SLLM_LOAD_EVIDENCE_DIR"] == "/evidence/load-evidence"
    spec["code_pins"][str(MANIFEST)] = MANIFEST_SHA
    spec["code_pins"][str(PATCH)] = PATCH_SHA
    spec["status"] = "PREPARED_USER_RUN_STORE_STOP_DIAGNOSTIC_NOT_EXECUTED"
    spec["preparation_scope"] = {
        "base_spec_sha256": BASE_SPEC_SHA,
        "base_command_sha256": BASE_COMMAND_SHA,
        "legacy_runner_inconsistency": "V4 FILE consumer required the reviewed file producer but still mounted the legacy runner; explicitly corrected only that overlay/mount.",
        "native_backend_pin_update": [OLD_BACKEND, NEW_BACKEND],
        "scope": "Same old AAB two lifecycles and full7200; Store-stop read-only diagnostics only. No native fixed32 comparison or execution.",
    }
    argv = command["argv"]
    index = argv.index("-c") + 1
    body = argv[index]
    assert hashlib.sha256(body.encode()).hexdigest() == command["root_python_sha256"]
    node = next(n for n in ast.parse(body).body if isinstance(n, ast.FunctionDef) and n.name == "stop_service")
    original = ast.get_source_segment(body, node)
    changed = store_stop_source(original)
    lines = body.splitlines(keepends=True)
    body = "".join(lines[:node.lineno - 1]) + changed + "\n" + "".join(lines[node.end_lineno:])
    compile(body, "<prepared-native-store-stop>", "exec")
    argv[index] = body
    unit = "--unit=" + old_run + "-controller.service"
    assert argv.count(unit) == 1
    argv[argv.index(unit)] = "--unit=" + RUN + "-controller.service"
    argv[-3] = str(SPEC_PATH)
    spec_bytes = (json.dumps(spec, ensure_ascii=False, indent=2) + "\n").encode()
    command["spec_sha256"] = argv[-2] = hashlib.sha256(spec_bytes).hexdigest()
    command["root_python_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    command["inline_lines"] = len(body.splitlines())
    command["status"] = "PREPARED_USER_RUN_ONLY_NOT_EXECUTED"
    command["store_stop_diagnostic_scope"] = "No real signals added; process_stop_snapshot10/20 within unchanged stop deadline; finish/gone unchanged."
    assert argv[-1] == "FRESH_VERIFIED_BOOKING_EXPIRY_EPOCH_REQUIRED"
    return spec_bytes, (json.dumps(command, ensure_ascii=False, indent=2) + "\n").encode()


def write_new(path, data):
    temporary = path.with_name(path.name + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink()



# This one-off variant derives the already executed, immutable pool-progress profile.
POOL_SPEC = EVIDENCE / "native-qjhs000-pool-progress-concrete-spec-20261106.json"
POOL_COMMAND = EVIDENCE / "native-qjhs000-pool-progress-manual-root-command-20261106.json"
POOL_SPEC_SHA = "539f4c68239f73d9cd48cb7cd9c7143acc8b22d0f40f1c7f6b041aba8c12c0b4"
POOL_COMMAND_SHA = "b72f4d3d29e2e44f796f5ec41c26bb738825f5b206452e9cb193e4d10e8afa5f"
GRACE_RUN = "kimi-native-qjhs000-pool-grace300-20261106-once"
GRACE_SPEC = EVIDENCE / "native-qjhs000-pool-grace300-concrete-spec-20261106.json"
GRACE_COMMAND = EVIDENCE / "native-qjhs000-pool-grace300-manual-root-command-20261106.json"
POOL_BUILD = ROOT / "sllm_store/build/kimi-pool-shutdown-progress-v1"
POOL_ELF_SHA = "9fcb0768be685df4595950cedee3351e2967c91cb6163222e99ca36de003d45c"
POOL_MANIFEST_SHA = "042292ff33ff994bf24504c9b5761287a5ab16ecde0d37542df8f93d6d9954ff"
GRACE_GATE = """ stop_started=time.monotonic();stop_end=stop_started+600
 assert stop_end<started+6600 and stop_end+120+180<end,'full final-stop slot unavailable'
 for role in ('head','worker','observer'):
  p=props(units[role]);assert int(p['MainPID'])>0
  active=int(p['ActiveEnterTimestampMonotonic'])/1e6
  b=s['budgets']['observer' if role=='observer' else role+'_guard']
  parts=re.findall(r'(\\d+)(h|min|s)',p['RuntimeMaxUSec'])
  assert ''.join(n+u for n,u in parts)==p['RuntimeMaxUSec'].replace(' ',''),'unrecognized native runtime'
  native=sum(int(n)*{'h':3600,'min':60,'s':1}[u] for n,u in parts)
  assert native==b['native'] and stop_end+120<active+native,'native release slot unavailable'
  if role=='observer':assert stop_end+120<active+b['duration'],'observer release slot unavailable'
  else:
   go=json.loads((base/('guard-'+role)/'GO.json').read_text())
   assert go['cid']==cids[role] and go['nonce']==arms[role]['nonce']
   assert stop_end<go['monotonic']+b['deadline'],'normal-exit deadline unavailable'
   assert stop_end+120<active+b['overall'],'guard cleanup/release slot unavailable'
"""


def validate_pool_pins(spec):
    assert spec['source_commit'] == '794a0f3b08185d0ec7d054480922b2a5c5e24505'
    manifest = checked_json(POOL_BUILD / 'build-manifest.json', POOL_MANIFEST_SHA)
    elf = POOL_BUILD / '_checkpoint_store.cpython-312-x86_64-linux-gnu.so'
    assert digest(elf) == manifest['elf_sha256'] == POOL_ELF_SHA
    assert spec['code_pins'][str(elf)] == POOL_ELF_SHA
    assert spec['code_pins'][str(POOL_BUILD / 'build-manifest.json')] == POOL_MANIFEST_SHA
    abi = manifest['abi']
    assert abi['torch'] == '2.10.0+cu129' and abi['cuda'] == '12.9' and abi['cxx11_abi'] is True
    assert abi['extension_suffix'] == '.cpython-312-x86_64-linux-gnu.so'
    assert abi['elf_sha256'] == POOL_ELF_SHA and abi['cuda_initialized'] is False and abi['store_instantiated'] is False
    for field in ('source_pins', 'old_artifact_pins'):
        for relative, expected in manifest[field].items():
            assert digest(ROOT / relative) == expected, ('build provenance drift', relative)
    deps = ROOT / 'sllm_store/build/kimi-cpu-deps-verified-20261106'
    assert len(manifest['dependency_pins']) == 331
    for relative, expected in manifest['dependency_pins'].items():
        assert digest(deps / relative) == expected, ('build dependency drift', relative)
    for path, expected in spec['code_pins'].items():
        assert digest(path) == expected, ('code pin drift', path)
    for relative, expected in spec['native_source_pins'].items():
        assert digest(ROOT / relative) == expected, ('native pin drift', relative)
    assert digest(spec['guard']) == spec['guard_sha256']
    assert digest(spec['observer']) == spec['observer_sha256']
    for overlay in spec['overlays']:
        assert digest(overlay['source']) == overlay['sha256']
    for asset in spec['assets']:
        assert pathlib.Path(asset['path']).stat().st_size == asset['bytes'] and digest(asset['path']) == asset['sha256']
    for request in spec['requests'].values():
        mounts = [m for m in request['mounts'] if pathlib.Path(m['Source']).name.startswith('_checkpoint_store.')]
        assert len(mounts) == 1 and mounts[0]['Source'] == str(elf) and mounts[0]['ReadOnly'] is True


def render_pool_grace(spec, command, source_commit, helper_sha):
    spec = copy.deepcopy(spec)
    command = copy.deepcopy(command)
    old_run = spec['run_id']
    assert spec['helper_sha256'] == helper_sha == HELPER_SHA
    for name, body in spec['role_bodies'].items():
        assert hashlib.sha256(body.encode()).hexdigest() == spec['role_body_sha256'][name]
    assert len(spec['role_bodies']) == 9 and spec['budgets']['full'] == 7200
    spec['run_id'] = GRACE_RUN
    spec['source_commit'] = source_commit
    for role, request in spec['requests'].items():
        assert request['name'] == old_run + '-' + role
        request['name'] = GRACE_RUN + '-' + role
    assert spec['budgets']['phase_max'][-2:] == [240, 180]
    spec['budgets']['phase_max'][-2] = 720
    spec['budgets']['phase_names'][-2] = 'orderedstop600+cleanup_release120'
    spec['budgets']['sum'] = sum(spec['budgets']['phase_max'])
    assert spec['budgets']['sum'] == 6540 < 7200
    spec['status'] = 'PREPARED_USER_RUN_POOL_GRACE300_NOT_EXECUTED'
    spec['pool_grace300_scope'] = {
        'base_spec_sha256': POOL_SPEC_SHA, 'base_command_sha256': POOL_COMMAND_SHA,
        'store_seconds': 300, 'ordered_stop_seconds': 600,
        'cleanup_release_reserve_seconds': 120, 'evidence_reserve_seconds': 180,
        'limit': 'One bounded diagnostic, not a completion-time guarantee or minimum threshold. Actual-clock admission may reject simultaneous phase maxima; guards, ownership and capacity unchanged.',
    }
    argv = command['argv']; index = argv.index('-c') + 1; body = argv[index]
    assert hashlib.sha256(body.encode()).hexdigest() == command['root_python_sha256']
    old_gate = ' assert time.monotonic()-started<6360;stop_end=time.monotonic()+240\n'
    old_store = "stop_service('store','worker',min(stop_end,time.monotonic()+30))"
    old_exit = "normal_exit('worker');normal_exit('head');tmp=ob/"
    assert body.count(old_gate) == body.count(old_store) == body.count(old_exit) == 1
    new_store = "store_stop_end=time.monotonic()+300;assert store_stop_end<stop_end,'full Store stop slot unavailable';stop_service('store','worker',store_stop_end)"
    body = body.replace(old_gate, GRACE_GATE).replace(old_store, new_store)
    body = body.replace(old_exit, "normal_exit('worker');normal_exit('head');assert time.monotonic()<stop_end;tmp=ob/")
    message_old = "assert time.monotonic()<deadline;healthy();now=time.monotonic()"
    assert body.count(message_old) == 1
    body = body.replace(message_old, "assert time.monotonic()<deadline,'Store stop deadline exceeded';healthy();now=time.monotonic()")
    compile(body, '<pool-grace300>', 'exec'); argv[index] = body
    old_unit = '--unit=' + old_run + '-controller.service'
    assert argv.count(old_unit) == 1
    argv[argv.index(old_unit)] = '--unit=' + GRACE_RUN + '-controller.service'
    argv[-3] = str(GRACE_SPEC)
    sb = (json.dumps(spec, ensure_ascii=False, indent=2) + '\n').encode()
    command['spec_sha256'] = argv[-2] = hashlib.sha256(sb).hexdigest()
    command['root_python_sha256'] = hashlib.sha256(body.encode()).hexdigest()
    command['inline_lines'] = len(body.splitlines())
    command['status'] = 'PREPARED_USER_RUN_POOL_GRACE300_NOT_EXECUTED'
    command['store_stop_diagnostic_scope'] = spec['pool_grace300_scope']
    return sb, (json.dumps(command, ensure_ascii=False, indent=2) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--pool-grace300", action="store_true", help="Prepare only the fixed 300s Store-stop diagnostic variant")
    args = parser.parse_args()
    if args.pool_grace300:
        spec = checked_json(POOL_SPEC, POOL_SPEC_SHA)
        command = checked_json(POOL_COMMAND, POOL_COMMAND_SHA)
        assert command["spec_sha256"] == POOL_SPEC_SHA
        assert command["argv"][-3:-1] == [str(POOL_SPEC), POOL_SPEC_SHA]
        validate_pool_pins(spec)
        helper_sha = committed_helper(args.source_commit)
        assert not (EVIDENCE.parent / "runs" / GRACE_RUN).exists(), "run identity already used"
        assert not GRACE_SPEC.exists() and not GRACE_COMMAND.exists(), "prepared output already exists"
        sb, cb = render_pool_grace(spec, command, args.source_commit, helper_sha)
        write_new(GRACE_SPEC, sb)
        write_new(GRACE_COMMAND, cb)
        print(json.dumps({"status": "PREPARED_ONLY_NO_EXECUTION", "spec": str(GRACE_SPEC), "command": str(GRACE_COMMAND), "spec_sha256": hashlib.sha256(sb).hexdigest(), "command_sha256": hashlib.sha256(cb).hexdigest()}))
        return
    spec = checked_json(BASE_SPEC, BASE_SPEC_SHA)
    command = checked_json(BASE_COMMAND, BASE_COMMAND_SHA)
    assert command["spec_sha256"] == BASE_SPEC_SHA
    assert command["argv"][-3:-1] == [str(BASE_SPEC), BASE_SPEC_SHA]
    validate_input_pins(spec)
    helper_sha = committed_helper(args.source_commit)
    assert not (EVIDENCE.parent / "runs" / RUN).exists(), "run identity already used"
    assert not SPEC_PATH.exists() and not COMMAND_PATH.exists(), "prepared output already exists"
    spec_bytes, command_bytes = render(spec, command, args.source_commit, helper_sha)
    write_new(SPEC_PATH, spec_bytes)
    write_new(COMMAND_PATH, command_bytes)
    print(json.dumps({
        "status": "PREPARED_ONLY_NO_EXECUTION",
        "source_commit": args.source_commit,
        "helper_sha256": helper_sha,
        "spec": str(SPEC_PATH), "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "command": str(COMMAND_PATH), "command_sha256": hashlib.sha256(command_bytes).hexdigest(),
    }, indent=2))


if __name__ == "__main__":
    main()
