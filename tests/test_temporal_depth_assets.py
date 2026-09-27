"""Model-free regressions for LTX temporal-depth preprocessor provisioning."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_SERVICE_PATH = _APP / "services" / "managed_preprocessors.py"
_FILES_LOCATOR_PATH = _APP / "shared" / "utils" / "files_locator.py"
_WGP_PATH = _APP / "wgp.py"
_LAUNCH_PATH = _APP / "launch.py"
_GITIGNORE_PATH = _ROOT / ".gitignore"
_VENDOR_ROOT = _APP / "preprocessing" / "video_depth_anything"


def _load_service():
    app_path = str(_APP)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)
    files_spec = importlib.util.spec_from_file_location(
        "shared.utils.files_locator", _FILES_LOCATOR_PATH,
    )
    if files_spec is None or files_spec.loader is None:
        raise AssertionError("Could not load files_locator")
    files_module = importlib.util.module_from_spec(files_spec)
    files_spec.loader.exec_module(files_module)

    shared_package = types.ModuleType("shared")
    shared_package.__path__ = [str(_APP / "shared")]
    utils_package = types.ModuleType("shared.utils")
    utils_package.__path__ = [str(_APP / "shared" / "utils")]
    utils_package.files_locator = files_module
    shared_package.utils = utils_package

    spec = importlib.util.spec_from_file_location(
        "managed_preprocessors_test", _SERVICE_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load managed_preprocessors")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            "shared": shared_package,
            "shared.utils": utils_package,
            "shared.utils.files_locator": files_module,
        },
    ):
        spec.loader.exec_module(module)
    return module


def _load_flags():
    app_path = str(_APP)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)
    return importlib.import_module("services.video_prompt_flags")


def _load_ast_function(path, name, *, class_name=None, namespace=None):
    """Load one actual function body without importing its heavy module."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if class_name is None:
        candidates = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
    else:
        class_node = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        candidates = [
            node for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
    if len(candidates) != 1:
        raise AssertionError(f"Expected one {name} function in {path}")
    function_node = ast.fix_missing_locations(candidates[0])
    # A method's staticmethod decorator belongs to its class descriptor; the
    # extracted function body is called directly in this model-free harness.
    function_node.decorator_list = []
    scope = dict(namespace or {})
    exec(compile(ast.Module(body=[function_node], type_ignores=[]), str(path), "exec"), scope)
    return scope[name]


def _load_wgp_handlers(*names, namespace=None):
    """Execute selected actual WGP handlers without importing model runtime."""

    scope = dict(namespace or {})
    for name in names:
        scope[name] = _load_ast_function(
            _WGP_PATH,
            name,
            namespace=scope,
        )
    return scope


def _evaluate_assignment_rhs(path, target_name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == target_name
            for target in node.targets
        )
    ]
    if len(assignments) != 1:
        raise AssertionError(f"Expected one assignment to {target_name} in {path}")
    expression = ast.Expression(body=assignments[0].value)
    return eval(compile(ast.fix_missing_locations(expression), str(path), "eval"), namespace)


class _FakeResponse:
    def __init__(self, payload: bytes, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024 * 1024):
        for offset in range(0, len(self.payload), max(1, chunk_size)):
            yield self.payload[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class TestTemporalDepthAssetRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_service()

    def test_official_variants_are_pinned_and_sized(self):
        base = self.module.VIDEO_DEPTH_CHECKPOINTS["vitb"]
        large = self.module.VIDEO_DEPTH_CHECKPOINTS["vitl"]

        self.assertEqual(base["size"], 458_247_082)
        self.assertEqual(
            base["sha256"],
            "775e578e8f9431ec0496514aa466bd0a1f67c28d0f518267809f35a43c04329b",
        )
        self.assertEqual(large["size"], 1_538_392_012)
        self.assertEqual(
            large["sha256"],
            "43df27c6b396042ba34ff7b798ab279f64d204d2e86d7a373968f8fa36d0e6fa",
        )
        for checkpoint in (base, large):
            self.assertEqual(len(checkpoint["revision"]), 40)
            self.assertEqual(checkpoint["license"], "CC-BY-NC-4.0")

    def test_temporal_depth_detection_handles_ui_and_named_values(self):
        uses = self.module.uses_temporal_depth
        for value in (
            "TVG", "PTVG", "TEVG", "TVGT", "PTVGT", "TEVGT",
            "depth_temporal", "depth_temporalT",
        ):
            with self.subTest(value=value):
                self.assertTrue(uses({"video_prompt_type": value}))
        self.assertTrue(uses({"video_prompt_type": ["P", "T", "V", "G"]}))
        self.assertTrue(uses({"video_prompt_type": ["P", "T", "V", "G", "T"]}))
        for value in ("", "PVG", "DVG", "T", "VGT", "PVGT", "DVGT", None):
            with self.subTest(value=value):
                self.assertFalse(uses({"video_prompt_type": value}))
        self.assertFalse(uses(None))

    def test_video_prompt_decoder_separates_alignment_from_depth(self):
        decode = self.module.decode_video_prompt_type
        for value, control_flags, aligned, temporal_depth in (
            ("T", "", True, False),
            ("PVGT", "PVG", True, False),
            ("DVGT", "DVG", True, False),
            ("PTVG", "PTVG", False, True),
            ("PTVGT", "PTVG", True, True),
            ("TEVGT", "TEVG", True, True),
            ("depth_temporalT", "depth_temporal", True, True),
        ):
            with self.subTest(value=value):
                decoded = decode(value)
                self.assertEqual(decoded.control_flags, control_flags)
                self.assertEqual(decoded.timeline_aligned, aligned)
                self.assertEqual(decoded.uses_temporal_depth, temporal_depth)

        # An unordered legacy input still receives conservative depth
        # provisioning because its items cannot identify a trailing marker.
        unordered = decode({"P", "V", "G", "T"})
        self.assertFalse(unordered.timeline_aligned)
        self.assertTrue(unordered.uses_temporal_depth)

    def test_classic_alignment_transitions_preserve_internal_depth_flags(self):
        flags = _load_flags()
        set_alignment = flags.set_video_prompt_type_alignment
        decode = flags.decode_video_prompt_type
        aligned = set_alignment("PTVG", "T")
        self.assertEqual(aligned, "PTVGT")
        self.assertTrue(decode(aligned).timeline_aligned)
        self.assertTrue(decode(aligned).uses_temporal_depth)

        unaligned = set_alignment(aligned, "")
        self.assertEqual(unaligned, "PTVG")
        self.assertFalse(decode(unaligned).timeline_aligned)
        self.assertTrue(decode(unaligned).uses_temporal_depth)

    def test_invalid_variant_fails_before_network_access(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported.*variant"):
            self.module.ensure_video_depth_checkpoint("vitg")

    def test_h3_lora_affine_packages_are_revision_pinned(self):
        maps = self.module.MINIMAX_H3_LORA_AFFINE_MAPS
        self.assertEqual(set(maps), {"fl2va", "ref2va"})
        expected = {
            ("fl2va", 8): (
                130_072,
                "a42778e02ab2708dc70e23837ec4d3061b44f938c940decbc7a5b91f2c27c59e",
            ),
            ("ref2va", 8): (
                130_072,
                "7179899e59fce9c36038cd6c0c57edaced0032c769c436cef234b07bf809381f",
            ),
        }
        for key, (size, sha256) in expected.items():
            architecture, width = key
            spec = maps[architecture][width]
            self.assertEqual(spec["size"], size)
            self.assertEqual(spec["sha256"], sha256)
            self.assertIn(
                "1830091bf4b27df2f901920d55b1fb748f33e7eb",
                spec["url"],
            )


class TestTemporalDepthDownloadSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_service()

    @staticmethod
    def _spec(payload: bytes) -> dict:
        return {
            "label": "Fixture Depth",
            "repo_id": "example/depth",
            "revision": "a" * 40,
            "filename": "fixture.pth",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "license": "test-only",
        }

    def test_download_is_verified_and_atomically_published(self):
        payload = (b"temporal-depth-fixture-" * 50_000) + b"done"
        spec = self._spec(payload)
        response = _FakeResponse(payload)
        progress = []

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "depth", "fixture.pth")
            with mock.patch.object(
                self.module.requests, "get", return_value=response,
            ) as request:
                result = self.module._download_with_resume(
                    spec, target, progress.append,
                )

            self.assertEqual(result, target)
            self.assertEqual(Path(target).read_bytes(), payload)
            self.assertFalse(os.path.exists(target + ".part"))
            self.assertTrue(response.closed)
            self.assertTrue(any("Downloading Fixture Depth" in item for item in progress))
            self.assertTrue(any("Verifying Fixture Depth" in item for item in progress))
            request.assert_called_once()
            requested_url = request.call_args.args[0]
            self.assertIn(spec["revision"], requested_url)
            self.assertEqual(request.call_args.kwargs["headers"], {})

    def test_partial_download_resumes_with_range_header(self):
        payload = b"resume-this-checkpoint-safely"
        split = 9
        spec = self._spec(payload)
        response = _FakeResponse(payload[split:], status_code=206)

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "depth", "fixture.pth")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            Path(target + ".part").write_bytes(payload[:split])
            with mock.patch.object(
                self.module.requests, "get", return_value=response,
            ) as request:
                self.module._download_with_resume(spec, target)

            self.assertEqual(Path(target).read_bytes(), payload)
            self.assertEqual(
                request.call_args.kwargs["headers"],
                {"Range": f"bytes={split}-"},
            )

    def test_verified_downloader_accepts_a_pinned_direct_source_url(self):
        payload = b"small-h3-affine-map"
        spec = self._spec(payload)
        spec["url"] = "https://raw.example.invalid/pinned/affine.sft"

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "minimax_h3", "affine.sft")
            with mock.patch.object(
                self.module.requests,
                "get",
                return_value=_FakeResponse(payload),
            ) as request:
                self.module._download_with_resume(spec, target)

            self.assertEqual(request.call_args.args[0], spec["url"])
            self.assertEqual(Path(target).read_bytes(), payload)

    def test_hash_mismatch_never_publishes_checkpoint(self):
        payload = b"expected checkpoint"
        corrupted = b"corrupt! checkpoint"
        self.assertEqual(len(payload), len(corrupted))
        spec = self._spec(payload)

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "depth", "fixture.pth")
            with mock.patch.object(
                self.module.requests,
                "get",
                return_value=_FakeResponse(corrupted),
            ):
                with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                    self.module._download_with_resume(spec, target)

            self.assertFalse(os.path.exists(target))
            self.assertFalse(os.path.exists(target + ".part"))


class TestTemporalDepthWiring(unittest.TestCase):
    def test_preflight_runs_before_managed_loras_and_model_generation(self):
        launch = _LAUNCH_PATH.read_text(encoding="utf-8")
        preflight = launch.index("ensure_video_depth_checkpoint(")
        managed_lora = launch.index(
            "_ensure_managed_loras_present(", preflight,
        )
        generation = launch.index("wgp.generate_video(", managed_lora)
        self.assertLess(preflight, managed_lora)
        self.assertLess(managed_lora, generation)

    def test_non_rest_paths_keep_a_late_provisioning_fallback(self):
        wgp = _WGP_PATH.read_text(encoding="utf-8")
        temporal_branch = wgp.index('elif process_type=="depth_temporal":')
        next_branch = wgp.index('elif process_type=="gray":', temporal_branch)
        segment = wgp[temporal_branch:next_branch]
        self.assertIn("ensure_video_depth_checkpoint(variant)", segment)
        self.assertNotIn(
            'fl.locate_file(f"depth/video_depth_anything_', segment,
        )

    def test_wgp_uses_decoded_alignment_for_runtime_and_classic_controls(self):
        wgp = _WGP_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "reset_control_aligment = video_prompt_type_flags.timeline_aligned",
            wgp,
        )
        self.assertIn(
            "filter_letters(video_prompt_type_flags.control_flags, video_guide_processes)",
            wgp,
        )
        alignment = wgp[
            wgp.index("def refresh_video_prompt_type_alignment("):
            wgp.index("def refresh_video_prompt_type_video_guide(")
        ]
        self.assertIn("set_video_prompt_type_alignment(", alignment)
        self.assertIn("update_video_prompt_type_flags(", wgp)
        self.assertIn(
            "decode_video_prompt_type(video_video_prompt_type).control_flags",
            wgp,
        )
        self.assertIn(
            "decode_video_prompt_type(video_prompt_type_value).timeline_aligned",
            wgp,
        )

    def test_actual_classic_handlers_keep_depth_internal_and_alignment_trailing(self):
        flags = _load_flags()
        decode = flags.decode_video_prompt_type
        model_defs = {"current": {}}
        gr = types.SimpleNamespace(update=lambda **kwargs: kwargs)
        namespace = {
            "decode_video_prompt_type": decode,
            "set_video_prompt_type_alignment": flags.set_video_prompt_type_alignment,
            "update_video_prompt_type_flags": flags.update_video_prompt_type_flags,
            "video_guide_processes": "OPEDTSLCMU",
            "all_guide_processes": "OPEDTSLCMUVGBH",
            "process_map_video_guide": {
                "O": "pose_align", "P": "pose", "E": "canny",
                "D": "depth", "T": "depth_temporal", "S": "scribble",
                "L": "flow", "C": "gray", "M": "inpaint", "U": "identity",
            },
            "get_current_model_settings": lambda state: state["settings"],
            "get_state_model_type": lambda _state: "test",
            "get_model_settings": lambda state, _model_type: state["settings"],
            "get_model_def": lambda _model_type: model_defs["current"],
            "switch_image_guide_editor": lambda *args: ("mask-guide", "guide", "mask"),
            "injected_frames_positions_visible": lambda _flags: False,
            "input_video_strength_visible": lambda *_args: False,
            "time": types.SimpleNamespace(time=lambda: 1.0),
            "gr": gr,
        }
        handlers = _load_wgp_handlers(
            "refresh_video_prompt_type_alignment",
            "update_video_prompt_type",
            "switch_image_mode",
            "refresh_video_prompt_type_image_refs",
            "refresh_video_prompt_type_video_guide",
            "refresh_video_prompt_type_video_mask",
            "refresh_video_prompt_type_video_custom_dropbox",
            "refresh_video_prompt_type_video_custom_checkbox",
            namespace=namespace,
        )

        settings = {"video_prompt_type": "VG"}
        handlers["update_video_prompt_type"](
            {"settings": settings}, process_type=["depth_temporal"],
        )
        self.assertEqual(settings["video_prompt_type"], "TVG")
        self.assertTrue(decode(settings["video_prompt_type"]).uses_temporal_depth)
        self.assertFalse(decode(settings["video_prompt_type"]).timeline_aligned)

        settings["video_prompt_type"] = "PTVGT"
        handlers["update_video_prompt_type"](
            {"settings": settings}, any_video_guide=True, process_type=["raw"],
        )
        self.assertEqual(settings["video_prompt_type"], "VGT")
        self.assertTrue(decode(settings["video_prompt_type"]).timeline_aligned)
        self.assertFalse(decode(settings["video_prompt_type"]).uses_temporal_depth)

        align = handlers["refresh_video_prompt_type_alignment"]
        aligned = align(None, "PTVG", "T")
        self.assertEqual(aligned, "PTVGT")
        unaligned = align(None, aligned, "")
        self.assertEqual(unaligned, "PTVG")
        self.assertTrue(decode(unaligned).uses_temporal_depth)

        image_ref_result = handlers["refresh_video_prompt_type_image_refs"](
            None, "PVGT", "I", 0, "",
        )
        self.assertEqual(image_ref_result[0], "PVGIT")
        self.assertTrue(decode(image_ref_result[0]).timeline_aligned)
        self.assertFalse(decode(image_ref_result[0]).uses_temporal_depth)

        model_defs["current"] = {
            "inpaint_support": True,
            "inpaint_video_prompt_type": "VAG",
            "image_video_prompt_type": "KI",
        }
        image_mode_state = {
            "image_mode_tab": 1,
            "settings": {"video_prompt_type": "PTVGT"},
        }
        handlers["switch_image_mode"](image_mode_state)
        switched = image_mode_state["settings"]["video_prompt_type"]
        self.assertEqual(switched, "KIT")
        self.assertTrue(decode(switched).timeline_aligned)
        self.assertFalse(decode(switched).uses_temporal_depth)

        refresh_guide = handlers["refresh_video_prompt_type_video_guide"]
        guide_args = (None, "", "PTVGT", "V", 0, None, None, None, "")
        raw_guide_result = refresh_guide(*guide_args)
        self.assertEqual(raw_guide_result[0], "VT")
        self.assertTrue(decode(raw_guide_result[0]).timeline_aligned)
        self.assertFalse(decode(raw_guide_result[0]).uses_temporal_depth)
        depth_guide_result = refresh_guide(
            None, "", "PVGT", "TVG", 0, None, None, None, "",
        )
        self.assertEqual(depth_guide_result[0], "TVGT")
        self.assertTrue(decode(depth_guide_result[0]).timeline_aligned)
        self.assertTrue(decode(depth_guide_result[0]).uses_temporal_depth)

        mask_result = handlers["refresh_video_prompt_type_video_mask"](
            None, "PTVGT", "A", 0, None, None, None,
        )
        self.assertEqual(mask_result[0], "PTVGAT")
        self.assertTrue(decode(mask_result[0]).timeline_aligned)
        self.assertTrue(decode(mask_result[0]).uses_temporal_depth)

        model_defs["current"] = {
            "custom_video_selection": {
                "letters_filter": "X",
                "choices": [("Off", ""), ("On", "X")],
            },
        }
        custom_drop = handlers["refresh_video_prompt_type_video_custom_dropbox"](
            None, "PVGT", "X",
        )
        custom_check = handlers["refresh_video_prompt_type_video_custom_checkbox"](
            None, "PVGT", True,
        )
        for result in (custom_drop, custom_check):
            self.assertEqual(result, "PVGXT")
            self.assertTrue(decode(result).timeline_aligned)
            self.assertFalse(decode(result).uses_temporal_depth)

    def test_recam_legacy_settings_migration_preserves_alignment_marker(self):
        service = _load_service()
        fix_settings = _load_ast_function(
            _ROOT / "app" / "models" / "wan" / "wan_handler.py",
            "fix_settings",
            class_name="family_handler",
            namespace={
                "test_oneframe_overlap": lambda _base_model_type: False,
                "test_class_i2v": lambda _base_model_type: False,
                "test_svi2pro": lambda _base_model_type: False,
            },
        )
        ui_defaults = {"video_prompt_type": "PT"}
        fix_settings("recam_1.3B", 2.30, {}, ui_defaults)
        self.assertEqual(ui_defaults["video_prompt_type"], "PUVT")
        decoded = service.decode_video_prompt_type(ui_defaults["video_prompt_type"])
        self.assertTrue(decoded.timeline_aligned)
        self.assertFalse(decoded.uses_temporal_depth)

    def test_wanmove_control_start_uses_alignment_not_temporal_depth(self):
        decode = _load_flags().decode_video_prompt_type
        source = _ROOT / "app" / "models" / "wan" / "any2video.py"
        for value, expected in (
            ("PVGT", 0),
            ("PTVG", 23),
            ("PTVGT", 0),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    _evaluate_assignment_rhs(
                        source,
                        "control_video_pos",
                        {
                            "decode_video_prompt_type": decode,
                            "video_prompt_type": value,
                            "window_start_frame_no": 23,
                        },
                    ),
                    expected,
                )

    def test_h3_lora_affine_preflight_runs_before_managed_lora_download(self):
        launch = _LAUNCH_PATH.read_text(encoding="utf-8")
        affine = launch.index("ensure_minimax_h3_lora_affine_maps(")
        managed_lora = launch.index(
            "_ensure_managed_loras_present(", affine,
        )
        self.assertLess(affine, managed_lora)

    def test_official_inference_source_and_license_are_vendored(self):
        expected = (
            "LICENSE",
            "VENDOR.md",
            "utils/util.py",
            "video_depth_anything/video_depth.py",
            "video_depth_anything/dpt_temporal.py",
            "video_depth_anything/motion_module/attention.py",
            "video_depth_anything/motion_module/motion_module.py",
        )
        for relative in expected:
            with self.subTest(relative=relative):
                self.assertTrue((_VENDOR_ROOT / relative).is_file())

        notice = (_VENDOR_ROOT / "VENDOR.md").read_text(encoding="utf-8")
        self.assertIn(
            "4f5ae23172ba60fd7bc11ef671cca678842c7072", notice,
        )
        gitignore = _GITIGNORE_PATH.read_text(encoding="utf-8")
        self.assertNotIn(
            "app/preprocessing/video_depth_anything/", gitignore,
        )


if __name__ == "__main__":
    unittest.main()
