"""Focused model-free regressions for MiniMax H3 Ref2VA support."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import types
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_HANDLER = _APP / "models" / "minimax_h3" / "minimax_h3_handler.py"
_MAIN = _APP / "models" / "minimax_h3" / "minimax_h3_main.py"
_PACKING = _APP / "models" / "minimax_h3" / "packing.py"
_CONDITIONER = _APP / "models" / "minimax_h3" / "conditioner.py"
_DEFAULT = _APP / "defaults" / "minimax_h3_ref2va.json"
_WGP = _APP / "wgp.py"
_INPUTS_PANEL = _ROOT / "ui" / "src" / "components" / "Sidebar" / "InputsPanel.tsx"
_H3_PLAN_DIALOG = _ROOT / "ui" / "src" / "components" / "H3GenerationPlanDialog.tsx"
_STORE = _ROOT / "ui" / "src" / "stores" / "useStore.ts"
_TYPES = _ROOT / "ui" / "src" / "types" / "index.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_handler():
    tree = ast.parse(_read(_HANDLER), filename=str(_HANDLER))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name.startswith("_") for name in names):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in ("_hf_url", "_is_reference_mode"):
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "family_handler":
            selected.append(node)
    namespace = {"os": __import__("os"), "torch": types.SimpleNamespace(bfloat16="bfloat16")}
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_HANDLER), "exec"), namespace)
    return namespace["family_handler"]


class TestMiniMaxH3Ref2VADefinition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handler = _load_handler()

    def test_separate_ref2va_checkpoint_is_pinned(self):
        defaults = json.loads(_read(_DEFAULT))
        model = defaults["model"]
        self.assertEqual(model["architecture"], "minimax_h3_ref2va")
        self.assertIn("minimax_h3_ref2va_pruned_fp8_scaled.safetensors", model["URLs"][0])
        self.assertIn("eb8a16107c595128b3a578f82d2ce2f75920c355", model["URLs"][0])
        fl2va = json.loads(_read(_APP / "defaults" / "minimax_h3.json"))
        self.assertEqual(fl2va["model"]["architecture"], "minimax_h3")
        self.assertIn("minimax_h3_fl2va", fl2va["model"]["URLs"][0])

    def test_ref2va_contract_is_arbitrary_reference_not_keyframe_mode(self):
        model_def = self.handler.query_model_def("minimax_h3_ref2va", {})
        self.assertEqual(
            self.handler.query_supported_types(), ["minimax_h3", "minimax_h3_ref2va"]
        )
        self.assertTrue(model_def["minimax_h3_reference_mode"])
        self.assertFalse(model_def["sliding_window"])
        self.assertEqual((model_def["frames_minimum"], model_def["frames_maximum"]), (107, 345))
        self.assertEqual(model_def["image_prompt_types_allowed"], "T")
        self.assertFalse(model_def["end_frames_always_enabled"])
        self.assertEqual(model_def["reference_image_max_count"], 9)
        self.assertEqual(model_def["reference_video_max_count"], 3)
        self.assertEqual(model_def["reference_audio_max_count"], 3)
        self.assertEqual(model_def["mixed_reference_max_count"], 12)
        self.assertEqual(model_def["minimax_h3_conditioning_mode"], "semantic_references")
        self.assertTrue(model_def["minimax_h3_conditioning_modes_mutually_exclusive"])
        self.assertEqual(
            model_def["semantic_reference_limits"],
            {
                "image_count": 9,
                "video_count": 3,
                "audio_count": 3,
                "mixed_file_count": 12,
                "output_duration_seconds": {"min": 4, "max": 15},
                "reference_video_duration_seconds": {"min": 2, "max": 15, "total_max": 15},
                "reference_audio_duration_seconds": {"min": 2, "max": 15, "total_max": 15},
            },
        )

        fl2va = self.handler.query_model_def("minimax_h3", {})
        self.assertEqual(fl2va["minimax_h3_conditioning_mode"], "first_last_frames")
        self.assertTrue(fl2va["end_frames_always_enabled"])
        self.assertNotIn("semantic_reference_limits", fl2va)

    def test_reference_count_validation_enforces_official_limits(self):
        valid = {
            "image_refs": [object()] * 6,
            "video_prompt_type": "V+",
            "audio_prompt_type": "ABC",
        }
        self.assertIsNone(
            self.handler.validate_generative_settings("minimax_h3_ref2va", {}, valid)
        )
        too_many_images = dict(valid, image_refs=[object()] * 10)
        self.assertIn(
            "at most 9",
            self.handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, too_many_images
            ),
        )
        too_many_mixed = {
            "image_refs": [object()] * 9,
            "video_prompt_type": "V+",
            "audio_prompt_type": "AB",
        }
        self.assertIn(
            "at most 12",
            self.handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, too_many_mixed
            ),
        )

    def test_keyframe_and_semantic_reference_modes_are_mutually_exclusive(self):
        self.assertIn(
            "FL2VA checkpoint",
            self.handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, {"image_start": object()}
            ),
        )
        self.assertIn(
            "Ref2VA checkpoint",
            self.handler.validate_generative_settings(
                "minimax_h3", {}, {"image_refs": [object()]}
            ),
        )

    def test_runtime_and_conditioner_have_mixed_reference_paths(self):
        main = _read(_MAIN)
        packing = _read(_PACKING)
        conditioner = _read(_CONDITIONER)
        self.assertIn("build_ref2va_packed_sequence", main)
        self.assertIn("input_frames3=None", main)
        self.assertIn("audio_guide3=None", main)
        self.assertIn("first/last-frame conditioning requires the FL2VA checkpoint", main)
        self.assertIn("class MiniMaxH3PreparedReference", packing)
        self.assertIn("condition_audio_timestep", packing)
        self.assertIn("_presentation_entries", conditioner)
        self.assertIn('f"<Video {counters[kind]}>: "', conditioner)
        self.assertIn('f"<Audio {counters[kind]}>: "', conditioner)

    def test_upstream_ref2va_provenance_is_pinned(self):
        provenance = _read(_APP / "models" / "minimax_h3" / "UPSTREAM.md")
        self.assertIn("fa79896eadbcb048dc13e76233b3b72486b522a8", provenance)
        self.assertIn("eb8a16107c595128b3a578f82d2ce2f75920c355", provenance)

    def test_studio_bridge_preserves_three_video_and_audio_references(self):
        wgp = _read(_WGP)
        self.assertIn('"video_guide2", "video_guide3"', wgp)
        self.assertIn("video_guide2 = inputs.get(\"video_guide2\")", wgp)
        self.assertIn("video_guide3 = inputs.get(\"video_guide3\")", wgp)
        self.assertIn("prepare_semantic_reference_video", wgp)
        self.assertIn("input_frames3 = src_video3", wgp)
        self.assertIn('if "C" in audio_prompt_type:', wgp)

        inputs_panel = _read(_INPUTS_PANEL)
        store = _read(_STORE)
        types = _read(_TYPES)
        self.assertIn("H3_REF2VA_LIMITS = { images: 9, videos: 3, audio: 3, mixed: 12 }", inputs_panel)
        self.assertIn("Selected checkpoint:", inputs_panel)
        self.assertIn("Install correct Ref2VA checkpoint", inputs_panel)
        self.assertIn("maestro:minimax-h3-ref2va-terms-v1", store)
        self.assertIn("acceptHostTerm('minimax_h3_ref2va')", inputs_panel)
        self.assertIn("Accept for this host", inputs_panel)
        self.assertIn("HOST_TERM_NOTICES.minimax_h3_ref2va.text", inputs_panel)
        self.assertIn("Pinned FL2VA cannot use semantic references", store)
        self.assertIn("Pinned Ref2VA cannot use first/last-frame anchors", store)
        self.assertIn("'minimax_h3_ref2va'", store)
        library = _read(_ROOT / "ui" / "src" / "components" / "Sidebar" / "ProjectReferenceLibrary.tsx")
        self.assertIn("selectModel('minimax_h3_ref2va')", library)
        self.assertIn("H3 semantic ref (auto-select)", library)
        self.assertIn("addProjectAssetVariant(project, assetId", library)
        self.assertIn("source_workspace: project", library)
        self.assertIn('accept="image/*,video/*"', library)
        self.assertIn("video_guide3?: string", types)
        self.assertIn("audio_guide3?: string", types)

    def test_studio_adaptive_ui_preserves_hybrid_h3_inputs(self):
        inputs_panel = _read(_INPUTS_PANEL)
        store = _read(_STORE)
        plan_dialog = _read(_H3_PLAN_DIALOG)

        # General H3 Studio exposes semantic references and frame anchors at
        # the same time when adaptive routing is enabled.
        self.assertIn("const h3AdaptiveConditioning = params.h3_adaptive_conditioning !== false", inputs_panel)
        self.assertIn("h3StudioWorkflow && (h3AdaptiveConditioning || h3HasSemanticInputs)", inputs_panel)
        self.assertIn("const canAttachFrameAnchors = !dedicatedRef2VAMode || h3AdaptiveConditioning", inputs_panel)
        self.assertIn("const showFrameAnchorControls = canAttachFrameAnchors || h3HasFrameInputs", inputs_panel)
        self.assertIn("canAttachSemanticReferences && h3TermsAccepted", inputs_panel)

        # Switching between the managed H3 checkpoints must not erase either
        # side of the hybrid request before the planner can inspect it.
        options_slice = store.split("loadModelOptions: async (modelType) =>", 1)[1].split("// System config", 1)[0]
        self.assertIn("H3 model selection must not normalize away", options_slice)
        self.assertNotIn("image_refs: undefined", options_slice)
        self.assertNotIn("image_start: undefined", options_slice)
        self.assertNotIn("imageRefs: []", options_slice)
        self.assertIn("(state.imageRefType || h3SemanticReferenceSubmission)", store)
        self.assertIn("params.h3_adaptive_conditioning !== false", store)
        self.assertIn("p._h3_requested_checkpoint", store)
        self.assertIn("newParams.h3_adaptive_conditioning", store)
        self.assertIn("restoreSemanticH3Paths", store)
        self.assertIn("? [...(params.image_refs as string[])]", store)
        self.assertIn("state.imageRefs.length + (state.params.image_refs?.length ?? 0)", store)
        self.assertIn("restoreGeneration !== _settingsRestoreGeneration", store)
        self.assertIn("_h3Ref2VATermsHostAccepted", store)
        self.assertIn("ref2va.current_version === H3_REF2VA_LEGACY_TERM_VERSION", store)
        self.assertIn("_clearLegacyH3Ref2VATermsAcceptance", store)
        self.assertIn("acceptHostTerm('minimax_h3_ref2va')", plan_dialog)
        self.assertIn("HOST_TERM_NOTICES.minimax_h3_ref2va.text", plan_dialog)

        # Fixed checkpoint overrides fail before upload rather than silently
        # stripping inputs, while the plan discloses the actual model/transition.
        self.assertIn("!h3AdaptiveConditioning && !h3FixedRef2VA && h3HasSemanticReferences", store)
        self.assertIn("!h3AdaptiveConditioning && h3FixedRef2VA && h3HasFrameAnchors", store)
        self.assertIn("Checkpoint switch", plan_dialog)
        self.assertIn("semantic references are not applied on this segment", plan_dialog)

    def test_studio_final_end_frame_is_reserved_for_last_h3_segment(self):
        inputs_panel = _read(_INPUTS_PANEL)
        plan_dialog = _read(_H3_PLAN_DIALOG)
        self.assertIn("const nativeEndWindow = h3StudioWorkflow ? lastWindow : 0", inputs_panel)
        self.assertIn("window: nativeEndWindow", inputs_panel)
        self.assertIn("modelOptions?.frames_steps || 0", inputs_panel)
        self.assertIn("Reserved as the final frame of the final FL2VA segment", inputs_panel)
        self.assertIn("Final end frame reserved", plan_dialog)

    def test_three_named_reference_video_slots_are_valid(self):
        valid = {
            "video_prompt_type": "V++-",
            "audio_prompt_type": "",
            "video_guide": "one.mp4",
            "video_guide2": "two.mp4",
            "video_guide3": "three.mp4",
        }
        self.assertIsNone(
            self.handler.validate_generative_settings("minimax_h3_ref2va", {}, valid)
        )

    def test_long_ref2va_handoff_has_exact_latent_and_reload_safe_paths(self):
        main = _read(_MAIN)
        launch = _read(_ROOT / "app" / "launch.py")
        self.assertIn("self._ref2va_handoff_cache", main)
        self.assertIn('handoff_mode == "temporal_tail"', main)
        self.assertIn("override_last_video_latent", main)
        self.assertIn("override_last_audio_latent", main)
        self.assertIn("normalized_video_latents", main)
        self.assertIn("_create_h3_ref2va_tail_video", launch)
        self.assertIn('"semantic_still"', launch)
        self.assertIn('boundary_type in {"continuous", "precut"}', launch)


def _gpu_runtime_available():
    if not all(
        importlib.util.find_spec(name) is not None for name in ("torch", "diffusers")
    ):
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


_RUNTIME_AVAILABLE = _gpu_runtime_available()


@unittest.skipUnless(_RUNTIME_AVAILABLE, "MiniMax H3 CUDA runtime is not available")
class TestMiniMaxH3Ref2VAPacking(unittest.TestCase):
    def test_reference_rows_precede_target_rows_and_keep_fixed_timesteps(self):
        import sys

        sys.path.insert(0, str(_APP))
        try:
            import torch
            from models.minimax_h3.packing import (
                MiniMaxH3PreparedReference,
                build_ref2va_packed_sequence,
                build_row_timesteps,
            )

            references = [
                MiniMaxH3PreparedReference("image", latent_height=4, latent_width=6),
                MiniMaxH3PreparedReference(
                    "video",
                    num_latent_frames=2,
                    latent_height=4,
                    latent_width=6,
                    num_audio_latents=3,
                ),
                MiniMaxH3PreparedReference("audio", num_audio_latents=2),
            ]
            layout = build_ref2va_packed_sequence(
                torch.ones(2, dtype=torch.long), references, 3, 4, 6, 4, (1, 2, 2)
            )
            self.assertEqual(layout.num_condition_video_rows, 18)
            self.assertEqual(layout.num_condition_audio_rows, 10)
            self.assertEqual(layout.video_indices.numel(), 36)
            self.assertEqual(layout.audio_indices.numel(), 18)
            timesteps, indices = build_row_timesteps(layout, 0.2, 0.4, 0.999, 1.0)
            assigned = timesteps[indices]
            self.assertTrue(
                torch.allclose(
                    assigned[layout.video_indices[:18]], torch.full((18,), 0.999)
                )
            )
            self.assertTrue(
                torch.equal(assigned[layout.audio_indices[:10]], torch.ones(10))
            )
        finally:
            if sys.path and sys.path[0] == str(_APP):
                sys.path.pop(0)

    def test_reference_media_duration_limits_are_runtime_enforced(self):
        import sys

        sys.path.insert(0, str(_APP))
        try:
            import torch
            from models.minimax_h3.minimax_h3_main import MiniMaxH3Model

            model = object.__new__(MiniMaxH3Model)
            model.reference_mode = True

            with self.assertRaisesRegex(ValueError, "reference video 1 must be 2-15 seconds"):
                model.generate(
                    "test",
                    input_frames=torch.zeros(3, 22, 32, 32),
                    video_prompt_type="V",
                )

            with self.assertRaisesRegex(ValueError, "reference audio 1 must be 2-15 seconds"):
                model.generate(
                    "test",
                    input_ref_images=[torch.zeros(3, 32, 32)],
                    input_waveform=torch.zeros(32000),
                    audio_prompt_type="A",
                )
        finally:
            if sys.path and sys.path[0] == str(_APP):
                sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
