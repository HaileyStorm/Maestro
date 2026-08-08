from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.model_updates import (
    HfUpdateCandidate,
    ModelUpdateError,
    VersionedModelUpdater,
    select_huggingface_artifact,
    select_huggingface_candidate,
    validate_safetensors,
)


_INT8_TENSORWISE_MARKER = b'{"format":"int8_tensorwise"}'


def _f32_payload(shape: list[int], value: float = 0.25) -> bytes:
    elements = 1
    for dimension in shape:
        elements *= dimension
    return struct.pack(f"<{elements}f", *([value] * elements))


def _safetensors_bytes() -> bytes:
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    return struct.pack("<Q", len(header)) + header + b"\0\0\0\0"


def _safetensors_with_tensors(
    tensors: dict[
        str,
        tuple[str, list[int]] | tuple[str, list[int], bytes],
    ],
) -> bytes:
    widths = {
        "U8": 1,
        "I8": 1,
        "F8_E4M3": 1,
        "F8_E5M2": 1,
        "F16": 2,
        "BF16": 2,
        "F32": 4,
    }
    offset = 0
    header = {}
    payloads = []
    for name, spec in tensors.items():
        dtype, shape, *supplied_payload = spec
        elements = 1
        for dimension in shape:
            elements *= dimension
        size = elements * widths[dtype]
        payload = supplied_payload[0] if supplied_payload else bytes(size)
        if len(payload) != size:
            raise ValueError(f"Payload for {name!r} has {len(payload)} bytes; expected {size}")
        end = offset + size
        header[name] = {
            "dtype": dtype,
            "shape": shape,
            "data_offsets": [offset, end],
        }
        payloads.append(payload)
        offset = end
    encoded = json.dumps(header).encode()
    return struct.pack("<Q", len(encoded)) + encoded + b"".join(payloads)


class ModelUpdateSelectionTests(unittest.TestCase):
    def test_selects_highest_compatible_version_and_pins_repo_sha(self):
        policy = {
            "repo_id": "owner/model",
            "file_pattern": r"alpha-[0-9.]+/h3_fl2va_v(?P<version>[0-9.]+)\.safetensors",
        }
        payload = {
            "sha": "a" * 40,
            "siblings": [
                {"rfilename": "alpha-0.9/h3_fl2va_v0.9.safetensors", "lfs": {"size": 10, "sha256": "1" * 64}},
                {"rfilename": "alpha-0.10/h3_fl2va_v0.10.safetensors", "lfs": {"size": 20, "sha256": "2" * 64}},
                {"rfilename": "alpha-9/ref2va_v9.0.safetensors", "lfs": {"size": 30, "sha256": "3" * 64}},
            ],
        }
        selected = select_huggingface_candidate(payload, policy)
        self.assertEqual(selected.version, "0.10")
        self.assertEqual(selected.revision, "a" * 40)
        self.assertIn("/resolve/" + "a" * 40 + "/", selected.url)

    def test_selects_fixed_companion_at_latest_repo_revision(self):
        policy = {
            "repo_id": "owner/encoder",
            "path": "weights/encoder.safetensors",
        }
        payload = {
            "sha": "b" * 40,
            "siblings": [
                {
                    "rfilename": "weights/encoder.safetensors",
                    "lfs": {"size": 42, "sha256": "3" * 64},
                },
            ],
        }
        selected = select_huggingface_artifact(payload, policy)
        self.assertEqual(selected.path, policy["path"])
        self.assertEqual(selected.version, "b" * 40)
        self.assertIn("/resolve/" + "b" * 40 + "/", selected.url)


class VersionedModelUpdaterTests(unittest.TestCase):
    def assert_transaction_artifacts_cleaned(self, root: Path) -> None:
        leftovers = [
            path.name
            for path in root.rglob("*")
            if "maestro-rollback" in path.name
            or "manifest-stage" in path.name
            or "manifest-backup" in path.name
        ]
        self.assertEqual(leftovers, [])

    def test_offline_check_keeps_last_known_good_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old.safetensors"
            old.write_bytes(_safetensors_bytes())
            model_def = {"URLs": ["https://huggingface.co/o/r/resolve/old/old.safetensors"], "model_update": {"repo_id": "o/r", "file_pattern": ".*"}}
            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                candidate_fetcher=lambda _policy: (_ for _ in ()).throw(OSError("offline")),
            )
            result = updater.ensure_latest("model", model_def, force=True)
            self.assertEqual(result["status"], "offline")
            self.assertTrue(old.is_file())
            self.assertTrue(model_def["URLs"][0].endswith("old.safetensors"))

    def test_replacement_is_validated_published_then_old_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old.safetensors"
            old.write_bytes(_safetensors_bytes())
            data = _safetensors_bytes()
            candidate = HfUpdateCandidate(
                "o/r", "b" * 40, "versions/new_v2.safetensors", "2.0",
                len(data), hashlib.sha256(data).hexdigest(),
            )
            model_def = {"URLs": ["https://huggingface.co/o/r/resolve/old/old.safetensors"], "model_update": {"repo_id": "o/r", "file_pattern": ".*"}}

            def download(_candidate, stage):
                target = stage / "versions" / "new_v2.safetensors"
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                candidate_fetcher=lambda _policy: candidate,
                downloader=download,
                clock=lambda: 100.0,
            )
            result = updater.ensure_latest("model", model_def, force=True)
            self.assertEqual(result["status"], "updated")
            self.assertFalse(old.exists())
            self.assertTrue((root / candidate.filename).is_file())
            self.assertEqual(model_def["URLs"], [candidate.url])
            manifest = json.loads((root / ".maestro-model-updates/model.json").read_text())
            self.assertEqual(manifest["candidate"]["sha256"], candidate.sha256)
            self.assertEqual(manifest["rollback_provenance"]["previous_file"], "old.safetensors")
            self.assertFalse(manifest["rollback_provenance"]["same_filename"])
            self.assert_transaction_artifacts_cleaned(root)

    def test_same_filename_checkpoint_manifest_replace_failure_rolls_back_bytes_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "model.safetensors"
            old_bytes = _safetensors_bytes() + b"old"
            new_bytes = _safetensors_bytes() + b"new"
            destination.write_bytes(old_bytes)
            old_candidate = HfUpdateCandidate(
                "o/r", "a" * 40, "model.safetensors", "1.0",
                len(old_bytes), hashlib.sha256(old_bytes).hexdigest(),
            )
            new_candidate = HfUpdateCandidate(
                "o/r", "b" * 40, "model.safetensors", "2.0",
                len(new_bytes), hashlib.sha256(new_bytes).hexdigest(),
            )
            original_url = old_candidate.url
            model_def = {
                "URLs": [original_url],
                "model_update": {"repo_id": "o/r", "file_pattern": ".*"},
            }

            def download(_candidate, stage):
                target = stage / "model.safetensors"
                target.write_bytes(new_bytes)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                candidate_fetcher=lambda _policy: new_candidate,
                downloader=download,
                clock=lambda: 100.0,
            )
            old_manifest = {
                "schema_version": 1,
                "model_type": "model",
                "candidate": old_candidate.__dict__,
                "last_checked": 1.0,
                "installed_at": 1.0,
            }
            updater._write_manifest("model", old_manifest)
            manifest_path = updater._manifest_path("model")
            original_manifest_bytes = manifest_path.read_bytes()
            real_replace = os.replace

            def fail_manifest_commit(source, target):
                if Path(target) == manifest_path and "manifest-stage" in Path(source).name:
                    real_replace(source, target)
                    raise OSError("injected manifest replace failure")
                return real_replace(source, target)

            with (
                mock.patch("services.model_updates.os.replace", side_effect=fail_manifest_commit),
                self.assertRaisesRegex(Exception, "restored last-known-good"),
            ):
                updater.ensure_latest("model", model_def, force=True)

            self.assertEqual(destination.read_bytes(), old_bytes)
            self.assertEqual(manifest_path.read_bytes(), original_manifest_bytes)
            self.assertEqual(model_def["URLs"], [original_url])
            self.assert_transaction_artifacts_cleaned(root)

    def test_failed_validation_preserves_old_checkpoint_and_definition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old.safetensors"
            old.write_bytes(_safetensors_bytes())
            candidate = HfUpdateCandidate("o/r", "c" * 40, "new.safetensors", "3", 99, "4" * 64)
            original = "https://huggingface.co/o/r/resolve/old/old.safetensors"
            model_def = {"URLs": [original], "model_update": {"repo_id": "o/r", "file_pattern": ".*"}}

            def corrupt(_candidate, stage):
                target = stage / "new.safetensors"
                target.write_bytes(b"bad")
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                candidate_fetcher=lambda _policy: candidate,
                downloader=corrupt,
            )
            with self.assertRaises(ModelUpdateError):
                updater.ensure_latest("model", model_def, force=True)
            self.assertTrue(old.is_file())
            self.assertEqual(model_def["URLs"], [original])
            self.assertFalse((root / "new.safetensors").exists())

    def test_int8_compatibility_rejects_fp8_before_publish_and_preserves_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "model.safetensors"
            old_bytes = _safetensors_with_tensors({
                "blocks.0.attn.out_proj.comfy_quant": ("U8", [1]),
                "blocks.0.attn.out_proj.weight": ("I8", [4, 4]),
                "blocks.0.attn.out_proj.weight_scale": ("F32", [4, 1]),
            })
            destination.write_bytes(old_bytes)
            new_bytes = _safetensors_with_tensors({
                "blocks.0.attn.out_proj.comfy_quant": ("U8", [1]),
                "blocks.0.attn.out_proj.weight": ("F8_E4M3", [4, 4]),
                "blocks.0.attn.out_proj.weight_scale": ("F32", []),
            })
            old_candidate = HfUpdateCandidate(
                "o/r", "a" * 40, destination.name, "1.0",
                len(old_bytes), hashlib.sha256(old_bytes).hexdigest(),
            )
            new_candidate = HfUpdateCandidate(
                "o/r", "b" * 40, destination.name, "2.0",
                len(new_bytes), hashlib.sha256(new_bytes).hexdigest(),
            )
            model_def = {
                "URLs": [old_candidate.url],
                "model_update": {
                    "repo_id": "o/r",
                    "file_pattern": ".*",
                    "compatibility": {"quantization_family": "h3_int8_convrot"},
                },
            }

            def download(_candidate, stage):
                target = stage / destination.name
                target.write_bytes(new_bytes)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                candidate_fetcher=lambda _policy: new_candidate,
                downloader=download,
            )
            old_manifest = {
                "schema_version": 1,
                "model_type": "model",
                "candidate": old_candidate.__dict__,
                "last_checked": 1.0,
                "installed_at": 1.0,
            }
            updater._write_manifest("model", old_manifest)
            manifest_path = updater._manifest_path("model")
            original_manifest = manifest_path.read_bytes()

            with (
                mock.patch.object(updater, "_publish_transaction") as publish,
                self.assertRaisesRegex(ModelUpdateError, "expected I8/F32 ConvRot"),
            ):
                updater.ensure_latest("model", model_def, force=True)
            publish.assert_not_called()
            self.assertEqual(destination.read_bytes(), old_bytes)
            self.assertEqual(manifest_path.read_bytes(), original_manifest)
            self.assertEqual(model_def["URLs"], [old_candidate.url])
            self.assert_transaction_artifacts_cleaned(root)

    def test_pink_main_header_accepts_only_compact_or_full_adaln_layout(self):
        compatibility = {
            "quantization_family": "h3_int8_convrot",
            "required_tensors": {
                "blocks.0.adaln_proj.linear.weight": {
                    "variants": [
                        {"dtype": "I8", "shape": [12, 2688]},
                        {"dtypes": ["F16", "BF16"], "shape": [12, 8]},
                    ],
                },
            },
        }
        layouts = (
            ("I8", [12, 2688], True),
            ("F16", [12, 8], True),
            ("BF16", [12, 8], True),
            ("I8", [12, 8], False),
            ("F16", [12, 2688], False),
        )
        for dtype, shape, accepted in layouts:
            with self.subTest(dtype=dtype, shape=shape), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "pink.safetensors"
                data = _safetensors_with_tensors({
                    "blocks.0.attn.out_proj.comfy_quant": ("U8", [72]),
                    "blocks.0.attn.out_proj.weight": ("I8", [4, 8]),
                    "blocks.0.attn.out_proj.weight_scale": ("F32", [4, 1]),
                    "blocks.0.adaln_proj.linear.weight": (dtype, shape),
                })
                path.write_bytes(data)
                candidate = HfUpdateCandidate(
                    "o/pink", "b" * 40, path.name, "0.2",
                    len(data), hashlib.sha256(data).hexdigest(),
                )
                if accepted:
                    validate_safetensors(path, candidate, compatibility)
                else:
                    with self.assertRaisesRegex(ModelUpdateError, "explicitly supported layout"):
                        validate_safetensors(path, candidate, compatibility)

    def test_w4a8_family_and_critical_layout_accept_header_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "w4a8.safetensors"
            data = _safetensors_with_tensors({
                "blocks.0.attn.out_proj.weight": ("I8", [5376, 3584]),
                "blocks.0.attn.out_proj.weight_s_rel": ("F8_E4M3", [5376, 448]),
                "blocks.0.attn.out_proj.weight_s_channel": ("F32", [5376]),
                "blocks.0.adaln_proj.linear.weight": ("F16", [96768, 8]),
            })
            path.write_bytes(data)
            candidate = HfUpdateCandidate(
                "o/w4a8", "c" * 40, path.name, "1",
                len(data), hashlib.sha256(data).hexdigest(),
            )

            validate_safetensors(path, candidate, {
                "quantization_family": "h3_w4a8",
                "required_tensors": {
                    "blocks.0.adaln_proj.linear.weight": {
                        "dtype": "F16",
                        "shape": [96768, 8],
                    },
                },
            })

    def test_conditioner_header_accepts_scalar_singleton_and_row_scales(self):
        compatibility = {
            "quantization_family": "h3_int8_convrot",
            "required_tensors": {
                "model.embed_tokens.comfy_quant": {
                    "dtype": "U8",
                    "json_fields": {"format": "int8_tensorwise"},
                },
                "model.embed_tokens.weight": {"dtype": "I8", "shape": [4, 3]},
                "model.embed_tokens.weight_scale": {
                    "dtype": "F32",
                    "shapes": [[], [1], [4], [4, 1]],
                    "finite_positive": True,
                },
            },
        }
        for scale_shape in ([], [1], [4], [4, 1]):
            with self.subTest(scale_shape=scale_shape), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "conditioner.safetensors"
                data = _safetensors_with_tensors({
                    "model.embed_tokens.comfy_quant": (
                        "U8", [len(_INT8_TENSORWISE_MARKER)], _INT8_TENSORWISE_MARKER,
                    ),
                    "model.embed_tokens.weight": ("I8", [4, 3]),
                    "model.embed_tokens.weight_scale": (
                        "F32", scale_shape, _f32_payload(scale_shape),
                    ),
                })
                path.write_bytes(data)
                candidate = HfUpdateCandidate(
                    "o/conditioner", "c" * 40, path.name, "1",
                    len(data), hashlib.sha256(data).hexdigest(),
                )
                validate_safetensors(path, candidate, compatibility)

    def test_conditioner_header_rejects_bad_marker_and_scale_values(self):
        compatibility = {
            "quantization_family": "h3_int8_convrot",
            "required_tensors": {
                "model.embed_tokens.comfy_quant": {
                    "dtype": "U8",
                    "json_fields": {"format": "int8_tensorwise"},
                },
                "model.embed_tokens.weight_scale": {
                    "dtype": "F32",
                    "shapes": [[], [1], [4], [4, 1]],
                    "finite_positive": True,
                },
            },
        }
        cases = (
            (b'{"format":"nvfp4"}', 0.25, "does not match the required fields"),
            (b"{", 0.25, "invalid JSON metadata"),
            (_INT8_TENSORWISE_MARKER, -0.25, "finite and positive"),
            (_INT8_TENSORWISE_MARKER, float("nan"), "finite and positive"),
        )
        for marker, scale, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "conditioner.safetensors"
                data = _safetensors_with_tensors({
                    "model.embed_tokens.comfy_quant": ("U8", [len(marker)], marker),
                    "model.embed_tokens.weight": ("I8", [4, 3]),
                    "model.embed_tokens.weight_scale": (
                        "F32", [], _f32_payload([], scale),
                    ),
                })
                path.write_bytes(data)
                candidate = HfUpdateCandidate(
                    "o/conditioner", "c" * 40, path.name, "1",
                    len(data), hashlib.sha256(data).hexdigest(),
                )
                with self.assertRaisesRegex(ModelUpdateError, message):
                    validate_safetensors(path, candidate, compatibility)

    def test_conditioner_component_rejects_bad_scale_shape_before_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "encoder.safetensors"
            old_bytes = _safetensors_with_tensors({
                "model.embed_tokens.comfy_quant": (
                    "U8", [len(_INT8_TENSORWISE_MARKER)], _INT8_TENSORWISE_MARKER,
                ),
                "model.embed_tokens.weight": ("I8", [4, 3]),
                "model.embed_tokens.weight_scale": ("F32", [], _f32_payload([])),
            })
            new_bytes = _safetensors_with_tensors({
                "model.embed_tokens.comfy_quant": (
                    "U8", [len(_INT8_TENSORWISE_MARKER)], _INT8_TENSORWISE_MARKER,
                ),
                "model.embed_tokens.weight": ("I8", [4, 3]),
                "model.embed_tokens.weight_scale": ("F32", [2], _f32_payload([2])),
            })
            destination.write_bytes(old_bytes)
            old_candidate = HfUpdateCandidate(
                "o/conditioner", "a" * 40, destination.name, "old",
                len(old_bytes), hashlib.sha256(old_bytes).hexdigest(),
            )
            new_candidate = HfUpdateCandidate(
                "o/conditioner", "b" * 40, destination.name, "new",
                len(new_bytes), hashlib.sha256(new_bytes).hexdigest(),
            )
            compatibility = {
                "quantization_family": "h3_int8_convrot",
                "required_tensors": {
                    "model.embed_tokens.comfy_quant": {
                        "dtype": "U8",
                        "json_fields": {"format": "int8_tensorwise"},
                    },
                    "model.embed_tokens.weight": {"dtype": "I8", "shape": [4, 3]},
                    "model.embed_tokens.weight_scale": {
                        "dtype": "F32",
                        "shapes": [[], [1], [4], [4, 1]],
                        "finite_positive": True,
                    },
                },
            }
            policy = {
                "id": "conditioning_encoder",
                "repo_id": "o/conditioner",
                "path": destination.name,
                "url_field": "text_encoder_URLs",
                "compatibility": compatibility,
            }
            model_def = {
                "text_encoder_URLs": [old_candidate.url],
                "component_updates": [policy],
            }

            def download(_candidate, stage):
                target = stage / destination.name
                target.write_bytes(new_bytes)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                artifact_fetcher=lambda _policy: new_candidate,
                downloader=download,
            )

            with (
                mock.patch.object(updater, "_publish_transaction") as publish,
                self.assertRaisesRegex(ModelUpdateError, "expected scalar or per-row F32"),
            ):
                updater.ensure_components_latest("pink", model_def, force=True)
            publish.assert_not_called()
            self.assertEqual(destination.read_bytes(), old_bytes)
            self.assertEqual(model_def["text_encoder_URLs"], [old_candidate.url])
            self.assert_transaction_artifacts_cleaned(root)

    def test_finalize_download_rejects_incompatible_header_without_manifest_or_url_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = _safetensors_with_tensors({
                "blocks.0.attn.out_proj.comfy_quant": ("U8", [1]),
                "blocks.0.attn.out_proj.weight": ("F8_E4M3", [2, 2]),
                "blocks.0.attn.out_proj.weight_scale": ("F32", []),
            })
            downloaded = root / "new.safetensors"
            downloaded.write_bytes(data)
            candidate = HfUpdateCandidate(
                "o/r", "d" * 40, downloaded.name, "2.0",
                len(data), hashlib.sha256(data).hexdigest(),
            )
            original_url = "https://huggingface.co/o/r/resolve/old/old.safetensors"
            model_def = {
                "URLs": [original_url],
                "model_update": {
                    "repo_id": "o/r",
                    "file_pattern": ".*",
                    "compatibility": {"quantization_family": "h3_int8_convrot"},
                },
            }
            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
            )

            with self.assertRaisesRegex(ModelUpdateError, "expected I8/F32 ConvRot"):
                updater.finalize_download("model", model_def, candidate.__dict__)

            self.assertFalse(updater._manifest_path("model").exists())
            self.assertEqual(model_def["URLs"], [original_url])

    def test_apply_recorded_rejects_preexisting_incompatible_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = _safetensors_with_tensors({
                "blocks.0.attn.out_proj.comfy_quant": ("U8", [1]),
                "blocks.0.attn.out_proj.weight": ("F8_E4M3", [2, 2]),
                "blocks.0.attn.out_proj.weight_scale": ("F32", []),
            })
            downloaded = root / "recorded.safetensors"
            downloaded.write_bytes(data)
            candidate = HfUpdateCandidate(
                "o/r", "e" * 40, downloaded.name, "2.0",
                len(data), hashlib.sha256(data).hexdigest(),
            )
            original_url = "https://huggingface.co/o/r/resolve/safe/safe.safetensors"
            model_def = {
                "URLs": [original_url],
                "model_update": {
                    "repo_id": "o/r",
                    "file_pattern": ".*",
                    "compatibility": {"quantization_family": "h3_int8_convrot"},
                },
            }
            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
            )
            updater._write_manifest("model", {
                "schema_version": 1,
                "model_type": "model",
                "candidate": candidate.__dict__,
                "last_checked": 1.0,
                "installed_at": 1.0,
            })
            manifest_path = updater._manifest_path("model")
            original_manifest = manifest_path.read_bytes()

            with self.assertRaisesRegex(ModelUpdateError, "expected I8/F32 ConvRot"):
                updater.apply_recorded("model", model_def)

            self.assertEqual(model_def["URLs"], [original_url])
            self.assertEqual(manifest_path.read_bytes(), original_manifest)

    def test_fixed_companion_is_transactionally_replaced_and_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "encoder.safetensors"
            old.write_bytes(_safetensors_bytes())
            data = _safetensors_bytes() + b"new"
            candidate = HfUpdateCandidate(
                "o/encoder", "d" * 40, "encoder.safetensors", "d" * 40,
                len(data), hashlib.sha256(data).hexdigest(),
            )
            policy = {
                "id": "conditioning_encoder",
                "repo_id": "o/encoder",
                "path": "encoder.safetensors",
                "url_field": "text_encoder_URLs",
            }
            model_def = {
                "URLs": [],
                "text_encoder_URLs": ["https://huggingface.co/o/encoder/resolve/old/encoder.safetensors"],
                "component_updates": [policy],
            }

            def download(_candidate, stage):
                target = stage / "encoder.safetensors"
                target.write_bytes(data)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                artifact_fetcher=lambda _policy: candidate,
                downloader=download,
                clock=lambda: 200.0,
            )
            results = updater.ensure_components_latest("pink", model_def, force=True)
            self.assertEqual(results["conditioning_encoder"]["status"], "updated")
            self.assertEqual(old.read_bytes(), data)
            self.assertEqual(model_def["text_encoder_URLs"], [candidate.url])
            manifest = json.loads(
                (root / ".maestro-model-updates/pink--conditioning_encoder.json").read_text()
            )
            self.assertEqual(manifest["candidate"]["sha256"], candidate.sha256)
            self.assertTrue(manifest["rollback_provenance"]["same_filename"])
            self.assert_transaction_artifacts_cleaned(root)

    def test_same_filename_component_manifest_write_failure_keeps_last_known_good(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "encoder.safetensors"
            old_bytes = _safetensors_bytes() + b"old"
            new_bytes = _safetensors_bytes() + b"new"
            destination.write_bytes(old_bytes)
            old_candidate = HfUpdateCandidate(
                "o/encoder", "c" * 40, "encoder.safetensors", "old",
                len(old_bytes), hashlib.sha256(old_bytes).hexdigest(),
            )
            new_candidate = HfUpdateCandidate(
                "o/encoder", "d" * 40, "encoder.safetensors", "new",
                len(new_bytes), hashlib.sha256(new_bytes).hexdigest(),
            )
            policy = {
                "id": "conditioning_encoder",
                "repo_id": "o/encoder",
                "path": "encoder.safetensors",
                "url_field": "text_encoder_URLs",
            }
            model_def = {
                "text_encoder_URLs": [old_candidate.url],
                "component_updates": [policy],
            }

            def download(_candidate, stage):
                target = stage / "encoder.safetensors"
                target.write_bytes(new_bytes)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                artifact_fetcher=lambda _policy: new_candidate,
                downloader=download,
            )
            key = updater._component_key("pink", policy)
            old_manifest = {
                "schema_version": 1,
                "model_type": "pink",
                "component_id": "conditioning_encoder",
                "candidate": old_candidate.__dict__,
                "last_checked": 1.0,
                "installed_at": 1.0,
            }
            updater._write_manifest(key, old_manifest)
            manifest_path = updater._manifest_path(key)
            original_manifest_bytes = manifest_path.read_bytes()

            with (
                mock.patch.object(
                    updater,
                    "_stage_manifest",
                    side_effect=OSError("injected manifest write failure"),
                ),
                self.assertRaisesRegex(OSError, "manifest write failure"),
            ):
                updater.ensure_components_latest("pink", model_def, force=True)

            self.assertEqual(destination.read_bytes(), old_bytes)
            self.assertEqual(manifest_path.read_bytes(), original_manifest_bytes)
            self.assertEqual(model_def["text_encoder_URLs"], [old_candidate.url])
            self.assert_transaction_artifacts_cleaned(root)

    def test_same_filename_component_manifest_replace_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "encoder.safetensors"
            old_bytes = _safetensors_bytes() + b"old"
            new_bytes = _safetensors_bytes() + b"new"
            destination.write_bytes(old_bytes)
            old_candidate = HfUpdateCandidate(
                "o/encoder", "c" * 40, "encoder.safetensors", "old",
                len(old_bytes), hashlib.sha256(old_bytes).hexdigest(),
            )
            new_candidate = HfUpdateCandidate(
                "o/encoder", "d" * 40, "encoder.safetensors", "new",
                len(new_bytes), hashlib.sha256(new_bytes).hexdigest(),
            )
            policy = {
                "id": "conditioning_encoder",
                "repo_id": "o/encoder",
                "path": "encoder.safetensors",
                "url_field": "text_encoder_URLs",
            }
            model_def = {
                "text_encoder_URLs": [old_candidate.url],
                "component_updates": [policy],
            }

            def download(_candidate, stage):
                target = stage / "encoder.safetensors"
                target.write_bytes(new_bytes)
                return str(target)

            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                artifact_fetcher=lambda _policy: new_candidate,
                downloader=download,
            )
            key = updater._component_key("pink", policy)
            old_manifest = {
                "schema_version": 1,
                "model_type": "pink",
                "component_id": "conditioning_encoder",
                "candidate": old_candidate.__dict__,
                "last_checked": 1.0,
                "installed_at": 1.0,
            }
            updater._write_manifest(key, old_manifest)
            manifest_path = updater._manifest_path(key)
            original_manifest_bytes = manifest_path.read_bytes()
            real_replace = os.replace

            def fail_manifest_commit(source, target):
                if Path(target) == manifest_path and "manifest-stage" in Path(source).name:
                    raise OSError("injected component manifest replace failure")
                return real_replace(source, target)

            with (
                mock.patch("services.model_updates.os.replace", side_effect=fail_manifest_commit),
                self.assertRaisesRegex(Exception, "restored last-known-good"),
            ):
                updater.ensure_components_latest("pink", model_def, force=True)

            self.assertEqual(destination.read_bytes(), old_bytes)
            self.assertEqual(manifest_path.read_bytes(), original_manifest_bytes)
            self.assertEqual(model_def["text_encoder_URLs"], [old_candidate.url])
            self.assert_transaction_artifacts_cleaned(root)

    def test_fixed_companion_offline_keeps_pinned_url_and_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "encoder.safetensors"
            old.write_bytes(_safetensors_bytes())
            original = "https://huggingface.co/o/encoder/resolve/old/encoder.safetensors"
            model_def = {
                "text_encoder_URLs": [original],
                "component_updates": [{
                    "id": "conditioning_encoder",
                    "repo_id": "o/encoder",
                    "path": "encoder.safetensors",
                    "url_field": "text_encoder_URLs",
                }],
            }
            updater = VersionedModelUpdater(
                root,
                locate_file=lambda name: str(root / name) if (root / name).is_file() else None,
                artifact_fetcher=lambda _policy: (_ for _ in ()).throw(OSError("offline")),
            )
            result = updater.ensure_components_latest("pink", model_def, force=True)
            self.assertEqual(result["conditioning_encoder"]["status"], "offline")
            self.assertTrue(old.is_file())
            self.assertEqual(model_def["text_encoder_URLs"], [original])


if __name__ == "__main__":
    unittest.main()
