"""CPU-only contract tests for MiniMax H3 arbitrary-frame guides."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from services.h3_guide_plan import (
    H3_GUIDE_CONDITIONING_FAMILY,
    H3_GUIDE_SOURCE_REVISION,
    H3GuidePlanError,
    canonical_h3_guide_plan,
    plan_h3_guide_inputs,
    validate_h3_guide_plan,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _reseal(plan: dict[str, object]) -> None:
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    encoded = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    plan["plan_sha256"] = "sha256:" + hashlib.sha256(encoded).hexdigest()


def _visual(frame_idx: int, count: int, label: str = "visual") -> dict[str, object]:
    return {
        "frame_idx": frame_idx,
        "visual": {"sha256": _digest(label), "count": count},
        "audio": None,
    }


def _audio(frame_idx: int, count: int, label: str = "audio") -> dict[str, object]:
    return {
        "frame_idx": frame_idx,
        "visual": None,
        "audio": {"sha256": _digest(label), "count": count},
    }


class H3GuidePlanTests(unittest.TestCase):
    def test_visual_sources_use_first_image_or_floor_to_native_grid(self) -> None:
        for original, used, selection in (
            (4, 1, "first_image"),
            (5, 5, "legal_prefix"),
            (21, 5, "legal_prefix"),
            (22, 22, "legal_prefix"),
            (38, 22, "legal_prefix"),
            (39, 39, "legal_prefix"),
        ):
            with self.subTest(original=original):
                plan = plan_h3_guide_inputs(124, [_visual(0, original)])
                self.assertIsNotNone(plan)
                visual = plan["guides"][0]["visual"]  # type: ignore[index]
                self.assertEqual(visual["original_frame_count"], original)
                self.assertEqual(visual["used_frame_count"], used)
                self.assertEqual(visual["selection"], selection)
                self.assertEqual(visual["end_frame_exclusive"], used)

    def test_negative_indices_resolve_against_target_and_bounds_are_exact(self) -> None:
        plan = plan_h3_guide_inputs(
            124,
            [
                _visual(-1, 1, "last"),
                _visual(-124, 5, "first"),
            ],
        )
        self.assertIsNotNone(plan)
        self.assertEqual(
            [guide["resolved_frame_idx"] for guide in plan["guides"]],
            [123, 0],
        )
        self.assertEqual(
            [guide["authored_frame_idx"] for guide in plan["guides"]],
            [-1, -124],
        )
        for invalid in (-125, 124, True, 1.5, "5"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(H3GuidePlanError):
                    plan_h3_guide_inputs(124, [_visual(invalid, 1)])  # type: ignore[arg-type]

    def test_visual_span_must_fit_without_remaining_space_crop(self) -> None:
        exact = plan_h3_guide_inputs(124, [_visual(102, 22)])
        self.assertIsNotNone(exact)
        self.assertEqual(
            exact["guides"][0]["visual"]["end_frame_exclusive"],  # type: ignore[index]
            124,
        )
        with self.assertRaisesRegex(H3GuidePlanError, "visual span exceeds"):
            plan_h3_guide_inputs(124, [_visual(103, 22)])

    def test_audio_uses_source_exact_remaining_capacity(self) -> None:
        plan = plan_h3_guide_inputs(
            124,
            [
                _audio(5, 198, "exact"),
                _audio(5, 199, "cropped"),
            ],
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["target_audio_ticks"], 207)
        first, second = [guide["audio"] for guide in plan["guides"]]
        self.assertEqual(first["capacity_tick_count"], 198)
        self.assertEqual(first["used_tick_count"], 198)
        self.assertEqual(first["original_tick_count"], 198)
        self.assertEqual(second["capacity_tick_count"], 198)
        self.assertEqual(second["used_tick_count"], 198)
        self.assertEqual(second["original_tick_count"], 199)

    def test_paired_av_shares_start_but_keeps_independent_spans(self) -> None:
        plan = plan_h3_guide_inputs(
            124,
            [
                {
                    "frame_idx": 5,
                    "visual": {"sha256": _digest("clip"), "count": 38},
                    "audio": {"sha256": _digest("sound"), "count": 250},
                }
            ],
        )
        self.assertIsNotNone(plan)
        guide = plan["guides"][0]
        self.assertEqual(guide["resolved_frame_idx"], 5)
        self.assertEqual(guide["visual"]["used_frame_count"], 22)
        self.assertEqual(guide["visual"]["end_frame_exclusive"], 27)
        self.assertEqual(guide["audio"]["capacity_tick_count"], 198)
        self.assertEqual(guide["audio"]["used_tick_count"], 198)

    def test_each_input_requires_committed_visual_or_audio(self) -> None:
        invalid_inputs: tuple[object, ...] = (
            [{"frame_idx": 0, "visual": None, "audio": None}],
            [{"frame_idx": 0, "visual": {"sha256": _digest("v")}, "audio": None}],
            [{"frame_idx": 0, "visual": {"sha256": "video.mp4", "count": 5}, "audio": None}],
            [{"frame_idx": 0, "visual": {"sha256": _digest("v"), "count": True}, "audio": None}],
            [{"frame_idx": 0, "visual": None, "audio": {"sha256": _digest("a"), "count": 0}}],
            [{"frame_idx": 0, "visual": None, "audio": None, "prompt": "private"}],
            ["guide"],
            "guide",
        )
        for inputs in invalid_inputs:
            with self.subTest(inputs=inputs):
                with self.assertRaises(H3GuidePlanError):
                    plan_h3_guide_inputs(124, inputs)

    def test_target_is_one_legal_bounded_frame_count(self) -> None:
        for valid in (5, 22, 39, 124, 345):
            with self.subTest(valid=valid):
                self.assertIsNotNone(
                    plan_h3_guide_inputs(valid, [_visual(0, 1)])
                )
        for invalid in (4, 6, 344, 346, True, 124.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(H3GuidePlanError):
                    plan_h3_guide_inputs(invalid, [_visual(0, 1)])

    def test_authored_order_and_duplicates_are_preserved_and_sealed(self) -> None:
        inputs = [
            _visual(5, 5, "second"),
            _audio(5, 10, "duplicate-position"),
            _visual(0, 5, "first"),
            _visual(5, 5, "second"),
        ]
        plan = plan_h3_guide_inputs(124, inputs)
        self.assertIsNotNone(plan)
        self.assertEqual(
            [guide["resolved_frame_idx"] for guide in plan["guides"]],
            [5, 5, 0, 5],
        )
        self.assertEqual(
            [guide["sequence_index"] for guide in plan["guides"]],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            plan["guides"][0]["visual"]["sha256"],
            plan["guides"][3]["visual"]["sha256"],
        )
        self.assertEqual(validate_h3_guide_plan(plan), plan)
        self.assertEqual(
            canonical_h3_guide_plan(plan),
            canonical_h3_guide_plan(plan_h3_guide_inputs(124, inputs)),
        )

        reordered = copy.deepcopy(plan)
        reordered["guides"][0], reordered["guides"][1] = (
            reordered["guides"][1],
            reordered["guides"][0],
        )
        with self.assertRaises(H3GuidePlanError):
            validate_h3_guide_plan(reordered)

    def test_schema_geometry_and_digest_tamper_fail_closed(self) -> None:
        plan = plan_h3_guide_inputs(124, [_visual(0, 22), _audio(5, 198)])
        self.assertIsNotNone(plan)
        mutations = (
            lambda value: value.__setitem__("execution_available", True),
            lambda value: value.__setitem__("automatic_fallback", True),
            lambda value: value.__setitem__("continuation_composition_available", True),
            lambda value: value.__setitem__("conditioning_family", "ref2va"),
            lambda value: value["source"].__setitem__("revision", "0" * 40),
            lambda value: value["guides"][0]["visual"].__setitem__("used_frame_count", 5),
            lambda value: value["guides"][1]["audio"].__setitem__("used_tick_count", 197),
            lambda value: value.__setitem__("media_path", "/private/video.mp4"),
        )
        for mutate in mutations:
            changed = copy.deepcopy(plan)
            mutate(changed)
            with self.subTest(changed=changed):
                with self.assertRaises(H3GuidePlanError):
                    validate_h3_guide_plan(changed)

        digest_only = copy.deepcopy(plan)
        digest_only["plan_sha256"] = _digest("tampered")
        with self.assertRaisesRegex(H3GuidePlanError, "digest drifted"):
            validate_h3_guide_plan(digest_only)

    def test_scalar_subclasses_cannot_spoof_canonical_semantics(self) -> None:
        class EqualityString(str):
            def __eq__(self, other: object) -> bool:
                return True

            __hash__ = str.__hash__

        class EqualityInteger(int):
            def __eq__(self, other: object) -> bool:
                return True

        original = plan_h3_guide_inputs(22, [_visual(0, 1)])
        self.assertIsNotNone(original)

        bad_kind = copy.deepcopy(original)
        bad_kind["kind"] = EqualityString("not-the-kind")
        _reseal(bad_kind)
        with self.assertRaisesRegex(H3GuidePlanError, "exact plain JSON"):
            validate_h3_guide_plan(bad_kind)

        bad_geometry = copy.deepcopy(original)
        bad_geometry["guides"][0]["resolved_frame_idx"] = EqualityInteger(999)
        _reseal(bad_geometry)
        with self.assertRaisesRegex(H3GuidePlanError, "exact plain JSON"):
            validate_h3_guide_plan(bad_geometry)

    def test_cyclic_and_overdeep_json_fail_with_contract_errors(self) -> None:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(H3GuidePlanError, "JSON cycle"):
            plan_h3_guide_inputs(
                22,
                [{
                    "frame_idx": 0,
                    "visual": {"sha256": _digest("cyclic"), "count": cyclic},
                    "audio": None,
                }],
            )

        plan = plan_h3_guide_inputs(22, [_visual(0, 1)])
        self.assertIsNotNone(plan)
        deep: object = "leaf"
        for _ in range(40):
            deep = [deep]
        plan["guides"] = deep
        with self.assertRaisesRegex(H3GuidePlanError, "bounded JSON depth"):
            validate_h3_guide_plan(plan)

    def test_plan_contains_commitments_and_geometry_not_private_media(self) -> None:
        plan = plan_h3_guide_inputs(
            124,
            [{
                "frame_idx": 5,
                "visual": {"sha256": _digest("visual"), "count": 22},
                "audio": {"sha256": _digest("audio"), "count": 20},
            }],
        )
        self.assertIsNotNone(plan)
        encoded = json.dumps(plan, sort_keys=True)
        for private in ("path", "prompt", "tensor", "content", "role"):
            self.assertNotIn(private, encoded.lower())
        self.assertEqual(plan["source"]["revision"], H3_GUIDE_SOURCE_REVISION)

    def test_runtime_ref2va_fallback_continuation_and_opening_claims_reject(self) -> None:
        for kwargs in (
            {"conditioning_family": "ref2va"},
            {"execution_available": True},
            {"runtime_available": True},
            {"automatic_fallback": True},
            {"continuation_composition_available": True},
            {"opening_guide_present": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(H3GuidePlanError):
                    plan_h3_guide_inputs(124, [_visual(0, 1)], **kwargs)

        plan = plan_h3_guide_inputs(124, [_visual(0, 1)])
        self.assertIsNotNone(plan)
        self.assertEqual(plan["conditioning_family"], H3_GUIDE_CONDITIONING_FAMILY)
        self.assertIs(plan["execution_available"], False)
        self.assertIs(plan["automatic_fallback"], False)
        self.assertIs(plan["continuation_composition_available"], False)

    def test_no_inputs_leave_legacy_h3_path_without_a_guide_plan(self) -> None:
        self.assertIsNone(plan_h3_guide_inputs(124))
        self.assertIsNone(plan_h3_guide_inputs(124, None))
        self.assertIsNone(plan_h3_guide_inputs(124, []))

        import services.h3_guide_plan as module

        self.assertFalse(hasattr(module, "execute_h3_guide_plan"))
        self.assertFalse(hasattr(module, "compose_h3_guide_continuation"))


if __name__ == "__main__":
    unittest.main()
