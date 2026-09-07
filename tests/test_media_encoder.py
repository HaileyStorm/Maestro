"""CPU-only regressions for bounded owned media encoder subprocesses."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from shared.utils.media_encoder import EncoderProcess, run_encoder  # noqa: E402
import shared.utils.media_encoder as media_encoder  # noqa: E402


MONITOR_NAME = "maestro-media-encoder-monitor"


def _encoder_monitors() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == MONITOR_NAME]


class MediaEncoderTests(unittest.TestCase):
    def tearDown(self) -> None:
        self.assertEqual(_encoder_monitors(), [])

    def test_windows_child_preserves_hidden_console_and_process_group(self):
        child = Mock(returncode=0)
        child.poll.return_value = 0
        child.wait.return_value = 0
        child.stdin.closed = False
        startup = Mock(dwFlags=0)
        with patch.object(media_encoder.sys, "platform", "win32"), \
                patch.object(subprocess, "STARTUPINFO", return_value=startup, create=True), \
                patch.object(subprocess, "STARTF_USESHOWWINDOW", 1, create=True), \
                patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch.object(subprocess, "Popen", return_value=child) as launch:
            self.assertEqual(run_encoder(["encoder"], timeout=2), 0)
        self.assertIs(launch.call_args.kwargs["startupinfo"], startup)
        self.assertEqual(startup.dwFlags, 1)
        self.assertEqual(launch.call_args.kwargs["creationflags"], 512)

    def test_streams_input_and_leaves_caller_stdout_open(self):
        with tempfile.TemporaryFile() as output:
            command = [
                sys.executable,
                "-c",
                "import sys; data = sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(data.upper())",
            ]
            with EncoderProcess(command, timeout=2, stdout=output) as encoder:
                self.assertEqual(len(_encoder_monitors()), 1)
                self.assertFalse(_encoder_monitors()[0].daemon)
                encoder.write(b"bounded input")
                self.assertEqual(encoder.finish(), 0)
                self.assertEqual(encoder.close(), 0)
            self.assertFalse(output.closed)
            output.seek(0)
            self.assertEqual(output.read(), b"BOUNDED INPUT")

    def test_run_encoder_returns_nonzero_and_forwards_stdout(self):
        with tempfile.TemporaryFile() as output:
            command = [
                sys.executable,
                "-c",
                "import sys; print('{\"duration\": 1.25}'); sys.exit(7)",
            ]
            self.assertEqual(run_encoder(command, timeout=2, stdout=output), 7)
            self.assertFalse(output.closed)
            output.seek(0)
            self.assertEqual(output.read().strip(), b'{"duration": 1.25}')

    def test_timeout_interrupts_a_synchronous_blocked_pipe_write(self):
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "^Encoder operation timed out$"):
            with EncoderProcess(command, timeout=0.15) as encoder:
                encoder.write(b"x" * (8 * 1024 * 1024))
        self.assertLess(time.monotonic() - started, 3)

    @unittest.skipUnless(os.name == "posix", "POSIX signal behavior required")
    def test_timeout_kills_and_reaps_child_that_ignores_termination(self):
        processes: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        command = [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(30)",
        ]
        with tempfile.TemporaryFile() as output, patch.object(
            media_encoder.subprocess, "Popen", side_effect=capture,
        ):
            with self.assertRaisesRegex(TimeoutError, "^Encoder operation timed out$"):
                run_encoder(command, timeout=1, stdout=output)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        self.assertEqual(processes[0].returncode, -signal.SIGKILL)

    def test_cancellation_reaps_owned_child(self):
        processes: list[subprocess.Popen[bytes]] = []
        checks = 0
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def cancel_after_start() -> bool:
            nonlocal checks
            checks += 1
            return checks > 1

        with patch.object(media_encoder.subprocess, "Popen", side_effect=capture):
            with self.assertRaisesRegex(InterruptedError, "^Encoder operation cancelled$"):
                run_encoder(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    timeout=2,
                    abort_check=cancel_after_start,
                )
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)

    def test_pre_cancel_does_not_start_a_child(self):
        with patch.object(media_encoder.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(InterruptedError, "^Encoder operation cancelled$"):
                with EncoderProcess(
                    [sys.executable, "-c", "raise SystemExit"],
                    timeout=1,
                    abort_check=lambda: True,
                ):
                    self.fail("pre-cancelled context entered")
        popen.assert_not_called()

    def test_callback_exception_is_preserved_after_reaping(self):
        marker = RuntimeError("callback failed")
        processes: list[subprocess.Popen[bytes]] = []
        checks = 0
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def fail_after_start() -> bool:
            nonlocal checks
            checks += 1
            if checks > 1:
                raise marker
            return False

        with patch.object(media_encoder.subprocess, "Popen", side_effect=capture):
            with self.assertRaises(RuntimeError) as caught:
                run_encoder(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    timeout=2,
                    abort_check=fail_after_start,
                )
        self.assertIs(caught.exception, marker)
        self.assertIsNotNone(processes[0].returncode)

    def test_broken_pipe_is_path_free_and_child_is_reaped(self):
        private_marker = "/private/output.mov"
        processes: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        command = [sys.executable, "-c", "raise SystemExit(0)", private_marker]
        with patch.object(media_encoder.subprocess, "Popen", side_effect=capture):
            with self.assertRaises(RuntimeError) as caught:
                with EncoderProcess(command, timeout=2) as encoder:
                    processes[0].wait(timeout=1)
                    encoder.write(b"data")
        self.assertEqual(str(caught.exception), "Encoder process closed its input unexpectedly")
        self.assertNotIn(private_marker, str(caught.exception))
        self.assertIsNotNone(processes[0].returncode)

    def test_caller_exception_stops_child_and_joins_monitor(self):
        processes: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        marker = ValueError("caller failure")
        with patch.object(media_encoder.subprocess, "Popen", side_effect=capture):
            with self.assertRaises(ValueError) as caught:
                with EncoderProcess(
                    [sys.executable, "-c", "import time; time.sleep(30)"], timeout=2,
                ):
                    raise marker
        self.assertIs(caught.exception, marker)
        self.assertIsNotNone(processes[0].returncode)

    def test_monitor_start_failure_reaps_child_with_path_free_error(self):
        processes: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(media_encoder.subprocess, "Popen", side_effect=capture), patch.object(
            media_encoder.threading.Thread, "start", side_effect=RuntimeError("thread failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "^Encoder monitor could not be started$"):
                with EncoderProcess(
                    [sys.executable, "-c", "import time; time.sleep(30)"], timeout=2,
                ):
                    self.fail("context entered without a monitor")
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)

    def test_timeout_validation_and_startup_failure_are_path_free(self):
        for invalid in (0, -1, float("inf"), float("nan"), True, "invalid"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "^timeout must be a finite positive number$",
            ):
                EncoderProcess([sys.executable], timeout=invalid)

        private_marker = "/private/missing-encoder"
        with self.assertRaises(RuntimeError) as caught:
            with EncoderProcess([private_marker], timeout=1):
                self.fail("missing command started")
        self.assertEqual(str(caught.exception), "Encoder process could not be started")
        self.assertNotIn(private_marker, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
