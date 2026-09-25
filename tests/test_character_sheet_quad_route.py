"""Model-free admission checks for the hidden Quad FLUX queue route."""

from __future__ import annotations

import ast
import asyncio
import copy
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.reference_admission import (  # noqa: E402
    ReferenceAdmissionCapacityError,
    ReferenceAdmissionCorruptionError,
    ReferenceAdmissionMismatchError,
    ReferenceAdmissionPersistenceError,
    ReferenceAdmissionValidationError,
)


class _Request:
    def __init__(self, body):
        self.body = body
        self.state = types.SimpleNamespace(maestro_session_id="owner-session")

    async def json(self):
        return self.body


class _Admission:
    def __init__(self, disposition="new"):
        self.disposition = disposition
        self.job_id = "quad_job_01"
        self.owns_lease = disposition == "new"
        self.lease_token = "lease-1"


class _AdmissionStore:
    lease_seconds = 3

    def __init__(self):
        self.disposition = "new"
        self.begun = []
        self.accepted = []

    def begin(self, request_id, **scope):
        self.begun.append((request_id, scope))
        return _Admission(self.disposition)

    def accept(self, request_id, **scope):
        self.accepted.append((request_id, scope))


class CharacterSheetQuadRouteTests(unittest.TestCase):
    def setUp(self):
        launch = APP / "launch.py"
        tree = ast.parse(launch.read_text(encoding="utf-8"), filename=str(launch))
        route = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "generate_character_sheet_quad"
        )
        route = copy.deepcopy(route)
        route.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[]))
        self.jobs = {}
        self.admission = _AdmissionStore()
        self.queued = []
        self.authorized = []
        self.runtime_available = True
        self.anchor_available = True
        self.anchor = {
            "anchor": {
                "schema_version": 3, "project_id": "scene",
                "anchor_id": "output_01", "kind": "generated",
                "sha256": "a" * 64,
                "source_model_id": "flux2_klein_9b",
                "source_model_family": "flux",
            },
            "source_path": "/synthetic/project/anchor.png",
        }
        self.resources = {
            "schedule": {"steps": 28, "guidance": 4.0},
            "model_artifact_commitment": "b" * 64,
        }

        def require_project(_request, project, *, permission=None):
            self.authorized.append((project, permission))
            if project != "scene":
                raise HTTPException(status_code=403, detail="Project access denied")
            return "/synthetic/project"

        def resolve_anchor(*_args):
            if not self.anchor_available:
                raise HTTPException(status_code=409, detail="Anchor changed")
            return self.anchor

        def runtime_snapshot():
            if not self.runtime_available:
                raise RuntimeError("Terms or LoRA unavailable")
            return self.resources

        def queue(job, **kwargs):
            self.jobs[job["id"]] = job
            self.queued.append((job, kwargs))

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "ReferenceAdmissionValidationError": ReferenceAdmissionValidationError,
            "ReferenceAdmissionMismatchError": ReferenceAdmissionMismatchError,
            "ReferenceAdmissionCapacityError": ReferenceAdmissionCapacityError,
            "ReferenceAdmissionCorruptionError": ReferenceAdmissionCorruptionError,
            "ReferenceAdmissionPersistenceError": ReferenceAdmissionPersistenceError,
            "_asset_scope": lambda _request, project: (project, "main"),
            "_require_project_access": require_project,
            "_require_project_asset_media_access": lambda *_args, **_kwargs: {"asset_type": "character"},
            "_resolve_character_sheet_anchor_for_request": resolve_anchor,
            "_character_sheet_quad_runtime_snapshot": runtime_snapshot,
            "_reference_admission_store": lambda: self.admission,
            "normalize_request_id": lambda value: value,
            "owner_principal_digest": lambda *_args: "owner-digest",
            "_session_secret": lambda: b"synthetic-test-secret-at-least-32-bytes",
            "_queue_recovery_project_identity": lambda *_args: "project-instance",
            "_character_sheet_quad_key": lambda: b"synthetic-test-secret-at-least-32-bytes",
            "_jobs": self.jobs,
            "_job_owned_by_request": lambda *_args: True,
            "_begin_workspace_operation": lambda *_args: None,
            "_end_workspace_operation": lambda *_args: None,
            "_queue_recovery_register_and_publish": queue,
            "_run_character_sheet_quad": lambda *_args: None,
            "_request_remote": types.SimpleNamespace(get=lambda: False),
            "wgp": types.SimpleNamespace(get_default_settings=lambda _model: {"guidance_scale": 4.0}),
            "uuid": types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex="proposed-id")),
            "time": types.SimpleNamespace(time=lambda: 1.0, monotonic=lambda: 1.0),
            "asyncio": asyncio,
        }
        exec(compile(module, str(launch), "exec"), namespace)
        self.route = namespace["generate_character_sheet_quad"]
        self.request = _Request({
            "request_id": "request_01", "anchor_variant_id": "variant_01",
            "anchor_output_id": "output_01", "seed": 7,
        })

    def test_hidden_route_queues_one_sealed_private_parent_and_replays_identity(self):
        with mock.patch("services.character_sheet_executor.build_quad_job_identity") as identity:
            identity.return_value = {"identity_seal": "c" * 64, "seed": 7}
            response = asyncio.run(self.route("scene", "asset_01", self.request))
        self.assertEqual(response["job_id"], "quad_job_01")
        self.assertEqual(len(self.queued), 1)
        job, arguments = self.queued[0]
        self.assertEqual(job["logical_job_kind"], "character_sheet_quad_parent")
        self.assertEqual(job["workspace"], "scene")
        self.assertEqual(job["params"]["image_refs"], [self.anchor["source_path"]])
        self.assertEqual(job["params"]["resolution"], "1024x1024")
        self.assertTrue(job["access_policy"]["private"])
        self.assertEqual(arguments["recovery_kind"], "studio_project_asset_preparation")
        self.assertEqual(self.authorized, [("scene", "project.generate")])
        self.admission.disposition = "replay"
        replay = asyncio.run(self.route("scene", "asset_01", self.request))
        self.assertEqual(replay["job_id"], response["job_id"])
        self.assertEqual(len(self.queued), 1)

    def test_anchor_or_runtime_failure_stops_before_admission(self):
        self.anchor_available = False
        with self.assertRaises(HTTPException) as anchor_failure:
            asyncio.run(self.route("scene", "asset_01", self.request))
        self.assertEqual(anchor_failure.exception.status_code, 409)
        self.assertEqual(self.admission.begun, [])
        self.anchor_available = True
        self.runtime_available = False
        with self.assertRaises(HTTPException) as runtime_failure:
            asyncio.run(self.route("scene", "asset_01", self.request))
        self.assertEqual(runtime_failure.exception.status_code, 409)
        self.assertEqual(self.admission.begun, [])


if __name__ == "__main__":
    unittest.main()
