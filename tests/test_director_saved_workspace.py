"""Workspace-selection contracts for Director's saved-state routes."""
from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services import director_pipeline as director  # noqa: E402


LAUNCH_PATH = APP_DIR / "launch.py"
SAVED_ROUTE_NAMES = {
    "director_pipeline_resume",
    "list_saved_pipelines",
    "get_saved_pipeline",
    "tag_pipeline_clip",
    "repair_saved_pipeline",
    "cancel_saved_pipeline_repair",
    "rerun_pipeline_clip_image",
    "rerun_pipeline_clip_video",
    "rejoin_pipeline_clips",
    "delete_pipeline_endpoint",
}


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Request:
    def __init__(self, *, remote: bool, body: dict | None = None):
        self.state = types.SimpleNamespace(
            maestro_remote=remote,
            maestro_session_id="owner",
        )
        self._body = dict(body or {})

    async def json(self):
        return dict(self._body)


def _route_namespace():
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    selected = SAVED_ROUTE_NAMES | {"_request_project_workspace"}
    body = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in selected:
            continue
        copied = copy.deepcopy(node)
        copied.decorator_list = []
        body.append(copied)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)

    access_calls: list[str] = []
    saved_calls: list[tuple[str, str]] = []
    operations: list[tuple[str, str]] = []
    init_calls: list[bool] = []

    def require_access(_request, workspace):
        access_calls.append(workspace)
        return f"/projects/{workspace}"

    def require_saved(_request, pid, workspace):
        saved_calls.append((pid, workspace))
        return (
            {"pipeline_id": pid, "status": "failed", "workspace": workspace},
            f"/projects/{workspace}",
        )

    namespace = {
        "Request": _Request,
        "HTTPException": _HTTPException,
        "JSONResponse": lambda payload, status_code=200: {
            "payload": payload,
            "status_code": status_code,
        },
        "_get_active_workspace": lambda: "local-active",
        "_init_pipeline": lambda: init_calls.append(True),
        "_require_project_access": require_access,
        "_require_saved_pipeline": require_saved,
        "_public_pipeline_state": lambda state: dict(state),
        "_saved_pipeline_live_recovery_overlay": lambda _pid: {},
        "_begin_workspace_operation": lambda workspace: operations.append(
            ("begin", workspace)
        ),
        "_end_workspace_operation": lambda workspace: operations.append(
            ("end", workspace)
        ),
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    namespace["access_calls"] = access_calls
    namespace["saved_calls"] = saved_calls
    namespace["operations"] = operations
    namespace["init_calls"] = init_calls
    return namespace


class TestDirectorSavedWorkspaceSelection(unittest.TestCase):
    def test_helper_preserves_local_fallback_and_requires_remote_scope(self):
        namespace = _route_namespace()
        resolve = namespace["_request_project_workspace"]

        self.assertEqual(
            resolve(_Request(remote=False), None),
            "local-active",
        )
        self.assertEqual(
            resolve(_Request(remote=True), "project-a"),
            "project-a",
        )
        with self.assertRaises(_HTTPException) as raised:
            resolve(_Request(remote=True), "")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "workspace is required for remote project access",
        )

    def test_remote_omission_is_rejected_for_list_get_resume_and_body_mutation(self):
        namespace = _route_namespace()
        remote = _Request(remote=True)
        calls = (
            lambda: namespace["list_saved_pipelines"](remote),
            lambda: namespace["get_saved_pipeline"](remote, "pipe-a"),
            lambda: namespace["director_pipeline_resume"](remote, "pipe-a"),
            lambda: asyncio.run(namespace["tag_pipeline_clip"](
                "pipe-a",
                0,
                _Request(remote=True, body={"tag": "good"}),
            )),
        )
        for call in calls:
            with self.subTest(route=call):
                with self.assertRaises(_HTTPException) as raised:
                    call()
                self.assertEqual(raised.exception.status_code, 400)

        self.assertEqual(namespace["access_calls"], [])
        self.assertEqual(namespace["saved_calls"], [])
        self.assertEqual(namespace["init_calls"], [])

    def test_explicit_workspace_succeeds_for_list_get_resume_and_body_mutation(self):
        namespace = _route_namespace()
        remote = _Request(remote=True)
        with (
            patch.object(director, "list_pipeline_states", return_value=[]),
            patch.object(director, "get_pipeline", return_value={}),
            patch.object(director, "resume_pipeline", return_value=(True, "resumed")),
            patch.object(director, "update_clip_tag", return_value=True),
        ):
            self.assertEqual(
                namespace["list_saved_pipelines"](remote, "project-a"),
                {"pipelines": []},
            )
            saved = namespace["get_saved_pipeline"](
                remote, "pipe-a", "project-a",
            )
            self.assertEqual(saved["workspace"], "project-a")
            self.assertEqual(
                namespace["director_pipeline_resume"](
                    remote, "pipe-a", "project-a",
                ),
                {"status": "resumed", "pipeline_id": "pipe-a"},
            )
            self.assertEqual(
                asyncio.run(namespace["tag_pipeline_clip"](
                    "pipe-a",
                    0,
                    _Request(
                        remote=True,
                        body={"workspace": "project-a", "tag": "good"},
                    ),
                )),
                {"status": "ok"},
            )

        self.assertEqual(namespace["access_calls"], ["project-a"])
        self.assertEqual(
            namespace["saved_calls"],
            [
                ("pipe-a", "project-a"),
                ("pipe-a", "project-a"),
                ("pipe-a", "project-a"),
            ],
        )
        self.assertEqual(
            namespace["operations"],
            [
                ("begin", "project-a"),
                ("end", "project-a"),
                ("begin", "project-a"),
                ("end", "project-a"),
            ],
        )
        self.assertEqual(namespace["init_calls"], [True])

    def test_every_saved_state_route_resolves_workspace_before_authorization(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAUNCH_PATH))
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in SAVED_ROUTE_NAMES
        }
        self.assertEqual(set(functions), SAVED_ROUTE_NAMES)
        for name, function_source in functions.items():
            with self.subTest(route=name):
                self.assertIn("_request_project_workspace", function_source)
                self.assertNotIn("_get_active_workspace", function_source)
                authorize_at = min(
                    position
                    for marker in (
                        "_require_project_access",
                        "_require_saved_pipeline",
                    )
                    if (position := function_source.find(marker)) >= 0
                )
                self.assertLess(
                    function_source.index("_request_project_workspace"),
                    authorize_at,
                )
                init_at = function_source.find("_init_pipeline")
                if init_at >= 0:
                    self.assertLess(
                        function_source.index("_request_project_workspace"),
                        init_at,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
