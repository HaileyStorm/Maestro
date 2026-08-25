"""CPU-only contract tests for native MiniMax H3 Bridge plans."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from services.h3_bridge_plan import (
    H3BridgePlanError,
    canonical_h3_bridge_plan,
    plan_h3_bridge,
    validate_h3_bridge_plan,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _source(label: str, start: int, end: int) -> dict[str, object]:
    return {
        "sha256": _digest(label),
        "start_frame": start,
        "end_frame_exclusive": end,
    }


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


class H3BridgePlanTests(unittest.TestCase):
    def test_seals_source_ranges_trims_audio_and_final_assembly(self) -> None:
        plan = plan_h3_bridge(
            _source("clip-a", 210, 250),
            _source("clip-b", 0, 40),
            generated_frames=124,
            hidden_head_frames=17,
            hidden_tail_frames=17,
        )
        self.assertEqual(
            plan["sources"]["a_tail"],
            {
                **_source("clip-a", 210, 250),
                "frame_count": 40,
            },
        )
        self.assertEqual(plan["sources"]["b_head"]["frame_count"], 40)
        self.assertEqual(
            plan["generation"],
            {
                "generated_range": {"start_frame": 0, "end_frame_exclusive": 124},
                "generated_frames": 124,
                "generated_audio_ticks": 207,
                "hidden_head_range": {"start_frame": 0, "end_frame_exclusive": 17},
                "hidden_head_audio_ticks": 28,
                "hidden_tail_range": {
                    "start_frame": 107,
                    "end_frame_exclusive": 124,
                },
                "hidden_tail_audio_ticks": 29,
                "published_range": {
                    "start_frame": 17,
                    "end_frame_exclusive": 107,
                },
                "published_frames": 90,
                "published_audio_ticks": 150,
            },
        )
        self.assertEqual(
            plan["assembly"]["order"],
            ["clip_a", "published_bridge", "clip_b"],
        )
        self.assertEqual(
            plan["assembly"]["bridge_published_range"],
            plan["generation"]["published_range"],
        )
        self.assertEqual(plan["audio"]["bridge_mode"], "generated")
        self.assertEqual(plan["audio"]["left_seam"]["owner"], "clip_a")
        self.assertEqual(plan["audio"]["right_seam"]["owner"], "clip_b")
        self.assertFalse(plan["execution_available"])
        self.assertFalse(plan["automatic_fallback"])
        self.assertEqual(validate_h3_bridge_plan(plan), plan)
        self.assertEqual(canonical_h3_bridge_plan(plan), canonical_h3_bridge_plan(plan))

    def test_generated_range_is_ref2va_legal_and_bounded(self) -> None:
        for valid in (107, 124, 141, 328, 345):
            with self.subTest(valid=valid):
                plan = plan_h3_bridge(
                    _source("a", 0, 5),
                    _source("b", 0, 5),
                    generated_frames=valid,
                )
                self.assertEqual(plan["conditioning_family"], "ref2va")
        for invalid in (106, 108, 344, 346, True, 124.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(H3BridgePlanError):
                    plan_h3_bridge(
                        _source("a", 0, 5),
                        _source("b", 0, 5),
                        generated_frames=invalid,
                    )

    def test_hidden_ranges_may_be_empty_but_must_leave_published_media(self) -> None:
        no_trim = plan_h3_bridge(
            _source("a", 0, 5),
            _source("b", 0, 5),
            generated_frames=107,
        )
        self.assertEqual(
            no_trim["generation"]["hidden_head_range"],
            {"start_frame": 0, "end_frame_exclusive": 0},
        )
        self.assertEqual(
            no_trim["generation"]["hidden_tail_range"],
            {"start_frame": 107, "end_frame_exclusive": 107},
        )
        with self.assertRaisesRegex(H3BridgePlanError, "at least one published"):
            plan_h3_bridge(
                _source("a", 0, 5),
                _source("b", 0, 5),
                generated_frames=107,
                hidden_head_frames=53,
                hidden_tail_frames=54,
            )

    def test_audio_policy_is_explicit_and_crossfade_is_bounded_by_hidden_trim(self) -> None:
        crossfade = plan_h3_bridge(
            _source("a", 0, 22),
            _source("b", 0, 22),
            generated_frames=107,
            hidden_head_frames=17,
            hidden_tail_frames=17,
            left_seam_mode="crossfade",
            left_seam_owner="shared",
            left_seam_overlap_audio_ticks=20,
            right_seam_mode="crossfade",
            right_seam_owner="shared",
            right_seam_overlap_audio_ticks=20,
        )
        self.assertEqual(crossfade["audio"]["left_seam"]["mode"], "crossfade")
        self.assertEqual(crossfade["audio"]["right_seam"]["owner"], "shared")

        invalid_options = (
            {"left_seam_mode": "crossfade", "left_seam_owner": "shared"},
            {
                "left_seam_mode": "crossfade",
                "left_seam_owner": "clip_a",
                "left_seam_overlap_audio_ticks": 1,
            },
            {"left_seam_overlap_audio_ticks": 1},
            {"right_seam_owner": "clip_a"},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(H3BridgePlanError):
                    plan_h3_bridge(
                        _source("a", 0, 22),
                        _source("b", 0, 22),
                        generated_frames=107,
                        hidden_head_frames=17,
                        hidden_tail_frames=17,
                        **options,
                    )

    def test_drive_track_digest_is_required_if_and_only_if_used(self) -> None:
        drive = plan_h3_bridge(
            _source("a", 0, 5),
            _source("b", 0, 5),
            generated_frames=107,
            bridge_audio_mode="drive_track",
            drive_track_sha256=_digest("drive"),
            left_seam_owner="drive_track",
            right_seam_owner="drive_track",
        )
        self.assertEqual(drive["audio"]["drive_track_sha256"], _digest("drive"))
        for options in (
            {"bridge_audio_mode": "drive_track"},
            {"drive_track_sha256": _digest("unused")},
            {"left_seam_owner": "drive_track"},
        ):
            with self.subTest(options=options):
                with self.assertRaises(H3BridgePlanError):
                    plan_h3_bridge(
                        _source("a", 0, 5),
                        _source("b", 0, 5),
                        generated_frames=107,
                        **options,
                    )

    def test_rerolls_change_only_attempt_identity_not_recovery_identity(self) -> None:
        first = plan_h3_bridge(
            _source("a", 50, 72),
            _source("b", 0, 22),
            generated_frames=107,
            reroll_index=0,
        )
        second = plan_h3_bridge(
            _source("a", 50, 72),
            _source("b", 0, 22),
            generated_frames=107,
            reroll_index=1,
        )
        self.assertEqual(first["recovery_sha256"], second["recovery_sha256"])
        self.assertEqual(first["reroll"]["scope"], "bridge_only")
        self.assertNotEqual(
            first["reroll"]["identity_sha256"],
            second["reroll"]["identity_sha256"],
        )
        self.assertNotEqual(first["plan_sha256"], second["plan_sha256"])

    def test_paths_prompts_runtime_claims_and_tensorish_values_are_rejected(self) -> None:
        invalid_sources: tuple[object, ...] = (
            {**_source("a", 0, 5), "path": "/private/a.mp4"},
            {**_source("a", 0, 5), "prompt": "private prompt"},
            {**_source("a", 0, 5), "tensor": [1, 2, 3]},
            {"sha256": "a.mp4", "start_frame": 0, "end_frame_exclusive": 5},
        )
        for source in invalid_sources:
            with self.subTest(source=source):
                with self.assertRaises(H3BridgePlanError):
                    plan_h3_bridge(source, _source("b", 0, 5), generated_frames=107)
        for option in (
            {"execution_available": True},
            {"runtime_available": True},
            {"automatic_fallback": True},
            {"conditioning_family": "fl2va_timeline"},
        ):
            with self.subTest(option=option):
                with self.assertRaises(H3BridgePlanError):
                    plan_h3_bridge(
                        _source("a", 0, 5),
                        _source("b", 0, 5),
                        generated_frames=107,
                        **option,
                    )

    def test_schema_geometry_recovery_reroll_and_plan_tamper_fail_closed(self) -> None:
        original = plan_h3_bridge(
            _source("a", 0, 22),
            _source("b", 0, 22),
            generated_frames=124,
            hidden_head_frames=17,
            hidden_tail_frames=17,
        )
        mutations = (
            lambda plan: plan["sources"]["a_tail"].__setitem__("frame_count", 21),
            lambda plan: plan["generation"].__setitem__("published_frames", 89),
            lambda plan: plan["assembly"]["order"].reverse(),
            lambda plan: plan["audio"]["left_seam"].__setitem__("owner", "bridge"),
            lambda plan: plan["reroll"].__setitem__("scope", "entire_job"),
            lambda plan: plan.__setitem__("recovery_sha256", _digest("changed")),
            lambda plan: plan.__setitem__("media_path", "/private/bridge.mp4"),
        )
        for mutate in mutations:
            changed = copy.deepcopy(original)
            mutate(changed)
            _reseal(changed)
            with self.subTest(changed=changed):
                with self.assertRaises(H3BridgePlanError):
                    validate_h3_bridge_plan(changed)

        digest_only = copy.deepcopy(original)
        digest_only["plan_sha256"] = _digest("bad plan seal")
        with self.assertRaisesRegex(H3BridgePlanError, "plan digest drifted"):
            validate_h3_bridge_plan(digest_only)

    def test_scalar_subclasses_and_cycles_cannot_spoof_canonical_plan(self) -> None:
        class EqualityInteger(int):
            def __eq__(self, other: object) -> bool:
                return True

        plan = plan_h3_bridge(
            _source("a", 0, 5),
            _source("b", 0, 5),
            generated_frames=107,
        )
        changed = copy.deepcopy(plan)
        changed["generation"]["generated_frames"] = EqualityInteger(107)
        _reseal(changed)
        with self.assertRaisesRegex(H3BridgePlanError, "exact plain JSON"):
            validate_h3_bridge_plan(changed)

        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(H3BridgePlanError, "JSON cycle"):
            validate_h3_bridge_plan(cyclic)


if __name__ == "__main__":
    unittest.main()
