import json
import unittest
from unittest.mock import patch

from services.character_sheet_capabilities import (
    CharacterSheetCapabilityError,
    canonical_character_sheet_capabilities,
    character_sheet_capability_projection,
    decode_character_sheet_capabilities,
)


class CharacterSheetCapabilityTests(unittest.TestCase):
    def test_projection_has_exact_order_and_profile_states(self) -> None:
        projection = character_sheet_capability_projection()

        self.assertEqual(
            [profile["id"] for profile in projection["profiles"]],
            [
                "quad_flux2_klein",
                "quad_krea2",
                "dynamic_krea2_experimental",
                "triple_flux2_klein",
            ],
        )
        self.assertEqual(
            [profile["label"] for profile in projection["profiles"]],
            [
                "Quad — FLUX.2 Klein",
                "Quad — Krea 2",
                "Dynamic — Krea 2 (experimental)",
                "Triple — FLUX.2 Klein",
            ],
        )
        self.assertEqual(
            [profile["order"] for profile in projection["profiles"]],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [profile["status"] for profile in projection["profiles"]],
            [
                "requires_server_authorization",
                "legal_blocked",
                "legal_blocked",
                "later_unavailable",
            ],
        )
        self.assertEqual(
            [profile["id"] for profile in projection["profiles"] if profile["default"]],
            ["quad_flux2_klein"],
        )
        self.assertTrue(projection["profiles"][2]["experimental"])
        self.assertTrue(projection["profiles"][2]["requires_explicit_selection"])
        self.assertFalse(projection["profiles"][2]["default"])
        self.assertFalse(any(profile["available"] for profile in projection["profiles"]))
        self.assertFalse(any(profile["executable"] for profile in projection["profiles"]))
        self.assertFalse(projection["selection"]["client_may_enable_profiles"])

    def test_projection_has_exact_required_workflow(self) -> None:
        workflow = character_sheet_capability_projection()["workflow"]
        self.assertEqual(
            workflow,
            [
                {
                    "id": "anchor",
                    "label": "Create the anchor image",
                    "order": 0,
                    "required": True,
                },
                {
                    "id": "local_vlm_review",
                    "label": "Review locally with the VLM",
                    "order": 1,
                    "required": True,
                },
                {
                    "id": "qwen_image_edit_repair",
                    "label": "Repair with Qwen Image Edit",
                    "order": 2,
                    "required": False,
                    "condition": "review_finds_failed_roles",
                },
            ],
        )

    def test_projection_is_content_free(self) -> None:
        encoded = canonical_character_sheet_capabilities().decode("ascii").lower()
        forbidden = (
            "prompt",
            "anchor_id",
            "sha256",
            "digest",
            "file_path",
            "project_id",
            "authorization_evidence",
        )
        for token in forbidden:
            self.assertNotIn(token, encoded)

    def test_projection_is_fresh_and_deterministic(self) -> None:
        first = character_sheet_capability_projection()
        first["profiles"][0]["available"] = True
        second = character_sheet_capability_projection()

        self.assertFalse(second["profiles"][0]["available"])
        self.assertEqual(
            canonical_character_sheet_capabilities(),
            canonical_character_sheet_capabilities(),
        )

    def test_decoder_round_trips_canonical_projection(self) -> None:
        expected = character_sheet_capability_projection()
        self.assertEqual(
            decode_character_sheet_capabilities(
                canonical_character_sheet_capabilities()
            ),
            expected,
        )
        self.assertEqual(
            decode_character_sheet_capabilities(
                canonical_character_sheet_capabilities().decode("ascii")
            ),
            expected,
        )

    def test_decoder_rejects_client_enablement(self) -> None:
        projection = character_sheet_capability_projection()
        projection["profiles"][0]["available"] = True
        projection["profiles"][0]["executable"] = True
        with self.assertRaisesRegex(
            CharacterSheetCapabilityError, "exact server-authored"
        ):
            decode_character_sheet_capabilities(json.dumps(projection))

    def test_decoder_rejects_extra_private_fields(self) -> None:
        for key in ("prompt", "anchor_id", "sha256", "path"):
            with self.subTest(key=key):
                projection = character_sheet_capability_projection()
                projection[key] = "private"
                with self.assertRaises(CharacterSheetCapabilityError):
                    decode_character_sheet_capabilities(json.dumps(projection))

    def test_decoder_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(CharacterSheetCapabilityError, "duplicate key"):
            decode_character_sheet_capabilities(
                '{"schema_version":1,"schema_version":1}'
            )

    def test_decoder_rejects_wrong_types_invalid_json_and_oversize(self) -> None:
        invalid = (None, {}, [], 1)
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(CharacterSheetCapabilityError):
                    decode_character_sheet_capabilities(payload)  # type: ignore[arg-type]

        with self.assertRaises(CharacterSheetCapabilityError):
            decode_character_sheet_capabilities("{")
        with self.assertRaisesRegex(CharacterSheetCapabilityError, "too large"):
            decode_character_sheet_capabilities("x" * 16_385)
        with self.assertRaisesRegex(CharacterSheetCapabilityError, "UTF-8"):
            decode_character_sheet_capabilities(b"\xff")
        with self.assertRaisesRegex(CharacterSheetCapabilityError, "Unicode"):
            decode_character_sheet_capabilities("\ud800")

    def test_workflow_catalog_drift_fails_closed(self) -> None:
        drifted = list(character_sheet_capability_projection()["profiles"])
        drifted[0] = {
            key: value for key, value in drifted[0].items() if key != "order"
        }
        drifted[0]["available"] = True
        with patch(
            "services.character_sheet_capabilities.character_sheet_profile_catalog",
            return_value=tuple(drifted),
        ):
            with self.assertRaisesRegex(
                CharacterSheetCapabilityError, "workflow profiles"
            ):
                character_sheet_capability_projection()

    def test_workflow_catalog_type_drift_and_missing_keys_fail_closed(self) -> None:
        source = list(character_sheet_capability_projection()["profiles"])
        source = [
            {key: value for key, value in profile.items() if key != "order"}
            for profile in source
        ]

        wrong_type = [dict(profile) for profile in source]
        wrong_type[0]["available"] = 0
        with patch(
            "services.character_sheet_capabilities.character_sheet_profile_catalog",
            return_value=tuple(wrong_type),
        ):
            with self.assertRaisesRegex(
                CharacterSheetCapabilityError, "workflow profiles"
            ):
                character_sheet_capability_projection()

        missing_key = [dict(profile) for profile in source]
        del missing_key[0]["available"]
        with patch(
            "services.character_sheet_capabilities.character_sheet_profile_catalog",
            return_value=tuple(missing_key),
        ):
            with self.assertRaisesRegex(
                CharacterSheetCapabilityError, "workflow profiles"
            ):
                character_sheet_capability_projection()


if __name__ == "__main__":
    unittest.main()
