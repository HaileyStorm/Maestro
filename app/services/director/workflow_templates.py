"""Deterministic optional workflow templates for Director plans.

The H3 shot-table craft fields and review stages are adapted from the official
MiniMax H3 skills tree pinned at revision
``597042140567efefd8c4adcfe8124c20f63a3399``.  The source decision and full
provenance are recorded in ``docs/research/H3-workflow-refresh-2026-08-23.md``.

This module only projects an already-authored :class:`ProductionPlan` into an
API/persisted advisory view.  It does not add shots, rewrite prompts, inspect
creative content, drive execution, replace ``_h3_shot`` runtime contracts, or
claim UI consumption or completed review.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from .schema import ProductionPlan, ShotPlan


_OFFICIAL_H3_SKILLS_REVISION = "597042140567efefd8c4adcfe8124c20f63a3399"

_PENDING_QC_CHECKS = (
    "identity_and_reference_continuity",
    "spatial_and_lighting_continuity",
    "action_camera_and_handoff",
    "dialogue_sfx_and_audio_timing",
    "final_artifact_integrity",
)


def _seconds(value: Any) -> Decimal:
    """Return a finite timeline value without applying display rounding."""
    if isinstance(value, bool):
        raise ValueError("timeline seconds must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("timeline seconds must be numeric") from exc
    if not result.is_finite() or not isfinite(float(result)):
        raise ValueError("timeline seconds must be finite")
    return result


def _json_seconds(value: Decimal) -> float:
    return float(value)


def _shot_range(shot: ShotPlan, cursor: Decimal) -> tuple[Decimal, Decimal]:
    """Resolve one absolute range from authored clip timing or duration."""
    metadata = shot.metadata if isinstance(shot.metadata, dict) else {}
    has_clip_start = "clip_start" in metadata
    has_clip_end = "clip_end" in metadata
    if has_clip_start and has_clip_end:
        clip_start = _seconds(metadata["clip_start"])
        clip_end = _seconds(metadata["clip_end"])
        if clip_end < clip_start:
            raise ValueError(
                f"shot {shot.shot_id!r} clip_end precedes clip_start"
            )
        return clip_start, clip_end
    if has_clip_start or has_clip_end:
        raise ValueError(
            f"shot {shot.shot_id!r} timing requires clip_start and clip_end"
        )

    duration = _seconds(shot.duration_sec)
    if duration < 0:
        raise ValueError(f"shot {shot.shot_id!r} duration must not be negative")
    return cursor, cursor + duration


def _handoff(metadata: dict[str, Any], *keys: str, fallback: Any = None) -> Any:
    for key in keys:
        if key in metadata:
            return deepcopy(metadata[key])
    return deepcopy(fallback)


def _inherits_prior_handoff(prior: ShotPlan | None, current: ShotPlan) -> bool:
    if prior is None or current.continuity_strategy not in (
        "continuous",
        "extend_previous",
    ):
        return False
    prior_metadata = prior.metadata if isinstance(prior.metadata, dict) else {}
    current_metadata = (
        current.metadata if isinstance(current.metadata, dict) else {}
    )
    prior_group = prior_metadata.get("continuity_group")
    current_group = current_metadata.get("continuity_group")
    if prior_group or current_group:
        return bool(prior_group and prior_group == current_group)
    return True


def _shot_row(
    shot: ShotPlan,
    start: Decimal,
    end: Decimal,
    prior_handoff_out: Any,
) -> dict[str, Any]:
    metadata = shot.metadata if isinstance(shot.metadata, dict) else {}
    audio = deepcopy(shot.audio_plan.to_dict())
    if shot.dialogue_beats:
        audio["dialogue_beats"] = [beat.to_dict() for beat in shot.dialogue_beats]

    row: dict[str, Any] = {
        "shot_id": shot.shot_id,
        "index": shot.index,
        "start_sec": _json_seconds(start),
        "end_sec": _json_seconds(end),
        "duration_sec": _json_seconds(end - start),
        "scene": shot.scene_goal,
        "subjects": [subject.to_dict() for subject in shot.subjects_on_screen],
        "spatial": shot.spatial_setup,
        "environment": shot.environment,
        "lighting": shot.lighting,
        "action": list(shot.action_beats),
        "camera": shot.camera_plan.to_dict(),
        "audio": audio,
        "handoff_in": _handoff(
            metadata,
            "handoff_in",
            "opening_blocking",
            fallback=prior_handoff_out,
        ),
        "handoff_out": _handoff(
            metadata,
            "handoff_out",
            "closing_blocking",
            fallback=shot.ending_beat,
        ),
        "timed_cues": deepcopy(metadata.get("timed_cues", [])),
    }

    # These identifiers remain useful only when an owning workflow already
    # authored them.  The template never derives or guesses either record.
    for key in ("reference_anchor_ids", "asset_lineage"):
        if key in metadata:
            row[key] = deepcopy(metadata[key])

    if shot.skill_type == "music_video":
        authored_music = {
            key: deepcopy(metadata[key])
            for key in ("clip_start", "clip_end", "bpm", "beat_count", "section")
            if key in metadata
        }
        if authored_music:
            row["music_metadata"] = authored_music

    return row


def build_h3_shot_table_template(plan: ProductionPlan) -> dict[str, Any]:
    """Project one canonical row per shot without mutating ``plan``."""
    rows: list[dict[str, Any]] = []
    cursor = Decimal("0")
    prior_handoff_out: Any = None
    prior_shot: ShotPlan | None = None
    for shot in plan.shots:
        start, end = _shot_range(shot, cursor)
        inherited_handoff = (
            prior_handoff_out
            if _inherits_prior_handoff(prior_shot, shot)
            else None
        )
        row = _shot_row(shot, start, end, inherited_handoff)
        rows.append(row)
        cursor = end
        prior_handoff_out = deepcopy(row["handoff_out"])
        prior_shot = shot

    return {
        "type": "minimax_h3_shot_table",
        "version": 1,
        "surface": "api_persisted_plan",
        "authority": "advisory",
        "provenance": {
            "source": "MiniMax-AI/MiniMax-H3 skills",
            "revision": _OFFICIAL_H3_SKILLS_REVISION,
            "adaptation": "maestro_native",
        },
        "fallback_policy": {
            "latest_approved_asset_fallback": "explicit_only",
            "reuse_exact_reference_anchors_first": True,
            "preserve_authored_dialogue_and_audio": True,
            "retake_scope": "shot",
        },
        "shots": rows,
        "qc_checklist": [
            {"check": check, "status": "pending"}
            for check in _PENDING_QC_CHECKS
        ],
    }
