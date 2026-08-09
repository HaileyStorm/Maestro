import ast
import asyncio
import json
import math
import os
import re
import tempfile
import time
import types
import unittest
import uuid
from contextvars import ContextVar
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from PIL import Image

from services.output_access import public_output_policy, stamp_sidecar_policy
from services.project_assets import ProjectAssetStore


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "app" / "launch.py"


def _load_route_symbols(namespace):
    wanted = {
        "_project_asset_error",
        "_project_asset_provenance",
        "_project_reference_text",
        "_project_reference_dimensions",
        "_project_reference_request_config",
        "_project_reference_creative_request",
        "_project_reference_generation_params",
        "_write_project_reference_sidecar",
        "_run_project_reference_image_job",
        "_project_reference_local_generate",
        "_project_reference_local_reviewer",
        "_attach_project_reference_result",
        "generate_project_asset_references",
    }
    tree = ast.parse(LAUNCH.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id.startswith("_PROJECT_REFERENCE_")
            for target in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            node.decorator_list = []
            nodes.append(node)
    missing = wanted.difference(
        node.name for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    if missing:
        raise AssertionError(f"missing route symbols: {sorted(missing)}")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(LAUNCH), "exec"), namespace)
    return namespace


class _ModelRegistry:
    definitions = {
        "flux2_klein_9b": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "qwen_image_edit_2511_20B_fp8_lightning_4step": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "bad_editor": {"image_outputs": True},
        "video_only": {"image_outputs": False},
    }
    bases = {
        "flux2_klein_9b": "flux2_klein_9b",
        "qwen_image_edit_2511_20B_fp8_lightning_4step": "qwen_image_edit_plus2_20B",
        "bad_editor": "unknown",
        "video_only": "video",
    }

    @classmethod
    def get_model_def(cls, model):
        return cls.definitions.get(model)

    @classmethod
    def get_base_model_type(cls, model):
        return cls.bases.get(model)


class _Request:
    def __init__(self, body, session="owner-session"):
        self._body = body
        self.state = types.SimpleNamespace(
            maestro_session_id=session,
            maestro_remote=False,
        )

    async def json(self):
        return self._body


class _DoneThread:
    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class ProjectReferenceRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "outputs" / "project"
        self.output.mkdir(parents=True)
        self.store = ProjectAssetStore(
            self.root / "storage",
            allowed_source_roots=[self.root / "outputs"],
        )
        self.jobs = {}
        self.calls = []
        self.visibility_calls = []
        self.workspace_events = []
        self.review = self._passing_review

        namespace = {
            "HTTPException": HTTPException,
            "Request": object,
            "Path": Path,
            "json": json,
            "math": math,
            "os": os,
            "re": re,
            "time": time,
            "types": types,
            "uuid": uuid,
            "wgp": _ModelRegistry,
            "public_output_policy": public_output_policy,
            "stamp_sidecar_policy": stamp_sidecar_policy,
            "_jobs": self.jobs,
            "_active_gen_states": {},
            "_request_remote": ContextVar("route_test_remote", default=False),
            "_project_asset_store": lambda: self.store,
            "_asset_scope": self._asset_scope,
            "_require_project_access": lambda request, project: str(self.output),
            "_require_project_asset_media_access": self._require_asset,
            "_require_remote_visible_models": self._visible_models,
            "_http_output_policy_from_request": self._output_policy,
            "_begin_workspace_operation": lambda project: self.workspace_events.append(("begin", project)),
            "_end_workspace_operation": lambda project: self.workspace_events.append(("end", project)),
            "_queue_recovery_register_and_publish": self._register,
            "try_start": self._try_start,
            "update_job": self._update_job,
            "finish_job": self._finish_job,
            "snapshot_job": lambda job: dict(job),
            "request_cancel": lambda *args, **kwargs: None,
            "is_cancel_requested": lambda job: bool(job.get("cancel_requested")),
        }
        self.ns = _load_route_symbols(namespace)
        self.real_image_job = self.ns["_run_project_reference_image_job"]
        self.real_reviewer = self.ns["_project_reference_local_reviewer"]
        self.ns["_run_project_reference_image_job"] = self._image_job
        self.ns["_project_reference_local_reviewer"] = (
            lambda job, request: self.review(request)
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _passing_review(request):
        return {
            "status": "pass",
            "checks": {
                "identity": True,
                "request": True,
                "view": True,
                "accessory": True,
                "style": True,
            },
            "failed_roles": [],
            "reason_codes": [],
        }

    def _asset_scope(self, request, project):
        if request.state.maestro_session_id != "owner-session":
            raise HTTPException(status_code=404, detail="Project not found")
        return project, "main"

    def _require_asset(
        self, project, workspace, asset_id, session, *, variant_id=None,
    ):
        if session != "owner-session":
            raise HTTPException(status_code=404, detail="Reference asset not found")
        asset = self.store.get_asset(project, workspace, asset_id)
        if variant_id is not None and not any(
            item.get("id") == variant_id for item in asset.get("variants") or []
        ):
            raise HTTPException(status_code=404, detail="Reference variant not found")
        return asset

    def _visible_models(self, request, models):
        self.visibility_calls.append(tuple(models))
        if any(model == "hidden" for model in models if model):
            raise HTTPException(status_code=404, detail="Model not found")

    @staticmethod
    def _output_policy(body, *, owner_session_id):
        private = body.get("private_output")
        explicit = body.get("explicit_output")
        if private is not None and not isinstance(private, bool):
            raise HTTPException(status_code=400, detail="invalid private_output")
        if explicit is not None and not isinstance(explicit, bool):
            raise HTTPException(status_code=400, detail="invalid explicit_output")
        explicit = bool(explicit)
        return {
            "private": explicit if private is None else private,
            "explicit": explicit,
        }

    def _register(self, job, *, worker=None, **kwargs):
        job.setdefault("access_policy", {
            "private": bool(job.pop("private", False)),
            "explicit": bool(job.pop("explicit", False)),
        })
        self.jobs[job["id"]] = job
        if worker is not None:
            worker(job["id"])
        return _DoneThread()

    @staticmethod
    def _try_start(job, **updates):
        if job.get("status") != "queued":
            return False
        job.update(updates)
        job["status"] = "running"
        return True

    @staticmethod
    def _update_job(job, **updates):
        if job.get("status") != "running":
            return False
        job.update(updates)
        return True

    @staticmethod
    def _finish_job(job, status, **updates):
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["message"] = "Cancelled"
            return False
        if job.get("status") != "running":
            return False
        job.update(updates)
        job["status"] = status
        return True

    def _image_job(self, parent_job, params, *, role, phase, step, total_steps):
        call = {
            "role": role,
            "model": params["model_type"],
            "reference": list(params.get("image_refs") or []),
            "phase": phase,
            "prompt": params["prompt"],
        }
        self.calls.append(call)
        width, height = [int(value) for value in params["resolution"].split("x")]
        path = self.output / f"synthetic_{len(self.calls):03d}_{role}.png"
        Image.new(
            "RGB", (width, height),
            ((len(self.calls) * 31) % 255, 80, 140),
        ).save(path)
        return str(path)

    def _body(self, **updates):
        body = {
            "name": "Aster",
            "asset_type": "character",
            "description": "A consistent traveler design",
            "model_type": "flux2_klein_9b",
            "panel_size": [64, 64],
            "draft_size": [128, 128],
            "candidate_count": 1,
            "private_output": True,
            "explicit_output": True,
        }
        body.update(updates)
        return body

    def _run(self, body):
        return asyncio.run(
            self.ns["generate_project_asset_references"](
                "project", _Request(body),
            )
        )

    def _assets(self):
        return self.store.list_assets("project", "main")

    def test_production_default_generates_independent_order_and_atomic_sheet_first(self):
        response = self._run(self._body())
        self.assertTrue(response["asset"]["pending"])
        self.assertEqual(
            [call["role"] for call in self.calls],
            [
                "identity_front", "three_quarter", "profile", "full_body",
                "expression", "accessory_detail",
            ],
        )
        self.assertTrue(all(not call["reference"] for call in self.calls))
        asset = self._assets()[0]
        self.assertEqual(len(asset["variants"]), 1)
        outputs = asset["variants"][0]["outputs"]
        self.assertEqual(outputs[0]["label"], "Reference sheet")
        self.assertEqual(len(outputs), 7)
        self.assertNotIn("palette", [item["label"] for item in outputs])
        self.assertEqual(self.jobs[response["job_id"]]["status"], "completed")

    def test_hybrid_generates_anchor_then_local_targeted_edits(self):
        self._run(self._body(
            mode="hybrid",
            editor_model_type="qwen_image_edit_2511_20B_fp8_lightning_4step",
        ))
        self.assertEqual(self.calls[0]["model"], "flux2_klein_9b")
        self.assertEqual(self.calls[0]["reference"], [])
        anchor = str(self.output / "synthetic_001_identity_front.png")
        for call in self.calls[1:]:
            self.assertEqual(
                call["model"],
                "qwen_image_edit_2511_20B_fp8_lightning_4step",
            )
            self.assertEqual(call["reference"], [anchor])
        self.assertEqual(len(self.calls), 6)

    def test_hybrid_capability_rejection_precedes_asset_or_job_creation(self):
        with self.assertRaises(HTTPException) as raised:
            self._run(self._body(mode="hybrid", editor_model_type="bad_editor"))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self._assets(), [])
        self.assertEqual(self.jobs, {})

    def test_draft_is_one_shot_and_one_physical_output(self):
        self._run(self._body(mode="draft"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["role"], "sheet")
        outputs = self._assets()[0]["variants"][0]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["metadata"]["reference_sheet"]["role"], "sheet")

    def test_unavailable_local_vlm_is_nonfatal_and_persisted_as_bounded_status(self):
        self.review = lambda request: (_ for _ in ()).throw(RuntimeError("offline secret"))
        self._run(self._body())
        metadata = self._assets()[0]["variants"][0]["metadata"]["reference_sheet"]
        self.assertEqual(metadata["review_status"], "review_unavailable")
        self.assertEqual(metadata["reason_codes"], ["review_unavailable"])
        self.assertNotIn("offline secret", json.dumps(metadata))

    def test_semantic_failure_repairs_at_most_one_panel_once(self):
        reviews = {"count": 0}

        def review(request):
            reviews["count"] += 1
            if reviews["count"] == 1:
                return {
                    "status": "fail",
                    "checks": {
                        "identity": False,
                        "request": True,
                        "view": True,
                        "accessory": True,
                        "style": True,
                    },
                    "failed_roles": [request.panel_roles[0], request.panel_roles[1]],
                    "reason_codes": ["identity_mismatch"],
                }
            return self._passing_review(request)

        self.review = review
        self._run(self._body())
        roles = [call["role"] for call in self.calls]
        self.assertEqual(roles.count("identity_front"), 2)
        self.assertEqual(roles.count("three_quarter"), 1)
        self.assertEqual(len(self.calls), 7)
        metadata = self._assets()[0]["variants"][0]["metadata"]["reference_sheet"]
        self.assertEqual(metadata["roles"]["repaired"], ["identity_front"])

    def test_candidate_count_creates_separate_idempotent_sheet_variants(self):
        response = self._run(self._body(candidate_count=2, mode="draft"))
        asset = self._assets()[0]
        self.assertEqual(
            [variant["id"] for variant in asset["variants"]],
            [
                f"{response['job_id']}_sheet_1",
                f"{response['job_id']}_sheet_2",
            ],
        )
        self.assertEqual(len(self.calls), 2)

    def test_failure_before_first_complete_sheet_leaves_no_empty_card(self):
        def fail(*args, **kwargs):
            raise RuntimeError("private model detail")

        self.ns["_run_project_reference_image_job"] = fail
        response = self._run(self._body())
        self.assertEqual(self._assets(), [])
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Reference-sheet generation failed")
        self.assertNotIn("private model detail", json.dumps(job))

    def test_cancellation_before_composition_never_attaches_a_variant(self):
        original = self._image_job

        def cancel_after_panel(parent_job, *args, **kwargs):
            path = original(parent_job, *args, **kwargs)
            parent_job["cancel_requested"] = True
            return path

        self.ns["_run_project_reference_image_job"] = cancel_after_panel
        response = self._run(self._body())
        self.assertEqual(self._assets(), [])
        self.assertEqual(self.jobs[response["job_id"]]["status"], "cancelled")

    def test_retry_appends_lineage_and_never_changes_kept_parent(self):
        source = self.output / "kept.png"
        Image.new("RGB", (64, 64), "red").save(source)
        asset = self.store.create_asset(
            "project", "main",
            name="Existing", asset_type="character", description="source",
        )
        parent = self.store.add_variant(
            "project", "main", asset["id"],
            variant_id="kept_parent",
            variant_type="reference",
            label="Kept source",
            outputs=[source],
            status="kept",
        )
        response = self._run(self._body(
            asset_id=asset["id"],
            parent_variant_id=parent["id"],
            edit_instruction="Try a new coat",
            mode="draft",
        ))
        current = self.store.get_asset("project", "main", asset["id"])
        self.assertEqual(len(current["variants"]), 2)
        self.assertEqual(current["variants"][0]["status"], "kept")
        created = current["variants"][1]
        self.assertEqual(created["metadata"]["parent"], {
            "asset_id": asset["id"],
            "variant_id": "kept_parent",
        })
        self.assertEqual(created["id"], f"{response['job_id']}_sheet_1")

    def test_public_variant_metadata_is_prompt_free_path_free_and_policy_scoped(self):
        response = self._run(self._body(
            description="DO_NOT_PERSIST_IN_VARIANT_METADATA",
            mode="draft",
        ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["session_id"], "owner-session")
        self.assertEqual(job["access_policy"], {"private": True, "explicit": True})
        self.assertEqual(job["prompt_preview"], "")
        variant = self._assets()[0]["variants"][0]
        public = json.dumps({
            "metadata": variant["metadata"],
            "outputs": variant["outputs"],
            "provenance": variant["provenance"],
        })
        self.assertNotIn("DO_NOT_PERSIST_IN_VARIANT_METADATA", public)
        self.assertNotIn(str(self.root), public)
        self.assertEqual(variant["outputs"][0]["metadata"]["private"], True)
        self.assertEqual(variant["outputs"][0]["metadata"]["explicit"], True)
        self.assertEqual(
            variant["outputs"][0]["metadata"]["lineage"]["parent_job_id"],
            response["job_id"],
        )

    def test_body_model_and_output_policy_validate_before_any_asset_creation(self):
        invalid_cases = [
            self._body(candidate_count=0),
            self._body(model_type="video_only"),
            self._body(private_output="yes"),
            self._body(unexpected=True),
        ]
        for body in invalid_cases:
            with self.subTest(body=body):
                with self.assertRaises(HTTPException):
                    self._run(body)
                self.assertEqual(self._assets(), [])
                self.assertEqual(self.jobs, {})

    def test_project_asset_errors_are_fixed_and_local_import_alias_is_compatible(self):
        error = self.ns["_project_asset_error"](ValueError("secret filesystem detail"))
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.detail, "Invalid reference asset request")
        error = self.ns["_project_asset_error"](RuntimeError("private traceback"))
        self.assertEqual(error.status_code, 500)
        self.assertEqual(error.detail, "Reference asset operation failed")
        self.assertEqual(
            self.ns["_project_asset_provenance"]("local_import", default="typed"),
            "imported",
        )

    def test_real_child_job_is_distinct_owned_and_rewrites_prompt_sidecar(self):
        parent = {
            "id": "parent123",
            "status": "running",
            "workspace": "project",
            "out_dir": str(self.output),
            "session_id": "owner-session",
            "source_remote": True,
            "access_policy": {"private": True, "explicit": False},
        }
        captured = {}

        def register(child, **kwargs):
            captured["child"] = child
            captured["kwargs"] = kwargs
            output = self.output / "owned-child.png"
            Image.new("RGB", (64, 64), "blue").save(output)
            sidecar = output.with_suffix(".meta.json")
            sidecar.write_text(json.dumps({"prompt": "SECRET_PROMPT"}), encoding="utf-8")
            child.update({
                "status": "completed",
                "progress": 100,
                "output_files": [output.name],
                "access_policy": {"private": True, "explicit": False},
            })
            return _DoneThread()

        self.ns["_queue_recovery_register_and_publish"] = register
        path = self.real_image_job(
            parent,
            {
                "model_type": "flux2_klein_9b",
                "prompt": "SECRET_PROMPT",
                "resolution": "64x64",
            },
            role="identity_front",
            phase="Generating panel",
            step=1,
            total_steps=10,
        )
        self.assertEqual(path, str(self.output / "owned-child.png"))
        child = captured["child"]
        self.assertNotEqual(child["id"], parent["id"])
        self.assertEqual(child["session_id"], parent["session_id"])
        self.assertEqual(child["workspace"], parent["workspace"])
        self.assertEqual(child["out_dir"], parent["out_dir"])
        self.assertEqual(child["prompt_preview"], "")
        self.assertEqual(captured["kwargs"]["recovery_kind"], "studio_generation")
        sidecar = json.loads(
            (self.output / "owned-child.meta.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("SECRET_PROMPT", json.dumps(sidecar))
        self.assertEqual(sidecar["params"]["reference_sheet"]["role"], "identity_front")
        self.assertEqual(sidecar["reference_parent_job_id"], parent["id"])

    def test_real_reviewer_refuses_nonlocal_provider_and_reports_request_tps(self):
        from services import llm_service
        from services.reference_sheets import build_reference_sheet_plan, build_semantic_review_request

        image = self.output / "review.png"
        Image.new("RGB", (64, 64), "green").save(image)
        plan = build_reference_sheet_plan(
            asset_type="character",
            mode="draft",
            creative_request="bounded synthetic request",
            model="flux2_klein_9b",
            draft_size=(64, 64),
        )
        review_request = build_semantic_review_request(plan, image)
        parent = {
            "id": "review-parent",
            "status": "running",
            "workspace": "project",
        }
        with mock.patch.object(llm_service, "get_status", return_value={
            "provider": "remote", "loaded": True, "vision_available": True,
        }), mock.patch.object(llm_service, "vision_available", return_value=True), mock.patch.object(
            llm_service, "generate",
        ) as generate:
            with self.assertRaisesRegex(RuntimeError, "review_unavailable"):
                self.real_reviewer(parent, review_request)
            generate.assert_not_called()

        captured = {}

        def generate(**kwargs):
            captured.update(kwargs)
            kwargs["progress_callback"]({"live_tps": 12.25})
            return json.dumps(self._passing_review(review_request))

        with mock.patch.object(llm_service, "get_status", return_value={
            "provider": "local", "loaded": True, "vision_available": True,
        }), mock.patch.object(llm_service, "vision_available", return_value=True), mock.patch.object(
            llm_service, "generate", side_effect=generate,
        ):
            result = self.real_reviewer(parent, review_request)
        self.assertEqual(json.loads(result)["status"], "pass")
        self.assertEqual(captured["image_paths"], [str(image)])
        self.assertIsInstance(captured["json_schema"], dict)
        self.assertIn("12.2 tok/s", parent["message"])
        self.assertNotIn("text", parent)

    def test_real_reviewer_rechecks_provider_inside_atomic_model_lock(self):
        from services import llm_service
        from services.reference_sheets import build_reference_sheet_plan, build_semantic_review_request

        image = self.output / "review-race.png"
        Image.new("RGB", (64, 64), "blue").save(image)
        plan = build_reference_sheet_plan(
            asset_type="character",
            mode="draft",
            creative_request="bounded synthetic request",
            model="flux2_klein_9b",
            draft_size=(64, 64),
        )
        review_request = build_semantic_review_request(plan, image)
        parent = {"id": "review-race-parent", "status": "running"}
        state = {"provider": "local"}

        class ProviderSwapBoundary:
            def __enter__(self):
                state["provider"] = "remote"

            def __exit__(self, *_args):
                return False

        def status():
            return {
                "provider": state["provider"],
                "loaded": True,
                "vision_available": True,
            }

        with mock.patch.object(llm_service, "_lock", ProviderSwapBoundary()), mock.patch.object(
            llm_service, "get_status", side_effect=status,
        ), mock.patch.object(llm_service, "vision_available", return_value=True), mock.patch.object(
            llm_service, "generate",
        ) as generate:
            with self.assertRaisesRegex(RuntimeError, "review_unavailable"):
                self.real_reviewer(parent, review_request)
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
