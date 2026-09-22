"""CPU-only contract checks for Studio blend Insert and Overlap modes."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.blend_plan import (  # noqa: E402
    BLEND_CONTRACT_VERSION,
    BLEND_METADATA_INVALID_MESSAGE,
    MAX_BLEND_DURATION_SEC,
    build_blend_assembly,
    build_blend_plan,
    normalize_blend_request,
    resolve_blend_metadata,
    rewrite_blend_sidecar,
)
from services.output_access import output_policy_from_request  # noqa: E402


def _launch_tree() -> ast.Module:
    return ast.parse((APP / "launch.py").read_text(encoding="utf-8"), filename="app/launch.py")


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _load_function(name: str, namespace: dict):
    node = _function(_launch_tree(), name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "app/launch.py", "exec"), namespace)  # noqa: S102 - isolated route seam
    return namespace[name]


def _load_recoverable_input_keys() -> frozenset[str]:
    for node in _launch_tree().body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "_RECOVERABLE_INPUT_KEYS"
            for target in node.targets
        ):
            namespace = {"wgp": SimpleNamespace(ATTACHMENT_KEYS=())}
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, "app/launch.py", "exec"), namespace)  # noqa: S102
            return namespace["_RECOVERABLE_INPUT_KEYS"]
    raise AssertionError("_RECOVERABLE_INPUT_KEYS not found")


class _HTTPException(RuntimeError):
    def __init__(self, *, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Request:
    def __init__(self, body: dict, *, session_id: str = "1" * 32):
        self._body = body
        self.state = SimpleNamespace(
            maestro_session_id=session_id,
            maestro_remote=False,
        )

    async def json(self):
        return self._body


def _output_policy(params, *, owner_session_id):
    try:
        return output_policy_from_request(
            params,
            owner_session_id=owner_session_id,
        )
    except ValueError as error:
        raise _HTTPException(status_code=400, detail=str(error)) from error


class _Reader:
    def __init__(self, frames: int, fps: float):
        self._frames = frames
        self._fps = fps

    def __len__(self):
        return self._frames

    def get_avg_fps(self):
        return self._fps


class _FakeDecord:
    def __init__(self, clips: dict[str, tuple[int, float]]):
        self._clips = clips

    def VideoReader(self, path: str):
        frames, fps = self._clips[path]
        return _Reader(frames, fps)


def _worker_namespace(jobs: dict[str, dict], *, statuses: list):
    def finish_job(job, status, **updates):
        job.update(updates)
        job["status"] = status
        statuses.append(status)
        return True

    def record_job_outputs(job, output_files, **_kwargs):
        job["output_files"] = list(output_files)
        return list(output_files)

    def update_job(job, **updates):
        job.update(updates)
        return True

    def atomic_write_json(path, value):
        Path(path).write_text(json.dumps(value), encoding="utf-8")

    return {
        "_jobs": jobs,
        "_active_gen_states": {},
        "_run_generation": Mock(return_value=True),
        "register_abort_state": Mock(return_value=True),
        "unregister_abort_state": Mock(),
        "update_job": update_job,
        "record_job_outputs": record_job_outputs,
        "finish_job": finish_job,
        "fail_queued_job": lambda job, **updates: finish_job(job, "failed", **updates),
        "is_cancel_requested": Mock(return_value=False),
        "_atomic_write_json": atomic_write_json,
        "_workspace_dir": lambda: "/var/tmp",
        "json": json,
        "os": os,
        "subprocess": subprocess,
        "print": lambda *_args, **_kwargs: None,
        "traceback": __import__("traceback"),
    }


class TestBlendPlans(unittest.TestCase):
    def test_recovery_manifest_declares_both_authorized_source_clips(self):
        recoverable_keys = _load_recoverable_input_keys()
        self.assertTrue({"_blend_clip_a", "_blend_clip_b"} <= recoverable_keys)
        namespace = {
            "_RECOVERABLE_INPUT_KEYS": recoverable_keys,
            "os": os,
        }
        file_values = _load_function("_queue_recovery_file_values", namespace)
        self.assertEqual(
            file_values(
                {
                    "_blend_clip_a": "/var/tmp/blend-a.mp4",
                    "_blend_clip_b": "/var/tmp/blend-b.mp4",
                }
            ),
            [
                ("_blend_clip_a:0", "/var/tmp/blend-a.mp4"),
                ("_blend_clip_b:0", "/var/tmp/blend-b.mp4"),
            ],
        )

    def test_insert_uses_boundary_anchors_and_full_source_durations(self):
        plan = build_blend_plan("insert", 5, 24)
        self.assertEqual(plan["contract_version"], BLEND_CONTRACT_VERSION)
        self.assertEqual(plan["transition_frames"], 121)
        self.assertAlmostEqual(plan["effective_duration_sec"], 121 / 24)

        insert = build_blend_assembly(
            "insert", plan["effective_duration_sec"], 10, 8, fps=24,
        )
        self.assertEqual(insert["a_start_sec"], 0.0)
        self.assertEqual(insert["a_duration_sec"], 10.0)
        self.assertEqual(insert["b_start_sec"], 0.0)
        self.assertEqual(insert["b_duration_sec"], 8.0)

        overlap = build_blend_assembly(
            "overlap", plan["effective_duration_sec"], 10, 8, fps=24,
        )
        self.assertEqual(overlap["a_duration_sec"], 10 - 121 / 24)
        self.assertEqual(overlap["b_start_sec"], 121 / 24)
        self.assertEqual(overlap["b_duration_sec"], 8 - 121 / 24)

    def test_requested_limit_allows_lattice_rounding_slack(self):
        plan = build_blend_plan("insert", MAX_BLEND_DURATION_SEC, 24)
        self.assertEqual(plan["transition_frames"], 1441)
        self.assertGreater(plan["effective_duration_sec"], MAX_BLEND_DURATION_SEC)
        metadata = resolve_blend_metadata(
            {
                "_blend_contract_version": 1,
                "_blend_mode": "insert",
                "_blend_requested_duration_sec": MAX_BLEND_DURATION_SEC,
                "_blend_duration_sec": plan["effective_duration_sec"],
                "_blend_fps": 24,
            }
        )
        self.assertAlmostEqual(metadata["duration_sec"], plan["effective_duration_sec"])

        low_fps_plan = build_blend_plan("insert", MAX_BLEND_DURATION_SEC, 0.1)
        self.assertEqual(low_fps_plan["transition_frames"], 17)
        low_fps_metadata = resolve_blend_metadata(
            {
                "_blend_contract_version": 1,
                "_blend_mode": "insert",
                "_blend_requested_duration_sec": MAX_BLEND_DURATION_SEC,
                "_blend_duration_sec": low_fps_plan["effective_duration_sec"],
                "_blend_fps": 0.1,
            }
        )
        self.assertAlmostEqual(
            low_fps_metadata["duration_sec"], low_fps_plan["effective_duration_sec"],
        )

    def test_v1_fps_must_be_finite_and_positive(self):
        for fps in (0, -1, float("nan"), float("inf"), True, None):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                resolve_blend_metadata(
                    {
                        "_blend_contract_version": 1,
                        "_blend_mode": "insert",
                        "_blend_duration_sec": 3,
                        "_blend_fps": fps,
                    }
                )

    def test_v1_effective_duration_must_match_requested_frame_lattice(self):
        with self.assertRaisesRegex(ValueError, "frame lattice"):
            resolve_blend_metadata(
                {
                    "_blend_contract_version": 1,
                    "_blend_mode": "insert",
                    "_blend_requested_duration_sec": 1,
                    "_blend_duration_sec": 60,
                    "_blend_fps": 24,
                }
            )
        with self.assertRaisesRegex(ValueError, "missing requested"):
            resolve_blend_metadata(
                {
                    "_blend_contract_version": 1,
                    "_blend_mode": "insert",
                    "_blend_duration_sec": 25 / 24,
                    "_blend_fps": 24,
                }
            )

    def test_legacy_effective_duration_remains_unbounded_but_finite(self):
        legacy = resolve_blend_metadata(
            {"_blend_mode": "insert", "_blend_overlap_sec": 60.0416666667},
        )
        self.assertTrue(legacy["legacy"])
        self.assertEqual(legacy["mode"], "overlap")
        self.assertGreater(legacy["duration_sec"], MAX_BLEND_DURATION_SEC)
        assembly = build_blend_assembly(
            "overlap", legacy["duration_sec"], 120, 120, legacy=True,
        )
        self.assertAlmostEqual(assembly["duration_sec"], legacy["duration_sec"])
        with self.assertRaises(ValueError):
            resolve_blend_metadata(
                {"_blend_mode": "insert", "_blend_overlap_sec": float("nan")},
            )

    def test_sidecar_rewrite_updates_identity_and_prunes_owned_temp_only(self):
        with tempfile.TemporaryDirectory(dir="/var/tmp", prefix="maestro-sidecar-test-") as temp:
            temp_dir = str(Path(temp).resolve())
            sidecar = {
                "output_filename": "transition.mp4",
                "job_id": "blend-job",
                "audit": {"source": "clip-a", "review": "keep"},
                "params": {
                    "_blend_temp_dir": temp_dir,
                    "image_start": str(Path(temp_dir) / "a.png"),
                    "image_refs": [
                        temp_dir,
                        str(Path(temp_dir) / "hint.png"),
                        "/media/source.png",
                    ],
                    "_blend_clip_a": "/media/source-a.mp4",
                    "_blend_original_clip_a_name": "original-a.mov",
                    "prompt": "keep this prompt",
                },
                "upload_filenames": {
                    "image_start": "a.png",
                    "image_refs": ["temp-root", "hint.png", "source.png"],
                    "clip": "source-a.mp4",
                },
            }
            rewritten = rewrite_blend_sidecar(
                sidecar,
                output_filename="2026_blend.mp4",
                temp_dir=temp_dir,
            )
        self.assertEqual(rewritten["output_filename"], "2026_blend.mp4")
        self.assertEqual(rewritten["audit"], sidecar["audit"])
        self.assertNotIn("_blend_temp_dir", rewritten["params"])
        self.assertNotIn("image_start", rewritten["params"])
        self.assertEqual(rewritten["params"]["image_refs"], ["/media/source.png"])
        self.assertNotIn("_blend_clip_a", rewritten["params"])
        self.assertEqual(
            rewritten["blend_contract"]["sources"]["clip_a"],
            {"filename": "original-a.mov"},
        )
        self.assertNotIn("_blend_original_clip_a_name", rewritten["params"])
        self.assertNotIn("/media/", json.dumps(rewritten["blend_contract"]))
        self.assertNotIn("image_start", rewritten["upload_filenames"])
        self.assertEqual(rewritten["upload_filenames"]["image_refs"], ["source.png"])
        self.assertEqual(rewritten["upload_filenames"]["clip"], "source-a.mp4")

    def test_invalid_mode_and_duration_fail_closed(self):
        with self.assertRaises(ValueError):
            normalize_blend_request({"blend_mode": "crossfade", "overlap_sec": 3})
        for value in (0, -1, float("nan"), float("inf"), MAX_BLEND_DURATION_SEC + 1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_blend_request({"blend_mode": "insert", "transition_sec": value})

    def test_insert_legacy_duration_field_is_a_compatibility_fallback(self):
        plan = normalize_blend_request({"blend_mode": "insert", "overlap_sec": 4})
        self.assertEqual(plan["duration_field"], "transition_sec")
        self.assertEqual(plan["duration_sec"], 4.0)

    def test_insert_uses_transition_field_and_retains_v1_metadata(self):
        import numpy as np

        class Frame:
            def __init__(self, array):
                self._array = array
                self.shape = array.shape

            def asnumpy(self):
                return self._array

        class Reader:
            def __init__(self, seed):
                self._frames = [
                    Frame(np.full((64, 64, 3), seed + i, dtype=np.uint8))
                    for i in range(4)
                ]

            def __len__(self):
                return len(self._frames)

            def __getitem__(self, index):
                return self._frames[index]

            def get_avg_fps(self):
                return 24.0

        class Decord:
            def VideoReader(self, path):
                return Reader(10 if path.endswith("a.mp4") else 30)

        captured = []

        def publish(job, **_kwargs):
            if not job.get("session_id"):
                raise AssertionError("durable Blend job has no browser owner")
            if not isinstance(job.get("access_policy"), dict):
                raise AssertionError("durable Blend job has no access policy")
            captured.append(job)

        api = SimpleNamespace(post=lambda *_args, **_kwargs: (lambda function: function))
        endpoint = _load_function(
            "blend_endpoint",
            {
                "api": api,
                "Request": _Request,
                "HTTPException": _HTTPException,
                "_get_active_workspace": lambda: "default",
                "_require_project_access": lambda *_args, **_kwargs: "/var/tmp",
                "_resolve_authorized_request_media": lambda _request, path, _workspace: path,
                "_inherit_media_access_policy": lambda *_args, **_kwargs: {
                    "private": True,
                    "explicit": False,
                },
                "_http_output_policy_from_request": _output_policy,
                "_queue_recovery_register_and_publish": publish,
                "_run_blend_generation": lambda *_args, **_kwargs: None,
                "os": os,
                "uuid": uuid,
                "time": __import__("time"),
                "print": lambda *_args, **_kwargs: None,
            },
        )
        body = {
            "clip_a_path": "/var/tmp/a.mp4",
            "clip_b_path": "/var/tmp/b.mp4",
            "workspace": "default",
            "model_type": "video-model",
            "blend_mode": "insert",
            "transition_sec": 3,
            "anchor_frames": 5,
            "motion_prefix_sec": 2,
            "motion_suffix_sec": 2,
        }
        temp_dir = None
        with patch.dict(sys.modules, {"decord": Decord()}):
            response = asyncio.run(endpoint(_Request(body)))
        try:
            self.assertEqual(response["blend_mode"], "insert")
            self.assertIn("transition_sec", response)
            self.assertNotIn("overlap_sec", response)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["session_id"], "1" * 32)
            self.assertEqual(
                captured[0]["access_policy"],
                {"private": True, "explicit": False},
            )
            self.assertTrue(captured[0]["private"])
            self.assertFalse(captured[0]["explicit"])
            params = captured[0]["params"]
            temp_dir = params["_blend_temp_dir"]
            self.assertEqual(params["_blend_contract_version"], 1)
            self.assertEqual(params["_blend_mode"], "insert")
            self.assertEqual(params["_blend_requested_duration_sec"], 3.0)
            self.assertAlmostEqual(params["_blend_duration_sec"], 73 / 24)
            self.assertEqual(params["_blend_motion_prefix_sec"], 0.0)
            self.assertEqual(params["_blend_motion_suffix_sec"], 0.0)
            self.assertNotIn("_blend_overlap_sec", params)
            self.assertEqual(Path(params["image_start"]).name, "a_insert_end.png")
            self.assertEqual(Path(params["image_end"]).name, "b_insert_start.png")
            self.assertNotIn("video_source", params)
            self.assertNotIn("video_end", params)
            self.assertNotIn("video_prompt_type", params)
        finally:
            if temp_dir:
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)

    def test_versioned_metadata_requires_mode_and_legacy_insert_stays_overlap(self):
        legacy = resolve_blend_metadata(
            {"_blend_mode": "insert", "_blend_overlap_sec": 3},
        )
        self.assertTrue(legacy["legacy"])
        self.assertEqual(legacy["mode"], "overlap")

        with self.assertRaisesRegex(ValueError, "missing effective mode"):
            resolve_blend_metadata(
                {"_blend_contract_version": 1, "_blend_duration_sec": 3},
            )

    def test_source_duration_nan_is_rejected_before_clamp(self):
        with self.assertRaisesRegex(ValueError, "source durations"):
            build_blend_assembly("insert", 3, float("nan"), 8, fps=24)


class TestBlendEndpointValidation(unittest.TestCase):
    def test_invalid_request_is_rejected_before_temp_directory_creation(self):
        api = SimpleNamespace(post=lambda *_args, **_kwargs: (lambda function: function))
        endpoint = _load_function(
            "blend_endpoint",
            {
                "api": api,
                "Request": _Request,
                "HTTPException": _HTTPException,
                "_get_active_workspace": lambda: "default",
                "_require_project_access": lambda *_args, **_kwargs: "/var/tmp",
                "_resolve_authorized_request_media": lambda _request, path, _workspace: path,
                "os": os,
            },
        )
        body = {
            "clip_a_path": "/var/tmp/a.mp4",
            "clip_b_path": "/var/tmp/b.mp4",
            "workspace": "default",
            "model_type": "video-model",
            "blend_mode": "insert",
            "transition_sec": float("inf"),
        }
        with patch("tempfile.mkdtemp", side_effect=AssertionError("temp directory created too early")):
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(endpoint(_Request(body)))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("transition_sec", raised.exception.detail)

    def test_invalid_output_policy_fails_before_temp_directory_creation(self):
        api = SimpleNamespace(post=lambda *_args, **_kwargs: (lambda function: function))
        endpoint = _load_function(
            "blend_endpoint",
            {
                "api": api,
                "Request": _Request,
                "HTTPException": _HTTPException,
                "_get_active_workspace": lambda: "default",
                "_require_project_access": lambda *_args, **_kwargs: "/var/tmp",
                "_resolve_authorized_request_media": lambda _request, path, _workspace: path,
                "_inherit_media_access_policy": lambda *_args, **_kwargs: {
                    "private": False,
                    "explicit": False,
                },
                "_http_output_policy_from_request": _output_policy,
                "os": os,
            },
        )
        body = {
            "clip_a_path": "/var/tmp/a.mp4",
            "clip_b_path": "/var/tmp/b.mp4",
            "workspace": "default",
            "model_type": "video-model",
            "blend_mode": "insert",
            "transition_sec": 3,
            "private_output": "yes",
        }
        with patch("tempfile.mkdtemp", side_effect=AssertionError("temp directory created too early")):
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(endpoint(_Request(body)))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("private_output", raised.exception.detail)

    def test_invalid_video_probe_fails_before_queue_or_generation(self):
        class Frame:
            shape = (64, 64, 3)

        class Reader:
            def __len__(self):
                return 1

            def __getitem__(self, _index):
                return Frame()

            def get_avg_fps(self):
                return float("nan")

        class Decord:
            def VideoReader(self, _path):
                return Reader()

        published = []
        api = SimpleNamespace(post=lambda *_args, **_kwargs: (lambda function: function))
        endpoint = _load_function(
            "blend_endpoint",
            {
                "api": api,
                "Request": _Request,
                "HTTPException": _HTTPException,
                "_get_active_workspace": lambda: "default",
                "_require_project_access": lambda *_args, **_kwargs: "/var/tmp",
                "_resolve_authorized_request_media": lambda _request, path, _workspace: path,
                "_inherit_media_access_policy": lambda *_args, **_kwargs: {
                    "private": True,
                    "explicit": False,
                },
                "_http_output_policy_from_request": _output_policy,
                "_queue_recovery_register_and_publish": lambda job, **_kwargs: published.append(job),
                "os": os,
            },
        )
        body = {
            "clip_a_path": "/var/tmp/a.mp4",
            "clip_b_path": "/var/tmp/b.mp4",
            "workspace": "default",
            "model_type": "video-model",
            "blend_mode": "insert",
            "transition_sec": 3,
        }
        # Keep the endpoint's import across patch.dict's module restoration.
        __import__("numpy")
        with patch.dict(sys.modules, {"decord": Decord()}):
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(endpoint(_Request(body)))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "One or both Blend clips could not be read",
        )
        self.assertEqual(published, [])


class TestBlendWorkerAssembly(unittest.TestCase):
    def test_incomplete_versioned_metadata_fails_before_generation(self):
        statuses = []
        jobs = {
            "bad": {
                "id": "bad",
                "status": "queued",
                "params": {
                    "_blend_contract_version": 1,
                    "_blend_duration_sec": 3,
                    "_blend_temp_dir": "/var/tmp/does-not-exist",
                },
                "output_files": [],
            },
        }
        namespace = _worker_namespace(jobs, statuses=statuses)
        worker = _load_function("_run_blend_generation", namespace)
        worker("bad")
        self.assertEqual(statuses, ["failed"])
        self.assertFalse(namespace["_run_generation"].called)

    def test_bad_versioned_fps_uses_actionable_saved_settings_message(self):
        statuses = []
        jobs = {
            "bad-fps": {
                "id": "bad-fps",
                "status": "queued",
                "params": {
                    "_blend_contract_version": 1,
                    "_blend_mode": "insert",
                    "_blend_duration_sec": 3,
                    "_blend_fps": float("nan"),
                    "_blend_temp_dir": "/var/tmp/does-not-exist",
                },
                "output_files": [],
            },
        }
        namespace = _worker_namespace(jobs, statuses=statuses)
        worker = _load_function("_run_blend_generation", namespace)
        worker("bad-fps")
        self.assertEqual(statuses, ["failed"])
        self.assertEqual(jobs["bad-fps"]["message"], BLEND_METADATA_INVALID_MESSAGE)
        self.assertFalse(namespace["_run_generation"].called)

    def _run_worker(
        self,
        mode: str,
        *,
        legacy: bool = False,
        duration: float = 3,
        image_sources: bool = False,
        with_sidecar: bool = False,
        assembly_returncode: int = 0,
        cancel_during_assembly: bool = False,
        cancel_transition_only: bool = False,
        omit_sidecar: bool = False,
        mismatched_sidecar: bool = False,
        mismatched_policy: bool = False,
    ):
        with tempfile.TemporaryDirectory(dir="/var/tmp", prefix="maestro-blend-test-") as root:
            root_path = Path(root)
            out_dir = root_path / "out"
            out_dir.mkdir()
            temp_dir = root_path / "blend-temp"
            temp_dir.mkdir()
            clip_extension = ".png" if image_sources else ".mp4"
            clip_a = str(root_path / f"a{clip_extension}")
            clip_b = str(root_path / f"b{clip_extension}")
            Path(clip_a).touch()
            Path(clip_b).touch()
            transition = out_dir / "transition.mp4"
            transition.touch()
            clips = {} if image_sources else {clip_a: (240, 24), clip_b: (192, 24)}
            params = {
                "_blend_clip_a": clip_a,
                "_blend_clip_b": clip_b,
                "_blend_temp_dir": str(temp_dir),
                "_blend_fps": 24,
                "_blend_out_w": 640,
                "_blend_out_h": 360,
                "_blend_concat_w": 640,
                "_blend_concat_h": 360,
            }
            if legacy:
                params.update({"_blend_mode": "insert", "_blend_overlap_sec": 3})
            else:
                plan = build_blend_plan(mode, duration, 24)
                params.update(
                    {
                        "_blend_contract_version": 1,
                        "_blend_mode": mode,
                        "_blend_requested_duration_sec": duration,
                        "_blend_duration_sec": plan["effective_duration_sec"],
                    }
                )
            job = {
                "id": "blend-test",
                "status": "queued",
                "params": params,
                "output_files": [transition.name],
                "out_dir": str(out_dir),
                "workspace": "default",
                "access_policy": {"private": True, "explicit": False},
                "private": True,
                "explicit": False,
            }
            sidecar = {
                "output_filename": "transition.mp4",
                "job_id": "blend-test",
                "workspace": "default",
                "private": False if mismatched_policy else True,
                "explicit": False,
                "params": {} if mismatched_sidecar else dict(params),
            }
            if with_sidecar:
                sidecar.update(
                    {
                        "audit": {"source": "keep"},
                        "params": {
                            **params,
                            "_blend_temp_dir": str(temp_dir),
                            "image_start": str(temp_dir / "a.png"),
                            "_blend_clip_a": clip_a,
                            "prompt": "keep",
                        },
                        "upload_filenames": {"image_start": "a.png", "source": "keep.mp4"},
                    }
                )
            if not omit_sidecar:
                (out_dir / "transition.meta.json").write_text(
                    json.dumps(sidecar), encoding="utf-8",
                )
            jobs = {job["id"]: job}
            statuses = []
            commands = []
            filters = []
            namespace = _worker_namespace(jobs, statuses=statuses)
            if cancel_during_assembly:
                namespace["is_cancel_requested"] = Mock(side_effect=[False, True])
            elif cancel_transition_only:
                namespace["is_cancel_requested"] = Mock(side_effect=[False, True])
            worker = _load_function("_run_blend_generation", namespace)
            fake_decord = _FakeDecord(clips)
            # Keep the worker isolated from launch.py's heavy imports while
            # patching only the decord/subprocess seams it actually uses.
            def fake_run(command, **_kwargs):
                command = list(command)
                commands.append(command)
                if command and command[0] == "ffmpeg":
                    filter_path = command[command.index("-/filter_complex") + 1]
                    filters.append(Path(filter_path).read_text(encoding="utf-8"))
                    Path(command[-1]).touch()
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            class FakePopen:
                def __init__(self, command, **_kwargs):
                    self.command = list(command)
                    self.returncode = None
                    self.terminated = False
                    commands.append(self.command)
                    filter_path = self.command[self.command.index("-/filter_complex") + 1]
                    filters.append(Path(filter_path).read_text(encoding="utf-8"))
                    Path(self.command[-1]).touch()

                def poll(self):
                    if cancel_during_assembly and not self.terminated:
                        return None
                    self.returncode = assembly_returncode
                    return self.returncode

                def terminate(self):
                    self.terminated = True
                    self.returncode = -15

                def kill(self):
                    self.terminated = True
                    self.returncode = -9

                def wait(self, **_kwargs):
                    if self.returncode is None:
                        self.returncode = assembly_returncode
                    return self.returncode

                def communicate(self, **_kwargs):
                    if self.returncode is None:
                        self.returncode = assembly_returncode
                    return "", "synthetic ffmpeg failure" if self.returncode else ""

            with patch.dict(sys.modules, {"decord": fake_decord}), patch.object(
                subprocess, "run", side_effect=fake_run
            ), patch.object(
                subprocess, "Popen", FakePopen
            ):
                worker(job["id"])
            job["_test_remaining_files"] = sorted(path.name for path in out_dir.iterdir())
            sidecars = {
                path.name: json.loads(path.read_text(encoding="utf-8"))
                for path in out_dir.glob("*.meta.json")
            }
            return job, statuses, commands, filters, sidecars

    def test_insert_uses_untrimmed_a_and_b_inputs(self):
        job, statuses, commands, filters, _sidecars = self._run_worker("insert")
        ffmpeg = next(command for command in commands if command and command[0] == "ffmpeg")
        a_index = ffmpeg.index(str(Path(job["params"]["_blend_clip_a"])))
        b_index = ffmpeg.index(str(Path(job["params"]["_blend_clip_b"])))
        self.assertEqual(ffmpeg[a_index - 1], "-i")
        self.assertEqual(ffmpeg[b_index - 1], "-i")
        self.assertNotIn("-t", ffmpeg[:a_index])
        self.assertNotIn("-ss", ffmpeg[:b_index])
        self.assertIn("concat=n=3", filters[0])
        self.assertEqual(Path(ffmpeg[-1]).parent.name, "blend-temp")
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])
        self.assertEqual(
            len([name for name in job["_test_remaining_files"] if name.endswith("_blend.mp4")]),
            1,
        )
        self.assertEqual(statuses[-1], "completed")

    def test_cancel_during_ffmpeg_removes_unpublished_media_and_sidecar(self):
        job, statuses, _commands, _filters, _sidecars = self._run_worker(
            "insert", with_sidecar=True, cancel_during_assembly=True,
        )
        self.assertNotIn("completed", statuses)
        self.assertFalse(any(name.endswith("_blend.mp4") for name in job["_test_remaining_files"]))
        self.assertFalse(any(name.endswith("_blend.meta.json") for name in job["_test_remaining_files"]))
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])
        self.assertNotIn("transition.meta.json", job["_test_remaining_files"])

    def test_ffmpeg_failure_removes_partial_media_and_intermediate(self):
        job, statuses, _commands, _filters, _sidecars = self._run_worker(
            "insert", with_sidecar=True, assembly_returncode=1,
        )
        self.assertEqual(statuses[-1], "failed")
        self.assertFalse(any(name.endswith("_blend.mp4") for name in job["_test_remaining_files"]))
        self.assertFalse(any(name.endswith("_blend.meta.json") for name in job["_test_remaining_files"]))
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])
        self.assertNotIn("transition.meta.json", job["_test_remaining_files"])

    def test_transition_only_cancel_removes_media_and_sidecar(self):
        job, statuses, _commands, _filters, _sidecars = self._run_worker(
            "overlap", image_sources=True, with_sidecar=True,
            cancel_transition_only=True,
        )
        self.assertNotIn("completed", statuses)
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])
        self.assertNotIn("transition.meta.json", job["_test_remaining_files"])

    def test_missing_transition_sidecar_fails_without_public_final(self):
        job, statuses, _commands, _filters, _sidecars = self._run_worker(
            "insert", omit_sidecar=True,
        )
        self.assertEqual(statuses[-1], "failed")
        self.assertFalse(any(name.endswith("_blend.mp4") for name in job["_test_remaining_files"]))
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])

    def test_mismatched_transition_sidecar_fails_without_public_final(self):
        job, statuses, _commands, _filters, _sidecars = self._run_worker(
            "insert", mismatched_sidecar=True,
        )
        self.assertEqual(statuses[-1], "failed")
        self.assertFalse(any(name.endswith("_blend.mp4") for name in job["_test_remaining_files"]))
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])

    def test_mismatched_transition_policy_fails_without_public_final(self):
        job, statuses, _commands, _filters, _sidecars = self._run_worker(
            "insert", mismatched_policy=True,
        )
        self.assertEqual(statuses[-1], "failed")
        self.assertFalse(any(name.endswith("_blend.mp4") for name in job["_test_remaining_files"]))
        self.assertNotIn("transition.mp4", job["_test_remaining_files"])

    def test_overlap_keeps_trimmed_source_inputs(self):
        _job, statuses, commands, _filters, _sidecars = self._run_worker("overlap")
        ffmpeg = next(command for command in commands if command and command[0] == "ffmpeg")
        effective = build_blend_plan("overlap", 3, 24)["effective_duration_sec"]
        self.assertEqual(
            ffmpeg[:5], ["ffmpeg", "-y", "-t", f"{10 - effective:.4f}", "-i"],
        )
        self.assertIn(
            ["-ss", f"{effective:.4f}", "-i"],
            [ffmpeg[i : i + 3] for i in range(len(ffmpeg) - 2)],
        )
        self.assertEqual(statuses[-1], "completed")

    def test_overlap_keeps_a_trim_for_short_effective_duration(self):
        _job, statuses, commands, _filters, _sidecars = self._run_worker("overlap", duration=0.1)
        ffmpeg = next(command for command in commands if command and command[0] == "ffmpeg")
        self.assertIn("-t", ffmpeg[: ffmpeg.index("-i")])
        self.assertEqual(statuses[-1], "completed")

    def test_legacy_insert_metadata_assembles_overlap(self):
        _job, _statuses, commands, _filters, sidecars = self._run_worker(
            "insert", legacy=True, with_sidecar=True,
        )
        ffmpeg = next(command for command in commands if command and command[0] == "ffmpeg")
        self.assertIn("-t", ffmpeg)
        self.assertIn("-ss", ffmpeg)
        final_sidecar = next(iter(sidecars.values()))
        self.assertEqual(final_sidecar["blend_contract"]["version"], 0)
        self.assertTrue(final_sidecar["blend_contract"]["legacy"])
        self.assertEqual(final_sidecar["blend_contract"]["mode"], "overlap")
        self.assertEqual(final_sidecar["blend_contract"]["effective_duration_sec"], 3)

    def test_insert_loops_still_images_for_one_output_frame_each(self):
        _job, statuses, commands, _filters, _sidecars = self._run_worker(
            "insert", image_sources=True,
        )
        ffmpeg = next(command for command in commands if command and command[0] == "ffmpeg")
        self.assertEqual(ffmpeg.count("-loop"), 2)
        self.assertEqual(ffmpeg.count("-i"), 3)
        self.assertEqual(statuses[-1], "completed")

    def test_video_probe_failure_fails_without_ffmpeg_assembly(self):
        with tempfile.TemporaryDirectory(dir="/var/tmp", prefix="maestro-blend-probe-") as root:
            root_path = Path(root)
            out_dir = root_path / "out"
            out_dir.mkdir()
            temp_dir = root_path / "blend-temp"
            temp_dir.mkdir()
            clip_a = str(root_path / "a.mp4")
            clip_b = str(root_path / "b.mp4")
            Path(clip_a).touch()
            Path(clip_b).touch()
            transition = out_dir / "transition.mp4"
            transition.touch()
            params = {
                "_blend_clip_a": clip_a,
                "_blend_clip_b": clip_b,
                "_blend_temp_dir": str(temp_dir),
                "_blend_contract_version": 1,
                "_blend_mode": "insert",
                "_blend_requested_duration_sec": 3,
                "_blend_duration_sec": build_blend_plan("insert", 3, 24)["effective_duration_sec"],
                "_blend_fps": 24,
                "_blend_out_w": 640,
                "_blend_out_h": 360,
                "_blend_concat_w": 640,
                "_blend_concat_h": 360,
            }
            jobs = {
                "probe": {
                    "id": "probe",
                    "status": "queued",
                    "params": params,
                    "output_files": [transition.name],
                    "out_dir": str(out_dir),
                },
            }
            statuses = []
            commands = []
            namespace = _worker_namespace(jobs, statuses=statuses)
            worker = _load_function("_run_blend_generation", namespace)

            class BrokenDecord:
                def VideoReader(self, _path):
                    raise RuntimeError("synthetic decode failure")

            with patch.dict(sys.modules, {"decord": BrokenDecord()}), patch.object(
                subprocess, "run", side_effect=lambda command, **_kwargs: commands.append(list(command))
                or SimpleNamespace(returncode=0, stdout="", stderr=""),
            ):
                worker("probe")
        self.assertEqual(statuses[-1], "failed")
        self.assertFalse(any(command and command[0] == "ffmpeg" for command in commands))
        self.assertIn("source media", jobs["probe"]["error"])

    def test_final_sidecar_rebinds_output_and_prunes_temp_references(self):
        job, statuses, commands, _filters, sidecars = self._run_worker(
            "insert", with_sidecar=True,
        )
        self.assertEqual(statuses[-1], "completed")
        final_names = [name for name in sidecars if name != "transition.meta.json"]
        self.assertEqual(len(final_names), 1)
        final_name = final_names[0]
        final_sidecar = sidecars[final_name]
        self.assertEqual(final_sidecar["output_filename"], final_name.removesuffix(".meta.json") + ".mp4")
        self.assertEqual(final_sidecar["audit"], {"source": "keep"})
        self.assertNotIn("_blend_temp_dir", final_sidecar["params"])
        self.assertNotIn("image_start", final_sidecar["params"])
        self.assertNotIn("image_start", final_sidecar["upload_filenames"])
        self.assertNotIn("_blend_clip_a", final_sidecar["params"])
        self.assertEqual(
            final_sidecar["blend_contract"]["sources"]["clip_a"],
            {"filename": Path(job["params"]["_blend_clip_a"]).name},
        )

    def test_transition_only_sidecar_is_rewritten_before_temp_cleanup(self):
        _job, statuses, _commands, _filters, sidecars = self._run_worker(
            "overlap", image_sources=True, with_sidecar=True,
        )
        self.assertEqual(statuses[-1], "completed")
        final_sidecar = sidecars["transition.meta.json"]
        self.assertEqual(final_sidecar["output_filename"], "transition.mp4")
        self.assertNotIn("_blend_temp_dir", final_sidecar["params"])
        self.assertNotIn("image_start", final_sidecar["params"])


if __name__ == "__main__":
    unittest.main()
