"""Consent/provider gates and local content-neutrality regressions."""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.mature_policy import mature_mode_allowed  # noqa: E402
from services.output_access import output_policy_from_request  # noqa: E402


def _services(**updates):
    values = {
        "nsfw_mode": True,
        "host_terms_acceptance": {
            "lawful_use": {
                "version": 1,
                "accepted_at": "2026-08-06T12:00:00Z",
            },
        },
        "llm_provider": "local",
    }
    values.update(updates)
    return values


class LocalContentNeutralityTests(unittest.TestCase):
    def test_guidance_mode_requires_boolean_opt_in_consent_and_private_provider(self):
        self.assertTrue(mature_mode_allowed(_services()))
        self.assertFalse(mature_mode_allowed(_services(nsfw_mode=False)))
        self.assertFalse(mature_mode_allowed(_services(nsfw_mode=1)))
        self.assertFalse(mature_mode_allowed(_services(host_terms_acceptance={})))
        self.assertFalse(mature_mode_allowed(_services(host_terms_acceptance={
            "lawful_use": {"version": 0, "accepted_at": "2026-08-06T12:00:00Z"},
        })))
        self.assertFalse(mature_mode_allowed(_services(llm_provider="openai")))
        self.assertFalse(mature_mode_allowed(_services(llm_provider="Anthropic")))

    def test_legacy_lawful_use_timestamp_remains_v1_compatible(self):
        self.assertTrue(mature_mode_allowed({
            "nsfw_mode": True,
            "nsfw_accepted_at": "2026-08-06T12:00:00Z",
            "llm_provider": "local",
        }))

    def test_output_policy_depends_only_on_caller_flags(self):
        sensitive_metadata = {
            "model_type": "model-marked-nsfw-only",
            "activated_loras": ["adult_nude_violent.safetensors"],
            "_mmaudio_variant": "nsfw",
        }
        self.assertEqual(
            output_policy_from_request(
                dict(sensitive_metadata),
                owner_session_id="a" * 32,
                mature_output=True,
            ),
            {"private": False, "explicit": False},
        )
        explicit = {**sensitive_metadata, "explicit_output": True}
        self.assertEqual(
            output_policy_from_request(explicit, owner_session_id="a" * 32),
            {"private": True, "explicit": True},
        )
        public = {
            **sensitive_metadata,
            "explicit_output": True,
            "private_output": False,
        }
        self.assertEqual(
            output_policy_from_request(public, owner_session_id="a" * 32),
            {"private": False, "explicit": True},
        )

    def test_invalid_user_policy_flags_remain_bad_requests(self):
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
                {"explicit_output": "true"}, owner_session_id="a" * 32,
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "explicit_output must be a boolean")

    def test_no_content_derived_catalog_or_publication_policy_remains(self):
        sources = {
            "launch": (APP_ROOT / "launch.py").read_text(encoding="utf-8"),
            "policy": (APP_ROOT / "services" / "mature_policy.py").read_text(encoding="utf-8"),
            "store": (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(encoding="utf-8"),
            "selector": (ROOT / "ui" / "src" / "components" / "Sidebar" / "ModelSelector.tsx").read_text(encoding="utf-8"),
        }
        forbidden = (
            "_classify_generation_maturity",
            "_classify_director_maturity",
            "_classify_lora_nsfw",
            "request_is_mature",
            "lora_is_mature",
            "model_is_mature",
            "_activeSelectionHasMatureComponent",
            "_loraNeedsExplicit",
            "matureSelectionActive",
            ".filter(m => !m.nsfw_only",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertFalse(any(token in source for source in sources.values()))

    def test_flux_predecessor_moderation_is_absent(self):
        flux_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                APP_ROOT / "models" / "flux" / "modules" / "system_messages.py",
                APP_ROOT / "models" / "flux" / "modules" / "text_encoder_mistral.py",
                APP_ROOT / "models" / "flux" / "util.py",
            )
        )
        for token in (
            "Keep content PG-13",
            "SYSTEM_PROMPT_CONTENT_FILTER",
            "PROMPT_IMAGE_INTEGRITY",
            "PROMPT_TEXT_INTEGRITY",
            "def test_image(",
            "def test_txt(",
            "Your generated image may contain NSFW content",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, flux_sources)

    def test_flux_save_image_ignores_rejecting_legacy_classifier(self):
        import torch
        from einops import rearrange
        from PIL import ExifTags, Image

        util_path = APP_ROOT / "models" / "flux" / "util.py"
        module = ast.parse(util_path.read_text(encoding="utf-8"))
        helper = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "save_image"
        )
        namespace = {
            "torch": torch,
            "rearrange": rearrange,
            "Image": Image,
            "ExifTags": ExifTags,
            "track_usage_via_api": lambda *_args: None,
        }
        exec(
            compile(ast.Module(body=[helper], type_ignores=[]), str(util_path), "exec"),
            namespace,
        )

        def rejecting_classifier(_image):
            raise AssertionError("legacy classifier must not be called")

        with tempfile.TemporaryDirectory() as directory:
            template = str(Path(directory) / "neutral-{idx}.png")
            result = namespace["save_image"](
                rejecting_classifier,
                "flux-dev",
                template,
                0,
                torch.zeros((1, 3, 2, 2)),
                False,
                "A nude adult beside a violent controversial mural.",
            )
            self.assertEqual(result, 1)
            self.assertTrue(Path(template.format(idx=0)).is_file())

    def test_prompt_routes_use_fixed_error_envelopes(self):
        launch_path = APP_ROOT / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        module = ast.parse(source)
        route_names = {
            "llm_generate",
            "llm_enhance_prompt",
            "llm_describe_image",
            "director_plan_prompts",
            "director_plan_angle_prompts",
            "director_plan_prompts_and_images",
            "director_plan_short_film_prompts",
            "director_plan_short_film_script",
        }
        for node in module.body:
            if not isinstance(node, ast.AsyncFunctionDef) or node.name not in route_names:
                continue
            route_source = ast.get_source_segment(source, node) or ""
            with self.subTest(route=node.name):
                self.assertNotIn("detail=str(", route_source)
                self.assertIn("check the local Maestro logs", route_source)

        enhance = next(
            node for node in module.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "llm_enhance_prompt"
        )
        access_call = next(
            item for item in ast.walk(enhance)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "_require_project_access"
        )
        permission = next(
            keyword.value for keyword in access_call.keywords
            if keyword.arg == "permission"
        )
        requested_images = next(
            item for item in ast.walk(enhance)
            if isinstance(item, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "requested_image_paths"
                for target in item.targets
            )
        )
        self.assertIsInstance(permission, ast.Constant)
        self.assertEqual(permission.value, "project.generate")
        self.assertLess(access_call.lineno, requested_images.lineno)


if __name__ == "__main__":
    unittest.main()
