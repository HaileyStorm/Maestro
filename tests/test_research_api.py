"""Model-free contracts for the local research scheduler and API surface."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from services.research_runtime import (
    PUBLIC_RESEARCH_DISCLOSURE,
    ResearchNonceError,
    ResearchRuntime,
    ResearchRuntimeBusy,
    ResearchRuntimeError,
    SessionNonceStore,
)
from services.research_store import ResearchStore


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = (ROOT / "app/launch.py").read_text(encoding="utf-8")


class FakeStore:
    def __init__(self, root: Path, *, due: bool = False, eligible: int = 0):
        self.state_path = root / "state.json"
        self.enabled = 0
        self._due = due
        self.model = {
            "schema_version": 1,
            "schedule_enabled": False,
            "configured_batch_size": 6,
            "cadence": None,
            "last_cycle_at": None,
            "last_cycle": None,
            "next_due_at": None,
            "queued_candidate_count": 0,
            "research_active": False,
            "research_phase": None,
            "implementation_active": False,
            "implementation_chunk_count": eligible,
            "implementation_ready": eligible >= 3,
            "readiness_threshold": 3,
            "readiness_reason": "threshold_met" if eligible >= 3 else "waiting",
            "recent_pending": [],
            "last_implementation_run": {
                "active": False,
                "run_id": None,
                "packet_id": None,
                "started_at": None,
                "completed_at": None,
                "status": "never_run",
                "summary": "",
            },
        }

    def enable(self):
        self.enabled += 1
        self.state_path.write_text("{}", encoding="utf-8")
        self.model["schedule_enabled"] = True
        return {}

    def due(self):
        return self._due, "due" if self._due else "not_due"

    def read_model(self, **_kwargs):
        return json.loads(json.dumps(self.model))


class FakeProcess:
    def __init__(self, release: threading.Event | None = None):
        self.pid = 0
        self.returncode = None
        self.release = release or threading.Event()
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self.terminated or self.killed:
            self.returncode = -15
            return self.returncode
        if self.release.wait(timeout=0 if timeout is None else timeout):
            self.returncode = 0
            return 0
        raise subprocess.TimeoutExpired("research", timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class ResearchNonceTests(unittest.TestCase):
    def test_nonce_is_session_bound_single_use_and_short_lived(self):
        clock = [10.0]
        nonces = SessionNonceStore(ttl_seconds=5, monotonic=lambda: clock[0])
        first = nonces.issue("session-a")
        with self.assertRaises(ResearchNonceError):
            nonces.consume("session-b", first["nonce"])
        with self.assertRaises(ResearchNonceError):
            nonces.consume("session-a", first["nonce"])

        second = nonces.issue("session-a")
        nonces.consume("session-a", second["nonce"])
        with self.assertRaises(ResearchNonceError):
            nonces.consume("session-a", second["nonce"])

        expired = nonces.issue("session-a")
        clock[0] += 6
        with self.assertRaises(ResearchNonceError):
            nonces.consume("session-a", expired["nonce"])


class ResearchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_first_start_enables_once_and_due_scheduler_uses_private_child(self):
        store = FakeStore(self.root, due=True)
        calls = []

        def spawn(command, **kwargs):
            calls.append((command, kwargs))
            process = FakeProcess()
            process.release.set()
            return process

        runtime = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            popen_factory=spawn,
            scheduler_poll_seconds=0.02,
            retry_delay_seconds=60,
        )
        runtime.start()
        self.assertTrue(wait_until(lambda: bool(calls)))
        runtime.stop()
        runtime.start()
        runtime.stop()

        self.assertEqual(store.enabled, 1)
        command, kwargs = calls[0]
        self.assertTrue(command[0].endswith(("app/env/bin/python", "python")))
        self.assertTrue(command[1].endswith("app/scripts/run_research_cycle.py"))
        self.assertEqual(command[2:], ["run"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["start_new_session"])

    def test_real_store_first_start_is_anchored_and_explicit_disable_persists(self):
        store = ResearchStore(
            self.root / "research",
            allow_test_root=True,
        )
        calls = []
        runtime = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            popen_factory=lambda *args, **kwargs: calls.append((args, kwargs)),
            scheduler_poll_seconds=0.02,
        )
        runtime.start()
        time.sleep(0.05)
        runtime.stop()
        first = store.read_model()
        self.assertTrue(first["schedule_enabled"])
        self.assertIsNotNone(first["next_due_at"])
        self.assertEqual(calls, [])

        store.disable()
        restarted = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            scheduler_poll_seconds=0.02,
        )
        restarted.start()
        restarted.stop()
        self.assertFalse(store.read_model()["schedule_enabled"])

    def test_storage_enable_failure_does_not_abort_maestro_startup(self):
        class BrokenStore(FakeStore):
            def enable(self):
                raise OSError("private storage path")

        spawned = []
        implemented = []
        runtime = ResearchRuntime(
            store=BrokenStore(self.root),
            repo_root=ROOT,
            popen_factory=lambda *args, **kwargs: spawned.append((args, kwargs)),
            implementation_runner_factory=lambda *args, **kwargs: implemented.append(
                (args, kwargs)
            ),
            scheduler_poll_seconds=0.02,
        )
        runtime.start()
        self.assertTrue(runtime._started)
        self.assertIsNone(runtime._scheduler_thread)
        self.assertEqual(runtime._runtime_error, "Research storage is unavailable.")
        with self.assertRaises(ResearchRuntimeError):
            runtime.start_research(force=True)
        nonce = runtime.issue_implementation_nonce("owner")["nonce"]
        with self.assertRaises(ResearchRuntimeError):
            runtime.start_implementation(
                session_id="owner", nonce=nonce, force=True,
            )
        self.assertEqual(spawned, [])
        self.assertEqual(implemented, [])
        self.assertEqual(runtime._runtime_error, "Research storage is unavailable.")
        runtime.stop()
        self.assertFalse(runtime._started)

    def test_manual_research_is_single_flight_and_shutdown_terminates(self):
        store = FakeStore(self.root)
        release = threading.Event()
        process = FakeProcess(release)
        runtime = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            popen_factory=lambda *_args, **_kwargs: process,
            terminate_grace_seconds=0.1,
        )
        runtime.start_research(force=True)
        self.assertTrue(wait_until(lambda: runtime._research_process is process))
        with self.assertRaises(ResearchRuntimeBusy):
            runtime.start_research(force=True)
        runtime.stop()
        self.assertTrue(process.terminated or process.killed)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup contract")
    def test_runtime_shutdown_interrupts_real_child_once_and_finishes_cleanup(self):
        ready = self.root / "ready"
        cleaned = self.root / "cleaned"
        descendant_survived = self.root / "descendant-survived"
        descendant_code = (
            "from pathlib import Path; import signal, time; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(2); "
            f"Path({str(descendant_survived)!r}).write_text('survived')"
        )
        code = (
            "from pathlib import Path\n"
            "import subprocess, time\n"
            f"subprocess.Popen([{str(ROOT / 'app/env/bin/python')!r}, '-c', {descendant_code!r}])\n"
            f"Path({str(ready)!r}).write_text('ready')\n"
            "try:\n"
            "    time.sleep(60)\n"
            "finally:\n"
            "    time.sleep(0.2)\n"
            f"    Path({str(cleaned)!r}).write_text('cleaned')\n"
        )
        runtime = ResearchRuntime(
            store=FakeStore(self.root),
            repo_root=ROOT,
            popen_factory=lambda *_args, **_kwargs: subprocess.Popen(
                [str(ROOT / "app/env/bin/python"), "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            ),
            terminate_grace_seconds=1,
        )
        runtime.start_research(force=True)
        self.assertTrue(wait_until(ready.exists))
        runtime.stop()
        self.assertTrue(cleaned.exists())
        time.sleep(1.2)
        self.assertFalse(descendant_survived.exists())

    def test_force_reaches_only_readiness_count_and_one_finding_is_enough(self):
        store = FakeStore(self.root, eligible=1)
        called = threading.Event()
        captured = {}

        class Runner:
            def __init__(self, store_arg, root_arg, *, busy_predicate):
                captured["init"] = (store_arg, root_arg, busy_predicate)

            def run(self, **kwargs):
                captured["run"] = kwargs
                called.set()
                return {"provider_output": "must not be retained"}

        runtime = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            implementation_runner_factory=Runner,
        )
        nonce = runtime.issue_implementation_nonce("owner")["nonce"]
        runtime.start_implementation(
            session_id="owner", nonce=nonce, force=True,
        )
        self.assertTrue(called.wait(1))
        self.assertEqual(
            set(captured["run"]),
            {"force", "readiness_threshold", "cancel"},
        )
        self.assertTrue(captured["run"]["force"])
        self.assertEqual(captured["run"]["readiness_threshold"], 3)
        self.assertNotIn("provider_output", json.dumps(runtime.status()))

    def test_busy_application_refuses_and_consumes_nonce(self):
        store = FakeStore(self.root, eligible=3)
        runtime = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            busy_predicate=lambda: True,
        )
        nonce = runtime.issue_implementation_nonce("owner")["nonce"]
        with self.assertRaises(ResearchRuntimeBusy):
            runtime.start_implementation(
                session_id="owner", nonce=nonce, force=False,
            )
        with self.assertRaises(ResearchNonceError):
            runtime.start_implementation(
                session_id="owner", nonce=nonce, force=False,
            )

    def test_research_and_implementation_launches_are_mutually_exclusive(self):
        store = FakeStore(self.root, eligible=3)
        release = threading.Event()
        research_process = FakeProcess(release)
        runtime = ResearchRuntime(
            store=store,
            repo_root=ROOT,
            popen_factory=lambda *_args, **_kwargs: research_process,
        )
        runtime.start_research(force=True)
        self.assertTrue(wait_until(lambda: runtime._research_process is research_process))
        nonce = runtime.issue_implementation_nonce("owner")["nonce"]
        with self.assertRaises(ResearchRuntimeBusy):
            runtime.start_implementation(
                session_id="owner", nonce=nonce, force=False,
            )
        runtime.stop()

    def test_status_is_sanitized_and_disclosure_is_persistent(self):
        store = FakeStore(self.root, eligible=1)
        status = ResearchRuntime(store=store, repo_root=ROOT).status()
        encoded = json.dumps(status)
        self.assertEqual(status["disclosure"], PUBLIC_RESEARCH_DISCLOSURE)
        for private_value in (
            "private-project", "user prompt", "/media/private.mov", "job-log",
        ):
            self.assertNotIn(private_value, encoded)


class ResearchLaunchApiContracts(unittest.TestCase):
    def test_research_is_remote_denied_before_endpoint_body_parsing(self):
        self.assertIn('"/api/v1/research",', LAUNCH)
        middleware = LAUNCH[LAUNCH.index("async def _maestro_session_middleware"):]
        middleware = middleware[:middleware.index("# Upload size caps")]
        self.assertLess(
            middleware.index("remote_denial = _research_local_only_denial(request)"),
            middleware.index("rejected = _reject_cross_origin_mutation(request)"),
        )
        endpoint = LAUNCH[LAUNCH.index("async def run_research_implementation"):]
        self.assertIn("body = await request.json()", endpoint[:1800])

    def test_local_peer_is_allowed_and_remote_peer_is_denied(self):
        tree = ast.parse(LAUNCH, "app/launch.py")
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_research_local_only_denial"
        )

        class Response:
            def __init__(self, body, status_code):
                self.body = body
                self.status_code = status_code

        namespace = {
            "Request": object,
            "JSONResponse": Response,
            "_is_loopback_request_client": lambda request: request["loopback"],
            "_request_is_cloudflare_remote": lambda request: request["cloudflare"],
            "_request_external_origins": lambda request: set(request["origins"]),
            "_approved_local_origin": lambda origin: origin.endswith("localhost"),
        }
        isolated = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(isolated)
        exec(compile(isolated, "research-local-only", "exec"), namespace)
        deny = namespace["_research_local_only_denial"]
        local = {"loopback": True, "cloudflare": False, "origins": ["http://localhost"]}
        remote = {"loopback": False, "cloudflare": False, "origins": ["http://host"]}
        self.assertIsNone(deny(local))
        response = deny(remote)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body, {"detail": "Research controls are available locally only"})

    def test_all_research_successes_and_errors_receive_private_no_store(self):
        tree = ast.parse(LAUNCH, "app/launch.py")
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_recovery_response_requires_no_store"
        )
        source = ast.get_source_segment(LAUNCH, function) or ""
        self.assertIn('path == "/api/v1/research"', source)
        self.assertIn('path.startswith("/api/v1/research/")', source)
        self.assertIn('response.headers["Cache-Control"] = "private, no-store"', LAUNCH)
        self.assertIn('response.headers["Pragma"] = "no-cache"', LAUNCH)

        functions = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "_recovery_response_requires_no_store",
                "_stamp_recovery_no_store_response",
            }
        }
        isolated = ast.Module(body=list(functions.values()), type_ignores=[])
        ast.fix_missing_locations(isolated)
        namespace = {"Request": object, "Response": object}
        exec(compile(isolated, "research-no-store", "exec"), namespace)
        for status_code in (200, 403, 409, 503):
            response = type("Response", (), {"headers": {}, "status_code": status_code})()
            request = type("Request", (), {
                "url": type("Url", (), {"path": "/api/v1/research/status"})(),
            })()
            stamped = namespace["_stamp_recovery_no_store_response"](
                request, response,
            )
            self.assertEqual(stamped.headers["Cache-Control"], "private, no-store")
            self.assertEqual(stamped.headers["Pragma"], "no-cache")
        self.assertLess(
            LAUNCH.index("async def _exact_runtime_cors_middleware"),
            LAUNCH.index("async def _maestro_session_middleware"),
        )

    def test_routes_are_local_controls_and_client_payload_cannot_choose_scope(self):
        for route in (
            '/api/v1/research/status',
            '/api/v1/research/run',
            '/api/v1/research/implementation/nonce',
            '/api/v1/research/implementation/run',
        ):
            self.assertIn(route, LAUNCH)
        tree = ast.parse(LAUNCH, "app/launch.py")
        endpoint = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "run_research_implementation"
        )
        requested = {
            call.args[0].value
            for call in ast.walk(endpoint)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "get"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        }
        self.assertEqual(requested, {"nonce", "force"})

    def test_implementation_busy_probe_covers_every_live_app_lane(self):
        tree = ast.parse(LAUNCH, "app/launch.py")
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_research_application_busy"
        )
        source = ast.get_source_segment(LAUNCH, function) or ""
        for marker in (
            "_gen_lock.locked()",
            "_active_gen_states",
            "_jobs.values()",
            "_research_preparation_busy()",
            "pipeline_service._pipelines",
            "pipeline_service._pipeline_repairs",
            "_model_downloads",
            "_civitai_downloads",
            "get_active_downloads",
        ):
            self.assertIn(marker, source)

    def test_journal_owned_preparation_lanes_block_implementation(self):
        tree = ast.parse(LAUNCH, "app/launch.py")
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_research_preparation_busy"
        )
        isolated = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(isolated)
        namespace = {
            "_audio_analysis_state_lock": threading.RLock(),
            "_audio_analysis_active": None,
            "_director_preparation_lock": threading.RLock(),
            "_director_preparation_states": {},
        }
        exec(compile(isolated, "research-preparation-busy", "exec"), namespace)
        busy = namespace["_research_preparation_busy"]
        self.assertFalse(busy())

        namespace["_audio_analysis_active"] = {"status": "analyzing"}
        self.assertTrue(busy())
        namespace["_audio_analysis_active"] = None

        namespace["_director_preparation_states"] = {
            "finished": {"status": "completed"},
        }
        self.assertFalse(busy())
        for status in ("running", "analyzing", "classifying", "structuring", None):
            namespace["_director_preparation_states"] = {
                "active": {"status": status},
            }
            self.assertTrue(busy(), status)

    def test_shutdown_contract_cleans_posix_and_windows_process_groups(self):
        runtime = (ROOT / "app/services/research_runtime.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("os.killpg(pid, signal.SIGINT)", runtime)
        self.assertIn("signal.CTRL_BREAK_EVENT", runtime)
        self.assertIn('["taskkill", "/PID", str(pid), "/T", "/F"]', runtime)


if __name__ == "__main__":
    unittest.main()
