"""Guard error paths with Docker stand-ins; real CPU container checks are separate."""
import copy
import tempfile
import subprocess
from pathlib import Path
import time
import unittest
from unittest import mock

import v4_container_guard as guard


CID = "a" * 64


def row(status="created"):
    return dict(Id=CID, Created="fixed-created", Image="sha256:fixed", RestartCount=0,
        Config=dict(Labels={guard.LABEL: "native-reference"}),
        HostConfig=dict(RestartPolicy=dict(Name="no"), AutoRemove=False),
        State=dict(Status=status, Running=status == "running",
                   Pid=123 if status == "running" else 0, ExitCode=0))


class ContainerGuardContracts(unittest.TestCase):
    def run_guard(self, states, docker=None):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(guard, "inspect", side_effect=states), \
                mock.patch.object(guard, "docker", docker or mock.Mock(return_value="")), \
                mock.patch.object(guard.signal, "signal"):
            return guard.watch(CID, time.monotonic_ns() + 10_000_000_000, Path(temp) / "receipt")

    def test_refuses_names_before_docker(self):
        with mock.patch.object(guard, "inspect") as inspect:
            with self.assertRaisesRegex(ValueError, "immutable"):
                guard.watch("friendly-name", time.monotonic_ns() + 1_000_000, "unused")
            inspect.assert_not_called()

    def test_rejects_restart_autoremove_unlabelled_or_running_before_arming(self):
        original = row()
        for change in (lambda r: r["HostConfig"].update(AutoRemove=True),
                       lambda r: r["HostConfig"].update(RestartPolicy=dict(Name="always")),
                       lambda r: r["Config"].update(Labels={}),
                       lambda r: r.update(State=row("running")["State"])):
            bad = copy.deepcopy(original)
            change(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_guard([bad])

    def test_normal_exit_is_not_physical_cleanup(self):
        kill = mock.Mock()
        result = self.run_guard([row(), row("exited"), row("exited")], kill)
        kill.assert_not_called()
        self.assertTrue(result["container_stopped"])
        self.assertFalse(result["physical_cleanup_verified"])

    def test_inspection_failure_kills_exact_id_and_retains_failure(self):
        kill = mock.Mock(return_value="killed")
        result = self.run_guard([row(), RuntimeError("daemon error"), row("exited")], kill)
        kill.assert_called_once_with("kill", "--signal", "KILL", CID)
        self.assertEqual(result["reason"], "guard_error")
        self.assertIn("daemon error", result["error"])

    def test_unknown_cleanup_never_admits(self):
        kill = mock.Mock(side_effect=TimeoutError("kill timeout"))
        result = self.run_guard([row(), RuntimeError("inspect"), RuntimeError("inspect")], kill)
        self.assertFalse(result["container_stopped"])
        self.assertIn("kill_error", result)
        self.assertIn("final_inspect_error", result)

    def test_observation_timeout_does_not_kill_a_live_target_or_extend_deadline(self):
        kill = mock.Mock()
        result = self.run_guard([row(), subprocess.TimeoutExpired(['docker', 'inspect', CID], 1),
                                 row('exited'), row('exited')], kill)
        kill.assert_not_called()
        self.assertEqual(result['reason'], 'container_exit')


if __name__ == "__main__":
    unittest.main()
