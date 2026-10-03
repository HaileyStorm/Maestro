"""CPU admission evidence; these mocks do not prove live account/queue access."""

# Execute complete route functions without importing the live model/server.
# ruff: noqa: S102
from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


def source(**changes):
    return {
        "workspace": "authorized-project",
        "h3_cumulative_append": True,
        "model_type": "minimax_h3",
        "resolution": "1344x768",
        "video_length": 141,
        "sliding_window_size": 124,
        "prompt": "An adult courier crosses a tiled hall.",
        "num_inference_steps": 28,
        "custom_settings": {"h3_attention_engine": "sdpa"},
        "repeat_generation": 1,
        "batch_size": 1,
        **changes,
    }


class H3CumulativeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ROOT / "app/launch.py").read_text())

    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.events = []
        self.jobs = []
        self.workers = []
        self.authorized = True
        self.next_id = Mock(return_value="cpu-test-job")
        self.generation_worker = Mock(name="existing-generation-worker")
        self.estimates = Mock(return_value={"current": {"estimate": {"seconds": 111}}})

        def authorize(_request, workspace, *, permission):
            self.events.append(("access", workspace, permission))
            if not self.authorized:
                raise HTTPException(status_code=403, detail="Project access denied")
            return "test-project-root"

        def record(name):
            return lambda *args, **kwargs: self.events.append((name,))

        def ordinary_plan(body):
            self.events.append(("decoded-planner",))
            plan = {
                "clip_count": 1,
                "requested_frames": body["video_length"],
                "planned_frames": body["video_length"],
                "global_prompt": body["prompt"],
            }
            body["_h3_longform"] = plan
            return plan

        def register(job, *, worker, **_kwargs):
            self.events.append(("register",))
            self.jobs.append(copy.deepcopy(job))
            self.workers.append(worker)

        self.ns = {
            "Request": object,
            "HTTPException": HTTPException,
            "os": os,
            "time": time,
            "copy": copy,
            "_ENHANCED_PROMPT_CARDINALITY_KEY": "_enhanced_prompt_cardinality",
            "_H3_LONG_STUDIO_MODELS": {"minimax_h3", "minimax_h3_ref2va"},
            "wgp": types.SimpleNamespace(
                get_model_def=lambda _model: {"architecture": "minimax_h3"},
                get_base_model_type=lambda _model: "minimax_h3",
                get_model_min_frames_and_step=lambda _model: (22, 345, 17),
            ),
            "_require_project_access": authorize,
            "_get_active_workspace": lambda: "authorized-project",
            "_director_image_role_wire_mode": lambda _body: "legacy",
            "_request_krea_principal_role": lambda *_args: None,
            "_authorize_h3_turbo_benchmark_request": lambda *_args: None,
            "_apply_h3_adaptive_checkpoint": record("adaptive"),
            "_apply_fresh_h3_role_defaults": record("defaults"),
            "_prepare_h3_long_studio_request": ordinary_plan,
            "_h3_effective_model_types": lambda body, _plan: [body["model_type"]],
            "_h3_estimate_context": lambda body, _plan: {
                "model_type": body["model_type"]
            },
            "_local_owner_may_run_unvalidated_h3_turbo_ref2va": lambda _request: False,
            "_h3_profile_estimate_payload": self.estimates,
            "_h3_generation_requirements": lambda *_args: {"checkpoint_options": []},
            "_remote_visible_model_ids": lambda _request: None,
            "_public_h3_long_plan": lambda plan, *_args: (
                {"clip_count": plan["clip_count"]} if plan else None
            ),
            "_http_output_policy_from_request": lambda *_args, **_kwargs: {
                "private": False,
                "explicit": False,
            },
            "_new_generation_job_id": self.next_id,
            "_queue_recovery_register_and_publish": register,
            "_run_generation": self.generation_worker,
            "_run_generation_preparation": Mock(name="preparation-worker"),
            "_GenerationPreparationRequest": type(
                "PreparationRequest", (), {"__init__": lambda self, _request: None}
            ),
        }
        for name in (
            "_reject_client_krea_authority",
            "_reject_client_director_image_role_internals",
            "_require_remote_visible_models",
            "_require_h3_legal_execution",
            "_require_model_recipe_terms",
            "_reject_client_h3_turbo_validation_controls",
            "_authorize_generation_media_inputs",
            "_resolve_h3_style_workflow_request",
            "_normalize_video_prompt_type",
            "_normalize_image_prompt_type",
            "_require_h3_native_boundary_experimental",
            "_apply_h3_style_workflow_to_request",
            "_validate_h3_sampling_steps",
            "_validate_h3_explicit_multiclip_request",
            "_validate_h3_lora_request",
            "_require_h3_acceleration_available",
            "_validate_h3_turbo_estimate_context",
            "_validate_h3_spectrum_estimate_context",
            "_validate_h3_lightx2v_estimate_context",
            "_require_h3_generation_terms",
        ):
            self.ns[name] = record(name)
        wanted = {
            "_reject_client_h3_internal_state",
            "_consume_h3_cumulative_selection",
            "_h3_cumulative_http_available",
            "_validate_h3_cumulative_acceleration",
            "_public_h3_cumulative_plan",
            "_plan_generation_submission",
            "generate",
            "preview_generation_plan",
        }
        functions = [
            copy.deepcopy(n)
            for n in self.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name in wanted
        ]
        self.assertEqual({n.name for n in functions}, wanted)
        for node in functions:
            node.decorator_list = []
        exec(
            compile(
                ast.Module(body=functions, type_ignores=[]), "http-route-seams", "exec"
            ),
            self.ns,
        )

    def call(self, body, *, preview=False):
        async def request_json():
            return copy.deepcopy(body)

        request = types.SimpleNamespace(
            json=request_json,
            state=types.SimpleNamespace(
                maestro_session_id="authorized-owner-session",
                maestro_remote=False,
            ),
        )
        return asyncio.run(
            self.ns["preview_generation_plan" if preview else "generate"](request)
        )

    def test_rejections_create_no_job_or_durable_admission(self):
        invalid = [
            source(h3_cumulative_append=value) for value in (1, 0, "true", None, [])
        ] + [
            source(_h3_cumulative_append=True),
            source(custom_settings={"_h3_cumulative_append": True}),
            source(enhance_before_generate=True),
            source(model_type="minimax_h3_ref2va"),
            source(generation_mode="image"),
            source(multi_prompts_gen_type=3),
            source(h3_fl2va_loras=["example.safetensors"]),
            source(resolution="1345x768"),
            source(sliding_window_size=21),
            source(delivery_resolution="1920x1080"),
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                self.call(body)
            self.assertEqual(raised.exception.status_code, 400)
        with (
            patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}),
            self.assertRaises(HTTPException),
        ):
            self.call(source())
        self.authorized = False
        with self.assertRaises(HTTPException) as raised:
            self.call(source())
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.jobs, [])
        self.next_id.assert_not_called()

    def test_host_availability_is_exact_base_and_gate(self):
        available = self.ns["_h3_cumulative_http_available"]
        self.assertTrue(available("minimax_h3"))
        for model in ("minimax_h3_ref2va", "minimax_h3_w4a8_fl2va", "other"):
            self.assertFalse(available(model))
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}):
            self.assertFalse(available("minimax_h3"))

    def test_saved_profile_selector_matches_http_boolean_contract(self):
        from services.generation_presets import (
            GenerationPresetError,
            _normalize_v2_params,
        )

        params = {
            "resolution": "1344x768",
            "video_length": 141,
            "num_inference_steps": 28,
            "guidance_scale": 1,
            "seed": 41,
            "image_mode": 0,
            "repeat_generation": 1,
            "settings_version": 2,
        }
        for enabled in (True, False):
            normalized = _normalize_v2_params(
                {**params, "h3_cumulative_append": enabled}, mode="video"
            )
            self.assertEqual(
                self.ns["_consume_h3_cumulative_selection"](
                    source(h3_cumulative_append=normalized["h3_cumulative_append"])
                ),
                enabled,
            )
        for invalid in (None, "true", 1, [], {}):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(GenerationPresetError),
            ):
                _normalize_v2_params(
                    {**params, "h3_cumulative_append": invalid}, mode="video"
                )
        self.assertEqual(self.jobs, [])
        self.next_id.assert_not_called()

    def test_otherwise_valid_acceleration_is_rejected_before_admission(self):
        self.ns["wgp"].get_model_def = lambda _model: {
            "architecture": "minimax_h3",
            "URLs": ["minimax_h3_fl2va_pruned_fp8_scaled.safetensors"],
        }
        names = (
            "_validate_h3_turbo_estimate_context",
            "_validate_h3_spectrum_estimate_context",
            "_validate_h3_lightx2v_estimate_context",
        )
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                exec(
                    compile(
                        ast.Module(body=[node], type_ignores=[]),
                        "real-compatibility",
                        "exec",
                    ),
                    self.ns,
                )
        profiles = (
            ("h3_turbo_profile", "h3_turbo_v4", 4, names[0]),
            ("h3_spectrum_profile", "spectrum_h3_v1", 20, names[1]),
            ("h3_lightx2v_profile", "h3_lightx2v_fl2v_4_v1", 4, names[2]),
        )
        for key, profile, steps, validator in profiles:
            body = source(
                num_inference_steps=steps,
                custom_settings={"h3_attention_engine": "sdpa", key: profile},
            )
            # Real ordinary compatibility accepts this candidate; cumulative
            # admission must reject it before allocating any queue state.
            self.ns[validator](body)
            for preview in (False, True):
                with (
                    self.subTest(profile=profile, preview=preview),
                    self.assertRaises(HTTPException) as raised,
                ):
                    self.call(body, preview=preview)
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("Turn off", str(raised.exception.detail))
        self.assertEqual(self.jobs, [])
        self.next_id.assert_not_called()

    def test_immediate_and_held_requests_use_existing_generation_worker(self):
        for held in (False, True):
            self.events.clear()
            result = self.call(source(_queue_mode="held" if held else "now"))
            job = self.jobs[-1]
            self.assertIs(self.workers[-1], self.generation_worker)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["queue_held"], held)
            self.assertEqual(job["workspace"], "authorized-project")
            self.assertEqual(job["session_id"], "authorized-owner-session")
            self.assertEqual(job["window_total"], 2)
            self.assertIs(job["params"]["_h3_cumulative_append"], True)
            self.assertNotIn("h3_cumulative_append", job["params"])
            self.assertNotIn("_h3_longform", job["params"])
            self.assertEqual(job["params"]["sliding_window_size"], 124)
            self.assertEqual(result["h3_cumulative_plan"]["published_frames"], 141)
            self.assertIsNone(result["h3_estimate"])
            self.assertNotIn(("decoded-planner",), self.events)
            self.assertEqual(self.events[1][0], "access")
            self.assertLess(
                self.events.index(("_require_h3_generation_terms",)),
                self.events.index(("register",)),
            )
        self.estimates.assert_not_called()

    def test_preview_is_distinct_and_exposes_only_public_geometry(self):
        result = self.call(source(), preview=True)
        preview = result["h3_cumulative_plan"]
        self.assertEqual(preview["mode"], "cumulative_append")
        self.assertEqual([w["sampler_frames"] for w in preview["windows"]], [124, 39])
        self.assertEqual(
            [w["new_published_frames"] for w in preview["windows"]], [124, 17]
        )
        self.assertEqual(preview["windows"][-1]["cumulative_published_frames"], 141)
        self.assertIsNone(result["plan"])
        self.assertIsNone(result["h3_estimate"])
        self.assertIsNone(result["segment_count_estimate"])
        self.assertNotIn(source()["prompt"], json.dumps(result))
        self.assertNotIn("sha256", json.dumps(result))
        self.assertEqual(self.jobs, [])
        self.estimates.assert_not_called()
        for body in (
            source(_h3_cumulative_append=True),
            source(enhance_before_generate=True),
        ):
            with self.assertRaises(HTTPException):
                self.call(body, preview=True)

    def test_ordinary_h3_keeps_preparation_and_decoded_planning(self):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}):
            self.call(source(h3_cumulative_append=False))
            self.assertIsNot(self.workers[-1], self.generation_worker)
            self.assertEqual(self.jobs[-1]["status"], "preparing")
            self.call(source(h3_cumulative_append=False, _queue_mode="held"))
        self.assertIs(self.workers[-1], self.generation_worker)
        self.assertIn(("decoded-planner",), self.events)
        self.assertNotIn("_h3_cumulative_append", self.jobs[-1]["params"])
        self.assertNotIn("h3_cumulative_append", self.jobs[-1]["params"])
        self.estimates.assert_called()

    def test_authorized_subject_matter_keeps_the_same_admission_path(self):
        expected_events = None
        for prompt in (
            "An adult courier crosses a tiled hall.",
            "Two consenting adults share an intimate romantic scene.",
            "A fictional sword battle unfolds in a ruined courtyard.",
            "Two politicians argue about a controversial public policy.",
        ):
            with self.subTest(prompt=prompt):
                self.events.clear()
                self.call(source(prompt=prompt))
                self.assertEqual(self.jobs[-1]["params"]["prompt"], prompt)
                self.assertIs(self.workers[-1], self.generation_worker)
                if expected_events is None:
                    expected_events = list(self.events)
                else:
                    self.assertEqual(self.events, expected_events)


if __name__ == "__main__":
    unittest.main()
