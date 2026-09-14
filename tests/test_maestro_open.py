"""Model-free tests for the local Maestro launcher helper."""

from __future__ import annotations

import io
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import maestro_open  # noqa: E402


def _completed(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _search_payload(ref: str = maestro_open.DIRECT_APP_REF) -> str:
    return json.dumps(
        {
            "apps": [
                {
                    "app_id": "Maestro.git",
                    "title": "Maestro // Continuum",
                    "ref": ref,
                }
            ]
        }
    )


def _status_payload(
    *,
    running: bool,
    ready: bool,
    ready_url: str | None = None,
    path: Path = maestro_open.REPO_ROOT,
    state: str | None = None,
) -> str:
    return json.dumps(
        {
            "path": str(path),
            "running": running,
            "ready": ready,
            "ready_url": ready_url,
            "state": state or ("ready" if ready else ("starting" if running else "offline")),
        }
    )


class _Runner:
    def __init__(self, statuses: list[str], *, search: str | None = None):
        self.statuses = list(statuses)
        self.search = search or _search_payload()
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, dict(kwargs)))
        if "search" in argv:
            return _completed(self.search)
        if "status" in argv:
            if not self.statuses:
                raise AssertionError("unexpected extra pterm status poll")
            return _completed(self.statuses.pop(0))
        if argv[0].endswith("systemctl"):
            return _completed()
        raise AssertionError(f"unexpected command: {argv}")


class _ControlRecoveryRunner:
    """pterm is offline until the idempotent Pinokio start succeeds."""

    def __init__(self, statuses: list[str], *, foreign: bool = False):
        self.statuses = list(statuses)
        self.foreign = foreign
        self.control_ready = False
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, dict(kwargs)))
        if argv[0].endswith("systemctl"):
            if "pinokio.service" in argv:
                self.control_ready = True
            return _completed()
        if "search" in argv:
            if not self.control_ready:
                return _completed(
                    "",
                    returncode=1,
                    stderr="connect ECONNREFUSED 127.0.0.1:42000",
                )
            return _completed(_search_payload())
        if "status" in argv:
            if not self.control_ready:
                return _completed(
                    "",
                    returncode=1,
                    stderr="connect ECONNREFUSED 127.0.0.1:42000",
                )
            if not self.statuses:
                raise AssertionError("unexpected extra pterm status poll")
            return _completed(self.statuses.pop(0))
        raise AssertionError(f"unexpected command: {argv}")


class _StatusErrorRunner:
    def __init__(self, *, stderr: str):
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, dict(kwargs)))
        if argv[0].endswith("systemctl"):
            return _completed()
        if "search" in argv:
            return _completed(_search_payload())
        if "status" in argv:
            return _completed(
                "",
                returncode=1,
                stderr=self.stderr,
            )
        raise AssertionError(f"unexpected command: {argv}")


class _Response:
    def __init__(self, status: int = 200):
        self.status = status
        self.closed = False

    def close(self):
        self.closed = True


class _Clock:
    def __init__(self, step: float = 0.1):
        self.value = 0.0
        self.step = step

    def __call__(self):
        value = self.value
        self.value += self.step
        return value


class MaestroOpenTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="maestro-open-test-")
        self.pterm = Path(self.tempdir.name) / "pterm"
        self.pterm.write_text("#!/bin/sh\n", encoding="utf-8")
        self.pterm.chmod(0o755)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_resolve_pterm_prefers_path(self):
        configured = Path(self.tempdir.name) / "config.json"
        configured.write_text(json.dumps({"home": "/missing"}), encoding="utf-8")
        selected = maestro_open.resolve_pterm(
            which=lambda name: str(self.pterm),
            config_path=configured,
        )
        self.assertEqual(selected, self.pterm.resolve())

    def test_resolve_pterm_uses_configured_pinokio_home(self):
        home = Path(self.tempdir.name) / "pinokio"
        configured_pterm = home / "bin" / "npm" / "bin" / "pterm"
        configured_pterm.parent.mkdir(parents=True)
        configured_pterm.write_text("#!/bin/sh\n", encoding="utf-8")
        configured_pterm.chmod(0o755)
        config = Path(self.tempdir.name) / "config.json"
        config.write_text(json.dumps({"home": str(home)}), encoding="utf-8")
        selected = maestro_open.resolve_pterm(which=lambda name: None, config_path=config)
        self.assertEqual(selected, configured_pterm.resolve())

    def test_already_running_verifies_both_endpoints_then_opens(self):
        ready_url = "http://127.0.0.1:43210"
        runner = _Runner(
            [_status_payload(running=True, ready=True, ready_url=ready_url)]
        )
        probes: list[str] = []
        opened: list[str] = []

        def opener(request, **kwargs):
            probes.append(request.full_url)
            return _Response()

        result = maestro_open.run(
            ["--pterm", str(self.pterm), "--timeout", "1"],
            command_runner=runner,
            opener=opener,
            browser_opener=lambda url: opened.append(url) or True,
            which_systemctl=lambda name: "/usr/bin/systemctl",
            clock=_Clock(),
            sleep=lambda _: None,
        )

        self.assertEqual(result, 0)
        self.assertEqual(probes, [f"{ready_url}/health", f"{ready_url}/ready"])
        self.assertEqual(opened, [ready_url])
        pterm_calls = [call[0] for call in runner.calls if call[0][0] == str(self.pterm)]
        self.assertEqual([call[1] for call in pterm_calls], ["search", "status"])
        self.assertEqual(
            [argv for argv, _kwargs in runner.calls if argv[0].endswith("systemctl")],
            [["/usr/bin/systemctl", "--user", "start", "pinokio.service"]],
        )
        for _argv, kwargs in runner.calls:
            self.assertNotIn("shell", kwargs)

    def test_not_running_starts_existing_user_service(self):
        ready_url = "http://localhost:43211"
        runner = _Runner(
            [
                _status_payload(running=False, ready=False),
                _status_payload(running=True, ready=True, ready_url=ready_url),
            ]
        )
        result = maestro_open.run(
            ["--pterm", str(self.pterm), "--no-open", "--timeout", "2"],
            command_runner=runner,
            opener=lambda request, **kwargs: _Response(),
            which_systemctl=lambda name: "/usr/bin/systemctl",
            clock=_Clock(),
            sleep=lambda _: None,
        )

        self.assertEqual(result, 0)
        service_calls = [
            argv for argv, _kwargs in runner.calls if argv[0].endswith("systemctl")
        ]
        self.assertEqual(
            service_calls,
            [
                ["/usr/bin/systemctl", "--user", "start", "pinokio.service"],
                ["/usr/bin/systemctl", "--user", "start", maestro_open.SERVICE_NAME],
            ],
        )

    def test_status_is_read_only_when_offline(self):
        runner = _Runner([_status_payload(running=False, ready=False)])
        output = io.StringIO()
        systemctl = mock.Mock(side_effect=AssertionError("--status must not start service"))
        browser = mock.Mock(side_effect=AssertionError("--status must not open browser"))
        with redirect_stdout(output):
            result = maestro_open.run(
                ["--status", "--pterm", str(self.pterm)],
                command_runner=runner,
                which_systemctl=systemctl,
                browser_opener=browser,
            )
        self.assertEqual(result, 0)
        self.assertIn("running=no, ready=no", output.getvalue())
        systemctl.assert_not_called()
        browser.assert_not_called()
        pterm_calls = [call[0] for call in runner.calls if call[0][0] == str(self.pterm)]
        self.assertEqual([call[1] for call in pterm_calls], ["search", "status"])

    def test_status_verifies_ready_url_without_mutation_or_open(self):
        ready_url = "http://127.0.0.1:43212"
        runner = _Runner([_status_payload(running=True, ready=True, ready_url=ready_url)])
        probes: list[str] = []

        def opener(request, **kwargs):
            probes.append(request.full_url)
            return _Response()

        output = io.StringIO()
        with redirect_stdout(output):
            result = maestro_open.run(
                ["--status", "--pterm", str(self.pterm)],
                command_runner=runner,
                opener=opener,
                browser_opener=mock.Mock(),
                which_systemctl=mock.Mock(side_effect=AssertionError("read-only")),
            )
        self.assertEqual(result, 0)
        self.assertEqual(probes, [f"{ready_url}/health", f"{ready_url}/ready"])
        self.assertIn(f"Maestro ready: {ready_url}", output.getvalue())
        self.assertFalse(any("systemctl" in call[0][0] for call in runner.calls))

    def test_foreign_checkout_is_rejected_before_start(self):
        runner = _Runner(
            [
                _status_payload(
                    running=False,
                    ready=False,
                    path=Path(self.tempdir.name) / "other-checkout",
                )
            ]
        )
        with self.assertRaises(maestro_open.ForeignAppError):
            maestro_open.run(
                ["--pterm", str(self.pterm)],
                command_runner=runner,
                which_systemctl=lambda name: "/usr/bin/systemctl",
            )
        self.assertFalse(
            any(maestro_open.SERVICE_NAME in call[0] for call in runner.calls)
        )

    def test_non_loopback_url_is_never_probed_or_opened(self):
        runner = _Runner(
            [
                _status_payload(
                    running=True,
                    ready=True,
                    ready_url="https://example.invalid/maestro",
                )
            ]
        )
        opener = mock.Mock()
        browser = mock.Mock()
        with self.assertRaises(maestro_open.MaestroOpenError) as raised:
            maestro_open.run(
                ["--pterm", str(self.pterm)],
                command_runner=runner,
                opener=opener,
                browser_opener=browser,
            )
        self.assertIn("non-loopback", str(raised.exception))
        opener.assert_not_called()
        browser.assert_not_called()

    def test_timeout_includes_log_command(self):
        runner = _Runner([_status_payload(running=False, ready=False)] * 2)
        with self.assertRaises(maestro_open.MaestroOpenError) as raised:
            maestro_open.run(
                ["--pterm", str(self.pterm), "--no-open", "--timeout", "4"],
                command_runner=runner,
                which_systemctl=lambda name: "/usr/bin/systemctl",
                clock=_Clock(step=1.0),
                sleep=lambda _: None,
            )
        self.assertIn("journalctl --user -u maestro-continuum -n40 --no-pager", str(raised.exception))

    def test_nonfinite_timeout_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            maestro_open.run(["--status", "--pterm", str(self.pterm), "--timeout", "nan"])
        self.assertEqual(raised.exception.code, 2)

    def test_status_error_is_fatal_even_with_pterm_timeout_argument(self):
        runner = _StatusErrorRunner(stderr="HTTP 404: app reference not found")
        with self.assertRaises(maestro_open.CommandFailureError) as raised:
            maestro_open.run(
                ["--pterm", str(self.pterm), "--timeout", "600"],
                command_runner=runner,
                which_systemctl=lambda name: "/usr/bin/systemctl",
            )
        self.assertIn("HTTP 404", str(raised.exception))
        self.assertFalse(
            any(maestro_open.SERVICE_NAME in call[0] for call in runner.calls)
        )
        self.assertFalse(maestro_open._looks_like_control_plane_failure(raised.exception))

    def test_offline_pinokio_is_started_once_then_status_is_rechecked(self):
        ready_url = "http://127.0.0.1:43213"
        runner = _ControlRecoveryRunner(
            [
                _status_payload(running=False, ready=False),
                _status_payload(running=True, ready=True, ready_url=ready_url),
            ]
        )
        result = maestro_open.run(
            ["--pterm", str(self.pterm), "--no-open", "--timeout", "2"],
            command_runner=runner,
            opener=lambda request, **kwargs: _Response(),
            which_systemctl=lambda name: "/usr/bin/systemctl",
            clock=_Clock(),
            sleep=lambda _: None,
        )
        self.assertEqual(result, 0)
        self.assertEqual(
            [argv for argv, _kwargs in runner.calls if argv[0].endswith("systemctl")],
            [
                ["/usr/bin/systemctl", "--user", "start", "pinokio.service"],
                ["/usr/bin/systemctl", "--user", "start", maestro_open.SERVICE_NAME],
            ],
        )

    def test_control_recovery_never_falls_back_from_foreign_checkout(self):
        foreign_path = Path(self.tempdir.name) / "foreign"
        runner = _ControlRecoveryRunner(
            [
                _status_payload(
                    running=False,
                    ready=False,
                    path=foreign_path,
                )
            ]
        )
        with self.assertRaises(maestro_open.ForeignAppError):
            maestro_open.run(
                ["--pterm", str(self.pterm)],
                command_runner=runner,
                which_systemctl=lambda name: "/usr/bin/systemctl",
                clock=_Clock(),
                sleep=lambda _: None,
            )
        self.assertFalse(
            any(maestro_open.SERVICE_NAME in call[0] for call in runner.calls)
        )


if __name__ == "__main__":
    unittest.main()
