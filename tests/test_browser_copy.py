"""CPU and queue contracts for the Gallery browser-compatible copy tool."""

from __future__ import annotations

import ast
import asyncio
import contextvars
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from fastapi import HTTPException
from services.output_access import stamp_sidecar_policy
from services.queue_recovery_adapter import owner_principal_digest
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    recovery_unit_id,
    sha256_file,
)
from services.video_transform import (
    BROWSER_COPY_MAX_DURATION_SECONDS,
    BROWSER_COPY_MAX_INPUT_BYTES,
    BrowserCopyError,
    BrowserCopyLimitError,
    BrowserCopySpaceError,
    browser_compatible_copy,
    validate_browser_copy,
)

LAUNCH_SOURCE = (ROOT / "app/launch.py").read_text(encoding="utf-8")
LAUNCH_TREE = ast.parse(LAUNCH_SOURCE, filename="app/launch.py")
SECRET = b"browser-copy-tests-session-secret"
OWNER_DIGEST = owner_principal_digest(SECRET, "owner-session")


def _load_launch_functions(namespace: dict, *names: str) -> None:
    nodes = []
    for node in LAUNCH_TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    exec(  # noqa: S102 - only repository-owned launch functions are compiled for isolated tests.
        compile(ast.Module(body=nodes, type_ignores=[]), "launch.py", "exec"), namespace,
    )


def _sidecar(directory: str, names: set[str]) -> dict[str, dict]:
    result = {}
    for name in names:
        path = Path(directory) / f"{Path(name).stem}.meta.json"
        if path.is_file():
            result[name] = json.loads(path.read_text(encoding="utf-8"))
    return result


class _RouteFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="maestro-browser-copy-route-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "clip.mkv"
        self.source.write_bytes(b"gallery-video-bytes")
        self.sidecar_path = self.source.with_suffix(".meta.json")
        self.sidecar_path.write_text(json.dumps({
            "workspace": "project-a",
            "private": True,
            "explicit": True,
            "params": {"prompt": "violent fictional scene", "model_type": "source-model"},
        }), encoding="utf-8")
        self.registered: list[tuple[dict, dict]] = []
        self.permission = Mock(return_value=str(self.root))
        self._guard_mu = threading.Lock()
        self._guard_busy = {"workspace": False, "lineage": False}
        namespace = {}

        @contextmanager
        def tracked_guard(kind: str):
            with self._guard_mu:
                if self._guard_busy[kind]:
                    raise AssertionError(f"{kind} lock was held across an async wait")
                self._guard_busy[kind] = True
            try:
                yield
            finally:
                with self._guard_mu:
                    self._guard_busy[kind] = False

        def register(job: dict, **options) -> None:
            self.registered.append((job, options))
            namespace["_jobs"][job["id"]] = job

        self.namespace = namespace
        namespace.update({
            "asyncio": asyncio,
            "contextvars": contextvars,
            "hmac": hmac,
            "json": json,
            "os": os,
            "time": time,
            "uuid": uuid,
            "Request": object,
            "HTTPException": HTTPException,
            "_request_remote": contextvars.ContextVar("browser_copy_remote", default=True),
            "_require_project_access": self.permission,
            "_get_active_workspace": lambda: (_ for _ in ()).throw(
                AssertionError("browser-copy route used the global workspace")
            ),
            "_reserve_workspace_operations": lambda *_args: tracked_guard("workspace"),
            "_output_lineage_mutation_guard": lambda *_args: tracked_guard("lineage"),
            "_existing_workspace_dir": lambda _workspace: str(self.root),
            "load_media_sidecars": _sidecar,
            "_new_generation_job_id": lambda: "a" * 32,
            "_queue_recovery_register_and_publish": register,
            "_jobs": {},
            "_BROWSER_COPY_PREFLIGHTS": {},
        })
        _load_launch_functions(
            self.namespace,
            "_browser_copy_preflight_acquire",
            "_browser_copy_preflight_release",
            "_request_project_workspace",
            "_require_authorized_output",
            "_output_revision",
            "_browser_copy_source",
            "_run_tool_browser_copy",
            "tools_browser_copy",
        )
        self.revision = self.namespace["_output_revision"](
            str(self.source), str(self.root), self.source.name,
        )

    def request(self, **changes):
        body = {
            "workspace": "project-a",
            "name": self.source.name,
            "revision": self.revision,
        }
        body.update(changes)

        async def read_json():
            return body

        return types.SimpleNamespace(
            json=read_json,
            state=types.SimpleNamespace(
                maestro_remote=True,
                maestro_session_id="owner-session",
            ),
        )

    def submit(self, **changes) -> dict:
        return asyncio.run(self.namespace["tools_browser_copy"](self.request(**changes)))

    def close(self) -> None:
        self.temporary.cleanup()


class BrowserCopyRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _RouteFixture()
        self.addCleanup(self.fixture.close)

    def test_submission_seals_gallery_source_inherits_policy_and_registers_recovery(self):
        with patch("services.video_transform.validate_browser_copy", return_value={"duration": 1.0}):
            response = self.fixture.submit()

        self.assertEqual(response, {"job_id": "a" * 32, "status": "queued"})
        job, options = self.fixture.registered[0]
        self.assertEqual(job["kind"], "tool_browser_copy")
        self.assertEqual(job["workspace"], "project-a")
        self.assertEqual(job["session_id"], "owner-session")
        self.assertEqual(job["params"]["browser_copy_source_name"], self.fixture.source.name)
        self.assertEqual(job["params"]["browser_copy_source_path"], str(self.fixture.source))
        self.assertEqual(job["params"]["browser_copy_source_revision"], self.fixture.revision)
        self.assertTrue(job["params"]["private_output"])
        self.assertTrue(job["params"]["explicit_output"])
        self.assertNotIn("prompt", job["params"])
        self.assertEqual(options["recovery_kind"], "tool_browser_copy")
        self.assertIs(options["worker"], self.fixture.namespace["_run_tool_browser_copy"])
        self.fixture.permission.assert_any_call(
            unittest.mock.ANY, "project-a", permission="project.generate",
        )

    def test_invalid_scope_path_and_revision_never_queue(self):
        cases = [
            ({"workspace": ""}, 400),
            ({"name": "../clip.mkv"}, 400),
            ({"name": "clip.png"}, 400),
            ({"revision": "stale"}, 409),
        ]
        for changes, expected_status in cases:
            with (
                self.subTest(changes=changes),
                self.assertRaises(HTTPException) as raised,
                patch("services.video_transform.validate_browser_copy"),
            ):
                self.fixture.submit(**changes)
            self.assertEqual(raised.exception.status_code, expected_status)
        self.assertEqual(self.fixture.registered, [])

    def test_project_permission_failure_never_queues(self):
        self.fixture.permission.side_effect = HTTPException(403, "Project access denied")
        with (
            self.assertRaises(HTTPException),
            patch("services.video_transform.validate_browser_copy"),
        ):
            self.fixture.submit()
        self.assertEqual(self.fixture.registered, [])

    def test_same_source_same_session_returns_active_job(self):
        active = {
            "id": "b" * 32,
            "kind": "tool_browser_copy",
            "status": "running",
            "workspace": "project-a",
            "session_id": "owner-session",
            "out_dir": str(self.fixture.root),
            "params": {
                "browser_copy_source_name": self.fixture.source.name,
                "browser_copy_source_path": str(self.fixture.source),
                "browser_copy_source_revision": self.fixture.revision,
            },
        }
        self.fixture.namespace["_jobs"][active["id"]] = active
        with patch("services.video_transform.validate_browser_copy") as validate:
            response = self.fixture.submit()
        validate.assert_not_called()
        self.assertEqual(response, {"job_id": active["id"], "status": "running"})
        self.assertEqual(self.fixture.registered, [])

    def test_concurrent_submissions_probe_outside_locks_and_dedupe_after_revalidation(self):
        allow_probe_to_finish = threading.Event()
        probe_count = 0
        probe_lock = threading.Lock()
        gate = {}

        def preflight(_source, _out_dir):
            nonlocal probe_count
            with probe_lock:
                probe_count += 1
            gate["loop"].call_soon_threadsafe(gate["started"].set)
            if not allow_probe_to_finish.wait(timeout=5):
                raise AssertionError("coalesced preflight was not released")
            return {"duration": 1.0}

        async def submit_burst():
            gate["loop"] = asyncio.get_running_loop()
            gate["started"] = asyncio.Event()
            requests = [
                asyncio.create_task(
                    self.fixture.namespace["tools_browser_copy"](self.fixture.request()),
                )
                for _ in range(12)
            ]
            try:
                await asyncio.wait_for(gate["started"].wait(), timeout=1.0)
                await asyncio.sleep(0.02)
            finally:
                allow_probe_to_finish.set()
            return await asyncio.gather(*requests)

        with patch("services.video_transform.validate_browser_copy", side_effect=preflight):
            responses = asyncio.run(submit_burst())

        self.assertEqual(probe_count, 1)
        self.assertTrue(all(response == responses[0] for response in responses))
        self.assertEqual(responses[0], {"job_id": "a" * 32, "status": "queued"})
        self.assertEqual(len(self.fixture.registered), 1)
        self.assertEqual(self.fixture.namespace["_BROWSER_COPY_PREFLIGHTS"], {})
        self.assertEqual(self.fixture.namespace["_BROWSER_COPY_PREFLIGHTS"], {})

    def test_browser_copy_limits_return_plain_bounded_error(self):
        with (
            patch(
                "services.video_transform.validate_browser_copy",
                side_effect=BrowserCopyLimitError("The selected video exceeds the browser-copy size limit."),
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            self.fixture.submit()
        self.assertEqual(raised.exception.status_code, 413)
        self.assertNotIn(str(self.fixture.root), str(raised.exception.detail))
        self.assertEqual(self.fixture.registered, [])
        self.assertEqual(self.fixture.namespace["_BROWSER_COPY_PREFLIGHTS"], {})


class BrowserCopyWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _RouteFixture()
        self.addCleanup(self.fixture.close)
        with patch("services.video_transform.validate_browser_copy", return_value={"duration": 1.0}):
            self.fixture.submit()
        self.job = self.fixture.registered[0][0]
        self.job["access_policy"] = {"private": True, "explicit": True}
        self.namespace = self.fixture.namespace
        self.namespace.update({
            "_jobs": {self.job["id"]: self.job},
            "_gen_lock": threading.Lock(),
            "_active_gen_states": {},
            "generation_slot": lambda *_args: nullcontext(True),
            "try_start": self._try_start,
            "register_abort_state": lambda *_args: True,
            "unregister_abort_state": Mock(),
            "is_cancel_requested": lambda job: (
                job.get("status") == "cancelled" or bool(job.get("cancel_requested"))
            ),
            "_reserve_workspace_operations": lambda *_args: nullcontext(),
            "_output_lineage_mutation_guard": lambda *_args: nullcontext(),
            "_existing_workspace_dir": lambda _workspace: str(self.fixture.root),
            "_resume_processed_tool_output": lambda _job: None,
            "_recovery_sha256_file": sha256_file,
            "recovery_unit_id": recovery_unit_id,
            "stamp_sidecar_policy": stamp_sidecar_policy,
            "finish_job": self._finish,
        })
        _load_launch_functions(
            self.namespace,
            "_run_tool_browser_copy",
            "_write_tool_sidecar",
            "_processed_tool_settings",
            "_publish_processed_tool_output",
        )
        self.job["_recovery_manifest_pointer"] = {"path": "manifest.json"}

    @staticmethod
    def _try_start(job: dict, **updates) -> bool:
        job.update(status="running", **updates)
        return True

    @staticmethod
    def _finish(job: dict, status: str, **updates) -> bool:
        job.update(status=status, **updates)
        return True

    def test_worker_publishes_distinct_final_and_revalidates_before_publish(self):
        validations = []

        def validate(job):
            validations.append(self.namespace["_browser_copy_source"](job)[0])
            return [str(self.fixture.source)]

        self.namespace["_validated_tool_input_paths"] = validate

        def encode(_source, destination, **kwargs):
            self.assertTrue(callable(kwargs["abort_check"]))
            Path(destination).write_bytes(b"synthetic browser-ready mp4")
            return destination

        with patch("services.video_transform.browser_compatible_copy", side_effect=encode):
            self.assertTrue(self.namespace["_run_tool_browser_copy"](self.job["id"]))

        self.assertEqual(validations, [str(self.fixture.source)])
        self.assertEqual(self.job["status"], "completed")
        self.assertEqual(len(self.job["output_files"]), 1)
        output = self.fixture.root / self.job["output_files"][0]
        self.assertNotEqual(output, self.fixture.source)
        self.assertTrue(output.is_file())
        self.assertEqual(self.fixture.source.read_bytes(), b"gallery-video-bytes")
        metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["artifact_class"], "final")
        self.assertEqual(metadata["transform"]["video"], "h264")
        self.assertEqual(metadata["transform"]["pixel_format"], "yuv420p")
        self.assertTrue(metadata["private"])
        self.assertTrue(metadata["explicit"])
        self.assertEqual(metadata["tool_source_revision"], self.fixture.revision)
        self.assertEqual(metadata["params"]["prompt"], "violent fictional scene")
        self.assertEqual(list(self.fixture.root.glob(".browser-copy-*")), [])

    def test_worker_cancellation_removes_staging_and_keeps_source(self):
        def cancel(_source, destination, **_kwargs):
            Path(destination).write_bytes(b"partial output")
            self.job["status"] = "cancelled"
            return destination

        publisher = Mock()
        self.namespace["_publish_processed_tool_output"] = publisher
        with patch("services.video_transform.browser_compatible_copy", side_effect=cancel):
            self.assertFalse(self.namespace["_run_tool_browser_copy"](self.job["id"]))
        publisher.assert_not_called()
        self.assertEqual(self.job["status"], "cancelled")
        self.assertEqual(self.job["output_files"], [])
        self.assertEqual(self.fixture.source.read_bytes(), b"gallery-video-bytes")
        self.assertEqual(list(self.fixture.root.glob(".browser-copy-*")), [])
        self.assertEqual(list(self.fixture.root.glob("clip_browser_copy_*")), [])

    def test_worker_source_change_fails_with_redacted_error(self):
        self.fixture.source.write_bytes(b"changed-source-bytes")
        encoder = Mock()
        with patch("services.video_transform.browser_compatible_copy", encoder):
            self.assertFalse(self.namespace["_run_tool_browser_copy"](self.job["id"]))
        encoder.assert_not_called()
        self.assertEqual(self.job["status"], "failed")
        self.assertNotIn(str(self.fixture.root), self.job.get("error") or "")
        self.assertEqual(self.job["output_files"], [])


class BrowserCopyRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _RouteFixture()
        self.addCleanup(self.fixture.close)

    def test_manifest_pins_project_source_and_dispatches_recovery_worker(self):
        namespace = {
            "_RECOVERABLE_INPUT_KEYS": {"browser_copy_source_path"},
            "_app_dir": str(self.fixture.root),
            "_recovery_sha256_file": sha256_file,
            "_session_secret": lambda: SECRET,
            "_output_lineage_mutation_guard": lambda *_args: nullcontext(),
            "owner_principal_digest": owner_principal_digest,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "read_upload_access_sidecar": lambda _path: None,
            "_run_tool_browser_copy": object(),
            "hmac": hmac,
            "os": os,
        }
        _load_launch_functions(
            namespace,
            "_queue_recovery_file_values",
            "_queue_recovery_input_descriptors",
            "_queue_recovery_manifest_validator",
            "_queue_recovery_worker",
        )
        job = {
            "id": "c" * 32,
            "kind": "tool_browser_copy",
            "workspace": "project-a",
            "out_dir": str(self.fixture.root),
            "params": {"browser_copy_source_path": str(self.fixture.source)},
        }
        descriptors = namespace["_queue_recovery_input_descriptors"](job, OWNER_DIGEST)
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0]["field"], "browser_copy_source_path:0")
        self.assertEqual(descriptors[0]["scope"], "project")
        self.assertTrue(namespace["_queue_recovery_manifest_validator"](
            descriptors[0],
            owner_digest=OWNER_DIGEST,
            workspace="project-a",
            project_dir=str(self.fixture.root),
        ))
        self.assertIs(
            namespace["_queue_recovery_worker"](job),
            namespace["_run_tool_browser_copy"],
        )
        self.fixture.source.write_bytes(b"changed-source-bytes")
        self.assertFalse(namespace["_queue_recovery_manifest_validator"](
            descriptors[0],
            owner_digest=OWNER_DIGEST,
            workspace="project-a",
            project_dir=str(self.fixture.root),
        ))


def _binary(env_name: str, fallback: str) -> str | None:
    configured = os.environ.get(env_name)
    if configured:
        return configured if Path(configured).is_file() else shutil.which(configured)
    return shutil.which(fallback)


FFMPEG = _binary("FFMPEG_BINARY", "ffmpeg")
FFPROBE = _binary("FFPROBE_BINARY", "ffprobe")


def _run_media_command(command: list[str]) -> None:
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError("CPU media command failed")


def _make_source(ffmpeg: str, root: Path) -> Path:
    source = root / "source.mkv"
    _run_media_command([
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=10:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest",
        "-c:v", "mpeg4", "-q:v", "4", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", str(source),
    ])
    return source


def _probe_output(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,pix_fmt:format=duration,format_name",
            "-of", "json", str(path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError("CPU media probe failed")
    return json.loads(result.stdout)


class BrowserCopyProbeBoundsTests(unittest.TestCase):
    def test_resolution_pixels_and_frame_rate_are_bounded_before_encoding(self):
        class CompletedProbe:
            def __init__(self, output: bytes):
                self.returncode = 0
                self.output = output

            def communicate(self, *, timeout):
                return self.output, None

        with tempfile.TemporaryDirectory(prefix="maestro-browser-copy-bounds-") as temporary:
            root = Path(temporary)
            source = root / "source.mkv"
            source.write_bytes(b"source")
            cases = (
                (7680, 4320, "30/1"),
                (4000, 2600, "30/1"),
                (3840, 2160, "120/1"),
            )
            for width, height, frame_rate in cases:
                payload = {
                    "streams": [{
                        "codec_type": "video",
                        "codec_name": "h264",
                        "pix_fmt": "yuv420p",
                        "width": width,
                        "height": height,
                        "avg_frame_rate": frame_rate,
                        "duration": "1.0",
                    }],
                    "format": {"duration": "1.0", "format_name": "matroska"},
                }
                process = CompletedProbe(json.dumps(payload).encode("utf-8"))
                with (
                    self.subTest(width=width, height=height, frame_rate=frame_rate),
                    patch("services.video_transform.subprocess.Popen", return_value=process),
                    self.assertRaises(BrowserCopyLimitError),
                ):
                    validate_browser_copy(source, root)


class BrowserCopyProbeCancellationTests(unittest.TestCase):
    def test_probe_terminates_child_after_cancellation_poll(self):
        class WaitingProbe:
            def __init__(self):
                self.returncode = None
                self.communication_count = 0
                self.killed = False
                self.wait_timeout = None
                self.communication_timeouts = []

            def communicate(self, *, timeout):
                self.communication_count += 1
                self.communication_timeouts.append(timeout)
                raise subprocess.TimeoutExpired("ffprobe", timeout)

            def poll(self):
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

            def wait(self, *, timeout):
                self.wait_timeout = timeout
                return self.returncode

        with tempfile.TemporaryDirectory(prefix="maestro-browser-copy-cancel-") as temporary:
            root = Path(temporary)
            source = root / "source.mkv"
            source.write_bytes(b"source")
            process = WaitingProbe()
            abort_calls = 0

            def cancelled():
                nonlocal abort_calls
                abort_calls += 1
                return abort_calls >= 3

            destination = root / "browser-ready.mp4"
            with (
                patch("services.video_transform.subprocess.Popen", return_value=process),
                self.assertRaises(InterruptedError),
            ):
                browser_compatible_copy(
                    source,
                    destination,
                    abort_check=cancelled,
                )

        self.assertEqual(abort_calls, 3)
        self.assertEqual(process.communication_count, 1)
        self.assertLessEqual(process.communication_timeouts[0], 0.1)
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_timeout, 0.5)
        self.assertFalse(destination.exists())


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required")
class BrowserCopyMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="maestro-browser-copy-media-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = _make_source(FFMPEG, self.root)
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_copy_is_h264_yuv420p_aac_mp4_and_keeps_original(self):
        destination = self.root / "browser-ready.mp4"
        result = browser_compatible_copy(self.source, destination, timeout=60)
        self.assertEqual(result, str(destination))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)
        payload = _probe_output(FFPROBE, destination)
        video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
        audio = [stream for stream in payload["streams"] if stream["codec_type"] == "audio"]
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual([stream["codec_name"] for stream in audio], ["aac"])
        format_names = set(payload["format"]["format_name"].split(","))
        self.assertTrue({"mov", "mp4"}.intersection(format_names))
        self.assertAlmostEqual(float(payload["format"]["duration"]), 1.0, delta=0.25)
        self.assertLessEqual(destination.stat().st_size, 1024**3)
        self.assertEqual(list(self.root.glob(".browser-copy-*")), [])

    def test_cancel_failure_and_output_limit_leave_no_partial_files(self):
        destination = self.root / "cancelled.mp4"

        def cancel(command, **_kwargs):
            Path(command[-1]).write_bytes(b"partial")
            raise InterruptedError("cancelled")

        with self.assertRaises(InterruptedError):
            browser_compatible_copy(self.source, destination, timeout=60, runner=cancel)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".browser-copy-*")), [])

        def failed(command, **_kwargs):
            Path(command[-1]).write_bytes(b"partial")
            return 1

        with self.assertRaises(BrowserCopyError):
            browser_compatible_copy(self.source, destination, timeout=60, runner=failed)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".browser-copy-*")), [])

        def too_large(command, **_kwargs):
            output_limit = int(command[command.index("-fs") + 1])
            with open(command[-1], "wb") as handle:
                handle.truncate(output_limit + 1)
            return 0

        with self.assertRaises(BrowserCopyLimitError):
            browser_compatible_copy(self.source, destination, timeout=60, runner=too_large)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".browser-copy-*")), [])

    def test_input_duration_and_disk_limits_are_checked(self):
        oversize = self.root / "oversize.mkv"
        with open(oversize, "wb") as handle:
            handle.truncate(BROWSER_COPY_MAX_INPUT_BYTES + 1)
        with self.assertRaises(BrowserCopyLimitError):
            validate_browser_copy(oversize, self.root)

        summary = {
            "size": 100,
            "duration": BROWSER_COPY_MAX_DURATION_SECONDS + 1,
            "width": 64,
            "height": 36,
            "frame_rate": 10.0,
            "audio_codecs": ("aac",),
            "format_names": "matroska,webm",
            "video_codec": "h264",
            "pixel_format": "yuv420p",
        }
        with (
            patch("services.video_transform._probe_browser_copy_media", return_value=summary),
            self.assertRaises(BrowserCopyLimitError),
        ):
            validate_browser_copy(self.source, self.root)

        summary["duration"] = 1.0
        with (
            patch("services.video_transform._probe_browser_copy_media", return_value=summary),
            patch("services.video_transform.shutil.disk_usage", return_value=types.SimpleNamespace(free=0)),
            self.assertRaises(BrowserCopySpaceError),
        ):
            validate_browser_copy(self.source, self.root)
