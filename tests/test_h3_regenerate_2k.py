from __future__ import annotations

import copy
import hashlib
import json
import unittest

from services.h3_regenerate_2k import (
    H3_REGENERATE_2K_KIND,
    H3_REGENERATE_2K_SCHEMA,
    H3_REGENERATE_2K_SOURCE_REVISION,
    H3Regenerate2KError,
    build_h3_regenerate_2k_descriptor,
    public_h3_regenerate_2k_projection,
    validate_h3_regenerate_2k_descriptor,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _build(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "semantic_prompt_sha256": _digest("semantic prompt"),
        "executable_prompt_sha256": _digest("executable prompt"),
        "source_media": [
            {"role": "opening_image", "sha256": _digest("image"), "size_bytes": 12},
            {"role": "guide_audio", "sha256": _digest("audio"), "size_bytes": 34},
        ],
        "source_width": 1344,
        "source_height": 768,
        "source_fps": 24,
        "source_audio_sample_rate_hz": 32000,
        "source_audio_channels": 2,
        "base_artifact_basename": "h3-base.mp4",
        "base_artifact_sha256": _digest("base video"),
        "base_artifact_size_bytes": 56,
        "base_sidecar_sha256": _digest("verified sidecar"),
        "disclosure_revision_sha256": _digest("hosted disclosure revision 7"),
        "opt_in_revision_sha256": _digest("hosted disclosure revision 7"),
        "explicit_opt_in": True,
    }
    values.update(overrides)
    return build_h3_regenerate_2k_descriptor(**values)  # type: ignore[arg-type]


class H3Regenerate2KDescriptorTests(unittest.TestCase):
    def test_canonical_round_trip_is_deterministic_and_source_pinned(self) -> None:
        first = _build()
        second = _build()

        self.assertEqual(first, second)
        self.assertEqual(validate_h3_regenerate_2k_descriptor(first), first)
        self.assertEqual(first["kind"], H3_REGENERATE_2K_KIND)
        self.assertEqual(first["schema"], H3_REGENERATE_2K_SCHEMA)
        self.assertEqual(
            first["official_source"]["revision"],  # type: ignore[index]
            H3_REGENERATE_2K_SOURCE_REVISION,
        )
        self.assertEqual(first["source_stage"]["width"], 1344)  # type: ignore[index]
        self.assertEqual(first["source_stage"]["height"], 768)  # type: ignore[index]
        self.assertEqual(first["source_stage"]["fps"], 24)  # type: ignore[index]
        self.assertEqual(
            first["source_stage"]["audio_sample_rate_hz"],  # type: ignore[index]
            32000,
        )
        self.assertEqual(first["source_stage"]["audio_channels"], 2)  # type: ignore[index]
        self.assertEqual(
            first["target"],
            {"kind": "hosted_regenerate_2k", "name": "hosted Regenerate-2K"},
        )
        self.assertNotIn("width", first["target"])
        self.assertNotIn("height", first["target"])
        canonical = json.dumps(first, sort_keys=True, separators=(",", ":"))
        self.assertNotIn("semantic prompt", canonical)
        self.assertNotIn("executable prompt", canonical)

    def test_verified_native_source_geometry_is_sealed_without_target_dimensions(self) -> None:
        variants = (
            (768, 1344, 24, 32000, 2),
            (768, 768, 30, 48000, 1),
            (1024, 768, 60, 96000, 6),
        )
        digests: set[str] = set()
        for width, height, fps, sample_rate, channels in variants:
            with self.subTest(width=width, height=height):
                descriptor = _build(
                    source_width=width,
                    source_height=height,
                    source_fps=fps,
                    source_audio_sample_rate_hz=sample_rate,
                    source_audio_channels=channels,
                )
                self.assertEqual(
                    descriptor["source_stage"],
                    {
                        "kind": "context_ir_formatted",
                        "producer": "local_h3_native",
                        "official_context_ir": False,
                        "width": width,
                        "height": height,
                        "fps": fps,
                        "audio_sample_rate_hz": sample_rate,
                        "audio_channels": channels,
                    },
                )
                self.assertNotIn("width", descriptor["target"])
                self.assertNotIn("height", descriptor["target"])
                digests.add(descriptor["plan_sha256"])  # type: ignore[arg-type]
        self.assertEqual(len(digests), len(variants))

    def test_non_native_or_unbounded_source_facts_fail_closed(self) -> None:
        for override in (
            {"source_width": 1920, "source_height": 1080},
            {"source_fps": 0},
            {"source_audio_sample_rate_hz": 7999},
            {"source_audio_channels": 0},
            {"source_fps": True},
        ):
            with self.subTest(override=override):
                with self.assertRaises(H3Regenerate2KError):
                    _build(**override)

    def test_commitment_and_schema_drift_fail_closed(self) -> None:
        for mutate in (
            lambda value: value["prompt_commitments"].__setitem__(  # type: ignore[union-attr]
                "semantic_prompt_sha256", _digest("changed")
            ),
            lambda value: value["verified_base_artifact"].__setitem__(  # type: ignore[union-attr]
                "sidecar_sha256", _digest("changed sidecar")
            ),
            lambda value: value.__setitem__("version", 2),
            lambda value: value["official_source"].__setitem__(  # type: ignore[union-attr]
                "revision", "0" * 40
            ),
        ):
            changed = copy.deepcopy(_build())
            mutate(changed)
            with self.subTest(changed=changed):
                with self.assertRaises(H3Regenerate2KError):
                    validate_h3_regenerate_2k_descriptor(changed)

    def test_source_media_order_is_part_of_plan_commitment(self) -> None:
        changed = copy.deepcopy(_build())
        changed["source_media"].reverse()  # type: ignore[union-attr]
        with self.assertRaisesRegex(H3Regenerate2KError, "plan digest drifted"):
            validate_h3_regenerate_2k_descriptor(changed)

        rebuilt = _build(source_media=changed["source_media"])
        self.assertNotEqual(rebuilt["plan_sha256"], _build()["plan_sha256"])
        self.assertEqual(
            [item["role"] for item in rebuilt["source_media"]],  # type: ignore[union-attr]
            ["guide_audio", "opening_image"],
        )

    def test_explicit_opt_in_and_exact_disclosure_revision_are_required(self) -> None:
        with self.assertRaisesRegex(H3Regenerate2KError, "explicit.*opt-in"):
            _build(explicit_opt_in=False)
        with self.assertRaisesRegex(H3Regenerate2KError, "disclosed revision"):
            _build(opt_in_revision_sha256=_digest("different revision"))

    def test_descriptor_is_hosted_unavailable_and_has_no_executor(self) -> None:
        descriptor = _build()
        self.assertIs(descriptor["hosted_only"], True)
        self.assertIs(descriptor["execution_available"], False)
        self.assertIs(descriptor["automatic_fallback"], False)

        import services.h3_regenerate_2k as module

        self.assertFalse(hasattr(module, "execute_h3_regenerate_2k"))
        self.assertFalse(hasattr(module, "request_h3_regenerate_2k"))

    def test_exact_schema_contains_no_local_super_resolution_fields(self) -> None:
        def field_names(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value).union(
                    *(field_names(item) for item in value.values())
                )
            if isinstance(value, list):
                return set().union(*(field_names(item) for item in value))
            return set()

        names = {
            name.lower().replace("-", "_") for name in field_names(_build())
        }
        self.assertTrue(
            names.isdisjoint(
                {"flashvsr", "flash_vsr", "spatial_upsampling", "local_sr"}
            )
        )

    def test_malformed_builder_source_media_uses_contract_error(self) -> None:
        malformed = (
            None,
            "media.mp4",
            b"media",
            7,
            [None],
            ["opening_image"],
            [{"role": "opening_image"}],
        )
        for source_media in malformed:
            with self.subTest(source_media=source_media):
                with self.assertRaises(H3Regenerate2KError):
                    _build(source_media=source_media)

    def test_raw_or_operational_fields_are_not_accepted(self) -> None:
        for field in (
            "semantic_prompt",
            "media_path",
            "api_key",
            "provider_url",
            "request",
            "response",
            "endpoint_path",
        ):
            changed = copy.deepcopy(_build())
            changed[field] = "private"
            with self.subTest(field=field):
                with self.assertRaises(H3Regenerate2KError):
                    validate_h3_regenerate_2k_descriptor(changed)

    def test_public_projection_is_bounded_and_redacted(self) -> None:
        projection = public_h3_regenerate_2k_projection(_build())
        self.assertEqual(
            projection,
            {
                "kind": H3_REGENERATE_2K_KIND,
                "hosted_only": True,
                "availability": "unavailable",
                "execution_available": False,
                "automatic_fallback": False,
            },
        )
        serialized = json.dumps(projection, sort_keys=True)
        for forbidden in (
            "sha256",
            "revision",
            "timestamp",
            "path",
            "basename",
            "source_media",
            "prompt",
            "id",
        ):
            self.assertNotIn(forbidden, serialized.lower())


if __name__ == "__main__":
    unittest.main()
