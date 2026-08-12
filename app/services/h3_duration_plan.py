"""Pure, content-free MiniMax H3 duration snapping and redistribution.

This module deliberately does not know MiniMax frame formulas, prompt semantics,
or runtime profiles.  Callers inject the same authoritative server planner used
for generation.  The primitives here only search its integer-frame answers and
apply explicit, bounded segment edits.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Literal, NewType

GeneratedFrameCount = NewType("GeneratedFrameCount", int)
PublishedFrameCount = NewType("PublishedFrameCount", int)

SnapMode = Literal["nearest", "down"]
RedistributionMode = Literal["none", "next", "future"]
OracleConfidence = Literal["high", "low"]
ResultConfidence = Literal["high", "low", "unavailable"]

_MAX_TOTAL_FRAMES = 1_000_000
_MAX_SEGMENTS = 256
_MAX_ORACLE_CALLS = 512
_MAX_REASON_LENGTH = 512


class H3DurationPlanError(ValueError):
    """Raised when duration-plan inputs or oracle evidence are invalid."""


def _integer(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3DurationPlanError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )
    return value


def _reason(value: object, *, name: str = "oracle reason") -> str:
    if type(value) is not str:
        raise H3DurationPlanError(f"{name} must be text.")
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_REASON_LENGTH:
        raise H3DurationPlanError(
            f"{name} must contain 1 through {_MAX_REASON_LENGTH} characters."
        )
    if any(ord(character) < 32 and character not in "\t" for character in normalized):
        raise H3DurationPlanError(f"{name} contains unsupported control characters.")
    return normalized


@dataclass(frozen=True)
class PublishedFrameGrid:
    """Finite legal domain for published frame counts."""

    minimum: PublishedFrameCount
    maximum: PublishedFrameCount
    step: int = 1
    offset: int = 0

    def __post_init__(self) -> None:
        minimum = _integer(
            self.minimum,
            name="published grid minimum",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        maximum = _integer(
            self.maximum,
            name="published grid maximum",
            minimum=minimum,
            maximum=_MAX_TOTAL_FRAMES,
        )
        step = _integer(
            self.step,
            name="published grid step",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        offset = _integer(
            self.offset,
            name="published grid offset",
            minimum=0,
            maximum=step - 1,
        )
        first = minimum + ((offset - minimum) % step)
        if first > maximum:
            raise H3DurationPlanError("Published frame grid has no legal values.")
        object.__setattr__(self, "minimum", PublishedFrameCount(minimum))
        object.__setattr__(self, "maximum", PublishedFrameCount(maximum))
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "offset", offset)

    @property
    def first(self) -> PublishedFrameCount:
        return PublishedFrameCount(
            int(self.minimum) + ((self.offset - int(self.minimum)) % self.step)
        )

    @property
    def count(self) -> int:
        return ((int(self.maximum) - int(self.first)) // self.step) + 1

    def value_at(self, index: int) -> PublishedFrameCount:
        position = _integer(
            index,
            name="published grid index",
            minimum=0,
            maximum=self.count - 1,
        )
        return PublishedFrameCount(int(self.first) + position * self.step)

    def contains(self, value: object) -> bool:
        return (
            type(value) is int
            and int(self.minimum) <= value <= int(self.maximum)
            and (value - self.offset) % self.step == 0
        )

    def floor_index(self, value: int) -> int | None:
        requested = _integer(
            value,
            name="published frame count",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        if requested < int(self.first):
            return None
        return min(self.count - 1, (requested - int(self.first)) // self.step)

    def ceil_index(self, value: int) -> int | None:
        requested = _integer(
            value,
            name="published frame count",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        if requested > int(self.maximum):
            return None
        distance = max(0, requested - int(self.first))
        index = math.ceil(distance / self.step)
        return index if index < self.count else None


@dataclass(frozen=True)
class H3DurationOraclePlan:
    """Authoritative generated/publication geometry for one requested total."""

    requested_published_frames: PublishedFrameCount
    generated_frames: tuple[GeneratedFrameCount, ...]
    published_frames: tuple[PublishedFrameCount, ...]
    confidence: OracleConfidence
    reason: str

    def __post_init__(self) -> None:
        requested = _integer(
            self.requested_published_frames,
            name="requested published frames",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        generated = tuple(self.generated_frames)
        published = tuple(self.published_frames)
        if not generated or len(generated) > _MAX_SEGMENTS:
            raise H3DurationPlanError(
                f"Oracle plan must contain 1 through {_MAX_SEGMENTS} segments."
            )
        if len(generated) != len(published):
            raise H3DurationPlanError(
                "Oracle generated and published segment counts disagree."
            )
        checked_generated: list[GeneratedFrameCount] = []
        checked_published: list[PublishedFrameCount] = []
        for index, (generated_value, published_value) in enumerate(
            zip(generated, published)
        ):
            generated_int = _integer(
                generated_value,
                name=f"oracle segment {index} generated frames",
                minimum=1,
                maximum=_MAX_TOTAL_FRAMES,
            )
            published_int = _integer(
                published_value,
                name=f"oracle segment {index} published frames",
                minimum=1,
                maximum=_MAX_TOTAL_FRAMES,
            )
            if generated_int < published_int:
                raise H3DurationPlanError(
                    f"Oracle segment {index} publishes more frames than it generates."
                )
            checked_generated.append(GeneratedFrameCount(generated_int))
            checked_published.append(PublishedFrameCount(published_int))
        if sum(checked_published) != requested:
            raise H3DurationPlanError(
                "Oracle published segment frames do not match the requested total."
            )
        if self.confidence not in {"high", "low"}:
            raise H3DurationPlanError("Oracle confidence must be high or low.")
        object.__setattr__(
            self, "requested_published_frames", PublishedFrameCount(requested)
        )
        object.__setattr__(self, "generated_frames", tuple(checked_generated))
        object.__setattr__(self, "published_frames", tuple(checked_published))
        object.__setattr__(self, "reason", _reason(self.reason))

    @property
    def segment_count(self) -> int:
        return len(self.generated_frames)


DurationCountOracle = Callable[[PublishedFrameCount], H3DurationOraclePlan | None]
SegmentPlanOracle = Callable[
    [tuple[PublishedFrameCount, ...]], H3DurationOraclePlan | None
]


@dataclass(frozen=True)
class H3DurationSnapResult:
    """One explicit duration-snap decision."""

    mode: SnapMode
    requested_published_frames: PublishedFrameCount
    candidate_published_frames: PublishedFrameCount | None
    segment_count: int | None
    generated_frames: tuple[GeneratedFrameCount, ...]
    segment_published_frames: tuple[PublishedFrameCount, ...]
    confidence: ResultConfidence
    applied: bool
    reason: str


class _UnavailableOracleEvidence(RuntimeError):
    def __init__(self, confidence: ResultConfidence, reason: str) -> None:
        super().__init__(reason)
        self.confidence = confidence
        self.reason = reason


def snap_published_duration(
    requested_published_frames: int,
    *,
    mode: SnapMode,
    grid: PublishedFrameGrid,
    oracle: DurationCountOracle,
    max_oracle_calls: int = 128,
) -> H3DurationSnapResult:
    """Find a proven same-segment-count plateau boundary.

    ``nearest`` considers the boundary on either side and resolves an exact
    distance tie toward the shorter duration. ``down`` never increases the
    requested duration. A boundary is eligible only when high-confidence
    oracle evidence proves that the next legal grid value uses more segments.
    The injected oracle must expose the authoritative planner's nondecreasing
    segment count over this finite grid; unavailable or low-confidence evidence
    conservatively produces an unapplied result.
    """

    requested = _integer(
        requested_published_frames,
        name="requested published frames",
        minimum=1,
        maximum=_MAX_TOTAL_FRAMES,
    )
    if mode not in {"nearest", "down"}:
        raise H3DurationPlanError("Duration snap mode must be nearest or down.")
    if not isinstance(grid, PublishedFrameGrid):
        raise H3DurationPlanError("Duration snap grid is invalid.")
    if not callable(oracle):
        raise H3DurationPlanError("Duration planning oracle is not callable.")
    call_limit = _integer(
        max_oracle_calls,
        name="duration oracle call limit",
        minimum=8,
        maximum=_MAX_ORACLE_CALLS,
    )
    cache: dict[int, H3DurationOraclePlan | None] = {}

    def unavailable(confidence: ResultConfidence, reason: str) -> H3DurationSnapResult:
        return H3DurationSnapResult(
            mode=mode,
            requested_published_frames=PublishedFrameCount(requested),
            candidate_published_frames=None,
            segment_count=None,
            generated_frames=(),
            segment_published_frames=(),
            confidence=confidence,
            applied=False,
            reason=reason,
        )

    def plan_at(index: int) -> H3DurationOraclePlan:
        if index not in cache:
            if len(cache) >= call_limit:
                raise _UnavailableOracleEvidence(
                    "unavailable", "Duration oracle call limit was reached."
                )
            candidate = grid.value_at(index)
            plan = oracle(candidate)
            if plan is not None and not isinstance(plan, H3DurationOraclePlan):
                raise H3DurationPlanError(
                    "Duration oracle returned an unsupported result type."
                )
            if plan is not None and int(plan.requested_published_frames) != int(
                candidate
            ):
                raise H3DurationPlanError(
                    "Duration oracle result does not match its query."
                )
            cache[index] = plan
        plan = cache[index]
        if plan is None:
            raise _UnavailableOracleEvidence(
                "unavailable", "The authoritative planner found no legal candidate."
            )
        if plan.confidence != "high":
            raise _UnavailableOracleEvidence("low", plan.reason)
        ordered = sorted(
            (position, cached.segment_count)
            for position, cached in cache.items()
            if cached is not None and cached.confidence == "high"
        )
        if any(
            earlier_count > later_count
            for (_, earlier_count), (_, later_count) in pairwise(ordered)
        ):
            raise H3DurationPlanError(
                "Duration oracle segment counts are not monotonic on the legal grid."
            )
        return plan

    anchor_indices = {
        index
        for index in (grid.floor_index(requested), grid.ceil_index(requested))
        if index is not None
    }
    if not anchor_indices:
        return unavailable(
            "unavailable", "Requested duration is outside the legal frame grid."
        )

    def plateau_boundaries(anchor_index: int) -> list[tuple[int, H3DurationOraclePlan]]:
        anchor = plan_at(anchor_index)
        count = anchor.segment_count

        low = 0
        high = anchor_index
        while low < high:
            middle = (low + high) // 2
            if plan_at(middle).segment_count >= count:
                high = middle
            else:
                low = middle + 1
        first = low
        if plan_at(first).segment_count != count:
            raise H3DurationPlanError("Duration oracle plateau search is inconsistent.")

        low = anchor_index
        high = grid.count - 1
        while low < high:
            middle = (low + high + 1) // 2
            if plan_at(middle).segment_count <= count:
                low = middle
            else:
                high = middle - 1
        last = low
        if plan_at(last).segment_count != count:
            raise H3DurationPlanError("Duration oracle plateau search is inconsistent.")

        boundaries: list[tuple[int, H3DurationOraclePlan]] = []
        if first > 0:
            previous = plan_at(first - 1)
            if previous.segment_count < count:
                boundaries.append((first - 1, previous))
        if last + 1 < grid.count:
            following = plan_at(last + 1)
            if following.segment_count > count:
                boundaries.append((last, plan_at(last)))
        return boundaries

    try:
        candidates: dict[int, H3DurationOraclePlan] = {}
        for anchor_index in sorted(anchor_indices):
            for boundary_index, boundary_plan in plateau_boundaries(anchor_index):
                candidates[boundary_index] = boundary_plan
    except _UnavailableOracleEvidence as exc:
        return unavailable(exc.confidence, exc.reason)

    eligible = [
        (index, plan)
        for index, plan in candidates.items()
        if mode == "nearest" or int(grid.value_at(index)) <= requested
    ]
    if not eligible:
        return unavailable(
            "unavailable",
            "No proven segment-efficient boundary satisfies the selected snap mode.",
        )
    chosen_index, chosen_plan = min(
        eligible,
        key=lambda item: (
            abs(int(grid.value_at(item[0])) - requested),
            int(grid.value_at(item[0])),
        ),
    )
    chosen = grid.value_at(chosen_index)
    applied = int(chosen) != requested
    return H3DurationSnapResult(
        mode=mode,
        requested_published_frames=PublishedFrameCount(requested),
        candidate_published_frames=chosen,
        segment_count=chosen_plan.segment_count,
        generated_frames=chosen_plan.generated_frames,
        segment_published_frames=chosen_plan.published_frames,
        confidence="high",
        applied=applied,
        reason=(
            "Requested duration already ends at the last legal frame before "
            "the segment count increases."
            if not applied
            else "Snapped to the last legal frame before the segment count increases."
        ),
    )


@dataclass(frozen=True)
class H3SegmentFrameRange:
    """Editable publication bounds plus immutable generated/lock metadata."""

    index: int
    generated_frames: GeneratedFrameCount
    published_frames: PublishedFrameCount
    published_grid: PublishedFrameGrid
    authored_locked: bool = False
    completed_locked: bool = False

    def __post_init__(self) -> None:
        index = _integer(
            self.index,
            name="segment index",
            minimum=0,
            maximum=_MAX_SEGMENTS - 1,
        )
        generated = _integer(
            self.generated_frames,
            name=f"segment {index} generated frames",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        published = _integer(
            self.published_frames,
            name=f"segment {index} published frames",
            minimum=1,
            maximum=_MAX_TOTAL_FRAMES,
        )
        if not isinstance(self.published_grid, PublishedFrameGrid):
            raise H3DurationPlanError(f"Segment {index} published grid is invalid.")
        if not self.published_grid.contains(published):
            raise H3DurationPlanError(
                f"Segment {index} published frames are outside its legal grid."
            )
        if generated < published:
            raise H3DurationPlanError(
                f"Segment {index} publishes more frames than it generates."
            )
        if (
            type(self.authored_locked) is not bool
            or type(self.completed_locked) is not bool
        ):
            raise H3DurationPlanError(f"Segment {index} lock metadata is invalid.")
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "generated_frames", GeneratedFrameCount(generated))
        object.__setattr__(self, "published_frames", PublishedFrameCount(published))

    @property
    def locked(self) -> bool:
        return self.authored_locked or self.completed_locked

    @property
    def lock_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.authored_locked:
            reasons.append("authored")
        if self.completed_locked:
            reasons.append("completed")
        return tuple(reasons)


@dataclass(frozen=True)
class H3SegmentRedistributionResult:
    """Verified edit geometry and any visible target-duration mismatch."""

    mode: RedistributionMode
    target_total_published_frames: PublishedFrameCount
    original_total_published_frames: PublishedFrameCount
    current_total_published_frames: PublishedFrameCount
    proposed_total_published_frames: PublishedFrameCount
    residual_frames: int
    segments: tuple[H3SegmentFrameRange, ...]
    proposed_published_frames: tuple[PublishedFrameCount, ...]
    adjusted_segment_indices: tuple[int, ...]
    confidence: ResultConfidence
    applied: bool
    fully_preserved: bool
    reason: str


def _move_toward(
    segment: H3SegmentFrameRange,
    *,
    current_published_frames: PublishedFrameCount,
    amount: int,
    direction: int,
) -> tuple[PublishedFrameCount, int]:
    if amount <= 0:
        return current_published_frames, 0
    current = int(current_published_frames)
    grid = segment.published_grid
    if direction > 0:
        ceiling = min(int(grid.maximum), current + amount)
        index = grid.floor_index(ceiling)
        if index is None:
            return current_published_frames, 0
        candidate = max(current, int(grid.value_at(index)))
        return PublishedFrameCount(candidate), candidate - current
    floor = max(int(grid.minimum), current - amount)
    index = grid.ceil_index(floor)
    if index is None:
        return current_published_frames, 0
    candidate = min(current, int(grid.value_at(index)))
    return PublishedFrameCount(candidate), current - candidate


def redistribute_segment_duration(
    segments: Sequence[H3SegmentFrameRange],
    *,
    edited_index: int,
    edited_published_frames: int,
    target_total_published_frames: int,
    mode: RedistributionMode,
    oracle: SegmentPlanOracle,
) -> H3SegmentRedistributionResult:
    """Edit one segment and optionally compensate in later unlocked segments.

    ``next`` may use only the immediate successor and never skips over a lock.
    ``future`` distributes a stable equal share, then assigns any legal
    remainder in ascending segment order. Positive residual means the proposal
    is still shorter than the target; negative residual means it is longer.
    """

    original = tuple(segments)
    if not original or len(original) > _MAX_SEGMENTS:
        raise H3DurationPlanError(
            f"Duration edit must contain 1 through {_MAX_SEGMENTS} segments."
        )
    if any(not isinstance(segment, H3SegmentFrameRange) for segment in original):
        raise H3DurationPlanError("Duration edit segment metadata is invalid.")
    if tuple(segment.index for segment in original) != tuple(range(len(original))):
        raise H3DurationPlanError("Duration edit segment indices must be consecutive.")
    index = _integer(
        edited_index,
        name="edited segment index",
        minimum=0,
        maximum=len(original) - 1,
    )
    edited = _integer(
        edited_published_frames,
        name="edited published frames",
        minimum=1,
        maximum=_MAX_TOTAL_FRAMES,
    )
    target = _integer(
        target_total_published_frames,
        name="target total published frames",
        minimum=1,
        maximum=_MAX_TOTAL_FRAMES,
    )
    if mode not in {"none", "next", "future"}:
        raise H3DurationPlanError(
            "Duration redistribution mode must be none, next, or future."
        )
    if not callable(oracle):
        raise H3DurationPlanError("Segment planning oracle is not callable.")
    original_total = sum(int(segment.published_frames) for segment in original)
    if original_total > _MAX_TOTAL_FRAMES:
        raise H3DurationPlanError("Original segment duration exceeds the frame limit.")

    def result(
        *,
        proposed: tuple[PublishedFrameCount, ...],
        verified_segments: tuple[H3SegmentFrameRange, ...] | None,
        confidence: ResultConfidence,
        applied: bool,
        reason: str,
    ) -> H3SegmentRedistributionResult:
        proposed_total = sum(int(value) for value in proposed)
        residual = target - proposed_total
        visible_segments = (
            verified_segments if applied and verified_segments else original
        )
        current_total = proposed_total if applied else original_total
        adjusted = tuple(
            position
            for position, (before, after) in enumerate(
                zip((segment.published_frames for segment in original), proposed)
            )
            if int(before) != int(after)
        )
        return H3SegmentRedistributionResult(
            mode=mode,
            target_total_published_frames=PublishedFrameCount(target),
            original_total_published_frames=PublishedFrameCount(original_total),
            current_total_published_frames=PublishedFrameCount(current_total),
            proposed_total_published_frames=PublishedFrameCount(proposed_total),
            residual_frames=residual,
            segments=visible_segments,
            proposed_published_frames=proposed,
            adjusted_segment_indices=adjusted,
            confidence=confidence,
            applied=applied,
            fully_preserved=applied and residual == 0,
            reason=reason,
        )

    unchanged = tuple(segment.published_frames for segment in original)
    selected = original[index]
    if selected.locked:
        return result(
            proposed=unchanged,
            verified_segments=None,
            confidence="unavailable",
            applied=False,
            reason=(
                "The edited segment is locked by "
                f"{', '.join(selected.lock_reasons)} metadata."
            ),
        )
    if not selected.published_grid.contains(edited):
        return result(
            proposed=unchanged,
            verified_segments=None,
            confidence="unavailable",
            applied=False,
            reason="The requested segment length is outside its legal frame grid.",
        )

    proposed = list(unchanged)
    proposed[index] = PublishedFrameCount(edited)
    residual = target - sum(int(value) for value in proposed)
    redistribution_reason = "Redistribution is disabled."

    if mode == "next" and residual:
        next_index = index + 1
        if next_index >= len(original):
            redistribution_reason = "There is no immediate next segment to adjust."
        elif original[next_index].locked:
            redistribution_reason = "The immediate next segment is locked; later segments were not skipped to."
        else:
            moved, _ = _move_toward(
                original[next_index],
                current_published_frames=proposed[next_index],
                amount=abs(residual),
                direction=1 if residual > 0 else -1,
            )
            proposed[next_index] = moved
            redistribution_reason = "Adjusted only the immediate next segment."

    if mode == "future" and residual:
        eligible = [
            position
            for position in range(index + 1, len(original))
            if not original[position].locked
        ]
        if not eligible:
            redistribution_reason = "No unlocked future segments are available."
        else:
            direction = 1 if residual > 0 else -1
            remaining = abs(residual)
            share = math.ceil(remaining / len(eligible))
            for position in eligible:
                if remaining <= 0:
                    break
                moved, consumed = _move_toward(
                    original[position],
                    current_published_frames=proposed[position],
                    amount=min(share, remaining),
                    direction=direction,
                )
                proposed[position] = moved
                remaining -= consumed
            for position in eligible:
                if remaining <= 0:
                    break
                moved, consumed = _move_toward(
                    original[position],
                    current_published_frames=proposed[position],
                    amount=remaining,
                    direction=direction,
                )
                proposed[position] = moved
                remaining -= consumed
            redistribution_reason = "Distributed compensation across unlocked future segments in stable order."

    proposed_tuple = tuple(PublishedFrameCount(int(value)) for value in proposed)
    proposed_total = sum(int(value) for value in proposed_tuple)
    if proposed_total > _MAX_TOTAL_FRAMES:
        raise H3DurationPlanError("Proposed segment duration exceeds the frame limit.")
    residual = target - proposed_total
    if proposed_tuple == unchanged:
        return result(
            proposed=proposed_tuple,
            verified_segments=None,
            confidence="high",
            applied=False,
            reason=(
                "No frame changes were requested."
                if residual == 0
                else f"{redistribution_reason} Target mismatch remains visible."
            ),
        )

    oracle_plan = oracle(proposed_tuple)
    if oracle_plan is None:
        return result(
            proposed=proposed_tuple,
            verified_segments=None,
            confidence="unavailable",
            applied=False,
            reason="The authoritative planner found no legal proposal.",
        )
    if not isinstance(oracle_plan, H3DurationOraclePlan):
        raise H3DurationPlanError(
            "Segment planning oracle returned an unsupported result type."
        )
    if int(oracle_plan.requested_published_frames) != proposed_total:
        raise H3DurationPlanError("Segment oracle result does not match its proposal.")
    if oracle_plan.published_frames != proposed_tuple:
        raise H3DurationPlanError(
            "Segment oracle silently changed requested published frames."
        )
    if len(oracle_plan.generated_frames) != len(original):
        raise H3DurationPlanError("Segment oracle changed the segment cardinality.")
    if oracle_plan.confidence != "high":
        return result(
            proposed=proposed_tuple,
            verified_segments=None,
            confidence="low",
            applied=False,
            reason=oracle_plan.reason,
        )

    verified = tuple(
        replace(
            segment,
            generated_frames=oracle_plan.generated_frames[position],
            published_frames=proposed_tuple[position],
        )
        for position, segment in enumerate(original)
    )
    return result(
        proposed=proposed_tuple,
        verified_segments=verified,
        confidence="high",
        applied=True,
        reason=(
            f"{redistribution_reason} The original target duration is preserved."
            if residual == 0
            else f"{redistribution_reason} Target mismatch remains visible."
        ),
    )


__all__ = [
    "DurationCountOracle",
    "GeneratedFrameCount",
    "H3DurationOraclePlan",
    "H3DurationPlanError",
    "H3DurationSnapResult",
    "H3SegmentFrameRange",
    "H3SegmentRedistributionResult",
    "OracleConfidence",
    "PublishedFrameCount",
    "PublishedFrameGrid",
    "RedistributionMode",
    "ResultConfidence",
    "SegmentPlanOracle",
    "SnapMode",
    "redistribute_segment_duration",
    "snap_published_duration",
]
