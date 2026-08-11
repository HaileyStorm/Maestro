import ast
import asyncio
import copy
import glob
import hashlib
import hmac
import json
import math
import os
import re
import tempfile
import threading
import time
import types
import unittest
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from unittest import mock

from fastapi import HTTPException
from PIL import Image

from services import job_lifecycle as lifecycle
from services.output_access import (
    can_access_output,
    public_output_policy,
    stamp_sidecar_policy,
)
from services.project_assets import (
    ProjectAssetNotFoundError,
    ProjectAssetPersistenceError,
    ProjectAssetStore,
)


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "app" / "launch.py"


def _load_route_symbols(namespace):
    wanted = {
        "_project_asset_error",
        "_project_asset_provenance",
        "_can_access_project_asset_variant",
        "_public_authorized_project_assets",
        "_recovery_response_requires_no_store",
        "_project_reference_private_authored_snapshot",
        "_project_reference_text",
        "_project_reference_dimensions",
        "_project_reference_seal_intelligence_selection",
        "_project_reference_intelligence_selection",
        "_project_reference_model_schedule",
        "_project_reference_operation_routing",
        "_project_reference_private_commitment",
        "_project_reference_snapshot_commitment",
        "_project_reference_lora_schema_sidecars",
        "_project_reference_lora_schema_scopes",
        "_project_reference_lora_schema_roles",
        "_project_reference_lora_prompt_template",
        "_project_reference_lora_fragment",
        "_normalize_lora_parameter_schema",
        "_read_lora_parameter_schema",
        "_project_reference_known_lora_parameter_schema",
        "_project_reference_lora_parameter_schema",
        "_public_lora_parameter_schema",
        "_normalize_lora_parameter_values",
        "_project_reference_sha256_file",
        "_project_reference_resolve_additional_loras",
        "_project_reference_uncensored_review_setup",
        "_project_reference_explicit_generation_model",
        "_project_reference_capabilities",
        "_project_reference_request_config",
        "_project_reference_creative_request",
        "_project_reference_generation_params",
        "_write_project_reference_sidecar",
        "_cleanup_project_reference_private_source",
        "_project_reference_wait_at_output_boundary",
        "_project_reference_safe_failure_envelope",
        "_project_reference_child_failure_updates",
        "_run_project_reference_image_job",
        "_project_reference_runtime_intelligence_selection",
        "_project_reference_run_planning",
        "_project_reference_selected_reviewer",
        "_attach_project_reference_result",
        "_project_reference_publication_recovery_requested",
        "_project_reference_validate_committed_variant",
        "_recover_project_reference_publication",
        "_queue_recovery_worker",
        "_public_model_availability",
        "_public_manual_installation_manifest",
        "_compute_lora_id",
        "list_loras_details",
        "list_models",
        "list_project_assets",
        "get_project_reference_authoring",
        "get_project_reference_capabilities",
        "update_project_asset",
        "set_project_asset_variant_status",
        "generate_project_asset_references",
        "_job_model_term_ids",
        "_public_parent_job_id",
        "_public_failed_child_metadata",
        "_public_job_prompt_fields",
        "_public_job_created_at",
        "get_status",
        "list_jobs",
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
        "flux2_dev": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "flux2_klein_9b": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "qwen_image_edit_2511_20B_fp8_lightning_4step": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "qwen_image_edit_2511_20B_fp8_lightning_8step": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "verified_reference_generation": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "verified_reference_edit": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "verified_reference_repair": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "verified_reference_callout": {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        },
        "bad_editor": {"image_outputs": True},
        "video_only": {"image_outputs": False},
    }
    bases = {
        "flux2_dev": "flux2_dev",
        "flux2_klein_9b": "flux2_klein_9b",
        "qwen_image_edit_2511_20B_fp8_lightning_4step": "qwen_image_edit_plus2_20B",
        "qwen_image_edit_2511_20B_fp8_lightning_8step": "qwen_image_edit_plus2_20B",
        "verified_reference_generation": "flux2_klein_9b",
        "verified_reference_edit": "qwen_image_edit_plus2_20B",
        "verified_reference_repair": "qwen_image_edit_plus2_20B",
        "verified_reference_callout": "qwen_image_edit_plus2_20B",
        "bad_editor": "unknown",
        "video_only": "video",
    }
    displayed_model_types = tuple(definitions)
    models_def = definitions
    lora_dir = ""
    families_infos = {
        "flux": (1, "Flux"),
        "unknown": (99, "Unknown"),
    }

    @classmethod
    def get_model_def(cls, model):
        return cls.definitions.get(model)

    @classmethod
    def get_base_model_type(cls, model):
        return cls.bases.get(model)

    @classmethod
    def get_model_family(cls, model, *, for_ui=False):
        return "flux"

    @classmethod
    def get_default_settings(cls, model):
        return {
            "flux2_dev": {
                "sampling_steps": 30,
                "embedded_guidance_scale": 4,
            },
            "flux2_klein_9b": {
                "num_inference_steps": 4,
                "guidance_scale": 1,
            },
            "qwen_image_edit_2511_20B_fp8_lightning_4step": {
                "num_inference_steps": 4,
                "guidance_scale": 1,
            },
            "qwen_image_edit_2511_20B_fp8_lightning_8step": {
                "num_inference_steps": 8,
                "guidance_scale": 1,
            },
            "verified_reference_generation": {
                "num_inference_steps": 11,
                "guidance_scale": 1.1,
            },
            "verified_reference_edit": {
                "num_inference_steps": 12,
                "guidance_scale": 1.2,
            },
            "verified_reference_repair": {
                "num_inference_steps": 13,
                "guidance_scale": 1.3,
            },
            "verified_reference_callout": {
                "num_inference_steps": 14,
                "guidance_scale": 1.4,
            },
            "bad_editor": {"num_inference_steps": 12, "guidance_scale": 1},
            "video_only": {"num_inference_steps": 20, "guidance_scale": 1},
        }[model]

    @classmethod
    def get_lora_dir(cls, model):
        if not cls.lora_dir:
            raise RuntimeError("No synthetic LoRA directory")
        return cls.lora_dir

    @classmethod
    def get_lora_search_dirs(cls, model):
        return [cls.get_lora_dir(model)]

    @staticmethod
    def resolve_lora_path(model, filename):
        return ""

    @staticmethod
    def test_class_i2v(model):
        return False

    @staticmethod
    def test_class_t2v(model):
        return False


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
        _ModelRegistry.lora_dir = ""

        namespace = {
            "HTTPException": HTTPException,
            "Request": object,
            "Response": object,
            "Path": Path,
            "copy": copy,
            "glob": glob,
            "hashlib": hashlib,
            "hmac": hmac,
            "json": json,
            "math": math,
            "os": os,
            "re": re,
            "parse_qsl": parse_qsl,
            "urlsplit": urlsplit,
            "time": time,
            "types": types,
            "uuid": uuid,
            "wgp": _ModelRegistry,
            "public_output_policy": public_output_policy,
            "can_access_output": can_access_output,
            "stamp_sidecar_policy": stamp_sidecar_policy,
            "_jobs": self.jobs,
            "_gen_lock": object(),
            "_active_gen_states": {},
            "_request_remote": ContextVar("route_test_remote", default=False),
            "_session_secret": lambda: b"reference-route-test-secret",
            "_project_asset_store": lambda: self.store,
            "_asset_scope": self._asset_scope,
            "_require_project_access": lambda request, project: str(self.output),
            "_require_project_asset_media_access": self._require_asset,
            "_set_blender_candidate_status": lambda *args, **kwargs: None,
            "_existing_workspace_dir": lambda project: str(self.output),
            "_require_remote_visible_models": self._visible_models,
            "_require_model_recipe_terms": lambda _model_types: None,
            "_remote_visible_model_ids": lambda request: None,
            "_versioned_model_updater": types.SimpleNamespace(
                apply_recorded=lambda *args: None,
                apply_recorded_components=lambda *args: None,
            ),
            "_versioned_model_update_status": {},
            "_check_model_downloaded": lambda model: False,
            "_load_lora_manifest": lambda: {},
            "_build_lora_max_version_map": lambda _root: {},
            "_resolve_per_file_update_status": (
                lambda **_kwargs: {
                    "update_status": "local",
                    "latest_version_id": None,
                    "current_version_id": None,
                    "latest_published_at": None,
                    "latest_changelog": None,
                }
            ),
            "_http_output_policy_from_request": self._output_policy,
            "_begin_workspace_operation": lambda project: self.workspace_events.append(("begin", project)),
            "_end_workspace_operation": lambda project: self.workspace_events.append(("end", project)),
            "_queue_recovery_register_and_publish": self._register,
            "generation_slot": lambda _lock, _job: nullcontext(True),
            "try_start": self._try_start,
            "block_resource_admission_failure": (
                lifecycle.block_resource_admission_failure
            ),
            "checkpoint_recovery_job": (
                lambda job, **updates: (job.update(updates) or True)
            ),
            "update_job": self._update_job,
            "finish_job": self._finish_job,
            "snapshot_job": lambda job: dict(job),
            "request_cancel": lambda *args, **kwargs: None,
            "set_job_hold": lifecycle.set_job_hold,
            "is_cancel_requested": lambda job: bool(job.get("cancel_requested")),
        }
        self.ns = _load_route_symbols(namespace)
        self.real_uncensored_review_setup = self.ns[
            "_project_reference_uncensored_review_setup"
        ]
        self.real_explicit_generation_model = self.ns[
            "_project_reference_explicit_generation_model"
        ]
        self.ns["_project_reference_uncensored_review_setup"] = lambda: {
            "requested_model": "auto_local",
            "resolved_model": self.ns[
                "_PROJECT_REFERENCE_ABLITERATED_RECIPE"
            ]["model_id"],
            "resolved_provider": "local",
            "vision_required": True,
            "required_projector": self.ns[
                "_PROJECT_REFERENCE_ABLITERATED_RECIPE"
            ]["projector"],
            "installed": True,
            "projector_available": True,
            "vision_capable": True,
            "resident": False,
            "vision_available": None,
            "loading": False,
            "loading_phase": None,
            "setup_state": "ready_unloaded",
            "queue_ready": True,
        }
        self.real_intelligence_selection = self.ns[
            "_project_reference_intelligence_selection"
        ]
        self.ns["_project_reference_intelligence_selection"] = (
            self._intelligence_selection
        )
        self.real_image_job = self.ns["_run_project_reference_image_job"]
        self.ns["_run_project_reference_image_job"] = self._image_job
        self.real_selected_reviewer = self.ns[
            "_project_reference_selected_reviewer"
        ]
        self.ns["_project_reference_selected_reviewer"] = (
            lambda request, job, review_request, config: self.review(review_request)
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _passing_review(request):
        required_checks = request.response_schema["properties"]["checks"]["required"]
        return {
            "status": "pass",
            "checks": {name: True for name in required_checks},
            "failed_roles": [],
            "reason_codes": [],
        }

    @staticmethod
    def _intelligence_selection(
        request, *, requested_model, requested_provider, purpose, intent,
    ):
        if purpose == "planning":
            return {
                "requested_model": requested_model,
                "resolved_model": "deterministic",
                "resolved_provider": "local",
            }
        if requested_model == "off":
            return {
                "requested_model": "off",
                "resolved_model": None,
                "resolved_provider": "off",
            }
        return {
            "requested_model": requested_model,
            "resolved_model": "test-vlm",
            "resolved_provider": "local",
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
        updates.pop("generation_lock", None)
        updates.pop("poll_interval", None)
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

    def _image_job(
        self, parent_job, params, *, role, phase, step, total_steps,
        artifact_metadata=None,
    ):
        call = {
            "role": role,
            "model": params["model_type"],
            "reference": list(params.get("image_refs") or []),
            "activated_loras": list(params.get("activated_loras") or []),
            "loras_multipliers": params.get("loras_multipliers") or "",
            "phase": phase,
            "prompt": params["prompt"],
        }
        self.calls.append(call)
        width, height = [int(value) for value in params["resolution"].split("x")]
        safe_role = re.sub(r"[^A-Za-z0-9._-]+", "_", role)
        path = self.output / f"synthetic_{len(self.calls):03d}_{safe_role}.png"
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
            "explicit_output": False,
        }
        body.update(updates)
        return body

    def _explicit_body(self, **updates):
        body = self._body(
            preset="anatomy",
            anchor_basis="anatomy",
            type_fields={"poses": [{
                "id": "anatomy:nude-anatomy",
                "label": "nude anatomy",
                "custom": False,
                "group": "anatomy",
            }]},
            explicit_output=True,
        )
        body.update(updates)
        return body

    def _known_lora(self, constant_name):
        contract = self.ns[constant_name]
        lora = self.root / contract["filename"]
        lora.write_bytes(b"synthetic-known-breast-size-lora")
        lora.with_suffix(".civitai.json").write_text(json.dumps({
            "modelId": contract["model_id"],
            "versionId": contract["version_id"],
            "baseModel": contract["base_model"],
            "trainedWords": list(contract["trained_words"]),
        }))
        real_getsize = os.path.getsize

        def contract_getsize(path):
            if os.path.realpath(path) == os.path.realpath(lora):
                return contract["size_bytes"]
            return real_getsize(path)

        return contract, lora, contract_getsize

    def _install_verified_operation_routes(self):
        editor = "qwen_image_edit_2511_20B_fp8_lightning_8step"
        resolved = {
            "generation": "verified_reference_generation",
            "edit": "verified_reference_edit",
            "repair": "verified_reference_repair",
            "callout": "verified_reference_callout",
        }
        self.ns["_PROJECT_REFERENCE_VERIFIED_OPERATION_RECIPES"] = {
            operation: {
                requested: {
                    "operation": operation,
                    "base_model": requested,
                    "model_type": resolved[operation],
                    "recipe_id": f"verified-{operation}-v1",
                    "verification_status": "verified",
                },
            }
            for operation, requested in {
                "generation": "flux2_klein_9b",
                "edit": editor,
                "repair": editor,
                "callout": editor,
            }.items()
        }
        return editor, resolved

    def _run(self, body):
        return asyncio.run(
            self.ns["generate_project_asset_references"](
                "project", _Request(body),
            )
        )

    def _assets(self):
        return self.store.list_assets("project", "main")

    def _attachment_result(self, artifact_order):
        from services.reference_sheets import reference_pack_authored_settings_seal

        roles = ("canonical_identity", "turnaround")
        labels = ("CANONICAL IDENTITY", "TURNAROUND")
        private_authored = {"type_fields": {}, "detail_callouts": []}
        plan = types.SimpleNamespace(
            sheet_roles=roles,
            output_roles=roles,
            sheets=tuple(types.SimpleNamespace(label=label) for label in labels),
            generation_model="flux2_klein_9b",
            private_output=True,
            anchor_privacy="private_blurred",
            initial_blur=True,
            planner_version="reference-pack-v2",
            plan_seal="sealed-plan",
            private_authored_settings=lambda: copy.deepcopy(private_authored),
        )
        artifacts = tuple(
            types.SimpleNamespace(
                path=self.output / f"isolated-{role}-{index}.png",
                role=role,
                index=index,
                model="flux2_klein_9b",
                public_metadata=lambda role=role, index=index: {
                    "schema_version": 2,
                    "role": role,
                    "index": index,
                },
            )
            for role, index in artifact_order
        )
        return types.SimpleNamespace(
            plan=plan,
            artifacts=artifacts,
            max_repair_attempts=1,
            repair_attempts_used=0,
            public_metadata=lambda: {
                "schema_version": 2,
                "planner_version": "reference-pack-v2",
                "authored_settings": {
                    "seal": reference_pack_authored_settings_seal(
                        private_authored,
                    ),
                },
            },
        )

    def _attach_result(
        self, result, *, store, write_sidecar, store_factory=None,
    ):
        store_factory = store_factory or mock.Mock(return_value=store)
        parent_job = {
            "id": "isolated-reference-job",
            "workspace": "project",
            "status": "running",
            "access_policy": {
                "private": True,
                "explicit": False,
                "owner_session_id": "must-not-be-published",
                "internal_secret": "must-also-not-be-published",
            },
            "params": {"reference_pack": {}},
        }
        with mock.patch.dict(self.ns, {
            "_project_asset_store": store_factory,
            "_write_project_reference_sidecar": write_sidecar,
        }):
            return self.ns["_attach_project_reference_result"](
                asset_id="reference_asset",
                result=result,
                parent_job=parent_job,
                candidate_index=0,
                candidate_count=1,
                parent_variant_id=None,
            )

    def test_attach_result_rejects_reordered_or_gapped_artifacts_before_side_effects(self):
        invalid_orders = (
            (("turnaround", 1), ("canonical_identity", 0)),
            (("canonical_identity", 0), ("turnaround", 2)),
        )
        for artifact_order in invalid_orders:
            with self.subTest(artifact_order=artifact_order):
                store = mock.Mock()
                store_factory = mock.Mock(return_value=store)
                write_sidecar = mock.Mock()
                with self.assertRaisesRegex(
                    RuntimeError, "^reference_pack_output_invalid$",
                ):
                    self._attach_result(
                        self._attachment_result(artifact_order),
                        store=store,
                        write_sidecar=write_sidecar,
                        store_factory=store_factory,
                    )
                store_factory.assert_not_called()
                self.assertEqual(store.mock_calls, [])
                write_sidecar.assert_not_called()

    def test_attach_result_preserves_ordered_sources_and_privacy_metadata(self):
        result = self._attachment_result((
            ("canonical_identity", 0),
            ("turnaround", 1),
        ))
        store = mock.Mock()
        store.get_asset.side_effect = ProjectAssetNotFoundError("missing")
        store.create_asset.side_effect = (
            lambda _project, _workspace, **kwargs: {
                "variants": kwargs["variants"],
            }
        )
        write_sidecar = mock.Mock(
            wraps=self.ns["_write_project_reference_sidecar"],
        )

        attached = self._attach_result(
            result, store=store, write_sidecar=write_sidecar,
        )

        self.assertEqual(
            [output["source_path"] for output in attached["outputs"]],
            [str(artifact.path) for artifact in result.artifacts],
        )
        self.assertEqual(
            [output["label"] for output in attached["outputs"]],
            ["CANONICAL IDENTITY", "TURNAROUND"],
        )
        for output in attached["outputs"]:
            metadata = output["metadata"]
            self.assertEqual(metadata["private"], True)
            self.assertEqual(metadata["explicit"], False)
            self.assertEqual(metadata["initial_blur"], True)
            self.assertNotIn("owner_session_id", metadata)
            self.assertEqual(
                metadata["reference_pack"]["private_output"], True,
            )
            self.assertEqual(
                metadata["reference_pack"]["anchor_privacy"],
                "private_blurred",
            )
            self.assertEqual(
                metadata["reference_pack"]["initial_blur"], True,
            )
        self.assertEqual(write_sidecar.call_count, 2)
        for sidecar_call, artifact in zip(
            write_sidecar.call_args_list, result.artifacts,
        ):
            self.assertEqual(sidecar_call.args, (str(artifact.path),))
            self.assertEqual(set(sidecar_call.kwargs), {
                "parent_job", "model_type", "artifact_metadata",
            })
            self.assertEqual(
                sidecar_call.kwargs["parent_job"]["access_policy"],
                {
                    "private": True,
                    "explicit": False,
                    "owner_session_id": "must-not-be-published",
                    "internal_secret": "must-also-not-be-published",
                },
            )
            self.assertEqual(
                sidecar_call.kwargs["model_type"], artifact.model,
            )
            artifact_metadata = sidecar_call.kwargs["artifact_metadata"]
            self.assertEqual(artifact_metadata["private_output"], True)
            self.assertEqual(
                artifact_metadata["anchor_privacy"], "private_blurred",
            )
            self.assertEqual(artifact_metadata["initial_blur"], True)

            sidecar_path = artifact.path.with_suffix(".meta.json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["private"], True)
            self.assertEqual(sidecar["explicit"], False)
            self.assertEqual(sidecar["workspace"], "project")
            self.assertNotIn("owner_session_id", sidecar)
            self.assertNotIn("internal_secret", sidecar)
            self.assertEqual(
                sidecar["params"]["reference_pack"]["private_output"], True,
            )
            self.assertEqual(
                sidecar["params"]["reference_pack"]["anchor_privacy"],
                "private_blurred",
            )
            self.assertEqual(
                sidecar["params"]["reference_pack"]["initial_blur"], True,
            )
            serialized_sidecar = json.dumps(sidecar)
            self.assertNotIn("must-not-be-published", serialized_sidecar)
            self.assertNotIn("must-also-not-be-published", serialized_sidecar)

    def test_production_is_one_anchor_then_reference_guided_ordered_pack(self):
        response = self._run(self._body())
        self.assertTrue(response["asset"]["pending"])
        self.assertEqual(
            [call["role"] for call in self.calls],
            ["canonical_identity", "turnaround", "expressions"],
        )
        self.assertEqual(self.calls[0]["reference"], [])
        anchor = str(self.output / "synthetic_001_canonical_identity.png")
        self.assertTrue(all(
            call["reference"] == [anchor]
            for call in self.calls[1:]
        ))
        asset = self._assets()[0]
        self.assertEqual(len(asset["variants"]), 1)
        outputs = asset["variants"][0]["outputs"]
        self.assertEqual(outputs[0]["label"], "CANONICAL IDENTITY")
        self.assertEqual(len(outputs), 3)
        self.assertEqual(asset["variants"][0]["variant_type"], "reference_pack")
        self.assertEqual(response["plan"]["planner_version"], "reference-pack-v2")
        self.assertEqual(response["plan"]["anchor_role"], "canonical_identity")
        self.assertEqual(self.jobs[response["job_id"]]["status"], "completed")

    def test_hybrid_generates_anchor_then_local_targeted_edits(self):
        self._run(self._body(
            mode="hybrid",
            editor_model_type="qwen_image_edit_2511_20B_fp8_lightning_4step",
        ))
        self.assertEqual(self.calls[0]["model"], "flux2_klein_9b")
        self.assertEqual(self.calls[0]["reference"], [])
        anchor = str(self.output / "synthetic_001_canonical_identity.png")
        for call in self.calls[1:]:
            self.assertEqual(
                call["model"],
                "qwen_image_edit_2511_20B_fp8_lightning_4step",
            )
            self.assertEqual(call["reference"], [anchor])
        self.assertEqual(len(self.calls), 3)

    def test_hybrid_capability_rejection_precedes_asset_or_job_creation(self):
        with self.assertRaises(HTTPException) as raised:
            self._run(self._body(mode="hybrid", editor_model_type="bad_editor"))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self._assets(), [])
        self.assertEqual(self.jobs, {})

    def test_draft_is_truthfully_unanchored_ordered_one_shot_pack(self):
        self._run(self._body(mode="draft"))
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(not call["reference"] for call in self.calls))
        outputs = self._assets()[0]["variants"][0]["outputs"]
        self.assertEqual(len(outputs), 3)
        self.assertTrue(all(
            output["metadata"]["reference_pack"]["provenance"]["strategy"]
            == "draft_one_shot"
            for output in outputs
        ))

    def test_unavailable_local_vlm_is_nonfatal_and_persisted_as_bounded_status(self):
        self.review = lambda request: (_ for _ in ()).throw(RuntimeError("offline secret"))
        self._run(self._body())
        metadata = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(metadata["review_status"], "review_unavailable")
        self.assertEqual(metadata["reason_codes"], ["review_unavailable"])
        self.assertNotIn("offline secret", json.dumps(metadata))

    def test_explicit_or_unrestricted_review_cannot_be_disabled_or_unresolved(self):
        for body in (
            self._body(
                content_capability="unrestricted_local",
                review=False,
                review_model="off",
            ),
            self._explicit_body(
                content_capability="standard",
                review=False,
                review_model="off",
            ),
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                self._run(body)
            self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.jobs, {})
        self.assertEqual(self._assets(), [])

        standard_off = self.ns["_project_reference_request_config"](
            self._body(
                content_capability="standard",
                explicit_output=False,
                review=False,
                review_model="off",
            ),
            _Request({}),
        )
        self.assertFalse(standard_off["mandatory_review"])
        self.assertIsNone(standard_off["review_selection"]["resolved_model"])

        self.ns["_project_reference_intelligence_selection"] = (
            lambda request, *, requested_model, requested_provider, purpose, intent: (
                {
                    "requested_model": requested_model,
                    "resolved_model": "deterministic",
                    "resolved_provider": "local",
                }
                if purpose == "planning"
                else {
                    "requested_model": "auto_local",
                    "resolved_model": None,
                    "resolved_provider": "local",
                }
            )
        )
        with self.assertRaises(HTTPException) as raised:
            self._run(self._body(content_capability="unrestricted_local"))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.jobs, {})

    def test_mandatory_review_unavailable_is_terminal_and_never_publishes(self):
        self.review = lambda _request: (_ for _ in ()).throw(
            RuntimeError("PRIVATE_PROVIDER_FAILURE"),
        )
        response = self._run(self._body(
            content_capability="unrestricted_local",
        ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Reference-pack fidelity quality review failed")
        self.assertEqual(self._assets(), [])
        failure = job["params"]["reference_pack"]["quality_failure"]
        self.assertEqual(failure, {
            "status": "review_unavailable",
            "failed_roles": [],
            "reason_codes": ["review_unavailable"],
            "review_contract": "explicit_unrestricted_fidelity_v1",
        })
        self.assertNotIn("PRIVATE_PROVIDER_FAILURE", json.dumps(job))

    def test_mandatory_review_without_stable_descriptor_path_never_invokes_vlm(self):
        from services import reference_sheets

        self.review = lambda _request: self.fail(
            "reviewer must not receive a mutable pathname fallback"
        )
        with mock.patch.object(
            reference_sheets, "_review_descriptor_path", return_value=None,
        ):
            response = self._run(self._body(
                content_capability="unrestricted_local",
            ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(
            job["error"], "Reference-pack fidelity quality review failed",
        )
        self.assertEqual(self._assets(), [])
        self.assertEqual(
            job["params"]["reference_pack"]["quality_failure"]["status"],
            "review_unavailable",
        )

    def test_mandatory_malformed_review_is_terminal_and_never_publishes(self):
        def malformed(request):
            result = self._passing_review(request)
            result["critique"] = "PRIVATE_FREE_FORM_REVIEW"
            return result

        self.review = malformed
        response = self._run(self._explicit_body())
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self._assets(), [])
        failure = job["params"]["reference_pack"]["quality_failure"]
        self.assertEqual(failure["status"], "review_unavailable")
        self.assertEqual(failure["reason_codes"], ["review_unavailable"])
        self.assertNotIn("PRIVATE_FREE_FORM_REVIEW", json.dumps(job))

    def test_later_mandatory_review_failure_never_publishes_prior_candidate(self):
        reviews = {"count": 0}

        def second_malformed(request):
            reviews["count"] += 1
            self.assertEqual(
                self._assets(), [],
                "mandatory candidates must remain private until all reviews pass",
            )
            result = self._passing_review(request)
            if reviews["count"] == 2:
                result["critique"] = "malformed second candidate"
            return result

        self.review = second_malformed
        response = self._run(self._body(
            content_capability="unrestricted_local",
            candidate_count=2,
        ))
        self.assertEqual(self.jobs[response["job_id"]]["status"], "failed")
        self.assertEqual(reviews["count"], 2)
        self.assertEqual(self._assets(), [])

    def test_mandatory_second_publication_failure_is_atomic_without_deletion(self):
        real_copy = self.store._copy_outputs
        publications = {"count": 0}

        def fail_second_copy(*args, **kwargs):
            publications["count"] += 1
            if publications["count"] == 2:
                raise OSError("PRIVATE_SECOND_PUBLICATION_FAILURE")
            return real_copy(*args, **kwargs)

        with mock.patch.object(
            self.store, "_copy_outputs", side_effect=fail_second_copy,
        ), mock.patch.object(
            self.store,
            "delete_asset",
            side_effect=OSError("rollback deletion unavailable"),
        ) as delete_asset:
            response = self._run(self._body(
                content_capability="unrestricted_local",
                candidate_count=2,
            ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(publications["count"], 2)
        delete_asset.assert_not_called()
        self.assertEqual(self._assets(), [])
        self.assertNotIn("PRIVATE_SECOND_PUBLICATION_FAILURE", json.dumps(job))

    def test_post_review_source_replacement_cannot_be_published(self):
        write_sidecar = self.ns["_write_project_reference_sidecar"]
        replaced = []

        def replace_after_review(path, **kwargs):
            write_sidecar(path, **kwargs)
            if replaced:
                return
            source = Path(path)
            replacement = source.with_name(f"replacement-{source.name}")
            Image.new("RGB", (64, 64), "black").save(replacement)
            os.replace(replacement, source)
            replaced.append(source.name)

        self.ns["_write_project_reference_sidecar"] = replace_after_review
        response = self._run(self._body(
            content_capability="unrestricted_local",
        ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(len(replaced), 1)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Reference-pack generation failed")
        self.assertEqual(self._assets(), [])
        self.assertNotIn("reviewed source", json.dumps(job))

    def test_mandatory_existing_asset_batch_failure_preserves_parent_exactly(self):
        parent_source = self.output / "kept-parent.png"
        Image.new("RGB", (64, 64), "purple").save(parent_source)
        asset = self.store.create_asset(
            "project", "main",
            name="Existing", asset_type="character", description="source",
            variants=[{
                "id": "kept_parent",
                "variant_type": "reference",
                "label": "Kept parent",
                "outputs": [parent_source],
                "status": "kept",
            }],
        )
        before = copy.deepcopy(asset)
        real_copy = self.store._copy_outputs
        publications = {"count": 0}

        def fail_second_copy(*args, **kwargs):
            publications["count"] += 1
            if publications["count"] == 2:
                raise OSError("PRIVATE_EXISTING_BATCH_FAILURE")
            return real_copy(*args, **kwargs)

        with mock.patch.object(
            self.store, "_copy_outputs", side_effect=fail_second_copy,
        ):
            response = self._run(self._body(
                asset_id=asset["id"],
                content_capability="unrestricted_local",
                candidate_count=2,
            ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(publications["count"], 2)
        self.assertEqual(
            self.store.get_asset("project", "main", asset["id"]), before,
        )
        self.assertNotIn("PRIVATE_EXISTING_BATCH_FAILURE", json.dumps(job))

    def test_stable_candidate_id_collision_is_not_treated_as_replay(self):
        source = self.output / "collision.png"
        Image.new("RGB", (64, 64), "orange").save(source)
        asset = self.store.create_asset(
            "project", "main",
            name="Existing", asset_type="character", description="source",
            variants=[{
                "id": "fixedjob_pack_1",
                "variant_type": "reference_pack",
                "label": "Unrelated collision",
                "outputs": [source],
                "metadata": {"job": {"id": "another_job"}},
            }],
        )
        before = copy.deepcopy(asset)
        stable_uuid = types.SimpleNamespace(hex="fixedjob" + "0" * 24)
        with mock.patch.object(uuid, "uuid4", return_value=stable_uuid):
            response = self._run(self._body(
                asset_id=asset["id"],
                content_capability="unrestricted_local",
            ))
        self.assertEqual(self.jobs[response["job_id"]]["status"], "failed")
        self.assertEqual(self.calls, [])
        self.assertEqual(
            self.store.get_asset("project", "main", asset["id"]), before,
        )

    def test_partial_and_all_existing_replay_finalize_full_ordered_batch(self):
        asset = self.store.create_asset(
            "project", "main",
            name="Existing", asset_type="character", description="source",
        )
        self.ns["uuid"] = types.SimpleNamespace(
            uuid4=lambda: types.SimpleNamespace(hex="replay01000000000000000000000000")
        )
        body = self._body(
            asset_id=asset["id"],
            content_capability="unrestricted_local",
            candidate_count=2,
            mode="draft",
        )
        first = self._run(body)
        job_id = first["job_id"]
        self.assertEqual(job_id, "replay01")
        self.store.delete_variant(
            "project", "main", asset["id"], f"{job_id}_pack_2",
        )

        checkpoints = []

        def checkpoint(job, **updates):
            checkpoints.append(copy.deepcopy(updates))
            job.update(updates)
            return True

        self.ns["checkpoint_recovery_job"] = checkpoint
        self.calls.clear()
        partial = self._run(body)
        self.assertEqual(partial["job_id"], job_id)
        self.assertEqual(len(self.calls), 3)
        expected_ids = [f"{job_id}_pack_1", f"{job_id}_pack_2"]
        self.assertEqual(
            checkpoints[-1]["recovery_unit"]["variant_ids"], expected_ids,
        )
        partial_job = self.jobs[job_id]
        self.assertEqual(partial_job["status"], "completed")
        self.assertEqual(len(partial_job["output_files"]), 6)
        self.assertEqual(
            [item["id"] for item in self.store.get_asset(
                "project", "main", asset["id"],
            )["variants"]],
            expected_ids,
        )

        checkpoints.clear()
        self.calls.clear()
        replay = self._run(body)
        self.assertEqual(replay["job_id"], job_id)
        self.assertEqual(self.calls, [])
        replay_job = self.jobs[job_id]
        self.assertEqual(replay_job["status"], "completed")
        self.assertEqual(len(replay_job["output_files"]), 6)
        self.assertEqual(
            checkpoints[-1]["recovery_unit"]["variant_ids"], expected_ids,
        )

    def test_partial_replay_revalidates_existing_variant_before_atomic_append(self):
        asset = self.store.create_asset(
            "project", "main",
            name="Existing", asset_type="character", description="source",
        )
        self.ns["uuid"] = types.SimpleNamespace(
            uuid4=lambda: types.SimpleNamespace(hex="replay02000000000000000000000000")
        )
        body = self._body(
            asset_id=asset["id"],
            content_capability="unrestricted_local",
            candidate_count=2,
            mode="draft",
        )
        first = self._run(body)
        job_id = first["job_id"]
        first_id = f"{job_id}_pack_1"
        second_id = f"{job_id}_pack_2"
        self.store.delete_variant(
            "project", "main", asset["id"], second_id,
        )
        real_append = self.store.add_variants_atomic

        def delete_replay_before_append(*args, **kwargs):
            self.store.delete_variant(
                "project", "main", asset["id"], first_id,
            )
            return real_append(*args, **kwargs)

        self.calls.clear()
        with mock.patch.object(
            self.store,
            "add_variants_atomic",
            side_effect=delete_replay_before_append,
        ):
            replay = self._run(body)
        self.assertEqual(replay["job_id"], job_id)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.jobs[job_id]["status"], "failed")
        current = self.store.get_asset("project", "main", asset["id"])
        self.assertEqual(current["variants"], [])

    def test_partial_replay_missing_media_fails_before_atomic_append(self):
        asset = self.store.create_asset(
            "project", "main",
            name="Existing", asset_type="character", description="source",
        )
        self.ns["uuid"] = types.SimpleNamespace(
            uuid4=lambda: types.SimpleNamespace(hex="replay05000000000000000000000000")
        )
        body = self._body(
            asset_id=asset["id"],
            content_capability="unrestricted_local",
            candidate_count=2,
            mode="draft",
        )
        first = self._run(body)
        job_id = first["job_id"]
        first_id = f"{job_id}_pack_1"
        second_id = f"{job_id}_pack_2"
        self.store.delete_variant(
            "project", "main", asset["id"], second_id,
        )
        replayed = self.store.get_variant(
            "project", "main", asset["id"], first_id,
        )
        replayed_path = Path(self.store.resolve_output_path(
            "project", "main", replayed["outputs"][0]["relative_path"],
        ))
        real_append = self.store.add_variants_atomic

        def remove_media_before_append(*args, **kwargs):
            replayed_path.unlink()
            return real_append(*args, **kwargs)

        self.calls.clear()
        with mock.patch.object(
            self.store,
            "add_variants_atomic",
            side_effect=remove_media_before_append,
        ):
            replay = self._run(body)
        self.assertEqual(replay["job_id"], job_id)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.jobs[job_id]["status"], "failed")
        self.assertEqual(self.jobs[job_id]["output_files"], [])
        current = self.store.get_asset("project", "main", asset["id"])
        self.assertEqual([item["id"] for item in current["variants"]], [first_id])

    def test_live_replay_rejects_changed_review_status_and_missing_media(self):
        for index, corruption in enumerate(("review", "media"), start=3):
            with self.subTest(corruption=corruption):
                asset = self.store.create_asset(
                    "project", "main",
                    name=f"Replay {corruption}", asset_type="character",
                    description="source",
                )
                self.ns["uuid"] = types.SimpleNamespace(
                    uuid4=lambda index=index: types.SimpleNamespace(
                        hex=f"replay0{index}" + "0" * 24,
                    )
                )
                body = self._body(
                    asset_id=asset["id"],
                    content_capability="unrestricted_local",
                    mode="draft",
                )
                first = self._run(body)
                job_id = first["job_id"]
                variant = self.store.get_asset(
                    "project", "main", asset["id"],
                )["variants"][0]
                if corruption == "review":
                    with self.store._lock:
                        manifest = self.store._load_manifest("project")
                        stored_asset = self.store._find_asset(
                            manifest, "main", asset["id"],
                        )
                        stored = self.store._find_variant(
                            stored_asset, variant["id"],
                        )
                        stored["metadata"]["reference_pack"]["review_status"] = "fail"
                        self.store._write_manifest("project", manifest)
                else:
                    output = variant["outputs"][0]["relative_path"]
                    Path(self.store.resolve_output_path(
                        "project", "main", output,
                    )).unlink()
                self.calls.clear()
                replay = self._run(body)
                self.assertEqual(replay["job_id"], job_id)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.jobs[job_id]["status"], "failed")
                self.assertEqual(self.jobs[job_id]["output_files"], [])

    def test_mandatory_multi_success_cleans_stages_and_finishes_monotonically(self):
        progress = []

        def track_update(job, **updates):
            if "step" in updates:
                progress.append((updates["step"], updates["progress"]))
            return self._update_job(job, **updates)

        self.ns["update_job"] = track_update
        response = self._run(self._body(
            content_capability="unrestricted_local",
            candidate_count=2,
        ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["step"], job["total_steps"])
        self.assertEqual(job["progress"], 100)
        self.assertEqual([item[0] for item in progress], sorted(item[0] for item in progress))
        self.assertEqual([item[1] for item in progress], sorted(item[1] for item in progress))
        self.assertEqual(len(self._assets()[0]["variants"]), 2)
        self.assertEqual(list(self.output.glob("synthetic_*.png")), [])
        self.assertEqual(list(self.output.glob("synthetic_*.meta.json")), [])

    def test_mandatory_cancellation_during_batch_preparation_never_commits(self):
        sidecars = {"count": 0}

        def cancel_during_preparation(path, **kwargs):
            sidecars["count"] += 1
            if sidecars["count"] == 2:
                job = next(iter(self.jobs.values()))
                job["cancel_requested"] = True
                job["status"] = "cancelled"

        self.ns["_write_project_reference_sidecar"] = cancel_during_preparation
        with mock.patch.object(
            self.store, "create_asset", wraps=self.store.create_asset,
        ) as create_asset:
            response = self._run(self._body(
                content_capability="unrestricted_local",
                candidate_count=2,
            ))
        self.assertGreaterEqual(sidecars["count"], 2)
        create_asset.assert_not_called()
        self.assertEqual(self.jobs[response["job_id"]]["status"], "cancelled")
        self.assertEqual(self._assets(), [])

    def test_committed_mandatory_batch_parks_when_finalization_persistence_fails(self):
        def fail_finalization(*args, **kwargs):
            raise OSError("PRIVATE_FINALIZATION_PERSISTENCE_FAILURE")

        def park(job, **updates):
            job.update(updates)
            job["status"] = "queued"
            job["queue_held"] = True
            return True

        self.ns["finish_job"] = fail_finalization
        self.ns["block_generation_recovery"] = park
        with mock.patch.object(
            self.store,
            "delete_asset",
            side_effect=OSError("rollback deletion unavailable"),
        ) as delete_asset:
            response = self._run(self._body(
                content_capability="unrestricted_local",
                candidate_count=2,
            ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["queue_held"])
        self.assertEqual(
            job["recovery_state"],
            "publication_committed_finalization_pending",
        )
        self.assertEqual(len(self._assets()[0]["variants"]), 2)
        delete_asset.assert_not_called()
        self.assertNotIn(
            "PRIVATE_FINALIZATION_PERSISTENCE_FAILURE", json.dumps(job),
        )

    def test_post_completion_wrapper_failure_preserves_terminal_winner(self):
        def finish_then_raise(job, status, **updates):
            self._finish_job(job, status, **updates)
            raise OSError("PRIVATE_POST_COMPLETION_FAILURE")

        self.ns["finish_job"] = finish_then_raise
        response = self._run(self._body(
            content_capability="unrestricted_local",
            candidate_count=2,
        ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["step"], job["total_steps"])
        self.assertEqual(len(self._assets()[0]["variants"]), 2)
        self.assertNotIn("PRIVATE_POST_COMPLETION_FAILURE", json.dumps(job))

    def test_real_journal_restart_finalizes_committed_batch_without_generation(self):
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import (
            QueueRecoveryCoordinator,
            owner_principal_digest,
            project_instance_digest,
        )

        journal = QueueRecoveryJournal(self.root / "reference-recovery.jsonl")
        coordinator = QueueRecoveryCoordinator(journal)
        secret = b"synthetic-reference-recovery-secret"
        owner = owner_principal_digest(secret, "synthetic-owner")
        project = project_instance_digest(secret, "a" * 32)
        captured = {}
        fail_finalization = {"value": True}

        def durable(proposal):
            if fail_finalization["value"] and proposal.name in {
                "finish", "generation_recovery_blocked",
            }:
                raise OSError("synthetic finalization persistence failure")
            coordinator.prospective_transition(proposal)

        def register(job, *, worker=None, **kwargs):
            job["kind"] = kwargs.get(
                "recovery_kind", "studio_project_asset_preparation",
            )
            self.jobs[job["id"]] = job
            coordinator.register_job(
                job,
                owner_digest=owner,
                project_digest=project,
                request_manifest={"kind": "studio_project_asset_preparation"},
            )
            captured["worker"] = worker
            worker(job["id"])
            return _DoneThread()

        lifecycle._reset_queue_state_for_tests()
        lifecycle.configure_durability_hook(durable)
        try:
            with mock.patch.dict(self.ns, {
                "_queue_recovery_register_and_publish": register,
                "checkpoint_recovery_job": lifecycle.checkpoint_recovery_job,
                "finish_job": lifecycle.finish_job,
                "block_generation_recovery": lifecycle.block_generation_recovery,
            }):
                response = self._run(self._body(
                    content_capability="unrestricted_local",
                    candidate_count=2,
                ))
                job_id = response["job_id"]
                self.assertEqual(len(self._assets()[0]["variants"]), 2)
                generation_calls = len(self.calls)

                durable_pending = QueueRecoveryCoordinator(journal).restore().jobs[
                    job_id
                ]
                self.assertEqual(
                    durable_pending["recovery_state"], "publication_prepared",
                )
                self.assertEqual(
                    durable_pending["recovery_unit"]["kind"],
                    "project_reference_publication",
                )

                # Startup materialization restores queue authority; the worker
                # consumes only the durable unit plus committed asset manifest.
                recovered = dict(durable_pending)
                recovered.update({
                    "status": "queued",
                    "queue_held": False,
                    "workspace": "project",
                    "message": "Recovered publication finalization",
                })
                self.jobs[job_id] = recovered
                fail_finalization["value"] = False
                recovery_worker = self.ns["_queue_recovery_worker"](recovered)
                self.assertIs(
                    recovery_worker,
                    self.ns["_recover_project_reference_publication"],
                )
                recovery_worker(job_id)
                self.assertEqual(len(self.calls), generation_calls)
                self.assertEqual(recovered["status"], "completed")
                durable_terminal = QueueRecoveryCoordinator(journal).restore().jobs[
                    job_id
                ]
                self.assertEqual(durable_terminal["status"], "completed")
                self.assertEqual(durable_terminal["recovery_state"], "terminal")
        finally:
            lifecycle._reset_queue_state_for_tests()

    def test_standard_remote_review_also_requires_explicit_provider_disclosure(self):
        self.ns["_project_reference_intelligence_selection"] = (
            lambda request, *, requested_model, requested_provider, purpose, intent: {
                "requested_model": requested_model,
                "resolved_model": "deterministic" if purpose == "planning" else "remote-vlm",
                "resolved_provider": "local" if purpose == "planning" else "openai",
            }
        )
        validate = self.ns["_project_reference_request_config"]
        for review in (True, False):
            with self.subTest(review=review), self.assertRaises(HTTPException) as raised:
                validate(self._body(
                    review=review,
                    review_model="remote-vlm",
                ), _Request({}))
            self.assertEqual(raised.exception.status_code, 400)
        config = validate(self._body(
            review_model="remote-vlm",
            review_provider="openai",
        ), _Request({}))
        self.assertEqual(config["review_selection"]["resolved_provider"], "openai")

    def test_mandatory_remote_review_requires_explicit_provider_disclosure(self):
        def remote_review_selection(
            request, *, requested_model, requested_provider, purpose, intent,
        ):
            if purpose == "planning":
                return {
                    "requested_model": requested_model,
                    "resolved_model": "deterministic",
                    "resolved_provider": "local",
                }
            return {
                "requested_model": requested_model,
                "resolved_model": "remote-vlm",
                "resolved_provider": "openai",
            }

        self.ns["_project_reference_intelligence_selection"] = remote_review_selection
        validate = self.ns["_project_reference_request_config"]
        with self.assertRaises(HTTPException) as raised:
            validate(self._body(
                content_capability="unrestricted_local",
                review_model="remote-vlm",
            ), _Request({}))
        self.assertEqual(raised.exception.status_code, 400)
        config = validate(self._body(
            content_capability="unrestricted_local",
            review_model="remote-vlm",
            review_provider="openai",
        ), _Request({}))
        self.assertEqual(config["review_selection"]["resolved_provider"], "openai")
        self.assertTrue(config["mandatory_review"])

    def test_mandatory_review_repairs_then_blocks_final_register_mismatch(self):
        review_count = {"value": 0}

        def failing_review(request):
            review_count["value"] += 1
            checks = {
                name: name != "violent_register_fidelity"
                for name in request.response_schema["properties"]["checks"]["required"]
            }
            return {
                "status": "fail",
                "checks": checks,
                "failed_roles": ["turnaround"],
                "reason_codes": ["violent_register_mismatch"],
            }

        self.review = failing_review
        response = self._run(self._body(
            content_capability="unrestricted_local",
            max_repair_attempts=1,
        ))
        job = self.jobs[response["job_id"]]
        self.assertEqual(review_count["value"], 2)
        self.assertEqual(
            [call["role"] for call in self.calls].count("turnaround"), 2,
        )
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self._assets(), [])
        failure = job["params"]["reference_pack"]["quality_failure"]
        self.assertEqual(failure["reason_codes"], ["violent_register_mismatch"])
        self.assertEqual(failure["failed_roles"], ["turnaround"])

    def test_failed_detail_callout_uses_bounded_repair_route(self):
        reviews = 0

        def review(request):
            nonlocal reviews
            reviews += 1
            if reviews == 1:
                return {
                    "status": "fail",
                    "checks": {
                        name: name != "detail_register_fidelity"
                        for name in request.response_schema["properties"]["checks"]["required"]
                    },
                    "failed_roles": ["detail_callout:builtin:face"],
                    "reason_codes": ["detail_register_mismatch"],
                }
            return self._passing_review(request)

        self.review = review
        self._run(self._explicit_body(
            detail_callouts=[{"kind": "face", "operation": "reconstruct"}],
            max_repair_attempts=1,
        ))
        callout_calls = [
            call for call in self.calls
            if call["role"] == "detail_callout:builtin:face"
        ]
        self.assertEqual(len(callout_calls), 2)
        outputs = self._assets()[0]["variants"][0]["outputs"]
        repaired = next(
            output for output in outputs
            if output["metadata"]["reference_pack"]["role"]
            == "detail_callout:builtin:face"
        )
        self.assertEqual(
            repaired["metadata"]["reference_pack"]["provenance"]["strategy"],
            "detail_callout_repair",
        )

    def test_canonical_failure_regenerates_the_full_candidate_once(self):
        reviews = {"count": 0}
        progress = []

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
                    "failed_roles": [request.sheet_roles[0], request.sheet_roles[1]],
                    "reason_codes": ["identity_mismatch"],
                }
            return self._passing_review(request)

        self.review = review
        real_update = self.ns["update_job"]

        def track_update(job, **updates):
            if "step" in updates:
                progress.append((updates["step"], updates["progress"]))
            return real_update(job, **updates)

        self.ns["update_job"] = track_update
        self._run(self._body())
        roles = [call["role"] for call in self.calls]
        self.assertEqual(roles.count("canonical_identity"), 2)
        self.assertEqual(roles.count("turnaround"), 2)
        self.assertEqual(len(self.calls), 6)
        metadata = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(metadata["roles"]["repaired"], ["canonical_identity"])
        self.assertEqual([item[0] for item in progress], sorted(
            item[0] for item in progress
        ))
        self.assertEqual([item[1] for item in progress], sorted(
            item[1] for item in progress
        ))

    def test_max_repair_budget_drives_loop_progress_and_persisted_models(self):
        def review(request):
            return {
                "status": "fail",
                "checks": {
                    "identity": False,
                    "request": True,
                    "view": True,
                    "accessory": True,
                    "style": True,
                },
                "failed_roles": [request.sheet_roles[1]],
                "reason_codes": ["identity_mismatch"],
            }

        self.review = review
        response = self._run(self._body(max_repair_attempts=5))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["total_steps"], 26)
        self.assertEqual(job["step"], 26)
        self.assertEqual(job["progress"], 100)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(
            [call["role"] for call in self.calls].count("turnaround"),
            6,
        )
        request_metadata = job["params"]["reference_pack"]
        self.assertEqual(request_metadata["max_repair_attempts"], 5)
        self.assertEqual(request_metadata["repair_attempts_used"], 5)
        self.assertEqual(request_metadata["generation_model"], "flux2_klein_9b")
        metadata = self._assets()[0]["variants"][0]["metadata"]
        reference = metadata["reference_pack"]
        self.assertEqual(reference["max_repair_attempts"], 5)
        self.assertEqual(reference["repair_attempts_used"], 5)
        self.assertEqual(reference["generation_model"], "flux2_klein_9b")
        self.assertEqual(metadata["job"]["generation_model"], "flux2_klein_9b")

    def test_hybrid_persists_generation_and_editor_models(self):
        editor = "qwen_image_edit_2511_20B_fp8_lightning_4step"
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
                    "failed_roles": [request.sheet_roles[1]],
                    "reason_codes": ["identity_mismatch"],
                }
            return self._passing_review(request)

        self.review = review
        self._run(self._body(mode="hybrid", editor_model_type=editor))
        self.assertEqual(self.calls[-1]["role"], "turnaround")
        self.assertEqual(self.calls[-1]["model"], editor)
        self.assertTrue(self.calls[-1]["reference"])
        metadata = self._assets()[0]["variants"][0]["metadata"]
        reference = metadata["reference_pack"]
        self.assertEqual(reference["generation_model"], "flux2_klein_9b")
        self.assertEqual(reference["editor_model"], editor)
        self.assertEqual(metadata["job"]["generation_model"], "flux2_klein_9b")
        self.assertEqual(metadata["job"]["editor_model"], editor)
        outputs = self._assets()[0]["variants"][0]["outputs"]
        by_label = {output["metadata"]["reference_pack"]["role"]: output for output in outputs}
        self.assertEqual(
            by_label["canonical_identity"]["metadata"]["reference_pack"]["model"],
            "flux2_klein_9b",
        )
        for role in ("turnaround", "expressions"):
            self.assertEqual(
                by_label[role]["metadata"]["reference_pack"]["model"],
                editor,
            )
        self.assertEqual(list(self.output.glob("synthetic_*.meta.json")), [])
        self.assertEqual(list(self.output.glob("synthetic_*.png")), [])

    def test_candidate_count_creates_separate_idempotent_sheet_variants(self):
        response = self._run(self._body(candidate_count=2, mode="draft"))
        asset = self._assets()[0]
        self.assertEqual(
            [variant["id"] for variant in asset["variants"]],
            [
                f"{response['job_id']}_pack_1",
                f"{response['job_id']}_pack_2",
            ],
        )
        self.assertEqual(len(self.calls), 6)

    def test_multi_candidate_repair_counters_distinguish_per_candidate_and_total(self):
        reviews = {"count": 0}

        def review(request):
            reviews["count"] += 1
            if reviews["count"] % 2:
                return {
                    "status": "fail",
                    "checks": {
                        "identity": False,
                        "request": True,
                        "view": True,
                        "accessory": True,
                        "style": True,
                    },
                        "failed_roles": [request.sheet_roles[1]],
                    "reason_codes": ["identity_mismatch"],
                }
            return self._passing_review(request)

        self.review = review
        response = self._run(self._body(
            candidate_count=2, max_repair_attempts=2,
        ))
        job = self.jobs[response["job_id"]]
        request_metadata = job["params"]["reference_pack"]
        self.assertEqual(request_metadata["max_repair_attempts"], 2)
        self.assertEqual(request_metadata["max_repair_attempts_per_candidate"], 2)
        self.assertEqual(request_metadata["repair_attempts_requested_total"], 4)
        self.assertEqual(request_metadata["repair_attempts_used"], 2)
        self.assertEqual(request_metadata["repair_attempts_used_total"], 2)
        variants = self._assets()[0]["variants"]
        self.assertEqual(len(variants), 2)
        for variant in variants:
            reference = variant["metadata"]["reference_pack"]
            self.assertEqual(reference["max_repair_attempts"], 2)
            self.assertEqual(reference["repair_attempts_used"], 1)
        final_job_metadata = variants[-1]["metadata"]["job"]
        self.assertEqual(final_job_metadata["repair_attempts_requested_total"], 4)
        self.assertEqual(final_job_metadata["repair_attempts_used_total"], 2)

    def test_failure_before_first_complete_sheet_leaves_no_empty_card(self):
        def fail(*args, **kwargs):
            raise RuntimeError("private model detail")

        self.ns["_run_project_reference_image_job"] = fail
        response = self._run(self._body())
        self.assertEqual(self._assets(), [])
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Reference-pack generation failed")
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

    def test_cancellation_during_sidecar_never_publishes_variant(self):
        sidecar_calls = []

        def cancel_during_sidecar(path, *, parent_job, **kwargs):
            sidecar_calls.append(path)
            parent_job["cancel_requested"] = True
            parent_job["status"] = "cancelled"

        self.ns["_write_project_reference_sidecar"] = cancel_during_sidecar
        response = self._run(self._body())
        self.assertEqual(len(sidecar_calls), 1)
        self.assertEqual(self._assets(), [])
        self.assertEqual(self.jobs[response["job_id"]]["status"], "cancelled")

    def test_cancellation_after_atomic_completion_cannot_revoke_publication(self):
        cleanup_calls = []
        cancel_results = []

        def cancel_during_cleanup(path, root):
            cleanup_calls.append((path, root))
            job = next(iter(self.jobs.values()))
            cancel_results.append(lifecycle.request_cancel(job))

        self.ns["_cleanup_project_reference_private_source"] = cancel_during_cleanup
        response = self._run(self._body())
        self.assertTrue(cleanup_calls)
        self.assertTrue(all(not result.changed for result in cancel_results))
        self.assertEqual(self.jobs[response["job_id"]]["status"], "completed")
        self.assertEqual(len(self._assets()[0]["variants"]), 1)

    def test_cancellation_immediately_before_create_never_publishes_asset(self):
        def cancel_at_publication_checkpoint(job, **updates):
            job["cancel_requested"] = True
            job["status"] = "cancelled"
            return False

        self.ns["checkpoint_recovery_job"] = cancel_at_publication_checkpoint
        with mock.patch.object(
            self.store, "create_asset", wraps=self.store.create_asset,
        ) as create_asset, mock.patch.object(
            self.store,
            "delete_asset",
            side_effect=OSError("deletion must not be required"),
        ) as delete_asset:
            response = self._run(self._body())
        create_asset.assert_not_called()
        delete_asset.assert_not_called()
        self.assertEqual(self._assets(), [])
        self.assertEqual(self.jobs[response["job_id"]]["status"], "cancelled")

    def test_cancellation_immediately_before_add_never_publishes_variant(self):
        asset = self.store.create_asset(
            "project", "main", name="Existing", asset_type="character",
            description="source",
        )
        def cancel_at_publication_checkpoint(job, **updates):
            job["cancel_requested"] = True
            job["status"] = "cancelled"
            return False

        self.ns["checkpoint_recovery_job"] = cancel_at_publication_checkpoint
        with mock.patch.object(
            self.store,
            "add_variants_atomic",
            wraps=self.store.add_variants_atomic,
        ) as add_variants, mock.patch.object(
            self.store,
            "delete_variant",
            side_effect=OSError("deletion must not be required"),
        ) as delete_variant:
            response = self._run(self._body(asset_id=asset["id"]))
        add_variants.assert_not_called()
        delete_variant.assert_not_called()
        current = self.store.get_asset("project", "main", asset["id"])
        self.assertEqual(current["variants"], [])
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
        self.assertEqual(created["id"], f"{response['job_id']}_pack_1")

    def test_legacy_reference_sheet_parent_retry_does_not_require_v2_snapshot(self):
        source = self.output / "legacy-sheet.png"
        Image.new("RGB", (64, 64), "navy").save(source)
        asset = self.store.create_asset(
            "project", "main", name="Legacy", asset_type="character",
            description="source",
        )
        parent = self.store.add_variant(
            "project", "main", asset["id"],
            variant_id="legacy_sheet_parent",
            variant_type="reference_sheet",
            label="Legacy sheet",
            outputs=[source],
            status="kept",
        )
        response = self._run(self._body(
            asset_id=asset["id"],
            parent_variant_id=parent["id"],
            edit_instruction="Preserve the legacy sheet",
            mode="draft",
        ))
        current = self.store.get_asset("project", "main", asset["id"])
        self.assertEqual(len(current["variants"]), 2)
        self.assertEqual(current["variants"][0]["id"], "legacy_sheet_parent")
        self.assertEqual(current["variants"][1]["id"], f"{response['job_id']}_pack_1")

    def test_public_variant_metadata_is_prompt_free_path_free_and_policy_scoped(self):
        response = self._run(self._explicit_body(
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

    def test_max_repair_attempts_defaults_and_accepts_only_zero_through_five(self):
        validate = self.ns["_project_reference_request_config"]
        self.assertEqual(
            validate(self._body(), _Request({}))["max_repair_attempts"],
            1,
        )
        for value in (0, 5):
            with self.subTest(value=value):
                self.assertEqual(
                    validate(
                        self._body(max_repair_attempts=value),
                        _Request({}),
                    )["max_repair_attempts"],
                    value,
                )
        for value in (6, -1, True, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(HTTPException) as raised:
                    validate(
                        self._body(max_repair_attempts=value),
                        _Request({}),
                    )
                self.assertEqual(raised.exception.status_code, 400)

    def test_v2_capabilities_publish_exact_defaults_bounds_and_layout_off(self):
        response = self.ns["get_project_reference_capabilities"](
            "project", _Request({}),
        )
        self.assertEqual(response["schema_version"], 2)
        self.assertEqual(response["planner_version"], "reference-pack-v2")
        self.assertEqual(response["depths"]["compact"]["sheet_count"], 1)
        self.assertEqual(response["depths"]["custom"]["maximum"], 5)
        self.assertEqual(response["max_candidate_count"], 8)
        self.assertEqual(response["max_repair_attempts"], 5)
        self.assertEqual(response["default_models"], {
            "generation_model": "flux2_dev",
            "editor_model": "qwen_image_edit_2511_20B_fp8_lightning_8step",
        })
        self.assertEqual(response["managed_layout_assist"]["mode"], "off")
        self.assertEqual(response["managed_layout_assist"]["allowlisted"], [])
        self.assertEqual(response["review_policy"], {
            "mandatory_for_content_capabilities": ["unrestricted_local"],
            "mandatory_when_explicit_output": True,
            "off_allowed_for_content_capabilities": ["standard"],
            "mandatory_contract": "explicit_unrestricted_fidelity_v1",
        })
        no_store = self.ns["_recovery_response_requires_no_store"]
        self.assertTrue(no_store(
            "/api/v1/projects/project/assets/asset/variants/variant/"
            "reference-authoring",
        ))
        self.assertFalse(no_store(
            "/api/v1/projects/project/assets/asset/variants/variant/media/file.png",
        ))
        character = next(
            item for item in response["reference_types"]
            if item["id"] == "character"
        )
        identity = next(item for item in character["presets"] if item["id"] == "identity")
        self.assertEqual(identity["valid_source_roles"], identity["ordered_roles"])
        self.assertEqual(identity["detail_operations"], response["detail_operations"])
        self.assertTrue(character["supports_custom_details"])
        poses = next(item for item in character["type_fields"] if item["id"] == "poses")
        self.assertEqual(
            [group["id"] for group in poses["groups"]],
            ["views", "poses", "expressions", "anatomy"],
        )
        expression_options = next(
            group["options"] for group in poses["groups"]
            if group["id"] == "expressions"
        )
        self.assertIn(
            {"id": "expressions:joy", "label": "joy"},
            expression_options,
        )
        self.assertIn({"id": "face", "label": "Face"}, character["detail_kinds"])

    def test_v2_structured_authored_settings_validate_sources_and_migrate_legacy(self):
        validate = self.ns["_project_reference_request_config"]
        private_expression = "PRIVATE_CUSTOM_EXPRESSION"
        config = validate(self._body(
            type_fields={
                "poses": [
                    {
                        "id": "views:front", "label": "front",
                        "custom": False, "group": "views",
                    },
                    {
                        "id": "custom:0123456789abcdef",
                        "label": private_expression,
                        "custom": True, "group": "expressions",
                    },
                ],
            },
            detail_callouts=[{
                "custom_id": "custom:abcdef0123456789",
                "label": "PRIVATE_DETAIL_LABEL",
                "kind": "custom",
                "operation": "enhance",
                "source_role": "turnaround",
            }],
        ), _Request({}))
        self.assertEqual(config["type_fields"]["poses"][1]["label"], private_expression)
        self.assertEqual(config["detail_callouts"][0]["source_role"], "turnaround")
        legacy_text = "Views: front, profile; Expressions: calm, tense"
        legacy = validate(self._body(
            type_fields={"poses": legacy_text},
            detail_callouts=[{"kind": "face", "operation": "auto"}],
        ), _Request({}))
        self.assertEqual(legacy["type_fields"]["poses"], [{
            "id": "legacy:poses", "label": legacy_text,
            "custom": True, "group": "legacy",
        }])
        self.assertEqual(legacy["detail_callouts"], [{
            "custom_id": "builtin:face",
            "label": "Face",
            "kind": "face",
            "operation": "auto",
            "source_role": "canonical_identity",
        }])
        executed = self._run(self._body(
            depth="compact",
            type_fields={"poses": legacy_text},
        ))
        self.assertEqual(
            self.jobs[executed["job_id"]]["params"]["reference_pack"]
            ["private_authored_settings"]["type_fields"]["poses"][0]["label"],
            legacy_text,
        )
        forged = self._body(detail_callouts=[{
            "custom_id": "builtin:face", "label": "Face", "kind": "face",
            "operation": "enhance", "source_role": "identity_details",
        }])
        with self.assertRaises(HTTPException) as raised:
            validate(forged, _Request({}))
        self.assertEqual(raised.exception.status_code, 400)
        with self.assertRaises(HTTPException) as raised:
            validate(self._body(
                mode="draft",
                detail_callouts=[{"kind": "face", "operation": "auto"}],
            ), _Request({}))
        self.assertEqual(raised.exception.status_code, 400)

    def test_v2_authored_payload_container_types_fail_closed_even_when_falsy(self):
        validate = self.ns["_project_reference_request_config"]
        for field, malformed in (
            ("type_fields", []),
            ("type_fields", ""),
            ("type_fields", None),
            ("detail_callouts", {}),
            ("detail_callouts", False),
            ("detail_callouts", None),
        ):
            with self.subTest(field=field, malformed=malformed):
                with self.assertRaises(HTTPException) as raised:
                    validate(self._body(**{field: malformed}), _Request({}))
                self.assertEqual(raised.exception.status_code, 400)

    def test_v2_noncharacter_retry_infers_omitted_asset_type_before_defaults(self):
        first = self._run(self._body(
            name="Atrium",
            asset_type="location",
            preset="spatial",
            type_fields={"zones": "entry, gallery"},
        ))
        first_variant = self._assets()[0]["variants"][0]
        retry_body = self._body(
            asset_id=first["asset"]["id"],
            parent_variant_id=first_variant["id"],
            type_fields={"zones": "entry, gallery"},
        )
        retry_body.pop("asset_type")
        retry = self._run(retry_body)
        self.assertEqual(retry["plan"]["reference_type"], "location")
        self.assertEqual(retry["plan"]["preset"], "spatial")
        self.assertEqual(
            self.jobs[retry["job_id"]]["params"]["reference_pack"]
            ["private_authored_settings"]["type_fields"]["zones"][0]["label"],
            "entry, gallery",
        )

    def test_v2_multiple_callouts_publish_independent_ordered_outputs(self):
        response = self._run(self._body(detail_callouts=[
            {
                "custom_id": "builtin:face", "label": "Face", "kind": "face",
                "operation": "enhance", "source_role": "turnaround",
            },
            {
                "custom_id": "custom:abcdef0123456789",
                "label": "PRIVATE_SECOND_DETAIL", "kind": "custom",
                "operation": "reconstruct", "source_role": "expressions",
            },
        ]))
        self.assertEqual(response["plan"]["detail_callout_count"], 2)
        self.assertEqual(response["plan"]["ordered_output_roles"], [
            "canonical_identity", "turnaround", "expressions",
            "detail_callout:builtin:face",
            "detail_callout:custom:abcdef0123456789",
        ])
        self.assertEqual([call["role"] for call in self.calls[-2:]], [
            "detail_callout:builtin:face",
            "detail_callout:custom:abcdef0123456789",
        ])
        anchor = str(self.output / "synthetic_001_canonical_identity.png")
        turnaround = str(self.output / "synthetic_002_turnaround.png")
        expressions = str(self.output / "synthetic_003_expressions.png")
        self.assertEqual(self.calls[-2]["reference"], [turnaround, anchor])
        self.assertEqual(self.calls[-1]["reference"], [expressions, anchor])
        stored_variant = self._assets()[0]["variants"][0]
        variant = self.ns["_public_authorized_project_assets"](
            self._assets(), "test-session",
        )[0]["variants"][0]
        self.assertEqual(len(variant["outputs"]), 5)
        details = [
            output["metadata"]["reference_pack"]["detail"]
            for output in variant["outputs"][-2:]
        ]
        self.assertEqual(
            [item["custom_id"] for item in details],
            ["builtin:face", "custom:abcdef0123456789"],
        )
        public = json.dumps({"plan": response["plan"], "variant": variant})
        self.assertNotIn("PRIVATE_SECOND_DETAIL", public)
        self.assertEqual(
            stored_variant["metadata"]["private_authored_settings"]
            ["detail_callouts"][1]["label"],
            "PRIVATE_SECOND_DETAIL",
        )
        self.assertEqual(
            self.jobs[response["job_id"]]["params"]["reference_pack"]
            ["private_authored_settings"]["detail_callouts"][1]["label"],
            "PRIVATE_SECOND_DETAIL",
        )

    def test_v2_policy_stamped_sidecar_keeps_private_authored_settings_only(self):
        path = self.output / "private-authored-sidecar.png"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(path)
        private = {
            "type_fields": {"poses": [{
                "id": "custom:0123456789abcdef",
                "label": "PRIVATE_EXPRESSION_LABEL",
                "custom": True,
                "group": "expressions",
            }]},
            "detail_callouts": [{
                "custom_id": "custom:abcdef0123456789",
                "label": "PRIVATE_DETAIL_LABEL",
                "kind": "custom",
                "operation": "enhance",
                "source_role": "canonical_identity",
            }],
        }
        parent_job = {
            "id": "private-sidecar-job",
            "workspace": "project",
            "access_policy": {
                "private": True,
                "explicit": False,
                "owner_session_id": "NEVER_PERSIST_OWNER",
                "internal_secret": "NEVER_PERSIST_INTERNAL",
            },
            "params": {"reference_pack": {
                "private_authored_settings": private,
            }},
        }
        self.ns["_write_project_reference_sidecar"](
            str(path),
            parent_job=parent_job,
            model_type="flux2_klein_9b",
            artifact_metadata={
                "schema_version": 2,
                "role": "detail_callout:custom:abcdef0123456789",
            },
        )
        sidecar = json.loads(path.with_suffix(".meta.json").read_text())
        self.assertEqual(
            sidecar["params"]["reference_pack"]["private_authored_settings"],
            private,
        )
        serialized = json.dumps(sidecar)
        self.assertNotIn("NEVER_PERSIST_OWNER", serialized)
        self.assertNotIn("NEVER_PERSIST_INTERNAL", serialized)

    def test_v2_retry_reseals_exact_private_authored_settings_without_public_labels(self):
        type_fields = {"poses": [{
            "id": "custom:0123456789abcdef",
            "label": "PRIVATE_RETRY_EXPRESSION",
            "custom": True,
            "group": "expressions",
        }]}
        callouts = [{
            "custom_id": "custom:abcdef0123456789",
            "label": "PRIVATE_RETRY_DETAIL",
            "kind": "custom",
            "operation": "enhance",
            "source_role": "turnaround",
        }]
        first = self._run(self._body(
            type_fields=type_fields,
            detail_callouts=callouts,
        ))
        first_variant = self._assets()[0]["variants"][0]
        expected_private = {
            "type_fields": type_fields,
            "detail_callouts": callouts,
        }
        self.assertEqual(
            first_variant["metadata"]["private_authored_settings"],
            expected_private,
        )
        private_wire = self.ns["get_project_reference_authoring"](
            "project",
            first["asset"]["id"],
            first_variant["id"],
            _Request({}),
        )
        self.assertEqual(
            private_wire["authored_settings"],
            {
                "seal": first["plan"]["authored_settings"]["seal"],
                **expected_private,
            },
        )
        corrupted = copy.deepcopy(first_variant)
        corrupted["metadata"]["private_authored_settings"]["detail_callouts"][0][
            "label"
        ] = "CORRUPTED_PRIVATE_LABEL"
        with self.assertRaises(HTTPException) as corrupt_error:
            self.ns["_project_reference_private_authored_snapshot"](corrupted)
        self.assertEqual(corrupt_error.exception.status_code, 409)
        with self.assertRaises(HTTPException) as hidden:
            self.ns["get_project_reference_authoring"](
                "project",
                first["asset"]["id"],
                first_variant["id"],
                _Request({}, session="other-session"),
            )
        self.assertEqual(hidden.exception.status_code, 404)
        public_before_retry = json.dumps(
            self.ns["list_project_assets"]("project", _Request({})),
        )
        self.assertNotIn("private_authored_settings", public_before_retry)
        self.assertNotIn("PRIVATE_RETRY_EXPRESSION", public_before_retry)
        self.assertNotIn("PRIVATE_RETRY_DETAIL", public_before_retry)

        # A new store instance models a server reload: the retry body omits
        # both private fields and the backend restores them from the parent.
        self.store = ProjectAssetStore(
            self.root / "storage",
            allowed_source_roots=[self.root / "outputs"],
        )
        retry = self._run(self._body(
            asset_id=first["asset"]["id"],
            parent_variant_id=first_variant["id"],
        ))
        self.assertEqual(
            retry["plan"]["authored_settings"]["seal"],
            first["plan"]["authored_settings"]["seal"],
        )
        retry_private = self.jobs[retry["job_id"]]["params"]["reference_pack"][
            "private_authored_settings"
        ]
        self.assertEqual(retry_private, expected_private)
        public = json.dumps(
            self.ns["list_project_assets"]("project", _Request({})),
        )
        self.assertNotIn("private_authored_settings", public)
        self.assertNotIn("PRIVATE_RETRY_EXPRESSION", public)
        self.assertNotIn("PRIVATE_RETRY_DETAIL", public)

    def test_v2_reload_retry_fails_closed_when_private_snapshot_is_missing(self):
        first = self._run(self._body())
        asset = self._assets()[0]
        variant = asset["variants"][0]
        with self.store._lock:
            manifest = self.store._load_manifest("project")
            stored_asset = self.store._find_asset(
                manifest, "main", asset["id"],
            )
            stored_variant = self.store._find_variant(
                stored_asset, variant["id"],
            )
            stored_variant["metadata"].pop("private_authored_settings")
            self.store._write_manifest("project", manifest)
        before_jobs = set(self.jobs)
        with self.assertRaises(HTTPException) as unavailable:
            self.ns["get_project_reference_authoring"](
                "project", asset["id"], variant["id"], _Request({}),
            )
        self.assertEqual(unavailable.exception.status_code, 409)
        with self.assertRaises(HTTPException) as retry_error:
            self._run(self._body(
                asset_id=asset["id"], parent_variant_id=variant["id"],
            ))
        self.assertEqual(retry_error.exception.status_code, 409)
        self.assertEqual(set(self.jobs), before_jobs)
        self.assertEqual(len(self._assets()[0]["variants"]), 1)

    def test_v2_asset_mutation_responses_redact_private_authored_settings(self):
        private_label = "PRIVATE_MUTATION_RESPONSE_LABEL"
        created = self._run(self._body(detail_callouts=[{
            "custom_id": "custom:abcdef0123456789",
            "label": private_label,
            "kind": "custom",
            "operation": "enhance",
            "source_role": "turnaround",
        }]))
        asset = self._assets()[0]
        variant = asset["variants"][0]
        updated_asset = asyncio.run(self.ns["update_project_asset"](
            "project", asset["id"], _Request({"description": "Updated"}),
        ))
        updated_variant = asyncio.run(
            self.ns["set_project_asset_variant_status"](
                "project", asset["id"], variant["id"],
                _Request({"status": "kept"}),
            )
        )
        for response in (created["asset"], updated_asset, updated_variant):
            serialized = json.dumps(response)
            self.assertNotIn("private_authored_settings", serialized)
            self.assertNotIn(private_label, serialized)
        persisted = self.store.get_asset("project", "main", asset["id"])
        self.assertEqual(
            persisted["variants"][0]["metadata"]["private_authored_settings"]
            ["detail_callouts"][0]["label"],
            private_label,
        )

    def test_v2_type_depth_anchor_and_detail_discriminators_reject_cross_type_data(self):
        validate = self.ns["_project_reference_request_config"]
        config = validate(self._body(
            asset_type="setting",
            depth="custom",
            sheet_count=2,
            preset="lighting",
            type_fields={"lighting": "late afternoon"},
            detail_callouts=[{"kind": "fixture", "operation": "auto"}],
        ), _Request({}))
        self.assertEqual(config["asset_type"], "location")
        self.assertEqual(config["sheet_count"], 2)
        self.assertEqual(config["anchor_basis"], "least_occluded")
        invalid = [
            self._body(asset_type="location", type_fields={"poses": "standing"}),
            self._body(asset_type="prop", preset="anatomy"),
            self._body(depth="standard", sheet_count=2),
            self._body(intent="exact_spec", detail_callouts=[{
                "kind": "face", "operation": "reconstruct",
            }]),
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HTTPException):
                validate(body, _Request({}))

        anatomy = validate(self._body(
            preset="anatomy", private_output=None, explicit_output=False,
        ), _Request({}))
        self.assertEqual(anatomy["anchor_basis"], "anatomy")
        self.assertTrue(anatomy["policy"]["private"])
        for update in (
            {"preset": "identity"},
            {"anchor_basis": "primary_outfit"},
            {"type_fields": {}},
            {"type_fields": {"poses": [{
                "id": "custom:aaaaaaaaaaaaaaaa",
                "label": "nude anatomy",
                "custom": True,
                "group": "anatomy",
            }]}},
        ):
            with self.subTest(update=update), self.assertRaises(HTTPException) as raised:
                validate(self._explicit_body(**update), _Request({}))
            self.assertEqual(raised.exception.status_code, 400)

    def test_v2_privacy_and_initial_blur_seal_all_four_truthful_states(self):
        for private, blurred, expected in (
            (False, False, "project_visible"),
            (False, True, "project_blurred"),
            (True, False, "private_visible"),
            (True, True, "private_blurred"),
        ):
            with self.subTest(private=private, blurred=blurred):
                response = self._run(self._body(
                    private_output=private,
                    initial_blur=blurred,
                ))
                asset = self.store.get_asset(
                    "project", "main", response["asset"]["id"],
                )
                variant = asset["variants"][0]
                job_pack = self.jobs[response["job_id"]]["params"]["reference_pack"]
                variant_pack = variant["metadata"]["reference_pack"]
                for published in (response["plan"], job_pack, variant_pack):
                    self.assertEqual(published["private_output"], private)
                    self.assertEqual(published["initial_blur"], blurred)
                    self.assertEqual(published["anchor_privacy"], expected)
                self.assertTrue(all(
                    output["metadata"]["private"] is private
                    and output["metadata"]["initial_blur"] is blurred
                    and output["metadata"]["reference_pack"]["private_output"] is private
                    and output["metadata"]["reference_pack"]["anchor_privacy"] == expected
                    for output in variant["outputs"]
                ))

        anatomy_body = self._body(preset="anatomy")
        anatomy_body.pop("private_output")
        anatomy = self._run(anatomy_body)
        self.assertEqual(anatomy["plan"]["anchor_privacy"], "private_blurred")
        self.assertTrue(anatomy["plan"]["private_output"])
        self.assertTrue(anatomy["plan"]["initial_blur"])

    def test_v2_resolves_model_native_schedules_and_preserves_explicit_override(self):
        resolve = self.ns["_project_reference_model_schedule"]
        self.assertEqual(resolve({}, "flux2_dev"), {
            "model": "flux2_dev", "steps": 30, "guidance": 4.0,
            "guidance_key": "embedded_guidance_scale", "source": "model_default",
        })
        self.assertEqual(
            resolve({}, "flux2_klein_9b")["steps"], 4,
        )
        qwen = resolve({}, "qwen_image_edit_2511_20B_fp8_lightning_8step")
        self.assertEqual((qwen["steps"], qwen["guidance"]), (8, 1.0))
        explicit = resolve({
            "num_inference_steps": 17,
            "guidance_scale": 2.5,
        }, "flux2_dev")
        self.assertEqual((explicit["steps"], explicit["guidance"]), (17, 2.5))
        self.assertEqual(explicit["guidance_key"], "embedded_guidance_scale")
        self.assertEqual(explicit["source"], "explicit")

    def test_v2_reference_edits_preserve_user_loras_and_use_primary_plus_anchor(self):
        self._run(self._body(
            activated_loras=["user-style.safetensors"],
            loras_multipliers="1.25",
            detail_callouts=[{"kind": "garment", "operation": "enhance"}],
        ))
        self.assertGreaterEqual(len(self.calls), 3)
        for call in self.calls:
            # _image_job records only selected public fields; inspect the
            # captured params added below without persisting their paths.
            self.assertEqual(call["activated_loras"], ["user-style.safetensors"])
            self.assertEqual(call["loras_multipliers"], "1.25")
        self.assertTrue(self.calls[1]["reference"])
        summary = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(summary["user_loras"], {"count": 1, "preserved": True})

    def test_v2_retry_seal_and_public_metadata_are_content_free(self):
        response = self._run(self._body(
            description="PRIVATE_V2_PLAN_TEXT",
            detail_callouts=[{"kind": "face", "operation": "enhance"}],
        ))
        variant = self._assets()[0]["variants"][0]
        summary = variant["metadata"]["reference_pack"]
        self.assertEqual(summary["plan_seal"], response["plan"]["plan_seal"])
        self.assertEqual(summary["sheet_count"], 3)
        self.assertEqual(summary["generation_model"], "flux2_klein_9b")
        self.assertEqual(
            summary["editor_model"],
            "qwen_image_edit_2511_20B_fp8_lightning_8step",
        )
        public = json.dumps({"plan": response["plan"], "variant": variant})
        self.assertNotIn("PRIVATE_V2_PLAN_TEXT", public)
        self.assertNotIn(str(self.root), public)
        self.assertNotIn("prompt", public.casefold())

    def test_v2_content_neutral_requests_follow_the_same_authorized_path(self):
        response = self._run(self._body(
            description="adult anatomy, graphic battle damage, controversial symbols",
            preset="anatomy",
            anchor_basis="anatomy",
        ))
        self.assertEqual(self.jobs[response["job_id"]]["status"], "completed")
        self.assertEqual(len(self.calls), 3)
        summary = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(summary["anchor_basis"], "anatomy")
        self.assertEqual(summary["anchor_privacy"], "private_blurred")
        self.assertNotIn("adult anatomy", json.dumps(summary))

    def test_v2_unrestricted_routing_skips_unverified_experimental_recipes(self):
        self.ns["_PROJECT_REFERENCE_VERIFIED_OPERATION_RECIPES"] = {
            "generation": {
                "flux2_dev": {
                    "operation": "generation",
                    "base_model": "flux2_dev",
                    "model_type": "flux2_klein_9b",
                    "recipe_id": "experimental-civitai-candidate",
                    "verification_status": "experimental",
                },
            },
        }
        response = self._run(self._body(
            model_type="flux2_dev",
            content_capability="unrestricted_local",
        ))
        operations = response["plan"]["operation_routing"]["operations"]
        self.assertEqual(list(operations), [
            "generation", "edit", "repair", "callout",
        ])
        self.assertTrue(all(
            route["status"] == "skipped"
            and route["reason"] == "no_verified_compatible_recipe"
            and route["requested_model"] == route["resolved_model"]
            and "recipe_id" not in route
            for route in operations.values()
        ))
        self.assertEqual(self.calls[0]["model"], "flux2_dev")
        self.assertTrue(all(
            call["model"] == "qwen_image_edit_2511_20B_fp8_lightning_8step"
            for call in self.calls[1:]
        ))
        self.assertNotIn("civitai", json.dumps(response["plan"]).casefold())

    def test_v2_verified_routes_bind_every_operation_schedule_terms_and_metadata(self):
        editor, resolved = self._install_verified_operation_routes()
        term_calls = []
        self.ns["_require_model_recipe_terms"] = (
            lambda model_types: term_calls.append(tuple(model_types))
        )
        reviews = {"count": 0}

        def review(request):
            reviews["count"] += 1
            if reviews["count"] == 1:
                return {
                    "status": "fail",
                    "checks": {
                        "identity": True,
                        "request": False,
                        "view": True,
                        "accessory": True,
                        "style": True,
                        "overall_fidelity": False,
                        "mature_register_fidelity": True,
                        "violent_register_fidelity": True,
                        "detail_register_fidelity": True,
                    },
                    "failed_roles": ["turnaround"],
                    "reason_codes": [
                        "request_mismatch", "overall_fidelity_mismatch",
                    ],
                }
            return self._passing_review(request)

        self.review = review
        response = self._run(self._body(
            content_capability="unrestricted_local",
            depth="comprehensive",
            detail_callouts=[{"kind": "face", "operation": "enhance"}],
        ))
        operations = response["plan"]["operation_routing"]["operations"]
        expected_steps = {
            "generation": 11, "edit": 12, "repair": 13, "callout": 14,
        }
        expected_guidance = {
            "generation": 1.1, "edit": 1.2, "repair": 1.3, "callout": 1.4,
        }
        for operation, model in resolved.items():
            self.assertEqual(operations[operation], {
                "status": "applied",
                "requested_model": (
                    "flux2_klein_9b" if operation == "generation" else editor
                ),
                "resolved_model": model,
                "schedule": {
                    "model": model,
                    "steps": expected_steps[operation],
                    "guidance": expected_guidance[operation],
                    "guidance_key": "guidance_scale",
                    "source": "model_default",
                },
                "recipe_id": f"verified-{operation}-v1",
                "verification_status": "verified",
            })
        self.assertEqual(self.calls[0]["model"], resolved["generation"])
        self.assertEqual(self.calls[-1]["model"], resolved["repair"])
        by_role = {call["role"]: call for call in self.calls[:-1]}
        self.assertEqual(by_role["turnaround"]["model"], resolved["edit"])
        self.assertEqual(
            by_role["identity_details"]["model"], resolved["edit"],
        )
        self.assertEqual(
            by_role["detail_callout:builtin:face"]["model"],
            resolved["callout"],
        )
        job_pack = self.jobs[response["job_id"]]["params"]["reference_pack"]
        self.assertEqual(
            {model: job_pack["model_schedules"][model]["steps"] for model in resolved.values()},
            {
                resolved["generation"]: 11,
                resolved["edit"]: 12,
                resolved["repair"]: 13,
                resolved["callout"]: 14,
            },
        )
        self.assertEqual(set(term_calls[0]), {
            "flux2_klein_9b", editor, "test-vlm", *resolved.values(),
        })
        variant_pack = self.store.get_asset(
            "project", "main", response["asset"]["id"],
        )["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(
            variant_pack["operation_routing"], response["plan"]["operation_routing"],
        )
        self.assertEqual(variant_pack["plan_seal"], response["plan"]["plan_seal"])

    def test_v2_verified_routes_resolve_lora_scopes_against_every_resolved_model(self):
        _editor, resolved = self._install_verified_operation_routes()
        requested_path = self.root / "requested-only.safetensors"
        resolved_path = self.root / "resolved-compatible.safetensors"
        requested_path.write_bytes(b"requested-only")
        resolved_path.write_bytes(b"resolved-compatible")
        validate = self.ns["_project_reference_request_config"]
        generation_lora = [{
            "id": "route.safetensors", "multiplier": 1.0,
            "scope": "generation",
        }]

        with mock.patch.object(
            _ModelRegistry,
            "resolve_lora_path",
            side_effect=lambda model, _name: (
                str(requested_path) if model == "flux2_klein_9b" else ""
            ),
        ), self.assertRaises(HTTPException) as raised:
            validate(self._body(
                content_capability="unrestricted_local",
                additional_loras=generation_lora,
            ), _Request({}))
        self.assertEqual(raised.exception.status_code, 409)

        with mock.patch.object(
            _ModelRegistry,
            "resolve_lora_path",
            side_effect=lambda model, _name: (
                str(resolved_path)
                if model == resolved["generation"] else ""
            ),
        ):
            config = validate(self._body(
                content_capability="unrestricted_local",
                additional_loras=generation_lora,
            ), _Request({}))
        self.assertEqual(
            config["additional_loras"][0]["resolved_scopes"],
            ("generation",),
        )

        editing_lora = [{
            "id": "route.safetensors", "multiplier": 1.0,
            "scope": "editing",
        }]
        with mock.patch.object(
            _ModelRegistry,
            "resolve_lora_path",
            side_effect=lambda model, _name: (
                str(resolved_path) if model == resolved["edit"] else ""
            ),
        ), self.assertRaises(HTTPException):
            validate(self._body(
                content_capability="unrestricted_local",
                additional_loras=editing_lora,
            ), _Request({}))

        with mock.patch.object(
            _ModelRegistry,
            "resolve_lora_path",
            side_effect=lambda model, _name: (
                str(resolved_path) if model in {
                    resolved["edit"], resolved["repair"], resolved["callout"],
                } else ""
            ),
        ):
            config = validate(self._body(
                content_capability="unrestricted_local",
                additional_loras=editing_lora,
            ), _Request({}))
        self.assertEqual(
            config["additional_loras"][0]["resolved_scopes"],
            ("editing",),
        )

    def test_v2_job_recovery_params_seal_exact_models_schedules_and_intelligence(self):
        response = self._run(self._body(content_capability="unrestricted_local"))
        metadata = self.jobs[response["job_id"]]["params"]["reference_pack"]
        self.assertEqual(metadata["plan_seal"], response["plan"]["plan_seal"])
        self.assertEqual(
            {
                key: metadata["planning"][key]
                for key in (
                    "requested_model", "resolved_model", "resolved_provider",
                )
            },
            {
                "requested_model": "auto",
                "resolved_model": "deterministic",
                "resolved_provider": "local",
            },
        )
        self.assertNotIn("selection_revision", metadata["planning"])
        self.assertEqual(metadata["review"]["resolved_model"], "test-vlm")
        self.assertRegex(metadata["review"]["selection_revision"], r"^[0-9a-f]{64}$")
        schedules = metadata["model_schedules"]
        self.assertEqual(schedules["flux2_klein_9b"]["model"], "flux2_klein_9b")
        self.assertEqual(schedules["flux2_klein_9b"]["steps"], 4)
        self.assertEqual(
            schedules["qwen_image_edit_2511_20B_fp8_lightning_8step"]["model"],
            "qwen_image_edit_2511_20B_fp8_lightning_8step",
        )
        self.assertEqual(
            schedules["qwen_image_edit_2511_20B_fp8_lightning_8step"]["steps"],
            8,
        )
        scanned = self.ns["_job_model_term_ids"]({
            "model_type": "generation-root",
            "params": {"reference_pack": metadata},
        })
        self.assertIn("test-vlm", scanned)
        self.assertEqual(scanned.count("test-vlm"), 1)

    def test_v2_explicit_character_requires_canonical_nude_anatomy_state(self):
        validate = self.ns["_project_reference_request_config"]
        initialized = validate(self._explicit_body(), _Request({}))
        self.assertEqual(initialized["mode"], "production")
        self.assertEqual(initialized["preset"], "anatomy")
        self.assertEqual(initialized["anchor_basis"], "anatomy")
        self.assertIn({
            "id": "anatomy:nude-anatomy",
            "label": "nude anatomy",
            "custom": False,
            "group": "anatomy",
        }, initialized["type_fields"]["poses"])
        self.assertEqual(initialized["content_capability"], "unrestricted_local")
        self.assertTrue(initialized["initial_blur"])
        self.assertEqual(initialized["intelligence_policy"], "uncensored_auto")

        overridden = validate(self._explicit_body(
            content_capability="standard",
            initial_blur=False,
            intelligence_policy="standard_auto",
        ), _Request({}))
        self.assertEqual(overridden["anchor_basis"], "anatomy")
        self.assertEqual(overridden["content_capability"], "standard")
        self.assertFalse(overridden["initial_blur"])
        self.assertEqual(overridden["intelligence_policy"], "standard_auto")
        self.assertTrue(all(
            route["status"] == "standard" and "reason" not in route
            for route in overridden["operation_routing"]
        ))

        location = validate(self._body(
            asset_type="location", preset="spatial",
            explicit_output=True,
        ), _Request({}))
        self.assertEqual(location["anchor_basis"], "least_occluded")
        creature = validate(self._body(
            asset_type="creature", preset="identity",
            explicit_output=True,
        ), _Request({}))
        self.assertEqual(creature["anchor_basis"], "primary_outfit")

    def test_v2_uncensored_auto_uses_only_its_exact_local_visual_reviewer(self):
        recipe = self.ns["_PROJECT_REFERENCE_ABLITERATED_RECIPE"]
        capabilities = self.ns["_project_reference_capabilities"]()
        self.assertEqual(
            capabilities["uncensored_auto_review"],
            self.ns["_project_reference_uncensored_review_setup"](),
        )
        self.assertEqual(
            capabilities["explicit_generation_model"]["resolved_model"],
            "flux2_dev",
        )
        calls = []

        def selection(
            request, *, requested_model, requested_provider, purpose, intent,
        ):
            calls.append((purpose, requested_model, requested_provider))
            if purpose == "review":
                self.fail("uncensored_auto must not select a generic reviewer")
            return {
                "requested_model": requested_model,
                "resolved_model": "deterministic",
                "resolved_provider": "local",
            }

        self.ns["_project_reference_intelligence_selection"] = selection
        config = self.ns["_project_reference_request_config"](
            self._explicit_body(), _Request({}),
        )
        self.assertEqual(calls, [("planning", "auto", None)])
        self.assertEqual(config["review_selection"]["requested_model"], "auto_local")
        self.assertEqual(
            config["review_selection"]["resolved_model"], recipe["model_id"],
        )
        self.assertEqual(config["review_selection"]["resolved_provider"], "local")
        self.assertRegex(
            config["review_selection"]["selection_revision"], r"^[0-9a-f]{64}$",
        )

    def test_v2_uncensored_auto_rejects_remote_or_different_reviewer(self):
        recipe = self.ns["_PROJECT_REFERENCE_ABLITERATED_RECIPE"]
        validate = self.ns["_project_reference_request_config"]
        invalid = (
            {"review_provider": "openai"},
            {"review_model": "generic-local-vlm"},
            {
                "review_model": recipe["model_id"],
                "review_provider": "openai",
            },
        )
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(HTTPException):
                validate(self._explicit_body(**update), _Request({}))

    def test_v2_uncensored_review_setup_is_queue_ready_without_residency(self):
        from services import llm_service

        recipe = self.ns["_PROJECT_REFERENCE_ABLITERATED_RECIPE"]
        catalog = [{
            "id": recipe["model_id"],
            "downloaded": True,
            "projector_available": True,
            "vision_capable": True,
        }]
        with mock.patch.object(
            llm_service, "get_available_models", return_value=catalog,
        ), mock.patch.object(llm_service, "get_status", return_value={
            "loaded": False,
            "provider": "local",
            "loading": False,
        }):
            setup = self.real_uncensored_review_setup()
        self.assertEqual(setup["setup_state"], "ready_unloaded")
        self.assertTrue(setup["queue_ready"])
        self.assertFalse(setup["resident"])
        self.assertIsNone(setup["vision_available"])
        self.assertEqual(setup["required_projector"], recipe["projector"])

    def test_v2_uncensored_review_setup_reports_projector_and_runtime_failures(self):
        from services import llm_service

        recipe = self.ns["_PROJECT_REFERENCE_ABLITERATED_RECIPE"]
        base = {
            "id": recipe["model_id"],
            "downloaded": True,
            "projector_available": False,
            "vision_capable": True,
        }
        cases = (
            (
                base,
                {"loaded": False, "provider": "local", "loading": False},
                "missing_projector",
            ),
            (
                {
                    **base,
                    "projector_available": True,
                    "vision_capable": False,
                },
                {"loaded": False, "provider": "local", "loading": False},
                "missing_projector",
            ),
            (
                {**base, "projector_available": True},
                {
                    "loaded": True,
                    "model_id": recipe["model_id"],
                    "provider": "local",
                    "vision_available": False,
                    "loading": False,
                },
                "loaded_without_vision",
            ),
        )
        for catalog_model, status, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(
                llm_service, "get_available_models", return_value=[catalog_model],
            ), mock.patch.object(llm_service, "get_status", return_value=status):
                setup = self.real_uncensored_review_setup()
            self.assertEqual(setup["setup_state"], expected)
            self.assertFalse(setup["queue_ready"])

    def test_v2_uncensored_auto_fails_closed_when_exact_setup_is_not_ready(self):
        self.ns["_project_reference_uncensored_review_setup"] = lambda: {
            "setup_state": "missing_projector",
            "queue_ready": False,
        }
        with self.assertRaises(HTTPException) as raised:
            self.ns["_project_reference_request_config"](
                self._explicit_body(), _Request({}),
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("missing_projector", raised.exception.detail)

    def test_v2_explicit_generation_preference_does_not_require_support_downloads(self):
        model_terms = __import__("services.model_terms", fromlist=["unused"])
        previous_downloaded = self.ns["_check_model_downloaded"]
        self.addCleanup(
            lambda: self.ns.__setitem__("_check_model_downloaded", previous_downloaded),
        )
        self.ns["_check_model_downloaded"] = lambda _model: False
        self.ns["_model_visibility_response"] = lambda: {
            "configured": True,
            "enabled_models": list(
                self.ns["_PROJECT_REFERENCE_EXPLICIT_CREATE_MODELS"]
            ),
        }
        with mock.patch.object(
            model_terms, "model_terms_manifest_valid", return_value=True,
        ), mock.patch.object(
            model_terms,
            "model_terms_statuses",
            return_value=[{"accepted": True}],
        ), mock.patch.object(
            _ModelRegistry,
            "manual_checkpoint_integrity_ready",
            return_value=True,
            create=True,
        ), mock.patch.object(
            _ModelRegistry,
            "get_model_def",
            return_value={"image_outputs": True},
        ):
            preferred = self.real_explicit_generation_model()
        self.assertEqual(
            preferred["resolved_model"], "krea2_moody_mix_v7_fp8",
        )
        self.assertEqual(
            preferred["selection_source"], "verified_manual_preference",
        )
        self.assertTrue(preferred["candidates"][0]["ready"])
        self.assertFalse(preferred["candidates"][0]["downloaded"])

    def test_v2_explicit_omission_uses_scoped_preference_but_standard_and_draft_do_not(self):
        self.ns["_project_reference_explicit_generation_model"] = lambda: {
            "resolved_model": "krea2_moody_mix_v7_fp8",
        }
        previous_definitions = dict(_ModelRegistry.definitions)
        previous_bases = dict(_ModelRegistry.bases)

        def restore_registry():
            _ModelRegistry.definitions.clear()
            _ModelRegistry.definitions.update(previous_definitions)
            _ModelRegistry.bases.clear()
            _ModelRegistry.bases.update(previous_bases)

        self.addCleanup(restore_registry)
        _ModelRegistry.definitions["krea2_moody_mix_v7_fp8"] = {
            "image_outputs": True,
            "image_ref_choices": {"choices": [("Reference", "KI")]},
        }
        _ModelRegistry.bases["krea2_moody_mix_v7_fp8"] = "krea2_raw"
        original_defaults = _ModelRegistry.get_default_settings

        def defaults(model):
            if model == "krea2_moody_mix_v7_fp8":
                return {"num_inference_steps": 52, "guidance_scale": 3.5}
            return original_defaults(model)

        with mock.patch.object(
            _ModelRegistry, "get_default_settings", side_effect=defaults,
        ):
            explicit = self.ns["_project_reference_request_config"](
                self._explicit_body(model_type=None), _Request({}),
            )
            hybrid = self.ns["_project_reference_request_config"](
                self._explicit_body(
                    mode="hybrid", model_type=None,
                ),
                _Request({}),
            )
            standard = self.ns["_project_reference_request_config"](
                self._body(model_type=None), _Request({}),
            )
            draft = self.ns["_project_reference_request_config"](
                self._body(mode="draft", model_type=None), _Request({}),
            )
            supplied = self.ns["_project_reference_request_config"](
                self._explicit_body(model_type="flux2_dev"),
                _Request({}),
            )
            hybrid_supplied = self.ns["_project_reference_request_config"](
                self._explicit_body(
                    mode="hybrid",
                    model_type="flux2_dev",
                ),
                _Request({}),
            )
        self.assertEqual(explicit["model_type"], "krea2_moody_mix_v7_fp8")
        self.assertEqual(hybrid["model_type"], "krea2_moody_mix_v7_fp8")
        self.assertEqual(standard["model_type"], "flux2_dev")
        self.assertEqual(draft["model_type"], "flux2_dev")
        self.assertEqual(supplied["model_type"], "flux2_dev")
        self.assertEqual(hybrid_supplied["model_type"], "flux2_dev")

    def test_v2_initial_blur_is_output_concealment_not_private_access(self):
        self._run(self._body(
            private_output=False,
            explicit_output=False,
            initial_blur=True,
        ))
        output = self._assets()[0]["variants"][0]["outputs"][0]
        self.assertFalse(output["metadata"]["private"])
        self.assertTrue(output["metadata"]["initial_blur"])
        self.assertTrue(
            output["metadata"]["reference_pack"]["initial_blur"]
        )

    def test_v2_explicit_intelligence_selection_can_queue_while_unloaded(self):
        self.ns["_llm_model_catalog"] = lambda _request, _provider="": [{
            "id": "queued-local-vlm",
            "provider": "local",
            "vision_capable": True,
        }]
        selection = self.real_intelligence_selection(
            _Request({}),
            requested_model="queued-local-vlm",
            requested_provider="local",
            purpose="review",
            intent="generic",
        )
        self.assertEqual(selection, {
            "requested_model": "queued-local-vlm",
            "resolved_model": "queued-local-vlm",
            "resolved_provider": "local",
        })

    def test_v2_review_runtime_revalidates_exact_sealed_identity(self):
        sealed = self.ns["_project_reference_seal_intelligence_selection"]({
            "requested_model": "chosen-vlm",
            "resolved_model": "chosen-vlm",
            "resolved_provider": "local",
        })
        self.assertRegex(sealed["selection_revision"], r"^[0-9a-f]{64}$")
        self.ns["_resolve_llm_chat_model"] = lambda _request, model: {
            "model_id": model,
            "response_model_id": model,
            "provider": "local",
            "vision_capable": True,
        }
        runtime = self.ns["_project_reference_runtime_intelligence_selection"](
            _Request({}),
            {"intelligence_recipe": {
                "id": "chosen-vlm",
                "model_id": "hidden-review-fallback",
            }},
            sealed,
            purpose="review",
        )
        self.assertEqual(runtime["response_model_id"], "chosen-vlm")

        with self.assertRaises(HTTPException):
            self.ns["_project_reference_runtime_intelligence_selection"](
                _Request({}),
                {"intelligence_recipe": None},
                {**sealed, "selection_revision": "0" * 64},
                purpose="review",
            )
        self.ns["_resolve_llm_chat_model"] = lambda _request, _model: {
            "model_id": "fallback-vlm",
            "response_model_id": "fallback-vlm",
            "provider": "local",
            "vision_capable": True,
        }
        with self.assertRaisesRegex(RuntimeError, "review_unavailable"):
            self.ns["_project_reference_runtime_intelligence_selection"](
                _Request({}),
                {"intelligence_recipe": None},
                sealed,
                purpose="review",
            )

    def test_v2_uncensored_reviewer_revalidates_loaded_identity_and_vision(self):
        from services import llm_service

        recipe = dict(self.ns["_PROJECT_REFERENCE_ABLITERATED_RECIPE"])
        selection = self.ns["_project_reference_seal_intelligence_selection"]({
            "requested_model": "auto_local",
            "resolved_model": recipe["model_id"],
            "resolved_provider": "local",
        })
        config = {
            "intelligence_policy": "uncensored_auto",
            "intelligence_recipe": recipe,
            "review_selection": selection,
        }
        self.ns["_resolve_llm_chat_model"] = lambda _request, model: {
            "model_id": model,
            "response_model_id": model,
            "provider": "local",
            "vision_capable": True,
        }
        self.ns["_run_llm_with_selection"] = (
            lambda _selection, operation, **kwargs: operation(**kwargs)
        )
        review_request = types.SimpleNamespace(
            instruction="bounded fidelity review",
            creative_request="synthetic authored request",
            sheet_roles=("canonical_identity",),
            sheet_paths=(self.root / "synthetic.png",),
            response_schema={"type": "object"},
        )
        with mock.patch.object(llm_service, "get_status", return_value={
            "loaded": True,
            "model_id": recipe["model_id"],
            "provider": "local",
            "vision_available": False,
        }), mock.patch.object(llm_service, "generate") as generate, mock.patch.object(
            llm_service, "unload_model",
        ) as unload:
            with self.assertRaisesRegex(RuntimeError, "review_unavailable"):
                self.real_selected_reviewer(
                    _Request({}), {}, review_request, config,
                )
        generate.assert_not_called()
        unload.assert_called_once_with()

        with mock.patch.object(llm_service, "get_status", return_value={
            "loaded": True,
            "model_id": recipe["model_id"],
            "provider": "local",
            "vision_available": True,
        }), mock.patch.object(
            llm_service, "generate", return_value='{"status":"pass"}',
        ) as generate, mock.patch.object(llm_service, "unload_model") as unload:
            result = self.real_selected_reviewer(
                _Request({}), {}, review_request, config,
            )
        self.assertEqual(result, '{"status":"pass"}')
        generate.assert_called_once()
        unload.assert_called_once_with()

    def test_v2_selected_planner_runs_and_validates_bounded_schema_in_worker(self):
        from services import llm_service
        from services.reference_sheets import build_reference_pack_plan

        plan = build_reference_pack_plan(
            reference_type="character",
            mode="production",
            intent="generic",
            depth="compact",
            creative_request="bounded synthetic planner request",
            generation_model="flux2_dev",
            editor_model="qwen_image_edit_2511_20B_fp8_lightning_8step",
        )
        config = {
            "planning": {
                "requested_model": "auto",
                "resolved_model": "queued-planner",
                "resolved_provider": "local",
            },
            "intelligence_recipe": None,
        }
        parent = {
            "status": "running",
            "params": {"reference_pack": {}},
        }
        self.ns["_resolve_llm_chat_model"] = lambda _request, model: {
            "model_id": model,
            "response_model_id": model,
            "provider": "local",
            "vision_capable": False,
        }
        captured = {}
        planner_payload = {
            "schema_version": 2,
            "reference_type": plan.reference_type,
            "preset": plan.preset,
            "anchor_basis": plan.anchor_basis,
            "ordered_roles": list(plan.sheet_roles),
            "role_briefs": [{
                "role": role,
                "brief": f"Validated execution brief for {role}",
            } for role in plan.sheet_roles],
        }

        def run(selection, operation, prompt, **kwargs):
            captured.update({
                "selection": selection,
                "prompt": prompt,
                **kwargs,
            })
            return operation(prompt, **kwargs)

        self.ns["_run_llm_with_selection"] = run
        with mock.patch.object(llm_service, "get_status", return_value={
            "loaded": False,
        }), mock.patch.object(
            llm_service, "generate", return_value=json.dumps(planner_payload),
        ), mock.patch.object(llm_service, "unload_model") as unload:
            planned = self.ns["_project_reference_run_planning"](
                _Request({}), parent, plan, config,
            )
        self.assertEqual(
            parent["params"]["reference_pack"]["planning_status"],
            "validated",
        )
        self.assertEqual(captured["json_schema"]["type"], "object")
        self.assertEqual(
            planned.sheets[0].objective,
            "Validated execution brief for canonical_identity",
        )
        self.assertEqual(planned.plan_seal, plan.plan_seal)
        self.assertNotEqual(planned.role_brief_seal, plan.role_brief_seal)
        self.assertNotIn("bounded synthetic planner request", json.dumps(parent))
        unload.assert_called_once_with()

    def test_v2_planner_briefs_drive_generation_and_are_sealed_for_replay(self):
        def intelligence(
            request, *, requested_model, requested_provider, purpose, intent,
        ):
            if purpose == "planning":
                return {
                    "requested_model": "auto",
                    "resolved_model": "queued-planner",
                    "resolved_provider": "local",
                }
            return self._intelligence_selection(
                request,
                requested_model=requested_model,
                requested_provider=requested_provider,
                purpose=purpose,
                intent=intent,
            )

        self.ns["_project_reference_intelligence_selection"] = intelligence
        self.ns["_resolve_llm_chat_model"] = lambda _request, model: {
            "model_id": model,
            "response_model_id": model,
            "provider": "local",
            "vision_capable": False,
        }

        def planner(_selection, _operation, _prompt, **kwargs):
            properties = kwargs["json_schema"]["properties"]
            roles = properties["ordered_roles"]["items"]["enum"]
            return json.dumps({
                "schema_version": properties["schema_version"]["const"],
                "reference_type": properties["reference_type"]["const"],
                "preset": properties["preset"]["const"],
                "anchor_basis": properties["anchor_basis"]["const"],
                "ordered_roles": roles,
                "role_briefs": [{
                    "role": role,
                    "brief": f"SERVER_VALIDATED_{role.upper()}_BRIEF",
                } for role in roles],
            })

        self.ns["_run_llm_with_selection"] = planner
        response = self._run(self._body())
        self.assertIn("SERVER_VALIDATED_CANONICAL_IDENTITY_BRIEF", self.calls[0]["prompt"])
        self.assertIn("SERVER_VALIDATED_TURNAROUND_BRIEF", self.calls[1]["prompt"])
        job_pack = self.jobs[response["job_id"]]["params"]["reference_pack"]
        variant_pack = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(job_pack["planning_status"], "validated")
        self.assertEqual(len(job_pack["role_brief_seal"]), 64)
        self.assertEqual(job_pack["plan_seal"], response["plan"]["plan_seal"])
        self.assertEqual(variant_pack["plan_seal"], response["plan"]["plan_seal"])
        self.assertNotIn("SERVER_VALIDATED", json.dumps(variant_pack))

    def test_v2_planner_role_mismatches_fail_closed_and_auto_falls_back(self):
        from services.reference_sheets import build_reference_pack_plan

        plan = build_reference_pack_plan(
            reference_type="character",
            mode="production",
            intent="generic",
            depth="standard",
            creative_request="bounded mismatch request",
            generation_model="flux2_dev",
            editor_model="qwen_image_edit_2511_20B_fp8_lightning_8step",
        )
        self.ns["_resolve_llm_chat_model"] = lambda _request, model: {
            "model_id": model,
            "response_model_id": model,
            "provider": "local",
            "vision_capable": False,
        }
        fixed = {
            "schema_version": 2,
            "reference_type": plan.reference_type,
            "preset": plan.preset,
            "anchor_basis": plan.anchor_basis,
            "ordered_roles": list(plan.sheet_roles),
        }
        invalid_role_lists = (
            list(plan.sheet_roles[:-1]),
            [plan.sheet_roles[0], plan.sheet_roles[0], plan.sheet_roles[2]],
            list(reversed(plan.sheet_roles)),
        )
        for roles in invalid_role_lists:
            payload = {
                **fixed,
                "role_briefs": [
                    {"role": role, "brief": f"Brief for {role}"}
                    for role in roles
                ],
            }
            self.ns["_run_llm_with_selection"] = (
                lambda *_args, _payload=payload, **_kwargs: json.dumps(_payload)
            )
            parent = {"status": "running", "params": {"reference_pack": {}}}
            explicit = {
                "planning": {
                    "requested_model": "exact-planner",
                    "resolved_model": "exact-planner",
                    "resolved_provider": "local",
                },
                "intelligence_recipe": None,
            }
            with self.subTest(roles=roles), self.assertRaisesRegex(
                RuntimeError, "reference_planner_output_invalid",
            ):
                self.ns["_project_reference_run_planning"](
                    _Request({}), parent, plan, explicit,
                )

        self.ns["_run_llm_with_selection"] = (
            lambda *_args, **_kwargs: "invalid-json"
        )
        parent = {"status": "running", "params": {"reference_pack": {}}}
        auto = {
            "planning": {
                "requested_model": "auto",
                "resolved_model": "auto-planner",
                "resolved_provider": "local",
            },
            "intelligence_recipe": None,
        }
        fallback = self.ns["_project_reference_run_planning"](
            _Request({}), parent, plan, auto,
        )
        self.assertIs(fallback, plan)
        self.assertEqual(
            parent["params"]["reference_pack"]["planning_status"],
            "deterministic_fallback",
        )

    def test_v2_explicit_planner_load_failure_is_terminal_job_failure(self):
        def intelligence(
            request, *, requested_model, requested_provider, purpose, intent,
        ):
            if purpose == "planning":
                return {
                    "requested_model": "exact-planner",
                    "resolved_model": "exact-planner",
                    "resolved_provider": "local",
                }
            return self._intelligence_selection(
                request,
                requested_model=requested_model,
                requested_provider=requested_provider,
                purpose=purpose,
                intent=intent,
            )

        self.ns["_project_reference_intelligence_selection"] = intelligence
        self.ns["_resolve_llm_chat_model"] = mock.Mock(
            side_effect=RuntimeError("synthetic load failure"),
        )
        response = self._run(self._body(planning_model="exact-planner"))
        self.assertEqual(self.jobs[response["job_id"]]["status"], "failed")
        self.assertEqual(self._assets(), [])
        self.assertEqual(self.calls, [])

    def test_v2_auto_planner_fallback_is_published_truthfully(self):
        def intelligence(
            request, *, requested_model, requested_provider, purpose, intent,
        ):
            if purpose == "planning":
                return {
                    "requested_model": "auto",
                    "resolved_model": "auto-planner",
                    "resolved_provider": "local",
                }
            return self._intelligence_selection(
                request,
                requested_model=requested_model,
                requested_provider=requested_provider,
                purpose=purpose,
                intent=intent,
            )

        self.ns["_project_reference_intelligence_selection"] = intelligence
        self.ns["_resolve_llm_chat_model"] = lambda _request, model: {
            "model_id": model,
            "response_model_id": model,
            "provider": "local",
            "vision_capable": False,
        }
        self.ns["_run_llm_with_selection"] = (
            lambda *_args, **_kwargs: "invalid planner output"
        )
        response = self._run(self._body())
        job_pack = self.jobs[response["job_id"]]["params"]["reference_pack"]
        variant = self._assets()[0]["variants"][0]
        variant_pack = variant["metadata"]["reference_pack"]
        self.assertEqual(job_pack["planning_status"], "deterministic_fallback")
        self.assertEqual(
            variant_pack["planning_status"], "deterministic_fallback",
        )
        self.assertTrue(all(
            output["metadata"]["reference_pack"]["planning_status"]
            == "deterministic_fallback"
            for output in variant["outputs"]
        ))

    def test_v2_lora_digest_freezes_only_inside_worker_phase(self):
        lora = self.root / "queued.safetensors"
        lora.write_bytes(b"tiny-test-lora")
        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(lora),
        ):
            digest = mock.Mock(return_value="d" * 64)
            self.ns["_project_reference_sha256_file"] = digest
            pending = self.ns["_project_reference_resolve_additional_loras"](
                [{"id": lora.name, "multiplier": 1.0, "scope": "auto"}],
                generation_model="flux2_dev",
                editor_model="qwen_image_edit_2511_20B_fp8_lightning_8step",
            )
            digest.assert_not_called()
            self.assertEqual(pending[0]["source_sha256"], "pending")
            frozen = self.ns["_project_reference_resolve_additional_loras"](
                [{"id": lora.name, "multiplier": 1.0, "scope": "auto"}],
                generation_model="flux2_dev",
                editor_model="qwen_image_edit_2511_20B_fp8_lightning_8step",
                freeze=True,
            )
        self.assertEqual(digest.call_count, 2)
        self.assertEqual(len(frozen[0]["source_sha256"]), 64)

    def test_v2_parameterized_lora_schema_is_explicit_private_and_retry_exact(self):
        lora = self.root / "parameterized.safetensors"
        lora.write_bytes(b"parameterized-test-lora")
        schema_payload = {
            "schema_version": 1,
            "parameters": [{
                "id": "body_scale",
                "label": "Body scale",
                "type": "enum",
                "required": True,
                "scopes": ["generation"],
                "options": [
                    {
                        "value": "owner-small-private",
                        "label": "Small",
                        "prompt_fragment": "PRIVATE_PARAMETER_SMALL",
                    },
                    {
                        "value": "owner-large-private",
                        "label": "Large",
                        "prompt_fragment": "PRIVATE_PARAMETER_EXPANSION",
                    },
                ],
            }],
            "trigger_fragments": [{
                "text": "PRIVATE_PARAMETER_TRIGGER",
                "scopes": ["generation"],
            }],
        }
        lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": schema_payload,
        }))
        schema = self.ns["_read_lora_parameter_schema"]([
            (str(lora.with_suffix(".maestro.json")), "maestro_sidecar"),
        ])
        enumerable_schema = copy.deepcopy(schema)
        enumerable_schema.pop("schema_digest")
        enumerable_schema.pop("schema_source")
        self.assertNotEqual(
            schema["schema_digest"],
            hashlib.sha256(json.dumps(
                enumerable_schema, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode()).hexdigest(),
        )
        public_schema = self.ns["_public_lora_parameter_schema"](schema)
        self.assertEqual(public_schema["schema_source"], "maestro_sidecar")
        self.assertNotIn("PRIVATE_PARAMETER", json.dumps(public_schema))
        first_commitment = self.ns["_normalize_lora_parameter_values"](
            schema, {"body_scale": "owner-large-private"},
        )
        second_commitment = self.ns["_normalize_lora_parameter_values"](
            schema, {"body_scale": "owner-large-private"},
        )
        self.assertNotEqual(first_commitment[1], second_commitment[1])
        self.assertNotEqual(first_commitment[2], second_commitment[2])
        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(lora),
        ):
            legacy = self.ns["_project_reference_resolve_additional_loras"]([{
                "id": lora.name, "multiplier": 1.0, "scope": "generation",
            }], generation_model="flux2_klein_9b", editor_model=None)
        self.assertIsNone(legacy[0]["parameter_schema_digest"])
        self.assertEqual(legacy[0]["parameter_values"], ())
        self.assertEqual(legacy[0]["parameter_expansions"], [])

        selection = {
            "id": lora.name,
            "multiplier": 1.15,
            "scope": "auto",
            "parameter_schema_digest": schema["schema_digest"],
            "parameter_values": {"body_scale": "owner-large-private"},
        }
        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(lora),
        ):
            first = self._run(self._body(additional_loras=[selection]))
        first_call_count = len(self.calls)
        self.assertIn("PRIVATE_PARAMETER_TRIGGER", self.calls[0]["prompt"])
        self.assertIn("PRIVATE_PARAMETER_EXPANSION", self.calls[0]["prompt"])
        self.assertTrue(all(
            "PRIVATE_PARAMETER" not in call["prompt"]
            for call in self.calls[1:first_call_count]
        ))
        first_variant = self._assets()[0]["variants"][0]
        public_pack = first_variant["metadata"]["reference_pack"]
        applied = public_pack["additional_loras"]["applied"][0]
        self.assertEqual(applied["parameters"]["count"], 1)
        self.assertEqual(applied["parameters"]["ids"], ["body_scale"])
        enumerable_sha = hashlib.sha256(json.dumps(
            [{"id": "body_scale", "value": "owner-large-private"}],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        self.assertNotEqual(
            applied["parameters"]["values_digest"], enumerable_sha,
        )
        public_serialized = json.dumps(public_pack)
        self.assertNotIn("owner-large-private", public_serialized)
        self.assertNotIn("PRIVATE_PARAMETER", public_serialized)
        self.assertNotIn("commitment_context", public_serialized)
        private = first_variant["metadata"]["private_authored_settings"]
        self.assertRegex(
            private["additional_lora_parameters"][0]["commitment_context"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            private["additional_lora_parameters"][0]["values"],
            [{"id": "body_scale", "value": "owner-large-private"}],
        )
        corrupted = copy.deepcopy(first_variant)
        corrupted["metadata"]["private_authored_settings"][
            "additional_lora_parameters"
        ][0]["values"][0]["value"] = "owner-small-private"
        with self.assertRaises(HTTPException) as corrupt_error:
            self.ns["_project_reference_private_authored_snapshot"](corrupted)
        self.assertEqual(corrupt_error.exception.status_code, 409)
        authoring = self.ns["get_project_reference_authoring"](
            "project", first["asset"]["id"], first_variant["id"], _Request({}),
        )
        self.assertEqual(authoring["additional_loras"], [{
            **selection,
            "parameter_values_digest": applied["parameters"]["values_digest"],
            "parameter_expansion_digest": applied["parameters"][
                "expansion_digest"
            ],
        }])
        self.assertNotIn("commitment_context", json.dumps(authoring))
        self.assertNotIn(
            "additional_lora_parameters", authoring["authored_settings"],
        )

        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(lora),
        ):
            retry = self._run(self._body(
                asset_id=first["asset"]["id"],
                parent_variant_id=first_variant["id"],
                type_fields=private["type_fields"],
                detail_callouts=private["detail_callouts"],
            ))
        retry_private = self.jobs[retry["job_id"]]["params"][
            "reference_pack"
        ]["private_authored_settings"]
        self.assertEqual(
            retry_private["additional_lora_parameters"],
            private["additional_lora_parameters"],
        )
        self.assertIn(
            "PRIVATE_PARAMETER_EXPANSION", self.calls[first_call_count]["prompt"],
        )
        explicit_empty = self._run(self._body(
            asset_id=first["asset"]["id"],
            parent_variant_id=first_variant["id"],
            type_fields=private["type_fields"],
            detail_callouts=private["detail_callouts"],
            additional_loras=[],
        ))
        self.assertNotIn(
            "additional_lora_parameters",
            self.jobs[explicit_empty["job_id"]]["params"]["reference_pack"][
                "private_authored_settings"
            ],
        )

    def test_server_known_lora_contracts_require_exact_identity_and_disclose_triggers(self):
        _ModelRegistry.lora_dir = str(self.root)
        cases = (
            (
                "_PROJECT_REFERENCE_BREAST_SIZE_LORA",
                "breast_size",
                [
                    "tiny breasts", "small breasts", "saggy breasts",
                    "breast implants", "huge breasts", "skin detail",
                ],
            ),
            (
                "_PROJECT_REFERENCE_SEXGOD_LORA",
                "activation_keyword",
                ["femalenudestyle"],
            ),
        )
        for constant_name, parameter_id, expected_phrases in cases:
            with self.subTest(contract=constant_name):
                contract, lora, contract_getsize = self._known_lora(
                    constant_name,
                )
                with mock.patch("os.path.getsize", side_effect=contract_getsize):
                    schema = self.ns[
                        "_project_reference_lora_parameter_schema"
                    ]("flux2_dev", str(lora))
                    details = self.ns["list_loras_details"]("flux2_dev")
                self.assertEqual(schema["schema_source"], "server_known_contract")
                self.assertEqual(schema["_expected_source_sha256"], contract["sha256"])
                self.assertEqual(schema["parameters"][0]["id"], parameter_id)
                row = next(
                    item for item in details["loras"]
                    if item["filename"] == contract["filename"]
                )
                public_schema = row["parameter_schema"]
                self.assertEqual(
                    public_schema["schema_source"], "server_known_contract",
                )
                disclosure = public_schema["trigger_disclosure"]
                self.assertEqual(disclosure["source"], "server_known_contract")
                self.assertEqual(
                    [item["text"] for item in disclosure["activation_phrases"]],
                    expected_phrases,
                )
                self.assertEqual(disclosure["scopes"], ["generation"])
                self.assertNotIn("_expected_source_sha256", json.dumps(public_schema))

                with mock.patch("os.path.getsize", side_effect=contract_getsize):
                    self.assertIsNone(self.ns[
                        "_project_reference_lora_parameter_schema"
                    ]("flux2_klein_9b", str(lora)))
                lookalike = self.root / f"lookalike-{contract['filename']}"
                lookalike.write_bytes(b"lookalike")
                lookalike.with_suffix(".civitai.json").write_text(
                    lora.with_suffix(".civitai.json").read_text(),
                )
                real_getsize = os.path.getsize

                def lookalike_getsize(path):
                    if os.path.realpath(path) == os.path.realpath(lookalike):
                        return contract["size_bytes"]
                    return real_getsize(path)

                with mock.patch("os.path.getsize", side_effect=lookalike_getsize):
                    self.assertIsNone(self.ns[
                        "_project_reference_lora_parameter_schema"
                    ]("flux2_dev", str(lookalike)))
                sidecar_path = lora.with_suffix(".civitai.json")
                exact_metadata = json.loads(sidecar_path.read_text())
                mismatched_metadata = dict(exact_metadata)
                mismatched_metadata["versionId"] += 1
                sidecar_path.write_text(json.dumps(mismatched_metadata))
                with mock.patch("os.path.getsize", side_effect=contract_getsize):
                    self.assertIsNone(self.ns[
                        "_project_reference_lora_parameter_schema"
                    ]("flux2_dev", str(lora)))
                sidecar_path.write_text(json.dumps(exact_metadata))

        contract, lora, contract_getsize = self._known_lora(
            "_PROJECT_REFERENCE_BREAST_SIZE_LORA",
        )
        lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": {
                "schema_version": 1,
                "parameters": [{
                    "id": "owner_override",
                    "label": "Owner override",
                    "type": "boolean",
                    "default": True,
                    "true_prompt_fragment": "private owner phrase",
                }],
            },
        }))
        with mock.patch("os.path.getsize", side_effect=contract_getsize):
            owner = self.ns["_project_reference_lora_parameter_schema"](
                "flux2_dev", str(lora),
            )
        self.assertEqual(owner["schema_source"], "maestro_sidecar")
        self.assertEqual(owner["parameters"][0]["id"], "owner_override")
        self.assertNotIn(
            "trigger_disclosure",
            self.ns["_public_lora_parameter_schema"](owner),
        )
        advisory = self.root / "trained-words-only.safetensors"
        advisory.write_bytes(b"advisory-only")
        advisory.with_suffix(".civitai.json").write_text(json.dumps({
            "modelId": 999,
            "versionId": 1000,
            "baseModel": "Flux.2 D",
            "trainedWords": ["huge breasts", "femalenudestyle"],
        }))
        details = self.ns["list_loras_details"]("flux2_dev")
        advisory_row = next(
            item for item in details["loras"]
            if item["filename"] == advisory.name
        )
        self.assertNotIn("parameter_schema", advisory_row)
        self.assertNotIn("parameter_schema_status", advisory_row)

    def test_server_known_loras_apply_by_role_freeze_exact_hash_and_retry(self):
        _ModelRegistry.lora_dir = str(self.root)
        cases = (
            (
                "_PROJECT_REFERENCE_BREAST_SIZE_LORA",
                {"breast_size": "huge", "skin_detail": True},
                ["huge breasts", "skin detail"],
            ),
            (
                "_PROJECT_REFERENCE_SEXGOD_LORA",
                {"activation_keyword": True},
                ["femalenudestyle"],
            ),
        )
        for constant_name, values, expected_phrases in cases:
            with self.subTest(contract=constant_name):
                contract, lora, contract_getsize = self._known_lora(
                    constant_name,
                )

                def resolve(model, filename):
                    if model == "flux2_dev" and filename == lora.name:
                        return str(lora)
                    return ""

                with mock.patch("os.path.getsize", side_effect=contract_getsize), mock.patch.object(
                    _ModelRegistry, "resolve_lora_path", side_effect=resolve,
                ):
                    schema = self.ns[
                        "_project_reference_lora_parameter_schema"
                    ]("flux2_dev", str(lora))
                    selection = {
                        "id": lora.name,
                        "multiplier": 0.85,
                        "scope": "generation",
                        "parameter_schema_digest": schema["schema_digest"],
                        "parameter_values": values,
                    }
                    with self.assertRaises(HTTPException) as missing_parameters:
                        self.ns["_project_reference_resolve_additional_loras"]([{
                            "id": lora.name,
                            "multiplier": 0.85,
                            "scope": "generation",
                        }], generation_model="flux2_dev", editor_model=None)
                    self.assertEqual(
                        missing_parameters.exception.status_code, 400,
                    )
                    config = self.ns["_project_reference_request_config"](
                        self._body(
                            model_type="flux2_dev",
                            additional_loras=[selection],
                        ),
                        _Request({}),
                    )
                self.assertEqual(
                    config["additional_loras"][0]["expected_source_sha256"],
                    contract["sha256"],
                )
                params = self.ns["_project_reference_generation_params"](
                    config,
                    model_type="flux2_dev",
                    prompt="base prompt",
                    size=(64, 64),
                    seed=1,
                    operation_scope="generation",
                    operation_role="canonical_identity",
                )
                for phrase in expected_phrases:
                    self.assertIn(phrase, params["prompt"])
                other_role = self.ns["_project_reference_generation_params"](
                    config,
                    model_type="flux2_dev",
                    prompt="base prompt",
                    size=(64, 64),
                    seed=1,
                    operation_scope="generation",
                    operation_role="canonical_space",
                )
                for phrase in expected_phrases:
                    self.assertNotIn(phrase, other_role["prompt"])

                with mock.patch("os.path.getsize", side_effect=contract_getsize), mock.patch.object(
                    _ModelRegistry, "resolve_lora_path", side_effect=resolve,
                ), mock.patch.dict(self.ns, {
                    "_project_reference_sha256_file": mock.Mock(
                        return_value=contract["sha256"],
                    ),
                }):
                    frozen = self.ns[
                        "_project_reference_resolve_additional_loras"
                    ](
                        [selection],
                        generation_model="flux2_dev",
                        editor_model=None,
                        operation_roles={
                            "generation": ("canonical_identity",),
                            "editing": (),
                        },
                        freeze=True,
                        commitment_contexts={
                            lora.name: config["additional_loras"][0][
                                "parameter_commitment_context"
                            ],
                        },
                    )
                self.assertEqual(
                    frozen[0]["expected_source_sha256"], contract["sha256"],
                )
                with mock.patch("os.path.getsize", side_effect=contract_getsize), mock.patch.object(
                    _ModelRegistry, "resolve_lora_path", side_effect=resolve,
                ), mock.patch.dict(self.ns, {
                    "_project_reference_sha256_file": mock.Mock(
                        return_value="0" * 64,
                    ),
                }), self.assertRaises(HTTPException) as mismatch:
                    self.ns["_project_reference_resolve_additional_loras"](
                        [selection],
                        generation_model="flux2_dev",
                        editor_model=None,
                        operation_roles={
                            "generation": ("canonical_identity",),
                            "editing": (),
                        },
                        freeze=True,
                    )
                self.assertEqual(mismatch.exception.status_code, 409)

        contract, lora, contract_getsize = self._known_lora(
            "_PROJECT_REFERENCE_BREAST_SIZE_LORA",
        )

        def resolve_breast(model, filename):
            return str(lora) if model == "flux2_dev" and filename == lora.name else ""

        with mock.patch("os.path.getsize", side_effect=contract_getsize), mock.patch.object(
            _ModelRegistry, "resolve_lora_path", side_effect=resolve_breast,
        ):
            schema = self.ns["_project_reference_lora_parameter_schema"](
                "flux2_dev", str(lora),
            )
            location_selection = {
                "id": lora.name,
                "multiplier": 1.0,
                "scope": "generation",
                "parameter_schema_digest": schema["schema_digest"],
                "parameter_values": {
                    "breast_size": "small", "skin_detail": True,
                },
            }
            for asset_type, preset in (
                ("location", "spatial"),
                ("creature", "identity"),
            ):
                with self.subTest(asset_type=asset_type), self.assertRaises(
                    HTTPException,
                ) as inapplicable:
                    self.ns["_project_reference_request_config"](
                        self._body(
                            asset_type=asset_type, preset=preset,
                            model_type="flux2_dev",
                            additional_loras=[location_selection],
                        ),
                        _Request({}),
                    )
                self.assertEqual(inapplicable.exception.status_code, 409)

        with mock.patch("os.path.getsize", side_effect=contract_getsize), mock.patch.object(
            _ModelRegistry, "resolve_lora_path", side_effect=resolve_breast,
        ), mock.patch.dict(self.ns, {
            "_project_reference_sha256_file": mock.Mock(
                return_value=contract["sha256"],
            ),
        }):
            first = self._run(self._body(
                model_type="flux2_dev",
                additional_loras=[location_selection],
            ))
            first_variant = self._assets()[0]["variants"][0]
            first_call_count = len(self.calls)
            retry = self._run(self._body(
                asset_id=first["asset"]["id"],
                parent_variant_id=first_variant["id"],
                model_type="flux2_dev",
            ))
        self.assertIn("small breasts", self.calls[0]["prompt"])
        self.assertIn("small breasts", self.calls[first_call_count]["prompt"])
        self.assertEqual(
            self.jobs[first["job_id"]]["params"]["reference_pack"][
                "private_authored_settings"
            ]["additional_lora_parameters"],
            self.jobs[retry["job_id"]]["params"]["reference_pack"][
                "private_authored_settings"
            ]["additional_lora_parameters"],
        )

    def test_v2_lora_parameter_schema_required_optional_and_control_validation(self):
        normalize = self.ns["_normalize_lora_parameter_schema"]
        normalize_values = self.ns["_normalize_lora_parameter_values"]
        schema = normalize({
            "schema_version": 1,
            "parameters": [
                {
                    "id": "required_choice",
                    "label": "Required choice",
                    "type": "enum",
                    "required": True,
                    "options": [{
                        "value": "selected",
                        "label": "Selected",
                        "prompt_fragment": "required selected",
                    }],
                },
                {
                    "id": "optional_note",
                    "label": "Optional note",
                    "type": "text",
                    "required": False,
                    "max_length": 40,
                    "prompt_template": "note {value}",
                },
                {
                    "id": "amount",
                    "label": "Amount",
                    "type": "number",
                    "default": 1.0,
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "step": 0.5,
                    "prompt_template": "amount {value}",
                },
                {
                    "id": "count",
                    "label": "Count",
                    "type": "integer",
                    "required": True,
                    "minimum": 1,
                    "maximum": 5,
                    "step": 2,
                    "prompt_template": "count {value}",
                },
                {
                    "id": "enabled",
                    "label": "Enabled",
                    "type": "boolean",
                    "default": True,
                    "true_prompt_fragment": "enabled",
                },
            ],
        })
        with self.assertRaisesRegex(ValueError, "Required"):
            normalize_values(schema, {})
        values, _values_digest, _expansion_digest, expansions = normalize_values(
            schema, {"required_choice": "selected", "count": 3},
        )
        self.assertEqual(values, (
            ("required_choice", "selected"),
            ("amount", 1.0),
            ("count", 3),
            ("enabled", True),
        ))
        self.assertEqual(len(expansions), 4)
        with self.assertRaisesRegex(ValueError, "text parameter"):
            normalize_values(schema, {
                "required_choice": "selected",
                "optional_note": "line one\nline two",
                "count": 3,
            })
        with self.assertRaisesRegex(ValueError, "number parameter step"):
            normalize_values(schema, {
                "required_choice": "selected", "amount": 0.3, "count": 3,
            })
        with self.assertRaisesRegex(ValueError, "integer parameter"):
            normalize_values(schema, {
                "required_choice": "selected", "count": 2,
            })
        with self.assertRaisesRegex(ValueError, "boolean parameter"):
            normalize_values(schema, {
                "required_choice": "selected", "count": 3,
                "enabled": 1,
            })
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            normalize({
                "schema_version": 1,
                "parameters": [{
                    "id": "x", "label": "X", "type": "boolean",
                    "true_prompt_fragment": "x", "unexpected": 1,
                }],
            })
        with self.assertRaisesRegex(ValueError, "enum option"):
            normalize({
                "schema_version": 1,
                "parameters": [{
                    "id": "long", "label": "Long", "type": "enum",
                    "options": [{
                        "value": "v" * 257,
                        "label": "Long",
                        "prompt_fragment": "long",
                    }],
                }],
            })
        for ambiguous_options in (
            [
                {"value": 1.0, "label": "Float", "prompt_fragment": "float"},
            ],
            [
                {
                    "value": -0.0,
                    "label": "Negative zero",
                    "prompt_fragment": "negative zero",
                },
            ],
            [
                {"value": 1, "label": "Integer", "prompt_fragment": "integer"},
                {"value": 1.0, "label": "Float", "prompt_fragment": "float"},
            ],
            [
                {
                    "value": 9_007_199_254_740_992,
                    "label": "Unsafe integer",
                    "prompt_fragment": "unsafe integer",
                },
            ],
        ):
            with self.subTest(ambiguous_options=ambiguous_options):
                with self.assertRaisesRegex(ValueError, "enum option"):
                    normalize({
                        "schema_version": 1,
                        "parameters": [{
                            "id": "ambiguous",
                            "label": "Ambiguous",
                            "type": "enum",
                            "options": ambiguous_options,
                        }],
                    })
        distinct_json_scalars = normalize({
            "schema_version": 1,
            "parameters": [{
                "id": "json_scalar",
                "label": "JSON scalar",
                "type": "enum",
                "options": [
                    {"value": True, "label": "Boolean", "prompt_fragment": "bool"},
                    {"value": 1, "label": "Integer", "prompt_fragment": "integer"},
                    {"value": 1.5, "label": "Float", "prompt_fragment": "float"},
                    {
                        "value": -9_007_199_254_740_991,
                        "label": "Minimum safe integer",
                        "prompt_fragment": "minimum safe integer",
                    },
                    {
                        "value": 9_007_199_254_740_991,
                        "label": "Maximum safe integer",
                        "prompt_fragment": "maximum safe integer",
                    },
                ],
            }],
        })
        for scalar in (
            True, 1, 1.5,
            -9_007_199_254_740_991, 9_007_199_254_740_991,
        ):
            with self.subTest(scalar=scalar):
                values, _value_digest, _expansion_digest, _expansions = (
                    normalize_values(
                        distinct_json_scalars, {"json_scalar": scalar},
                    )
                )
                self.assertEqual(type(values[0][1]), type(scalar))
                self.assertEqual(values[0][1], scalar)
        large_options = [
            {
                "value": f"value-{index}",
                "label": f"Value {index}",
                "prompt_fragment": "x" * 500,
            }
            for index in range(64)
        ]
        with self.assertRaisesRegex(ValueError, "schema exceeds"):
            normalize({
                "schema_version": 1,
                "parameters": [
                    {
                        "id": f"large{index}",
                        "label": f"Large {index}",
                        "type": "enum",
                        "options": copy.deepcopy(large_options),
                    }
                    for index in range(2)
                ],
            })
        oversized_sidecar = self.root / "oversized.maestro.json"
        oversized_sidecar.write_text(" " * 1_048_577)
        with self.assertRaisesRegex(ValueError, "size limit"):
            self.ns["_read_lora_parameter_schema"]([
                (str(oversized_sidecar), "maestro_sidecar"),
            ])

    def test_v2_lora_parameter_sidecar_precedence_and_strength_only_rejection(self):
        lora = self.root / "precedence.safetensors"
        lora.write_bytes(b"precedence-lora")
        civitai_schema = {
            "schema_version": 1,
            "parameters": [{
                "id": "source", "label": "Imported", "type": "boolean",
                "default": False, "true_prompt_fragment": "imported",
            }],
        }
        owner_schema = copy.deepcopy(civitai_schema)
        owner_schema["parameters"][0]["label"] = "Owner override"
        lora.with_suffix(".civitai.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": civitai_schema,
        }))
        lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": owner_schema,
        }))
        schema = self.ns["_read_lora_parameter_schema"](
            self.ns["_project_reference_lora_schema_sidecars"](
                "flux2_klein_9b", str(lora),
            ),
        )
        self.assertEqual(schema["parameters"][0]["label"], "Owner override")
        self.assertEqual(schema["schema_source"], "maestro_sidecar")

        primary = self.root / "primary"
        linked = self.root / "linked"
        primary.mkdir()
        linked.mkdir()
        linked_lora = linked / "shared.safetensors"
        linked_lora.write_bytes(b"linked-lora")
        primary_schema = copy.deepcopy(owner_schema)
        primary_schema["parameters"][0]["label"] = "Primary override"
        linked_schema = copy.deepcopy(owner_schema)
        linked_schema["parameters"][0]["label"] = "Linked metadata"
        (primary / "shared.maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": primary_schema,
        }))
        linked_lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": linked_schema,
        }))
        _ModelRegistry.lora_dir = str(primary)
        linked_result = self.ns["_read_lora_parameter_schema"](
            self.ns["_project_reference_lora_schema_sidecars"](
                "flux2_klein_9b", str(linked_lora),
            ),
        )
        self.assertEqual(
            linked_result["parameters"][0]["label"], "Primary override",
        )

        strength_only = self.root / "strength-only.safetensors"
        strength_only.write_bytes(b"strength-only")
        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(strength_only),
        ), self.assertRaises(HTTPException) as raised:
            self.ns["_project_reference_resolve_additional_loras"]([{
                "id": strength_only.name,
                "multiplier": 1.0,
                "scope": "generation",
                "parameter_schema_digest": "a" * 64,
                "parameter_values": {"anything": True},
            }], generation_model="flux2_klein_9b", editor_model=None)
        self.assertEqual(raised.exception.status_code, 400)

    def test_generic_lora_details_exposes_only_sanitized_owner_schema(self):
        _ModelRegistry.lora_dir = str(self.root)
        lora = self.root / "details.safetensors"
        lora.write_bytes(b"details-lora")
        lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": {
                "schema_version": 1,
                "parameters": [{
                    "id": "shape",
                    "label": "Shape",
                    "type": "enum",
                    "default": "rounded",
                    "options": [{
                        "value": "rounded",
                        "label": "Rounded",
                        "prompt_fragment": "PRIVATE_DETAILS_FRAGMENT",
                    }],
                }],
            },
        }))
        details = self.ns["list_loras_details"]("flux2_klein_9b")
        row = details["loras"][0]
        self.assertEqual(row["filename"], lora.name)
        self.assertEqual(row["parameter_schema_status"], "ready")
        self.assertEqual(
            row["parameter_schema"]["parameters"][0]["id"], "shape",
        )
        self.assertEqual(
            row["parameter_schema"]["schema_source"], "maestro_sidecar",
        )
        self.assertNotIn("trigger_disclosure", row["parameter_schema"])
        self.assertNotIn("PRIVATE_DETAILS_FRAGMENT", json.dumps(row))

        primary = self.root / "catalog-primary"
        linked = self.root / "catalog-linked"
        primary.mkdir()
        linked.mkdir()
        linked_lora = linked / "shared.safetensors"
        linked_lora.write_bytes(b"linked-catalog-lora")
        (primary / "shared.maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": {
                "schema_version": 1,
                "parameters": [{
                    "id": "source", "label": "Primary catalog override",
                    "type": "boolean", "default": True,
                    "true_prompt_fragment": "PRIVATE_PRIMARY_FRAGMENT",
                }],
            },
        }))
        linked_lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": {
                "schema_version": 1,
                "parameters": [{
                    "id": "source", "label": "Linked catalog metadata",
                    "type": "boolean", "default": True,
                    "true_prompt_fragment": "PRIVATE_LINKED_FRAGMENT",
                }],
            },
        }))
        _ModelRegistry.lora_dir = str(primary)
        with mock.patch.object(
            _ModelRegistry, "get_lora_search_dirs", return_value=[str(linked)],
        ):
            linked_details = self.ns["list_loras_details"]("flux2_klein_9b")
        linked_schema = linked_details["loras"][0]["parameter_schema"]
        self.assertEqual(
            linked_schema["parameters"][0]["label"],
            "Primary catalog override",
        )
        self.assertNotIn("PRIVATE_", json.dumps(linked_schema))

    def test_v2_lora_parameter_expansion_budgets_bound_schema_and_operation(self):
        fragment = "x" * 500
        parameters = [
            {
                "id": f"p{index}",
                "label": f"Parameter {index}",
                "type": "enum",
                "default": "on",
                "options": [{
                    "value": "on",
                    "label": "On",
                    "prompt_fragment": fragment,
                }],
            }
            for index in range(16)
        ]
        bounded_schema = {
            "schema_version": 1,
            "parameters": parameters,
        }
        normalized = self.ns["_normalize_lora_parameter_schema"](
            bounded_schema,
        )
        values, _values_digest, _expansion_digest, expansions = self.ns[
            "_normalize_lora_parameter_values"
        ](normalized, {})
        self.assertEqual(len(values), 16)
        self.assertEqual(sum(len(item["text"]) for item in expansions), 8_000)

        overflow_schema = copy.deepcopy(bounded_schema)
        overflow_schema["parameters"].append({
            "id": "overflow",
            "label": "Overflow",
            "type": "enum",
            "default": "on",
            "options": [{
                "value": "on", "label": "On", "prompt_fragment": fragment,
            }],
        })
        overflow_normalized = self.ns["_normalize_lora_parameter_schema"](
            overflow_schema,
        )
        with self.assertRaisesRegex(ValueError, "resource budget"):
            self.ns["_normalize_lora_parameter_values"](
                overflow_normalized, {},
            )

        lora = self.root / "budget.safetensors"
        lora.write_bytes(b"budget-lora")
        lora.with_suffix(".maestro.json").write_text(json.dumps({
            "maestro_lora_parameter_schema": bounded_schema,
        }))
        selections = [
            {
                "id": f"budget-{index}.safetensors",
                "multiplier": 1.0,
                "scope": "generation",
                "parameter_schema_digest": normalized["schema_digest"],
                "parameter_values": {},
            }
            for index in range(5)
        ]
        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(lora),
        ):
            boundary = self.ns["_project_reference_resolve_additional_loras"](
                selections[:4],
                generation_model="flux2_klein_9b",
                editor_model=None,
            )
            self.assertEqual(len(boundary), 4)
            with self.assertRaises(HTTPException) as raised:
                self.ns["_project_reference_resolve_additional_loras"](
                    selections,
                    generation_model="flux2_klein_9b",
                    editor_model=None,
                )
        self.assertEqual(raised.exception.status_code, 400)

    def test_moody_manual_catalog_manifest_is_exact_and_token_free(self):
        expected = {
            "krea2_moody_mix_v7_fp8": (
                "moodyKrea2Mix_v70.safetensors",
                "405db6a1d060075d176c3578063b6fa2feb07b58bb61ddb403ddba0669a35a6d",
            ),
            "krea2_moody_cutie_v4_fp8": (
                "moodyCutieMixKrea2_v40.safetensors",
                "6c54d783aaaab1a6924fafcfa3afa9f36abe72a59723d424e932484a8c98316a",
            ),
        }
        for model_type, (filename, digest) in expected.items():
            with self.subTest(model_type=model_type):
                model_def = json.loads(
                    (ROOT / "app" / "defaults" / f"{model_type}.json").read_text()
                )["model"]
                manifest = self.ns["_public_manual_installation_manifest"](
                    model_def,
                )
                self.assertEqual(manifest["filename"], filename)
                self.assertEqual(manifest["sha256"], digest)
                self.assertEqual(manifest["size_bytes"], 14125457032)
                self.assertEqual(manifest["destination_hint"], "app/ckpts")
                self.assertIs(manifest["local_verification_required"], True)
                self.assertTrue(manifest["source_url"].startswith("https://"))
                self.assertTrue(manifest["download_url"].startswith("https://"))
                self.assertNotRegex(json.dumps(manifest), r"(?i)(token|api[_-]?key)=")
                for unsafe_url in (
                    (
                        "https://civitai.com/api/download/models/3209007"
                        if model_type == "krea2_moody_mix_v7_fp8"
                        else "https://civitai.com/api/download/models/3211049"
                    ),
                    "https://user:password@example.invalid/model",
                    "https://example.invalid/model?token=private",
                    "https://example.invalid/model?api%5Fkey=private",
                    "https://example.invalid/model?X-Amz-Signature=private",
                    "https://example.invalid/model?download=1",
                    "https://civitai.com/api/download/models/3209007?type=Diffusion%20Model&format=SECRETCREDENTIAL1234567890&fp=fp8",
                    "https://example.invalid/model#token=private",
                ):
                    unsafe = copy.deepcopy(model_def)
                    unsafe["artifact_provenance"]["checkpoint"][
                        "download_url"
                    ] = unsafe_url
                    self.assertIsNone(
                        self.ns["_public_manual_installation_manifest"](unsafe),
                    )

    def test_pornmaster_manual_manifest_allows_only_registered_queryless_tuple(self):
        model_def = json.loads((
            ROOT / "app" / "defaults"
            / "flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke.json"
        ).read_text())["model"]
        manifest = self.ns["_public_manual_installation_manifest"](model_def)
        self.assertEqual(manifest, {
            "filename": "pornmasterFlux2Klein_v4TurboFp8.safetensors",
            "size_bytes": 9433104872,
            "sha256": (
                "e90eeb50140a10806341b7521c340214c6f76cec2f8f8dae7a443c5806072df7"
            ),
            "source_url": (
                "https://civitai.com/models/2382648?modelVersionId=2973304"
            ),
            "download_url": "https://civitai.com/api/download/models/2973304",
            "destination_hint": "app/ckpts",
            "local_verification_required": True,
        })

        drift_cases = {
            "model_id": 2382649,
            "version_id": 2973305,
            "file_id": 1,
            "filename": "lookalike.safetensors",
            "size_bytes": 9433104873,
            "sha256": "0" * 64,
            "precision": "fp16",
            "artifact_kind": "lookalike",
            "creator": "lookalike",
        }
        for field, value in drift_cases.items():
            with self.subTest(field=field):
                unsafe = copy.deepcopy(model_def)
                unsafe["artifact_provenance"]["checkpoint"][field] = value
                if field == "filename":
                    unsafe["URLs"] = [value]
                self.assertIsNone(
                    self.ns["_public_manual_installation_manifest"](unsafe),
                )

        for unsafe_url in (
            "https://user:password@civitai.com/api/download/models/2973304",
            "https://civitai.com/api/download/models/2973304#fragment",
            "https://civitai.com/api/download/models/2973305",
            "https://civitai.com/api/download/models/2973304?token=private",
            "https://www.civitai.com/api/download/models/2973304",
        ):
            with self.subTest(unsafe_url=unsafe_url):
                unsafe = copy.deepcopy(model_def)
                unsafe["artifact_provenance"]["checkpoint"][
                    "download_url"
                ] = unsafe_url
                self.assertIsNone(
                    self.ns["_public_manual_installation_manifest"](unsafe),
                )

    def test_v2_deferred_lora_freeze_keeps_returned_plan_seal_stable(self):
        lora = self.root / "deferred.safetensors"
        lora.write_bytes(b"deferred-worker-lora")
        captured = {}

        def register(job, *, worker=None, **_kwargs):
            job.setdefault("access_policy", {
                "private": bool(job.pop("private", False)),
                "explicit": bool(job.pop("explicit", False)),
            })
            self.jobs[job["id"]] = job
            captured["worker"] = worker
            return _DoneThread()

        self.ns["_queue_recovery_register_and_publish"] = register
        with mock.patch.object(
            _ModelRegistry, "resolve_lora_path", return_value=str(lora),
        ):
            response = self._run(self._body(additional_loras=[{
                "id": lora.name,
                "multiplier": 1.0,
                "scope": "auto",
            }]))
            job_pack = self.jobs[response["job_id"]]["params"]["reference_pack"]
            pending_resource_seal = job_pack["resource_seal"]
            self.assertEqual(
                self.jobs[response["job_id"]]["resource_intent"],
                "text",
            )
            self.assertEqual(job_pack["plan_seal"], response["plan"]["plan_seal"])
            self.assertEqual(self._assets(), [])
            captured["worker"](response["job_id"])

        job_pack = self.jobs[response["job_id"]]["params"]["reference_pack"]
        variant_pack = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        self.assertEqual(job_pack["plan_seal"], response["plan"]["plan_seal"])
        self.assertEqual(variant_pack["plan_seal"], response["plan"]["plan_seal"])
        self.assertNotEqual(job_pack["resource_seal"], pending_resource_seal)

    def test_v2_busy_generation_lane_allows_text_planning_only(self):
        captured = {}
        planning = mock.Mock(side_effect=lambda _request, _job, plan, _config: plan)

        def register(job, *, worker=None, **_kwargs):
            self.jobs[job["id"]] = job
            captured["worker"] = worker
            return _DoneThread()

        self.ns["_queue_recovery_register_and_publish"] = register
        parent_generation_slot = mock.Mock()
        self.ns["generation_slot"] = parent_generation_slot
        self.ns["_project_reference_run_planning"] = planning
        response = self._run(self._body())
        parent = self.jobs[response["job_id"]]
        self.assertEqual(parent["status"], "queued")
        self.assertEqual(parent["resource_intent"], "text")

        thread = threading.Thread(
            target=captured["worker"], args=(response["job_id"],),
        )
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        planning.assert_called_once()
        parent_generation_slot.assert_not_called()
        self.assertEqual(parent["status"], "completed")
        self.assertGreater(len(self.calls), 0)
        child_source = ast.get_source_segment(
            LAUNCH.read_text(encoding="utf-8"),
            next(
                node for node in ast.parse(
                    LAUNCH.read_text(encoding="utf-8")
                ).body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_run_project_reference_image_job"
            ),
        )
        self.assertIn('"resource_intent": "generation"', child_source)
        self.assertIn('recovery_kind="studio_generation"', child_source)

    def test_v2_scoped_loras_apply_only_to_compatible_operations_and_seal_hashes(self):
        generation_lora = self.root / "generation" / "chosen.safetensors"
        editing_lora = self.root / "editing" / "chosen.safetensors"
        generation_lora.parent.mkdir()
        editing_lora.parent.mkdir()
        generation_lora.write_bytes(b"generation-compatible-lora")
        editing_lora.write_bytes(b"editing-compatible-lora")

        def resolve(model, filename):
            self.assertEqual(filename, "chosen.safetensors")
            if model == "flux2_klein_9b":
                return str(generation_lora)
            if model == "qwen_image_edit_2511_20B_fp8_lightning_8step":
                return str(editing_lora)
            return ""

        with mock.patch.object(_ModelRegistry, "resolve_lora_path", side_effect=resolve):
            response = self._run(self._body(
                additional_loras=[{
                    "id": "chosen.safetensors",
                    "multiplier": 1.25,
                    "scope": "auto",
                }],
            ))
        self.assertIn("chosen.safetensors", self.calls[0]["activated_loras"])
        self.assertTrue(all(
            "chosen.safetensors" in call["activated_loras"]
            for call in self.calls[1:]
        ))
        summary = self._assets()[0]["variants"][0]["metadata"]["reference_pack"]
        applied = summary["additional_loras"]["applied"][0]
        self.assertEqual(applied["weight"], 1.25)
        self.assertEqual(applied["resolved_scope"], ["generation", "editing"])
        sealed = self.jobs[response["job_id"]]["params"]["reference_pack"][
            "sealed_additional_loras"
        ][0]
        self.assertEqual(len(sealed["source_sha256"]), 64)
        self.assertNotIn(str(self.root), json.dumps(sealed))

        with mock.patch.object(
            _ModelRegistry,
            "resolve_lora_path",
            side_effect=lambda model, _filename: (
                str(editing_lora)
                if model == "qwen_image_edit_2511_20B_fp8_lightning_8step" else ""
            ),
        ), self.assertRaises(HTTPException) as raised:
            self.ns["_project_reference_request_config"](
                self._body(additional_loras=[{
                    "id": "chosen.safetensors",
                    "multiplier": 0.75,
                    "scope": "generation",
                }]),
                _Request({}),
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_model_catalog_exposes_image_output_capability(self):
        catalog = self.ns["list_models"](_Request({}))
        by_id = {item["model_type"]: item for item in catalog["models"]}
        self.assertIs(by_id["flux2_klein_9b"]["image_outputs"], True)
        self.assertIs(by_id["video_only"]["image_outputs"], False)

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

    def test_list_route_preserves_auth_and_redacts_storage_failures(self):
        reference = self.output / "route-card.png"
        Image.new("RGB", (32, 32), "purple").save(reference)
        created = self.store.create_asset(
            "project", "main", asset_id="route_card",
            name="Route card", asset_type="character",
            variants=[{
                "id": "private_variant",
                "variant_type": "reference",
                "label": "Private reference",
                "status": "kept",
                "outputs": [{
                    "source_path": reference,
                    "metadata": {
                        "private": True,
                        "explicit": True,
                        "owner_session_id": "legacy-owner-token",
                    },
                }],
            }],
        )
        response = self.ns["list_project_assets"](
            "project", _Request({}),
        )
        self.assertEqual(response["assets"][0]["id"], created["id"])
        public_policy = response["assets"][0]["variants"][0]["outputs"][0]["metadata"]
        self.assertEqual(public_policy["private"], True)
        self.assertEqual(public_policy["explicit"], True)
        self.assertNotIn("owner_session_id", public_policy)

        with mock.patch.object(self.store, "list_assets") as list_assets:
            with self.assertRaises(HTTPException) as denied:
                self.ns["list_project_assets"](
                    "project", _Request({}, session="other-session"),
                )
            self.assertEqual(denied.exception.status_code, 404)
            list_assets.assert_not_called()

        with mock.patch.object(
            self.store,
            "list_assets",
            side_effect=ProjectAssetPersistenceError(
                "private manifest path and parser detail",
            ),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                self.ns["list_project_assets"]("project", _Request({}))
        self.assertEqual(unavailable.exception.status_code, 503)
        self.assertEqual(
            unavailable.exception.detail,
            "Reference asset storage is unavailable",
        )
        self.assertNotIn("private manifest", unavailable.exception.detail)

    def test_real_child_job_is_distinct_owned_and_rewrites_prompt_sidecar(self):
        parent = {
            "id": "parent123",
            "status": "running",
            "workspace": "project",
            "out_dir": str(self.output),
            "session_id": "owner-session",
            "source_remote": True,
            "access_policy": {"private": True, "explicit": False},
            "queue_priority": 37,
            "queue_held": False,
            "hold_after_output": False,
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
        self.assertEqual(child["resource_intent"], "generation")
        self.assertEqual(child["parent_job_id"], parent["id"])
        self.assertEqual(child["queue_priority"], 37)
        self.assertFalse(child["queue_held"])
        self.assertFalse(child["hold_after_output"])
        self.assertEqual(parent["resource_intent"], "text")
        self.assertEqual(parent["resource_execution"], "standard")
        self.assertEqual(captured["kwargs"]["recovery_kind"], "studio_generation")
        sidecar = json.loads(
            (self.output / "owned-child.meta.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("SECRET_PROMPT", json.dumps(sidecar))
        self.assertEqual(sidecar["params"]["reference_pack"]["role"], "identity_front")
        self.assertEqual(sidecar["reference_parent_job_id"], parent["id"])

    def test_slow_reference_child_remains_pending_until_it_succeeds(self):
        parent = {
            "id": "slow-child-parent",
            "status": "running",
            "workspace": "project",
            "out_dir": str(self.output),
            "session_id": "owner-session",
            "source_remote": False,
            "access_policy": {"private": True, "explicit": False},
            "hold_after_output": False,
        }
        child_started = threading.Event()
        release_child = threading.Event()
        call_finished = threading.Event()
        result = {}

        def register(child, **_kwargs):
            self.jobs[child["id"]] = child

            def complete_when_released():
                child["status"] = "running"
                child_started.set()
                release_child.wait()
                output = self.output / "slow-child-output.png"
                Image.new("RGB", (64, 64), "blue").save(output)
                child.update({
                    "status": "completed",
                    "progress": 100,
                    "output_files": [output.name],
                })

            thread = threading.Thread(target=complete_when_released)
            thread.start()
            return thread

        def run_parent_call():
            try:
                result["path"] = self.real_image_job(
                    parent,
                    {
                        "model_type": "flux2_klein_9b",
                        "prompt": "synthetic",
                        "resolution": "64x64",
                    },
                    role="identity_front",
                    phase="Generating panel",
                    step=1,
                    total_steps=2,
                )
            except Exception as error:  # pragma: no cover - asserted below
                result["error"] = error
            finally:
                call_finished.set()

        self.ns["_queue_recovery_register_and_publish"] = register
        call = threading.Thread(target=run_parent_call)
        try:
            call.start()
            self.assertTrue(child_started.wait(timeout=2))
            self.assertFalse(call_finished.is_set())
            self.assertEqual(parent["status"], "running")
            release_child.set()
            call.join(timeout=2)
            self.assertFalse(call.is_alive())
            self.assertNotIn("error", result)
            self.assertEqual(
                result["path"], str(self.output / "slow-child-output.png"),
            )
        finally:
            release_child.set()
            call.join(timeout=2)

    def test_failed_reference_child_preserves_safe_parent_envelope_and_code(self):
        private_error = f"private traceback at {self.root}/secret-model.safetensors"
        captured = {}

        def register(job, *, worker=None, **_kwargs):
            job.setdefault("access_policy", {
                "private": bool(job.pop("private", False)),
                "explicit": bool(job.pop("explicit", False)),
            })
            self.jobs[job["id"]] = job
            if not job.get("parent_job_id"):
                worker(job["id"])
                return _DoneThread()

            captured["child"] = job

            def fail_child():
                job.update({
                    "status": "failed",
                    "resource_state": "released",
                    "message": private_error,
                    "error": private_error,
                    "traceback": private_error,
                    "failure_details": {
                        "code": "model_load_failed",
                        "stage": "model_load",
                        "detail": private_error,
                        "exception_type": "RuntimeError",
                        "is_oom": False,
                        "private_path": private_error,
                    },
                    "oom_info": {
                        "is_oom": False,
                        "message": private_error,
                    },
                })

            thread = threading.Thread(target=fail_child)
            thread.start()
            return thread

        self.ns["_queue_recovery_register_and_publish"] = register
        self.ns["_run_project_reference_image_job"] = self.real_image_job
        response = self._run(self._body())
        parent = self.jobs[response["job_id"]]
        child = captured["child"]

        self.assertEqual(parent["status"], "failed")
        self.assertEqual(parent["failed_child_job_id"], child["id"])
        self.assertEqual(parent["failed_child_status"], "failed")
        self.assertEqual(parent["failed_child_reason"], "model_load_failed")
        self.assertEqual(parent["failure_details"], {
            "code": "model_load_failed",
            "stage": "model_load",
            "detail": (
                "The generation model could not be loaded with the available host memory."
            ),
            "exception_type": "RuntimeError",
            "is_oom": False,
        })
        self.assertEqual(parent["error"], parent["failure_details"]["detail"])
        self.assertNotIn("oom_info", parent)
        self.assertNotIn(private_error, json.dumps(parent))
        self.assertEqual(self._assets(), [])

    def test_reference_child_cancel_and_blocked_fallbacks_are_bounded(self):
        cases = (
            (
                {
                    "id": "cancelled-child",
                    "status": "cancelled",
                    "error": "private /path/to/input.png",
                },
                "cancelled",
                "child_cancelled",
            ),
            (
                {
                    "id": "blocked-child",
                    "status": "queued",
                    "resource_state": "blocked",
                    "recovery_state": "blocked_preparation",
                    "message": "private prompt and traceback",
                },
                "blocked",
                "child_blocked",
            ),
        )
        for snapshot, expected_status, expected_reason in cases:
            with self.subTest(expected_status=expected_status):
                updates = self.ns[
                    "_project_reference_child_failure_updates"
                ](snapshot)
                self.assertEqual(
                    updates["failed_child_job_id"], snapshot["id"],
                )
                self.assertEqual(
                    updates["failed_child_status"], expected_status,
                )
                self.assertEqual(
                    updates["failed_child_reason"], expected_reason,
                )
                self.assertRegex(expected_reason, r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
                self.assertNotIn("private", json.dumps(updates))

        invalid = self.ns["_project_reference_child_failure_updates"]({
            "id": "../private-child",
            "status": "failed",
        })
        self.assertIsNone(invalid["failed_child_job_id"])

    def test_reference_parent_cancel_wins_child_failure_stamp_race(self):
        lifecycle._reset_queue_state_for_tests()
        parent = {
            "id": "cancel-wins-parent",
            "status": "running",
            "workspace": "project",
            "out_dir": str(self.output),
            "session_id": "owner-session",
            "source_remote": False,
            "access_policy": {"private": True, "explicit": False},
            "hold_after_output": False,
        }
        child_started = threading.Event()
        release_child = threading.Event()
        captured = {}
        result = {}

        def register(child, **_kwargs):
            captured["child"] = child

            def fail_after_release():
                lifecycle.try_start(child)
                child_started.set()
                release_child.wait()
                lifecycle.finish_job(
                    child,
                    "failed",
                    failure_details={
                        "code": "generation_failed",
                        "stage": "generation",
                        "detail": "private traceback",
                        "exception_type": "RuntimeError",
                        "is_oom": False,
                    },
                )

            thread = threading.Thread(target=fail_after_release)
            thread.start()
            return thread

        def run_parent_call():
            try:
                self.real_image_job(
                    parent,
                    {
                        "model_type": "flux2_klein_9b",
                        "prompt": "synthetic",
                        "resolution": "64x64",
                    },
                    role="identity_front",
                    phase="Generating panel",
                    step=1,
                    total_steps=2,
                )
            except Exception as error:
                result["error"] = str(error)

        with mock.patch.dict(self.ns, {
            "_queue_recovery_register_and_publish": register,
            "update_job": lifecycle.update_job,
            "snapshot_job": lifecycle.snapshot_job,
            "request_cancel": lifecycle.request_cancel,
            "set_job_hold": lifecycle.set_job_hold,
            "is_cancel_requested": lifecycle.is_cancel_requested,
            "_active_gen_states": {},
        }):
            call = threading.Thread(target=run_parent_call)
            try:
                call.start()
                self.assertTrue(child_started.wait(timeout=2))
                self.assertTrue(lifecycle.request_cancel(parent).changed)
                release_child.set()
                call.join(timeout=2)
                self.assertFalse(call.is_alive())
            finally:
                release_child.set()
                call.join(timeout=2)
                lifecycle._reset_queue_state_for_tests()

        self.assertEqual(parent["status"], "cancelled")
        # The child's own terminal failure may win independently, but it cannot
        # revoke the parent's earlier cancellation or stamp new diagnostics.
        self.assertEqual(captured["child"]["status"], "failed")
        self.assertNotIn("failed_child_job_id", parent)
        self.assertNotIn("failure_details", parent)
        self.assertEqual(result["error"], "reference_image_generation_failed")

    def test_reference_failed_child_relation_projects_in_status_and_list(self):
        parent = {
            "id": "reference-parent",
            "status": "failed",
            "progress": 0,
            "message": "Generation failed.",
            "output_files": [],
            "error": "Generation failed.",
            "created_at": 123.0,
            "session_id": "owner-session",
            "failed_child_job_id": "reference-child",
            "failed_child_status": "failed",
            "failed_child_reason": "model_load_failed",
        }
        child = {
            "id": "reference-child",
            "parent_job_id": parent["id"],
            "status": "failed",
            "progress": 0,
            "message": "Generation failed.",
            "output_files": [],
            "error": "Generation failed.",
            "created_at": 124.0,
            "session_id": "owner-session",
        }
        self.jobs.update({parent["id"]: parent, child["id"]: child})
        request = _Request({})
        response = types.SimpleNamespace(headers={})

        def owned(job, owner_request):
            return bool(
                job
                and job.get("session_id")
                == owner_request.state.maestro_session_id
            )

        endpoint_stubs = {
            "_set_recovery_no_store": lambda value: value.headers.update({
                "Cache-Control": "private, no-store",
            }),
            "_job_owned_by_request": owned,
            "_queue_recovery_is_blocked": lambda _job: False,
            "_job_eta_values": lambda _job: (None, None),
            "queue_position": lambda _job: None,
            "_queue_wait_reason_for_job": lambda _job: None,
            "_public_h3_boundary": lambda _value: None,
            "public_h3_offload_plan": lambda _value: None,
            "_public_resource_metadata": lambda _job: {},
            "_public_queue_residency_metadata": lambda *_args, **_kwargs: {},
            "_public_progress_telemetry": lambda _job: {},
            "job_events": lambda *_args: [],
            "queue_control_state": lambda: {},
            "_public_queue_recovery_metadata": lambda _job: {},
        }
        with mock.patch.dict(self.ns, endpoint_stubs):
            status = self.ns["get_status"](
                parent["id"], request, response,
            )
            listed = self.ns["list_jobs"](
                request, types.SimpleNamespace(headers={}),
            )

            relation = {
                "failed_child_job_id": "reference-child",
                "failed_child_status": "failed",
                "failed_child_reason": "model_load_failed",
            }
            self.assertEqual(
                {key: status[key] for key in relation}, relation,
            )
            parent_row = next(
                item for item in listed["jobs"]
                if item["job_id"] == parent["id"]
            )
            self.assertEqual(
                {key: parent_row[key] for key in relation}, relation,
            )
            self.assertEqual(
                response.headers["Cache-Control"], "private, no-store",
            )

            child["session_id"] = "other-session"
            fenced = self.ns["get_status"](
                parent["id"], request, types.SimpleNamespace(headers={}),
            )
            self.assertIsNone(fenced["failed_child_job_id"])
            self.assertIsNone(fenced["failed_child_status"])
            self.assertIsNone(fenced["failed_child_reason"])

            child["session_id"] = "owner-session"
            child["parent_job_id"] = "different-parent"
            mismatched = self.ns["get_status"](
                parent["id"], request, types.SimpleNamespace(headers={}),
            )
            self.assertIsNone(mismatched["failed_child_job_id"])
            self.assertIsNone(mismatched["failed_child_status"])
            self.assertIsNone(mismatched["failed_child_reason"])

            child["parent_job_id"] = parent["id"]
            for field, invalid in (
                ("failed_child_status", "running"),
                ("failed_child_reason", "private/path"),
                ("failed_child_reason", "a" * 65),
            ):
                with self.subTest(field=field, invalid=invalid):
                    original = parent[field]
                    parent[field] = invalid
                    malformed = self.ns["get_status"](
                        parent["id"],
                        request,
                        types.SimpleNamespace(headers={}),
                    )
                    self.assertIsNone(malformed["failed_child_job_id"])
                    self.assertIsNone(malformed["failed_child_status"])
                    self.assertIsNone(malformed["failed_child_reason"])
                    parent[field] = original

    def test_reference_output_boundary_holds_then_resumes_without_work(self):
        parent = {
            "id": "boundary-parent",
            "status": "running",
            "hold_after_output": True,
            "cancel_requested": False,
        }
        result = []
        thread = threading.Thread(target=lambda: result.append(
            self.ns["_project_reference_wait_at_output_boundary"](
                parent, "sheet",
            )
        ))
        thread.start()
        deadline = time.time() + 2
        while parent.get("phase") != "Reference generation held" and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(thread.is_alive())
        self.assertEqual(parent["resource_intent"], "text")
        self.assertEqual(parent["resource_state"], "queued")
        parent["hold_after_output"] = False
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(parent["resource_state"], "released")

    def test_child_targeted_hold_is_not_overwritten_by_parent_projection(self):
        parent = {
            "id": "child-hold-parent",
            "status": "running",
            "workspace": "project",
            "out_dir": str(self.output),
            "session_id": "owner-session",
            "source_remote": False,
            "access_policy": {"private": True, "explicit": False},
            "hold_after_output": False,
        }
        observed = {}

        def register(child, **_kwargs):
            # Represents a hold routed by the UI to the authoritative child.
            child["queue_held"] = True
            child["hold_after_output"] = True

            def complete_later():
                time.sleep(0.2)
                observed["queue_held"] = child["queue_held"]
                observed["hold_after_output"] = child["hold_after_output"]
                output = self.output / "child-held-output.png"
                Image.new("RGB", (64, 64), "blue").save(output)
                child.update({
                    "status": "completed",
                    "progress": 100,
                    "output_files": [output.name],
                })

            thread = threading.Thread(target=complete_later)
            thread.start()
            return thread

        self.ns["_queue_recovery_register_and_publish"] = register
        self.real_image_job(
            parent,
            {
                "model_type": "flux2_klein_9b",
                "prompt": "synthetic",
                "resolution": "64x64",
            },
            role="identity_front",
            phase="Generating panel",
            step=1,
            total_steps=2,
        )
        self.assertEqual(observed, {
            "queue_held": True,
            "hold_after_output": True,
        })

    def test_real_child_yield_stays_visible_until_explicit_child_resume(self):
        lifecycle._reset_queue_state_for_tests()
        parent = {
            "id": "yield-parent",
            "status": "running",
            "workspace": "project",
            "out_dir": str(self.output),
            "session_id": "owner-session",
            "source_remote": False,
            "access_policy": {"private": True, "explicit": False},
            "hold_after_output": True,
        }
        generation_lock = threading.Lock()
        captured = {}

        def register(child, **_kwargs):
            captured["child"] = child
            child["access_policy"] = dict(parent["access_policy"])

            def run_child():
                with lifecycle.generation_slot(
                    generation_lock, child, poll_interval=0.005,
                ) as acquired:
                    if not acquired or not lifecycle.try_start(
                        child,
                        generation_lock=generation_lock,
                        poll_interval=0.005,
                    ):
                        return
                    output = self.output / "yield-child-output.png"
                    Image.new("RGB", (64, 64), "blue").save(output)
                    lifecycle.update_job(
                        child, progress=100, output_files=[output.name],
                    )
                    captured["yielded"] = (
                        lifecycle.yield_generation_slot_after_output(
                            generation_lock, child, poll_interval=0.005,
                        )
                    )
                    lifecycle.finish_job(
                        child, "completed", progress=100,
                        output_files=[output.name],
                    )

            thread = threading.Thread(target=run_child)
            thread.start()
            return thread

        self.ns["_queue_recovery_register_and_publish"] = register
        result = {}

        def run_parent_call():
            try:
                result["path"] = self.real_image_job(
                    parent,
                    {
                        "model_type": "flux2_klein_9b",
                        "prompt": "synthetic",
                        "resolution": "64x64",
                    },
                    role="identity_front",
                    phase="Generating panel",
                    step=1,
                    total_steps=2,
                )
            except Exception as error:  # pragma: no cover - asserted below
                result["error"] = error

        call = threading.Thread(target=run_parent_call)
        try:
            call.start()
            deadline = time.time() + 2
            while time.time() < deadline:
                child = captured.get("child")
                if (
                    child is not None
                    and child.get("status") == "queued"
                    and child.get("queue_held") is True
                    and parent.get("hold_after_output") is False
                ):
                    break
                time.sleep(0.01)
            child = captured["child"]
            self.assertEqual(child["status"], "queued")
            self.assertTrue(child["queue_held"])
            self.assertEqual(child["parent_job_id"], parent["id"])
            self.assertFalse(parent["hold_after_output"])
            self.assertTrue(call.is_alive())
            self.assertEqual(lifecycle.set_job_hold(child, False), "resumed")
            call.join(timeout=2)
            self.assertFalse(call.is_alive())
            self.assertNotIn("error", result)
            self.assertTrue(captured["yielded"])
            self.assertEqual(
                result["path"], str(self.output / "yield-child-output.png"),
            )
        finally:
            if call.is_alive() and captured.get("child") is not None:
                lifecycle.set_job_hold(captured["child"], False)
                call.join(timeout=2)
            lifecycle._reset_queue_state_for_tests()

    def test_candidate_boundary_stops_before_the_next_candidate(self):
        boundaries = []

        def stop_at_candidate(_parent, boundary):
            boundaries.append(boundary)
            return boundary != "candidate"

        self.ns["_project_reference_wait_at_output_boundary"] = stop_at_candidate
        response = self._run(self._body(candidate_count=2))
        job = self.jobs[response["job_id"]]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.calls and len(self.calls), 3)
        self.assertEqual(boundaries.count("candidate"), 1)
        self.assertEqual(self._assets(), [])

    def test_reference_admission_persistence_failures_block_and_never_run_work(self):
        for failed_transition in ("queue_register", "start"):
            with self.subTest(failed_transition=failed_transition):
                lifecycle._reset_queue_state_for_tests()
                captured = {}
                transitions = []
                failed = {"value": False}

                def durable(proposal):
                    transitions.append(proposal.name)
                    if proposal.name == failed_transition and not failed["value"]:
                        failed["value"] = True
                        raise RuntimeError("synthetic persistence failure")

                generation_lock = threading.Lock()

                def register(job, *, worker=None, **_kwargs):
                    self.jobs[job["id"]] = job
                    if not job.get("parent_job_id"):
                        captured["worker"] = worker
                        return _DoneThread()

                    def admit_child():
                        try:
                            with lifecycle.generation_slot(
                                generation_lock, job,
                            ) as acquired:
                                if not acquired or not lifecycle.try_start(
                                    job,
                                    generation_lock=generation_lock,
                                    message="Generating reference child",
                                ):
                                    return
                                self.calls.append({"unexpected": True})
                        except RuntimeError:
                            lifecycle.block_resource_admission_failure(job)

                    thread = threading.Thread(target=admit_child)
                    thread.start()
                    return thread

                lifecycle.configure_durability_hook(durable)
                try:
                    with mock.patch.dict(self.ns, {
                        "_queue_recovery_register_and_publish": register,
                        "_run_project_reference_image_job": self.real_image_job,
                        "_gen_lock": generation_lock,
                        "block_resource_admission_failure": (
                            lifecycle.block_resource_admission_failure
                        ),
                    }):
                        response = self._run(self._body())
                        parent = self.jobs[response["job_id"]]
                        self.assertEqual(parent["resource_intent"], "text")
                        captured["worker"](response["job_id"])
                finally:
                    lifecycle._reset_queue_state_for_tests()

                self.assertTrue(failed["value"])
                self.assertEqual(parent["resource_intent"], "text")
                self.assertEqual(parent["status"], "failed")
                child = next(
                    job for job in self.jobs.values()
                    if job.get("parent_job_id") == parent["id"]
                )
                self.assertEqual(child["resource_intent"], "generation")
                self.assertEqual(child["status"], "queued")
                self.assertTrue(child["queue_held"])
                self.assertEqual(
                    child["recovery_state"], "blocked_preparation",
                )
                self.assertEqual(
                    child["phase"], "resource_admission_failed",
                )
                self.assertIn("resubmit", child["message"].casefold())
                self.assertEqual(self.calls, [])
                self.assertEqual(self._assets(), [])
                self.assertIn("resource_admission_blocked", transitions)
                self.assertTrue(generation_lock.acquire(blocking=False))
                generation_lock.release()


if __name__ == "__main__":
    unittest.main()
