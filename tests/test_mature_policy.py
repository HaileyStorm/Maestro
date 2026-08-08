"""Dependency-free explicit-component classification regressions."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.mature_policy import (  # noqa: E402
    lora_is_mature,
    mature_mode_allowed,
    request_is_mature,
)
from services.output_access import (  # noqa: E402
    harden_output_access_for_maturity,
    output_policy_from_request,
)


def _services(**updates):
    values = {
        "nsfw_mode": True,
        "nsfw_accepted_at": "2026-08-06T12:00:00Z",
        "llm_provider": "local",
    }
    values.update(updates)
    return values


class MatureClassificationTests(unittest.TestCase):
    def test_mode_requires_boolean_opt_in_consent_and_private_provider(self):
        self.assertTrue(mature_mode_allowed(_services()))
        self.assertFalse(mature_mode_allowed(_services(nsfw_mode=False)))
        self.assertFalse(mature_mode_allowed(_services(nsfw_mode=1)))
        self.assertFalse(mature_mode_allowed(_services(nsfw_accepted_at="")))
        self.assertFalse(mature_mode_allowed(_services(llm_provider="openai")))
        self.assertFalse(mature_mode_allowed(_services(llm_provider="Anthropic")))

    def test_lora_override_and_sidecar_flags_beat_keyword_fallback(self):
        self.assertFalse(lora_is_mature(
            filename="uncensored-style.safetensors",
            metadata={"nsfw_override": False, "nsfw": True},
        ))
        self.assertTrue(lora_is_mature(
            filename="ordinary.safetensors",
            metadata={"nsfw_override": True, "nsfw": False},
        ))
        self.assertFalse(lora_is_mature(
            filename="nsfw-name.safetensors", metadata={"nsfw": False},
        ))
        self.assertTrue(lora_is_mature(
            filename="portrait.safetensors", metadata={"tags": ["uncensored"]},
        ))
        self.assertFalse(lora_is_mature(filename="sussex_landscape.safetensors"))

    def test_model_lora_and_mmaudio_are_classified_without_admission_gate(self):
        requests = [
            {"model_definition": {"nsfw_only": True}},
            {"loras": [{"filename": "local_nsfw_style.safetensors"}]},
            {"mmaudio_variant": "nsfw"},
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assertTrue(request_is_mature(**request))

    def test_ordinary_request_is_not_classified_explicit(self):
        self.assertFalse(request_is_mature(
            model_definition={"nsfw_only": False},
            loras=[{"filename": "cinematic-light.safetensors"}],
            mmaudio_variant="v2",
        ))

    def test_authoritative_lora_sidecar_drives_neutral_filename(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_mature_lora_descriptors"
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "portrait"
            (base.with_suffix(".civitai.json")).write_text(
                json.dumps({"name": "Portrait", "nsfw": True}),
                encoding="utf-8",
            )

            class FakeWgp:
                @staticmethod
                def get_lora_dir(_model_type):
                    return directory

                @staticmethod
                def resolve_lora_path(_model_type, _raw_name):
                    return str(base.with_suffix(".safetensors"))

            namespace = {"wgp": FakeWgp, "os": __import__("os"), "json": json}
            exec(
                compile(ast.Module(body=[helper], type_ignores=[]), str(launch_path), "exec"),
                namespace,
            )
            descriptors = namespace["_mature_lora_descriptors"]({
                "model_type": "ordinary",
                "activated_loras": ["portrait.safetensors"],
            })
        self.assertTrue(request_is_mature(loras=descriptors))

    def test_primary_mirror_lora_override_beats_linked_sidecar(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_mature_lora_descriptors"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mirror = root / "mirror"
            linked = root / "linked"
            mirror.mkdir()
            linked.mkdir()
            mirror_base = mirror / "portrait"
            linked_base = linked / "portrait"
            mirror_base.with_suffix(".civitai.json").write_text(
                json.dumps({"nsfw_override": False}), encoding="utf-8",
            )
            linked_base.with_suffix(".civitai.json").write_text(
                json.dumps({"nsfw": True}), encoding="utf-8",
            )

            class FakeWgp:
                @staticmethod
                def get_lora_dir(_model_type):
                    return str(mirror)

                @staticmethod
                def resolve_lora_path(_model_type, _raw_name):
                    return str(linked_base.with_suffix(".safetensors"))

            namespace = {"wgp": FakeWgp, "os": __import__("os"), "json": json}
            exec(
                compile(ast.Module(body=[helper], type_ignores=[]), str(launch_path), "exec"),
                namespace,
            )
            descriptors = namespace["_mature_lora_descriptors"]({
                "model_type": "ordinary",
                "activated_loras": ["portrait.safetensors"],
            })
            self.assertFalse(request_is_mature(loras=descriptors))

            mirror_base.with_suffix(".civitai.json").write_text(
                json.dumps({"nsfw_override": True}), encoding="utf-8",
            )
            linked_base.with_suffix(".civitai.json").write_text(
                json.dumps({"nsfw": False}), encoding="utf-8",
            )
            descriptors = namespace["_mature_lora_descriptors"]({
                "model_type": "ordinary",
                "activated_loras": ["portrait.safetensors"],
            })
            self.assertTrue(request_is_mature(loras=descriptors))

    def test_all_effective_h3_segment_models_participate_in_classification(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helpers = [
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "_effective_mmaudio_variant",
                "_h3_effective_model_types",
                "_classify_generation_maturity",
            }
        ]

        class FakeWgp:
            server_config = {
                "services": _services(nsfw_mode=False),
                "mmaudio_mode": 0,
            }
            MMAUDIO_MODE_NSFW = 3

            @staticmethod
            def get_model_def(model_type):
                return {
                    "nsfw_only": model_type == "minimax_h3_pinkcherry_fl2va",
                }

        namespace = {
            "wgp": FakeWgp,
            "_mature_lora_descriptors": lambda _body: [],
            "_H3_LONG_STUDIO_MODELS": {
                "minimax_h3",
                "minimax_h3_pinkcherry_fl2va",
                "minimax_h3_w4a8_fl2va",
                "minimax_h3_ref2va",
            },
        }
        exec(
            compile(ast.Module(body=helpers, type_ignores=[]), str(launch_path), "exec"),
            namespace,
        )
        classify = namespace["_classify_generation_maturity"]
        body = {"model_type": "minimax_h3_ref2va"}
        plan = {"segment_models": [
            {"model_type": "minimax_h3_ref2va"},
            {"model_type": "minimax_h3_pinkcherry_fl2va"},
        ]}
        self.assertTrue(classify(body, plan))
        FakeWgp.server_config = {"services": _services(llm_provider="openai")}
        self.assertTrue(classify(body, plan))

    def test_unplanned_explicit_base_does_not_invent_mature_checkpoint(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helpers = [
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "_effective_mmaudio_variant",
                "_h3_effective_model_types",
                "_classify_generation_maturity",
            }
        ]

        class FakeWgp:
            MMAUDIO_MODE_NSFW = 3
            server_config = {
                "services": _services(nsfw_mode=False),
                "mmaudio_mode": 0,
            }

            @staticmethod
            def get_model_def(model_type):
                return {
                    "nsfw_only": model_type == "minimax_h3_pinkcherry_fl2va",
                }

        namespace = {
            "wgp": FakeWgp,
            "_mature_lora_descriptors": lambda _body: [],
            "_H3_LONG_STUDIO_MODELS": {
                "minimax_h3",
                "minimax_h3_pinkcherry_fl2va",
                "minimax_h3_w4a8_fl2va",
                "minimax_h3_ref2va",
            },
        }
        exec(
            compile(ast.Module(body=helpers, type_ignores=[]), str(launch_path), "exec"),
            namespace,
        )
        self.assertFalse(namespace["_classify_generation_maturity"]({
            "model_type": "minimax_h3",
            "explicit_output": True,
        }))

    def test_configured_mmaudio_nsfw_fallback_is_effective_for_sfx(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_effective_mmaudio_variant"
        )

        class FakeWgp:
            MMAUDIO_MODE_NSFW = 3
            server_config = {"mmaudio_mode": 3}

        namespace = {"wgp": FakeWgp}
        exec(
            compile(ast.Module(body=[helper], type_ignores=[]), str(launch_path), "exec"),
            namespace,
        )
        effective = namespace["_effective_mmaudio_variant"]
        self.assertEqual(effective({"sfx_mode": True}), "nsfw")
        self.assertEqual(effective({"MMAudio_setting": 1}), "nsfw")
        self.assertEqual(
            effective({"sfx_mode": True, "_mmaudio_variant": "bogus"}),
            "nsfw",
        )
        self.assertEqual(
            effective({"sfx_mode": True, "_mmaudio_variant": "NSFW"}),
            "nsfw",
        )
        FakeWgp.server_config = {"mmaudio_mode": 1}
        self.assertEqual(
            effective({"sfx_mode": True, "_mmaudio_variant": "NSFW"}),
            "",
        )
        self.assertEqual(
            effective({"sfx_mode": True, "_mmaudio_variant": " nsfw "}),
            "",
        )
        self.assertEqual(effective({}), "")

    def test_director_keeps_output_classification_out_of_planner_intent(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        route = next(
            node for node in module.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "director_pipeline_start"
        )
        assignments = [
            node for node in ast.walk(route)
            if isinstance(node, ast.Assign)
        ]
        capture = next(
            node for node in assignments
            if any(
                isinstance(target, ast.Name)
                and target.id == "caller_explicit_output"
                for target in node.targets
            )
        )
        self.assertIsInstance(capture.value, ast.Compare)
        self.assertIsInstance(capture.value.ops[0], ast.Is)
        self.assertIsInstance(capture.value.comparators[0], ast.Constant)
        self.assertIs(capture.value.comparators[0].value, True)
        planner_assignment = next(
            node for node in assignments
            if any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "body"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "explicit_output"
                for target in node.targets
            )
        )
        self.assertIsInstance(planner_assignment.value, ast.Name)
        self.assertEqual(planner_assignment.value.id, "caller_explicit_output")

    def test_classification_keeps_direct_and_director_h3_selection_in_parity(self):
        launch_path = APP_ROOT / "launch.py"
        director_path = APP_ROOT / "services" / "director_pipeline.py"
        launch_module = ast.parse(launch_path.read_text(encoding="utf-8"))
        director_module = ast.parse(director_path.read_text(encoding="utf-8"))
        direct_helper = next(
            node for node in launch_module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_h3_preferred_fl2va_model"
        )
        director_helper = next(
            node for node in director_module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_director_h3_preferred_fl2va"
        )

        class FakeWgp:
            @staticmethod
            def get_model_def(_model_type):
                return {"nsfw_only": True}

        constants = {
            "_H3_BASE_FL2VA_MODEL": "base",
            "_H3_EXPLICIT_FL2VA_MODEL": "pinkcherry",
            "_H3_W4A8_FL2VA_MODEL": "w4a8",
            "_H3_FL2VA_MODELS": {"base", "pinkcherry", "w4a8"},
        }
        direct_namespace = {"wgp": FakeWgp, **constants}
        director_namespace = {"_wgp": FakeWgp, **constants}
        exec(
            compile(
                ast.Module(body=[direct_helper], type_ignores=[]),
                str(launch_path),
                "exec",
            ),
            direct_namespace,
        )
        exec(
            compile(
                ast.Module(body=[director_helper], type_ignores=[]),
                str(director_path),
                "exec",
            ),
            director_namespace,
        )

        direct_params = {"model_type": "base", "explicit_output": True}
        access = output_policy_from_request(
            dict(direct_params),
            owner_session_id="a" * 32,
            mature_output=True,
        )
        self.assertEqual(
            access,
            {"private": True, "explicit": True},
        )
        direct_choice = direct_namespace["_h3_preferred_fl2va_model"](
            direct_params,
        )
        director_choice = director_namespace["_director_h3_preferred_fl2va"](
            {"model_type": "base", "explicit_output": True}, "base",
        )
        self.assertEqual(direct_choice, "base")
        self.assertEqual(director_choice, direct_choice)
        for selected in ("pinkcherry", "w4a8"):
            with self.subTest(selected=selected):
                direct_choice = direct_namespace["_h3_preferred_fl2va_model"]({
                    "model_type": "ref2va",
                    "_h3_requested_checkpoint": selected,
                    "explicit_output": True,
                })
                director_choice = director_namespace[
                    "_director_h3_preferred_fl2va"
                ]({
                    "_h3_requested_checkpoint": selected,
                    "explicit_output": True,
                }, "ref2va")
                self.assertEqual(direct_choice, selected)
                self.assertEqual(director_choice, selected)

    def test_http_endpoints_report_invalid_policy_flags_as_bad_requests(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_http_output_policy_from_request"
        )

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        namespace = {
            "HTTPException": FakeHTTPException,
            "output_policy_from_request": output_policy_from_request,
        }
        exec(
            compile(ast.Module(body=[helper], type_ignores=[]), str(launch_path), "exec"),
            namespace,
        )
        with self.assertRaises(FakeHTTPException) as raised:
            namespace["_http_output_policy_from_request"](
                {"explicit_output": "true"},
                owner_session_id="a" * 32,
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "explicit_output must be a boolean",
        )

        endpoints = {
            "director_generate_music",
            "director_pipeline_start",
            "generate",
        }
        for route in (
            node for node in module.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name in endpoints
        ):
            with self.subTest(route=route.name):
                self.assertTrue(any(
                    isinstance(call.func, ast.Name)
                    and call.func.id == "_http_output_policy_from_request"
                    for call in ast.walk(route)
                    if isinstance(call, ast.Call)
                ))

    def test_director_helper_checks_both_generation_lanes(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_classify_director_maturity"
        )
        seen = []

        def classify(lane, _plan=None):
            seen.append(lane)
            return lane.get("model_type") == "mature-video"

        namespace = {"_classify_generation_maturity": classify}
        exec(
            compile(ast.Module(body=[helper], type_ignores=[]), str(launch_path), "exec"),
            namespace,
        )
        self.assertTrue(namespace["_classify_director_maturity"]({
            "image_model": "ordinary-image",
            "video_model": "mature-video",
            "explicit_output": True,
            "image_loras": {"activated_loras": ["portrait.safetensors"]},
            "video_loras": {"activated_loras": ["cinema.safetensors"]},
        }))
        self.assertEqual(
            [(lane["model_type"], lane["activated_loras"]) for lane in seen],
            [
                ("ordinary-image", ["portrait.safetensors"]),
                ("mature-video", ["cinema.safetensors"]),
            ],
        )
        self.assertTrue(all(lane["explicit_output"] for lane in seen))

    def test_job_registry_hardens_direct_and_inherited_mature_jobs(self):
        launch_path = APP_ROOT / "launch.py"
        module = ast.parse(launch_path.read_text(encoding="utf-8"))
        registry_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "_JobRegistry"
        )

        class Context:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        namespace = {
            "_request_remote": Context(False),
            "_request_session_id": Context("a" * 32),
            "_workspace_lifecycle_lock": threading.RLock(),
            "_classify_generation_maturity": (
                lambda params, _plan=None: params.get("model_type") == "mature"
            ),
            "output_policy_from_request": output_policy_from_request,
            "harden_output_access_for_maturity": harden_output_access_for_maturity,
        }
        exec(
            compile(ast.Module(body=[registry_node], type_ignores=[]), str(launch_path), "exec"),
            namespace,
        )
        registry = namespace["_JobRegistry"]()
        registry["ordinary"] = {"params": {"model_type": "ordinary"}}
        self.assertFalse(registry["ordinary"]["explicit"])
        self.assertFalse(registry["ordinary"]["private"])

        registry["direct"] = {"params": {
            "model_type": "mature",
            "explicit_output": False,
            "private_output": False,
        }}
        self.assertTrue(registry["direct"]["explicit"])
        self.assertTrue(registry["direct"]["private"])

        registry["director"] = {
            "params": {"model_type": "mature"},
            "session_id": "a" * 32,
            "access_policy": {
                "private": False,
                "explicit": False,
                "owner_session_id": None,
            },
        }
        self.assertTrue(registry["director"]["explicit"])
        self.assertTrue(registry["director"]["private"])

        registry["public_override"] = {
            "params": {"model_type": "mature"},
            "session_id": "a" * 32,
            "access_policy": {
                "private": False,
                "explicit": True,
                "owner_session_id": None,
            },
        }
        self.assertTrue(registry["public_override"]["explicit"])
        self.assertFalse(registry["public_override"]["private"])


if __name__ == "__main__":
    unittest.main()
