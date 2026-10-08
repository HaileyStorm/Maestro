"""Exact authored-scene assembly contracts for verified H3 physical children."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re


def _video_artifact(unit: dict) -> dict:
    videos = [item for item in unit.get("artifacts") or []
              if isinstance(item, dict) and os.path.splitext(str(item.get("basename") or ""))[1].lower()
              in {".mp4", ".webm", ".mkv", ".mov"}]
    if len(videos) != 1:
        raise ValueError("H3 scene child has no unique verified video")
    item = videos[0]
    if (os.path.basename(str(item.get("basename") or "")) != item.get("basename")
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or "")) is None
            or not unit.get("unit_id")):
        raise ValueError("H3 scene child identity is incomplete")
    return item


def scene_dependency_settings_match(settings: dict, units: list[dict]) -> bool:
    """Recheck the ordered producer geometry without guessing from filenames."""
    try:
        if (not units or type(settings.get("assembly_version")) is not int or settings["assembly_version"] != 1
                or type(settings.get("scene_index")) is not int or settings["scene_index"] < 0
                or type(settings.get("fps")) is not int or settings["fps"] != 24
                or re.fullmatch(r"[0-9a-f]{64}", str(settings.get("plan_sha256") or "")) is None
                or type(settings.get("published_start_frame")) is not int or settings["published_start_frame"] < 0
                or type(settings.get("published_frames")) is not int):
            return False
        indices = [unit["index"] for unit in units]
        if (any(unit.get("kind") != "h3_segment" or unit.get("state") != "completed" for unit in units)
                or any(type(index) is not int or index < 0 for index in indices)
                or any(type(unit.get("variant")) is not int or unit["variant"] < 0 for unit in units)
                or indices != list(range(indices[0], indices[0] + len(indices)))
                or len({unit.get("variant") for unit in units}) != 1):
            return False
        geometry = [unit["settings"] for unit in units]
        published = [item["published_frames"] for item in geometry]
        generated = [item["generated_frames"] for item in geometry]
        trims = [item["trim_tail_frames"] for item in geometry]
        discards = [item["discard_prefix_frames"] for item in geometry]
        if any(type(x) is not int for group in (published, generated, trims, discards) for x in group):
            return False
        if any(p <= 0 or t < 0 or g - t != p or d not in {0, 17}
               for p, g, t, d in zip(published, generated, trims, discards)):
            return False
        origin = settings["audio_start_sec"]
        if type(origin) not in {int, float} or not math.isfinite(origin) or origin < 0:
            return False
        return (settings.get("physical_indices") == indices
                and settings.get("component_hashes") == [_video_artifact(unit)["sha256"] for unit in units]
                and settings.get("clip_start_frames") == discards
                and settings.get("clip_tail_frames") == trims
                and settings.get("clip_published_frames") == published
                and settings.get("clip_generated_frames") == generated
                and settings.get("published_frames") == sum(published))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def director_h3_scene_specs(plan: dict, units: dict[int, dict], *, audio_start_sec: float) -> list[dict]:
    """Return only complete scenes whose sealed child geometry matches the plan.

    Recovery segment geometry describes new frames. Native files additionally
    contain 17 returned history frames; concat discards those once. Tail trims
    have already happened before segment publication.
    """
    owners = plan.get("segment_source_indices")
    generated = plan.get("clip_frames")
    published = plan.get("clip_published_frames")
    trims = plan.get("clip_trim_tail_frames")
    models = plan.get("segment_models")
    count = plan.get("clip_count")
    if (type(count) is not int or count <= 0
            or any(not isinstance(group, list) or len(group) != count
                   for group in (owners, generated, published, trims, models))
            or any(type(owner) is not int or owner < 0 for owner in owners)
            or owners != sorted(owners) or set(owners) != set(range(max(owners) + 1))):
        raise ValueError("H3 scene assembly ownership is incomplete")
    if (any(type(value) is not int or value <= 0 for group in (generated, published) for value in group)
            or any(type(value) is not int or value < 0 for value in trims)
            or any(not isinstance(model, dict) or type(model.get("discard_frames", 0)) is not int
                   or model.get("discard_frames", 0) not in {0, 17} for model in models)):
        raise ValueError("H3 scene assembly geometry is invalid")
    try:
        origin = float(audio_start_sec) if type(audio_start_sec) in {int, float} else float("nan")
    except OverflowError:
        origin = float("nan")
    if not math.isfinite(origin) or origin < 0:
        raise ValueError("H3 scene soundtrack origin is invalid")
    specs = []
    cursor = 0
    for owner in range(max(owners) + 1):
        indices = [index for index, scene in enumerate(owners) if scene == owner]
        # Future-segment peak replanning must not duplicate an already sealed
        # scene. Bind this scene's portion of the plan and exact source clock.
        scene_plan = {"version": 1, "scene_index": owner, "physical_indices": indices,
                      "generated_frames": [generated[index] for index in indices],
                      "published_frames": [published[index] for index in indices],
                      "trim_tail_frames": [trims[index] for index in indices],
                      "discard_prefix_frames": [models[index].get("discard_frames", 0) for index in indices],
                      "published_start_frame": cursor, "audio_start_sec": origin + cursor / 24.0}
        plan_sha = hashlib.sha256(json.dumps(scene_plan, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False).encode()).hexdigest()
        members = [units.get(index) for index in indices]
        if all(isinstance(unit, dict) for unit in members):
            for index, unit in zip(indices, members):
                if (unit.get("index") != index
                        or (unit.get("settings") or {}).get("generated_frames") != generated[index]
                        or (unit.get("settings") or {}).get("published_frames") != published[index]
                        or (unit.get("settings") or {}).get("trim_tail_frames") != trims[index]
                        or (unit.get("settings") or {}).get("discard_prefix_frames")
                            != int(models[index].get("discard_frames") or 0)):
                    raise ValueError("H3 scene child differs from committed geometry")
            settings = {
                "assembly_version": 1, "scene_index": owner, "fps": 24,
                "plan_sha256": plan_sha, "physical_indices": indices,
                "component_hashes": [_video_artifact(unit)["sha256"] for unit in members],
                "clip_start_frames": [int(models[index].get("discard_frames") or 0) for index in indices],
                "clip_tail_frames": [trims[index] for index in indices],
                "clip_generated_frames": [generated[index] for index in indices],
                "clip_published_frames": [published[index] for index in indices],
                "published_frames": sum(published[index] for index in indices),
                "published_start_frame": cursor,
                "audio_start_sec": origin + cursor / 24.0,
            }
            if not scene_dependency_settings_match(settings, members):
                raise ValueError("H3 scene dependencies are invalid")
            specs.append({"index": owner, "settings": settings,
                          "dependencies": [unit["unit_id"] for unit in members],
                          "component_names": [_video_artifact(unit)["basename"] for unit in members]})
        cursor += sum(published[index] for index in indices)
    return specs


def scene_receipt_matches(unit: dict) -> bool:
    """Bind the assembly receipt to its exact media and current audio policy."""
    from services.h3_audio_safety import DEFAULT_TARGET_DBTP, POLICY_VERSION
    try:
        settings = unit["settings"]
        media = unit["attestation"]["media"]
        peak = unit["attestation"]["h3_audio_true_peak"]
        if any(not isinstance(value, dict) for value in (settings, media, peak)):
            return False
        artifact = _video_artifact(unit)
        return (settings.get("h3_audio_true_peak_policy")
                    == {"policy_version": POLICY_VERSION, "target_dbtp": DEFAULT_TARGET_DBTP}
                and peak.get("verified") is True and peak.get("policy_version") == POLICY_VERSION
                and peak.get("target_dbtp") == DEFAULT_TARGET_DBTP
                and media.get("validation") == "valid" and media.get("fps") == 24
                and type(media.get("frame_count")) is int
                and media["frame_count"] == settings.get("published_frames")
                and media.get("artifact_sha256") == "sha256:" + artifact["sha256"]
                and media.get("artifact_size_bytes") == artifact.get("size"))
    except (KeyError, TypeError, ValueError):
        return False
