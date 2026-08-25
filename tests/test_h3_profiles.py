from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_profiles import (  # noqa: E402
    DEFAULT_H3_PROFILE_ID,
    NON_OWNER_DEFAULT_H3_PROFILE_ID,
    OWNER_DEFAULT_H3_PROFILE_ID,
    build_profile_options,
    default_profile_settings,
    fresh_profile_id_for_account_role,
    profile_definitions,
    profile_settings,
)

class H3ProfileTests(unittest.TestCase):
    def test_high_is_the_single_fresh_default_bundle(self):
        self.assertEqual(DEFAULT_H3_PROFILE_ID, "high")
        self.assertEqual(OWNER_DEFAULT_H3_PROFILE_ID, "high")
        self.assertEqual(NON_OWNER_DEFAULT_H3_PROFILE_ID, "quality")
        settings = default_profile_settings("minimax_h3_ref2va")
        self.assertEqual(
            settings,
            {
                "model_type": "minimax_h3_ref2va",
                "num_inference_steps": 28,
                "resolution": "1344x768",
                "custom_settings": {"h3_attention_engine": "sol_attn"},
                "tea_cache": 0,
                "activated_loras": [],
                "loras_multipliers": "",
                "lora_weights": {},
                "spatial_upsampling": "",
                "delivery_resolution": "",
                "delivery_fit": "",
            },
        )

    def test_fresh_default_is_high_for_owner_and_quality_for_non_owner(self):
        self.assertEqual(fresh_profile_id_for_account_role("owner"), "high")
        self.assertEqual(fresh_profile_id_for_account_role("user"), "quality")
        self.assertEqual(fresh_profile_id_for_account_role(None), "high")
        self.assertEqual(fresh_profile_id_for_account_role(""), "high")

    def test_profile_contract_matches_curated_settings(self):
        definitions = profile_definitions()
        self.assertEqual(
            [item["id"] for item in definitions],
            [
                "draft", "fast", "quality", "high", "spectrum_experimental",
                "lightx2v_experimental", "dasiwa_ref2va_experimental",
                "dasiwa_ref2va_suspected_experimental",
                "better_motion_ref2va_experimental", "1080p_delivery",
                "ultra", "4k_delivery",
            ],
        )
        profiles = {item["id"]: item for item in definitions}
        self.assertEqual(
            (profiles["draft"]["accelerator"], profiles["draft"]["num_inference_steps"], profiles["draft"]["resolution"], profiles["draft"]["attention_engine"]),
            ("turbo", 4, "608x352", "sage2"),
        )
        self.assertEqual(
            (profiles["fast"]["accelerator"], profiles["fast"]["num_inference_steps"], profiles["fast"]["resolution"], profiles["fast"]["attention_engine"]),
            ("turbo", 8, "864x480", "sage2"),
        )
        self.assertEqual(
            (profiles["quality"]["attention_engine"], profiles["quality"]["num_inference_steps"], profiles["quality"]["resolution"]),
            ("sol_attn", 23, "960x544"),
        )
        self.assertEqual(
            (profiles["high"]["attention_engine"], profiles["high"]["num_inference_steps"], profiles["high"]["resolution"]),
            ("sol_attn", 28, "1344x768"),
        )
        self.assertEqual(
            (
                profiles["spectrum_experimental"]["accelerator"],
                profiles["spectrum_experimental"]["attention_engine"],
                profiles["spectrum_experimental"]["num_inference_steps"],
                profiles["spectrum_experimental"]["resolution"],
            ),
            ("spectrum", "sol_attn", 28, "1344x768"),
        )
        self.assertIn("11 paired hidden-feature anchors", profiles["spectrum_experimental"]["description"])
        self.assertIn("quality and speed still require live validation", profiles["spectrum_experimental"]["description"])
        self.assertEqual(
            (profiles["ultra"]["attention_engine"], profiles["ultra"]["num_inference_steps"], profiles["ultra"]["resolution"]),
            ("sdpa", 32, "1344x768"),
        )
        self.assertEqual(
            (
                profiles["1080p_delivery"]["resolution"],
                profiles["1080p_delivery"]["spatial_upsampling"],
                profiles["1080p_delivery"]["delivery_resolution"],
                profiles["1080p_delivery"]["delivery_fit"],
            ),
            ("1344x768", "flashvsr1.5", "1920x1080", "center_crop"),
        )
        self.assertIn("1344x768 native", profiles["1080p_delivery"]["description"])
        self.assertIn("High", profiles["1080p_delivery"]["description"])
        self.assertEqual(
            (
                profiles["ultra"]["spatial_upsampling"],
                profiles["ultra"]["delivery_resolution"],
                profiles["ultra"]["delivery_fit"],
            ),
            ("flashvsr2pass2", "2688x1536", "upscale_exact"),
        )
        self.assertEqual(
            (
                profiles["4k_delivery"]["resolution"],
                profiles["4k_delivery"]["spatial_upsampling"],
                profiles["4k_delivery"]["delivery_resolution"],
                profiles["4k_delivery"]["delivery_fit"],
            ),
            ("1344x768", "flashvsr3", "3840x2160", "center_crop"),
        )
        self.assertIn("not native 4K", profiles["4k_delivery"]["description"])
        self.assertEqual(
            profiles["dasiwa_ref2va_experimental"]["lora_filename"],
            "dasiwa_ref2va_hybrid_v1_4step.safetensors",
        )
        self.assertEqual(
            profiles["better_motion_ref2va_experimental"]["lora_strength"],
            0.9,
        )

    def test_ref2va_lora_experiments_are_exact_explicit_and_never_fallbacks(self):
        options = build_profile_options(
            {
                "model_type": "minimax_h3_ref2va",
                "checkpoint_sha256": (
                    "71c61492faf65b410d0726840ac3b27b017fcfeb76b16ae11589223d81b7121c"
                ),
                "reference_shape": {},
            },
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            dasiwa_status={
                "available": True, "downloaded": True,
                "download_required": False,
            },
            dasiwa_suspected_status={
                "available": True, "downloaded": True,
                "download_required": False,
            },
            better_motion_status={
                "available": False, "downloaded": False,
                "download_required": True,
                "reason": "Civitai Early Access",
            },
        )
        by_id = {item["id"]: item for item in options}
        dasiwa = by_id["dasiwa_ref2va_experimental"]
        self.assertTrue(dasiwa["available"])
        self.assertFalse(dasiwa["download_required"])
        self.assertIsNone(dasiwa["fallback_profile_id"])
        self.assertEqual(
            dasiwa["settings"]["activated_loras"],
            ["dasiwa_ref2va_hybrid_v1_4step.safetensors"],
        )
        self.assertEqual(dasiwa["settings"]["loras_multipliers"], "1.0")
        self.assertEqual(
            dasiwa["settings"]["lora_weights"],
            {"dasiwa_ref2va_hybrid_v1_4step.safetensors": [1.0]},
        )
        self.assertNotIn("h3_turbo_profile", dasiwa["settings"]["custom_settings"])
        self.assertFalse(any(
            key.startswith("h3_experiment_")
            for key in dasiwa["settings"]["custom_settings"]
        ))
        motion = by_id["better_motion_ref2va_experimental"]
        self.assertFalse(motion["available"])
        self.assertTrue(motion["download_required"])
        self.assertEqual(
            motion["download_components"],
            ["Better Motion Ref2VA Experimental artifact"],
        )
        self.assertIsNone(motion["fallback_profile_id"])
        suspected = by_id["dasiwa_ref2va_suspected_experimental"]
        self.assertTrue(suspected["available"])
        self.assertIsNone(suspected["fallback_profile_id"])
        self.assertEqual(
            suspected["settings"]["lora_weights"],
            {"dasiwa_ref2va_hybrid_v1_4step.safetensors": [1.0]},
        )

        base = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            dasiwa_status={"available": True, "downloaded": True},
            dasiwa_suspected_status={"available": True, "downloaded": True},
            better_motion_status={"available": True, "downloaded": True},
        )
        for profile_id in (
            "dasiwa_ref2va_experimental",
            "dasiwa_ref2va_suspected_experimental",
            "better_motion_ref2va_experimental",
        ):
            candidate = next(item for item in base if item["id"] == profile_id)
            self.assertFalse(candidate["available"])
            self.assertIn("Ref2VA", candidate["fallback_reason"])
            self.assertIsNone(candidate["fallback_profile_id"])

    def test_turbo_profiles_are_visibly_unavailable_until_registered(self):
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: False,
        )
        by_id = {item["id"]: item for item in options}
        self.assertFalse(by_id["draft"]["available"])
        self.assertIn("not registered", by_id["draft"]["fallback_reason"])
        self.assertEqual(by_id["draft"]["fallback_profile_id"], "quality")
        self.assertEqual(by_id["fast"]["fallback_profile_id"], "quality")
        self.assertIsNone(by_id["quality"]["fallback_profile_id"])
        self.assertTrue(by_id["quality"]["available"])

    def test_bundle_is_non_locking_and_never_changes_policy_or_references(self):
        options = build_profile_options(
            {
                "model_type": "minimax_h3_ref2va",
                "explicit_output": False,
                "private": True,
                "h3_adaptive_conditioning": True,
                "reference_shape": {"image_count": 2},
            },
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={
                "registered": True, "downloaded": True,
            },
            turbo_compatibility=lambda _settings: (
                False, "Ref2VA Turbo requires its recorded visual gate."
            ),
        )
        for option in options:
            settings = option["settings"]
            self.assertFalse({
                "explicit_output", "private", "h3_adaptive_conditioning",
                "reference_shape", "image_refs", "image_start", "image_end",
            } & set(settings))
        self.assertFalse(options[0]["available"])
        self.assertIn("recorded visual gate", options[0]["fallback_reason"])
        self.assertEqual(options[2]["settings"]["model_type"], "minimax_h3_ref2va")

    def test_registered_turbo_is_selected_and_download_state_is_exposed(self):
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={
                "registered": True, "downloaded": False,
            },
            sage2_status={
                "available": True, "validated": True,
                "validated_profiles": ["draft"],
            },
            turbo_compatibility=lambda _settings: (True, None),
        )
        draft = options[0]
        self.assertTrue(draft["available"])
        self.assertTrue(draft["download_required"])
        self.assertEqual(draft["settings"]["model_type"], "minimax_h3")
        self.assertEqual(draft["settings"]["activated_loras"], [])
        self.assertEqual(draft["settings"]["lora_weights"], {})
        self.assertEqual(draft["settings"]["tea_cache"], 0)
        self.assertEqual(
            draft["settings"]["custom_settings"]["h3_turbo_profile"],
            "h3_turbo_v4",
        )
        self.assertEqual(
            draft["settings"]["custom_settings"]["h3_attention_engine"],
            "sage2",
        )

    def test_spectrum_is_explicit_non_default_and_only_available_for_all_base_plans(self):
        common = dict(
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
        )
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            **common,
        )
        spectrum = next(item for item in options if item["id"] == "spectrum_experimental")
        self.assertTrue(spectrum["available"])
        self.assertEqual(
            spectrum["settings"]["custom_settings"],
            {
                "h3_attention_engine": "sol_attn",
                "h3_spectrum_profile": "spectrum_h3_v1",
            },
        )
        self.assertEqual(spectrum["settings"]["activated_loras"], [])
        self.assertEqual(spectrum["settings"]["tea_cache"], 0)
        self.assertEqual(DEFAULT_H3_PROFILE_ID, "high")

        for context in (
            {"model_type": "minimax_h3_ref2va", "reference_shape": {}},
            {
                "model_type": "minimax_h3",
                "reference_shape": {},
                "_segment_contexts": [
                    {"model_type": "minimax_h3"},
                    {"model_type": "minimax_h3_ref2va"},
                ],
            },
            {
                "model_type": "minimax_h3",
                "reference_shape": {"image_count": 1},
            },
        ):
            with self.subTest(context=context):
                candidates = build_profile_options(context, **common)
                candidate = next(
                    item for item in candidates if item["id"] == "spectrum_experimental"
                )
                self.assertFalse(candidate["available"])
                self.assertTrue(any(
                    token in candidate["fallback_reason"]
                    for token in ("Base", "Ref2VA")
                ))

    def test_spectrum_profile_uses_runtime_compatibility_gate(self):
        seen = []
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            spectrum_compatibility=lambda settings: (
                seen.append(settings) or False,
                "runtime matrix rejected this configuration",
            ),
        )
        spectrum = next(item for item in options if item["id"] == "spectrum_experimental")
        self.assertFalse(spectrum["available"])
        self.assertIn("runtime matrix", spectrum["fallback_reason"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(
            seen[0]["custom_settings"]["h3_spectrum_profile"],
            "spectrum_h3_v1",
        )

    def test_fast_keeps_sage_settings_but_waits_for_its_exact_geometry_gate(self):
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={
                "available": True,
                "validated": True,
                "validated_profiles": ["draft"],
                "reason": "validated only at the recorded 608x352 envelope",
            },
        )
        self.assertTrue(options[0]["available"])
        self.assertFalse(options[1]["available"])
        self.assertIn("exact geometry gate is pending", options[1]["fallback_reason"])
        self.assertEqual(options[1]["settings"]["resolution"], "864x480")
        self.assertEqual(options[1]["settings"]["custom_settings"]["h3_attention_engine"], "sage2")
        released = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={
                "available": True,
                "validated": True,
                "validated_profiles": ["draft", "fast"],
            },
        )
        self.assertTrue(released[0]["available"])
        self.assertTrue(released[1]["available"])

    def test_base_sage_profiles_require_the_release_bound_runtime_gate(self):
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={
                "available": True,
                "validated": False,
                "validation_reason": "validation record hash mismatch",
            },
        )
        self.assertFalse(options[0]["available"])
        self.assertFalse(options[1]["available"])
        self.assertIn("hash mismatch", options[0]["fallback_reason"])

    def test_sage_profiles_never_apply_to_unvalidated_h3_checkpoints(self):
        for model_type in (
            "minimax_h3_w4a8_fl2va",
            "minimax_h3_pinkcherry_fl2va",
            "minimax_h3_ref2va",
        ):
            self.assertEqual(
                profile_settings(model_type, "draft")["custom_settings"]["h3_attention_engine"],
                "sdpa",
            )
            self.assertEqual(
                profile_settings(model_type, "fast")["custom_settings"]["h3_attention_engine"],
                "sdpa",
            )
        options = build_profile_options(
            {"model_type": "minimax_h3_ref2va", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={"available": True, "validated": True},
        )
        self.assertEqual(options[0]["attention_engine"], "sdpa")
        self.assertEqual(options[0]["settings"]["custom_settings"]["h3_attention_engine"], "sdpa")
        self.assertNotIn("Sage2", options[0]["description"])

    def test_sage_profiles_reject_adaptive_non_base_routing_contexts(self):
        common = dict(
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={
                "available": True,
                "validated": True,
                "validated_profiles": ["draft", "fast"],
            },
        )
        contexts = (
            ({"model_type": "minimax_h3", "reference_shape": {"image_count": 1}}, "Ref2VA"),
            ({
                "model_type": "minimax_h3",
                "reference_shape": {},
                "_segment_contexts": [
                    {"model_type": "minimax_h3"},
                    {"model_type": "minimax_h3_ref2va"},
                ],
            }, "every planned segment"),
        )
        for context, reason in contexts:
            with self.subTest(reason=reason):
                options = build_profile_options(context, **common)
                self.assertFalse(options[0]["available"])
                self.assertFalse(options[1]["available"])
                self.assertIn(reason, options[0]["fallback_reason"])

    def test_explicit_output_does_not_disable_base_draft_or_fast(self):
        options = build_profile_options(
            {
                "model_type": "minimax_h3",
                "reference_shape": {},
                "explicit_output": True,
            },
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={
                "available": True,
                "validated": True,
                "validated_profiles": ["draft", "fast"],
            },
        )
        self.assertTrue(options[0]["available"])
        self.assertTrue(options[1]["available"])
        self.assertEqual(options[0]["settings"]["model_type"], "minimax_h3")
        self.assertEqual(options[1]["settings"]["model_type"], "minimax_h3")

    def test_long_adaptive_base_does_not_invent_an_incompatible_segment(self):
        options = build_profile_options(
            {
                "model_type": "minimax_h3",
                "duration_seconds": 30,
                "reference_shape": {},
                "h3_adaptive_conditioning": True,
                "explicit_output": True,
            },
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (True, None),
            sage2_status={
                "available": True,
                "validated": True,
                "validated_profiles": ["draft", "fast"],
            },
        )
        self.assertTrue(options[0]["available"])
        self.assertTrue(options[1]["available"])
        self.assertIsNone(options[0]["fallback_reason"])
        self.assertIsNone(options[1]["fallback_reason"])

    def test_profiles_preserve_w4a8_selection_and_runtime_decides_turbo(self):
        seen = []
        options = build_profile_options(
            {"model_type": "minimax_h3_w4a8_fl2va", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda settings: (
                seen.append(settings["model_type"]) is None, None
            ),
        )
        self.assertTrue(options[0]["available"])
        self.assertEqual(seen, ["minimax_h3_w4a8_fl2va", "minimax_h3_w4a8_fl2va"])
        self.assertEqual(options[2]["settings"]["model_type"], "minimax_h3_w4a8_fl2va")

    def test_pinkcherry_rejects_turbo_and_falls_forward_to_native_quality(self):
        options = build_profile_options(
            {
                "model_type": "minimax_h3_pinkcherry_fl2va",
                "reference_shape": {},
            },
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            turbo_status={"registered": True, "downloaded": True},
            turbo_compatibility=lambda _settings: (
                False,
                "H3 Turbo is incompatible with PinkCherry",
            ),
        )
        self.assertFalse(options[0]["available"])
        self.assertFalse(options[1]["available"])
        self.assertEqual(options[0]["fallback_profile_id"], "quality")
        self.assertEqual(options[1]["fallback_profile_id"], "quality")
        self.assertTrue(options[2]["available"])
        self.assertNotIn(
            "h3_turbo_profile",
            options[2]["settings"]["custom_settings"],
        )
        self.assertEqual(
            options[2]["settings"]["model_type"],
            "minimax_h3_pinkcherry_fl2va",
        )

    def test_fresh_ui_hydration_uses_role_profile_without_touching_policy_fields(self):
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(
            encoding="utf-8"
        )
        hydration = store[
            store.index("function _applyModelDefaults("):
            store.index("// Family → generation mode mapping")
        ]
        self.assertIn("const isH3 = H3_STUDIO_MODELS.has(modelType)", hydration)
        self.assertIn("overrides.resolution = defaults.resolution", hydration)
        self.assertIn("overrides.custom_settings", hydration)
        self.assertIn("overrides.tea_cache = defaults.tea_cache ?? 0", hydration)
        self.assertIn("overrides.activated_loras = []", hydration)
        self.assertIn("overrides.delivery_resolution = undefined", hydration)
        self.assertIn("overrides.delivery_fit = undefined", hydration)
        self.assertIn("spatialUpsampling: ''", hydration)
        self.assertIn("defaults.h3_default_profile_id", hydration)
        self.assertIn("h3SelectedProfile: selectedProfile", hydration)
        self.assertIn("H3_PERFORMANCE_PROFILE_IDS.has", hydration)
        self.assertNotIn("overrides.model_type", hydration)
        self.assertNotIn("prompt:", hydration)
        self.assertNotIn("privateOutput", hydration)
        self.assertNotIn("h3_adaptive_conditioning", hydration)

    def test_preset_and_output_restore_cancel_fresh_hydration_as_custom(self):
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(
            encoding="utf-8"
        )
        preset_start = store.rindex("loadPreset: (preset)")
        preset = store[preset_start:store.index("deletePreset:", preset_start)]
        output = store[
            store.index("loadSettingsFromOutput: async"):
            store.index("// Restore image refs as File objects")
        ]
        self.assertIn("++_modelDefaultsSeq", preset)
        self.assertIn("h3SelectedProfile: 'custom'", preset)
        self.assertIn("delivery_resolution: typeof preset.params.delivery_resolution", preset)
        self.assertIn("delivery_fit: typeof preset.params.delivery_fit", preset)
        self.assertIn("spatialUpsampling: preset.spatial_upsampling || ''", preset)
        self.assertIn("++_modelDefaultsSeq", output)
        self.assertIn("h3SelectedProfile: 'custom'", output)

    def test_native_resolution_override_atomically_clears_delivery_chain(self):
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(
            encoding="utf-8"
        )
        action = store[
            store.index("setH3NativeResolution: (resolution) =>"):
            store.index("settingsOpen:", store.index("setH3NativeResolution: (resolution) =>"))
        ]
        self.assertIn("resolution,", action)
        self.assertIn("delivery_resolution: undefined", action)
        self.assertIn("delivery_fit: undefined", action)
        self.assertIn("spatialUpsampling: ''", action)
        self.assertIn("h3SelectedProfile: 'custom'", action)

    def test_manual_and_restored_custom_state_wins_async_default_races(self):
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(
            encoding="utf-8"
        )
        mode_switch = store[
            store.index("setGenerationMode: (mode)"):
            store.index("params: { ...defaultParams }")
        ]
        resolution = store[
            store.index("setResolutionPreset: (preset)"):
            store.index("durationSeconds: 5")
        ]
        loras = store[
            store.rindex("toggleLora: (filename)"):
            store.rindex("// Presets")
        ]
        options = store[
            store.rindex("loadModelOptions: async (modelType)"):
            store.index("// System config", store.rindex("loadModelOptions: async (modelType)"))
        ]
        self.assertIn("if (restoredSnapshot)", mode_switch)
        self.assertIn("++_modelDefaultsSeq", mode_switch)
        self.assertIn("_applyModelDefaults(get, set, newModelType)", mode_switch)
        self.assertGreaterEqual(resolution.count("++_modelDefaultsSeq"), 2)
        self.assertGreaterEqual(loras.count("++_modelDefaultsSeq"), 2)
        self.assertIn("const defaultsSeq = _modelDefaultsSeq", options)
        self.assertGreaterEqual(
            options.count("defaultsSeq === _modelDefaultsSeq"), 2
        )


if __name__ == "__main__":
    unittest.main()
