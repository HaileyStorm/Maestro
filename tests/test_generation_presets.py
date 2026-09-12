"""Focused persistence and isolation tests for saved generation presets."""

from __future__ import annotations

import ast
import asyncio
import json
import multiprocessing
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import generation_presets as presets


ACCOUNT_A = "account-scope-a"
ACCOUNT_B = "account-scope-b"
PROJECT_A = "project-instance-scope-a"
PROJECT_B = "project-instance-scope-b"
SCOPE_KEY = b"generation-preset-test-scope-key-32-bytes-minimum"


def preset_payload(
    *,
    name: str = "Klein quality",
    steps: int = 28,
    model_type: str = "flux_2_klein_9b",
) -> dict:
    return {
        "name": name,
        "mode": "image",
        "model_type": model_type,
        "activated_loras": ["detail.safetensors", "lighting.safetensors"],
        "loras_multipliers": "0.80;0.80 1.15;0.90",
        "lora_weights": {
            "detail.safetensors": [0.8, 0.8],
            "lighting.safetensors": [1.15, 0.9],
        },
        "spatial_upsampling": "",
        "params": {
            "num_inference_steps": steps,
            "guidance_scale": 3.5,
            "resolution": "1024x1024",
            "seed": 42,
            "flow_shift": 2,
            "self_refiner_setting": 0,
            "stage2_steps": 4,
            "tea_cache": 0,
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_sol_dense_steps": 10,
            },
        },
    }


def _profile_value(descriptor: dict) -> object:
    if "enum" in descriptor:
        return descriptor["enum"][0]
    kind = descriptor["type"]
    if kind == "string":
        return ""
    if kind == "number":
        return descriptor.get("minimum", 0.0)
    if kind == "integer":
        return descriptor.get("minimum", 0)
    if kind == "boolean":
        return False
    if kind in {"string_array", "number_array"}:
        return []
    if kind == "object":
        properties = descriptor.get("properties")
        if properties:
            return {
                key: _profile_value(field_descriptor)
                for key, field_descriptor in properties.items()
            }
        return {}
    raise AssertionError(f"unsupported test descriptor {kind}")


def v2_preset_payload() -> dict:
    fields = presets.GENERATION_PROFILE_FIELDS
    params = {
        key: _profile_value(descriptor)
        for key, descriptor in fields["params"].items()
    }
    params.update({
        "resolution": "1280x720",
        "settings_version": 2.52,
        "h3_adaptive_conditioning": False,
        "delivery_resolution": None,
        "custom_settings": {
            key: _profile_value(descriptor)
            for key, descriptor in fields["custom_settings"].items()
        },
    })
    ui_settings = {
        key: _profile_value(descriptor)
        for key, descriptor in fields["ui"].items()
    }
    ui_settings.update({
        "durationSeconds": 12.5,
        "slidingWindowSeconds": 8.0,
        "voiceCloneEnabled": False,
        "imageRefType": "",
        "outputCount": 0,
        "h3StyleWorkflow": "",
    })
    return {
        **preset_payload(name="Complete profile", model_type="minimax_h3"),
        "profile_version": 2,
        "params": params,
        "ui_settings": ui_settings,
    }


def _concurrent_create(
    runtime_root: str,
    index: int,
    gate: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    store = presets.GenerationPresetStore(runtime_root, scope_key=SCOPE_KEY)
    gate.wait()
    try:
        record = store.create(
            account_scope=ACCOUNT_A,
            project_scope=PROJECT_A,
            preset=preset_payload(name=f"Concurrent {index}", steps=23 + index),
            preset_id=f"concurrent-{index}",
        )
        results.put(("ok", record["id"]))
    except Exception as error:  # pragma: no cover - parent reports details
        results.put(("error", type(error).__name__))


def _same_id_create(
    runtime_root: str,
    name: str,
    preset_id: str,
    gate: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    store = presets.GenerationPresetStore(runtime_root, scope_key=SCOPE_KEY)
    gate.wait()
    try:
        record = store.create(
            account_scope=ACCOUNT_A,
            project_scope=PROJECT_A,
            preset=preset_payload(name=name),
            preset_id=preset_id,
        )
        results.put(("ok", record["name"], record["created_at"]))
    except Exception as error:  # pragma: no cover - parent reports details
        results.put(("error", type(error).__name__))


def _concurrent_update(
    runtime_root: str,
    name: str,
    expected_revision: str,
    gate: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    store = presets.GenerationPresetStore(runtime_root, scope_key=SCOPE_KEY)
    gate.wait()
    try:
        record = store.update(
            account_scope=ACCOUNT_A,
            project_scope=PROJECT_A,
            preset_id="race-update",
            preset=preset_payload(name=name),
            expected_revision=expected_revision,
        )
        results.put(("ok", record["name"], record["revision"]))
    except Exception as error:  # pragma: no cover - parent reports details
        results.put(("error", type(error).__name__))


class GenerationPresetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime_root = Path(self.temporary.name) / "storage"
        self.runtime_root.mkdir()
        self.store = presets.GenerationPresetStore(
            self.runtime_root,
            scope_key=SCOPE_KEY,
            clock=lambda: 1_787_000_000.25,
        )

    def create(
        self,
        *,
        payload: dict | None = None,
        preset_id: str | None = "preset-a",
        account_scope: str = ACCOUNT_A,
        project_scope: str = PROJECT_A,
    ) -> dict:
        return self.store.create(
            account_scope=account_scope,
            project_scope=project_scope,
            preset=preset_payload() if payload is None else payload,
            preset_id=preset_id,
        )

    def update(
        self,
        *,
        payload: dict,
        preset_id: str = "preset-a",
        expected_revision: str,
        account_scope: str = ACCOUNT_A,
        project_scope: str = PROJECT_A,
    ) -> dict:
        return self.store.update(
            account_scope=account_scope,
            project_scope=project_scope,
            preset_id=preset_id,
            preset=payload,
            expected_revision=expected_revision,
        )

    def listing(
        self,
        *,
        account_scope: str = ACCOUNT_A,
        project_scope: str = PROJECT_A,
    ) -> list[dict]:
        return self.store.list(
            account_scope=account_scope,
            project_scope=project_scope,
        )

    def test_create_list_delete_and_restart_reopen(self) -> None:
        created = self.create()
        self.assertEqual(created["id"], "preset-a")
        self.assertEqual(created["created_at"], 1_787_000_000.25)
        self.assertEqual(created["params"]["num_inference_steps"], 28)
        self.assertEqual(self.listing(), [created])

        restarted = presets.GenerationPresetStore(
            self.runtime_root, scope_key=SCOPE_KEY,
        )
        self.assertEqual(
            restarted.list(account_scope=ACCOUNT_A, project_scope=PROJECT_A),
            [created],
        )
        self.assertTrue(
            restarted.delete(
                account_scope=ACCOUNT_A,
                project_scope=PROJECT_A,
                preset_id="preset-a",
            )
        )
        self.assertEqual(
            restarted.list(account_scope=ACCOUNT_A, project_scope=PROJECT_A),
            [],
        )
        self.assertFalse(
            restarted.delete(
                account_scope=ACCOUNT_A,
                project_scope=PROJECT_A,
                preset_id="preset-a",
            )
        )

    def test_account_and_project_scope_mismatches_are_invisible(self) -> None:
        first = self.create()
        for account_scope, project_scope in (
            (ACCOUNT_B, PROJECT_A),
            (ACCOUNT_A, PROJECT_B),
            (ACCOUNT_B, PROJECT_B),
        ):
            self.assertEqual(
                self.listing(
                    account_scope=account_scope,
                    project_scope=project_scope,
                ),
                [],
            )
            self.assertFalse(
                self.store.delete(
                    account_scope=account_scope,
                    project_scope=project_scope,
                    preset_id=first["id"],
                )
            )

        second = self.create(
            account_scope=ACCOUNT_B,
            project_scope=PROJECT_A,
            payload=preset_payload(name="Other account", steps=32),
        )
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.listing()), 1)
        self.assertEqual(
            self.listing(account_scope=ACCOUNT_B, project_scope=PROJECT_A),
            [second],
        )

    def test_exact_parameter_round_trip_preserves_requested_step_counts(self) -> None:
        for index, requested_steps in enumerate((23, 28, 32), start=1):
            payload = preset_payload(
                name=f"Exact {requested_steps}",
                steps=requested_steps,
            )
            created = self.create(
                payload=payload,
                preset_id=f"exact-{index}",
            )
            self.assertEqual(created["params"], payload["params"])
            self.assertEqual(
                created["params"]["num_inference_steps"],
                requested_steps,
            )
        reopened = presets.GenerationPresetStore(
            self.runtime_root, scope_key=SCOPE_KEY,
        )
        self.assertEqual(
            [record["params"]["num_inference_steps"] for record in reopened.list(
                account_scope=ACCOUNT_A,
                project_scope=PROJECT_A,
            )],
            [23, 28, 32],
        )

    def test_v2_complete_profile_round_trips_every_classified_field_exactly(self) -> None:
        payload = v2_preset_payload()
        created = self.create(payload=payload, preset_id="complete-v2")
        self.assertEqual(created["profile_version"], 2)
        self.assertEqual(created["params"], payload["params"])
        self.assertEqual(created["ui_settings"], payload["ui_settings"])
        self.assertNotIn("model_type", created["params"])
        self.assertNotIn("activated_loras", created["params"])
        self.assertNotIn("loras_multipliers", created["params"])
        self.assertIsNone(created["params"]["delivery_resolution"])
        self.assertIs(created["params"]["h3_adaptive_conditioning"], False)
        self.assertEqual(created["activated_loras"], payload["activated_loras"])
        self.assertEqual(created["spatial_upsampling"], "")
        self.assertEqual(created["ui_settings"]["outputCount"], 0)

        reopened = presets.GenerationPresetStore(
            self.runtime_root, scope_key=SCOPE_KEY,
        )
        self.assertEqual(
            reopened.list(account_scope=ACCOUNT_A, project_scope=PROJECT_A),
            [created],
        )

    def test_update_replaces_every_v2_setting_and_preserves_stable_identity(self) -> None:
        original = self.create(
            payload=v2_preset_payload(),
            preset_id="complete-v2",
        )
        second = self.create(
            payload=preset_payload(name="Keep order"),
            preset_id="second",
        )
        desired = v2_preset_payload()
        desired.update({
            "name": "Updated complete profile",
            "mode": "video",
            "activated_loras": [],
            "loras_multipliers": "",
            "lora_weights": {},
            "spatial_upsampling": "flashvsr1.5",
        })
        desired["params"].update({
            "num_inference_steps": 48,
            "guidance_scale": 7.25,
            "seed": 0,
            "delivery_resolution": "1920x1080",
            "h3_adaptive_conditioning": True,
        })
        desired["params"]["custom_settings"].update({
            "h3_attention_engine": "sdpa",
            "h3_sol_dense_steps": 0,
        })
        desired["ui_settings"].update({
            "durationSeconds": 33.25,
            "slidingWindowSeconds": 4.0,
            "voiceCloneEnabled": True,
            "imageRefType": "KI",
            "outputCount": 4,
        })

        updated = self.update(
            payload=desired,
            preset_id=original["id"],
            expected_revision=original["revision"],
        )

        self.assertEqual(updated["id"], original["id"])
        self.assertEqual(updated["created_at"], original["created_at"])
        self.assertNotEqual(updated["revision"], original["revision"])
        self.assertEqual(
            {key: updated[key] for key in presets._preset_keys(updated)},
            desired,
        )
        listed = self.listing()
        self.assertEqual(
            [record["id"] for record in listed],
            [second["id"], updated["id"]],
        )
        self.assertEqual(listed[-1], updated)
        self.assertEqual(len(listed), 2)

    def test_public_revision_is_derived_from_the_complete_stored_record(self) -> None:
        legacy = self.create(preset_id="legacy-revision")
        modern = self.create(
            payload=v2_preset_payload(),
            preset_id="modern-revision",
        )
        envelope = json.loads(self.store.path.read_text(encoding="utf-8"))
        records = {
            record["id"]: record
            for values in envelope["state"]["scopes"].values()
            for record in values
        }
        self.assertNotIn("revision", records[legacy["id"]])
        self.assertNotIn("revision", records[modern["id"]])
        self.assertEqual(
            legacy["revision"],
            presets.hashlib.sha256(
                presets._canonical(records[legacy["id"]]),
            ).hexdigest(),
        )
        self.assertEqual(
            modern["revision"],
            presets.hashlib.sha256(
                presets._canonical(records[modern["id"]]),
            ).hexdigest(),
        )

    def test_update_conflicts_for_wrong_scope_missing_record_and_stale_revision(self) -> None:
        original = self.create()
        desired = preset_payload(name="Updated", steps=32)
        for account_scope, project_scope, preset_id in (
            (ACCOUNT_B, PROJECT_A, original["id"]),
            (ACCOUNT_A, PROJECT_B, original["id"]),
            (ACCOUNT_A, PROJECT_A, "missing-preset"),
        ):
            with self.subTest(
                account_scope=account_scope,
                project_scope=project_scope,
                preset_id=preset_id,
            ), self.assertRaisesRegex(
                presets.GenerationPresetConflict,
                "preset changed or is unavailable",
            ):
                self.update(
                    payload=desired,
                    preset_id=preset_id,
                    expected_revision=original["revision"],
                    account_scope=account_scope,
                    project_scope=project_scope,
                )

        updated = self.update(
            payload=desired,
            expected_revision=original["revision"],
        )
        with self.assertRaisesRegex(
            presets.GenerationPresetConflict,
            "preset changed or is unavailable",
        ):
            self.update(
                payload=preset_payload(name="Later change", steps=36),
                expected_revision=original["revision"],
            )
        self.assertEqual(self.listing(), [updated])
        for invalid_revision in (None, "", "A" * 64, "0" * 63, True):
            with self.subTest(revision=invalid_revision), self.assertRaisesRegex(
                presets.GenerationPresetError,
                "expected preset revision is invalid",
            ):
                self.update(
                    payload=desired,
                    expected_revision=invalid_revision,
                )

    def test_identical_update_retry_does_not_write_or_duplicate(self) -> None:
        original = self.create()
        desired = preset_payload(name="Updated once", steps=32)
        updated = self.update(
            payload=desired,
            expected_revision=original["revision"],
        )
        before = self.store.path.read_bytes()
        replay = self.update(
            payload=desired,
            expected_revision=original["revision"],
        )
        self.assertEqual(replay, updated)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.listing(), [updated])

    def test_v2_manifest_classifies_generate_params_and_known_exclusions(self) -> None:
        fields = presets.GENERATION_PROFILE_FIELDS
        self.assertEqual(set(fields), {
            "version", "params", "excluded_params", "ui", "custom_settings",
        })
        self.assertEqual(fields["version"], 2)
        types_source = (ROOT / "ui" / "src" / "types" / "index.ts").read_text(
            encoding="utf-8",
        )
        generate = types_source.split(
            "export interface GenerateParams extends H3AdaptiveSelection {", 1,
        )[1].split("\n}", 1)[0]
        adaptive = types_source.split(
            "export interface H3AdaptiveSelection {", 1,
        )[1].split("\n}", 1)[0]
        declared = set(re.findall(
            r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\??:",
            generate + "\n" + adaptive,
            re.MULTILINE,
        ))
        classified = set(fields["params"]) | {
            key for key in fields["excluded_params"] if "." not in key
        }
        self.assertFalse(declared - classified)
        self.assertEqual(fields["excluded_params"]["prompt"], "content")
        self.assertEqual(fields["excluded_params"]["image_start"], "media")
        self.assertEqual(fields["excluded_params"]["model_type"], "envelope")
        self.assertEqual(
            fields["excluded_params"]["activated_loras"], "envelope",
        )
        self.assertEqual(
            fields["excluded_params"]["loras_multipliers"], "envelope",
        )
        self.assertEqual(
            fields["excluded_params"]["spatial_upsampling"], "envelope",
        )
        self.assertNotIn("spatial_upsampling", fields["params"])
        self.assertNotIn("spatialUpsampling", fields["ui"])
        self.assertEqual(
            fields["excluded_params"]["custom_settings.image_ref_keyword_content"],
            "content",
        )
        for key in (
            "durationSeconds", "resolutionPreset", "aspectRatio",
            "h3StyleWorkflow", "outpaintVideoBox", "blendAnchorStrength",
        ):
            self.assertIn(key, fields["ui"])
        for key in (
            "sample_solver", "embedded_guidance_scale", "audio_guidance_scale",
            "top_p", "top_k", "alt_guidance_scale", "modality_scale",
            "sfx_text_weight",
        ):
            self.assertIn(key, fields["params"])

    def test_v2_rejects_unknown_content_media_authority_and_incomplete_ui(self) -> None:
        cases = []
        for key, value in (
            ("prompt", "private creative text"),
            ("image_start", "/private/reference.png"),
            ("h3_ref2va_terms_accepted", True),
            ("unknown_technical_knob", 1),
        ):
            payload = v2_preset_payload()
            payload["params"][key] = value
            cases.append(payload)
        creative_custom = v2_preset_payload()
        creative_custom["params"]["custom_settings"][
            "image_ref_keyword_content"
        ] = "private subject"
        cases.append(creative_custom)
        unknown_custom = v2_preset_payload()
        unknown_custom["params"]["custom_settings"]["unknown"] = 1
        cases.append(unknown_custom)
        unknown_ui = v2_preset_payload()
        unknown_ui["ui_settings"]["accountId"] = "private"
        cases.append(unknown_ui)
        missing_ui = v2_preset_payload()
        del missing_ui["ui_settings"]["durationSeconds"]
        cases.append(missing_ui)

        for index, payload in enumerate(cases):
            with self.subTest(index=index), self.assertRaisesRegex(
                presets.GenerationPresetError,
                "unsupported or private|incomplete",
            ):
                self.create(payload=payload, preset_id=f"rejected-v2-{index}")
        self.assertEqual(self.listing(), [])

    def test_v2_requires_runtime_core_and_rejects_invalid_descriptors(self) -> None:
        missing = v2_preset_payload()
        del missing["params"]["video_length"]
        missing_metadata = v2_preset_payload()
        del missing_metadata["params"]["settings_version"]

        cases = [
            (missing, "missing required"),
            (missing_metadata, "missing required"),
        ]
        for location, key, value in (
            ("params", "video_length", 0),
            ("params", "num_inference_steps", 0),
            ("params", "guidance_scale", 101),
            ("params", "settings_version", None),
            ("params", "resolution", "99999x720"),
            ("params", "ltx25_video_vae", "experimental"),
            ("params", "top_p", 1.1),
            ("params", "sample_solver", "a prose solver"),
            ("params", "h3_adaptive_fl2va_model", "/tmp/checkpoint"),
            ("params", "h3_fl2va_loras", [""]),
            ("params", "h3_fl2va_loras_multipliers", "strong weight"),
            ("params", "h3_ref2va_loras_multipliers", "11.0"),
            ("params", "h3_style_workflow", "cinematic workflow"),
            ("ui_settings", "voiceCloneMode", "three"),
            ("ui_settings", "filmGrainIntensity", 1.01),
            ("ui_settings", "resolutionPreset", "4k"),
            ("ui_settings", "h3StyleWorkflow", "/tmp/workflow"),
            ("ui_settings", "outpaintVideoBox", {
                "x": 0, "y": 0, "w": 0, "h": 1,
            }),
        ):
            payload = v2_preset_payload()
            payload[location][key] = value
            cases.append((
                payload,
                "invalid|outside|supported|bounded|requires|exceeds|cannot",
            ))
        for model_type in ("a prose model name", "/tmp/model"):
            payload = v2_preset_payload()
            payload["model_type"] = model_type
            cases.append((payload, "model type is invalid"))
        for duplicate in ("params", "ui_settings"):
            payload = v2_preset_payload()
            key = "spatial_upsampling" if duplicate == "params" else "spatialUpsampling"
            payload[duplicate][key] = "flashvsr2"
            cases.append((payload, "unsupported or private|incomplete"))

        for index, (payload, error) in enumerate(cases):
            with self.subTest(index=index), self.assertRaisesRegex(
                presets.GenerationPresetError, error,
            ):
                self.create(payload=payload, preset_id=f"invalid-v2-{index}")
        self.assertEqual(self.listing(), [])

    def test_v2_accepts_real_selector_codes_and_signed_lora_weights(self) -> None:
        payload = v2_preset_payload()
        payload["params"].update({
            "video_prompt_type": "V#1#",
            "force_fps": "control",
            "ltx25_video_vae": "nad",
            "sample_solver": "unipc",
            "top_p": 0.9,
            "top_k": 50,
            "alt_guidance_scale": 2.5,
            "h3_fl2va_loras": ["nested/custom motion.safetensors"],
            "h3_fl2va_loras_multipliers": "-0.75;1.00",
        })
        payload["params"]["custom_settings"].update({
            "keyscale": "F# minor",
            "timesignature": 6,
        })
        payload["ui_settings"].update({
            "voiceCloneMode": "two",
            "imageRefType": "KI",
            "audioSubMode": "music",
            "editSubMode": "recast",
            "resolutionPreset": "768p",
            "aspectRatio": "auto",
            "outpaintVideoBox": {"x": -0.25, "y": 0, "w": 1.25, "h": 1},
        })
        payload["lora_weights"] = {
            "detail.safetensors": [-0.8, 0.8],
            "lighting.safetensors": [1.15, -0.9],
        }
        payload["loras_multipliers"] = "-0.80;0.80 1.15;-0.90"

        created = self.create(payload=payload, preset_id="actual-controls")
        self.assertEqual(created["params"]["video_prompt_type"], "V#1#")
        self.assertEqual(created["params"]["custom_settings"]["keyscale"], "F# minor")
        self.assertEqual(created["ui_settings"]["resolutionPreset"], "768p")
        self.assertEqual(created["lora_weights"], payload["lora_weights"])

    def test_v2_accepts_exact_runtime_core_with_optional_params_absent(self) -> None:
        payload = v2_preset_payload()
        payload["params"] = {
            key: payload["params"][key]
            for key in (
                "resolution", "video_length", "num_inference_steps",
                "guidance_scale", "seed", "image_mode", "repeat_generation",
                "settings_version",
            )
        }
        created = self.create(payload=payload, preset_id="runtime-core")
        self.assertEqual(created["params"], payload["params"])
        self.assertNotIn("delivery_resolution", created["params"])

    def test_legacy_and_v2_records_coexist_without_shape_migration(self) -> None:
        legacy_payload = preset_payload(name="Legacy")
        legacy = self.create(payload=legacy_payload, preset_id="legacy")
        modern_payload = v2_preset_payload()
        modern = self.create(payload=modern_payload, preset_id="modern")
        self.assertNotIn("profile_version", legacy)
        self.assertEqual(modern["profile_version"], 2)
        self.assertEqual(self.listing(), [legacy, modern])

    def test_h3_delivery_chain_round_trips_without_stale_inheritance(self) -> None:
        payload = preset_payload(name="1080p delivery", steps=32)
        payload["spatial_upsampling"] = "flashvsr1.5"
        payload["params"]["delivery_resolution"] = "1920x1080"
        payload["params"]["delivery_fit"] = "center_crop"
        created = self.create(payload=payload, preset_id="delivery-chain")
        self.assertEqual(created["spatial_upsampling"], "flashvsr1.5")
        self.assertEqual(created["params"]["delivery_resolution"], "1920x1080")
        self.assertEqual(created["params"]["delivery_fit"], "center_crop")

    def test_caller_id_replay_is_idempotent_and_altered_replay_conflicts(self) -> None:
        first = self.create()
        before = self.store.path.read_bytes()
        replay = self.create()
        self.assertEqual(replay, first)
        self.assertEqual(self.store.path.read_bytes(), before)

        altered = preset_payload(steps=32)
        with self.assertRaises(presets.GenerationPresetConflict):
            self.create(payload=altered)
        self.assertEqual(self.store.path.read_bytes(), before)

        with self.assertRaisesRegex(
            presets.GenerationPresetError, "caller-stable preset_id",
        ):
            self.create(
                payload=preset_payload(name="Missing id", steps=23),
                preset_id=None,
            )

    def test_corrupt_malformed_and_unsafe_state_fail_closed(self) -> None:
        self.create()
        self.store.path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(presets.GenerationPresetIntegrityError):
            self.listing()

        self.store.path.unlink()
        self.create()
        envelope = json.loads(self.store.path.read_text(encoding="utf-8"))
        envelope["state"]["next_sequence"] = 999
        self.store.path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(presets.GenerationPresetIntegrityError):
            self.listing()

        self.store.path.unlink()
        external = self.runtime_root / "external.json"
        external.write_text("{}", encoding="utf-8")
        try:
            self.store.path.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(presets.GenerationPresetIntegrityError):
            self.listing()

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-specific")
    def test_atomic_publication_fsyncs_file_then_replaces_then_fsyncs_directory(self) -> None:
        events: list[str] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def tracked_fsync(descriptor: int) -> None:
            events.append(
                "directory-fsync"
                if stat.S_ISDIR(os.fstat(descriptor).st_mode)
                else "file-fsync"
            )
            real_fsync(descriptor)

        def tracked_replace(source: str, destination: str) -> None:
            events.append("replace")
            real_replace(source, destination)

        with (
            mock.patch.object(presets.os, "fsync", side_effect=tracked_fsync),
            mock.patch.object(presets.os, "replace", side_effect=tracked_replace),
        ):
            self.create()
        self.assertLess(events.index("file-fsync"), events.index("replace"))
        self.assertLess(events.index("replace"), events.index("directory-fsync"))

    def test_cross_process_concurrent_creates_serialize_without_lost_updates(self) -> None:
        context = multiprocessing.get_context(
            "spawn" if os.name == "nt" else "fork",
        )
        gate = context.Event()
        results = context.Queue()
        workers = [
            context.Process(
                target=_concurrent_create,
                args=(str(self.runtime_root), index, gate, results),
            )
            for index in range(6)
        ]
        for worker in workers:
            worker.start()
        gate.set()
        reports = [results.get(timeout=20) for _worker in workers]
        for worker in workers:
            worker.join(timeout=20)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual({report[0] for report in reports}, {"ok"})
        reopened = presets.GenerationPresetStore(
            self.runtime_root, scope_key=SCOPE_KEY,
        )
        records = reopened.list(
            account_scope=ACCOUNT_A,
            project_scope=PROJECT_A,
        )
        self.assertEqual(len(records), len(workers))
        self.assertEqual(
            {record["id"] for record in records},
            {f"concurrent-{index}" for index in range(len(workers))},
        )

    def test_same_id_races_are_idempotent_or_conflict_without_duplicates(self) -> None:
        context = multiprocessing.get_context(
            "spawn" if os.name == "nt" else "fork",
        )

        def race(runtime_root: Path, names: tuple[str, str], preset_id: str):
            gate = context.Event()
            results = context.Queue()
            workers = [
                context.Process(
                    target=_same_id_create,
                    args=(str(runtime_root), name, preset_id, gate, results),
                )
                for name in names
            ]
            for worker in workers:
                worker.start()
            gate.set()
            reports = [results.get(timeout=20) for _worker in workers]
            for worker in workers:
                worker.join(timeout=20)
                self.assertEqual(worker.exitcode, 0)
            return reports

        identical = race(self.runtime_root, ("Same", "Same"), "same-id")
        self.assertEqual([report[0] for report in identical], ["ok", "ok"])
        self.assertEqual(len({report[2] for report in identical}), 1)
        self.assertEqual(
            [record["id"] for record in self.listing()],
            ["same-id"],
        )

        conflict_root = self.runtime_root / "conflict-runtime"
        conflict_root.mkdir()
        conflicting = race(
            conflict_root,
            ("First contender", "Second contender"),
            "same-id-conflict",
        )
        self.assertEqual(
            sorted(report[0] for report in conflicting),
            ["error", "ok"],
        )
        self.assertIn(
            ("error", "GenerationPresetConflict"),
            conflicting,
        )
        conflict_store = presets.GenerationPresetStore(
            conflict_root, scope_key=SCOPE_KEY,
        )
        self.assertEqual(
            len(conflict_store.list(
                account_scope=ACCOUNT_A,
                project_scope=PROJECT_A,
            )),
            1,
        )

    def test_concurrent_updates_allow_exactly_one_revision_winner(self) -> None:
        original = self.create(preset_id="race-update")
        context = multiprocessing.get_context(
            "spawn" if os.name == "nt" else "fork",
        )
        gate = context.Event()
        results = context.Queue()
        workers = [
            context.Process(
                target=_concurrent_update,
                args=(
                    str(self.runtime_root),
                    name,
                    original["revision"],
                    gate,
                    results,
                ),
            )
            for name in ("First update", "Second update")
        ]
        for worker in workers:
            worker.start()
        gate.set()
        reports = [results.get(timeout=20) for _worker in workers]
        for worker in workers:
            worker.join(timeout=20)
            self.assertEqual(worker.exitcode, 0)

        self.assertEqual(
            sorted(report[0] for report in reports),
            ["error", "ok"],
        )
        self.assertIn(
            ("error", "GenerationPresetConflict"),
            reports,
        )
        listed = self.listing()
        self.assertEqual(len(listed), 1)
        winner = next(report for report in reports if report[0] == "ok")
        self.assertEqual(listed[0]["name"], winner[1])
        self.assertEqual(listed[0]["revision"], winner[2])

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-specific")
    def test_post_commit_directory_sync_failure_is_indeterminate_and_retryable(self) -> None:
        real_fsync = os.fsync

        def fail_directory_sync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("simulated directory sync failure")
            real_fsync(descriptor)

        with mock.patch.object(
            presets.os, "fsync", side_effect=fail_directory_sync,
        ):
            with self.assertRaises(presets.GenerationPresetCommitIndeterminate):
                self.create()

        committed = self.listing()
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["id"], "preset-a")
        self.assertEqual(self.create(), committed[0])
        self.assertEqual(len(self.listing()), 1)

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-specific")
    def test_indeterminate_update_replays_with_original_revision(self) -> None:
        original = self.create()
        desired = preset_payload(name="Committed update", steps=32)
        real_fsync = os.fsync

        def fail_directory_sync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("simulated directory sync failure")
            real_fsync(descriptor)

        with mock.patch.object(
            presets.os, "fsync", side_effect=fail_directory_sync,
        ):
            with self.assertRaises(presets.GenerationPresetCommitIndeterminate):
                self.update(
                    payload=desired,
                    expected_revision=original["revision"],
                )

        committed = self.listing()
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["name"], "Committed update")
        self.assertEqual(
            self.update(
                payload=desired,
                expected_revision=original["revision"],
            ),
            committed[0],
        )
        self.assertEqual(self.listing(), committed)

    def test_pre_commit_sync_failure_does_not_publish_partial_state(self) -> None:
        real_fsync = os.fsync

        def fail_regular_sync(descriptor: int) -> None:
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("simulated file sync failure")
            real_fsync(descriptor)

        with mock.patch.object(
            presets.os, "fsync", side_effect=fail_regular_sync,
        ):
            with self.assertRaises(presets.GenerationPresetIntegrityError):
                self.create()
        self.assertFalse(self.store.path.exists())

    @unittest.skipIf(os.name == "nt", "POSIX owner-only modes are required")
    def test_owner_only_directory_lock_and_state_modes_are_repaired_safely(self) -> None:
        self.create()
        os.chmod(self.store.directory, 0o755)
        os.chmod(self.store.lock_path, 0o644)
        os.chmod(self.store.path, 0o644)

        reopened = presets.GenerationPresetStore(
            self.runtime_root, scope_key=SCOPE_KEY,
        )
        self.assertEqual(len(reopened.list(
            account_scope=ACCOUNT_A,
            project_scope=PROJECT_A,
        )), 1)
        self.assertEqual(stat.S_IMODE(reopened.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(reopened.lock_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(reopened.path.stat().st_mode), 0o600)

    def test_keyed_scope_constructor_and_canonical_numeric_corruption_fail_closed(self) -> None:
        with self.assertRaises(presets.GenerationPresetError):
            presets.GenerationPresetStore(self.runtime_root, scope_key=b"too-short")
        created = self.create()
        wrong_key = presets.GenerationPresetStore(
            self.runtime_root,
            scope_key=b"different-generation-preset-scope-key-32-bytes",
        )
        self.assertEqual(
            wrong_key.list(account_scope=ACCOUNT_A, project_scope=PROJECT_A),
            [],
        )
        self.assertEqual(self.listing(), [created])

        self.store.path.write_text(
            '{"state":{"next_sequence":1e999,"schema_version":1,"scopes":{}},'
            '"state_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}',
            encoding="utf-8",
        )
        with self.assertRaises(presets.GenerationPresetIntegrityError):
            self.listing()

    def test_records_and_plain_json_values_are_bounded(self) -> None:
        bounded = presets.GenerationPresetStore(
            self.runtime_root,
            scope_key=SCOPE_KEY,
            max_records=2,
        )
        for index in range(2):
            bounded.create(
                account_scope=ACCOUNT_A,
                project_scope=PROJECT_A,
                preset=preset_payload(name=f"Bounded {index}"),
                preset_id=f"bounded-{index}",
            )
        with self.assertRaises(presets.GenerationPresetLimitError):
            bounded.create(
                account_scope=ACCOUNT_A,
                project_scope=PROJECT_A,
                preset=preset_payload(name="Over bound"),
                preset_id="bounded-2",
            )

        invalid = preset_payload()
        invalid["params"]["custom_settings"] = {
            "h3_spectrum_profile": [[[[[[[[["too-deep"]]]]]]]]],
        }
        with self.assertRaises(presets.GenerationPresetError):
            self.create(payload=invalid, preset_id="deep")

        invalid = preset_payload()
        invalid["activated_loras"] = tuple(invalid["activated_loras"])
        with self.assertRaises(presets.GenerationPresetError):
            self.create(payload=invalid, preset_id="tuple")

        invalid = preset_payload()
        invalid["params"]["guidance_scale"] = float("nan")
        with self.assertRaises(presets.GenerationPresetError):
            self.create(payload=invalid, preset_id="nan")

        invalid = preset_payload()
        invalid["mode"] = []
        with self.assertRaises(presets.GenerationPresetError):
            self.create(payload=invalid, preset_id="bad-mode")

        invalid = preset_payload()
        invalid["lora_weights"]["detail.safetensors"] = [10**1000]
        with self.assertRaises(presets.GenerationPresetError):
            self.create(payload=invalid, preset_id="huge-weight")

        invalid = preset_payload()
        invalid["name"] = "x" * 257
        with self.assertRaises(presets.GenerationPresetError):
            self.create(payload=invalid, preset_id="long-name")

    def test_generation_fields_and_lora_cross_fields_have_concrete_contracts(self) -> None:
        mutations = (
            lambda value: value["params"].update({"num_inference_steps": 0}),
            lambda value: value["params"].update({"guidance_scale": "3.5"}),
            lambda value: value["params"].update({"resolution": "automatic"}),
            lambda value: value["params"].update({"seed": 4.5}),
            lambda value: value["params"].update({"flow_shift": 20.5}),
            lambda value: value["params"].update({"self_refiner_setting": 3}),
            lambda value: value["params"].update({"stage2_steps": 0}),
            lambda value: value["params"].update({"tea_cache": True}),
            lambda value: value["params"]["custom_settings"].update(
                {"h3_attention_engine": "automatic"},
            ),
            lambda value: value["params"]["custom_settings"].update(
                {"h3_sol_tau": 3.0},
            ),
            lambda value: value["lora_weights"].pop("detail.safetensors"),
            lambda value: value.update({"loras_multipliers": "0.80 1.15;0.90"}),
            lambda value: value["lora_weights"].update(
                {"detail.safetensors": [0.8]},
            ),
            lambda value: value.update(
                {"loras_multipliers": "0.80;0.81 1.15;0.90"},
            ),
        )
        for index, mutate in enumerate(mutations):
            payload = preset_payload()
            mutate(payload)
            with self.subTest(index=index):
                with self.assertRaises(presets.GenerationPresetError):
                    self.create(payload=payload, preset_id=f"invalid-{index}")

        empty_loras = preset_payload(name="No LoRAs", steps=52)
        empty_loras["activated_loras"] = []
        empty_loras["loras_multipliers"] = ""
        empty_loras["lora_weights"] = {}
        created = self.create(payload=empty_loras, preset_id="no-loras")
        self.assertEqual(created["params"]["num_inference_steps"], 52)
        self.assertEqual(created["activated_loras"], [])

    def test_prompt_and_private_content_fields_are_never_accepted_or_stored(self) -> None:
        private_text = "PRIVATE-PROMPT-SENTINEL"
        for mutate in (
            lambda payload: payload.update({"prompt": private_text}),
            lambda payload: payload.update({"alt_prompt": private_text}),
            lambda payload: payload.update({"duration_seconds": 20}),
            lambda payload: payload["params"].update({"negative_prompt": private_text}),
            lambda payload: payload["params"].update({"image_refs": [private_text]}),
            lambda payload: payload["params"].update({"duration_seconds": 20}),
            lambda payload: payload["params"].update({"video_length": 0}),
            lambda payload: payload["params"]["custom_settings"].update(
                {"prompt": private_text},
            ),
            lambda payload: payload["params"]["custom_settings"].update(
                {"voxcpm_speaker_count": 1},
            ),
        ):
            payload = preset_payload()
            mutate(payload)
            with self.assertRaises(presets.GenerationPresetError):
                self.create(payload=payload, preset_id="private")

        created = self.create(payload=preset_payload(name="Content free"))
        self.assertNotIn("prompt", created)
        self.assertNotIn("sequence", created)
        self.assertEqual(
            set(created),
            set(preset_payload()) | {"id", "created_at", "revision"},
        )
        raw = self.store.path.read_bytes()
        self.assertNotIn(private_text.encode("utf-8"), raw)
        self.assertNotIn(ACCOUNT_A.encode("utf-8"), raw)
        self.assertNotIn(PROJECT_A.encode("utf-8"), raw)

    def test_scope_and_identifier_inputs_remain_exact_and_legacy_scopes_are_opaque(self) -> None:
        legacy = self.create(
            account_scope="accounts-off:legacy",
            project_scope="legacy-project-instance",
            payload=preset_payload(name="Legacy exact"),
            preset_id="legacy-preset",
        )
        self.assertEqual(
            self.listing(
                account_scope="accounts-off:legacy",
                project_scope="legacy-project-instance",
            ),
            [legacy],
        )
        self.assertEqual(
            self.listing(
                account_scope="ACCOUNTS-OFF:LEGACY",
                project_scope="legacy-project-instance",
            ),
            [],
        )
        with self.assertRaises(presets.GenerationPresetError):
            self.create(preset_id="bad/id")
        with self.assertRaises(presets.GenerationPresetError):
            self.create(
                payload=preset_payload(name="Missing stable id", steps=23),
                preset_id=None,
            )

    def test_routes_and_ui_use_exact_project_scope_without_prompt_content(self) -> None:
        launch = (APP / "launch.py").read_text(encoding="utf-8")
        remote_prefixes = launch[
            launch.index("_REMOTE_LOCAL_ONLY_PREFIXES ="):
            launch.index("_REMOTE_LOCAL_ONLY_EXACT =")
        ]
        self.assertNotIn('"/api/v1/presets"', remote_prefixes)

        routes = launch[
            launch.index("# ── Generation Presets"):
            launch.index("def _read_app_version")
        ]
        self.assertIn("_generation_preset_scope(", routes)
        self.assertIn("_queue_recovery_existing_project_identity", routes)
        self.assertIn('permission="project.read"', routes)
        self.assertIn('permission="project.mutate"', routes)
        create_route = routes[
            routes.index("async def create_preset"):
            routes.index('@api.put("/api/v1/presets/{preset_id}")')
        ]
        self.assertLess(
            create_route.index("_generation_preset_scope("),
            create_route.index("await request.json()"),
        )
        update_route = routes[
            routes.index("async def update_preset"):
            routes.index('@api.delete("/api/v1/presets/{preset_id}")')
        ]
        self.assertLess(
            update_route.index("_generation_preset_scope("),
            update_route.index("await request.json()"),
        )

        client = (ROOT / "ui" / "src" / "api" / "client.ts").read_text(
            encoding="utf-8",
        )
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(
            encoding="utf-8",
        )
        client_contract = client[
            client.index("// --- Presets ---"):
            client.index("// --- LoRAs ---")
        ]
        store_start = store.index("  savePreset: async (name) => {")
        store_contract = store[
            store_start:store.index("// Model options", store_start)
        ]
        self.assertIn("fetchPresets(workspace: string)", client_contract)
        self.assertIn("crypto.getRandomValues", client_contract)
        self.assertNotIn("prompt:", client_contract)
        self.assertNotIn("prompt: ''", store_contract)
        self.assertNotIn("negative_prompt", store_contract)
        self.assertIn("activeWorkspace", store_contract)

    def test_route_accepts_both_profile_versions_through_the_real_store(self) -> None:
        source = (APP / "launch.py").read_text(encoding="utf-8")
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in {
                     "create_preset", "update_preset",
                     "_raise_generation_preset_error",
                 }]
        for node in nodes:
            node.decorator_list = []
        class RouteHttpError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        class Request:
            def __init__(self, body):
                self.body = body
            async def json(self):
                return json.loads(json.dumps(self.body))
        namespace = dict(Request=Request, HTTPException=RouteHttpError,
                         _generation_preset_scope=lambda *_args, **_kwargs: (ACCOUNT_A, PROJECT_A),
                         _generation_preset_store=lambda: self.store)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "profile-route", "exec"), namespace)
        for index, payload in enumerate((preset_payload(), v2_preset_payload())):
            created = asyncio.run(namespace["create_preset"](Request({"id": f"route-{index}", **payload}), "project"))
            self.assertEqual(created["params"], payload["params"])
            self.assertEqual(created.get("ui_settings"), payload.get("ui_settings"))
        current = self.listing()[1]
        desired = v2_preset_payload()
        desired["name"] = "Updated through route"
        desired["params"]["num_inference_steps"] = 52
        desired["ui_settings"]["durationSeconds"] = 24.5
        updated = asyncio.run(namespace["update_preset"](
            "route-1",
            Request({"expected_revision": current["revision"], **desired}),
            "project",
        ))
        self.assertEqual(updated["id"], current["id"])
        self.assertEqual(updated["created_at"], current["created_at"])
        self.assertNotEqual(updated["revision"], current["revision"])
        self.assertEqual(updated["params"], desired["params"])
        self.assertEqual(updated["ui_settings"], desired["ui_settings"])
        with self.assertRaises(RouteHttpError) as caught:
            asyncio.run(namespace["create_preset"](Request({"id": "invalid", **v2_preset_payload(), "private": "not a setting"}), "project"))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(len(self.listing()), 2)

    def test_unauthorized_mutation_routes_never_read_the_request_body(self) -> None:
        launch_path = APP / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(launch_path))

        class RouteHttpError(Exception):
            def __init__(self, status_code: int, detail: str):
                self.status_code = status_code
                self.detail = detail

        body_reads = []

        class Request:
            async def json(self):
                body_reads.append(True)
                return {"private": "must-not-be-read"}

        def deny(*_args, **_kwargs):
            raise RouteHttpError(404, "Project not found")

        for function_name, arguments in (
            ("create_preset", lambda: (Request(), "foreign")),
            ("update_preset", lambda: ("preset-a", Request(), "foreign")),
        ):
            node = next(
                item for item in tree.body
                if isinstance(item, ast.AsyncFunctionDef)
                and item.name == function_name
            )
            node.decorator_list = []
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            namespace = {
                "Request": Request,
                "HTTPException": RouteHttpError,
                "_generation_preset_scope": deny,
            }
            exec(compile(module, str(launch_path), "exec"), namespace)
            with self.subTest(route=function_name), self.assertRaises(
                RouteHttpError,
            ) as denied:
                asyncio.run(namespace[function_name](*arguments()))
            self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(body_reads, [])


if __name__ == "__main__":
    unittest.main()
