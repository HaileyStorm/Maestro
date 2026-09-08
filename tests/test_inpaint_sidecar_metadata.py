"""Inpaint sidecars preserve user mask choices without running SAM or a model."""
from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import io
from pathlib import Path
import sys
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
