"""Inpaint sidecars preserve user mask choices without running SAM or a model."""
from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "app" / "launch.py"


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Request:
    def __init__(self, body: dict):
        self._body = copy.deepcopy(body)

    async def json(self) -> dict:
        return copy.deepcopy(self._body)


class _Frame:
    shape = (720, 1280, 3)


class _VideoReader:
    def __init__(self, _path: str):
        pass

    def get_avg_fps(self) -> float:
        return 24.0

    def __len__(self) -> int:
        return 120

    def __getitem__(self, _index: int) -> _Frame:
        return _Frame()


def _load_endpoint(published_jobs: list[dict]):
    source = LAUNCH.read_text(encoding="utf-8")
    node = next(
        item for item in ast.parse(source, filename=str(LAUNCH)).body
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "inpaint_endpoint"
    )
    node = copy.deepcopy(node)
    node.decorator_list = []

    def publish(job, **kwargs):
        published_jobs.append({"job": copy.deepcopy(job), "kwargs": kwargs})

    namespace = {
        "Request": object,
        "HTTPException": _HTTPException,
        "os": types.SimpleNamespace(
            path=types.SimpleNamespace(isfile=lambda path: path == "cached.npy"),
        ),
        "time": types.SimpleNamespace(time=lambda: 1.0),
        "traceback": types.SimpleNamespace(print_exc=lambda: None),
        "uuid": types.SimpleNamespace(
            uuid4=lambda: types.SimpleNamespace(hex="metadatajob000000"),
        ),
        "_get_active_workspace": lambda: "default",
        "_require_project_access": (
            lambda _request, _workspace, *, permission: (
                "/project" if permission == "project.generate" else None
            )
        ),
        "_resolve_authorized_request_media": (
            lambda _request, path, _workspace: path
        ),
        "_queue_recovery_register_and_publish": publish,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(LAUNCH), "exec"),
        namespace,
    )
    return namespace["inpaint_endpoint"]


class InpaintSidecarMetadataTests(unittest.TestCase):
    def test_lower_resolution_scales_before_segmentation_and_reuses_source_probe(self):
        events: list[tuple] = []

        class CountingVideoReader(_VideoReader):
            def __init__(self, path: str):
                events.append(("probe", path))

        def run_ffmpeg(command, **_kwargs):
            events.append(("scale", command[-1], command[command.index("-vf") + 1]))
            Path(command[-1]).write_bytes(b"scaled video")
            return types.SimpleNamespace(returncode=0)

        def segment_video(**kwargs):
            events.append(("segment", kwargs["video_path"]))
            masks_dir = Path(kwargs["video_path"]).parent / ".masks"
            masks_dir.mkdir()
            mask = masks_dir / "generated.npy"
            mask.write_bytes(b"completed mask")
            return {"masks_path": str(mask)}

        publish_spec = importlib.util.spec_from_file_location(
            "services.atomic_file_publish", ROOT / "app/services/atomic_file_publish.py",
        )
        atomic_file_publish = importlib.util.module_from_spec(publish_spec)
        publish_spec.loader.exec_module(atomic_file_publish)

        inpaint_service = types.ModuleType("services.inpaint_service")
        inpaint_service.check_sam_status = lambda: None
        inpaint_service.segment_video = segment_video
        inpaint_service.unload_sam = lambda: None
        inpaint_service.ensure_sam_running = lambda: True
        inpaint_service.shutdown_sam = lambda: None
        inpaint_service.parse_inpaint_intent = lambda description: {
            "target": "subject", "prompt": description, "negative_prompt": "",
        }
        services = types.ModuleType("services")
        services.__path__ = []
        decord = types.ModuleType("decord")
        decord.VideoReader = CountingVideoReader
        subprocess = types.ModuleType("subprocess")
        subprocess.run = run_ffmpeg

        with tempfile.TemporaryDirectory() as project:
            source = Path(project) / "source.mp4"
            source.write_bytes(b"original source")
            existing_sibling = Path(project) / "source_sam_scaled.mp4"
            existing_sibling.write_bytes(b"owned existing video")
            existing_masks_dir = Path(project) / ".masks"
            existing_masks_dir.mkdir(mode=0o755)
            existing_masks_dir.chmod(0o755)
            (existing_masks_dir / "old.npy").write_bytes(b"owned existing mask")
            published_jobs: list[dict] = []
            endpoint = _load_endpoint(published_jobs)
            endpoint.__globals__["os"] = os
            endpoint.__globals__["_require_project_access"] = (
                lambda _request, _workspace, *, permission: project
            )
            body = {
                "video_path": str(source), "description": "Replace the subject.",
                "model_type": "ltx2", "resolution": "640x360",
            }
            with (
                patch.dict(sys.modules, {
                    "services": services,
                    "services.inpaint_service": inpaint_service,
                    "services.atomic_file_publish": atomic_file_publish,
                    "decord": decord,
                    "subprocess": subprocess,
                }),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                asyncio.run(endpoint(_Request(body)))

            self.assertEqual(events[0], ("probe", str(source)))
            self.assertEqual(events[1][0], "scale")
            scaled = Path(events[1][1])
            self.assertEqual(scaled.parent.parent, Path(project))
            self.assertEqual(events[1][2], "scale=640:352:flags=lanczos")
            self.assertEqual(events[2], ("segment", str(scaled)))
            self.assertFalse(scaled.exists())
            self.assertFalse(scaled.parent.exists())
            self.assertEqual(existing_sibling.read_bytes(), b"owned existing video")
            self.assertEqual(source.read_bytes(), b"original source")
            self.assertEqual(published_jobs[0]["job"]["params"]["resolution"], "640x360")
            self.assertEqual(published_jobs[0]["job"]["params"]["video_length"], 120)
            saved_mask = Path(published_jobs[0]["job"]["params"]["retake_masks_path"])
            self.assertEqual(saved_mask.parent.parent, Path(project))
            self.assertTrue(saved_mask.parent.name.startswith(".inpaint-mask-"))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(saved_mask.parent.stat().st_mode), 0o700)
            self.assertEqual(saved_mask.read_bytes(), b"completed mask")
            self.assertEqual((existing_masks_dir / "old.npy").read_bytes(), b"owned existing mask")

            permissive_dir = Path(project) / ".inpaint-mask-existing"
            permissive_dir.mkdir(mode=0o755)
            permissive_dir.chmod(0o755)
            (permissive_dir / "old.npy").write_bytes(b"owned private mask")
            events.clear()
            with (
                patch.dict(sys.modules, {
                    "services": services,
                    "services.inpaint_service": inpaint_service,
                    "services.atomic_file_publish": atomic_file_publish,
                    "decord": decord,
                    "subprocess": subprocess,
                }),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                asyncio.run(endpoint(_Request(body)))
            self.assertEqual(len(published_jobs), 2)
            second_mask = Path(published_jobs[1]["job"]["params"]["retake_masks_path"])
            self.assertNotEqual(second_mask.parent, saved_mask.parent)
            self.assertNotEqual(second_mask.parent, permissive_dir)
            self.assertEqual(second_mask.read_bytes(), b"completed mask")
            self.assertEqual(saved_mask.read_bytes(), b"completed mask")
            self.assertEqual((permissive_dir / "old.npy").read_bytes(), b"owned private mask")

            events.clear()
            inpaint_service.segment_video = lambda **_kwargs: {
                "masks_path": str(existing_sibling),
            }
            with (
                patch.dict(sys.modules, {
                    "services": services,
                    "services.inpaint_service": inpaint_service,
                    "services.atomic_file_publish": atomic_file_publish,
                    "decord": decord,
                    "subprocess": subprocess,
                }),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(_HTTPException) as escaped_mask,
            ):
                asyncio.run(endpoint(_Request({**body, "invert_mask": True})))
            self.assertEqual(escaped_mask.exception.status_code, 500)
            self.assertFalse(Path(events[1][1]).parent.exists())
            self.assertEqual(existing_sibling.read_bytes(), b"owned existing video")
            self.assertEqual(len(published_jobs), 2)

            if os.name != "nt":
                def symlinked_mask(**kwargs):
                    masks_dir = Path(kwargs["video_path"]).parent / ".masks"
                    masks_dir.mkdir()
                    link = masks_dir / "linked.npy"
                    link.symlink_to(existing_sibling)
                    return {"masks_path": str(link)}

                events.clear()
                inpaint_service.segment_video = symlinked_mask
                with (
                    patch.dict(sys.modules, {
                        "services": services,
                        "services.inpaint_service": inpaint_service,
                        "services.atomic_file_publish": atomic_file_publish,
                        "decord": decord,
                        "subprocess": subprocess,
                    }),
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(_HTTPException) as symlink_mask,
                ):
                    asyncio.run(endpoint(_Request({**body, "invert_mask": True})))
                self.assertEqual(symlink_mask.exception.status_code, 500)
                self.assertFalse(Path(events[1][1]).parent.exists())
                self.assertEqual(existing_sibling.read_bytes(), b"owned existing video")
                self.assertEqual(len(published_jobs), 2)

            events.clear()
            inpaint_service.ensure_sam_running = lambda: False
            with (
                patch.dict(sys.modules, {
                    "services": services,
                    "services.inpaint_service": inpaint_service,
                    "services.atomic_file_publish": atomic_file_publish,
                    "decord": decord,
                    "subprocess": subprocess,
                }),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(_HTTPException) as failure,
            ):
                asyncio.run(endpoint(_Request(body)))
            self.assertEqual(failure.exception.status_code, 503)
            self.assertEqual([event[0] for event in events], ["probe", "scale"])
            self.assertFalse(Path(events[1][1]).parent.exists())
            self.assertEqual(existing_sibling.read_bytes(), b"owned existing video")

    def test_explicit_target_and_invert_choice_survive_all_four_states(self):
        forbidden_calls: list[str] = []

        def forbidden(name: str):
            def call(*_args, **_kwargs):
                forbidden_calls.append(name)
                raise AssertionError(f"{name} must not run")

            return call

        inpaint_service = types.ModuleType("services.inpaint_service")
        inpaint_service.check_sam_status = forbidden("check_sam_status")
        inpaint_service.segment_video = forbidden("segment_video")
        inpaint_service.unload_sam = forbidden("unload_sam")
        inpaint_service.ensure_sam_running = forbidden("ensure_sam_running")
        inpaint_service.shutdown_sam = lambda: None
        inpaint_service.parse_inpaint_intent = lambda description: {
            "target": "parsed subject",
            "prompt": description,
            "negative_prompt": "",
        }
        services = types.ModuleType("services")
        services.__path__ = []
        decord = types.ModuleType("decord")
        decord.VideoReader = _VideoReader

        for explicit_target in ("", "  lead dancer  "):
            for invert_mask in (False, True):
                with self.subTest(
                    explicit_target=explicit_target,
                    invert_mask=invert_mask,
                ):
                    published_jobs: list[dict] = []
                    endpoint = _load_endpoint(published_jobs)
                    body = {
                        "video_path": "source.mp4",
                        "description": "Replace the selected region.",
                        "sam_target": explicit_target,
                        "invert_mask": invert_mask,
                        "model_type": "ltx2",
                        "masks_path": "cached.npy",
                    }
                    with (
                        patch.dict(sys.modules, {
                            "services": services,
                            "services.inpaint_service": inpaint_service,
                            "decord": decord,
                        }),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        result = asyncio.run(endpoint(_Request(body)))

                    self.assertEqual(len(published_jobs), 1)
                    published = published_jobs[0]
                    self.assertEqual(published["kwargs"], {})
                    params = published["job"]["params"]
                    expected_explicit = explicit_target.strip()
                    expected_effective = expected_explicit or "parsed subject"
                    self.assertEqual(params["edit_target"], expected_effective)
                    self.assertEqual(params["edit_sam_target"], expected_explicit)
                    self.assertIs(params["edit_invert_mask"], invert_mask)
                    self.assertEqual(result["target"], expected_effective)
                    self.assertEqual(forbidden_calls, [])


if __name__ == "__main__":
    unittest.main()
