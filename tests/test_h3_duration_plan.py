"""Model-free contracts for H3 duration snapping and segment redistribution."""

from __future__ import annotations

import math
import os
import sys
import unittest

_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.h3_duration_plan import (
    GeneratedFrameCount,
    H3DurationOraclePlan,
    H3DurationPlanError,
    H3SegmentFrameRange,
    PublishedFrameCount,
    PublishedFrameGrid,
    redistribute_segment_duration,
    snap_published_duration,
)


def _split_published(total: int, count: int) -> tuple[PublishedFrameCount, ...]:
    base, remainder = divmod(total, count)
    return tuple(
        PublishedFrameCount(base + (1 if index < remainder else 0))
        for index in range(count)
    )


def _generated_for(
    published: tuple[PublishedFrameCount, ...],
) -> tuple[GeneratedFrameCount, ...]:
    return tuple(
        GeneratedFrameCount(int(value) + ((5 - int(value)) % 17)) for value in published
    )


class _CeilingOracle:
    """Synthetic authoritative oracle; production formulas remain uninvolved."""

    def __init__(self, ceiling: int, *, confidence: str = "high") -> None:
        self.ceiling = ceiling
        self.confidence = confidence
        self.calls: list[int] = []

    def __call__(self, total: PublishedFrameCount) -> H3DurationOraclePlan:
        value = int(total)
        self.calls.append(value)
        count = math.ceil(value / self.ceiling)
        published = _split_published(value, count)
        return H3DurationOraclePlan(
            requested_published_frames=PublishedFrameCount(value),
            generated_frames=_generated_for(published),
            published_frames=published,
            confidence=self.confidence,
            reason=(
                "Synthetic deterministic planning evidence."
                if self.confidence == "high"
                else "Prompt geometry is not available yet."
            ),
        )


class H3DurationSnapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = PublishedFrameGrid(
            minimum=PublishedFrameCount(1),
            maximum=PublishedFrameCount(1_036),
        )

    def test_345_and_346_use_the_proven_one_segment_boundary(self):
        for requested, mode, expected, applied in (
            (345, "nearest", 345, False),
            (345, "down", 345, False),
            (346, "nearest", 345, True),
            (346, "down", 345, True),
        ):
            with self.subTest(requested=requested, mode=mode):
                oracle = _CeilingOracle(345)
                result = snap_published_duration(
                    requested,
                    mode=mode,
                    grid=self.grid,
                    oracle=oracle,
                )
                self.assertEqual(result.candidate_published_frames, expected)
                self.assertEqual(result.segment_count, 1)
                self.assertEqual(result.applied, applied)
                self.assertEqual(result.confidence, "high")
                self.assertLessEqual(len(oracle.calls), 128)

    def test_690_and_691_use_the_proven_two_segment_boundary(self):
        for requested, expected, expected_count in (
            (690, 690, 2),
            (691, 690, 2),
        ):
            for mode in ("nearest", "down"):
                with self.subTest(requested=requested, mode=mode):
                    result = snap_published_duration(
                        requested,
                        mode=mode,
                        grid=self.grid,
                        oracle=_CeilingOracle(345),
                    )
                    self.assertEqual(result.candidate_published_frames, expected)
                    self.assertEqual(result.segment_count, expected_count)

    def test_nearest_breaks_an_exact_tie_downward(self):
        result = snap_published_duration(
            15,
            mode="nearest",
            grid=PublishedFrameGrid(
                minimum=PublishedFrameCount(1),
                maximum=PublishedFrameCount(31),
            ),
            oracle=_CeilingOracle(10),
        )
        self.assertEqual(result.candidate_published_frames, 10)

    def test_down_never_rounds_up_at_the_grid_minimum(self):
        result = snap_published_duration(
            5,
            mode="down",
            grid=PublishedFrameGrid(
                minimum=PublishedFrameCount(5),
                maximum=PublishedFrameCount(73),
                step=17,
                offset=5,
            ),
            oracle=_CeilingOracle(30),
        )
        self.assertIsNone(result.candidate_published_frames)
        self.assertFalse(result.applied)
        self.assertEqual(result.confidence, "unavailable")

    def test_grid_search_uses_only_legal_values_and_ties_down(self):
        oracle = _CeilingOracle(30)
        result = snap_published_duration(
            39,
            mode="nearest",
            grid=PublishedFrameGrid(
                minimum=PublishedFrameCount(5),
                maximum=PublishedFrameCount(90),
                step=17,
                offset=5,
            ),
            oracle=oracle,
        )
        self.assertEqual(result.candidate_published_frames, 22)
        self.assertTrue(all((value - 5) % 17 == 0 for value in oracle.calls))

    def test_no_unseen_transition_means_no_claimed_boundary(self):
        result = snap_published_duration(
            200,
            mode="nearest",
            grid=PublishedFrameGrid(
                minimum=PublishedFrameCount(1),
                maximum=PublishedFrameCount(345),
            ),
            oracle=_CeilingOracle(345),
        )
        self.assertIsNone(result.candidate_published_frames)
        self.assertEqual(result.confidence, "unavailable")

    def test_low_confidence_evidence_is_preserved_without_auto_snap(self):
        result = snap_published_duration(
            346,
            mode="nearest",
            grid=self.grid,
            oracle=_CeilingOracle(345, confidence="low"),
        )
        self.assertIsNone(result.candidate_published_frames)
        self.assertEqual(result.confidence, "low")
        self.assertIn("not available", result.reason)

    def test_oracle_no_candidate_is_visible(self):
        result = snap_published_duration(
            346,
            mode="nearest",
            grid=self.grid,
            oracle=lambda _value: None,
        )
        self.assertIsNone(result.candidate_published_frames)
        self.assertEqual(result.confidence, "unavailable")
        self.assertIn("no legal candidate", result.reason)

    def test_planner_value_error_is_unavailable_not_fatal(self):
        def oracle(total: PublishedFrameCount):
            value = int(total)
            if value < 360:
                raise ValueError(
                    "H3 authored event falls outside the published physical geometry"
                )
            published = _split_published(value, max(1, math.ceil(value / 345)))
            return H3DurationOraclePlan(
                requested_published_frames=PublishedFrameCount(value),
                generated_frames=_generated_for(published),
                published_frames=published,
                confidence="high",
                reason="Synthetic deterministic planning evidence.",
            )

        result = snap_published_duration(
            481,
            mode="nearest",
            grid=self.grid,
            oracle=oracle,
        )
        self.assertIsNone(result.candidate_published_frames)
        self.assertEqual(result.confidence, "unavailable")
        self.assertIn("no legal candidate", result.reason)

    def test_hostile_frame_and_grid_inputs_are_rejected(self):
        with self.assertRaises(H3DurationPlanError):
            snap_published_duration(
                True,
                mode="nearest",
                grid=self.grid,
                oracle=_CeilingOracle(345),
            )
        with self.assertRaises(H3DurationPlanError):
            PublishedFrameGrid(
                minimum=PublishedFrameCount(5),
                maximum=PublishedFrameCount(10),
                step=17,
                offset=11,
            )
        with self.assertRaises(H3DurationPlanError):
            snap_published_duration(
                100,
                mode="closest",  # type: ignore[arg-type]
                grid=self.grid,
                oracle=_CeilingOracle(345),
            )

    def test_oracle_cannot_mislabel_requested_or_published_frames(self):
        def wrong_query(_value: PublishedFrameCount) -> H3DurationOraclePlan:
            return H3DurationOraclePlan(
                requested_published_frames=PublishedFrameCount(11),
                generated_frames=(GeneratedFrameCount(17),),
                published_frames=(PublishedFrameCount(11),),
                confidence="high",
                reason="Wrong query test evidence.",
            )

        with self.assertRaisesRegex(H3DurationPlanError, "does not match its query"):
            snap_published_duration(
                15,
                mode="nearest",
                grid=PublishedFrameGrid(
                    minimum=PublishedFrameCount(1),
                    maximum=PublishedFrameCount(31),
                ),
                oracle=wrong_query,
            )
        with self.assertRaises(H3DurationPlanError):
            H3DurationOraclePlan(
                requested_published_frames=PublishedFrameCount(10),
                generated_frames=(GeneratedFrameCount(10),),
                published_frames=(PublishedFrameCount(True),),
                confidence="high",
                reason="Invalid boolean frame evidence.",
            )


def _segments(
    values: tuple[int, ...] = (100, 100, 100),
    *,
    minimum: int = 50,
    maximum: int = 150,
    step: int = 10,
    authored_locks: frozenset[int] = frozenset(),
    completed_locks: frozenset[int] = frozenset(),
) -> tuple[H3SegmentFrameRange, ...]:
    return tuple(
        H3SegmentFrameRange(
            index=index,
            generated_frames=GeneratedFrameCount(value + 20),
            published_frames=PublishedFrameCount(value),
            published_grid=PublishedFrameGrid(
                minimum=PublishedFrameCount(minimum),
                maximum=PublishedFrameCount(maximum),
                step=step,
                offset=0,
            ),
            authored_locked=index in authored_locks,
            completed_locked=index in completed_locks,
        )
        for index, value in enumerate(values)
    )


def _segment_oracle(
    published: tuple[PublishedFrameCount, ...],
    *,
    confidence: str = "high",
) -> H3DurationOraclePlan:
    return H3DurationOraclePlan(
        requested_published_frames=PublishedFrameCount(sum(published)),
        generated_frames=tuple(
            GeneratedFrameCount(int(value) + 17) for value in published
        ),
        published_frames=published,
        confidence=confidence,
        reason=(
            "Synthetic exact segment planning evidence."
            if confidence == "high"
            else "Segment plan confidence is low."
        ),
    )


class H3SegmentRedistributionTests(unittest.TestCase):
    def test_none_keeps_the_visible_target_mismatch(self):
        result = redistribute_segment_duration(
            _segments(),
            edited_index=0,
            edited_published_frames=120,
            target_total_published_frames=300,
            mode="none",
            oracle=_segment_oracle,
        )
        self.assertTrue(result.applied)
        self.assertFalse(result.fully_preserved)
        self.assertEqual(result.proposed_published_frames, (120, 100, 100))
        self.assertEqual(result.current_total_published_frames, 320)
        self.assertEqual(result.residual_frames, -20)
        self.assertIn("mismatch remains visible", result.reason)

    def test_next_compensates_positive_and_negative_deltas(self):
        for edited, expected in (
            (70, (70, 130, 100)),
            (140, (140, 60, 100)),
        ):
            with self.subTest(edited=edited):
                result = redistribute_segment_duration(
                    _segments(),
                    edited_index=0,
                    edited_published_frames=edited,
                    target_total_published_frames=300,
                    mode="next",
                    oracle=_segment_oracle,
                )
                self.assertEqual(result.proposed_published_frames, expected)
                self.assertEqual(result.residual_frames, 0)
                self.assertTrue(result.fully_preserved)
                self.assertEqual(result.adjusted_segment_indices, (0, 1))

    def test_next_never_skips_a_locked_immediate_successor(self):
        result = redistribute_segment_duration(
            _segments(authored_locks=frozenset({1})),
            edited_index=0,
            edited_published_frames=70,
            target_total_published_frames=300,
            mode="next",
            oracle=_segment_oracle,
        )
        self.assertEqual(result.proposed_published_frames, (70, 100, 100))
        self.assertEqual(result.residual_frames, 30)
        self.assertEqual(result.adjusted_segment_indices, (0,))
        self.assertIn("not skipped", result.reason)
        self.assertEqual(result.segments[1].lock_reasons, ("authored",))

    def test_future_distribution_is_stable_and_spans_unlocked_segments(self):
        calls = []

        def oracle(values: tuple[PublishedFrameCount, ...]) -> H3DurationOraclePlan:
            calls.append(values)
            return _segment_oracle(values)

        results = [
            redistribute_segment_duration(
                _segments(),
                edited_index=0,
                edited_published_frames=70,
                target_total_published_frames=300,
                mode="future",
                oracle=oracle,
            )
            for _ in range(3)
        ]
        self.assertTrue(
            all(
                result.proposed_published_frames == (70, 120, 110) for result in results
            )
        )
        self.assertTrue(all(result.residual_frames == 0 for result in results))
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1], calls[2])

    def test_future_distribution_handles_negative_delta_deterministically(self):
        result = redistribute_segment_duration(
            _segments(),
            edited_index=0,
            edited_published_frames=130,
            target_total_published_frames=300,
            mode="future",
            oracle=_segment_oracle,
        )
        self.assertEqual(result.proposed_published_frames, (130, 80, 90))
        self.assertEqual(result.residual_frames, 0)
        self.assertTrue(result.fully_preserved)

    def test_future_nondivisible_share_never_crosses_target(self):
        for edited, expected in (
            (98, (98, 101, 101, 100)),
            (102, (102, 99, 99, 100)),
        ):
            with self.subTest(edited=edited):
                result = redistribute_segment_duration(
                    _segments(
                        values=(100, 100, 100, 100),
                        minimum=1,
                        maximum=200,
                        step=1,
                    ),
                    edited_index=0,
                    edited_published_frames=edited,
                    target_total_published_frames=400,
                    mode="future",
                    oracle=_segment_oracle,
                )
                self.assertEqual(result.proposed_published_frames, expected)
                self.assertEqual(result.residual_frames, 0)
                self.assertTrue(result.fully_preserved)

    def test_future_skips_authored_and_completed_locks(self):
        result = redistribute_segment_duration(
            _segments(
                values=(100, 100, 100, 100),
                authored_locks=frozenset({1}),
                completed_locks=frozenset({2}),
            ),
            edited_index=0,
            edited_published_frames=70,
            target_total_published_frames=400,
            mode="future",
            oracle=_segment_oracle,
        )
        self.assertEqual(result.proposed_published_frames, (70, 100, 100, 130))
        self.assertEqual(result.residual_frames, 0)
        self.assertEqual(result.segments[1].lock_reasons, ("authored",))
        self.assertEqual(result.segments[2].lock_reasons, ("completed",))

    def test_insufficient_capacity_leaves_a_visible_residual(self):
        result = redistribute_segment_duration(
            _segments(maximum=110),
            edited_index=0,
            edited_published_frames=70,
            target_total_published_frames=300,
            mode="future",
            oracle=_segment_oracle,
        )
        self.assertEqual(result.proposed_published_frames, (70, 110, 110))
        self.assertEqual(result.residual_frames, 10)
        self.assertFalse(result.fully_preserved)
        self.assertIn("mismatch remains visible", result.reason)

    def test_grid_residual_is_not_silently_rounded_past_target(self):
        result = redistribute_segment_duration(
            _segments(),
            edited_index=0,
            edited_published_frames=90,
            target_total_published_frames=297,
            mode="next",
            oracle=_segment_oracle,
        )
        self.assertEqual(result.proposed_published_frames, (90, 100, 100))
        self.assertEqual(result.residual_frames, 7)
        self.assertEqual(result.current_total_published_frames, 290)

    def test_locked_edited_segment_is_unchanged_without_calling_oracle(self):
        calls = []

        def oracle(values: tuple[PublishedFrameCount, ...]) -> H3DurationOraclePlan:
            calls.append(values)
            return _segment_oracle(values)

        result = redistribute_segment_duration(
            _segments(completed_locks=frozenset({0})),
            edited_index=0,
            edited_published_frames=120,
            target_total_published_frames=300,
            mode="future",
            oracle=oracle,
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.proposed_published_frames, (100, 100, 100))
        self.assertEqual(result.segments[0].lock_reasons, ("completed",))
        self.assertEqual(calls, [])

    def test_illegal_edit_is_reported_without_coercion_or_oracle(self):
        calls = []
        result = redistribute_segment_duration(
            _segments(),
            edited_index=0,
            edited_published_frames=95,
            target_total_published_frames=300,
            mode="future",
            oracle=lambda values: calls.append(values),  # type: ignore[arg-type]
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.proposed_published_frames, (100, 100, 100))
        self.assertIn("outside its legal frame grid", result.reason)
        self.assertEqual(calls, [])

    def test_low_confidence_proposal_is_preserved_but_not_applied(self):
        result = redistribute_segment_duration(
            _segments(),
            edited_index=0,
            edited_published_frames=120,
            target_total_published_frames=300,
            mode="none",
            oracle=lambda values: _segment_oracle(values, confidence="low"),
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.confidence, "low")
        self.assertEqual(result.current_total_published_frames, 300)
        self.assertEqual(result.proposed_total_published_frames, 320)
        self.assertEqual(result.proposed_published_frames, (120, 100, 100))

    def test_oracle_generated_frames_replace_stale_generated_geometry(self):
        result = redistribute_segment_duration(
            _segments(),
            edited_index=0,
            edited_published_frames=120,
            target_total_published_frames=300,
            mode="none",
            oracle=_segment_oracle,
        )
        self.assertEqual(
            tuple(segment.generated_frames for segment in result.segments),
            (137, 117, 117),
        )
        self.assertEqual(
            tuple(segment.published_frames for segment in result.segments),
            (120, 100, 100),
        )

    def test_oracle_may_not_coerce_or_change_segment_cardinality(self):
        def coerced(values: tuple[PublishedFrameCount, ...]) -> H3DurationOraclePlan:
            changed = (PublishedFrameCount(int(values[0]) - 10), *values[1:])
            return H3DurationOraclePlan(
                requested_published_frames=PublishedFrameCount(sum(changed)),
                generated_frames=_generated_for(changed),
                published_frames=changed,
                confidence="high",
                reason="Coerced test evidence.",
            )

        with self.assertRaisesRegex(H3DurationPlanError, "does not match its proposal"):
            redistribute_segment_duration(
                _segments(),
                edited_index=0,
                edited_published_frames=120,
                target_total_published_frames=300,
                mode="none",
                oracle=coerced,
            )

    def test_hostile_segment_inputs_are_bounded(self):
        with self.assertRaises(H3DurationPlanError):
            redistribute_segment_duration(
                _segments(),
                edited_index=True,
                edited_published_frames=120,
                target_total_published_frames=300,
                mode="none",
                oracle=_segment_oracle,
            )
        with self.assertRaises(H3DurationPlanError):
            H3SegmentFrameRange(
                index=0,
                generated_frames=GeneratedFrameCount(100),
                published_frames=PublishedFrameCount(110),
                published_grid=PublishedFrameGrid(
                    minimum=PublishedFrameCount(50),
                    maximum=PublishedFrameCount(150),
                    step=10,
                ),
            )


if __name__ == "__main__":
    unittest.main()
