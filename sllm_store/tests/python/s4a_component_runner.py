"""One S4a attempt: official server and component client in one guarded container.

No SDK import in this parent. External GO25/cleanup65/release60 guards remain
required. A failure barrier proves ordered process containment, not eager SDK
failure cleanup or native IPC-close success. No runtime starts on import.
"""
import argparse
import json
import os
import pathlib
import select
import signal
import socket
import subprocess
import sys
import stat
import time

SDK = pathlib.Path("/source/sllm_store")
COMPONENT = SDK / "tests/python/test_kimi_tensor_component.py"


def startticks(pid):
    try:
        return int(pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except FileNotFoundError:
        return None


def verified_exit(process, identity):
    return process.poll() is not None and startticks(identity[0]) != identity[1]


def stop_owned(process, identity, deadline):
    if process.poll() is None:
        if startticks(process.pid) != identity[1]:
            return False
        process.terminate()
        try:
            process.wait(timeout=max(0, deadline - time.monotonic() - 1))
        except subprocess.TimeoutExpired:
            if startticks(process.pid) != identity[1]:
                return False
            process.kill()
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                return False
    return verified_exit(process, identity)


def graceful_server_stop(server, identity, deadline):
    assert server.poll() is None and startticks(server.pid) == identity[1], "server identity changed"
    server.send_signal(signal.SIGINT)
    return (server.wait(timeout=max(0, deadline - time.monotonic())) == 0
            and verified_exit(server, identity))


def acknowledge_exit(channel, server, identity):
    if not verified_exit(server, identity):
        return False
    channel.sendall(json.dumps({"server_exited": True, "pid": identity[0],
                                "startticks": identity[1]}).encode() + b"\n")
    return True


def failure_order(server, server_identity, client, client_identity, channel, deadline):
    if not stop_owned(server, server_identity, deadline - 1):
        return False
    already_exited = client.poll() is not None
    acknowledged = False
    if not already_exited:
        try:
            acknowledged = acknowledge_exit(channel, server, server_identity)
        except (BrokenPipeError, ConnectionResetError):
            pass
    # Client may rethrow/clean up after ACK; termination is only after server reap.
    try:
        client.wait(timeout=max(0, deadline - time.monotonic() - .5))
    except subprocess.TimeoutExpired:
        if not stop_owned(client, client_identity, deadline):
            return False
    if not verified_exit(client, client_identity):
        return False
    if already_exited:
        return "SERVER_REAPED_CLIENT_ALREADY_EXITED_NO_BARRIER"
    return "SERVER_REAPED_ACK_SENT_CLIENT_REAPED" if acknowledged else "SERVER_REAPED_NO_ACK_CLIENT_REAPED"


def park_failed():
    # No safe ACK/exit if the server cannot be reaped. Native guard is last resort.
    while True:
        try:
            signal.pause()
        except BaseException:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=pathlib.Path, required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    parser.add_argument("--evidence", type=pathlib.Path, required=True)
    parser.add_argument("--go-file", type=pathlib.Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mixed-empty-archive")
    parser.add_argument("--expected-c-sha256")
    args = parser.parse_args()
    assert args.expected_c_sha256 is None or args.mixed_empty_archive, "extension pin requires mixed mode"
    assert all(path.is_absolute() for path in (args.storage, args.receipt, args.evidence))
    expected_go = pathlib.Path("/control/component-go.json") if args.mixed_empty_archive else args.evidence.parent / "component-go.json"
    assert args.go_file == expected_go, "unexpected GO metadata path"
    assert all(not path.is_symlink() for path in (args.go_file, *args.go_file.parents))
    info = args.go_file.stat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o644 and info.st_size <= 1024
    if args.mixed_empty_archive:
        assert info.st_uid == 0 and args.go_file.parent.stat().st_uid == 0
        assert stat.S_IMODE(args.go_file.parent.stat().st_mode) == 0o755
    go = json.loads(args.go_file.read_text())
    assert set(go) == {"monotonic", "run_id"} and go["run_id"] == args.run_id
    go_monotonic = float(go["monotonic"])
    assert 0 <= time.monotonic() - go_monotonic < 4, "future/stale GO or entry margin exhausted"
    args.evidence.mkdir()
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(SDK / "sllm_store") + ":" + env.get("LD_LIBRARY_PATH", "")
    startup_end = go_monotonic + 8
    client_end = go_monotonic + 16
    stop_end = go_monotonic + 21
    server = client = channel = None
    result = {"status": "FAILED_RETAINED", "stage": "startup", "run_id": args.run_id,
              "server_returncode": None, "client_returncode": None,
              "server_stop_kind": "NOT_STARTED", "forced": False}
    logs = []
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 8073))
        code = (f"import sys,asyncio;sys.path.insert(0,{str(SDK)!r});"
                "from sllm_store.server import serve;"
                f"asyncio.run(serve('127.0.0.1',8073,{str(args.storage)!r},1,1048576,67108864,True))")
        server_log = (args.evidence / "server.log").open("xb")
        logs.append(server_log)
        server = subprocess.Popen([sys.executable, "-I", "-c", code], env=env,
                                  stdout=server_log, stderr=subprocess.STDOUT)
        server_identity = (server.pid, startticks(server.pid))
        assert server_identity[1] is not None, "server identity unavailable"
        while True:
            assert server.poll() is None, "server exited before readiness"
            assert time.monotonic() < startup_end, "startup cutoff exceeded"
            try:
                with socket.create_connection(("127.0.0.1", 8073), timeout=.05):
                    break
            except OSError:
                time.sleep(.02)
        result["stage"] = "client"
        channel, child_channel = socket.socketpair()
        client_log = (args.evidence / "client.log").open("xb")
        logs.append(client_log)
        code = (f"import sys,runpy;sys.path.insert(0,{str(SDK)!r});"
                f"runpy.run_path({str(COMPONENT)!r},run_name='__main__')")
        command = [sys.executable, "-I", "-c", code, "--storage", str(args.storage),
                   "--archive", args.archive, "--receipt", str(args.receipt),
                   "--existing-archive", "--failure-barrier-fd", str(child_channel.fileno()),
                   "--server-pid", str(server_identity[0]),
                   "--server-startticks", str(server_identity[1])]
        if args.mixed_empty_archive:
            command += ["--mixed-empty-archive", args.mixed_empty_archive]
        if args.expected_c_sha256 is not None:
            command += ["--expected-c-sha256", args.expected_c_sha256]
        try:
            client = subprocess.Popen(command, env=env, pass_fds=(child_channel.fileno(),),
                                      stdout=client_log, stderr=subprocess.STDOUT)
        finally:
            child_channel.close()
        client_identity = (client.pid, startticks(client.pid))
        assert client_identity[1] is not None, "client identity unavailable"
        ready, _, _ = select.select([channel], [], [], max(0, client_end - time.monotonic()))
        assert ready, "client cutoff exceeded"
        message = channel.recv(2048)
        assert not message, "client requested failure barrier: " + message.decode(errors="replace")
        result["client_returncode"] = client.wait(timeout=max(0, client_end - time.monotonic()))
        assert result["client_returncode"] == 0, "client failed"
        # EOF alone is not success; require the component's final roundtrip receipt.
        records = [json.loads(line) for line in (args.evidence / "client.log").read_text().splitlines()
                   if line.startswith("{")]
        expected_stage = "OFFICIAL_MIXED_EMPTY_COMPONENT_ROUNDTRIP" if args.mixed_empty_archive else "OFFICIAL_COMPONENT_ROUNDTRIP"
        assert records[-1]["stage"] == expected_stage, "roundtrip receipt absent"
        result["stage"] = "server_stop"
        result["server_stop_kind"] = "SIGINT_REQUESTED"
        assert graceful_server_stop(server, server_identity, stop_end), "server did not exit zero after SIGINT"
        result["server_stop_kind"] = "SIGINT_GRACEFUL"
        result.update(status="COMPONENT_ROUNDTRIP_ORDERED_STOP_PASS", stage="complete")
    except BaseException as error:
        result["error"] = repr(error)
        if server is not None:
            result.update(forced=True, server_stop_kind="FAILURE_TERM_KILL_FALLBACK")
            if client is not None:
                safe = failure_order(server, server_identity, client, client_identity, channel, stop_end)
            else:
                safe = stop_owned(server, server_identity, stop_end)
            result.update(server_returncode=server.poll(),
                          client_returncode=client.poll() if client is not None else None)
            if not safe:
                result["barrier"] = "UNVERIFIED_NATIVE_GUARD_REQUIRED"
                (args.evidence / "result.json").write_text(json.dumps(result, sort_keys=True))
                print(json.dumps(result), flush=True)
                park_failed()
            result["barrier"] = safe if client is not None else "SERVER_REAPED_NO_CLIENT"
    finally:
        if channel is not None:
            channel.close()
        for log in logs:
            log.close()
    result.update(server_returncode=server.poll() if server is not None else None,
                  client_returncode=client.poll() if client is not None else None)
    result["elapsed_from_go"] = time.monotonic() - go_monotonic
    (args.evidence / "result.json").write_text(json.dumps(result, sort_keys=True))
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "COMPONENT_ROUNDTRIP_ORDERED_STOP_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
