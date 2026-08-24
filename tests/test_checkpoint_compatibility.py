"""Model-free regressions for safe CivitAI checkpoint imports."""
from __future__ import annotations

import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MODULE_PATH = os.path.join(
    _ROOT, "app", "services", "checkpoint_compatibility.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "maestro_checkpoint_compatibility", _MODULE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Could not load checkpoint compatibility module")
compatibility = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = compatibility
_SPEC.loader.exec_module(compatibility)


FLUX1_SHAPES = {
    "img_in.weight": (3072, 64),
    "txt_in.weight": (3072, 4096),
    "double_blocks.0.img_attn.qkv.weight": (9216, 3072),
}
FLUX2_DEV_SHAPES = {
    "img_in.weight": (6144, 128),
    "txt_in.weight": (6144, 15360),
    "double_blocks.0.img_attn.qkv.weight": (18432, 6144),
}
KLEIN4_SHAPES = {
    "img_in.weight": (3072, 128),
    "txt_in.weight": (3072, 7680),
    "double_blocks.0.img_attn.qkv.weight": (9216, 3072),
}
KLEIN9_SHAPES = {
    "img_in.weight": (4096, 128),
    "txt_in.weight": (4096, 12288),
    "double_blocks.0.img_attn.qkv.weight": (12288, 4096),
}
LTX_SHAPES = {
    "patchify_proj.weight": (4096, 128),
    "transformer_blocks.0.attn1.to_q.weight": (4096, 4096),
    "adaln_single.emb.timestep_embedder.linear_1.weight": (4096, 256),
}
KREA2_SHAPES = {
    "first.weight": (6144, 64),
    "blocks.0.attn.wq.weight": (6144, 6144),
    "blocks.0.attn.wk.weight": (1536, 6144),
}
QWEN_SHAPES = {
    "img_in.weight": (3072, 64),
    "transformer_blocks.0.attn.to_q.weight": (3072, 3072),
}
Z_IMAGE_SHAPES = {
    "x_embedder.weight": (3840, 64),
    "cap_embedder.1.weight": (3840, 2560),
}


def _write_safetensors(path: str, shapes: dict[str, tuple[int, ...]]) -> None:
    header = {}
    offset = 0
    for key, shape in shapes.items():
        elements = 1
        for dimension in shape:
            elements *= dimension
        byte_count = elements * 2
        header[key] = {
            "dtype": "F16",
            "shape": list(shape),
            "data_offsets": [offset, offset + byte_count],
        }
        offset += byte_count
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(encoded)))
        handle.write(encoded)
        if offset:
            handle.seek(offset - 1, os.SEEK_CUR)
            handle.write(b"\0")


def _ordered_resolver(*roots: str):
    def resolve(filename: str) -> str | None:
        for root in roots:
            candidate = os.path.join(root, filename)
            if os.path.isfile(candidate):
                return candidate
        return None

    return resolve


class TestCheckpointMappings(unittest.TestCase):
    def test_every_allowlisted_target_has_a_matching_shipped_template(self):
        for targets in compatibility._CHECKPOINT_TARGETS.values():
            for target in targets:
                with self.subTest(template=target.template_model_type):
                    path = os.path.join(
                        _ROOT,
                        "app",
                        "defaults",
                        f"{target.template_model_type}.json",
                    )
                    self.assertTrue(os.path.isfile(path))
                    with open(path, "r", encoding="utf-8") as handle:
                        definition = json.load(handle)
                    self.assertEqual(
                        definition["model"]["architecture"],
                        target.architecture,
                    )

    def test_unknown_and_sdxl_bases_are_not_assigned_a_pipeline(self):
        self.assertEqual(
            compatibility.checkpoint_targets_for_base("SDXL 1.0"), ()
        )
        self.assertEqual(
            compatibility.checkpoint_targets_for_base("Unknown Future Model"),
            (),
        )
        self.assertIn(
            "not supported",
            compatibility.unsupported_checkpoint_reason("SDXL 1.0"),
        )

    def test_ltx_versions_map_to_their_actual_generations(self):
        ltx20 = compatibility.checkpoint_targets_for_base("LTXV2")
        ltx23 = compatibility.checkpoint_targets_for_base("LTXV 2.3")
        self.assertEqual(ltx20[0].architecture, "ltx2_19B")
        self.assertEqual(ltx23[0].architecture, "ltx2_22B")

    def test_only_ambiguous_verified_family_requires_user_choice(self):
        krea = compatibility.checkpoint_targets_for_base("Krea 2")
        self.assertEqual(
            {target.architecture for target in krea},
            {"krea2_raw", "krea2_turbo"},
        )
        self.assertIsNone(
            compatibility.suggested_checkpoint_architecture("Krea 2")
        )
        self.assertEqual(
            compatibility.suggested_checkpoint_architecture("Flux.1 D"),
            "flux",
        )

    def test_metadata_gate_rejects_cross_family_selection(self):
        with self.assertRaisesRegex(
            compatibility.CheckpointCompatibilityError,
            "compatible with flux, not 'flux2_dev'",
        ):
            compatibility.ensure_allowed_checkpoint_target(
                "Flux.1 D", "flux2_dev"
            )

    def test_unidentified_and_ltx_bases_stay_explicit(self):
        self.assertIn(
            "cannot safely choose a compatible pipeline",
            compatibility.unsupported_checkpoint_reason(""),
        )
        self.assertIn(
            "verified checkpoint-import pipeline",
            compatibility.unsupported_checkpoint_reason("Wan 2.2"),
        )
        self.assertEqual(
            compatibility.suggested_checkpoint_architecture("LTXV2"),
            "ltx2_19B",
        )
        self.assertEqual(
            compatibility.suggested_checkpoint_architecture("LTXV 2.3"),
            "ltx2_22B",
        )
        self.assertEqual(
            compatibility.checkpoint_template_model_type("Flux.1 D", "flux"),
            "flux",
        )
        self.assertEqual(
            compatibility.checkpoint_template_model_type("Krea 2", "krea2_turbo"),
            "krea2_turbo",
        )
        self.assertIsNone(
            compatibility.checkpoint_template_model_type("Krea 2", "flux")
        )
        with self.assertRaisesRegex(
            compatibility.CheckpointCompatibilityError,
            "compatible with ltx2_19B, not 'ltx2_22B'",
        ):
            compatibility.ensure_allowed_checkpoint_target("LTXV2", "ltx2_22B")


class TestCheckpointTensorSignatures(unittest.TestCase):
    def _validate(
        self,
        shapes: dict[str, tuple[int, ...]],
        base_model: str,
        architecture: str,
    ) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.safetensors")
            _write_safetensors(path, shapes)
            return compatibility.validate_checkpoint_file(
                path, base_model, architecture
            )

    def test_flux1_cannot_be_registered_as_flux2(self):
        receipt = self._validate(FLUX1_SHAPES, "Flux.1 D", "flux")
        self.assertEqual(receipt["architecture"], "flux")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.safetensors")
            _write_safetensors(path, FLUX1_SHAPES)
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "compatible with flux, not 'flux2_dev'",
            ):
                compatibility.validate_checkpoint_file(
                    path, "Flux.1 D", "flux2_dev"
                )

    def test_flux2_variants_do_not_share_incompatible_shapes(self):
        self._validate(FLUX2_DEV_SHAPES, "Flux.2 D", "flux2_dev")
        self._validate(KLEIN4_SHAPES, "Flux.2 Klein 4B", "flux2_klein_4b")
        self._validate(KLEIN9_SHAPES, "Flux.2 Klein 9B", "flux2_klein_9b")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "klein4.safetensors")
            _write_safetensors(path, KLEIN4_SHAPES)
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "matches flux2_klein_4b, not the selected flux2_klein_9b",
            ):
                compatibility.validate_checkpoint_file(
                    path, "Flux.2 Klein 9B", "flux2_klein_9b"
                )

    def test_shared_layouts_still_require_the_metadata_target(self):
        with tempfile.TemporaryDirectory() as directory:
            flux_path = os.path.join(directory, "flux1.safetensors")
            ltx_path = os.path.join(directory, "ltx.safetensors")
            krea_path = os.path.join(directory, "krea.safetensors")
            _write_safetensors(flux_path, FLUX1_SHAPES)
            _write_safetensors(ltx_path, LTX_SHAPES)
            _write_safetensors(krea_path, KREA2_SHAPES)

            self.assertEqual(
                set(compatibility.detect_checkpoint_architectures(flux_path)),
                {"flux", "flux_schnell", "flux_dev_kontext"},
            )
            self.assertEqual(
                set(compatibility.detect_checkpoint_architectures(ltx_path)),
                {"ltx2_19B", "ltx2_22B"},
            )
            self.assertEqual(
                set(compatibility.detect_checkpoint_architectures(krea_path)),
                {"krea2_raw", "krea2_turbo"},
            )

            flux_receipt = compatibility.validate_checkpoint_file(
                flux_path, "Flux.1 D", "flux"
            )
            self.assertEqual(flux_receipt["status"], "verified")
            self.assertEqual(flux_receipt["architecture"], "flux")
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "compatible with flux, not 'flux_schnell'",
            ):
                compatibility.validate_checkpoint_file(
                    flux_path, "Flux.1 D", "flux_schnell"
                )
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "compatible with ltx2_19B, not 'ltx2_22B'",
            ):
                compatibility.validate_checkpoint_file(
                    ltx_path, "LTXV2", "ltx2_22B"
                )
            krea_receipt = compatibility.validate_checkpoint_file(
                krea_path, "Krea 2", "krea2_raw"
            )
            self.assertEqual(krea_receipt["architecture"], "krea2_raw")
            self.assertEqual(
                set(krea_receipt["matched_layouts"]),
                {"krea2_raw", "krea2_turbo"},
            )

    def test_qwen_z_image_and_ltx_generations_validate_their_own_shapes(self):
        self._validate(QWEN_SHAPES, "Qwen", "qwen_image_20B")
        self._validate(Z_IMAGE_SHAPES, "ZImageTurbo", "z_image")
        self._validate(LTX_SHAPES, "LTXV2", "ltx2_19B")
        self._validate(LTX_SHAPES, "LTXV 2.3", "ltx2_22B")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "qwen.safetensors")
            _write_safetensors(path, QWEN_SHAPES)
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "matches qwen_image_20B, not the selected z_image",
            ):
                compatibility.validate_checkpoint_file(
                    path, "ZImageTurbo", "z_image"
                )

    def test_wrapped_and_quantized_tensor_names_are_normalized(self):
        wrapped = {
            f"model.diffusion_model.{key}._data": shape
            for key, shape in FLUX2_DEV_SHAPES.items()
        }
        self._validate(wrapped, "Flux.2 D", "flux2_dev")

    def test_non_safetensors_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.bin")
            _write_safetensors(path, FLUX1_SHAPES)
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "SafeTensor files only",
            ):
                compatibility.validate_checkpoint_file(
                    path, "Flux.1 D", "flux"
                )

    def test_tensor_offsets_cannot_extend_past_downloaded_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "truncated.safetensors")
            header = {
                "img_in.weight": {
                    "dtype": "F16",
                    "shape": [3072, 64],
                    "data_offsets": [0, 100],
                }
            }
            encoded = json.dumps(header).encode("utf-8")
            with open(path, "wb") as handle:
                handle.write(struct.pack("<Q", len(encoded)))
                handle.write(encoded)
                handle.write(b"\0\0")
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "outside the downloaded payload",
            ):
                compatibility.read_safetensors_header(path)

    def test_tensor_ranges_must_be_non_overlapping_and_size_coherent(self):
        with tempfile.TemporaryDirectory() as directory:
            overlapping = os.path.join(directory, "overlapping.safetensors")
            header = {
                "first": {
                    "dtype": "F16",
                    "shape": [1],
                    "data_offsets": [0, 2],
                },
                "second": {
                    "dtype": "F16",
                    "shape": [1],
                    "data_offsets": [0, 2],
                },
            }
            encoded = json.dumps(header).encode("utf-8")
            with open(overlapping, "wb") as handle:
                handle.write(struct.pack("<Q", len(encoded)))
                handle.write(encoded)
                handle.write(b"\0\0")
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "overlapping or gapped",
            ):
                compatibility.read_safetensors_header(overlapping)

            inconsistent = os.path.join(directory, "inconsistent.safetensors")
            header = {
                "tensor": {
                    "dtype": "F16",
                    "shape": [2],
                    "data_offsets": [0, 2],
                }
            }
            encoded = json.dumps(header).encode("utf-8")
            with open(inconsistent, "wb") as handle:
                handle.write(struct.pack("<Q", len(encoded)))
                handle.write(encoded)
                handle.write(b"\0\0")
            with self.assertRaisesRegex(
                compatibility.CheckpointCompatibilityError,
                "inconsistent byte range",
            ):
                compatibility.read_safetensors_header(inconsistent)

            boolean_descriptor = os.path.join(
                directory,
                "boolean-descriptor.safetensors",
            )
            header = {
                "tensor": {
                    "dtype": "F16",
                    "shape": [True],
                    "data_offsets": [False, 2],
                }
            }
            encoded = json.dumps(header).encode("utf-8")
            with open(boolean_descriptor, "wb") as handle:
                handle.write(struct.pack("<Q", len(encoded)))
                handle.write(encoded)
                handle.write(b"\0\0")
            with self.assertRaises(compatibility.CheckpointCompatibilityError):
                compatibility.read_safetensors_header(boolean_descriptor)


class TestLegacyCheckpointQuarantine(unittest.TestCase):
    def _definition(
        self,
        *,
        architecture: str,
        base_model: str,
        filename: str,
        visible: bool = True,
    ) -> dict:
        return {
            "model": {
                "name": "Imported checkpoint",
                "architecture": architecture,
                "visible": visible,
                "URLs": [filename],
                "civitai": {
                    "modelType": "Checkpoint",
                    "baseModel": base_model,
                    "filename": filename,
                },
            }
        }

    def test_bad_legacy_registration_is_hidden_without_deleting_weights(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            checkpoints = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(checkpoints)
            weight_path = os.path.join(checkpoints, "flux1.safetensors")
            _write_safetensors(weight_path, FLUX1_SHAPES)
            definition_path = os.path.join(finetunes, "bad.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(
                    self._definition(
                        architecture="flux2_dev",
                        base_model="Flux.1 D",
                        filename="flux1.safetensors",
                    ),
                    handle,
                )

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            self.assertTrue(changes[0]["applied"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                quarantined = json.load(handle)["model"]
            self.assertFalse(quarantined["visible"])
            self.assertEqual(
                quarantined["civitai"]["compatibility_status"], "blocked"
            )
            self.assertIn(
                "compatible with flux, not 'flux2_dev'",
                quarantined["civitai"]["compatibility_reason"],
            )
            self.assertEqual(
                quarantined["maestro_checkpoint_quarantine"]["reason"],
                quarantined["civitai"]["compatibility_reason"],
            )
            self.assertIn("maestro_checkpoint_quarantine", quarantined)
            self.assertTrue(os.path.isfile(weight_path))

    def test_provenance_filename_must_match_the_weight_definition_loads(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            checkpoints = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(checkpoints)
            verified_path = os.path.join(checkpoints, "verified.safetensors")
            loaded_path = os.path.join(checkpoints, "loaded.safetensors")
            _write_safetensors(verified_path, FLUX1_SHAPES)
            _write_safetensors(loaded_path, FLUX2_DEV_SHAPES)
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="verified.safetensors",
            )
            definition["model"]["URLs"] = ["loaded.safetensors"]
            definition_path = os.path.join(finetunes, "mismatch.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                model = json.load(handle)["model"]
            self.assertFalse(model["visible"])
            self.assertIn("does not match", model["civitai"]["compatibility_reason"])
            self.assertTrue(os.path.isfile(verified_path))
            self.assertTrue(os.path.isfile(loaded_path))

    def test_provenance_filename_must_be_a_literal_safe_component(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            checkpoints = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(checkpoints)
            weight_path = os.path.join(checkpoints, "verified.safetensors")
            _write_safetensors(weight_path, FLUX1_SHAPES)
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="../verified.safetensors",
            )
            definition["model"]["URLs"] = ["verified.safetensors"]
            definition_path = os.path.join(finetunes, "traversal.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                model = json.load(handle)["model"]
            self.assertFalse(model["visible"])
            self.assertIn(
                "safe local component",
                model["civitai"]["compatibility_reason"],
            )
            self.assertTrue(os.path.isfile(weight_path))

    def test_valid_definition_is_left_alone_and_old_marker_can_restore(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            checkpoints = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(checkpoints)
            weight_path = os.path.join(checkpoints, "flux1.safetensors")
            _write_safetensors(weight_path, FLUX1_SHAPES)
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="flux1.safetensors",
                visible=False,
            )
            definition["model"]["maestro_checkpoint_quarantine"] = {
                "previous_visible": True,
                "reason": "old block",
            }
            definition["model"]["civitai"]["compatibility_status"] = "blocked"
            definition_path = os.path.join(finetunes, "valid.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertTrue(changes[0]["compatible"])
            self.assertTrue(changes[0]["applied"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                restored = json.load(handle)["model"]
            self.assertTrue(restored["visible"])
            self.assertNotIn("maestro_checkpoint_quarantine", restored)
            self.assertNotIn(
                "compatibility_status", restored["civitai"]
            )

    def test_existing_non_safetensor_is_quarantined_without_deleting_weights(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            checkpoints = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(checkpoints)
            weight_path = os.path.join(checkpoints, "legacy.gguf")
            with open(weight_path, "wb") as handle:
                handle.write(b"GGUF")
            definition_path = os.path.join(finetunes, "legacy.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(
                    self._definition(
                        architecture="flux",
                        base_model="Flux.1 D",
                        filename="legacy.gguf",
                    ),
                    handle,
                )

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            self.assertTrue(changes[0]["applied"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                model = json.load(handle)["model"]
            self.assertFalse(model["visible"])
            self.assertIn("maestro_checkpoint_quarantine", model)
            self.assertTrue(os.path.isfile(weight_path))

    def test_quarantine_marker_is_not_removed_when_weight_is_missing(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            os.makedirs(finetunes)
            os.makedirs(os.path.join(app_dir, "ckpts"))
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="missing.safetensors",
                visible=False,
            )
            definition["model"]["maestro_checkpoint_quarantine"] = {
                "previous_visible": True,
                "reason": "awaiting revalidation",
            }
            definition_path = os.path.join(finetunes, "missing.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            self.assertTrue(changes[0]["applied"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                model = json.load(handle)["model"]
            self.assertFalse(model["visible"])
            self.assertIn("maestro_checkpoint_quarantine", model)

    def test_new_marker_restores_exact_prior_visibility_reason_and_receipt(self):
        with tempfile.TemporaryDirectory() as app_dir:
            finetunes = os.path.join(app_dir, "finetunes")
            checkpoints = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(checkpoints)
            weight_path = os.path.join(checkpoints, "flux1.safetensors")
            _write_safetensors(weight_path, FLUX1_SHAPES)
            definition = self._definition(
                architecture="flux2_dev",
                base_model="Flux.1 D",
                filename="flux1.safetensors",
                visible=False,
            )
            civitai = definition["model"]["civitai"]
            civitai["versionId"] = 314
            civitai["compatibility_status"] = "legacy-review"
            civitai["compatibility_reason"] = "owner note"
            civitai["compatibility"] = {
                "status": "legacy",
                "signature_version": 0,
            }
            definition_path = os.path.join(finetunes, "restore.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            compatibility.quarantine_incompatible_checkpoint_definitions(app_dir)
            with open(definition_path, "r", encoding="utf-8") as handle:
                quarantined = json.load(handle)
            quarantined["model"]["architecture"] = "flux"
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(quarantined, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir
            )
            self.assertEqual(len(changes), 1)
            self.assertTrue(changes[0]["compatible"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                restored = json.load(handle)["model"]
            self.assertFalse(restored["visible"])
            self.assertEqual(restored["civitai"]["versionId"], 314)
            self.assertEqual(
                restored["civitai"]["compatibility_status"],
                "legacy-review",
            )
            self.assertEqual(
                restored["civitai"]["compatibility_reason"],
                "owner note",
            )
            self.assertEqual(
                restored["civitai"]["compatibility"]["signature_version"],
                0,
            )
            self.assertNotIn("maestro_checkpoint_quarantine", restored)

    def test_linked_only_valid_weight_restores_after_loader_equivalent_audit(self):
        with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as linked:
            finetunes = os.path.join(app_dir, "finetunes")
            primary = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(primary)
            weight_path = os.path.join(linked, "flux1.safetensors")
            _write_safetensors(weight_path, FLUX1_SHAPES)
            before = os.stat(weight_path)
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="flux1.safetensors",
                visible=False,
            )
            definition["model"]["maestro_checkpoint_quarantine"] = {
                "previous_visible": True,
                "reason": "not found in primary",
            }
            definition["model"]["civitai"]["compatibility_status"] = "blocked"
            definition_path = os.path.join(finetunes, "linked-valid.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir,
                checkpoint_root=primary,
                resolve_checkpoint=_ordered_resolver(primary, linked),
            )
            self.assertEqual(len(changes), 1)
            self.assertTrue(changes[0]["compatible"])
            with open(definition_path, "r", encoding="utf-8") as handle:
                restored = json.load(handle)["model"]
            self.assertTrue(restored["visible"])
            self.assertNotIn("maestro_checkpoint_quarantine", restored)
            after = os.stat(weight_path)
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))

    def test_linked_only_incompatible_weight_reports_tensor_reason(self):
        with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as linked:
            finetunes = os.path.join(app_dir, "finetunes")
            primary = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(primary)
            weight_path = os.path.join(linked, "wrong.safetensors")
            _write_safetensors(weight_path, FLUX2_DEV_SHAPES)
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="wrong.safetensors",
            )
            definition_path = os.path.join(finetunes, "linked-wrong.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir,
                checkpoint_root=primary,
                resolve_checkpoint=_ordered_resolver(primary, linked),
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            self.assertIn("matches flux2_dev", changes[0]["reason"])
            self.assertNotIn("unavailable", changes[0]["reason"])
            self.assertTrue(os.path.isfile(weight_path))

    def test_primary_weight_shadows_compatible_linked_copy(self):
        with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as linked:
            finetunes = os.path.join(app_dir, "finetunes")
            primary = os.path.join(app_dir, "ckpts")
            os.makedirs(finetunes)
            os.makedirs(primary)
            primary_path = os.path.join(primary, "shadowed.safetensors")
            linked_path = os.path.join(linked, "shadowed.safetensors")
            _write_safetensors(primary_path, FLUX2_DEV_SHAPES)
            _write_safetensors(linked_path, FLUX1_SHAPES)
            definition = self._definition(
                architecture="flux",
                base_model="Flux.1 D",
                filename="shadowed.safetensors",
            )
            definition_path = os.path.join(finetunes, "shadowed.json")
            with open(definition_path, "w", encoding="utf-8") as handle:
                json.dump(definition, handle)

            changes = compatibility.quarantine_incompatible_checkpoint_definitions(
                app_dir,
                checkpoint_root=primary,
                resolve_checkpoint=_ordered_resolver(primary, linked),
            )
            self.assertEqual(len(changes), 1)
            self.assertFalse(changes[0]["compatible"])
            self.assertIn("matches flux2_dev", changes[0]["reason"])
            self.assertTrue(os.path.isfile(primary_path))
            self.assertTrue(os.path.isfile(linked_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
