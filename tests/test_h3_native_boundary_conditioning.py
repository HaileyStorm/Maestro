"""CPU-only regressions for opt-in MiniMax H3 native AV boundaries."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import torch

from models.minimax_h3.packing import (
    MINIMAX_H3_AUDIO_TAG,
    MINIMAX_H3_TEXT_TAG,
    MINIMAX_H3_VIDEO_TAG,
    MiniMaxH3PreparedReference,
    build_packed_sequence,
    build_ref2va_packed_sequence,
    build_row_timesteps,
)
from services.h3_boundary_policy import (
    H3_FL2VA_MODELS,
    H3_NATIVE_HISTORY_FRAMES,
    H3_NATIVE_OVERLAP_FRAMES,
    attest_boundary_file,
    decide_h3_boundary,
    generation_frames_for_segment,
    verify_boundary_file,
)
from services.sample_campaign_coordinator import SAMPLE_JOB_KIND


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _AdmissionRequest:
    def __init__(self, body: dict):
        self._body = body
        self.state = types.SimpleNamespace(maestro_remote=False)

    async def json(self) -> dict:
        return copy.deepcopy(self._body)


def _load_functions(path: Path, names: set[str], namespace: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = []
    for node in tree.body:
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            continue
        node = copy.deepcopy(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
        nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


def _load_nested_function(path: Path, name: str, namespace: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            selected = node
            break
    if selected is None:
        raise AssertionError(f"Missing nested function: {name}")
    module = ast.Module(body=[selected], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def _native_admission_namespace():
    """Load public/preparation/worker guards with later work forbidden."""
    events: list[str] = []
    project_access_permissions: list[str] = []
    worker_admissions: list[str] = []
    legal_admissions: list[tuple[str, ...]] = []

    def forbidden(name: str):
        def reject(*args, **kwargs):
            events.append(name)
            raise AssertionError(f"{name} ran after native-boundary rejection")

        return reject

    failed_preparations: list[dict] = []
    finished_jobs: list[tuple] = []

    def require_project_generation(request, workspace, *, permission):
        if permission != "project.generate":
            raise AssertionError(f"unexpected project permission: {permission}")
        project_access_permissions.append(permission)
        return "/tmp/project"

    namespace = {
        "Request": _AdmissionRequest,
        "_GenerationPreparationRequest": object,
        "HTTPException": _HTTPException,
        "copy": copy,
        "hashlib": __import__("hashlib"),
        "os": os,
        "time": types.SimpleNamespace(time=lambda: 1.0),
        "traceback": __import__("traceback"),
        "torch": types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False),
        ),
        "wgp": types.SimpleNamespace(get_model_def=lambda model_type: {}),
        "_H3_LONG_STUDIO_MODELS": {
            "minimax_h3", "minimax_h3_ref2va",
        },
        "_H3_TURBO_BENCHMARK_REFERENCE_BYTES": 0,
        "_H3_TURBO_BENCHMARK_REFERENCE_SHA256": "",
        "_SAMPLE_CAMPAIGN_JOB_KIND": SAMPLE_JOB_KIND,
        "_get_active_workspace": lambda: "default",
        "_require_project_access": require_project_generation,
        "_project_access_permissions": project_access_permissions,
        "_reject_client_h3_internal_state": lambda body: None,
        "_reject_client_h3_turbo_validation_controls": lambda body: None,
        "_authorize_generation_media_inputs": (
            lambda request, body, workspace: None
        ),
        "_require_remote_visible_models": lambda request, models: None,
        "_require_h3_legal_execution": lambda model_types: legal_admissions.append(
            tuple(str(model_type) for model_type in model_types)
        ),
        "_h3_legal_admissions": legal_admissions,
        "_apply_fresh_h3_role_defaults": lambda body, request: None,
        "_apply_h3_adaptive_checkpoint": lambda body: None,
        "_resolve_h3_style_workflow_request": lambda body: None,
        "_apply_h3_style_workflow_to_request": lambda body: None,
        "_normalize_video_prompt_type": lambda body: None,
        "_normalize_image_prompt_type": lambda body: None,
        "_jobs": {},
        "_credit_prepare_admission": lambda job: None,
        "_credit_prepare_dispatch": lambda job: None,
        "_credit_block_runtime_error": lambda job: None,
        "_CREDIT_INTERNAL_PARAMS": frozenset(),
        "CreditRuntimeError": ValueError,
        "EntitlementError": ValueError,
        "is_cancel_requested": lambda job: False,
        "update_preparation_job": lambda job, **updates: True,
        "fail_preparation": (
            lambda job, **updates: failed_preparations.append(dict(updates))
        ),
        "generation_slot": (
            lambda lock, job, **kwargs: contextlib.nullcontext(True)
        ),
        "_WgpNativeGpuExecutionSlot": (
            lambda acquired, **kwargs: contextlib.nullcontext(bool(acquired))
        ),
        "_generation_native_gpu_cancel_checkpoint": lambda job: None,
        "_credit_admission_evaluations": {},
        "_gen_lock": object(),
        "_active_gen_states": {"other-worker": {}},
        "_stamp_requested_generation_residency": lambda job, **kwargs: None,
        "try_start": lambda job, **kwargs: (
            worker_admissions.append(str(job.get("id") or "")) or True
        ),
        "_queue_recovery_delivery_pending": lambda job: None,
        "_director_image_role_wire_mode": lambda body: "legacy",
        "_require_h3_offload_plan_parity": lambda job: None,
        "_require_job_model_recipe_terms": lambda job: None,
        "_apply_per_job_coefficient": lambda job: None,
        "finish_job": (
            lambda *args, **kwargs: finished_jobs.append((args, kwargs))
        ),
        "_lifecycle_finish_job": (
            lambda *args, **kwargs: (
                finished_jobs.append((args, kwargs)) or True
            )
        ),
        "_restore_base_coefficient": lambda: None,
    }
    for name in (
        "_validate_h3_sampling_steps",
        "_validate_h3_explicit_multiclip_request",
        "_prepare_h3_long_studio_request",
        "_require_h3_acceleration_available",
        "_h3_estimate_context",
        "_h3_generation_requirements",
        "write_sealed_request_manifest",
        "complete_preparation",
        "_start_generation_worker",
        "_ensure_versioned_model_current",
        "_ensure_h3_effective_models_current",
        "register_abort_state",
    ):
        namespace[name] = forbidden(name)
    _load_functions(
        APP / "launch.py",
        {
            "_trusted_h3_prepared_plan",
            "_h3_job_model_types",
            "_require_job_runtime_model_admission",
            "_require_h3_native_boundary_experimental",
            "_plan_generation_submission",
            "preview_generation_plan",
            "_run_generation_preparation",
            "_run_generation",
        },
        namespace,
    )
    # Pure prerequisite checks are outside this test's native-boundary focus.
    # Keep them successful so the experimental boundary remains the first
    # deliberately rejected worker condition.
    namespace["_require_h3_offload_plan_parity"] = lambda job: None
    namespace["_require_job_model_recipe_terms"] = lambda job: None
    namespace["_apply_h3_adaptive_checkpoint"] = lambda body: None
    return (
        namespace,
        events,
        failed_preparations,
        finished_jobs,
        worker_admissions,
    )


def _load_handler():
    path = APP / "models" / "minimax_h3" / "minimax_h3_handler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("_")
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            "_hf_url", "_is_reference_mode",
        }:
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "family_handler":
            selected.append(node)
    namespace = {
        "os": os,
        "torch": types.SimpleNamespace(bfloat16="bfloat16"),
    }
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["family_handler"]


class NativeBoundaryPolicyTests(unittest.TestCase):
    def test_full_boundary_semantic_table_and_exact_flavor_memory(self):
        for flavor in sorted(H3_FL2VA_MODELS):
            for boundary in ("continuous", "precut", "cut", "transition"):
                for semantic in (False, True):
                    with self.subTest(
                        flavor=flavor, boundary=boundary, semantic=semantic,
                    ):
                        decision = decide_h3_boundary(
                            segment_index=1,
                            boundary_type=boundary,
                            semantic_references=semantic,
                            preferred_fl2va_model=flavor,
                        )
                        if boundary in {"continuous", "precut"}:
                            self.assertEqual(
                                decision.model_type,
                                "minimax_h3_ref2va" if semantic else flavor,
                            )
                            self.assertTrue(decision.temporal_overlap)
                            self.assertEqual(
                                (decision.overlap_frames, decision.discard_frames),
                                (18, 17),
                            )
                            self.assertFalse(decision.predecessor_semantic_still)
                        else:
                            self.assertEqual(decision.model_type, "minimax_h3_ref2va")
                            self.assertFalse(decision.temporal_overlap)
                            self.assertEqual(decision.overlap_frames, 0)
                            self.assertEqual(decision.discard_frames, 0)
                            self.assertEqual(
                                decision.predecessor_semantic_still, not semantic,
                            )

    def test_18_to_17_generation_math(self):
        decision = decide_h3_boundary(
            segment_index=1,
            boundary_type="continuous",
            semantic_references=False,
            preferred_fl2va_model="minimax_h3",
        )
        self.assertEqual(H3_NATIVE_OVERLAP_FRAMES, 18)
        self.assertEqual(H3_NATIVE_HISTORY_FRAMES, 17)
        self.assertEqual(generation_frames_for_segment(175, decision), 192)
        self.assertEqual((192 - 5) % 17, 0)

    def test_recovery_file_attestation_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unit-boundary.mp4"
            path.write_bytes(b"private synthetic boundary")
            descriptor = attest_boundary_file(path)
            self.assertEqual(verify_boundary_file(descriptor), str(path.resolve()))
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash attestation"):
                verify_boundary_file(descriptor)


class NativeBoundaryPackingTests(unittest.TestCase):
    def test_runtime_consumes_explicit_boundary_waveform_and_combines_prompt_media(self):
        source = (
            APP / "models" / "minimax_h3" / "minimax_h3_main.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "boundary_waveform = self._coerce_waveform(\n"
            "                input_waveform,\n"
            "                input_waveform_sample_rate,",
            source,
        )
        self.assertIn(
            "prompt_presentation = [\n"
            "                {",
            source,
        )
        self.assertIn("] + prompt_presentation", source)

    def test_fl2va_packing_preserves_float64_clock_tags_and_frozen_targets(self):
        text_tags = torch.tensor([MINIMAX_H3_TEXT_TAG, MINIMAX_H3_TEXT_TAG])
        layout = build_packed_sequence(
            text_tags,
            num_latent_frames=2,
            latent_height=4,
            latent_width=4,
            num_audio_latents=2,
            patch_size=(1, 2, 2),
            keyframe_anchors=(
                ("history", 5), "first", "last", ("frame", 1, 3),
            ),
            audio_condition_anchors=(("history", 3), ("first", 1)),
            target_condition_audio_latents=1,
            target_condition_video_frames=1,
        )
        self.assertEqual(layout.position_ids.dtype, torch.float64)
        self.assertEqual(layout.sequence_length, 54)
        self.assertEqual(layout.num_condition_video_rows, 32)
        self.assertEqual(layout.num_condition_audio_rows, 8)
        self.assertTrue(torch.all(layout.token_tags[:2] == MINIMAX_H3_TEXT_TAG))
        self.assertTrue(
            torch.all(layout.token_tags[layout.video_indices] == MINIMAX_H3_VIDEO_TAG)
        )
        self.assertTrue(
            torch.all(layout.token_tags[layout.audio_indices] == MINIMAX_H3_AUDIO_TAG)
        )
        target_origin = 2.0 + 5.0 / 3.0 + 4 * 20.0 / 3.0
        rows_per_frame = 4
        first_row = 2 + 5 * rows_per_frame
        last_row = first_row + rows_per_frame
        frame_row = last_row + rows_per_frame
        self.assertAlmostEqual(layout.position_ids[first_row, 0].item(), target_origin)
        self.assertAlmostEqual(
            layout.position_ids[last_row, 0].item(),
            target_origin + 20.0 / 3.0,
        )
        self.assertAlmostEqual(
            layout.position_ids[frame_row, 0].item(), target_origin + 5.0,
        )
        condition_audio_times = layout.position_ids[34:42, 0].tolist()
        self.assertEqual(condition_audio_times[:3], [2.0, 3.0, 4.0])
        self.assertEqual(condition_audio_times[3:6], [2.0, 3.0, 4.0])
        self.assertAlmostEqual(condition_audio_times[6], target_origin)
        self.assertAlmostEqual(condition_audio_times[7], target_origin)

        timesteps, inverse = build_row_timesteps(
            layout, 900, 800, 999, 998, target_condition_timestep=1,
        )
        row_values = timesteps[inverse]
        self.assertEqual(
            int((row_values[layout.video_indices] == 1).sum()), rows_per_frame,
        )
        self.assertEqual(
            int((row_values[layout.audio_indices] == 998).sum()), 10,
        )

    def test_ref2va_keeps_keyframes_before_semantic_reference_rows(self):
        refs = [
            MiniMaxH3PreparedReference(
                "image", latent_height=4, latent_width=4,
            ),
            MiniMaxH3PreparedReference(
                "video", num_latent_frames=2, latent_height=4,
                latent_width=4, num_audio_latents=2,
            ),
        ]
        layout = build_ref2va_packed_sequence(
            torch.tensor([MINIMAX_H3_TEXT_TAG]), refs,
            num_latent_frames=2, latent_height=4, latent_width=4,
            num_audio_latents=2, patch_size=(1, 2, 2),
            keyframe_anchors=(("history", 5), "first"),
            audio_condition_anchors=(("history", 3), ("first", 1)),
        )
        self.assertEqual(layout.position_ids.dtype, torch.float64)
        self.assertEqual(layout.num_condition_video_rows, 36)
        self.assertEqual(layout.num_condition_audio_rows, 12)
        # Modality-specific row order is keyframes, semantic refs, target.
        self.assertEqual(layout.video_indices[:24].tolist(), list(range(1, 25)))
        self.assertEqual(layout.audio_indices[:8].tolist(), list(range(25, 33)))


class NativeBoundaryPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launch_path = APP / "launch.py"
        cls.studio = _load_functions(
            launch_path,
            {"_h3_preferred_fl2va_model", "_plan_h3_adaptive_models"},
            {
                "_H3_LONG_STUDIO_MODELS": {
                    "minimax_h3", "minimax_h3_w4a8_fl2va",
                    "minimax_h3_pinkcherry_fl2va", "minimax_h3_ref2va",
                },
                "_H3_FL2VA_MODELS": set(H3_FL2VA_MODELS),
                "_H3_BASE_FL2VA_MODEL": "minimax_h3",
                "_H3_EXPLICIT_FL2VA_MODEL": "minimax_h3_pinkcherry_fl2va",
                "_H3_W4A8_FL2VA_MODEL": "minimax_h3_w4a8_fl2va",
            },
        )
        director_path = APP / "services" / "director_pipeline.py"
        cls.director = _load_functions(
            director_path,
            {
                "_director_h3_preferred_fl2va",
                "_director_h3_segment_models",
                "_director_merge_h3_keyframe_refs",
            },
            {
                "_H3_FL2VA_MODELS": set(H3_FL2VA_MODELS),
                "_H3_BASE_FL2VA_MODEL": "minimax_h3",
                "_H3_REF2VA_MODEL": "minimax_h3_ref2va",
                "_H3_VIDEO_MODELS": set(H3_FL2VA_MODELS) | {
                    "minimax_h3_ref2va",
                },
            },
        )

    def test_native_policy_is_re_evaluated_and_matches_studio_director(self):
        boundaries = [
            {"type": "continuous"}, {"type": "cut"},
            {"type": "precut"}, {"type": "transition"},
        ]
        for flavor in sorted(H3_FL2VA_MODELS):
            for semantic in (False, True):
                with self.subTest(flavor=flavor, semantic=semantic):
                    body = {
                        "model_type": flavor,
                        "_h3_requested_checkpoint": flavor,
                        "h3_native_boundary_conditioning": True,
                        "image_refs": ["semantic.png"] if semantic else [],
                    }
                    studio = self.studio["_plan_h3_adaptive_models"](
                        body,
                        clip_count=5,
                        clip_boundaries=boundaries,
                        first_anchor="edge.png",
                        last_anchor="edge.png",
                    )
                    director = self.director["_director_h3_segment_models"](
                        body,
                        selected=flavor,
                        boundaries=boundaries,
                        segment_count=5,
                        first_anchor="edge.png",
                        last_anchor="edge.png",
                        semantic_references=semantic,
                    )
                    keys = {
                        "model_type", "temporal_overlap",
                        "predecessor_semantic_still", "overlap_frames",
                        "discard_frames",
                    }
                    self.assertEqual(
                        [{key: item.get(key) for key in keys} for item in studio],
                        [{key: item.get(key) for key in keys} for item in director],
                    )
                    # A cut does not create a sticky semantic run: the next
                    # precut boundary is independently back on the exact FL
                    # flavor when no user semantic reference exists.
                    if not semantic:
                        self.assertEqual(studio[2]["model_type"], "minimax_h3_ref2va")
                        self.assertEqual(studio[3]["model_type"], flavor)

    def test_flag_off_retains_legacy_sticky_behavior(self):
        plan = self.studio["_plan_h3_adaptive_models"](
            {"model_type": "minimax_h3_w4a8_fl2va"},
            clip_count=3,
            clip_boundaries=[{"type": "cut"}, {"type": "continuous"}],
            first_anchor=None,
            last_anchor=None,
        )
        self.assertEqual(
            [item["model_type"] for item in plan],
            [
                "minimax_h3_w4a8_fl2va", "minimax_h3_ref2va",
                "minimax_h3_ref2va",
            ],
        )

    def test_flag_off_semantic_edge_routes_first_fl_then_ref(self):
        plan = self.studio["_plan_h3_adaptive_models"](
            {
                "model_type": "minimax_h3_ref2va",
                "image_refs": ["semantic.png"],
                "h3_native_boundary_conditioning": False,
            },
            clip_count=2,
            clip_boundaries=[{"type": "continuous"}],
            first_anchor="procedural.png",
            last_anchor=None,
        )
        self.assertEqual(
            [item["model_type"] for item in plan],
            ["minimax_h3", "minimax_h3_ref2va"],
        )
        launch_source = (APP / "launch.py").read_text(encoding="utf-8")
        self.assertIn(
            'h3_longform.get("original_image_start") if i == 0 else None',
            launch_source,
        )
        self.assertIn(
            'if segment_model == "minimax_h3_ref2va":\n'
            '                        if native_h3_boundaries:',
            launch_source,
        )
        self.assertIn(
            'else:\n'
            '                            clip_params["image_start"] = None\n'
            '                            clip_params["image_end"] = None',
            launch_source,
        )

    def test_legacy_ref_prompt_type_is_t_and_native_capability_is_gated(self):
        handler = _load_handler()
        with mock.patch.dict(os.environ, {}, clear=True):
            model_def = handler.query_model_def("minimax_h3_ref2va", {})
        self.assertEqual(model_def["image_prompt_types_allowed"], "T")
        self.assertEqual(
            model_def["minimax_h3_native_boundary_image_prompt_types_allowed"],
            "T",
        )
        self.assertFalse(model_def["h3_native_boundary_conditioning"])
        self.assertIn(
            "FL2VA checkpoint",
            handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, {"image_start": object()},
            ),
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            disabled = handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, {
                    "image_start": object(),
                    "h3_native_boundary_conditioning": True,
                },
            )
        self.assertIn("disabled until", disabled)
        with mock.patch.dict(
            os.environ,
            {"MAESTRO_H3_NATIVE_BOUNDARY_EXPERIMENTAL": "1"},
            clear=True,
        ):
            experimental_def = handler.query_model_def(
                "minimax_h3_ref2va", {},
            )
            self.assertTrue(
                experimental_def["h3_native_boundary_conditioning"],
            )
            self.assertEqual(
                experimental_def[
                    "minimax_h3_native_boundary_image_prompt_types_allowed"
                ],
                "TSE",
            )
            self.assertIsNone(handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, {
                    "image_start": object(),
                    "h3_native_boundary_conditioning": True,
                },
            ))
        wgp_source = (APP / "wgp.py").read_text(encoding="utf-8")
        self.assertIn(
            'base_model_type == "minimax_h3_ref2va"\n'
            '            and ui_defaults.get("h3_native_boundary_conditioning") is True',
            wgp_source,
        )
        self.assertIn(
            '"minimax_h3_native_boundary_image_prompt_types_allowed"',
            wgp_source,
        )

    def test_native_boundary_rejects_public_planning_before_work_and_worker_before_model(self):
        helpers = _load_functions(
            APP / "launch.py",
            {
                "_require_h3_native_boundary_experimental",
                "_generation_tasks_succeeded",
            },
            {
                "os": os,
                "_H3_LONG_STUDIO_MODELS": {
                    "minimax_h3", "minimax_h3_ref2va",
                },
            },
        )
        require = helpers["_require_h3_native_boundary_experimental"]
        experimental = {
            "model_type": "minimax_h3",
            "h3_native_boundary_conditioning": True,
        }
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                require(experimental)
            require({"model_type": "minimax_h3"})
            require({
                "model_type": "ltx2_19b",
                "h3_native_boundary_conditioning": True,
            })
        with mock.patch.dict(
            os.environ,
            {"MAESTRO_H3_NATIVE_BOUNDARY_EXPERIMENTAL": "1"},
            clear=True,
        ):
            require(experimental)

        tree = ast.parse(
            (APP / "launch.py").read_text(encoding="utf-8"),
        )
        required_guard_callers = {
            "_plan_generation_submission", "_run_generation",
        }
        seen_guard_callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in required_guard_callers:
                continue
            if any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_require_h3_native_boundary_experimental"
                for call in ast.walk(node)
            ):
                seen_guard_callers.add(node.name)
        self.assertEqual(seen_guard_callers, required_guard_callers)

        shared_plan_callers = {
            "preview_generation_plan", "_run_generation_preparation", "generate",
        }
        seen_plan_callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in shared_plan_callers:
                continue
            if any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_plan_generation_submission"
                for call in ast.walk(node)
            ):
                seen_plan_callers.add(node.name)
        self.assertEqual(seen_plan_callers, shared_plan_callers)

        admission, forbidden_events, failed, finished, worker_admissions = (
            _native_admission_namespace()
        )
        rejected_body = {
            "workspace": "default",
            "model_type": "minimax_h3",
            "prompt": "model-free admission probe",
            "image_mode": 2,
            "h3_native_boundary_conditioning": True,
        }
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(_HTTPException) as preview_error:
                asyncio.run(admission["preview_generation_plan"](
                    _AdmissionRequest(rejected_body),
                ))
        self.assertEqual(preview_error.exception.status_code, 400)
        self.assertIn("unavailable", str(preview_error.exception.detail))
        self.assertEqual(
            admission["_project_access_permissions"],
            ["project.generate"],
        )
        self.assertEqual(admission["_h3_legal_admissions"], [("minimax_h3",)])
        self.assertEqual(forbidden_events, [])
        self.assertEqual(worker_admissions, [])

        preparation_job = {
            "id": "native-preparation",
            "params": copy.deepcopy(rejected_body),
            "workspace": "default",
            "out_dir": "/tmp/project",
        }
        admission["_jobs"] = {"native-preparation": preparation_job}
        with mock.patch.dict(os.environ, {}, clear=True):
            admission["_run_generation_preparation"](
                "native-preparation",
                _AdmissionRequest({}),
                enhance=False,
            )
        self.assertEqual(forbidden_events, [])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["phase"], "preparation_failed")
        self.assertEqual(worker_admissions, [])

        worker_job = {
            "id": "native-worker",
            "params": copy.deepcopy(rejected_body),
            "status": "queued",
            "out_dir": "",
        }
        admission["_jobs"] = {"native-worker": worker_job}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(admission["_run_generation"]("native-worker"))
        self.assertEqual(worker_admissions, ["native-worker"])
        self.assertEqual(forbidden_events, [])
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][0][1], "failed")
        self.assertIn("unavailable", finished[0][1]["error"])

        succeeded = helpers["_generation_tasks_succeeded"]
        self.assertTrue(succeeded(
            completed=2, skipped=0, total_tasks=2, cancelled=False,
        ))
        self.assertFalse(succeeded(
            completed=0, skipped=2, total_tasks=2, cancelled=False,
        ))
        self.assertFalse(succeeded(
            completed=0, skipped=0, total_tasks=0, cancelled=False,
        ))
        self.assertFalse(succeeded(
            completed=1, skipped=0, total_tasks=2, cancelled=False,
        ))

    def test_native_planning_preserves_turbo_sage_and_profile_settings(self):
        body = {
            "model_type": "minimax_h3_w4a8_fl2va",
            "h3_native_boundary_conditioning": True,
            "custom_settings": {
                "h3_attention_engine": "sage2",
                "h3_turbo_profile": "h3_turbo_v4",
                "h3_sol_tau": 0.75,
            },
            "num_inference_steps": 8,
            "activated_loras": ["managed-turbo.safetensors"],
            "loras_multipliers": "1.0",
        }
        preserved = {
            key: body[key]
            for key in (
                "custom_settings", "num_inference_steps",
                "activated_loras", "loras_multipliers",
            )
        }
        self.studio["_plan_h3_adaptive_models"](
            body,
            clip_count=2,
            clip_boundaries=[{"type": "continuous"}],
            first_anchor=None,
            last_anchor=None,
        )
        self.assertEqual(
            {key: body[key] for key in preserved}, preserved,
        )

    def test_native_override_retains_overlap_and_applies_explicit_ref_drop(self):
        body = {
            "model_type": "minimax_h3_w4a8_fl2va",
            "h3_native_boundary_conditioning": True,
            "image_refs": ["semantic.png"],
            "h3_segment_overrides": [
                {
                    "model_type": "minimax_h3_w4a8_fl2va",
                    "drop_semantic_refs": True,
                },
                {
                    "model_type": "minimax_h3_w4a8_fl2va",
                    "drop_semantic_refs": True,
                },
            ],
        }
        studio = self.studio["_plan_h3_adaptive_models"](
            body, clip_count=2,
            clip_boundaries=[{"type": "continuous"}],
            first_anchor=None, last_anchor=None,
        )
        director = self.director["_director_h3_segment_models"](
            body, selected="minimax_h3_w4a8_fl2va",
            boundaries=[{"type": "continuous"}], segment_count=2,
            first_anchor=None, last_anchor=None, semantic_references=True,
        )
        for plan in (studio, director):
            self.assertTrue(plan[1]["temporal_overlap"])
            self.assertEqual(plan[1]["discard_frames"], 17)
            self.assertTrue(plan[1]["drop_semantic_refs"])
        cut_body = dict(body)
        cut_body["h3_segment_overrides"] = [None, body["h3_segment_overrides"][1]]
        with self.assertRaisesRegex(ValueError, "native H3 boundary policy"):
            self.studio["_plan_h3_adaptive_models"](
                cut_body, clip_count=2,
                clip_boundaries=[{"type": "cut"}],
                first_anchor=None, last_anchor=None,
            )

    def test_audio_guide_is_semantic_in_native_studio_and_director(self):
        body = {
            "model_type": "minimax_h3",
            "h3_native_boundary_conditioning": True,
            "audio_guide": "voice.wav",
        }
        studio = self.studio["_plan_h3_adaptive_models"](
            body, clip_count=2,
            clip_boundaries=[{"type": "continuous"}],
            first_anchor=None, last_anchor=None,
        )
        director = self.director["_director_h3_segment_models"](
            body, selected="minimax_h3",
            boundaries=[{"type": "continuous"}], segment_count=2,
            first_anchor=None, last_anchor=None, semantic_references=True,
        )
        self.assertEqual(
            [item["model_type"] for item in studio],
            ["minimax_h3_ref2va", "minimax_h3_ref2va"],
        )
        self.assertEqual(
            [item["model_type"] for item in studio],
            [item["model_type"] for item in director],
        )

    def test_director_combines_global_and_per_clip_keyframes(self):
        merge = self.director["_director_merge_h3_keyframe_refs"]
        self.assertEqual(
            merge(
                ["global.png", "shared.png"],
                [["local-a.png", "shared.png"], ["local-b.png"]],
            ),
            ["global.png", "shared.png", "local-a.png", "local-b.png"],
        )


class NativeBoundaryRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class RecoveryError(RuntimeError):
            pass

        cls.functions = _load_functions(
            APP / "launch.py",
            {
                "_h3_dependency_closed_recovery_units",
                "_h3_segment_recovery_settings",
                "_merge_h3_ref2va_keyframes",
            },
            {"QueueRecoveryRuntimeError": RecoveryError},
        )

    def test_restart_dependency_closure_rejects_tampered_predecessor_chain(self):
        close = self.functions["_h3_dependency_closed_recovery_units"]
        segment0 = {
            "unit_id": "segment-0", "kind": "h3_segment",
            "dependencies": [], "artifacts": [{"sha256": "old-0"}],
            "continuation": {"sha256": "continuation-0"},
            "settings": {"discard_prefix_frames": 0},
        }
        segment1 = {
            "unit_id": "segment-1", "kind": "h3_segment",
            "dependencies": ["segment-0"],
            "artifacts": [{"sha256": "segment-1-bytes"}],
            "settings": {
                "discard_prefix_frames": 17,
                "predecessor_artifact_hashes": ["old-0"],
                "predecessor_continuation_sha256": "continuation-0",
            },
        }
        concat = {
            "unit_id": "concat", "kind": "h3_concat",
            "dependencies": ["segment-0", "segment-1"],
            "artifacts": [{"sha256": "final"}],
            "settings": {
                "component_hashes": ["old-0", "segment-1-bytes"],
                "clip_start_frames": [0, 17],
            },
        }
        self.assertEqual(len(close([segment0, segment1, concat])), 3)
        segment0["artifacts"][0]["sha256"] = "rerendered-0"
        self.assertEqual(
            [unit["unit_id"] for unit in close([segment0, segment1, concat])],
            ["segment-0"],
        )

    def test_segment_trim_settings_and_combined_ref_keyframes(self):
        settings = self.functions["_h3_segment_recovery_settings"]({
            "boundary_overlap_discard_frames": 17,
            "native_boundary_conditioning": True,
            "generated_frames": 243,
            "published_frames": 240,
            "trim_tail_frames": 3,
        })
        self.assertEqual(settings["discard_prefix_frames"], 17)
        self.assertEqual(settings["trim_tail_frames"], 3)
        self.assertEqual(settings["published_frames"], 240)
        merge = self.functions["_merge_h3_ref2va_keyframes"]
        self.assertEqual(
            merge(["semantic-a.png"], ["edge-b.png"]),
            ["semantic-a.png", "edge-b.png"],
        )
        with self.assertRaisesRegex(ValueError, "at most 9"):
            merge(list(range(8)), [8, 9])

    def test_concat_only_replay_binds_and_passes_segment_trims(self):
        units = {
            0: {
                "unit_id": "u0", "settings": {
                    "discard_prefix_frames": 0,
                    "generated_frames": 243,
                    "published_frames": 240,
                    "trim_tail_frames": 3,
                },
                "artifacts": [{
                    "basename": "segment0.mp4", "sha256": "hash0",
                }],
            },
            1: {
                "unit_id": "u1", "settings": {
                    "discard_prefix_frames": 17,
                    "generated_frames": 243,
                    "published_frames": 240,
                    "trim_tail_frames": 3,
                },
                "artifacts": [{
                    "basename": "segment1.mp4", "sha256": "hash1",
                }],
            },
        }
        calls = []
        recovery_calls = []

        class Wgp:
            @staticmethod
            def concatenate_multi_clip_videos(paths, output, audio, **kwargs):
                calls.append((paths, output, audio, kwargs))
                return True

        true_peak_policy = _load_functions(
            APP / "launch.py", {"_h3_true_peak_policy_identity"}, {},
        )["_h3_true_peak_policy_identity"]
        true_peak_stats = {**true_peak_policy(), "verified": True}
        namespace = {
            "os": os,
            "job": {"id": "job"},
            "job_id": "job",
            "out_dir": "/project",
            "raw_params": {},
            "h3_delivery_request": False,
            "producer_artifact_roles": {},
            "gen": {"file_list": []},
            "wgp": Wgp,
            "QueueRecoveryRuntimeError": RuntimeError,
            "is_cancel_requested": lambda job: False,
            "_enforce_deferred_h3_final_audio": (
                lambda job, output_path, update_job_fn=None: dict(
                    true_peak_stats
                )
            ),
            "_sample_campaign_transition_lock": contextlib.nullcontext(),
            "sample_safe_unit_current": lambda abort_state: True,
            "abort_state": object(),
            "update_job": lambda job, **updates: (job.update(updates) or True),
            "_queue_recovery_unit_matches": lambda job, **kwargs: units[kwargs["index"]],
            "recovery_unit_id": lambda *args, **kwargs: (
                recovery_calls.append(kwargs) or "concat-unit"
            ),
            "replay_concat_to_stable_output": lambda out_dir, component_basenames, output_basename, concatenate: concatenate(
                [os.path.join(out_dir, name) for name in component_basenames],
                os.path.join(out_dir, output_basename),
            ),
            "_write_output_sidecars": lambda *args, **kwargs: None,
            "_queue_recovery_checkpoint_unit": lambda *args, **kwargs: {
                "unit_id": "concat-unit",
            },
            "_h3_true_peak_policy_identity": true_peak_policy,
        }
        replay = _load_nested_function(
            APP / "launch.py", "_replay_h3_concat_from_verified_segments",
            namespace,
        )
        replay(
            variant=0, total_segments=2,
            clip_info={"audio_start_sec": 0, "preserve_generated_audio": True},
        )
        self.assertEqual(calls[0][3]["clip_start_frames"], [0, 17])
        self.assertEqual(
            recovery_calls[0]["settings"]["clip_tail_frames"], [3, 3],
        )
        self.assertEqual(
            recovery_calls[0]["settings"]["component_hashes"],
            ["hash0", "hash1"],
        )
        self.assertEqual(
            recovery_calls[0]["settings"]["h3_audio_true_peak_policy"],
            true_peak_policy(),
        )
        self.assertEqual(namespace["job"]["h3_audio_true_peak"], true_peak_stats)


class NativeBoundaryDecodeTests(unittest.TestCase):
    def test_generated_h3_stereo_is_preserved_by_final_mux(self):
        from shared.utils import audio_video

        with mock.patch.object(
            audio_video,
            "get_mp4_audio_codec_settings",
            return_value={"codec": "aac", "bitrate": "128k"},
        ), mock.patch.object(audio_video.subprocess, "run") as run:
            audio_video.combine_and_concatenate_video_with_audio_tracks(
                "final.mp4",
                "video.mp4",
                [],
                ["generated.wav"],
                0,
                32000,
                output_audio_channels=2,
            )
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-ac") + 1], "2")

        with mock.patch.object(
            audio_video,
            "get_mp4_audio_codec_settings",
            return_value={"codec": "aac", "bitrate": None},
        ), mock.patch.object(audio_video.subprocess, "run") as legacy_run:
            audio_video.combine_and_concatenate_video_with_audio_tracks(
                "final.mp4", "video.mp4", [], ["generated.wav"], 0, 32000,
                False, None, "aac_128", True,
            )
        legacy_command = legacy_run.call_args.args[0]
        self.assertEqual(legacy_command[legacy_command.index("-ac") + 1], "1")

        with self.assertRaisesRegex(ValueError, "mono or stereo"):
            audio_video.combine_and_concatenate_video_with_audio_tracks(
                "final.mp4", "video.mp4", [], [], 0, 32000,
                output_audio_channels=3,
            )
        with self.assertRaisesRegex(ValueError, "mono or stereo"):
            audio_video.combine_and_concatenate_video_with_audio_tracks(
                "final.mp4", "video.mp4", [], [], 0, 32000,
                output_audio_channels=True,
            )

    def test_stereo_layout_applies_to_source_prefix_and_generated_tail(self):
        from shared.utils import audio_video

        with mock.patch.object(
            audio_video,
            "get_mp4_audio_codec_settings",
            return_value={"codec": "aac", "bitrate": None},
        ), mock.patch.object(audio_video.subprocess, "run") as run:
            audio_video.combine_and_concatenate_video_with_audio_tracks(
                "final.mp4",
                "video.mp4",
                ["source.wav"],
                ["generated.wav"],
                1.0,
                32000,
                source_audio_metadata=[{
                    "codec": "pcm_s16le",
                    "sample_rate": 32000,
                    "channels": 1,
                    "duration": 1.0,
                }],
                output_audio_channels=2,
            )
        command = run.call_args.args[0]
        filters = command[command.index("-filter_complex") + 1]
        self.assertEqual(filters.count("channel_layouts=stereo"), 2)
        self.assertNotIn("channel_layouts=mono", filters)

        contract = _load_functions(
            APP / "wgp.py",
            {"resolve_mux_audio_contract"},
            {
                "np": np,
                "resolve_mux_audio_sampling_rate": lambda *_args: 48000,
            },
        )["resolve_mux_audio_contract"]
        self.assertEqual(
            contract(
                "minimax_h3",
                np.zeros((32000, 2), dtype=np.float32),
                32000,
                [{"sample_rate": 48000}],
                ["generated.wav"],
                native_h3_audio_selected=True,
            ),
            (32000, 2),
        )
        for replacement_kind in ("audio_source", "mmaudio"):
            with self.subTest(replacement_kind=replacement_kind):
                self.assertEqual(
                    contract(
                        "minimax_h3",
                        np.zeros((32000, 2), dtype=np.float32),
                        32000,
                        [{"sample_rate": 48000}],
                        [f"{replacement_kind}.wav"],
                        native_h3_audio_selected=False,
                    ),
                    (48000, 1),
                )
        self.assertEqual(
            contract(
                "ltx2_22B",
                np.zeros((32000, 2), dtype=np.float32),
                32000,
                [{"sample_rate": 48000}],
                ["generated.wav"],
            ),
            (48000, 1),
        )
        with self.assertRaisesRegex(ValueError, "must be stereo"):
            contract(
                "minimax_h3",
                np.zeros((32000, 1), dtype=np.float32),
                32000,
                native_h3_audio_selected=True,
            )
        with self.assertRaisesRegex(ValueError, "requires a MiniMax H3 model"):
            contract(
                "ltx2_22B",
                np.zeros((32000, 2), dtype=np.float32),
                32000,
                native_h3_audio_selected=True,
            )
        with self.assertRaisesRegex(ValueError, "audio is missing"):
            contract(
                "minimax_h3",
                None,
                32000,
                native_h3_audio_selected=True,
            )
        wgp_source = (APP / "wgp.py").read_text(encoding="utf-8")
        self.assertIn("output_audio_channels=mux_audio_channels", wgp_source)
        self.assertIn(
            "native_h3_audio_selected=native_h3_audio_selected",
            wgp_source,
        )
        selected_audio_branch = wgp_source.index(
            "elif output_new_audio_data is not None:"
        )
        resolver_call = wgp_source.index(
            "mux_audio_sampling_rate, mux_audio_channels =",
            selected_audio_branch,
        )
        self.assertIn(
            'native_h3_audio_selected = base_model_type in {\n'
            '                                "minimax_h3", "minimax_h3_ref2va",',
            wgp_source[selected_audio_branch:resolver_call],
        )

    def test_private_descriptor_is_scrubbed_before_saved_metadata(self):
        source = (APP / "wgp.py").read_text(encoding="utf-8")
        scrub = source.index('inputs.pop("_h3_native_boundary", None)')
        metadata = source.index('configs = prepare_inputs_dict("metadata", inputs')
        self.assertLess(scrub, metadata)
        launch_source = (APP / "launch.py").read_text(encoding="utf-8")
        sidecar_copy = launch_source.index("sidecar_params = source_params.copy()")
        native_scrub = launch_source.index(
            'sidecar_params.pop("_h3_native_boundary", None)', sidecar_copy,
        )
        sidecar_write = launch_source.index('"params": sidecar_params', sidecar_copy)
        self.assertLess(native_scrub, sidecar_write)
        self.assertIn(
            'sidecar_params.pop("_h3_native_boundary_request", None)',
            launch_source[native_scrub:sidecar_write],
        )

    def test_private_boundary_decoder_converts_thwc_to_normalized_cthw(self):
        samples = round(18 / 24 * 32000)

        class Completed:
            returncode = 0
            stdout = np.zeros((samples, 2), dtype="<f4").tobytes()

        namespace = {
            "os": os,
            "np": np,
            "subprocess": types.SimpleNamespace(run=lambda *args, **kwargs: Completed()),
            "get_video_info": lambda path: (24, 5, 4, 18),
            "get_resampled_video": lambda *args: torch.zeros(
                (18, 4, 5, 3), dtype=torch.uint8,
            ),
        }
        loader = _load_functions(
            APP / "wgp.py", {"load_h3_native_boundary_inputs"}, namespace,
        )["load_h3_native_boundary_inputs"]
        with mock.patch(
            "services.h3_boundary_policy.verify_boundary_file",
            return_value="private.mp4",
        ):
            video, audio = loader({
                "path": "private.mp4", "size": 1, "sha256": "0" * 64,
                "fps": 24, "audio_sample_rate": 32000, "audio_channels": 2,
                "overlap_frames": 18, "discard_frames": 17,
            })
        self.assertEqual(tuple(video.shape), (3, 18, 4, 5))
        self.assertTrue(torch.all(video == -1))
        self.assertEqual(audio.shape, (samples, 2))


class NativeBoundaryConcatTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_concat_discards_exactly_17_frames_and_matching_audio(self):
        namespace = _load_functions(
            APP / "wgp.py",
            {"PostDecodeStageError", "concatenate_multi_clip_videos"},
            {"os": os},
        )
        concatenate = namespace["concatenate_multi_clip_videos"]
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, color in enumerate(("red", "blue")):
                path = Path(directory) / f"clip-{index}.mp4"
                subprocess.run([
                    "ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color={color}:s=64x64:r=24:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=32000:duration=1",
                    "-frames:v", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "32000", "-ac", "2", "-shortest",
                    str(path),
                ], check=True, capture_output=True, timeout=60)
                paths.append(str(path))
            output = Path(directory) / "joined.mp4"
            self.assertTrue(concatenate(
                paths, str(output), clip_start_frames=[0, 17],
            ))
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames,r_frame_rate",
                "-of", "json", str(output),
            ], check=True, capture_output=True, text=True, timeout=60)
            payload = __import__("json").loads(probe.stdout)["streams"][0]
            self.assertEqual(int(payload["nb_read_frames"]), 31)
            self.assertEqual(payload["r_frame_rate"], "24/1")
            audio_probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=duration,sample_rate,channels",
                "-of", "json", str(output),
            ], check=True, capture_output=True, text=True, timeout=60)
            audio_stream = json.loads(audio_probe.stdout)["streams"][0]
            self.assertEqual(audio_stream["sample_rate"], "32000")
            self.assertEqual(int(audio_stream["channels"]), 2)
            expected_duration = 31 / 24
            self.assertLessEqual(
                abs(float(audio_stream["duration"]) - expected_duration),
                1024 / 32000,
            )

            untrimmed_output = Path(directory) / "joined-untrimmed.mp4"
            self.assertTrue(concatenate(
                paths, str(untrimmed_output), clip_start_frames=[0, 0],
            ))
            untrimmed_probe = subprocess.run([
                "ffprobe", "-v", "error", "-count_frames",
                "-select_streams", "v:0", "-show_entries",
                "stream=nb_read_frames", "-of", "json",
                str(untrimmed_output),
            ], check=True, capture_output=True, text=True, timeout=60)
            untrimmed_stream = json.loads(
                untrimmed_probe.stdout,
            )["streams"][0]
            self.assertEqual(int(untrimmed_stream["nb_read_frames"]), 48)


class NativeBoundaryBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = APP / "scripts" / "benchmark_h3_profiles.py"
        spec = importlib.util.spec_from_file_location("h3_boundary_benchmark", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.benchmark = module

    def test_eight_cases_are_opt_in_fixed_seed_geometry(self):
        cases = [
            case for case in self.benchmark.DEFAULT_CASES
            if case.boundary_mode
        ]
        self.assertEqual(len(cases), 8)
        self.assertTrue(all(not case.enabled for case in cases))
        self.assertEqual(
            {
                (case.native_boundary_conditioning, case.boundary_mode,
                 case.semantic_reference)
                for case in cases
            },
            {
                (native, boundary, semantic)
                for native in (False, True)
                for boundary in ("continuous", "cut")
                for semantic in (False, True)
            },
        )
        for case in cases:
            payload = self.benchmark.build_generation_payload(
                case, project="synthetic", seed=314159265,
                reference_path="procedural.png",
            )
            self.assertEqual(payload["video_length"], 350)
            self.assertEqual(payload["sliding_window_size"], 175)
            self.assertEqual(payload["resolution"], "608x352")
            self.assertEqual(payload["num_inference_steps"], 20)
            self.assertEqual(payload["custom_settings"]["h3_attention_engine"], "sdpa")
            self.assertEqual(
                payload["h3_boundary_overrides"], [{"type": case.boundary_mode}],
            )
            self.assertEqual(
                payload["h3_native_boundary_conditioning"],
                case.native_boundary_conditioning,
            )
            self.assertEqual(payload["image_start"], "procedural.png")
            self.assertEqual(
                bool(payload.get("image_refs")), case.semantic_reference,
            )
            config = case.public_config()
            self.assertEqual(
                config["procedural_edge_effective"], case.procedural_edge,
            )
            self.assertNotIn("conditioning_compromise", config)

    def test_vram_evidence_is_attributed_by_cache_sample_delta(self):
        report = {
            "records": [{
                "cache_key": "fixed-lane",
                "sample_count": 5,
                "peak_gpu_memory_bytes": 123,
                "generation_wall_time_seconds": 9.5,
                "spec": {
                    "model": {"id": "minimax_h3"},
                    "engine": {"id": "sdpa"},
                    "task": {
                        "width": 608, "height": 352, "frame_count": 175,
                        "sampling_steps": 20,
                    },
                },
            }],
        }
        self.assertEqual(
            self.benchmark.benchmark_sample_counts(report),
            {"fixed-lane": 5},
        )
        evidence = self.benchmark.boundary_vram_evidence(
            report, prior_sample_counts={"fixed-lane": 3},
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["sample_count_delta"], 2)
        self.assertEqual(evidence[0]["peak_gpu_memory_bytes"], 123)
        self.assertEqual(
            self.benchmark.boundary_vram_evidence(
                report, prior_sample_counts={"fixed-lane": 5},
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
