"""The restart gate must wait for the old backend, not just a CLI reply."""

import json
import socket
from types import SimpleNamespace
import unittest
from unittest import mock

from app.scripts import pinokio_restart_stop as restart_stop


class PinokioRestartStopTests(unittest.TestCase):
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
