"""Offline contracts for the Workers redirect and launcher registration helper."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "app" / "scripts" / "register_share_url.py"
STATUS_HELPER_PATH = ROOT / "app" / "scripts" / "restart_status.py"
WATCH_HELPER_PATH = ROOT / "app" / "scripts" / "share_registration_watch.py"
TUNNEL_SUPERVISOR_PATH = ROOT / "app" / "scripts" / "quick_tunnel_supervisor.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("maestro_stable_share_helper", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load stable-share helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_status_helper():
    spec = importlib.util.spec_from_file_location(
        "maestro_restart_status_helper", STATUS_HELPER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load restart-status helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_script_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class _RawResponse(_Response):
    def __init__(self, content, *, status=200, headers=None):
        self.content = content
        self.status = status
        self.headers = headers or {}

    def read(self, limit):
        return self.content[:limit]


class StableShareRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.helper = _load_helper()
        self.local = "http://127.0.0.1:7860"
        self.quick = "https://current-tunnel.trycloudflare.com"
        self.stable = "https://maestro.account.workers.dev"
        self.secret = "s" * 64

    def opener(self, *, health_target=None, fail_update=False):
        calls = []
        expected_health_target = health_target or self.quick

        def open_request(request, timeout):
            calls.append(request)
            if request.full_url.endswith("/.well-known/maestro-share/target"):
                if fail_update:
                    raise URLError("unavailable")
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            if request.full_url.endswith("/.well-known/maestro-share/health"):
                return _Response({
                    "ok": True, "configured": True, "target": expected_health_target,
                })
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        return calls, open_request

    def test_healthy_worker_is_updated_verified_and_preferred(self):
        calls, opener = self.opener()
        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].get_method(), "PUT")
        self.assertEqual(json.loads(calls[0].data), {"target": self.quick})
        self.assertEqual(calls[1].get_method(), "GET")
        self.assertEqual(calls[0].get_header("Authorization"), f"Bearer {self.secret}")
        self.assertEqual(calls[1].get_header("Authorization"), f"Bearer {self.secret}")
        registration = calls[2]
        self.assertIsNone(registration.get_header("Authorization"))
        self.assertEqual(json.loads(registration.data), {
            "share_url": self.stable,
            "quick_tunnel_url": self.quick,
            "stable_verified": True,
        })

    def test_all_worker_and_local_requests_use_fixed_json_client_headers(self):
        calls, opener = self.opener()
        self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(len(calls), 3)
        for request in calls:
            with self.subTest(url=request.full_url):
                self.assertEqual(
                    request.get_header("User-agent"),
                    "Maestro-Stable-Share/1.0",
                )
                self.assertEqual(request.get_header("Accept"), "application/json")

        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('_REQUEST_USER_AGENT = "Maestro-Stable-Share/1.0"', source)
        self.assertNotIn("Python-urllib", source)

    def test_unavailable_or_stale_worker_falls_back_to_current_quick_tunnel(self):
        cases = (
            self.opener(fail_update=True),
            self.opener(health_target="https://stale.trycloudflare.com"),
        )
        for calls, opener in cases:
            with self.subTest(case=len(calls)):
                selected = self.helper.register_share_url(
                    self.local,
                    self.quick,
                    stable_url=self.stable,
                    update_secret=self.secret,
                    open_request=opener,
                    sleep=lambda _seconds: None,
                )
                self.assertEqual(selected, (self.quick, "quick"))
                registration = calls[-1]
                self.assertEqual(json.loads(registration.data), {
                    "share_url": self.quick,
                    "quick_tunnel_url": self.quick,
                    "stable_verified": False,
                })

    def test_health_poll_allows_delayed_kv_visibility(self):
        calls = []
        health_reads = 0

        def opener(request, timeout):
            nonlocal health_reads
            calls.append(request)
            if request.full_url.endswith("/.well-known/maestro-share/target"):
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            if request.full_url.endswith("/.well-known/maestro-share/health"):
                health_reads += 1
                target = (
                    "https://stale.trycloudflare.com"
                    if health_reads < 3 else self.quick
                )
                return _Response({"ok": True, "configured": True, "target": target})
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        delays = []
        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=delays.append,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(health_reads, 3)
        self.assertEqual(delays, [5.0, 5.0])

    def test_health_poll_retries_negative_kv_cache_503(self):
        health_reads = 0

        def opener(request, timeout):
            nonlocal health_reads
            if request.full_url.endswith("/.well-known/maestro-share/target"):
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            if request.full_url.endswith("/.well-known/maestro-share/health"):
                health_reads += 1
                if health_reads < 3:
                    raise HTTPError(request.full_url, 503, "not ready", {}, None)
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        delays = []
        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=delays.append,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(health_reads, 3)
        self.assertEqual(delays, [5.0, 5.0])

    def test_backend_replay_does_not_rewrite_or_recheck_worker_target(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        selected = self.helper.replay_share_url(
            self.local,
            self.quick,
            self.stable,
            stable_verified=True,
            open_request=opener,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0].full_url,
            self.local + "/api/v1/access-context/share-url",
        )
        self.assertNotIn("Authorization", dict(calls[0].header_items()))

    def test_secret_never_enters_local_api_body_or_error_text(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if request.full_url.startswith(self.stable):
                raise URLError(self.secret)
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(selected, (self.quick, "quick"))
        local_request = calls[-1]
        self.assertNotIn(self.secret, local_request.full_url)
        self.assertNotIn(self.secret, local_request.data.decode("utf-8"))
        self.assertIsNone(local_request.get_header("Authorization"))
        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("--secret", source)
        self.assertNotIn("print(update_secret", source)
        self.assertIn("_NoRedirect", source)

    def test_stable_origin_validation_is_workers_dev_only(self):
        self.assertEqual(
            self.helper._canonical_workers_dev_url(self.stable + "/"), self.stable,
        )
        for value in (
            "http://maestro.account.workers.dev",
            "https://workers.dev",
            "https://maestro.account.workers.dev.evil.test",
            "https://maestro.account.workers.dev/path",
            "https://maestro.account.workers.dev?token=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper._canonical_workers_dev_url(value)


class StableShareReplayTests(unittest.TestCase):
    def setUp(self):
        self.watch = _load_script_module("maestro_share_watch_test", WATCH_HELPER_PATH)
        self.supervisor = _load_script_module(
            "maestro_quick_tunnel_supervisor_test", TUNNEL_SUPERVISOR_PATH,
        )
        self.local = "http://127.0.0.1:7860"
        self.quick = "https://first-tunnel.trycloudflare.com"
        self.rotated = "https://second-tunnel.trycloudflare.com"

    def test_staged_readiness_allows_healthy_import_beyond_thirty_seconds(self):
        clock = [0.0]

        def now():
            return clock[0]

        def sleep(seconds):
            clock[0] += seconds

        def listener_ready(_origin, _timeout):
            return clock[0] >= 36.0

        def http_probe(_origin, path, _timeout):
            if path == "/health":
                return clock[0] >= 38.0
            return clock[0] >= 42.0

        result = self.watch.wait_for_backend(
            self.local,
            startup_budget_seconds=90.0,
            poll_interval_seconds=2.0,
            now=now,
            sleep=sleep,
            listener_ready=listener_ready,
            http_probe=http_probe,
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.reason, "ready")
        self.assertGreaterEqual(clock[0], 42.0)

    def test_backend_readiness_epoch_replays_same_registration(self):
        readiness = iter((
            self.watch.ReadinessResult(True, "ready"),
            self.watch.ReadinessResult(False, "listener_timeout"),
            self.watch.ReadinessResult(True, "ready"),
        ))
        calls = []
        clock = [0.0]

        def wait_backend(*_args, **_kwargs):
            return next(readiness)

        def register(origin, quick, *_args, **_kwargs):
            calls.append((origin, quick))
            return quick, "quick"

        def sleep(seconds):
            clock[0] += seconds

        with contextlib.redirect_stdout(io.StringIO()):
            result = self.watch.watch_registration(
                self.local,
                self.watch.QuickUrlSource(self.quick, None),
                stable_url="",
                update_secret="",
                refresh_interval_seconds=300.0,
                poll_interval_seconds=1.0,
                max_cycles=3,
                now=lambda: clock[0],
                sleep=sleep,
                wait_backend=wait_backend,
                register=register,
                replay=register,
            )
        self.assertEqual(result, 0)
        self.assertEqual(calls, [(self.local, self.quick), (self.local, self.quick)])

    def test_backend_pid_identity_change_replays_without_a_false_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "backend.pid"
            pid_file.write_text("1001\n", encoding="ascii")
            pid_file.chmod(0o600)
            calls = []
            clock = [0.0]

            def register(origin, quick, *_args, **_kwargs):
                calls.append((origin, quick))
                if len(calls) == 1:
                    pid_file.write_text("1002\n", encoding="ascii")
                return quick, "quick"

            def sleep(seconds):
                clock[0] += seconds

            ready = lambda *_args, **_kwargs: self.watch.ReadinessResult(True, "ready")
            with contextlib.redirect_stdout(io.StringIO()):
                result = self.watch.watch_registration(
                    self.local,
                    self.watch.QuickUrlSource(self.quick, None),
                    stable_url="",
                    update_secret="",
                    backend_pid_file=pid_file,
                    refresh_interval_seconds=300.0,
                    poll_interval_seconds=1.0,
                    max_cycles=2,
                    now=lambda: clock[0],
                    sleep=sleep,
                    wait_backend=ready,
                    register=register,
                    replay=register,
                )
            self.assertEqual(result, 0)
            self.assertEqual(len(calls), 2)

    def test_quick_url_rotation_and_periodic_refresh_are_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            url_file = Path(directory) / "quick.url"
            self.supervisor.publish_quick_url(url_file, self.quick)
            source = self.watch.QuickUrlSource("", url_file)
            calls = []
            clock = [0.0]

            def register(origin, quick, *_args, **_kwargs):
                calls.append((origin, quick))
                if len(calls) == 1:
                    self.supervisor.publish_quick_url(url_file, self.rotated)
                return quick, "quick"

            def sleep(seconds):
                clock[0] += seconds

            ready = lambda *_args, **_kwargs: self.watch.ReadinessResult(True, "ready")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = self.watch.watch_registration(
                    self.local,
                    source,
                    stable_url="",
                    update_secret="",
                    refresh_interval_seconds=2.0,
                    poll_interval_seconds=1.0,
                    max_cycles=5,
                    now=lambda: clock[0],
                    sleep=sleep,
                    wait_backend=ready,
                    register=register,
                    replay=register,
                )
            self.assertEqual(result, 0)
            self.assertEqual(calls[0][1], self.quick)
            self.assertEqual(calls[1][1], self.rotated)
            self.assertGreaterEqual(len(calls), 3)
            self.assertIn(f"MAESTRO_SHARE_UPDATED {self.rotated} quick", output.getvalue())
            self.assertIn("MAESTRO_SHARE_REPLAYED quick", output.getvalue())

    def test_periodic_backend_replay_does_not_reset_worker_retry_deadline(self):
        full_calls = []
        replay_calls = []
        clock = [0.0]

        def register(origin, quick, **_kwargs):
            full_calls.append((origin, quick, clock[0]))
            return quick, "quick"

        def replay(origin, quick, *_args, **_kwargs):
            replay_calls.append((origin, quick, clock[0]))
            return quick, "quick"

        def sleep(seconds):
            clock[0] += seconds

        ready = lambda *_args, **_kwargs: self.watch.ReadinessResult(True, "ready")
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.watch.watch_registration(
                self.local,
                self.watch.QuickUrlSource(self.quick, None),
                stable_url="https://maestro.account.workers.dev",
                update_secret="s" * 64,
                refresh_interval_seconds=1.0,
                worker_retry_interval_seconds=3.0,
                poll_interval_seconds=1.0,
                max_cycles=5,
                now=lambda: clock[0],
                sleep=sleep,
                wait_backend=ready,
                register=register,
                replay=replay,
            )
        self.assertEqual(result, 0)
        self.assertEqual([call[2] for call in full_calls], [0.0, 3.0])
        self.assertGreaterEqual(len(replay_calls), 2)

    def test_status_file_requires_exact_quick_selection_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = self.watch.default_registration_status_file(
                self.local, runtime_dir=root,
            )
            self.watch.secure_publish_runtime_text(
                status,
                json.dumps({
                    "quick_url": self.quick,
                    "selected_url": self.rotated,
                    "kind": "quick",
                }),
            )
            clock = [0.0]
            result = self.watch.wait_for_registration_status(
                self.local,
                self.quick,
                status_file=status,
                budget_seconds=2.0,
                poll_interval_seconds=1.0,
                now=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
            self.assertIsNone(result)

    def test_status_publish_failure_retries_status_without_worker_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            clock = [0.0]
            real_publish = self.watch.secure_publish_runtime_text
            attempts = [0]

            def register(origin, quick, **_kwargs):
                calls.append((origin, quick))
                return quick, "quick"

            def flaky_publish(*args, **kwargs):
                attempts[0] += 1
                if attempts[0] < 3:
                    raise OSError("synthetic status failure")
                return real_publish(*args, **kwargs)

            ready = lambda *_args, **_kwargs: self.watch.ReadinessResult(True, "ready")
            output = io.StringIO()
            with (
                mock.patch.object(self.watch, "secure_publish_runtime_text", flaky_publish),
                contextlib.redirect_stdout(output),
            ):
                result = self.watch.watch_registration(
                    self.local,
                    self.watch.QuickUrlSource(self.quick, None),
                    stable_url="https://maestro.account.workers.dev",
                    update_secret="s" * 64,
                    refresh_interval_seconds=30.0,
                    poll_interval_seconds=1.0,
                    max_cycles=4,
                    now=lambda: clock[0],
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    wait_backend=ready,
                    register=register,
                    replay=register,
                    status_file=Path(directory) / "status.json",
                )
            self.assertEqual(result, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(attempts[0], 3)
            self.assertIn("MAESTRO_SHARE_WATCH_WAIT status_unavailable", output.getvalue())
            self.assertIn(f"MAESTRO_SHARE_READY {self.quick} quick", output.getvalue())

    def test_backend_dead_pid_fails_before_listener_timeout(self):
        result = self.watch.wait_for_backend(
            self.local,
            backend_pid=2_000_000_000,
            startup_budget_seconds=240.0,
            now=lambda: 0.0,
            sleep=lambda _seconds: self.fail("dead PID should not sleep"),
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.reason, "backend_process_exited")

    def test_startup_budget_covers_bounded_worker_verification(self):
        self.assertGreaterEqual(self.watch.DEFAULT_STARTUP_BUDGET_SECONDS, 210.0)
        source = (ROOT / "start.js").read_text(encoding="utf-8")
        self.assertIn("--wait-backend-only", source)

    def test_registration_lease_fences_a_second_live_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.watch.RegistrationLease(
                self.local, runtime_dir=Path(directory),
            )
            second = self.watch.RegistrationLease(
                self.local, runtime_dir=Path(directory),
            )
            with (
                first,
                self.assertRaises(self.watch.LeaseUnavailableError),
                second,
            ):
                self.fail("second registration owner acquired the live lease")
            with second:
                self.assertTrue(second.path.exists())

            with first:
                first.path.unlink()
                first.path.write_bytes(b"0")
                first.path.chmod(0o600)
                with self.assertRaises(self.watch.LeaseUnavailableError):
                    first.assert_current()

    def test_publish_is_atomic_owner_only_and_content_is_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runtime" / "quick.url"
            published = self.supervisor.publish_quick_url(target, self.quick + "/")
            self.assertEqual(published, self.quick)
            self.assertEqual(target.read_text(encoding="utf-8"), self.quick + "\n")
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_publisher_and_watcher_use_the_exact_same_default_runtime_path(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_dir = Path(directory)
            self.assertEqual(
                self.supervisor.runtime_url_file(self.local, runtime_dir=runtime_dir),
                self.watch.default_quick_url_file(self.local, runtime_dir=runtime_dir),
            )
            target = self.supervisor.runtime_url_file(
                self.local, runtime_dir=runtime_dir,
            )
            self.supervisor.publish_quick_url(target, self.quick)
            source = self.watch.QuickUrlSource("", target)
            self.assertEqual(source.current(), self.quick)

    def test_runtime_paths_reject_symlinks_hardlinks_and_replacement_races(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe = root / "safe.url"
            self.supervisor.publish_quick_url(safe, self.quick)

            symlink = root / "symlink.url"
            symlink.symlink_to(safe)
            with self.assertRaises((OSError, ValueError)):
                self.supervisor.publish_quick_url(symlink, self.rotated)
            with self.assertRaises(self.watch.WatchConfigurationError):
                self.watch.QuickUrlSource("", symlink).current()

            hardlink = root / "hardlink.url"
            os.link(safe, hardlink)
            with self.assertRaises((OSError, ValueError)):
                self.supervisor.publish_quick_url(safe, self.rotated)
            with self.assertRaises(self.watch.WatchConfigurationError):
                self.watch.QuickUrlSource("", hardlink).current()
            hardlink.unlink()

            def replace_destination():
                safe.unlink()
                safe.write_text(self.quick + "\n", encoding="utf-8")
                safe.chmod(0o600)

            with self.assertRaises(OSError):
                self.supervisor.publish_quick_url(
                    safe,
                    self.rotated,
                    before_replace=replace_destination,
                )
            self.assertEqual(safe.read_text(encoding="utf-8").strip(), self.quick)
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_runtime_directory_and_lease_reject_link_redirection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_dir = root / "real"
            real_dir.mkdir(mode=0o700)
            linked_dir = root / "linked"
            linked_dir.symlink_to(real_dir, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.supervisor.runtime_url_file(self.local, runtime_dir=linked_dir)

            lease = self.watch.RegistrationLease(self.local, runtime_dir=real_dir)
            lease.path.symlink_to(real_dir / "unrelated")
            with self.assertRaises(self.watch.LeaseUnavailableError):
                with lease:
                    self.fail("unsafe lease path was acquired")

    def test_secure_read_is_bounded_and_never_uses_stale_cached_url(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "quick.url"
            self.supervisor.publish_quick_url(target, self.quick)
            source = self.watch.QuickUrlSource("", target)
            self.assertEqual(source.current(), self.quick)
            target.unlink()
            with self.assertRaises(self.watch.WatchConfigurationError):
                source.current()

            target.write_bytes(b"x" * 513)
            target.chmod(0o600)
            with self.assertRaises(self.watch.WatchConfigurationError):
                self.watch.secure_read_runtime_text(target)

    def test_windows_platform_branch_uses_type_and_link_checks_not_posix_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runtime.url"
            target.write_text(self.quick, encoding="utf-8")
            target.chmod(0o644)
            metadata = target.lstat()
            with mock.patch.object(self.watch, "_posix_security_available", return_value=False):
                self.watch._validate_secure_regular(metadata, reason="runtime_file")
                hardlink = Path(directory) / "hardlink.url"
                os.link(target, hardlink)
                with self.assertRaises(self.watch.WatchConfigurationError):
                    self.watch._validate_secure_regular(
                        target.lstat(), reason="runtime_file",
                    )

    def test_direct_supervisor_restarts_cloudflared_and_publishes_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            target = root / "quick.url"
            fake = root / "fake-cloudflared"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, time\n"
                "state = pathlib.Path(os.environ['MAESTRO_FAKE_TUNNEL_STATE'])\n"
                "count = int(state.read_text() or '0') if state.exists() else 0\n"
                "state.write_text(str(count + 1))\n"
                "pathlib.Path(os.environ['MAESTRO_FAKE_SECRET_OBSERVATION']).write_text("
                "'present' if os.environ.get('PINOKIO_STABLE_SHARE_UPDATE_SECRET') else 'absent')\n"
                "name = 'first-tunnel' if count == 0 else 'second-tunnel'\n"
                "print(os.environ['MAESTRO_FAKE_PRIVATE_LINE'], flush=True)\n"
                "print(f'https://{name}.trycloudflare.com', flush=True)\n"
                "time.sleep(0.05)\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            environment = dict(os.environ)
            environment["MAESTRO_FAKE_TUNNEL_STATE"] = str(state)
            environment["MAESTRO_FAKE_PRIVATE_LINE"] = "private-cloudflared-diagnostic"
            secret_observation = root / "secret-observation"
            environment["MAESTRO_FAKE_SECRET_OBSERVATION"] = str(secret_observation)
            environment["PINOKIO_STABLE_SHARE_UPDATE_SECRET"] = "must-not-reach-cloudflared"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TUNNEL_SUPERVISOR_PATH),
                    "--origin", self.local,
                    "--url-file", str(target),
                    "--cloudflared", str(fake),
                    "--no-watcher",
                    "--max-tunnel-starts", "2",
                    "--startup-budget-seconds", "5",
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertIn(f"MAESTRO_QUICK_TUNNEL_READY {self.quick}", completed.stdout)
            self.assertIn(
                f"MAESTRO_QUICK_TUNNEL_ROTATED {self.rotated}", completed.stdout,
            )
            self.assertEqual(target.read_text(encoding="utf-8").strip(), self.rotated)
            self.assertNotIn("tunnel --url", completed.stdout)
            self.assertNotIn("private-cloudflared-diagnostic", completed.stdout)
            self.assertEqual(secret_observation.read_text(encoding="utf-8"), "absent")

    def test_duplicate_direct_supervisor_is_fenced_before_child_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "quick.url"
            marker = root / "launched"
            fake = root / "fake-cloudflared"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib\n"
                "pathlib.Path(os.environ['MAESTRO_FAKE_LAUNCH_MARKER']).write_text('yes')\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            environment = dict(os.environ)
            environment["MAESTRO_FAKE_LAUNCH_MARKER"] = str(marker)
            with self.watch.RegistrationLease(
                self.local,
                runtime_dir=root,
                namespace="tunnel-supervisor",
            ):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(TUNNEL_SUPERVISOR_PATH),
                        "--origin", self.local,
                        "--url-file", str(target),
                        "--cloudflared", str(fake),
                        "--no-watcher",
                        "--max-tunnel-starts", "1",
                    ],
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            self.assertEqual(
                completed.stdout.strip(), "MAESTRO_QUICK_TUNNEL_ALREADY_RUNNING",
            )
            self.assertFalse(marker.exists())

    def test_terminate_process_lookup_race_still_reaps_child(self):
        class Process:
            def __init__(self):
                self.waits = 0

            def poll(self):
                return None

            def terminate(self):
                raise ProcessLookupError()

            def wait(self, timeout=None):
                self.waits += 1
                return 0

        process = Process()
        self.supervisor._terminate_and_reap(process)
        self.assertEqual(process.waits, 1)

    def test_direct_watcher_integration_registers_after_health_and_ready(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                if self.path not in {"/health", "/ready"}:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_PUT(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                requests.append((self.path, self.headers.get("Origin"), body))
                payload = json.dumps({
                    "status": "ok",
                    "share_url": body["share_url"],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            with tempfile.TemporaryDirectory() as directory:
                runtime_dir = Path(directory)
                quick_file = self.watch.default_quick_url_file(
                    origin, runtime_dir=runtime_dir,
                )
                self.supervisor.publish_quick_url(quick_file, self.quick)
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(WATCH_HELPER_PATH),
                        "--origin", origin,
                        "--quick-url-file", str(quick_file),
                        "--runtime-dir", str(runtime_dir),
                        "--startup-budget-seconds", "10",
                        "--once",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            self.assertIn(f"MAESTRO_SHARE_READY {self.quick} quick", completed.stdout)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][0], "/api/v1/access-context/share-url")
            self.assertEqual(requests[0][1], origin)
            self.assertEqual(requests[0][2]["quick_tunnel_url"], self.quick)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_pinokio_adopts_existing_tunnel_without_starting_another(self):
        source = (ROOT / "start.js").read_text(encoding="utf-8")
        supervisor_commands = [
            line.strip()
            for line in source.splitlines()
            if "quick_tunnel_supervisor.py" in line
        ]
        self.assertEqual(len(supervisor_commands), 3)
        for command in supervisor_commands:
            actions = int("--publish-url" in command) + int("--clear-url" in command)
            self.assertEqual(actions, 1, command)
        self.assertTrue(any("--publish-url {{local.quick_share_url}}" in command for command in supervisor_commands))
        self.assertTrue(any("--publish-url {{local.observed_quick_share_url}}" in command for command in supervisor_commands))
        self.assertTrue(any("--clear-url" in command for command in supervisor_commands))
        self.assertIn("local.$share.cloudflare[local.url]", source)
        self.assertIn('id: "monitor-cloudflare-share"', source)
        self.assertIn("sec: 2", source)
        self.assertIn("local.$share.cloudflare[local.url] !== local.quick_share_url", source)
        self.assertIn("python scripts/register_share_url.py --watch", source)
        registration_commands = [
            line.strip()
            for line in source.splitlines()
            if "python scripts/register_share_url.py" in line
        ]
        self.assertTrue(registration_commands)
        self.assertTrue(all("--watch" in line or "--wait-watch" in line for line in registration_commands))
        self.assertNotIn("--secret", source)

    def test_windows_direct_share_mode_uses_same_supervisor_without_secret_argv(self):
        source = (ROOT / "app" / "scripts" / "run.bat").read_text(encoding="utf-8")
        self.assertIn('if /I "%~1"=="share" goto share', source)
        self.assertIn("python scripts\\quick_tunnel_supervisor.py", source)
        self.assertNotIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET", source)

    def test_failure_output_never_includes_exception_or_environment_secret(self):
        secret = "private-operator-value-" + ("s" * 40)
        original = os.environ.get("PINOKIO_STABLE_SHARE_UPDATE_SECRET")
        os.environ["PINOKIO_STABLE_SHARE_UPDATE_SECRET"] = secret
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = self.watch.main([
                    "--origin", "not-an-origin",
                    "--quick-url", self.quick,
                    "--once",
                ])
        finally:
            if original is None:
                os.environ.pop("PINOKIO_STABLE_SHARE_UPDATE_SECRET", None)
            else:
                os.environ["PINOKIO_STABLE_SHARE_UPDATE_SECRET"] = original
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue().strip(), "MAESTRO_SHARE_WATCH_FAILED invalid_configuration")
        self.assertNotIn(secret, output.getvalue())


class RestartStatusClientTests(unittest.TestCase):
    def setUp(self):
        self.helper = _load_status_helper()
        self.stable = "https://maestro.account.workers.dev"
        self.secret = "operator-secret-" + ("s" * 48)
        self.environ = {
            "PINOKIO_STABLE_SHARE_URL": self.stable,
            "PINOKIO_STABLE_SHARE_UPDATE_SECRET": self.secret,
        }
        self.now = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)

    def payload(self, **overrides):
        values = {
            "state": "planned",
            "reason": "restart",
            "message": "Host restart is planned.",
            "ttl_seconds": 1800,
            "generation": "generation_0123456789",
            "now": self.now,
        }
        values.update(overrides)
        return self.helper.build_status_payload(**values)

    def test_stable_status_url_is_canonical_workers_dev_only(self):
        self.assertEqual(
            self.helper.canonical_stable_url(
                " HTTPS://Maestro.Account.Workers.Dev/ ",
            ),
            self.stable,
        )
        for value in (
            "http://maestro.account.workers.dev",
            "https://workers.dev",
            "https://maestro.account.workers.dev.evil.test",
            "https://maestro.account.workers.dev:443",
            "https://user@maestro.account.workers.dev",
            "https://maestro.account.workers.dev/status",
            "https://maestro.account.workers.dev?secret=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper.canonical_stable_url(value)

    def test_status_requests_use_env_only_secret_and_never_redirect(self):
        calls = []
        expected = self.payload()

        def opener(request, timeout):
            calls.append((request, timeout))
            return _Response({"ok": True, "status": expected})

        status = self.helper.show_status(
            environ=self.environ,
            open_request=opener,
        )
        self.assertEqual(status, expected)
        request, timeout = calls[0]
        self.assertEqual(
            request.full_url,
            self.stable + "/.well-known/maestro-share/status",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            request.get_header("Authorization"),
            f"Bearer {self.secret}",
        )
        self.assertEqual(timeout, 10)
        self.assertIsNone(
            self.helper._NoRedirect().redirect_request(
                request, None, 307, "redirect", {}, "https://evil.test/",
            ),
        )

        source = STATUS_HELPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn('add_argument("--secret"', source)
        self.assertNotIn("print(update_secret", source)
        self.assertNotIn("print(self.secret", source)

    def test_put_and_delete_use_exact_request_shapes(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if request.get_method() == "PUT":
                return _Response({
                    "ok": True,
                    "status": json.loads(request.data),
                })
            return _Response({"ok": True, "cleared": True})

        eta = self.helper.build_eta(at="2026-08-12T18:40:00Z")
        payload = self.helper.build_status_payload(
            state="waiting_for_boundary",
            reason="maintenance",
            message="Host restart is queued.",
            ttl_seconds=1800,
            generation="generation_0123456789",
            eta=eta,
            now=self.now,
        )
        self.helper.set_status(
            payload,
            environ=self.environ,
            open_request=opener,
        )
        self.helper.clear_status(
            "generation_0123456789",
            environ=self.environ,
            open_request=opener,
        )

        self.assertEqual(calls[0].get_method(), "PUT")
        self.assertEqual(json.loads(calls[0].data), {
            "schema_version": 1,
            "generation": "generation_0123456789",
            "state": "waiting_for_boundary",
            "reason": "maintenance",
            "message": "Host restart is queued.",
            "issued_at": "2026-08-12T18:30:00Z",
            "expires_at": "2026-08-12T19:00:00Z",
            "eta": {"kind": "at", "at": "2026-08-12T18:40:00Z"},
        })
        self.assertEqual(calls[0].get_header("Content-type"), "application/json")
        self.assertEqual(calls[1].get_method(), "DELETE")
        self.assertEqual(
            json.loads(calls[1].data),
            {"generation": "generation_0123456789"},
        )

    def test_generation_utc_ttl_and_eta_are_strict_and_bounded(self):
        generated = self.helper.new_generation()
        self.assertRegex(generated, r"^[A-Za-z0-9_-]{16,64}$")
        with self.assertRaises(ValueError):
            self.helper.build_status_payload(
                state="planned",
                reason="restart",
                message="Restart planned.",
                ttl_seconds=60,
                generation="",
                now=self.now,
            )
        self.assertEqual(
            self.helper.canonical_utc("2026-08-12T18:30:00Z"),
            "2026-08-12T18:30:00Z",
        )
        self.assertEqual(
            self.helper.build_eta(
                earliest="2026-08-12T18:40:00Z",
                latest="2026-08-12T18:50:00Z",
            ),
            {
                "kind": "range",
                "earliest": "2026-08-12T18:40:00Z",
                "latest": "2026-08-12T18:50:00Z",
            },
        )
        for value in (
            "2026-08-12T18:30:00+00:00",
            "2026-08-12T18:30:00.000Z",
            "2026-08-12T18:30:00",
            "not-a-time",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper.canonical_utc(value)
        for ttl in (0, 86401):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.helper.build_status_payload(
                    state="planned",
                    reason="restart",
                    message="Restart planned.",
                    ttl_seconds=ttl,
                    now=self.now,
                )

        invalid_schema = self.payload()
        invalid_schema["schema_version"] = True
        with self.assertRaises(ValueError):
            self.helper.validate_status_payload(invalid_schema)
        with self.assertRaises(ValueError):
            self.payload(message="line one\nline two")
        for eta in (
            {"at": "2026-08-12T18:40:00Z"},
            {"kind": "at", "at": "2026-08-12T18:40:00Z", "extra": "x"},
            {
                "kind": "range",
                "earliest": "2026-08-12T18:50:00Z",
                "latest": "2026-08-12T18:40:00Z",
            },
            {"kind": "at", "at": "2026-08-12T18:29:59Z"},
            {"kind": "at", "at": "2026-08-12T19:00:01Z"},
        ):
            with self.subTest(eta=eta), self.assertRaises(ValueError):
                self.helper.build_status_payload(
                    state="planned",
                    reason="restart",
                    message="Restart planned.",
                    ttl_seconds=60,
                    eta=eta,
                    now=self.now,
                )

    def test_response_body_and_errors_are_bounded_and_redacted(self):
        oversized = b"{" + (b"x" * self.helper.MAX_RESPONSE_BYTES) + b"}"
        cases = (
            _RawResponse(b"{}", headers={"Content-Length": "999999"}),
            _RawResponse(oversized),
            _RawResponse(b"[]"),
            _RawResponse(b"{}", status=503),
        )
        for response in cases:
            with self.subTest(status=response.status), self.assertRaises(
                (TypeError, ValueError),
            ):
                self.helper.show_status(
                    environ=self.environ,
                    open_request=lambda _request, _timeout, value=response: value,
                )

        def failing_opener(_request, _timeout):
            raise URLError(self.secret)

        output = []
        with self.assertRaises(SystemExit) as raised:
            self.helper.main(
                ["show"],
                environ=self.environ,
                open_request=failing_opener,
                output=output.append,
            )
        self.assertEqual(str(raised.exception), "Maestro restart-status request failed")
        self.assertNotIn(self.secret, str(raised.exception))
        self.assertEqual(output, [])

    def test_cli_prints_only_content_free_summaries(self):
        calls = []
        current = self.payload(
            state="restarting",
            message="Private maintenance detail",
        )

        def opener(request, timeout):
            calls.append(request)
            if request.get_method() == "GET":
                return _Response({"ok": True, "status": current})
            if request.get_method() == "PUT":
                return _Response({
                    "ok": True,
                    "status": json.loads(request.data),
                })
            return _Response({"ok": True, "cleared": True})

        output = []
        self.helper.main(
            ["show"],
            environ=self.environ,
            open_request=opener,
            output=output.append,
        )
        self.helper.main(
            [
                "set",
                "--state", "verifying",
                "--reason", "restart",
                "--message", "Private maintenance detail",
            ],
            environ=self.environ,
            open_request=opener,
            output=output.append,
        )
        self.helper.main(
            ["clear", "--generation", "generation_0123456789"],
            environ=self.environ,
            open_request=opener,
            output=output.append,
        )
        self.assertEqual(output, [
            "MAESTRO_RESTART_STATUS restarting",
            "MAESTRO_RESTART_STATUS_SET verifying",
            "MAESTRO_RESTART_STATUS_CLEARED",
        ])
        self.assertNotIn("Private maintenance detail", " ".join(output))
        for request in calls:
            self.assertNotIn(self.secret, request.full_url)
            if request.data:
                self.assertNotIn(self.secret, request.data.decode("utf-8"))

    def test_clear_reports_generation_mismatch_truthfully(self):
        output = []
        result = self.helper.main(
            ["clear", "--generation", "generation_0123456789"],
            environ=self.environ,
            open_request=lambda _request, timeout: _Response({
                "ok": True,
                "cleared": False,
            }),
            output=output.append,
        )
        self.assertEqual(result, 1)
        self.assertEqual(output, ["MAESTRO_RESTART_STATUS_NOT_CLEARED"])

        with self.assertRaises(SystemExit) as raised:
            self.helper.main(
                ["clear", "--generation", ""],
                environ=self.environ,
                open_request=lambda _request, timeout: self.fail(
                    "invalid generations must fail before a request",
                ),
                output=output.append,
            )
        self.assertEqual(str(raised.exception), "Maestro restart-status request failed")


class StableShareSourceContracts(unittest.TestCase):
    def test_provision_helpers_parse_exact_stage_and_promotion_contract(self):
        helper_url = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision_helpers.mjs"
        ).as_uri()
        script = f"""
            import {{
              candidateMatches,
              canonicalizeManagedEnvironment,
              encodeCandidateMetadata,
              extractVersionUpload,
              parseCandidateMetadata,
              parseDeploymentReadback,
              parseProvisionArgs,
            }} from {json.dumps(helper_url)};
            const versionId = "12345678-1234-4abc-8def-123456789abc";
            const upload = extractVersionUpload(
              `Uploaded maestro-stable-share (1 sec)\nWorker Version ID: ${{versionId}}\n` +
              "Version Preview URL: https://12345678-maestro-stable-share.owner.workers.dev\\n",
              "maestro-stable-share",
            );
            const candidate = {{
              schemaVersion: 1,
              accountId: "a".repeat(32),
              workerName: "maestro-stable-share",
              namespaceId: "b".repeat(32),
              versionId,
              previewUrl: upload.previewUrl,
              stableUrl: upload.stableUrl,
              sourceSha256: "c".repeat(64),
              configSha256: "d".repeat(64),
              updateTokenSha256: "e".repeat(64),
            }};
            const encoded = encodeCandidateMetadata(candidate);
            const parsed = parseCandidateMetadata(encoded);
            const invalidArgs = [];
            for (const args of [["--promote"], ["--promote", "latest"], ["--stage", "extra"]]) {{
              try {{ parseProvisionArgs(args); invalidArgs.push(false); }}
              catch {{ invalidArgs.push(true); }}
            }}
            console.log(JSON.stringify({{
              defaultAction: parseProvisionArgs([]),
              stageAction: parseProvisionArgs(["--stage"]),
              promoteAction: parseProvisionArgs(["--promote", versionId.toUpperCase()]),
              upload,
              parsed,
              matches: candidateMatches(parsed, candidate),
              rejectsDigestDrift: !candidateMatches(parsed, {{ ...candidate, sourceSha256: "f".repeat(64) }}),
              rejectsChangedSecret: !candidateMatches(
                parsed,
                {{ ...candidate, updateTokenSha256: "a".repeat(64) }},
              ),
              activeReadback: parseDeploymentReadback(
                JSON.stringify({{ versions: [{{ version_id: versionId, percentage: 100 }}] }}),
                versionId,
              ),
              inactiveReadback: parseDeploymentReadback(
                JSON.stringify({{ versions: [
                  {{ version_id: versionId, percentage: 25 }},
                  {{ version_id: "87654321-4321-4abc-8def-abcdef123456", percentage: 75 }},
                ] }}),
                versionId,
              ),
              invalidReadback: parseDeploymentReadback(
                `{{"versions":[{{"version_id":"${{versionId}}","percentage":100}}],"versions":[]}}`,
                versionId,
              ),
              canonicalEnvironment: canonicalizeManagedEnvironment(
                "CLOUDFLARE_API_TOKEN=old\\nKEEP=value\\nCLOUDFLARE_API_TOKEN=leaked\\nPINOKIO_STABLE_SHARE_CANDIDATE=old\\nPINOKIO_STABLE_SHARE_CANDIDATE=stale\\n",
                {{ CLOUDFLARE_API_TOKEN: "", PINOKIO_STABLE_SHARE_CANDIDATE: "new" }},
                ["CLOUDFLARE_API_TOKEN", "PINOKIO_STABLE_SHARE_CANDIDATE"],
              ),
              stagedDefaultMode: canonicalizeManagedEnvironment(
                "KEEP=value\\n",
                {{ SHARE_MODE: "proxy", PINOKIO_STABLE_SHARE_CANDIDATE: "new" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              promotedDefaultMode: canonicalizeManagedEnvironment(
                "KEEP=value\\nPINOKIO_STABLE_SHARE_CANDIDATE=new\\nSHARE_MODE=proxy\\n",
                {{ SHARE_MODE: "proxy", PINOKIO_STABLE_SHARE_CANDIDATE: "" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              stagedRedirectMode: canonicalizeManagedEnvironment(
                "KEEP=value\\nSHARE_MODE=redirect\\n",
                {{ SHARE_MODE: "redirect", PINOKIO_STABLE_SHARE_CANDIDATE: "new" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              promotedRedirectMode: canonicalizeManagedEnvironment(
                "KEEP=value\\nPINOKIO_STABLE_SHARE_CANDIDATE=new\\nSHARE_MODE=redirect\\n",
                {{ SHARE_MODE: "redirect", PINOKIO_STABLE_SHARE_CANDIDATE: "" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              rejectsBadPreview: extractVersionUpload(
                `Worker Version ID: ${{versionId}}\nVersion Preview URL: https://other.workers.dev`,
                "maestro-stable-share",
              ) === null,
              invalidArgs,
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        version_id = "12345678-1234-4abc-8def-123456789abc"
        self.assertEqual(payload["defaultAction"], {"phase": "stage", "versionId": ""})
        self.assertEqual(payload["stageAction"], {"phase": "stage", "versionId": ""})
        self.assertEqual(
            payload["promoteAction"],
            {"phase": "promote", "versionId": version_id},
        )
        self.assertEqual(payload["upload"], {
            "versionId": version_id,
            "previewUrl": "https://12345678-maestro-stable-share.owner.workers.dev",
            "stableUrl": "https://maestro-stable-share.owner.workers.dev",
        })
        self.assertEqual(payload["parsed"]["versionId"], version_id)
        self.assertTrue(payload["matches"])
        self.assertTrue(payload["rejectsDigestDrift"])
        self.assertTrue(payload["rejectsChangedSecret"])
        self.assertTrue(payload["rejectsBadPreview"])
        self.assertTrue(payload["activeReadback"]["active"])
        self.assertFalse(payload["inactiveReadback"]["active"])
        self.assertIsNone(payload["invalidReadback"])
        self.assertEqual(
            payload["canonicalEnvironment"],
            "KEEP=value\nCLOUDFLARE_API_TOKEN=\nPINOKIO_STABLE_SHARE_CANDIDATE=new\n",
        )
        self.assertEqual(
            payload["stagedDefaultMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=new\nSHARE_MODE=proxy\n",
        )
        self.assertEqual(
            payload["promotedDefaultMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=\nSHARE_MODE=proxy\n",
        )
        self.assertEqual(
            payload["stagedRedirectMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=new\nSHARE_MODE=redirect\n",
        )
        self.assertEqual(
            payload["promotedRedirectMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=\nSHARE_MODE=redirect\n",
        )
        self.assertEqual(payload["invalidArgs"], [True, True, True])

    def test_provisioner_keeps_secret_off_argv_and_temporary_wrangler_config(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("if (apiToken) childEnvironment.CLOUDFLARE_API_TOKEN = apiToken", source)
        self.assertIn("JSON.stringify({ UPDATE_TOKEN: updateSecret })", source)
        self.assertIn('"--secrets-file", secretsPath', source)
        self.assertNotIn('"--var", updateSecret', source)
        self.assertNotIn("writeFileSync(configPath, updateSecret", source)
        self.assertNotIn("updateSecret]", source)
        self.assertIn("observability: { enabled: false }", source)
        self.assertIn('vars: { SHARE_MODE: shareMode }', source)
        self.assertIn(
            'const shareMode = (setting("SHARE_MODE") || "proxy").trim().toLowerCase()',
            source,
        )
        self.assertIn("randomBytes(32).toString(\"hex\")", source)
        self.assertIn("CLOUDFLARE_WORKERS_FREE_CONFIRMED=true", source)
        self.assertIn("renameSync(temporaryEnvironment, environmentPath)", source)
        self.assertIn("chmodSync(environmentPath, 0o600)", source)
        self.assertIn(
            "const effectiveUpdates = { ...updates, SHARE_MODE: shareMode }",
            source,
        )
        self.assertIn(
            "currentEnvironment,\n    effectiveUpdates,\n    managedEnvironmentKeys",
            source,
        )
        self.assertNotIn("process.stdout.write(updateSecret", source)
        self.assertIn("delete childEnvironment.PINOKIO_STABLE_SHARE_UPDATE_SECRET", source)
        self.assertIn("delete childEnvironment.PINOKIO_STABLE_SHARE_CANDIDATE", source)
        self.assertIn("delete childEnvironment.UPDATE_TOKEN", source)

    def test_provisioner_stages_without_traffic_then_promotes_exact_candidate(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        helpers = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision_helpers.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('"versions", "upload", "--config", configPath', source)
        self.assertIn('"versions", "deploy", `${candidate.versionId}@100%`', source)
        self.assertIn('"--config", configPath, "-y"', source)
        self.assertNotIn('wrangler(["deploy"', source)
        self.assertNotIn('wrangler(["secret", "put"', source)
        self.assertIn("extractVersionUpload(commandOutput(uploaded), workerName)", source)
        self.assertIn("PINOKIO_STABLE_SHARE_CANDIDATE: encodeCandidateMetadata(candidate)", source)
        self.assertIn("candidateMatches(candidate, expected)", source)
        self.assertIn("sourceSha256", source)
        self.assertIn("configSha256", source)
        self.assertIn("updateTokenSha256", source)
        self.assertIn("PINOKIO_STABLE_SHARE_URL: candidate.stableUrl", source)
        self.assertLess(
            source.index('"versions", "deploy", `${candidate.versionId}@100%`'),
            source.index("PINOKIO_STABLE_SHARE_URL: candidate.stableUrl"),
        )
        stage_start = source.index('if (action.phase === "stage")')
        promote_start = source.index("} else {", stage_start)
        self.assertNotIn("PINOKIO_STABLE_SHARE_URL", source[stage_start:promote_start])
        self.assertIn("CANDIDATE_KEYS", helpers)
        self.assertNotIn("updateSecret", helpers)
        self.assertIn("canonicalizeManagedEnvironment", source)
        self.assertIn('"deployments", "status", "--json"', source)
        self.assertNotIn('"versions", "deployments", "status"', source)
        self.assertIn("parseDeploymentReadback(readbackResult.stdout", source)
        self.assertIn("promotion outcome is ambiguous", source)

        namespace_assignment = source.index(
            "namespaceId = extractNamespaceId(commandOutput(created))",
        )
        namespace_persist = source.index("replaceEnvironmentValues({", namespace_assignment)
        version_upload = source.index('"versions", "upload"')
        self.assertLess(namespace_assignment, namespace_persist)
        self.assertLess(namespace_persist, version_upload)

    def test_oauth_fallback_is_logged_out_and_verified(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        helpers = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision_helpers.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('const oauthLane = !apiToken', source)
        self.assertIn('wrangler(["whoami", "--json"])', source)
        self.assertNotIn('wrangler(["whoami"])', source)
        self.assertIn('wrangler(["logout"])', source)
        self.assertIn("isWhoamiLoggedOut(commandOutput(verification))", source)
        self.assertIn("oauthLogoutVerified = true", source)
        self.assertIn("if (oauthAuthenticated && !oauthLogoutVerified)", source)
        self.assertIn("parsed.loggedIn !== true", helpers)
        self.assertIn("Array.isArray(parsed.accounts)", helpers)
        self.assertNotIn("parsed.memberships", helpers)

    def test_application_process_does_not_inherit_cloudflare_provisioning_token(self):
        mask = (ROOT / "launcher_secret_env.js").read_text(encoding="utf-8")
        self.assertIn('CLOUDFLARE_API_TOKEN: ""', mask)
        self.assertIn('PINOKIO_STABLE_SHARE_UPDATE_SECRET: ""', mask)

    def test_example_has_only_blank_secret_placeholders(self):
        environment = (ROOT / "ENVIRONMENT.example").read_text(encoding="utf-8")
        self.assertIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET=\n", environment)
        self.assertIn("CLOUDFLARE_API_TOKEN=\n", environment)
        self.assertIn("CLOUDFLARE_WORKERS_FREE_CONFIRMED=\n", environment)
        self.assertNotIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET=s", environment)


if __name__ == "__main__":
    unittest.main()
