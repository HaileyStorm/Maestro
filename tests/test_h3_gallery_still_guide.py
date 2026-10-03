"""CPU-only checks for the bounded Gallery H3 still-guide route and replay gate."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
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
    build_gallery_still_guide_pair_plan,
    build_gallery_still_guide_multiple_plan,
    make_gallery_still_guide_triple_source,
    make_gallery_still_guide_pair_source,
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


def load_launch_class(namespace: dict, name: str) -> None:
    import ast

    tree = ast.parse((ROOT / "app/launch.py").read_text(encoding="utf-8"))
    node = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "launch.py", "exec"), namespace)


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
    def __init__(self, root: Path, *, private: bool = True, explicit: bool = True, name: str = "guide.png"):
        self.root = root
        self.path = root / name
        self.save_image((210, 30, 50))
        self.sidecar = {
            "workspace": "project-a",
            "output_filename": self.path.name,
            "artifact_class": "final",
            "private": private,
            "explicit": explicit,
        }
        (root / (self.path.stem + ".meta.json")).write_text(
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


class H3GalleryStillGuidePairServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = StillSourceFixture(self.root, private=False, explicit=False)
        self.second = StillSourceFixture(self.root, name="second.png")
        self.second.save_image((0, 200, 30))

    def params(self):
        params = self.first.params()
        first = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        probe = probe_gallery_still(str(self.second.path))
        single = build_gallery_still_guide_plan(sha256=probe.sha256, frame_index=90, target_frames=124)
        second = make_gallery_still_guide_source(
            workspace="project-a", name="second.png", revision="revision-1",
            probe=probe, frame_index=90, target_frames=124, plan=single,
            source_private=True, source_explicit=True,
        )
        joint = build_gallery_still_guide_pair_plan(
            sha256=first["sha256"], frame_index=62,
            second_sha256=second["sha256"], second_frame_index=90, target_frames=124,
        )
        params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY] = make_gallery_still_guide_pair_source(first, second, joint)
        params[H3_GALLERY_STILL_GUIDE_PLAN_KEY] = joint
        params["image_end"] = str(self.second.path)
        params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY] = {"frame_index":62,"end_frame_index":90}
        return params

    def triple_params(self):
        params = self.params()
        self.third = StillSourceFixture(self.root, name="third.png", private=False, explicit=False)
        self.third.save_image((20, 30, 240))
        third = self.third.params()[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        third["frame_index"] = 31
        source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        first = {key: value for key, value in source.items() if key != "second_source"}
        second = source["second_source"]
        plan = build_gallery_still_guide_multiple_plan([first, second, third])
        params[H3_GALLERY_STILL_GUIDE_PLAN_KEY] = plan
        params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY] = make_gallery_still_guide_triple_source(first, second, third, plan)
        params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY].update({
            "third_frame_index": 31, "third_still_path": str(self.third.path),
        })
        return params

    def test_three_stills_preserve_picture_order_and_every_exact_binding(self):
        params = self.triple_params()
        before = copy.deepcopy(params)
        receipt = self.first.validate(params)
        self.assertEqual(receipt["guide_count"], 3)
        self.assertEqual(receipt["frame_indices"], [62, 90, 31])
        self.assertEqual([item["name"] for item in receipt["sources"]], ["guide.png", "second.png", "third.png"])
        self.assertEqual(params, before)
        # Use the actual worker manifest branch to prove the private path and
        # position survive preparation without becoming image_refs.
        import ast
        namespace = {"wgp": types.SimpleNamespace(task_id=1, get_model_min_frames_and_step=lambda _model: (124,17,345)), "raw_params": params}
        load_launch_functions(namespace, "_apply_generation_end_image_trim")
        worker = next(node for node in ast.parse((ROOT / "app/launch.py").read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "_run_generation")
        branch = next(node.orelse for node in ast.walk(worker) if isinstance(node, ast.If) and any(
            isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "manifest" for target in item.targets)
            and isinstance(item.value, ast.List) for item in node.orelse
        ))
        exec(compile(ast.Module(body=branch, type_ignores=[]), "launch.py", "exec"), namespace)
        prepared = namespace["manifest"][0]["params"]
        self.assertEqual(self.first.validate(prepared)["frame_indices"], [62, 90, 31])
        self.assertEqual(prepared["image_refs"], [])
        self.assertEqual(prepared["trim_tail_frames"], 0)
        self.assertEqual(prepared["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY]["third_still_path"], str(self.third.path))

    def test_triple_publication_strips_private_transport_and_preserves_exact_receipt(self):
        import ast
        params = self.triple_params()
        source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        writer = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == "_write_output_sidecars")
        guards = [node for node in writer.body if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name)
                  and node.test.func.id == "isinstance" and isinstance(node.test.args[0], ast.Name)
                  and node.test.args[0].id == "guide_source"
                  and not any(isinstance(item, ast.Try) for item in node.body)]
        self.assertEqual(len(guards), 2)
        namespace = {"guide_source": source, "sidecar_params": copy.deepcopy(params), "sidecar": {}}
        exec(compile(ast.Module(body=guards, type_ignores=[]), "launch.py", "exec"), namespace)
        published = namespace["sidecar_params"]
        self.assertNotIn(H3_GALLERY_STILL_GUIDE_SOURCE_KEY, published)
        self.assertNotIn(H3_GALLERY_STILL_GUIDE_PLAN_KEY, published)
        self.assertNotIn(H3_GALLERY_STILL_GUIDE_CUSTOM_KEY, published["custom_settings"])
        self.assertNotIn(str(self.third.path), __import__("json").dumps(published))
        receipt = namespace["sidecar"]["h3_guide_execution"]
        self.assertEqual(receipt["guide_count"], 3)
        self.assertEqual(receipt["frame_indices"], [62, 90, 31])

    def test_three_source_plan_rejects_malformed_types_without_raw_type_errors(self):
        params = self.triple_params()
        source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        records = [{key: value for key, value in source.items() if key not in {"second_source", "third_source"}},
                   source["second_source"], source["third_source"]]
        for field, value in (("name", []), ("frame_index", []), ("frame_index", True),
                             ("target_frames", []), ("workspace", {})):
            invalid = copy.deepcopy(records)
            invalid[2][field] = value
            with self.subTest(field=field), self.assertRaises(H3GalleryStillGuideError):
                build_gallery_still_guide_multiple_plan(invalid)

    def test_third_still_replay_refuses_revision_bytes_path_policy_and_position_drift(self):
        mutations = (
            lambda p: p["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY].update(third_frame_index=90),
            lambda p: p["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY].update(third_still_path=str(self.second.path)),
            lambda p: p[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]["third_source"].update(workspace="project-b"),
            lambda p: p[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]["third_source"].update(source_private=True),
            lambda p: p[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]["third_source"].update(plan_sha256="changed"),
            lambda p: p[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]["third_source"].update(name="second.png"),
            lambda p: p[H3_GALLERY_STILL_GUIDE_SOURCE_KEY].pop("second_source"),
            lambda p: self.third.save_image((200, 100, 200)),
        )
        for index, mutate in enumerate(mutations):
            params = self.triple_params()
            mutate(params)
            with self.subTest(index=index), self.assertRaises(H3GalleryStillGuideError):
                self.first.validate(params)
        params = self.triple_params()
        self.first.revision = lambda _path, _root, name: "revision-2" if name == "third.png" else "revision-1"
        with self.assertRaises(H3GalleryStillGuideError): self.first.validate(params)

    def test_binds_both_stills_in_picture_order_without_mutating_request(self):
        params = self.params()
        before = copy.deepcopy(params)
        result = self.first.validate(params)
        self.assertEqual(result["guide_count"], 2)
        self.assertEqual(result["frame_indices"], [62,90])
        self.assertEqual([s["name"] for s in result["sources"]], ["guide.png","second.png"])
        self.assertEqual(params, before)

    def test_worker_manifest_keeps_second_still_and_full_target_without_endpoint_trim(self):
        import ast

        params = self.params()
        # Recovery can carry an old endpoint trim; the worker must override it.
        params["trim_tail_frames"] = 17
        fake_wgp = types.SimpleNamespace(
            task_id=1, get_model_min_frames_and_step=lambda _model: (124, 17, 345),
        )
        namespace = {"wgp": fake_wgp, "raw_params": params}
        load_launch_functions(namespace, "_apply_generation_end_image_trim")
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_generation")
        # Execute the actual single-clip worker manifest branch, with no model
        # or worker loop loaded. This exercises the slot/trim handoff to WGP.
        branch = next(node.orelse for node in ast.walk(worker) if isinstance(node, ast.If) and any(
            isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "manifest" for target in item.targets)
            and isinstance(item.value, ast.List) for item in node.orelse
        ))
        exec(compile(ast.Module(body=branch, type_ignores=[]), "launch.py", "exec"), namespace)
        prepared = namespace["manifest"][0]["params"]
        self.assertEqual(prepared["trim_tail_frames"], 0)
        self.assertEqual(prepared["video_length"], 124)
        self.assertEqual(prepared["image_end"], str(self.second.path))
        self.assertEqual(self.first.validate(prepared)["frame_indices"], [62, 90])
        ordinary = {"model_type": "minimax_h3", "video_length": 124, "image_end": str(self.second.path)}
        namespace["_apply_generation_end_image_trim"](ordinary)
        self.assertEqual(ordinary["trim_tail_frames"], 17)
        # Unvalidated replay cannot publish a shortened timeline as this plan.
        prepared["trim_tail_frames"] = 17
        with self.assertRaises(H3GalleryStillGuideError):
            self.first.validate(prepared)

    def test_rejects_changed_second_image_bytes_despite_same_revision_callback(self):
        params = self.params()
        self.second.save_image((0,0,200))
        with self.assertRaises(H3GalleryStillGuideError):
            self.first.validate(params)

    def test_rejects_second_source_path_substitution(self):
        params = self.params()
        params["image_end"] = params["image_start"]
        with self.assertRaises(H3GalleryStillGuideError):
            self.first.validate(params)

    def test_requires_private_and_explicit_flags_from_second_source(self):
        for flags in ({"job_private":False}, {"job_explicit":False}):
            with self.subTest(flags=flags), self.assertRaises(H3GalleryStillGuideError):
                self.first.validate(self.params(), **flags)

    def test_rejects_second_project_binding_drift(self):
        params = self.params()
        sidecar = dict(self.second.sidecar, workspace="project-b")
        (self.root/"second.meta.json").write_text(__import__("json").dumps(sidecar))
        with self.assertRaises(H3GalleryStillGuideError):
            self.first.validate(params)

    def test_rejects_duplicate_or_noninteger_or_endpoint_frames(self):
        first = self.params()[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        for frame in (62,0,123,True,90.0):
            with self.subTest(frame=frame), self.assertRaises(H3GalleryStillGuideError):
                build_gallery_still_guide_pair_plan(
                    sha256=first["sha256"], frame_index=62,
                    second_sha256=first["second_source"]["sha256"],
                    second_frame_index=frame, target_frames=124,
                )

    def test_rejects_reordered_joint_plan_or_custom_frame_drift(self):
        params = self.params()
        source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
        params[H3_GALLERY_STILL_GUIDE_PLAN_KEY] = build_gallery_still_guide_pair_plan(
            sha256=source["second_source"]["sha256"], frame_index=90,
            second_sha256=source["sha256"], second_frame_index=62, target_frames=124,
        )
        with self.assertRaises(H3GalleryStillGuideError): self.first.validate(params)
        params = self.params()
        params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY]["end_frame_index"] = 89
        with self.assertRaises(H3GalleryStillGuideError): self.first.validate(params)

    def test_rejects_unbound_additional_media_and_second_revision_drift(self):
        for field in ("audio_guide","video_guide","image_refs"):
            params = self.params()
            params[field] = ["unbound.png"] if field == "image_refs" else "unbound.mp4"
            with self.subTest(field=field), self.assertRaises(H3GalleryStillGuideError):
                self.first.validate(params)
        params = self.params()
        self.first.revision = lambda path, root, name: "revision-2" if name == "second.png" else "revision-1"
        with self.assertRaises(H3GalleryStillGuideError): self.first.validate(params)


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
    def __init__(self, source, body, *, admission_account_session=False):
        self.state = types.SimpleNamespace(
            maestro_session_id=source.state.maestro_session_id,
            maestro_remote=source.state.maestro_remote,
            maestro_account_session_id=(
                source.state.maestro_account_session_id
                if admission_account_session else ""
            ),
        )
        self._body = copy.deepcopy(body)

    async def json(self):
        return copy.deepcopy(self._body)


class H3GuideAdmissionRequestTests(unittest.TestCase):
    def test_immediate_admission_keeps_account_marker_but_worker_drops_it(self):
        namespace = {
            "Request": object,
            "copy": copy,
            "ipaddress": ipaddress,
        }
        load_launch_class(namespace, "_GenerationPreparationRequest")
        source = types.SimpleNamespace(
            headers={"x-forwarded-proto": "https"},
            base_url="https://maestro.example/",
            client=types.SimpleNamespace(host="127.0.0.1"),
            state=types.SimpleNamespace(
                maestro_session_id="c" * 32,
                maestro_remote=True,
                maestro_account_principal={"id": "a" * 32, "role": "owner"},
                maestro_account_session_id="b" * 32,
            ),
        )
        preparation = namespace["_GenerationPreparationRequest"]
        admitted = preparation(
            source, {"workspace": "project-a"},
            admission_account_session=True,
        )
        self.assertEqual(admitted.state.maestro_account_session_id, "b" * 32)
        self.assertEqual(admitted.state.maestro_account_principal["role"], "owner")
        self.assertEqual(preparation(source).state.maestro_account_session_id, "")


class H3GalleryStillGuideRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = StillSourceFixture(self.root)
        self.queued: list[dict] = []
        self.admission_markers: list[str] = []
        self.preparation_request = None
        self.probe_threads: list[int] = []
        self.revision_value = "revision-1"
        self.token = object()

        async def generate(preparation_request):
            self.preparation_request = preparation_request
            self.admission_markers.append(
                preparation_request.state.maestro_account_session_id
            )
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
        load_launch_functions(self.ns, "_resolve_h3_gallery_still_guide_source", "h3_gallery_still_guide_endpoint")

    def _authorized_output(self, _request, workspace, name):
        if workspace != "project-a" or name != self.source.path.name:
            raise HTTPException(404, "Output file not found")
        return str(self.root), str(self.source.path), self.source.sidecar

    def pair_request(self, **changes):
        self.source = StillSourceFixture(self.root, private=False, explicit=False)
        self.second = StillSourceFixture(self.root, name="second.png")
        self.authorized_names = []

        def authorize(_request, workspace, name):
            self.authorized_names.append(name)
            source = next((item for item in (self.source, self.second) if item.path.name == name), None)
            if workspace != "project-a" or source is None:
                raise HTTPException(404, "Output file not found")
            return str(self.root), str(source.path), source.sidecar

        self.ns["_require_authorized_output"] = authorize
        self.ns["load_media_sidecars"] = load_media_sidecars
        self.ns["_inherit_media_access_policy"] = lambda *_args: {"private": False, "explicit": False}
        return self.request(second_still={
            "name": self.second.path.name, "revision": "revision-1", "frame_index": 90,
        }, **changes)

    def triple_request(self, **changes):
        self.pair_request()
        # Only the third source carries protected access flags in this case.
        self.second.sidecar.update(private=False, explicit=False)
        (self.root / "second.meta.json").write_text(__import__("json").dumps(self.second.sidecar))
        self.third = StillSourceFixture(self.root, name="third.png")
        self.third.save_image((10, 20, 230))
        def authorize(_request, workspace, name):
            self.authorized_names.append(name)
            source = next((item for item in (self.source, self.second, self.third) if item.path.name == name), None)
            if workspace != "project-a" or source is None: raise HTTPException(404, "Output file not found")
            return str(self.root), str(source.path), source.sidecar
        self.ns["_require_authorized_output"] = authorize
        body = {"second_still": {"name": "second.png", "revision": "revision-1", "frame_index": 90},
                "third_still": {"name": "third.png", "revision": "revision-1", "frame_index": 31}}
        body.update(changes)
        return self.request(**body)

    def test_triple_route_authorizes_every_source_and_inherits_third_policy_without_scanning_prompt(self):
        prompt = "<Picture 3> Consenting adult lovers, a violent battlefield, and controversial political satire."
        response = asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.triple_request(prompt=prompt)))
        self.assertEqual(self.authorized_names, ["guide.png", "second.png", "third.png"])
        self.assertEqual(response["h3_guide_execution"]["frame_indices"], [62, 90, 31])
        self.assertEqual(response["h3_guide_execution"]["guide_count"], 3)
        params = self.queued[0]
        self.assertEqual(params["prompt"], prompt)
        self.assertEqual(params["image_refs"], [])
        self.assertEqual(params["image_prompt_type"], "SE")
        self.assertTrue(params["private_output"])
        self.assertTrue(params["explicit_output"])
        self.assertEqual(params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY], {
            "frame_index": 62, "end_frame_index": 90, "third_frame_index": 31, "third_still_path": str(self.third.path),
        })
        self.assertEqual(self.source.validate(params)["guide_count"], 3)

    def test_triple_route_rejects_missing_second_duplicate_position_client_path_and_stale_third(self):
        request = self.triple_request()
        self.ns["_output_revision"] = lambda _path, _root, name: "changed" if name == "third.png" else "revision-1"
        with self.assertRaises(HTTPException) as error:
            asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](request))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.queued, [])
        self.ns["_output_revision"] = lambda *_args: "revision-1"
        for third in (None,
            {"name":"third.png","revision":"revision-1","frame_index":True},
            {"name":"third.png","revision":"revision-1","frame_index":90},
            {"name":"third.png","revision":"revision-1","frame_index":123},
            {"name":"second.png","revision":"revision-1","frame_index":31},
            {"name":"third.png","revision":"revision-1","frame_index":31,"path":"foreign"},
        ):
            with self.subTest(third=third), self.assertRaises(HTTPException) as error:
                asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.triple_request(third_still=third)))
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(HTTPException):
            asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.request(third_still={"name":"third.png","revision":"revision-1","frame_index":31})))
        self.assertEqual(self.queued, [])

    def test_pair_route_authorizes_both_sources_and_preserves_second_privacy(self):
        response = asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.pair_request()))
        self.assertEqual(self.authorized_names, ["guide.png", "second.png"])
        self.assertEqual(response["h3_guide_execution"]["guide_count"], 2)
        self.assertEqual(response["h3_guide_execution"]["frame_indices"], [62, 90])
        params = self.queued[0]
        self.assertEqual(params["image_start"], str(self.source.path))
        self.assertEqual(params["image_end"], str(self.second.path))
        # WGP's input cleaning retains the second image only in SE mode.
        self.assertEqual(params["image_prompt_type"], "SE")
        self.assertTrue(params["private_output"])
        self.assertTrue(params["explicit_output"])
        self.assertEqual(params["custom_settings"][H3_GALLERY_STILL_GUIDE_CUSTOM_KEY], {
            "frame_index": 62, "end_frame_index": 90,
        })
        receipt = self.source.validate(params)
        self.assertEqual(receipt["frame_indices"], [62, 90])
        self.assertEqual(self.preparation_request.state.maestro_account_session_id, "")
        self.assertEqual(self.admission_markers, ["s" * 32])

    def test_pair_route_rejects_invalid_and_stale_second_source_before_queue(self):
        request = self.pair_request()
        self.ns["_output_revision"] = lambda _path, _root, name: "changed" if name == "second.png" else "revision-1"
        with self.assertRaises(HTTPException) as error:
            asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](request))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.queued, [])

        for second in (
            None, {"name": "second.png", "revision": "revision-1", "frame_index": True},
            {"name": "second.png", "revision": "revision-1", "frame_index": 62},
            {"name": "guide.png", "revision": "revision-1", "frame_index": 90},
            {"name": "second.png", "revision": "revision-1", "frame_index": 123},
            {"name": "second.png", "revision": "revision-1", "frame_index": 90, "path": "foreign"},
        ):
            self.ns["_output_revision"] = lambda *_args: "revision-1"
            with self.subTest(second=second), self.assertRaises(HTTPException) as error:
                asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.request(second_still=second)))
            self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.queued, [])

    def test_pair_route_preserves_authorized_creative_prompts(self):
        for prompt in (
            "Two consenting adult lovers in an intimate bedroom scene.",
            "A battlefield aftermath with blood and destroyed vehicles.",
            "A controversial political satire in a city square.",
        ):
            with self.subTest(prompt=prompt):
                response = asyncio.run(self.ns["h3_gallery_still_guide_endpoint"](self.pair_request(prompt=prompt)))
                self.assertEqual(response["job_id"], "job-guide")
                self.assertEqual(self.queued[-1]["prompt"], prompt)
                self.assertEqual(self.queued[-1]["image_end"], str(self.second.path))
                self.assertEqual(self.source.validate(self.queued[-1])["guide_count"], 2)

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
                maestro_account_session_id="s" * 32,
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
        self.assertEqual(self.admission_markers, ["s" * 32])
        self.assertEqual(self.preparation_request.state.maestro_account_session_id, "")
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
