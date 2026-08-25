from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_dasiwa  # noqa: E402


def _write_safetensors(path: Path, metadata: dict[str, str], count: int) -> None:
    header: dict[str, object] = {"__metadata__": metadata}
    for index in range(count):
        header[f"tensor.{index}"] = {
            "dtype": "BF16", "shape": [0], "data_offsets": [0, 0],
        }
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded)


class H3DasiwaTests(unittest.TestCase):
    def test_pinned_identity_keeps_owner_override_and_exact_base_gate(self):
        identity = h3_dasiwa.dasiwa_identity()
        self.assertEqual(
            identity["revision"],
            "da516a7394d11bc5264375697848ca8fe52ba406",
        )
        self.assertEqual(identity["size"], 794_888_664)
        self.assertEqual(
            identity["sha256"],
            "d2a9a723d97520232f17b6fec33335f9e94b03b2c67b56f91f16780355479274",
        )
        self.assertEqual(identity["authored_steps"], 4)
        self.assertFalse(identity["model_card_gate"])
        self.assertEqual(
            set(identity["incompatible_accelerators"]),
            {"turbo", "lightx2v", "spectrum", "sla", "matlow"},
        )

    def test_header_only_validation_and_status_require_exact_base_identity(self):
        metadata = {
            "compatible_main_sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
            "compatibility_scope": "exact_checkpoint_sha256_only",
            "sampler_steps": "4",
            "tensor_count": "569",
            "validation_status": "static_projection_validated; perceptual_render_pending",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / h3_dasiwa.DASIWA_FILENAME
            _write_safetensors(path, metadata, 569)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with (
                mock.patch.object(h3_dasiwa, "DASIWA_SIZE", path.stat().st_size),
                mock.patch.object(h3_dasiwa, "DASIWA_SHA256", digest),
            ):
                validated = h3_dasiwa.validate_dasiwa_lora(path)
                self.assertTrue(validated["header_validated"])
                self.assertEqual(validated["tensor_count"], 569)
                mismatch = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_base_sha256="0" * 64,
                )
                self.assertFalse(mismatch["available"])
                self.assertIn("exact compatible", mismatch["reason"])
                self.assertNotIn("path", mismatch)
                available = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_base_sha256=h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256.upper(),
                )
                self.assertTrue(available["available"])
                self.assertFalse(available["download_required"])
                suspected = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_base_sha256=h3_dasiwa.DASIWA_SUSPECTED_BASE_SHA256,
                    allow_suspected_base=True,
                )
                self.assertTrue(suspected["available"])
                self.assertEqual(
                    suspected["compatibility"], "suspected_compatible_base"
                )

    def test_better_motion_is_download_required_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = h3_dasiwa.experiment_status(
                h3_dasiwa.BETTER_MOTION_ARTIFACT_ID,
                root=temporary,
            )
        self.assertFalse(status["available"])
        self.assertTrue(status["download_required"])
        self.assertEqual(status["filename"], h3_dasiwa.BETTER_MOTION_FILENAME)
        self.assertIn("downloaded explicitly", status["reason"])
        identity = h3_dasiwa.better_motion_identity()
        self.assertEqual(identity["version_id"], 3_257_589)
        self.assertEqual(identity["size"], 298_261_888)
        self.assertEqual(identity["benchmark_strengths"], [0.5, 0.7, 0.9, 1.0])

    def test_suspected_base_override_is_exact_boolean_and_explicit(self):
        with self.assertRaisesRegex(TypeError, "must be a boolean"):
            h3_dasiwa.experiment_status(
                h3_dasiwa.DASIWA_ARTIFACT_ID,
                root="missing",
                allow_suspected_base=1,
            )
        self.assertEqual(
            h3_dasiwa.DASIWA_SUSPECTED_BASE_FILENAME,
            "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
        )
        self.assertEqual(
            h3_dasiwa.DASIWA_SUSPECTED_BASE_SHA256,
            "f86f2f79ebd2d76eb8eeb46091e83982e6ff51d255747e7b16e92834b392b8e9",
        )

    def test_unchanged_validation_failure_is_cached_for_status_polling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / h3_dasiwa.DASIWA_FILENAME).write_bytes(b"invalid")
            h3_dasiwa._cached_validation.cache_clear()
            failure = h3_dasiwa.H3ExperimentCompatibilityError("pinned failure")
            with mock.patch.object(
                h3_dasiwa, "validate_dasiwa_lora", side_effect=failure,
            ) as validator:
                statuses = [
                    h3_dasiwa.experiment_status(
                        h3_dasiwa.DASIWA_ARTIFACT_ID,
                        root=root,
                        selected_base_sha256=h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
                    )
                    for _ in range(2)
                ]
            h3_dasiwa._cached_validation.cache_clear()
        self.assertEqual(validator.call_count, 1)
        self.assertTrue(all(status["reason"] == "pinned failure" for status in statuses))
        self.assertTrue(all(not status["available"] for status in statuses))


if __name__ == "__main__":
    unittest.main()
