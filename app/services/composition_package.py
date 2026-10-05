"""Sealed typed Blender segments consumed by CPU Editor.

Execution receives only worker-owned paths. Interrupted Blender attempts are
held; only a completed byte-sealed segment can be reused without execution.
"""

from __future__ import annotations
import copy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import uuid

from services.blender_mcp_service import BlenderMCPService, PINNED_INSTALL
from services.queue_recovery_runtime import sha256_file
from services.editor_export import editor_clip_frames

SCHEMA = "maestro/composition/v1"
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class CompositionError(ValueError):
    pass


class CompositionAttemptUnresolved(CompositionError):
    pass


class CompositionNeedsNative(CompositionError):
    pass


def closed(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        raise CompositionError(f"{label} must contain exactly the supported fields")


def identifier(value):
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        raise CompositionError("Invalid composition identifier")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise CompositionError("Composition integer is outside its supported range")
    return value


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def canvas(value):
    closed(value, ("width", "height", "fps"), "Canvas")
    integer(value["width"], 64, 7680)
    integer(value["height"], 64, 4320)
    if value["width"] % 2 or value["height"] % 2:
        raise CompositionError("Composition canvas dimensions must be even")
    fps = value["fps"]
    if type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 120:
        raise CompositionError(
            "Editor composition frame rate is outside its supported range"
        )
    return dict(value)


def normalize_package(value, service: BlenderMCPService):
    closed(
        value, ("schema", "id", "canvas", "audio", "segments", "clips"), "Composition"
    )
    if value["schema"] != SCHEMA or value["audio"] != {
        "mode": "silence",
        "sample_rate": 48000,
    }:
        raise CompositionError("Unsupported composition version or audio commitment")
    result = {
        "schema": SCHEMA,
        "id": identifier(value["id"]),
        "canvas": canvas(value["canvas"]),
        "audio": dict(value["audio"]),
        "segments": [],
        "clips": [],
    }
    if type(value["segments"]) is not list or not 1 <= len(value["segments"]) <= 2:
        raise CompositionError("Use one or two Blender segments")
    by_id = {}
    for raw in value["segments"]:
        closed(
            raw,
            ("id", "scene", "animation", "fps", "width", "height"),
            "Blender segment",
        )
        sid = identifier(raw["id"])
        if sid in by_id:
            raise CompositionError("Segment identifiers must be unique")
        scene = service._normalize_scene_create(raw["scene"])
        if not scene["clear_scene"]:
            raise CompositionError("Each segment must replace its private scene")
        animation = service._normalize_animation(raw["animation"])
        if not set(item["name"] for item in animation["objects"]) <= set(
            item["name"] for item in scene["objects"]
        ):
            raise CompositionError("Animation objects must belong to the segment scene")
        fps = integer(raw["fps"], 1, 240)
        integer(raw["width"], 64, 7680)
        integer(raw["height"], 64, 4320)
        if raw["width"] % 2 or raw["height"] % 2:
            raise CompositionError("Blender dimensions must be even")
        if (
            animation["frame_end"] - animation["frame_start"] + 1
            > service.limits.max_total_frames
        ):
            raise CompositionError("Segment exceeds the Blender frame limit")
        segment = {
            "id": sid,
            "scene": scene,
            "animation": animation,
            "fps": fps,
            "width": raw["width"],
            "height": raw["height"],
        }
        by_id[sid] = segment
        result["segments"].append(segment)
    if type(value["clips"]) is not list or not 1 <= len(value["clips"]) <= 8:
        raise CompositionError("Use one through eight clip instances")
    seen = set()
    total_frames = 0
    for raw in value["clips"]:
        closed(raw, ("id", "segment_id", "source_frame", "frame_count"), "Clip")
        cid = identifier(raw["id"])
        sid = identifier(raw["segment_id"])
        if cid in seen or sid not in by_id:
            raise CompositionError("Clip identity or segment reference is invalid")
        seen.add(cid)
        segment = by_id[sid]
        animation = segment["animation"]
        source = integer(raw["source_frame"], 0, service.limits.max_total_frames - 1)
        count = integer(raw["frame_count"], 1, service.limits.max_total_frames)
        if source + count > animation["frame_end"] - animation["frame_start"] + 1:
            raise CompositionError("Clip exceeds its rendered segment")
        total_frames += editor_clip_frames(
            count / segment["fps"], result["canvas"]["fps"]
        )
        result["clips"].append(
            {"id": cid, "segment_id": sid, "source_frame": source, "frame_count": count}
        )
    if total_frames / result["canvas"]["fps"] > 86400:
        raise CompositionError("Composition exceeds Editor duration limit")
    result["frame_count"] = total_frames
    return result


def editor_clips(package, paths):
    segments = {segment["id"]: segment for segment in package["segments"]}
    if set(paths) != set(segments):
        raise CompositionError("Completed segment closure does not match package")
    return [
        {
            "id": clip["id"],
            "path": str(paths[clip["segment_id"]]),
            "source_in": clip["source_frame"] / segments[clip["segment_id"]]["fps"],
            "duration": clip["frame_count"] / segments[clip["segment_id"]]["fps"],
            "has_audio": False,
        }
        for clip in package["clips"]
    ]


def validate_normalized_package(value, service):
    closed(
        value,
        ("schema", "id", "canvas", "audio", "segments", "clips", "frame_count"),
        "Sealed composition",
    )
    raw = {
        key: copy.deepcopy(item) for key, item in value.items() if key != "frame_count"
    }
    normalized = normalize_package(raw, service)
    if canonical(normalized) != canonical(value):
        raise CompositionError(
            "Sealed composition is not its normalized execution commitment"
        )
    return normalized


def sign_receipt(value, secret):
    return dict(
        value, mac=hmac.new(secret, canonical(value), hashlib.sha256).hexdigest()
    )


def read_receipt(path, secret):
    if path.is_symlink():
        raise CompositionError("Receipt is not a private regular file")
    size, _ = sha256_file(path)
    if size > 1024 * 1024:
        raise CompositionError("Receipt exceeds its supported size")
    value = json.loads(path.read_text())
    signature = value.pop("mac", None)
    if type(signature) is not str or not hmac.compare_digest(
        signature, hmac.new(secret, canonical(value), hashlib.sha256).hexdigest()
    ):
        raise CompositionError("Receipt authentication failed")
    return value


def atomic_json(path, payload):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(canonical(payload).decode() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def render_segments(
    package, service, directory, *, binding, probe, cancelled, secret, allow_native=True
):
    """Run closed native commands once; verify completed bytes before reuse.

    The caller owns the generation/native slots, scene lock, project reservation
    and exact request manifest. This function confers no resource or path authority.
    """
    if type(secret) is not bytes or len(secret) < 32:
        raise CompositionError(
            "Composition receipts require the owning server authentication key"
        )
    package = validate_normalized_package(package, service)

    def persist(path, payload):
        atomic_json(path, sign_receipt(payload, secret))

    directory = Path(directory)
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or service.project_root != directory.resolve()
    ):
        raise CompositionError("Composition worker directory binding changed")
    seal = {
        "package_sha256": digest(package),
        "binding": copy.deepcopy(binding),
        "blender_mcp_revision": PINNED_INSTALL.revision,
    }
    # Validate every complete native command before arming any segment attempt.
    render_commands = {}
    for segment in package["segments"]:
        animation = segment["animation"]
        arguments = {
            "output_path": segment["id"] + ".mp4",
            "frame_start": animation["frame_start"],
            "frame_end": animation["frame_end"],
            "fps": segment["fps"],
            "width": segment["width"],
            "height": segment["height"],
        }
        service._normalize_render_animation(arguments)
        render_commands[segment["id"]] = arguments
    cached = {}
    # Inspect all existing evidence before executing a missing earlier segment.
    # A later unknown attempt must hold the entire composition at admission.
    for segment in package["segments"]:
        if cancelled():
            raise InterruptedError("Composition cancelled")
        sid = segment["id"]
        path = directory / (sid + ".mp4")
        receipt_path = directory / (sid + ".receipt.json")
        expected = {**seal, "segment_id": sid, "segment_sha256": digest(segment)}
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = read_receipt(receipt_path, secret)
            if any(receipt.get(key) != value for key, value in expected.items()):
                raise CompositionError("Segment execution binding changed")
            if receipt.get("state") != "completed":
                raise CompositionAttemptUnresolved(
                    "Interrupted Blender attempt requires explicit recovery; it will not run again automatically"
                )
            size, sha = sha256_file(path, abort_check=cancelled)
            if size != receipt.get("size") or sha != receipt.get("sha256"):
                raise CompositionError("Completed segment bytes changed")
            cached[sid] = receipt
            continue
        if path.exists() or path.is_symlink():
            raise CompositionAttemptUnresolved("Unsealed segment already exists")
    if not allow_native and len(cached) != len(package["segments"]):
        raise CompositionNeedsNative("A committed segment has not run yet")
    result = {}
    receipts = []
    for segment in package["segments"]:
        if cancelled():
            raise InterruptedError("Composition cancelled")
        sid = segment["id"]
        path = directory / (sid + ".mp4")
        receipt_path = directory / (sid + ".receipt.json")
        if sid in cached:
            result[sid] = path
            receipts.append(cached[sid])
            continue
        expected = {**seal, "segment_id": sid, "segment_sha256": digest(segment)}
        # Arm before the first scene mutation. Any failure stays unresolved.
        receipt = {**expected, "state": "attempting", "attempt_id": uuid.uuid4().hex}
        persist(receipt_path, receipt)
        service.invoke("scene_create", segment["scene"], cancelled=cancelled)
        service.invoke("animate_keyframes", segment["animation"], cancelled=cancelled)
        animation = segment["animation"]
        service.invoke("render_animation", render_commands[sid], cancelled=cancelled)
        if cancelled():
            raise InterruptedError("Composition cancelled")
        media = probe(str(path))
        expected_frames = animation["frame_end"] - animation["frame_start"] + 1
        if (
            media.get("type") != "video"
            or media.get("width") != segment["width"]
            or media.get("height") != segment["height"]
            or not math.isclose(
                float(media.get("fps") or 0), segment["fps"], abs_tol=1e-4
            )
            or abs(float(media.get("duration") or 0) - expected_frames / segment["fps"])
            > 1 / segment["fps"]
            or media.get("has_audio")
        ):
            raise CompositionError(
                "Blender segment does not match its committed media clock"
            )
        size, sha = sha256_file(path, abort_check=cancelled)
        receipt.update(state="completed", size=size, sha256=sha)
        persist(receipt_path, receipt)
        result[sid] = path
        receipts.append(receipt)
    return result, receipts
