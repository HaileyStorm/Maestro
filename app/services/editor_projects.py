"""Project-scoped, non-destructive persistence for Maestro Editor timelines.

This is the first Editor slice. Media remains in its owning project; a saved
timeline contains references and edit decisions, never copies of source files.
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Optional

from services.editor_export import editor_clip_frames


EDITOR_SCHEMA_VERSION = 5
EDITOR_PROJECT_DIR = ".maestro_editor"
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MEDIA_EXTENSIONS = {
    ".aac", ".flac", ".gif", ".jpeg", ".jpg", ".m4a", ".mkv",
    ".mov", ".mp3", ".mp4", ".ogg", ".png", ".wav", ".webm",
    ".webp",
}
_VIDEO_EXTENSIONS = {".gif", ".mkv", ".mov", ".mp4", ".webm"}
_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
_IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png", ".webp"}
_EDITOR_TRANSITIONS = {"none", "dissolve", "fade_black"}
_EDITOR_AI_TOOLS = {
    "retake", "edit_anything", "recast", "repaint", "outpaint", "upscale",
    "film_grain", "revoice", "director_rerun",
}
_EDITOR_UPSCALE_METHODS = {
    "", "flashvsr2", "flashvsr3", "flashvsr4", "flashvsr2pass2",
    "flashvsr2pass4",
}
_EDITOR_FONT_FAMILIES = {
    "DejaVu Sans",
    "Arial",
    "Arial Black",
    "Georgia",
    "Times New Roman",
    "Verdana",
    "Trebuchet MS",
    "Courier New",
    "Impact",
}
_project_lock = threading.RLock()


class EditorProjectError(ValueError):
    """A project or asset was invalid or unsafe."""


def _finite_number(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _bounded_number(value: Any, default: float, low: float, high: float) -> float:
    return min(high, max(low, _finite_number(value, default)))


def _editor_font_family(value: Any) -> str:
    family = str(value or "Arial").strip()
    return family if family in _EDITOR_FONT_FAMILIES else "Arial"


def _safe_identifier(value: Any, *, fallback: Optional[str] = None) -> str:
    text = str(value or "").strip()
    if _PROJECT_ID_RE.fullmatch(text):
        return text
    if fallback is not None:
        return fallback
    raise EditorProjectError("Invalid Editor project identifier")


def _safe_workspace_name(value: Any) -> str:
    text = str(value or "default").strip() or "default"
    if text == "default":
        return text
    if (
        text in {".", ".."}
        or os.path.basename(text) != text
        or any(character in text for character in ("/", "\\", "\0"))
    ):
        raise EditorProjectError("Invalid workspace name")
    return text


def _is_under(path: str, root: str) -> bool:
    try:
        candidate = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        boundary = os.path.normcase(os.path.realpath(os.path.abspath(root)))
        return os.path.commonpath([candidate, boundary]) == boundary
    except (OSError, ValueError):
        return False


def _safe_join(root: str, *parts: str) -> Optional[str]:
    candidate = os.path.realpath(os.path.join(root, *parts))
    return candidate if _is_under(candidate, root) else None


def workspace_directory(save_root: str, workspace: str) -> str:
    workspace = _safe_workspace_name(workspace)
    root = os.path.realpath(os.path.abspath(save_root))
    candidate = root if workspace == "default" else os.path.join(root, workspace)
    if os.path.islink(candidate) or not _is_under(candidate, root):
        raise EditorProjectError("Editor workspace is outside the output root")
    return candidate


def editor_project_directory(save_root: str, workspace: str) -> str:
    directory = os.path.join(workspace_directory(save_root, workspace), EDITOR_PROJECT_DIR)
    if os.path.islink(directory):
        raise EditorProjectError("Editor project directory is a link")
    return directory


def _project_path(save_root: str, workspace: str, project_id: str) -> str:
    safe_id = _safe_identifier(project_id)
    path = os.path.join(editor_project_directory(save_root, workspace), f"{safe_id}.json")
    if os.path.islink(path):
        raise EditorProjectError("Editor project file is a link")
    return path


def _atomic_write_json(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(path),
            prefix=".editor-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.isfile(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


@contextmanager
def _serialized_editor_save(path: str):
    """Keep revision checks and replacement atomic across server processes."""
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.path.islink(directory):
        raise EditorProjectError("Editor project directory is a link")
    lock_path = os.path.join(directory, ".editor.lock")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(lock_path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(opened, current)
            or opened.st_nlink != 1
        ):
            raise EditorProjectError("Editor project lock is unavailable")
        if opened.st_size < 1:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                    break
                except OSError as error:
                    # LK_LOCK waits only about ten seconds per call. Keep
                    # waiting for a valid peer save, as flock does on POSIX.
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            if os.path.islink(directory) or not os.path.samestat(
                os.fstat(descriptor), os.lstat(lock_path)
            ):
                raise EditorProjectError("Editor project lock changed")
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def create_editor_project(
    *,
    name: str = "Untitled project",
    workspace: str = "default",
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
) -> dict[str, Any]:
    now = time.time()
    project_id = uuid.uuid4().hex[:16]
    return {
        "schema_version": EDITOR_SCHEMA_VERSION,
        "id": project_id,
        "name": (str(name or "Untitled project").strip() or "Untitled project")[:120],
        "workspace": _safe_workspace_name(workspace),
        "created_at": now,
        "updated_at": now,
        "canvas": {
            "width": int(min(7680, max(64, width))),
            "height": int(min(4320, max(64, height))),
            "fps": _bounded_number(fps, 30.0, 1.0, 120.0),
            "background": "#000000",
        },
        "assets": {},
        "markers": [],
        "tracks": [
            {
                "id": "video-main",
                "name": "Main video",
                "type": "video",
                "z_index": 0,
                "muted": False,
                "locked": False,
                "items": [],
            },
            {
                "id": "audio-main",
                "name": "Audio",
                "type": "audio",
                "z_index": 0,
                "muted": False,
                "locked": False,
                "items": [],
            },
            {
                "id": "titles-main",
                "name": "Titles",
                "type": "text",
                "z_index": 10,
                "muted": False,
                "locked": False,
                "items": [],
            },
        ],
        "export": {
            "quality": "high",
            "codec": "h264",
            "encoder": "auto",
            "include_audio": True,
            "resolution": "canvas",
            "frame_rate": "project",
            "filename": "",
            "spatial_upsampling": "",
            "film_grain_intensity": 0.0,
            "film_grain_saturation": 0.5,
        },
        "exports": [],
    }


def create_output_video_timeline(
    *, workspace: str, output_name: str, output_revision: str,
    media: Mapping[str, Any],
) -> dict[str, Any]:
    """Make a stable first cut for one server-verified Gallery video."""
    workspace = _safe_workspace_name(workspace)
    name = str(output_name or "")
    revision = str(output_revision or "")
    if (
        not name or name.startswith(".") or os.path.basename(name) != name or "\\" in name
        or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", revision)
        or media.get("type") != "video"
    ):
        raise EditorProjectError("Select an available project video")
    duration = _finite_number(media.get("duration"), 0.0)
    if duration < 1 / 240 or duration > 86400:
        raise EditorProjectError("Video duration is unavailable for editing")
    identity = hashlib.sha256(
        f"{workspace}\0{name}\0{revision}".encode("utf-8")
    ).hexdigest()[:32]
    project = create_editor_project(
        name=f"Cut of {name}", workspace=workspace,
        width=int(media.get("width") or 1920),
        height=int(media.get("height") or 1080),
        fps=float(media.get("fps") or 30),
    )
    project["canvas"]["width"] += project["canvas"]["width"] % 2
    project["canvas"]["height"] += project["canvas"]["height"] % 2
    project["id"] = identity
    project["assets"]["source-video"] = {
        "id": "source-video",
        "name": name,
        "type": "video",
        "origin": "output",
        "workspace": workspace,
        "output_id": name,
        "output_revision": revision,
        "private": bool(media.get("private", True)),
        "duration": duration,
        "width": int(media.get("width") or 0),
        "height": int(media.get("height") or 0),
        "fps": _finite_number(media.get("fps"), 0.0),
        "has_audio": bool(media.get("has_audio")),
    }
    project["opening_source"] = {key: project["assets"]["source-video"][key]
        for key in ("output_id", "output_revision", "workspace", "origin", "type")}
    project["tracks"][0]["items"] = [{
        "id": "source-clip", "asset_id": "source-video", "start": 0.0,
        "duration": duration, "source_in": 0.0, "speed": 1.0,
    }]
    return normalize_editor_project(project, workspace=workspace)


def apply_output_video_trim(
    current: Mapping[str, Any], proposed: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept a nonempty subset of existing clips, trims/order and bounded layers."""
    if (
        proposed.get("id") != current.get("id")
        or proposed.get("workspace") != current.get("workspace")
    ):
        raise EditorProjectError("Editor project belongs to a different workspace")
    if not isinstance(proposed.get("tracks"), list) or any(not isinstance(track, Mapping) for track in proposed["tracks"]):
        raise EditorProjectError("Select valid Editor tracks")
    try:
        originals = editor_sequence_clips(current)
        incoming_video = [track for track in proposed["tracks"] if track.get("id") == "video-main"]
        if len(incoming_video) != 1:
            raise ValueError("video track changed")
        proposed_items = incoming_video[0]["items"]
        if not isinstance(proposed_items, list) or not 1 <= len(proposed_items) <= len(originals):
            raise ValueError("keep at least one existing clip")
        by_id = {clip["id"]: (asset, clip) for asset, clip in originals}
        updated = copy.deepcopy(dict(current))
        # Legacy drafts keep their opening identity when the original clip leaves
        # the timeline. It is navigation metadata, never an export input.
        if "opening_source" not in updated:
            source = current["assets"]["source-video"]
            updated["opening_source"] = {key: source[key]
                for key in ("output_id", "output_revision", "workspace", "origin", "type")}
        track = next(track for track in updated["tracks"] if track["id"] == "video-main")
        items = []
        position = 0.0
        used = set()
        for incoming in proposed_items:
            # Retain the original single-cut API's id-less trim payload.
            clip_id = incoming.get("id", originals[0][1]["id"] if len(originals) == 1 else None)
            if clip_id in used or clip_id not in by_id:
                raise ValueError("clip identity changed")
            used.add(clip_id)
            asset, original = by_id[clip_id]
            start, length = incoming["source_in"], incoming["duration"]
            if (type(start) not in (int, float) or type(length) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(length)
                    or start < 0 or length < 1 / 240
                    or start + length > asset["duration"] + 1e-6):
                raise ValueError("invalid range")
            clip = copy.deepcopy(original)
            clip.update(source_in=float(start), duration=float(length), start=position)
            clip["take_states"][clip["asset_id"]]["source_in"] = float(start)
            items.append(clip)
            position += float(length)
        if position > 86400:
            raise ValueError("sequence too long")
        track["items"] = items
        retained_assets = {item.get("asset_id") for row in updated["tracks"] for item in row.get("items", [])}
        for asset, _ in originals:
            if asset["id"] not in retained_assets:
                updated["assets"].pop(asset["id"], None)
    except (KeyError, IndexError, StopIteration, TypeError, ValueError):
        raise EditorProjectError("Select a valid source range") from None
    incoming_titles = [track for track in proposed.get("tracks", []) if track.get("id") == "titles-main"]
    if incoming_titles:
        if len(incoming_titles) != 1:
            raise EditorProjectError("Select a valid text layer")
        titles = next(track for track in updated["tracks"] if track["id"] == "titles-main")
        plan = editor_text_layers({**updated, "tracks": [incoming_titles[0]]})
        titles["items"] = [_editor_title_item(item) for item in plan]
    incoming_audio = [track for track in proposed["tracks"] if track.get("id") == "audio-main"]
    if incoming_audio:
        original_bed = editor_audio_layer(current)
        candidate = editor_audio_layer({**updated, "tracks": incoming_audio})
        if candidate and (not original_bed or any(candidate[key] != original_bed[key] for key in ("id", "asset_id"))):
            raise EditorProjectError("Add audio from this project's Gallery")
        audio = next(track for track in updated["tracks"] if track["id"] == "audio-main")
        audio["items"] = [_editor_audio_item(candidate)] if candidate else []
        if original_bed and not candidate:
            del updated["assets"][original_bed["asset_id"]]
    incoming_images = [track for track in proposed["tracks"] if track.get("id") == "images-main"]
    if incoming_images:
        original = {item["id"]: item for item in editor_image_layers(current)}
        candidates = editor_image_layers({**updated, "tracks": incoming_images})
        if any(item["id"] not in original or item["asset_id"] != original[item["id"]]["asset_id"] for item in candidates):
            raise EditorProjectError("Add images from this project's Gallery")
        track = next((track for track in updated["tracks"] if track["id"] == "images-main"), None)
        if track is None:
            raise EditorProjectError("Add images from this project's Gallery")
        track["items"] = [_editor_image_item(item) for item in candidates]
        retained = {item["asset_id"] for item in candidates}
        for item in original.values():
            if item["asset_id"] not in retained:
                del updated["assets"][item["asset_id"]]
    return updated


def _editor_audio_item(plan: Mapping[str, Any]) -> dict[str, Any]:
    asset_id = plan["asset_id"]
    return {
        **dict(plan), "speed": 1.0, "opacity": 1.0,
        "fade_in": plan.get("fade_in", 0.0), "fade_out": plan.get("fade_out", 0.0),
        "transition_in": "none", "transition_out": "none",
        "take_asset_ids": [asset_id],
        "take_states": {asset_id: {"source_in": plan["source_in"], "speed": 1.0}},
        "transform": {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0},
        "fit": "contain", "disabled": False,
    }


def editor_audio_layer(project: Mapping[str, Any], *, require_fit: bool = False) -> dict | None:
    """One bounded audio layer; paths and source identity are server-owned."""
    try:
        tracks = [track for track in project["tracks"] if track.get("id") == "audio-main"]
        if not tracks:
            return None
        if len(tracks) != 1:
            raise ValueError()
        track = tracks[0]
        if (track.get("type") != "audio" or track.get("muted") or track.get("locked")
                or track.get("volume", 1.0) != 1.0 or track.get("z_index", 0) != 0):
            raise ValueError()
        items = track["items"]
        if not isinstance(items, list) or len(items) > 1:
            raise ValueError()
        if not items:
            return None
        item = items[0]
        plan = {key: item[key] for key in ("id", "asset_id", "start", "source_in", "duration", "volume", "muted")}
        plan.update({key: item.get(key, 0.0) for key in ("fade_in", "fade_out")})
        asset = project["assets"][plan["asset_id"]]
        if (not isinstance(plan["id"], str) or not _PROJECT_ID_RE.fullmatch(plan["id"])
                or asset.get("type") != "audio" or asset.get("origin") != "output"
                or asset.get("workspace") != project["workspace"] or not asset.get("has_audio")
                or type(plan["muted"]) is not bool):
            raise ValueError()
        for key in ("start", "source_in", "duration", "volume", "fade_in", "fade_out"):
            if type(plan[key]) not in (int, float) or not math.isfinite(plan[key]):
                raise ValueError()
        if (plan["start"] < 0 or plan["source_in"] < 0 or plan["duration"] < 1 / 240
                or not 0 <= plan["volume"] <= 1
                or any(not 0 <= plan[key] <= plan["duration"] for key in ("fade_in", "fade_out"))
                or plan["source_in"] + plan["duration"] > asset["duration"] + 1e-6
                or plan["start"] + plan["duration"] > 86400):
            raise ValueError()
        canonical = _editor_audio_item(plan)
        # Saved take state follows source trim; incoming edits may carry its old cache.
        if any(key not in canonical or (key != "take_states" and value != canonical[key]) for key, value in item.items()):
            raise ValueError()
        if require_fit:
            clips = next(track["items"] for track in project["tracks"] if track["id"] == "video-main")
            fps = project["canvas"]["fps"]
            end = sum(editor_clip_frames(clip["duration"], fps) for clip in clips) / fps if len(clips) > 1 else clips[0]["duration"]
            if plan["start"] + plan["duration"] > end + 1e-6:
                raise ValueError()
        return plan
    except (KeyError, StopIteration, TypeError, ValueError):
        raise EditorProjectError("Use one Gallery audio layer with a valid source trim, timeline start and volume from 0 to 100%. Each fade must be between zero and the trimmed audio duration. Its interval must end within the exported cut.") from None


def add_output_audio_layer(current: Mapping[str, Any], *, output_name: str,
                           output_revision: str, media: Mapping[str, Any]) -> dict[str, Any]:
    clips = editor_sequence_clips(current)
    if editor_audio_layer(current):
        raise EditorProjectError("Remove the current audio layer before adding another")
    if (not isinstance(output_name, str) or not output_name or output_name.startswith(".")
            or os.path.basename(output_name) != output_name or "\\" in output_name
            or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", output_revision)
            or media.get("type") != "audio" or not media.get("has_audio")):
        raise EditorProjectError("Select an available project audio file")
    duration = _finite_number(media.get("duration"), 0)
    if not 1 / 240 <= duration <= 86400:
        raise EditorProjectError("Audio duration is unavailable for editing")
    updated = copy.deepcopy(dict(current))
    asset_id = "source-audio"
    updated["assets"][asset_id] = {
        "id": asset_id, "name": output_name, "type": "audio", "origin": "output",
        "workspace": current["workspace"], "output_id": output_name, "output_revision": output_revision,
        "private": bool(media.get("private", True)), "duration": duration,
        "width": 0, "height": 0, "fps": 0.0, "has_audio": True,
    }
    fps = current["canvas"]["fps"]
    end = sum(editor_clip_frames(clip["duration"], fps) for _, clip in clips) / fps if len(clips) > 1 else clips[0][1]["duration"]
    plan = {"id": "audio-layer", "asset_id": asset_id, "source_in": 0.0, "start": 0.0,
            "duration": min(duration, end), "volume": 1.0, "muted": False}
    next(track for track in updated["tracks"] if track["id"] == "audio-main")["items"] = [_editor_audio_item(plan)]
    editor_sequence_clips(updated)
    return updated


def inspect_editor_still(path: str | os.PathLike[str]) -> dict:
    """Decode only bounded static rasters, with orientation and alpha retained."""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as image:
            if (image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1
                    or not 1 <= image.width <= 16384 or not 1 <= image.height <= 16384
                    or image.width * image.height > 32000000):
                raise ValueError()
            image.load()
            oriented = ImageOps.exif_transpose(image)
            return {"type": "image", "width": oriented.width, "height": oriented.height,
                    "duration": 0.0, "fps": 0.0, "has_audio": False}
    except (OSError, ValueError, Image.DecompressionBombError):
        raise EditorProjectError("Choose a static PNG, JPEG or WebP up to 32 megapixels and 16384 pixels per side. Export an animation as a still first.") from None


def _editor_image_item(plan: Mapping[str, Any]) -> dict:
    return {**dict(plan), "source_in": 0.0, "speed": 1.0, "volume": 1.0,
            "muted": False, "disabled": False, "fade_in": 0.0, "fade_out": 0.0,
            "transition_in": "none", "transition_out": "none", "fit": "contain",
            "take_asset_ids": [plan["asset_id"]],
            "take_states": {plan["asset_id"]: {"source_in": 0.0, "speed": 1.0}},
            "transform": {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0}}


def editor_image_layers(project: Mapping[str, Any], *, require_fit: bool = False) -> list[dict]:
    try:
        tracks = [track for track in project["tracks"] if track.get("id") == "images-main"]
        if not tracks:
            return []
        if len(tracks) != 1:
            raise ValueError()
        track = tracks[0]
        if (track.get("type") != "video" or track.get("muted") or track.get("locked")
                or track.get("volume", 1.0) != 1.0 or track.get("z_index", 5) != 5):
            raise ValueError()
        items = track["items"]
        if not isinstance(items, list) or len(items) > 8:
            raise ValueError()
        if not items:
            return []
        result, ids, asset_ids = [], set(), set()
        for item in items:
            plan = {key: item[key] for key in ("id", "asset_id", "start", "duration", "size", "opacity", "position")}
            asset = project["assets"][plan["asset_id"]]
            if (not isinstance(plan["id"], str) or not _PROJECT_ID_RE.fullmatch(plan["id"])
                    or asset.get("type") != "image" or asset.get("origin") != "output"
                    or asset.get("workspace") != project["workspace"] or asset.get("has_audio")
                    or not 1 <= asset["width"] <= 16384 or not 1 <= asset["height"] <= 16384
                    or asset["width"] * asset["height"] > 32000000
                    or plan["position"] not in {"top", "center", "bottom"}):
                raise ValueError()
            if any(type(plan[k]) not in (int, float) or not math.isfinite(plan[k]) for k in ("start", "duration", "size", "opacity")):
                raise ValueError()
            if (plan["start"] < 0 or plan["duration"] < 1 / 240 or plan["start"] + plan["duration"] > 86400
                    or not 0.1 <= plan["size"] <= 1 or not 0 <= plan["opacity"] <= 1):
                raise ValueError()
            canonical = _editor_image_item(plan)
            if any(key not in canonical or value != canonical[key] for key, value in item.items()):
                raise ValueError()
            if require_fit:
                clips = next(track["items"] for track in project["tracks"] if track["id"] == "video-main")
                fps = project["canvas"]["fps"] if len(clips) > 1 else (project["assets"][clips[0]["asset_id"]].get("fps") or project["canvas"]["fps"])
                end = sum(editor_clip_frames(clip["duration"], fps) for clip in clips) / fps if len(clips) > 1 else clips[0]["duration"]
                if plan["start"] + plan["duration"] > end + 1e-6 or plan["duration"] < 1 / max(1, fps) - 1e-9:
                    raise ValueError()
            if plan["id"] in ids or plan["asset_id"] in asset_ids:
                raise ValueError()
            ids.add(plan["id"])
            asset_ids.add(plan["asset_id"])
            result.append(plan)
        return result
    except (KeyError, StopIteration, TypeError, ValueError):
        raise EditorProjectError("Use up to eight Gallery still images, size 10–100%, opacity 0–100% and top, center or bottom placement. Each image must last at least one frame and end within the exported cut.") from None


def editor_image_layer(project: Mapping[str, Any], *, require_fit: bool = False) -> dict | None:
    """Compatibility reader for single-image callers; never drops extra rows."""
    plans = editor_image_layers(project, require_fit=require_fit)
    if len(plans) > 1:
        raise EditorProjectError("Read all image layers for this Editor draft")
    return plans[0] if plans else None


def add_output_image_layer(current: Mapping[str, Any], *, output_name: str,
                           output_revision: str, media: Mapping[str, Any]) -> dict:
    clips = editor_sequence_clips(current)
    if len(editor_image_layers(current)) >= 8:
        raise EditorProjectError("Use up to eight image layers")
    if (not isinstance(output_name, str) or not output_name or output_name.startswith(".")
            or os.path.basename(output_name) != output_name or "\\" in output_name
            or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", output_revision)
            or media.get("type") != "image" or media.get("has_audio")):
        raise EditorProjectError("Select an available project still image")
    updated = copy.deepcopy(dict(current))
    asset_id = "source-image" if "source-image" not in updated["assets"] else f"source-image-{uuid.uuid4().hex}"
    updated["assets"][asset_id] = {"id": asset_id, "name": output_name, "type": "image", "origin": "output",
        "workspace": current["workspace"], "output_id": output_name, "output_revision": output_revision,
        "private": bool(media.get("private", True)), "duration": 0.0, "fps": 0.0, "has_audio": False,
        "width": media.get("width"), "height": media.get("height")}
    fps = current["canvas"]["fps"]
    end = sum(editor_clip_frames(clip["duration"], fps) for _, clip in clips) / fps if len(clips) > 1 else clips[0][1]["duration"]
    plan = {"id": "image-layer" if asset_id == "source-image" else f"image-{uuid.uuid4().hex}", "asset_id": asset_id, "start": 0.0, "duration": end,
            "size": 0.25, "opacity": 1.0, "position": "center"}
    track = next((track for track in updated["tracks"] if track["id"] == "images-main"), None)
    if track is None:
        track = {"id": "images-main", "name": "Image layer", "type": "video", "z_index": 5,
                 "muted": False, "locked": False, "volume": 1.0, "items": []}
        updated["tracks"].append(track)
    track["items"].append(_editor_image_item(plan))
    editor_sequence_clips(updated)
    return updated


def _editor_title_item(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": plan["id"], "text": plan["text"], "start": float(plan["start"]),
        "duration": float(plan["duration"]), "position": plan["position"],
        "source_in": 0.0, "speed": 1.0, "volume": 1.0, "opacity": 1.0,
        "fade_in": 0.0, "fade_out": 0.0, "transition_in": "none", "transition_out": "none",
        "transform": {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0},
        "fit": "contain", "muted": False, "disabled": False,
        "style": {"x": 0.0, "y": 0.0, "font_family": "DejaVu Sans", "font_size": 64,
                  "color": "#ffffff", "background_color": "#000000",
                  "background_opacity": 0.6, "text_align": "center"},
    }


def editor_text_layers(project: Mapping[str, Any], *, require_fit: bool = False) -> list[dict]:
    """Compile only native timed titles. Unsupported effects never disappear."""
    try:
        tracks = [track for track in project["tracks"] if track.get("id") == "titles-main"]
        if not tracks:
            return []
        if len(tracks) != 1:
            raise ValueError()
        track = tracks[0]
        if (track.get("type") != "text" or track.get("muted") or track.get("locked")
                or track.get("volume", 1.0) != 1.0 or track.get("z_index", 10) != 10):
            raise ValueError()
        items = track["items"]
        if not isinstance(items, list) or len(items) > 8:
            raise ValueError()
        result, ids = [], set()
        for item in items:
            plan = {key: item[key] for key in ("id", "text", "start", "duration", "position")}
            text, start, length = plan["text"], plan["start"], plan["duration"]
            if (not isinstance(plan["id"], str) or not _PROJECT_ID_RE.fullmatch(plan["id"])
                    or plan["id"] in ids or not isinstance(text, str) or len(text) > 160
                    or text.count("\n") > 2 or any(ord(char) < 32 and char != "\n" for char in text)
                    or plan["position"] not in {"top", "center", "bottom"}
                    or type(start) not in (int, float) or type(length) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(length)
                    or start < 0 or length < 1 / 240 or start + length > 86400):
                raise ValueError()
            canonical = _editor_title_item(plan)
            if any(key not in canonical or value != canonical[key] for key, value in item.items()):
                raise ValueError()
            ids.add(plan["id"])
            result.append(plan)
        if require_fit:
            clips = next(track["items"] for track in project["tracks"] if track["id"] == "video-main")
            fps = project["canvas"]["fps"]
            title_fps = fps if len(clips) > 1 else project["assets"][clips[0]["asset_id"]].get("fps", fps) or fps
            end = (sum(editor_clip_frames(clip["duration"], fps) for clip in clips) / fps
                   if len(clips) > 1 else clips[0]["duration"])
            if any(item["start"] + item["duration"] > end + 1e-6
                   or item["duration"] < 1 / title_fps - 1e-9 for item in result):
                raise ValueError()
        return result
    except (KeyError, StopIteration, TypeError, ValueError):
        raise EditorProjectError("Use up to eight text layers, three lines and 160 characters each, with valid times and top, center or bottom placement. Text must last at least one video frame and end within the exported cut.") from None


def editor_sequence_clips(project: Mapping[str, Any]) -> list[tuple[dict, dict]]:
    """Validate the closed sequential-video capability, without dropping layers."""
    try:
        tracks = project["tracks"]
        main = [track for track in tracks if track["id"] == "video-main"]
        if len(main) != 1 or main[0].get("type") != "video":
            raise ValueError()
        if any(track.get("items") for track in tracks if track is not main[0] and track.get("id") not in {"titles-main", "audio-main", "images-main"}):
            raise ValueError()
        editor_text_layers(project)
        bed = editor_audio_layer(project)
        images = editor_image_layers(project)
        if main[0].get("muted") or main[0].get("locked"):
            raise ValueError()
        clips = main[0]["items"]
        assets = project["assets"]
        if not 1 <= len(clips) <= 8:
            raise ValueError()
        result, ids, asset_ids = [], set(), set()
        position = 0.0
        for clip in clips:
            asset_id, clip_id = clip["asset_id"], clip["id"]
            asset = assets[asset_id]
            start, length = clip["source_in"], clip["duration"]
            expected = {
                "id": clip_id, "asset_id": asset_id, "start": position,
                "source_in": start, "duration": length,
                "speed": 1.0, "volume": 1.0, "opacity": 1.0,
                "fade_in": 0.0, "fade_out": 0.0,
                "transition_in": "none", "transition_out": "none",
                "take_asset_ids": [asset_id],
                "take_states": {asset_id: {"source_in": start, "speed": 1.0}},
                "transform": {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0},
                "fit": "contain", "muted": False, "disabled": False,
            }
            if (clip != expected or clip_id in ids
                    or asset.get("type") != "video" or asset.get("origin") != "output"
                    or asset.get("workspace") != project["workspace"]
                    or type(start) not in (int, float) or type(length) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(length)
                    or start < 0 or length < 1 / 240
                    or start + length > asset["duration"] + 1e-6):
                raise ValueError()
            ids.add(clip_id); asset_ids.add(asset_id)
            position += length
            result.append((asset, clip))
        referenced_assets = asset_ids | {item["asset_id"] for item in images}
        if bed:
            referenced_assets.add(bed["asset_id"])
        if position > 86400 or set(assets) != referenced_assets:
            raise ValueError()
        return result
    except (KeyError, TypeError, ValueError):
        raise EditorProjectError("This Editor timeline needs supported sequential video cuts") from None


def append_output_video_clip(current: Mapping[str, Any], *, output_name: str,
                             output_revision: str, media: Mapping[str, Any]) -> dict[str, Any]:
    clips = editor_sequence_clips(current)
    if len(clips) >= 8:
        raise EditorProjectError("An Editor sequence supports up to eight clips")
    candidate = create_output_video_timeline(
        workspace=current["workspace"], output_name=output_name,
        output_revision=output_revision, media=media,
    )
    source = candidate["assets"]["source-video"]
    matches = [asset for asset in current["assets"].values() if asset.get("output_id") == output_name]
    if matches and (len(matches) != 1 or matches[0] != {**source, "id": matches[0]["id"]}):
        raise EditorProjectError("This video's source changed. Reopen the edit from Gallery")
    updated = copy.deepcopy(dict(current))
    # H.264/yuv420p needs even dimensions. Keep the first-source canvas,
    # rounding an odd edge up one pixel when it becomes a sequence.
    for dimension in ("width", "height"):
        updated["canvas"][dimension] += updated["canvas"][dimension] % 2
    token = uuid.uuid4().hex[:16]
    asset_id = matches[0]["id"] if matches else f"source-video-{token}"
    if not matches:
        updated["assets"][asset_id] = {**source, "id": asset_id}
    clip = candidate["tracks"][0]["items"][0]
    clip.update(id=f"source-clip-{token}", asset_id=asset_id,
                start=sum(item["duration"] for _, item in clips),
                take_asset_ids=[asset_id], take_states={asset_id: {"source_in": 0.0, "speed": 1.0}})
    next(track for track in updated["tracks"] if track["id"] == "video-main")["items"].append(clip)
    editor_sequence_clips(updated)
    return updated


def normalize_editor_project(project: Mapping[str, Any], *, workspace: str | None = None) -> dict[str, Any]:
    if not isinstance(project, Mapping):
        raise EditorProjectError("Editor project must be an object")
    normalized = copy.deepcopy(dict(project))
    if workspace is not None and _safe_workspace_name(project.get("workspace")) != _safe_workspace_name(workspace):
        raise EditorProjectError("Editor project belongs to a different workspace")
    normalized["schema_version"] = EDITOR_SCHEMA_VERSION
    normalized["id"] = _safe_identifier(normalized.get("id"), fallback=uuid.uuid4().hex[:16])
    normalized["name"] = (
        str(normalized.get("name") or "Untitled project").strip() or "Untitled project"
    )[:120]
    normalized["workspace"] = _safe_workspace_name(workspace or normalized.get("workspace"))
    try:
        normalized["revision"] = max(0, int(normalized.get("revision", 0)))
    except (TypeError, ValueError):
        raise EditorProjectError("Invalid Editor project revision") from None
    now = time.time()
    normalized["created_at"] = _finite_number(normalized.get("created_at"), now)
    # Normalization also runs for read/list operations. Preserve the stored
    # modification time here; save_editor_project is the only operation that
    # should advance it. Otherwise merely opening the Editor makes every
    # project appear newly modified and destroys useful recency ordering.
    normalized["updated_at"] = _finite_number(normalized.get("updated_at"), now)

    canvas = normalized.get("canvas") if isinstance(normalized.get("canvas"), Mapping) else {}
    background = str(canvas.get("background") or "#000000")
    normalized["canvas"] = {
        "width": int(_bounded_number(canvas.get("width"), 1920, 64, 7680)),
        "height": int(_bounded_number(canvas.get("height"), 1080, 64, 4320)),
        "fps": _bounded_number(canvas.get("fps"), 30.0, 1.0, 120.0),
        "background": background if _HEX_COLOR_RE.fullmatch(background) else "#000000",
    }

    raw_assets = normalized.get("assets")
    assets: dict[str, dict[str, Any]] = {}
    if isinstance(raw_assets, Mapping):
        for key, value in list(raw_assets.items())[:2000]:
            if not isinstance(value, Mapping):
                continue
            asset_id = _safe_identifier(value.get("id") or key, fallback=uuid.uuid4().hex[:16])
            media_type = str(value.get("type") or "video").lower()
            if media_type not in {"video", "image", "audio"}:
                continue
            origin = str(value.get("origin") or "output").lower()
            if origin not in {"output", "upload", "project"}:
                origin = "output"
            asset_workspace = _safe_workspace_name(
                value.get("workspace") or normalized["workspace"]
            )
            if asset_workspace != normalized["workspace"]:
                raise EditorProjectError("Editor asset belongs to a different workspace")
            output_id = str(value.get("output_id") or "").strip()
            if output_id and (
                len(output_id) > 255 or os.path.basename(output_id) != output_id
                or "\\" in output_id or "\0" in output_id
            ):
                raise EditorProjectError("Invalid Editor source output")
            output_revision = str(value.get("output_revision") or "").strip()
            if output_revision and not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", output_revision):
                raise EditorProjectError("Invalid Editor source revision")
            normalized_asset = {
                "id": asset_id,
                "name": os.path.basename(str(value.get("name") or "asset"))[:255],
                "type": media_type,
                "origin": origin,
                "workspace": normalized["workspace"],
                "output_id": output_id,
                "output_revision": output_revision,
                "private": bool(value.get("private", True)),
                "duration": max(0.0, _finite_number(value.get("duration"), 0.0)),
                "width": max(0, int(_finite_number(value.get("width"), 0))),
                "height": max(0, int(_finite_number(value.get("height"), 0))),
                "fps": max(0.0, _finite_number(value.get("fps"), 0.0)),
                "has_audio": bool(value.get("has_audio", media_type == "audio")),
            }
            # No client-supplied host path, URL, or preview state is persisted.
            # The route will resolve output_id against its authorized project.
            assets[asset_id] = normalized_asset
    normalized["assets"] = assets

    raw_tracks = normalized.get("tracks")
    tracks: list[dict[str, Any]] = []
    if isinstance(raw_tracks, list):
        for raw_track in raw_tracks[:100]:
            if not isinstance(raw_track, Mapping):
                continue
            track_type = str(raw_track.get("type") or "video").lower()
            if track_type not in {"video", "audio", "text"}:
                continue
            track_id = _safe_identifier(raw_track.get("id"), fallback=uuid.uuid4().hex[:16])
            items: list[dict[str, Any]] = []
            raw_items = raw_track.get("items")
            if isinstance(raw_items, list):
                for raw_item in raw_items[:5000]:
                    if not isinstance(raw_item, Mapping):
                        continue
                    item = dict(raw_item)
                    item["id"] = _safe_identifier(item.get("id"), fallback=uuid.uuid4().hex[:16])
                    item["start"] = max(0.0, _finite_number(item.get("start"), 0.0))
                    item["duration"] = _bounded_number(item.get("duration"), 1.0, 1 / 240, 86400.0)
                    item["source_in"] = max(0.0, _finite_number(item.get("source_in"), 0.0))
                    item["speed"] = _bounded_number(item.get("speed"), 1.0, 0.1, 8.0)
                    item["volume"] = _bounded_number(item.get("volume"), 1.0, 0.0, 4.0)
                    item["opacity"] = _bounded_number(item.get("opacity"), 1.0, 0.0, 1.0)
                    asset_id = str(item.get("asset_id") or "")
                    if track_type != "text" and asset_id not in assets:
                        continue
                    asset = assets.get(asset_id)
                    if asset and asset.get("type") != "image":
                        asset_duration = max(0.0, _finite_number(asset.get("duration"), 0.0))
                        if asset_duration > 0:
                            minimum_timeline_duration = 1 / 240
                            minimum_source_duration = minimum_timeline_duration * item["speed"]
                            item["source_in"] = min(
                                item["source_in"],
                                max(0.0, asset_duration - minimum_source_duration),
                            )
                            available_duration = max(
                                minimum_timeline_duration,
                                (asset_duration - item["source_in"]) / item["speed"],
                            )
                            item["duration"] = min(item["duration"], available_duration)
                    item["fade_in"] = _bounded_number(
                        item.get("fade_in"), 0.0, 0.0, item["duration"]
                    )
                    item["fade_out"] = _bounded_number(
                        item.get("fade_out"), 0.0, 0.0, item["duration"]
                    )
                    transition_in = str(item.get("transition_in") or "none")
                    transition_out = str(item.get("transition_out") or "none")
                    item["transition_in"] = (
                        transition_in if transition_in in _EDITOR_TRANSITIONS else "none"
                    )
                    item["transition_out"] = (
                        transition_out if transition_out in _EDITOR_TRANSITIONS else "none"
                    )
                    link_group_id = str(item.get("link_group_id") or "").strip()
                    if link_group_id and _PROJECT_ID_RE.fullmatch(link_group_id):
                        item["link_group_id"] = link_group_id
                    else:
                        item.pop("link_group_id", None)
                    raw_take_ids = item.get("take_asset_ids")
                    take_ids: list[str] = []
                    if isinstance(raw_take_ids, list):
                        for raw_take_id in raw_take_ids[:100]:
                            take_id = str(raw_take_id or "")
                            if take_id in assets and take_id not in take_ids:
                                take_ids.append(take_id)
                    if asset_id and asset_id in assets and asset_id not in take_ids:
                        take_ids.insert(0, asset_id)
                    if take_ids:
                        item["take_asset_ids"] = take_ids
                    else:
                        item.pop("take_asset_ids", None)
                    raw_take_states = item.get("take_states")
                    take_states: dict[str, dict[str, float]] = {}
                    if isinstance(raw_take_states, Mapping):
                        for take_id in take_ids:
                            raw_take_state = raw_take_states.get(take_id)
                            if not isinstance(raw_take_state, Mapping):
                                continue
                            take_asset = assets.get(take_id)
                            take_speed = _bounded_number(
                                raw_take_state.get("speed"), 1.0, 0.1, 8.0
                            )
                            take_source_in = max(
                                0.0, _finite_number(raw_take_state.get("source_in"), 0.0)
                            )
                            take_duration = max(
                                0.0, _finite_number(
                                    take_asset.get("duration") if isinstance(take_asset, Mapping) else 0.0,
                                    0.0,
                                )
                            )
                            if take_duration > 0:
                                take_source_in = min(
                                    take_source_in,
                                    max(0.0, take_duration - (1 / 240) * take_speed),
                                )
                            take_states[take_id] = {
                                "source_in": take_source_in,
                                "speed": take_speed,
                            }
                    # The active timeline item's values are authoritative. A stale
                    # cached take state must never move the current edit on reload.
                    if asset_id and asset_id in take_ids:
                        take_states[asset_id] = {
                            "source_in": item["source_in"],
                            "speed": item["speed"],
                        }
                    if take_states:
                        item["take_states"] = take_states
                    else:
                        item.pop("take_states", None)
                    raw_history = item.get("ai_history")
                    ai_history: list[dict[str, Any]] = []
                    if isinstance(raw_history, list):
                        for raw_entry in raw_history[-100:]:
                            if not isinstance(raw_entry, Mapping):
                                continue
                            history_asset_id = str(raw_entry.get("asset_id") or "")
                            history_tool = str(raw_entry.get("tool") or "")
                            if history_asset_id not in assets or history_tool not in _EDITOR_AI_TOOLS:
                                continue
                            ai_history.append({
                                "id": _safe_identifier(
                                    raw_entry.get("id"), fallback=uuid.uuid4().hex[:16]
                                ),
                                "tool": history_tool,
                                "asset_id": history_asset_id,
                                "created_at": _finite_number(raw_entry.get("created_at"), now),
                            })
                    if ai_history:
                        item["ai_history"] = ai_history
                    else:
                        item.pop("ai_history", None)
                    raw_director = item.get("director")
                    if isinstance(raw_director, Mapping):
                        pipeline_id = str(raw_director.get("pipeline_id") or "").strip()
                        try:
                            clip_index = int(raw_director.get("clip_index"))
                        except (TypeError, ValueError):
                            clip_index = -1
                        if _PROJECT_ID_RE.fullmatch(pipeline_id) and clip_index >= 0:
                            director_workspace = _safe_workspace_name(
                                raw_director.get("workspace") or normalized["workspace"]
                            )
                            if director_workspace != normalized["workspace"]:
                                raise EditorProjectError("Director clip belongs to a different workspace")
                            window_prompts = raw_director.get("window_prompts")
                            item["director"] = {
                                "pipeline_id": pipeline_id,
                                "clip_index": clip_index,
                                "pipeline_type": str(
                                    raw_director.get("pipeline_type") or ""
                                )[:80],
                                "workspace": director_workspace,
                                "video_prompt": str(
                                    raw_director.get("video_prompt") or ""
                                )[:200000],
                                "window_prompts": [
                                    str(prompt)[:200000]
                                    for prompt in window_prompts[:100]
                                    if str(prompt).strip()
                                ] if isinstance(window_prompts, list) else [],
                            }
                        else:
                            item.pop("director", None)
                    else:
                        item.pop("director", None)
                    transform = item.get("transform") if isinstance(item.get("transform"), Mapping) else {}
                    item["transform"] = {
                        "x": _finite_number(transform.get("x"), 0.0),
                        "y": _finite_number(transform.get("y"), 0.0),
                        "scale": _bounded_number(transform.get("scale"), 1.0, 0.05, 4.0),
                        "rotation": _bounded_number(transform.get("rotation"), 0.0, -360.0, 360.0),
                    }
                    item["fit"] = "cover" if str(item.get("fit")) == "cover" else "contain"
                    item["muted"] = bool(item.get("muted", False))
                    item["disabled"] = bool(item.get("disabled", False))
                    if track_type == "text":
                        style = item.get("style") if isinstance(item.get("style"), Mapping) else {}
                        color = str(style.get("color") or "#ffffff")
                        background_color = str(style.get("background_color") or "#000000")
                        text_align = str(style.get("text_align") or "center")
                        item["style"] = {
                            "x": _finite_number(style.get("x"), 0.0),
                            "y": _finite_number(style.get("y"), 0.0),
                            "font_family": _editor_font_family(style.get("font_family")),
                            "font_size": int(_bounded_number(style.get("font_size"), 64, 8, 400)),
                            "color": color if _HEX_COLOR_RE.fullmatch(color) else "#ffffff",
                            "background_color": background_color
                            if _HEX_COLOR_RE.fullmatch(background_color) else "#000000",
                            "background_opacity": _bounded_number(
                                style.get("background_opacity"), 0.32, 0.0, 1.0
                            ),
                            "text_align": text_align
                            if text_align in {"left", "center", "right"} else "center",
                        }
                    items.append(item)
            # A timeline track is a single editing lane: clips may meet at an
            # edit point but cannot occupy the same time range. Normalize old
            # or externally edited projects by retaining their order and
            # moving any overlap to the preceding clip's end.
            # Native titles are overlapping layers; retain their stacking order.
            if (track_type == "text" and track_id == "titles-main") or (track_type == "video" and track_id == "images-main"):
                tracks.append({**dict(raw_track), "id": track_id, "type": track_type, "items": items})
                continue
            items.sort(key=lambda item: (item["start"], item["id"]))
            previous_end = 0.0
            for item in items:
                if item["start"] < previous_end - 1e-9:
                    item["start"] = previous_end
                previous_end = item["start"] + item["duration"]
            tracks.append({
                **dict(raw_track),
                "id": track_id,
                "name": str(raw_track.get("name") or track_type.title())[:80],
                "type": track_type,
                "z_index": int(_finite_number(raw_track.get("z_index"), 0)),
                "muted": bool(raw_track.get("muted", False)),
                "locked": bool(raw_track.get("locked", False)),
                "volume": _bounded_number(raw_track.get("volume"), 1.0, 0.0, 4.0),
                "items": items,
            })
    if not tracks:
        tracks = create_editor_project(workspace=normalized["workspace"])["tracks"]
    normalized["tracks"] = tracks
    raw_markers = normalized.get("markers")
    markers: list[dict[str, Any]] = []
    if isinstance(raw_markers, list):
        for raw_marker in raw_markers[:1000]:
            if not isinstance(raw_marker, Mapping):
                continue
            color = str(raw_marker.get("color") or "#f59e0b")
            markers.append({
                "id": _safe_identifier(
                    raw_marker.get("id"), fallback=uuid.uuid4().hex[:16]
                ),
                "time": max(0.0, _finite_number(raw_marker.get("time"), 0.0)),
                "label": (str(raw_marker.get("label") or "Marker").strip() or "Marker")[:120],
                "color": color if _HEX_COLOR_RE.fullmatch(color) else "#f59e0b",
            })
    markers.sort(key=lambda marker: (marker["time"], marker["id"]))
    normalized["markers"] = markers
    export = normalized.get("export") if isinstance(normalized.get("export"), Mapping) else {}
    quality = str(export.get("quality") or "high").lower()
    codec = str(export.get("codec") or "h264").lower()
    encoder = str(export.get("encoder") or "auto").lower()
    resolution = str(export.get("resolution") or "canvas").lower()
    spatial_upsampling = str(export.get("spatial_upsampling") or "").lower()
    raw_frame_rate = export.get("frame_rate", "project")
    try:
        numeric_frame_rate = int(raw_frame_rate)
    except (TypeError, ValueError):
        numeric_frame_rate = 0
    normalized["export"] = {
        "quality": quality if quality in {"draft", "balanced", "high"} else "high",
        "codec": codec if codec in {"h264", "h265"} else "h264",
        "encoder": encoder
        if encoder in {"auto", "software", "nvidia", "intel", "apple"}
        else "auto",
        "include_audio": bool(export.get("include_audio", True)),
        "resolution": resolution
        if resolution in {"canvas", "2160p", "1080p", "720p", "480p"}
        else "canvas",
        "frame_rate": numeric_frame_rate
        if numeric_frame_rate in {24, 30, 60}
        else "project",
        "filename": str(export.get("filename") or "").strip()[:120],
        "spatial_upsampling": spatial_upsampling
        if spatial_upsampling in _EDITOR_UPSCALE_METHODS
        else "",
        "film_grain_intensity": max(
            0.0,
            min(1.0, _finite_number(export.get("film_grain_intensity"), 0.0)),
        ),
        "film_grain_saturation": max(
            0.0,
            min(1.0, _finite_number(export.get("film_grain_saturation"), 0.5)),
        ),
    }
    raw_exports = normalized.get("exports")
    export_history: list[dict[str, Any]] = []
    if isinstance(raw_exports, list):
        for raw_record in raw_exports[-50:]:
            if not isinstance(raw_record, Mapping):
                continue
            filename = os.path.basename(str(raw_record.get("filename") or ""))[:255]
            if not filename:
                continue
            record_codec = str(raw_record.get("codec") or "h264").lower()
            record_quality = str(raw_record.get("quality") or "high").lower()
            record_encoder = str(raw_record.get("encoder") or "auto").lower()
            record_upscale = str(
                raw_record.get("spatial_upsampling") or ""
            ).lower()
            export_history.append({
                "id": _safe_identifier(
                    raw_record.get("id"), fallback=uuid.uuid4().hex[:16]
                ),
                "filename": filename,
                "workspace": normalized["workspace"],
                "created_at": _finite_number(raw_record.get("created_at"), now),
                "duration": max(0.0, _finite_number(raw_record.get("duration"), 0.0)),
                "width": max(0, int(_finite_number(raw_record.get("width"), 0))),
                "height": max(0, int(_finite_number(raw_record.get("height"), 0))),
                "fps": max(0.0, _finite_number(raw_record.get("fps"), 0.0)),
                "codec": record_codec if record_codec in {"h264", "h265"} else "h264",
                "quality": record_quality
                if record_quality in {"draft", "balanced", "high"}
                else "high",
                "encoder": record_encoder
                if record_encoder in {"auto", "software", "nvidia", "intel", "apple"}
                else "auto",
                "spatial_upsampling": record_upscale
                if record_upscale in _EDITOR_UPSCALE_METHODS
                else "",
                "film_grain_intensity": max(
                    0.0,
                    min(
                        1.0,
                        _finite_number(raw_record.get("film_grain_intensity"), 0.0),
                    ),
                ),
                "film_grain_saturation": max(
                    0.0,
                    min(
                        1.0,
                        _finite_number(raw_record.get("film_grain_saturation"), 0.5),
                    ),
                ),
            })
    export_history.sort(key=lambda record: record["created_at"], reverse=True)
    normalized["exports"] = export_history[:50]
    return normalized


def save_editor_project(
    save_root: str, workspace: str, project: Mapping[str, Any], *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_editor_project(project, workspace=workspace)
    path = _project_path(save_root, normalized["workspace"], normalized["id"])
    with _project_lock, _serialized_editor_save(path):
        _project_path(save_root, workspace, normalized["id"])
        if os.path.isfile(path):
            if expected_revision is None:
                raise EditorProjectError("Editor project changed; reload before saving")
            current = load_editor_project(save_root, workspace, normalized["id"])
            if current["revision"] != expected_revision:
                raise EditorProjectError("Editor project changed; reload before saving")
            normalized["revision"] = current["revision"] + 1
            normalized["created_at"] = current["created_at"]
        else:
            if expected_revision not in (None, 0):
                raise EditorProjectError("Editor project changed; reload before saving")
            normalized["revision"] = 1
        normalized["updated_at"] = time.time()
        _atomic_write_json(path, normalized)
    return normalized


def load_editor_project(save_root: str, workspace: str, project_id: str) -> dict[str, Any]:
    path = _project_path(save_root, workspace, project_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(project_id)
    with _project_lock, open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return normalize_editor_project(payload, workspace=workspace)


def list_editor_projects(save_root: str, workspace: str) -> list[dict[str, Any]]:
    directory = editor_project_directory(save_root, workspace)
    if not os.path.isdir(directory):
        return []
    summaries: list[dict[str, Any]] = []
    with _project_lock:
        for entry in os.scandir(directory):
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
                continue
            try:
                with open(entry.path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                project = normalize_editor_project(payload, workspace=workspace)
                summaries.append({
                    "id": project["id"],
                    "name": project["name"],
                    "workspace": project["workspace"],
                    "created_at": project["created_at"],
                    "updated_at": project["updated_at"],
                    "duration": editor_project_duration(project),
                    "asset_count": len(project.get("assets") or {}),
                })
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    summaries.sort(key=lambda item: item["updated_at"], reverse=True)
    return summaries


def delete_editor_project(save_root: str, workspace: str, project_id: str) -> bool:
    path = _project_path(save_root, workspace, project_id)
    with _project_lock:
        if not os.path.isfile(path):
            return False
        os.remove(path)
    return True


def editor_project_duration(project: Mapping[str, Any]) -> float:
    duration = 0.0
    for track in project.get("tracks") or []:
        if not isinstance(track, Mapping):
            continue
        for item in track.get("items") or []:
            if not isinstance(item, Mapping) or item.get("disabled"):
                continue
            duration = max(
                duration,
                max(0.0, _finite_number(item.get("start"), 0.0))
                + max(0.0, _finite_number(item.get("duration"), 0.0)),
            )
    return duration


def resolve_editor_asset(
    asset: Mapping[str, Any],
    *,
    save_root: str,
    workspace: str,
    uploads_root: str,
) -> str:
    # The first Continuum Editor slice imports only an output already owned by
    # this project. The host-global upload directory has no Editor-specific
    # ownership proof, so it must not become a cross-project fallback.
    if str(asset.get("origin") or "output").lower() not in {"output", "project"}:
        raise EditorProjectError("Upload import is not available in Editor yet")
    name = os.path.basename(str(asset.get("name") or ""))
    path_hint = str(asset.get("path") or "")
    owning_workspace = _safe_workspace_name(workspace)
    if _safe_workspace_name(asset.get("workspace") or owning_workspace) != owning_workspace:
        raise EditorProjectError("Editor source belongs to a different workspace")
    output_root = workspace_directory(save_root, owning_workspace)
    del uploads_root
    # A stale explicit source must fail instead of silently resolving a
    # same-named clip after a workspace switch.
    candidates = [path_hint] if path_hint else [os.path.join(output_root, name)]
    for candidate in candidates:
        if not os.path.isabs(candidate):
            candidate = os.path.abspath(candidate)
        resolved = os.path.realpath(candidate)
        if owning_workspace == "default":
            # The default workspace owns files directly in save_root, not the
            # named project directories below it.
            contained = os.path.dirname(resolved) == output_root
        else:
            contained = _is_under(resolved, output_root)
        if contained and os.path.isfile(resolved) and Path(resolved).suffix.lower() in _MEDIA_EXTENSIONS:
            return resolved
    raise EditorProjectError(f"Editor source media was not found: {name or 'unnamed asset'}")


def probe_media(path: str, *, ffprobe: str = "ffprobe") -> dict[str, Any]:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    command = [
        ffprobe,
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise EditorProjectError((result.stderr or "Unable to inspect media").strip()[:500])
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    extension = Path(path).suffix.lower()
    try:
        video_frame_count = int((video or {}).get("nb_frames") or 0)
    except (TypeError, ValueError):
        # ffprobe commonly emits "N/A" for stills and some container formats.
        video_frame_count = 0
    if extension in _AUDIO_EXTENSIONS and video is None:
        media_type = "audio"
    elif extension in _IMAGE_EXTENSIONS or (video and video_frame_count == 1):
        media_type = "image"
    else:
        media_type = "video" if video else "audio"
    duration = _finite_number((payload.get("format") or {}).get("duration"), 0.0)
    if duration <= 0:
        duration = _finite_number((video or audio or {}).get("duration"), 0.0)
    rate = str((video or {}).get("avg_frame_rate") or "0/1")
    try:
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / max(1.0, float(denominator))
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "name": os.path.basename(path),
        "type": media_type,
        "duration": max(0.0, duration),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "fps": max(0.0, fps),
        "has_audio": audio is not None,
        "audio_channels": int((audio or {}).get("channels") or 0),
        "audio_sample_rate": int((audio or {}).get("sample_rate") or 0),
        "size": os.path.getsize(path),
    }


def inspect_editor_assets(
    project: Mapping[str, Any],
    *,
    save_root: str,
    workspace: str,
    uploads_root: str,
) -> list[dict[str, Any]]:
    """Return source availability without mutating or leaking project state."""
    normalized = normalize_editor_project(project, workspace=workspace)
    results: list[dict[str, Any]] = []
    for asset_id, asset in normalized["assets"].items():
        try:
            path = resolve_editor_asset(
                asset,
                save_root=save_root,
                workspace=workspace,
                uploads_root=uploads_root,
            )
            results.append({"asset_id": asset_id, "available": True, "path": path})
        except (EditorProjectError, FileNotFoundError, OSError) as error:
            results.append({
                "asset_id": asset_id,
                "available": False,
                "error": str(error),
            })
    return results
