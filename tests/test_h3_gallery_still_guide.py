"""CPU-only checks for the bounded Gallery H3 still-guide route and replay gate."""

from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.h3_gallery_still_guide import (
    H3_GALLERY_STILL_GUIDE_CUSTOM_KEY,
    H3_GALLERY_STILL_GUIDE_PLAN_KEY,
    H3_GALLERY_STILL_GUIDE_SOURCE_KEY,
    H3GalleryStillGuideError,
    build_gallery_still_guide_plan,
    make_gallery_still_guide_source,
    probe_gallery_still,
    validate_gallery_still_guide_job,
)
from services.search_index import classify_gallery_artifacts, load_media_sidecars
from services.win_safe_files import safe_direct_file_under


def load_launch_functions(namespace: dict, *names: str) -> None:
    import ast

    tree = ast.parse((ROOT / "app/launch.py").read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "launch.py", "exec"), namespace)


def load_nested_launch_function(namespace: dict, outer_name: str, nested_name: str) -> None:
    import ast

    tree = ast.parse((ROOT / "app/launch.py").read_text(encoding="utf-8"))
    outer = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == outer_name
    )
    nested = next(
        node for node in ast.walk(outer)
        if isinstance(node, ast.FunctionDef) and node.name == nested_name
    )
    nested.decorator_list = []
    exec(
        compile(ast.Module(body=[nested], type_ignores=[]), "launch.py", "exec"),
        namespace,
    )


class StillSourceFixture:
    def __init__(self, root: Path, *, private: bool = True, explicit: bool = True):
        self.root = root
        self.path = root / "guide.png"
        self.save_image((210, 30, 50))
        self.sidecar = {
            "workspace": "project-a",
            "output_filename": self.path.name,
            "artifact_class": "final",
            "private": private,
            "explicit": explicit,
        }
        (root / "guide.meta.json").write_text(
            __import__("json").dumps(self.sidecar), encoding="utf-8",
        )

    def save_image(self, color: tuple[int, int, int]) -> None:
        Image.new("RGB", (24, 16), color).save(self.path, format="PNG")

    def revision(self, _path: str, _root: str, _name: str) -> str:
        return "revision-1"

    def validate(self, params: dict, *, job_private: bool = True, job_explicit: bool = True):
        return validate_gallery_still_guide_job(
            params,
            workspace="project-a",
            out_dir=str(self.root),
            safe_direct_file_under=safe_direct_file_under,
            output_revision=self.revision,
            load_sidecars=load_media_sidecars,
            classify_artifacts=classify_gallery_artifacts,
            integrity_pending=lambda *_args: False,
            job_private=job_private,
            job_explicit=job_explicit,
        )

    def params(self) -> dict:
        probe = probe_gallery_still(str(self.path))
        plan = build_gallery_still_guide_plan(
            sha256=probe.sha256, frame_index=62, target_frames=124,
        )
        source = make_gallery_still_guide_source(
            workspace="project-a",
            name=self.path.name,
            revision="revision-1",
            probe=probe,
            frame_index=62,
            target_frames=124,
            plan=plan,
            source_private=self.sidecar["private"],
            source_explicit=self.sidecar["explicit"],
        )
        return {
            "model_type": "minimax_h3",
            "generation_mode": "video",
            "video_length": 124,
            "sliding_window_size": 124,
            "image_mode": 0,
            "image_prompt_type": "S",
            "video_prompt_type": "",
            "audio_prompt_type": "",
            "image_start": str(self.path),
            "image_refs": [],
            "custom_settings": {
                H3_GALLERY_STILL_GUIDE_CUSTOM_KEY: {"frame_index": 62},
                "h3_source_audio_mode": "native",
            },
            H3_GALLERY_STILL_GUIDE_SOURCE_KEY: source,
            H3_GALLERY_STILL_GUIDE_PLAN_KEY: plan,
        }


class H3GalleryStillGuideServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = StillSourceFixture(self.root)

    def test_probe_is_bounded_single_frame_and_plan_keeps_inert_capability_flags(self):
        probe = probe_gallery_still(str(self.source.path))
        self.assertEqual((probe.width, probe.height), (24, 16))
        self.assertEqual(probe.size, self.source.path.stat().st_size)
        plan = build_gallery_still_guide_plan(
            sha256=probe.sha256, frame_index=62, target_frames=124,
        )
        self.assertEqual(plan["guides"][0]["resolved_frame_idx"], 62)
        self.assertIsNone(plan["guides"][0]["audio"])
        self.assertIs(plan["execution_available"], False)
        self.assertIs(plan["automatic_fallback"], False)
        self.assertIs(plan["continuation_composition_available"], False)

    def test_recovered_job_rehashes_bytes_and_checks_revision_and_privacy(self):
        params = self.source.params()
        receipt = self.source.validate(params)
        self.assertEqual(receipt["frame_index"], 62)
        self.assertEqual(receipt["target_frames"], 124)

        # The generic single-window safety bump may enlarge only the WGP
        # window. It must not change the model frame count or guide commitment.
        params["sliding_window_size"] = 133
        receipt = self.source.validate(params)
        self.assertEqual(params["video_length"], 124)
        self.assertEqual(receipt["target_frames"], 124)
        self.assertEqual(params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY], {
            "frame_index": 62,
        })

        # Simulate replacement while preserving the UI stat revision token.
        self.source.save_image((30, 190, 70))
        with self.assertRaises(H3GalleryStillGuideError):
            self.source.validate(params)

        self.source.save_image((210, 30, 50))
        for job_private, job_explicit in ((False, True), (True, False)):
            with self.subTest(job_private=job_private, job_explicit=job_explicit):
                with self.assertRaises(H3GalleryStillGuideError):
                    self.source.validate(
                        params,
                        job_private=job_private,
                        job_explicit=job_explicit,
                    )

        self.source.sidecar["private"] = False
        (self.root / "guide.meta.json").write_text(
            __import__("json").dumps(self.source.sidecar), encoding="utf-8",
        )
        with self.assertRaises(H3GalleryStillGuideError):
            self.source.validate(params)

    def test_recovered_job_rejects_extra_audio_guide_or_forged_plan(self):
        params = self.source.params()
        params["audio_guide"] = str(self.source.path)
        with self.assertRaises(H3GalleryStillGuideError):
            self.source.validate(params)

        params = self.source.params()
        params[H3_GALLERY_STILL_GUIDE_PLAN_KEY]["execution_available"] = True
        with self.assertRaises(H3GalleryStillGuideError):
            self.source.validate(params)

    def test_launch_recovery_wrapper_uses_the_same_source_recheck(self):
        import typing

        namespace = {
            "Mapping": typing.Mapping,
            "Any": typing.Any,
            "_output_revision": self.source.revision,
        }
        load_launch_functions(namespace, "_validate_h3_gallery_still_guide_job")
        job = {
            "workspace": "project-a",
            "out_dir": str(self.root),
            "private": True,
            "explicit": True,
            "params": self.source.params(),
        }
        self.assertEqual(namespace["_validate_h3_gallery_still_guide_job"](job)["frame_index"], 62)
        self.source.save_image((5, 6, 7))
        with self.assertRaises(H3GalleryStillGuideError):
            namespace["_validate_h3_gallery_still_guide_job"](job)

        self.source.save_image((210, 30, 50))
        wrong_out_dir = self.root / "wrong-project"
        wrong_out_dir.mkdir()
        job["out_dir"] = str(wrong_out_dir)
        with self.assertRaises(H3GalleryStillGuideError):
            namespace["_validate_h3_gallery_still_guide_job"](job)

    def test_window_safety_bump_keeps_a_124_frame_request_to_one_native_clip(self):
        model_def = {
            "frames_minimum": 124,
            "frames_maximum": 345,
            "fps": 24,
            "frame_alignment_modulus": 17,
            "frame_alignment_remainder": 5,
            "frame_alignment_mode": "ceil",
        }

        def align(frames, _model_def):
            remainder = (frames - 5) % 17
            return frames if remainder == 0 else frames + (17 - remainder)

        namespace = {
            "wgp": types.SimpleNamespace(
                get_model_def=lambda _model: model_def,
                align_model_frame_count=align,
            ),
            "_H3_LONG_STUDIO_MODELS": frozenset({"minimax_h3"}),
        }
        load_launch_functions(namespace, "_prepare_h3_long_studio_request")
        params = {
            "model_type": "minimax_h3",
            "generation_mode": "video",
            "video_length": 124,
            "sliding_window_size": 133,  # 124 + latent(8) + safety unit(1)
            "h3_adaptive_conditioning": False,
            "image_start": str(self.source.path),
        }
        plan = namespace["_prepare_h3_long_studio_request"](params)
        self.assertIsNone(plan)
        self.assertEqual(params["video_length"], 124)

    def test_stale_source_at_publication_withholds_every_new_guide_final(self):
        import typing
        import uuid

        from services.job_lifecycle import GENERATED_MEDIA_EXTENSIONS

        generated_name = "fresh-guide.mp4"
        second_generated_name = "fresh-guide-repeat.mp4"
        existing_name = "prior-final.mp4"
        existing_path = self.root / existing_name
        existing_path.write_bytes(b"older legitimate final")
        (self.root / "prior-final.meta.json").write_text(
            __import__("json").dumps({
                "output_filename": existing_name,
                "artifact_class": "final",
                "private": True,
                "explicit": True,
            }),
            encoding="utf-8",
        )
        before = {
            self.source.path.name,
            "guide.meta.json",
            existing_name,
            "prior-final.meta.json",
        }
        generated_path = self.root / generated_name
        generated_path.write_bytes(b"rendered video without sidecar")
        second_generated_path = self.root / second_generated_name
        second_generated_path.write_bytes(b"another rendered window")
        self.assertEqual(
            classify_gallery_artifacts([{
                "name": generated_name,
                "meta": {},
            }])[generated_name],
            "final",
        )
        self.assertEqual(
            classify_gallery_artifacts([{
                "name": second_generated_name,
                "meta": {},
            }])[second_generated_name],
            "final",
        )
        guide_params = self.source.params()

        # Keep the revision token stable so this publication recheck must
        # detect the changed source bytes rather than a stat-token change.
        self.source.save_image((20, 190, 80))

        class PublicationFailure(Exception):
            def __init__(self, *args, **kwargs):
                super().__init__(*args)
                self.stage = kwargs.get("stage")
                self.code = kwargs.get("code")

        namespace = {
            "Any": typing.Any,
            "Mapping": typing.Mapping,
            "os": os,
            "uuid": uuid,
            "GENERATED_MEDIA_EXTENSIONS": GENERATED_MEDIA_EXTENSIONS,
            "_output_revision": self.source.revision,
            "_GenerationStageFailure": PublicationFailure,
            # Force both the marker and private quarantine paths to fail, so
            # the final safe-delete fallback is what prevents Gallery finality.
            "_atomic_write_json": lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(OSError("marker unavailable"))
            ),
            "_quarantine_recovery_artifact": lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(OSError("quarantine unavailable"))
            ),
        }
        load_launch_functions(
            namespace,
            "_validate_h3_gallery_still_guide_job",
            "_withhold_failed_h3_gallery_still_outputs",
        )
        load_nested_launch_function(
            namespace, "_run_generation", "_write_output_sidecars",
        )
        job = {
            "workspace": "project-a",
            "out_dir": str(self.root),
            "private": True,
            "explicit": True,
            "params": guide_params,
        }
        namespace.update({
            "job": job,
            "out_dir": str(self.root),
            "before": before,
            "job_id": "job-guide-publication",
            "file_names": [
                generated_name, second_generated_name, existing_name,
            ],
        })

        with self.assertRaises(PublicationFailure) as raised:
            namespace["_write_output_sidecars"](
                [generated_name, second_generated_name, existing_name],
            )
        self.assertEqual(raised.exception.stage, "publication")
        self.assertEqual(raised.exception.code, "publication_failed")
        self.assertFalse(generated_path.exists())
        self.assertFalse(second_generated_path.exists())
        self.assertEqual(existing_path.read_bytes(), b"older legitimate final")

        remaining_media = {
            path.name for path in self.root.iterdir()
            if not path.name.startswith(".")
            and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
        }
        sidecars = load_media_sidecars(str(self.root), remaining_media)
        classes = classify_gallery_artifacts([
            {"name": name, "meta": sidecars.get(name) or {}}
            for name in remaining_media
        ])
        self.assertNotIn(generated_name, classes)
        self.assertNotIn(second_generated_name, classes)
        self.assertEqual(classes[existing_name], "final")
        self.assertTrue(sidecars[existing_name]["private"])


class FakePreparationRequest:
    def __init__(self, source, body):
        self.state = types.SimpleNamespace(
            maestro_session_id=source.state.maestro_session_id,
            maestro_remote=source.state.maestro_remote,
        )
        self._body = copy.deepcopy(body)

    async def json(self):
        return copy.deepcopy(self._body)


class H3GalleryStillGuideRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = StillSourceFixture(self.root)
        self.queued: list[dict] = []
        self.probe_threads: list[int] = []
        self.revision_value = "revision-1"
        self.token = object()

        async def generate(preparation_request):
            self.queued.append(await preparation_request.json())
            self.assertIs(
                preparation_request.state._maestro_h3_gallery_still_guide_token,
                self.token,
            )
            return {"job_id": "job-guide", "status": "preparing", "held": False}

        self.ns = {
            "Request": object,
            "HTTPException": HTTPException,
            "asyncio": asyncio,
            "copy": copy,
            "os": os,
            "_H3_BASE_FL2VA_MODEL": "minimax_h3",
            "_H3_GALLERY_STILL_GUIDE_REQUEST_TOKEN": self.token,
            "_H3_GALLERY_STILL_GUIDE_SETTINGS": frozenset({
                "video_length", "resolution", "num_inference_steps",
                "guidance_scale", "seed", "activated_loras",
                "loras_multipliers", "tea_cache", "override_profile",
            }),
            "_GENERATION_MEDIA_INPUTS": (
                "image_start", "image_end", "image_refs", "image_guide",
                "image_mask", "video_guide", "video_guide2", "video_guide3",
                "video_mask", "video_source", "video_end", "audio_guide",
                "audio_guide2", "audio_guide3", "audio_guide4", "audio_guide5",
                "audio_guide6", "audio_conditioning_guide", "audio_source",
                "custom_guide", "voice_reference",
            ),
            "_request_project_workspace": lambda _request, workspace: workspace,
            "_require_project_access": lambda _request, _workspace, **_kwargs: str(self.root),
            "_require_remote_visible_models": lambda *_args: None,
            "_require_h3_legal_execution": lambda *_args: None,
            "_require_model_recipe_terms": lambda *_args: None,
            "_require_authorized_output": self._authorized_output,
            "_output_revision": lambda *_args: self.revision_value,
            "_GENERATION_MEDIA_INPUTS": (
                "image_start", "image_end", "image_refs", "image_guide",
                "image_mask", "video_guide", "video_guide2", "video_guide3",
                "video_mask", "video_source", "video_end", "audio_guide",
                "audio_guide2", "audio_guide3", "audio_guide4", "audio_guide5",
                "audio_guide6", "audio_conditioning_guide", "audio_source",
                "custom_guide", "voice_reference",
            ),
            "h3_integrity_is_pending": lambda *_args: False,
            "load_media_sidecars": lambda _root, names: (
                {self.source.path.name: self.source.sidecar}
                if self.source.path.name in names else {}
            ),
            "classify_gallery_artifacts": classify_gallery_artifacts,
            "wgp": types.SimpleNamespace(
                get_model_def=lambda _model: {
                    "frames_minimum": 124,
                    "frames_maximum": 345,
                    "frame_alignment_modulus": 17,
                    "frame_alignment_remainder": 5,
                    "frame_alignment_mode": "ceil",
                },
                align_model_frame_count=lambda frames, _model_def: (
                    frames if (frames - 5) % 17 == 0 else frames + (17 - (frames - 5) % 17)
                ),
                get_default_settings=lambda _model: {
                    "resolution": "608x352",
                    "num_inference_steps": 28,
                    "custom_settings": {
                        "h3_attention_engine": "sol_attn",
                        "h3_source_audio_mode": "lock_source",
                        "_h3_forged_default": True,
                    },
                    "image_refs": ["stale-default-reference"],
                    "video_guide": "stale-default-video",
                    "audio_guide": "stale-default-audio",
                    "audio_source": "stale-default-source",
                    "audio_path": "stale-default-audio-path",
                    "input_waveform": "stale-default-waveform",
                    "h3_native_boundary_conditioning": True,
                },
            ),
            "_inherit_media_access_policy": lambda *_args: {
                "private": self.source.sidecar["private"],
                "explicit": self.source.sidecar["explicit"],
            },
            "_GenerationPreparationRequest": FakePreparationRequest,
            "generate": generate,
        }
        load_launch_functions(self.ns, "h3_gallery_still_guide_endpoint")

    def _authorized_output(self, _request, workspace, name):
        if workspace != "project-a" or name != self.source.path.name:
            raise HTTPException(404, "Output file not found")
        return str(self.root), str(self.source.path), self.source.sidecar

    def request(self, **changes):
        body = {
            "workspace": "project-a",
            "name": self.source.path.name,
            "revision": "revision-1",
            "frame_index": 62,
            "model_type": "minimax_h3",
            "prompt": "A small painted robot turns toward the camera.",
            "settings": {"video_length": 124},
            "private_output": False,
            "explicit_output": False,
        }
        body.update(changes)

        async def read():
            return body

        return types.SimpleNamespace(
            json=read,
            state=types.SimpleNamespace(
                maestro_session_id="owner-session", maestro_remote=True,
            ),
        )

    def test_route_builds_one_trusted_still_and_inherits_source_privacy(self):
        loop_thread = threading.get_ident()
        original = probe_gallery_still

        def probe(path):
            self.probe_threads.append(threading.get_ident())
            return original(path)

        with patch("services.h3_gallery_still_guide.probe_gallery_still", side_effect=probe):
            response = asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.request()))
        self.assertEqual(response["job_id"], "job-guide")
        self.assertEqual(response["h3_guide_execution"]["frame_index"], 62)
        self.assertEqual(response["h3_guide_execution"]["guide_count"], 1)
        self.assertEqual(len(self.probe_threads), 2)
        self.assertTrue(all(thread_id != loop_thread for thread_id in self.probe_threads))
        params = self.queued[0]
        self.assertEqual(params["model_type"], "minimax_h3")
        self.assertEqual(params["image_start"], str(self.source.path))
        self.assertEqual(params["video_length"], 124)
        self.assertEqual(params["sliding_window_size"], 124)
        self.assertEqual(params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY], {
            "frame_index": 62,
        })
        self.assertNotIn("_h3_timeline_still_guide", params)
        self.assertEqual(params["video_prompt_type"], "")
        self.assertEqual(params["audio_prompt_type"], "")
        self.assertIsNone(params["video_guide"])
        self.assertIsNone(params["audio_guide"])
        self.assertIsNone(params["audio_source"])
        self.assertIsNone(params["audio_path"])
        self.assertIsNone(params["input_waveform"])
        self.assertEqual(params["image_refs"], [])
        self.assertEqual(params["custom_settings"]["h3_source_audio_mode"], "native")
        self.assertNotIn("_h3_forged_default", params["custom_settings"])
        self.assertTrue(params["private_output"])
        self.assertTrue(params["explicit_output"])
        source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        self.assertTrue(source["source_private"])
        self.assertTrue(source["source_explicit"])
        receipt = self.source.validate(params)
        self.assertEqual(receipt["workspace"], params["workspace"])
        self.assertEqual(params["image_start"], str(self.source.path))

        # The ordinary generation authorizer resolves the committed Gallery
        # path in place; preparation/recovery continue to bind it to out_dir.
        authorization_namespace = {
            "Request": object,
            "_GENERATION_MEDIA_INPUTS": ("image_start",),
            "_resolve_authorized_request_media": (
                lambda _request, value, workspace: (
                    value if workspace == "project-a" else None
                )
            ),
        }
        load_launch_functions(
            authorization_namespace, "_authorize_generation_media_inputs",
        )
        authorization_namespace["_authorize_generation_media_inputs"](
            object(), params, "project-a",
        )
        self.assertEqual(params["image_start"], str(self.source.path))
        self.assertEqual(self.source.validate(params)["target_frames"], 124)

    def test_route_rejects_stale_unknown_unsupported_and_endpoint_frames(self):
        self.revision_value = "new-revision"
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.request()))
        self.assertEqual(raised.exception.status_code, 409)
        self.revision_value = "revision-1"

        for body_change in (
            {"model_type": "minimax_h3_ref2va"},
            {"settings": {"video_length": 124, "audio_guide": "bad"}},
            {"custom_settings": {H3_GALLERY_STILL_GUIDE_CUSTOM_KEY: {"frame_index": 62}}},
            {"frame_index": 0},
            {"frame_index": 123},
        ):
            with self.subTest(body_change=body_change), self.assertRaises(HTTPException) as raised:
                asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.request(**body_change)))
            self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.queued, [])

    def test_normal_generate_api_rejects_the_new_private_custom_setting(self):
        # Drive the public route through its normal admission prefix. Only the
        # dedicated route carries the identity token that opens this field.
        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "asyncio": asyncio,
            "_ENHANCED_PROMPT_CARDINALITY_KEY": "_enhanced_prompt_cardinality",
            "_reject_client_krea_authority": lambda _body: None,
            "_require_project_access": lambda *_args, **_kwargs: str(self.root),
            "_normalize_project_asset_ref_descriptors": lambda _value: [],
            "_reject_client_director_image_role_internals": lambda _body: None,
            "_director_image_role_wire_mode": lambda _body: "legacy",
            "wgp": types.SimpleNamespace(
                get_model_def=lambda _model: {"architecture": "minimax_h3"},
            ),
            "_require_remote_visible_models": lambda *_args: None,
            "_require_h3_legal_execution": lambda *_args: None,
            "_require_model_recipe_terms": lambda *_args: None,
        }

        async def request_body():
            return {
                "workspace": "project-a",
                "model_type": "minimax_h3",
                "custom_settings": {
                    H3_GALLERY_STILL_GUIDE_CUSTOM_KEY: {"frame_index": 62},
                },
            }

        request = types.SimpleNamespace(
            json=request_body,
            state=types.SimpleNamespace(
                maestro_session_id="owner-session",
                maestro_remote=False,
            ),
        )
        load_launch_functions(
            namespace,
            "_reject_client_h3_internal_state",
            "generate",
        )
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(namespace["generate"](request))
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
