"""The restart gate must wait for the old backend, not just a CLI reply."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from app.scripts import pinokio_restart_stop as restart_stop


class PinokioRestartStopTests(unittest.TestCase):
    def test_failed_start_without_url_waits_for_real_backend_to_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "app"
            app.mkdir()
            script = app / "launch.py"
            script.write_text("import time\ntime.sleep(30)\n")
            backend = subprocess.Popen(
                [sys.executable, "launch.py"], cwd=app,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            stopped = False
            calls = []

            def fake_pterm(_binary, *args):
                nonlocal stopped
                calls.append(args)
                if args == ("stop", "start.js"):
                    stopped = True
                    return SimpleNamespace(stdout="")
                self.assertEqual(args, ("status", root.name))
                return SimpleNamespace(stdout=json.dumps({
                    "path": str(root),
                    "running_scripts": [] if stopped else ["start.js"],
                    "ready_url": None,
                }))

            try:
                with mock.patch.object(restart_stop, "APP_ROOT", root), mock.patch.object(
                    restart_stop, "_pterm", side_effect=fake_pterm
                ):
                    with self.assertRaises(TimeoutError):
                        restart_stop.stop_and_wait("fake", timeout=0.2)
                    self.assertIsNone(backend.poll())
                    self.assertIn(("stop", "start.js"), calls)
                    backend.terminate()
                    backend.wait(timeout=5)
                    stopped = False
                    restart_stop.stop_and_wait("fake", timeout=1)
            finally:
                if backend.poll() is None:
                    backend.terminate()
                backend.wait(timeout=5)

    def test_failed_start_refuses_incomplete_process_census(self):
        statuses = [{"path": str(restart_stop.APP_ROOT), "ready_url": None,
                     "running_scripts": scripts} for scripts in (["start.js"], [])]
        with mock.patch.object(restart_stop, "_status", side_effect=statuses), mock.patch.object(
            restart_stop, "_pterm"
        ), mock.patch.object(restart_stop, "_starting_backend_drained",
                             side_effect=ValueError("incomplete")):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                restart_stop.stop_and_wait("fake", timeout=1)

    def test_offline_status_does_not_authorize_duplicate_backend(self):
        with mock.patch.object(restart_stop, "_status", return_value={
            "path": str(restart_stop.APP_ROOT), "ready_url": None,
            "running_scripts": [],
        }), mock.patch.object(restart_stop, "_pterm") as command, mock.patch.object(
            restart_stop, "_starting_backend_drained", return_value=False
        ):
            with self.assertRaisesRegex(ValueError, "backend remains"):
                restart_stop.stop_and_wait("fake", timeout=1)
            command.assert_not_called()

    def test_waits_for_old_listener_after_pinokio_reports_script_stopped(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        stopped = False
        calls = []

        def fake_pterm(_binary, *args):
            nonlocal stopped
            calls.append(args)
            if args == ("stop", "start.js"):
                stopped = True
                return SimpleNamespace(stdout="")
            self.assertEqual(args, ("status", restart_stop.APP_ROOT.name))
            return SimpleNamespace(stdout=json.dumps({
                "path": str(restart_stop.APP_ROOT),
                "running_scripts": [] if stopped else ["start.js"],
                "ready_url": f"http://127.0.0.1:{port}",
            }))

        try:
            with mock.patch.object(restart_stop, "_pterm", side_effect=fake_pterm):
                with self.assertRaises(TimeoutError):
                    restart_stop.stop_and_wait("fake", timeout=0.2)
            self.assertIn(("stop", "start.js"), calls)
            self.assertTrue(stopped)
            server.close()
            stopped = False
            with mock.patch.object(restart_stop, "_pterm", side_effect=fake_pterm):
                restart_stop.stop_and_wait("fake", timeout=1)
        finally:
            server.close()

    def test_refuses_running_script_without_verifiable_local_listener(self):
        def fake_pterm(_binary, *args):
            self.assertEqual(args, ("status", restart_stop.APP_ROOT.name))
            return SimpleNamespace(stdout=json.dumps({
                "path": str(restart_stop.APP_ROOT),
                "running_scripts": ["start.js"],
                "ready_url": "https://example.invalid",
            }))

        with mock.patch.object(restart_stop, "_pterm", side_effect=fake_pterm):
            with self.assertRaisesRegex(ValueError, "non-local"):
                restart_stop.stop_and_wait("fake", timeout=0.2)

    def test_refuses_a_different_app_before_sending_stop(self):
        def fake_pterm(_binary, *args):
            self.assertEqual(args, ("status", restart_stop.APP_ROOT.name))
            return SimpleNamespace(stdout=json.dumps({
                "path": str(restart_stop.APP_ROOT.parent),
                "running_scripts": ["start.js"],
                "ready_url": "http://127.0.0.1:42000",
            }))

        with mock.patch.object(restart_stop, "_pterm", side_effect=fake_pterm) as command:
            with self.assertRaisesRegex(ValueError, "different app"):
                restart_stop.stop_and_wait("fake", timeout=0.2)
        self.assertEqual(command.call_count, 1)


if __name__ == "__main__":
    unittest.main()
