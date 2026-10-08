"""CPU temporal crop preparation and inverse composition, without a face model.

Observations are explicit reviewed rectangles, not detector or identity results.
This stage does not regenerate faces. It preserves the source clock and audio,
and accepts only bounded, zero-start, 24-fps CFR clips for now.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil

from services import h3_gallery_av_guide as av
from shared.utils.media_encoder import EncoderProcess

SCHEMA = "maestro.h3.face-crops"
MAX_RGB_BYTES = 512 * 1024**2


def _seal(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _json_command(command, cancel_check):
    data = bytearray()
    av._stream(command, 2 * 1024**2, data.extend, cancel_check)
    return json.loads(data)


def _probe(snapshot, cancel_check):
    data = _json_command([shutil.which("ffprobe") or "ffprobe", "-v", "error",
        "-max_alloc", "67108864", "-threads", "2", *av._input(snapshot),
        "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_read_packets",
        "-of", "json"], cancel_check)
    try:
        stream, = data["streams"]
        width, height = stream["width"], stream["height"]
        packet_count = int(stream["nb_read_packets"])
        if (stream["r_frame_rate"] != "24/1" or stream["avg_frame_rate"] not in ("24/1", "0/0")
                or type(width) is not int or type(height) is not int
                or min(width, height) < 8 or max(width, height) > 4096
                or not 1 <= packet_count <= av.MAX_FRAMES):
            raise ValueError()
        # Reject oversized geometry before asking FFprobe to decode any frame.
        data = _json_command([shutil.which("ffprobe") or "ffprobe", "-v", "error",
            "-max_alloc", "67108864", "-threads", "2", *av._input(snapshot), "-select_streams", "v:0", "-show_frames",
            "-show_entries", "frame=width,height,best_effort_timestamp_time", "-of", "json"], cancel_check)
        frames = data["frames"]
        if (len(frames) != packet_count or not 1 <= len(frames) <= av.MAX_FRAMES
                or len(frames) * width * height * 3 > MAX_RGB_BYTES
                or any((frame["width"], frame["height"]) != (width, height) for frame in frames)):
            raise ValueError()
        # Matroska's millisecond time base rounds a true 24-fps clock.
        if any(not math.isfinite(float(frame["best_effort_timestamp_time"]))
               or abs(float(frame["best_effort_timestamp_time"]) - index / 24) > 0.00051
               for index, frame in enumerate(frames)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Face crops require a bounded, zero-start 24-fps CFR video") from None
    return {"width": width, "height": height, "frame_count": len(frames), "fps": "24/1"}


def _audio(snapshot, cancel_check):
    return _json_command([shutil.which("ffprobe") or "ffprobe", "-v", "error",
        "-max_alloc", "67108864", *av._input(snapshot), "-select_streams", "a", "-show_packets", "-show_data_hash", "sha256",
        "-show_entries", "packet=stream_index,pts_time,dts_time,data_hash:stream=index,codec_name",
        "-show_streams", "-of", "json"], cancel_check)


def _check_audio(before, after):
    if [s["codec_name"] for s in before.get("streams", [])] != [s["codec_name"] for s in after.get("streams", [])]:
        raise ValueError("Face composition changed source audio codecs")
    indices_before = [s["index"] for s in before.get("streams", [])]
    indices_after = [s["index"] for s in after.get("streams", [])]
    # Muxers may change interleaving across streams without changing any stream.
    left = [p for index in indices_before for p in before.get("packets", []) if p["stream_index"] == index]
    right = [p for index in indices_after for p in after.get("packets", []) if p["stream_index"] == index]
    if len(left) != len(right):
        raise ValueError("Face composition changed source audio packets")
    for a, b in zip(left, right):
        if (a["data_hash"] != b["data_hash"]
                or indices_before.index(a["stream_index"]) != indices_after.index(b["stream_index"])):
            raise ValueError("Face composition changed source audio packets")
        for key in ("pts_time", "dts_time"):
            if (key not in a or key not in b or not math.isfinite(float(a[key]))
                    or not math.isfinite(float(b[key])) or abs(float(a[key]) - float(b[key])) > 0.00101):
                raise ValueError("Face composition changed source audio alignment")


def plan_crops(facts, observations):
    """Smooth reviewed face rectangles only within contiguous runs of one shot.

    An absent observation means original pixels at composition, with no inferred
    subject, interpolation or fallback to another face. Boxes use x0,y0,x1,y1.
    """
    if type(observations) is not dict or set(observations) != {"shots", "boxes", "canvas", "padding", "smoothing"}:
        raise ValueError("Invalid face observation fields")
    count, width, height = (facts[k] for k in ("frame_count", "width", "height"))
    shots, boxes, canvas = (observations[k] for k in ("shots", "boxes", "canvas"))
    padding, smoothing = observations["padding"], observations["smoothing"]
    if (type(shots) is not list or not shots or shots[0] != 0
            or any(type(x) is not int or not 0 <= x < count for x in shots)
            or shots != sorted(set(shots)) or type(boxes) is not list or len(boxes) != count
            or type(canvas) is not list or len(canvas) != 2
            or any(type(x) is not int or not 64 <= x <= 1536 or x % 32 for x in canvas)
            or type(padding) not in (int, float) or not math.isfinite(padding) or not 1 <= padding <= 4
            or type(smoothing) is not int or not 1 <= smoothing <= 15
            or count * canvas[0] * canvas[1] * 3 > MAX_RGB_BYTES):
        raise ValueError("Invalid face observation geometry or limits")
    history, rectangles = [], []
    for index, box in enumerate(boxes):
        if index in shots or box is None:
            history = []
        if box is None:
            rectangles.append(None)
            continue
        if (type(box) is not list or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
            raise ValueError("Invalid reviewed face rectangle")
        history.append(box)
        history = history[-smoothing:]
        x0, y0, x1, y1 = [sum(row[k] for row in history) / len(history) for k in range(4)]
        # Keep the canvas aspect; record the actual integer, clamped rectangle.
        crop_w = max(x1 - x0, (y1 - y0) * canvas[0] / canvas[1]) * padding
        crop_h = crop_w * canvas[1] / canvas[0]
        scale = min(1, width / crop_w, height / crop_h)
        crop_w, crop_h = max(1, round(crop_w * scale)), max(1, round(crop_h * scale))
        x = min(max(0, round((x0 + x1 - crop_w) / 2)), width - crop_w)
        y = min(max(0, round((y0 + y1 - crop_h) / 2)), height - crop_h)
        rectangles.append([x, y, crop_w, crop_h])
    if not any(rectangles):
        raise ValueError("No reviewed faces in this clip")
    return {"canvas": canvas[:], "rectangles": rectangles, "shots": shots[:],
            "observations_sha256": _seal(observations)}


def _read_rgb(snapshot, facts, cancel_check):
    import numpy as np
    count = facts["frame_count"] * facts["width"] * facts["height"] * 3
    pixels = np.empty(count, dtype=np.uint8)
    offset = 0
    def consume(chunk):
        nonlocal offset
        pixels[offset:offset + len(chunk)] = np.frombuffer(chunk, dtype=np.uint8)
        offset += len(chunk)
    av._stream([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-nostdin",
        "-max_alloc", "67108864", "-threads", "2", *av._input(snapshot), "-map", "0:v:0", "-an", "-threads", "2", "-fps_mode", "passthrough",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], count, consume, cancel_check)
    if offset != count:
        raise ValueError("Face video decoded frame count changed")
    return pixels.reshape(facts["frame_count"], facts["height"], facts["width"], 3)


def _render_frames(original, replacements, track, cancel_check):
    from PIL import Image
    import numpy as np
    canvas = tuple(track["canvas"])
    for index, rectangle in enumerate(track["rectangles"]):
        av._check(cancel_check)
        image = Image.fromarray(original[index])
        if replacements is None:
            if rectangle is None:
                yield bytes(canvas[0] * canvas[1] * 3)
            else:
                x, y, width, height = rectangle
                yield image.crop((x, y, x + width, y + height)).resize(canvas, Image.Resampling.BICUBIC).tobytes()
        else:
            # A narrow first stage: hard rectangular paste, no invented mask.
            if rectangle is not None:
                x, y, width, height = rectangle
                patch = Image.fromarray(replacements[index]).resize((width, height), Image.Resampling.BICUBIC)
                image.paste(patch, (x, y))
            yield np.asarray(image).tobytes()


def _encode(path, frames, width, height, cancel_check, audio_source=None):
    command = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-nostdin", "-n", "-copyts",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{width}x{height}",
        "-framerate", "24", "-i", "pipe:0"]
    if audio_source is not None:
        command += av._input(audio_source)
    command += ["-map", "0:v:0"]
    if audio_source is not None:
        command += ["-map", "1:a?", "-c:a", "copy"]
    command += ["-c:v", "ffv1", "-level", "3", "-threads", "2", "-pix_fmt", "bgr0",
                "-avoid_negative_ts", "disabled", "-f", "matroska", str(path)]
    with EncoderProcess(command, timeout=60, abort_check=cancel_check) as encoder:
        for frame in frames:
            encoder.write(frame)
        if encoder.finish() != 0:
            raise ValueError("Face media encoding failed")
    os.chmod(path, 0o600)


def _write_json(path, value):
    with Path(path).open("x") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepare_crops(source, observations, destination, *, cancel_check=None):
    """Create a new private directory; plan.json is its final readiness marker."""
    if os.path.lexists(destination):
        raise FileExistsError("Face crop destination already exists")
    with av._snapshot(str(source), "video", cancel_check) as (snapshot, digest, size):
        facts = _probe(snapshot, cancel_check)
        track = plan_crops(facts, observations)
        if (facts["width"] * facts["height"] + track["canvas"][0] * track["canvas"][1]) * facts["frame_count"] * 3 > MAX_RGB_BYTES:
            raise ValueError("Face preparation exceeds composition memory limit")
        pixels = _read_rgb(snapshot, facts, cancel_check)
        destination = Path(destination)
        destination.mkdir(mode=0o700)  # Exclusive; preserve any existing bundle.
        crops = destination / "crops.mkv"
        _encode(crops, _render_frames(pixels, None, track, cancel_check), *track["canvas"], cancel_check)
        if not 0 < crops.stat().st_size <= av.MAX_ENCODED_BYTES:
            raise ValueError("Face crops exceed the replacement encoded size limit")
        if _probe(crops, cancel_check) != {**facts, "width": track["canvas"][0], "height": track["canvas"][1]}:
            raise ValueError("Face crop clock or geometry changed")
        plan = {"schema": SCHEMA, "version": 1, "source": {**facts, "sha256": digest, "size": size},
                "track": track, "crops_sha256": _file_digest(crops)}
        plan["plan_sha256"] = _seal(plan)
        av._check(cancel_check)
        _write_json(destination / "plan.json", plan)
        return plan


def _validate_plan(plan):
    if (type(plan) is not dict or set(plan) != {"schema", "version", "source", "track", "crops_sha256", "plan_sha256"}
            or plan["schema"] != SCHEMA or type(plan["version"]) is not int or plan["version"] != 1
            or plan["plan_sha256"] != _seal({k: v for k, v in plan.items() if k != "plan_sha256"})):
        raise ValueError("Face crop plan commitment changed")
    facts, track = plan["source"], plan["track"]
    if (type(facts) is not dict or set(facts) != {"width", "height", "frame_count", "fps", "sha256", "size"}
            or type(track) is not dict or set(track) != {"canvas", "rectangles", "shots", "observations_sha256"}):
        raise ValueError("Invalid face crop plan fields")
    if (any(type(facts[k]) is not int for k in ("width", "height", "frame_count", "size"))
            or not 8 <= min(facts["width"], facts["height"])
            or max(facts["width"], facts["height"]) > 4096
            or not 1 <= facts["frame_count"] <= av.MAX_FRAMES
            or not 0 < facts["size"] <= av.MAX_ENCODED_BYTES or facts["fps"] != "24/1"
            or type(track["rectangles"]) is not list or len(track["rectangles"]) != facts["frame_count"]):
        raise ValueError("Invalid face crop source limits")
    for digest in (facts["sha256"], plan["crops_sha256"], track["observations_sha256"]):
        if type(digest) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid face crop byte commitment")
    boxes = []
    for row in track["rectangles"]:
        if row is None:
            boxes.append(None)
        elif (type(row) is list and len(row) == 4 and all(type(v) is int for v in row)
                and row[2] > 0 and row[3] > 0):
            boxes.append([row[0], row[1], row[0] + row[2], row[1] + row[3]])
        else:
            raise ValueError("Invalid face crop inverse transforms")
    # Validate bounds without recomputing integer-rounded inverse transforms.
    plan_crops(facts, dict(shots=track["shots"], boxes=boxes, canvas=track["canvas"], padding=1, smoothing=1))
    return facts, track


def compose_crops(source, replacement, plan, replacement_sha256, destination, *, cancel_check=None):
    """Compose an explicitly pinned crop result into a new lossless private file.

    This does not prove replacement crops were generated by H3, nor face quality.
    The completion receipt is written only after clock and audio checks.
    """
    facts, track = _validate_plan(plan)
    if os.path.lexists(destination):
        raise FileExistsError("Face composite destination already exists")
    if type(replacement_sha256) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", replacement_sha256) is None:
        raise ValueError("Explicit replacement byte commitment is required")
    with av._snapshot(str(source), "video", cancel_check) as (snapshot, digest, size):
        if (digest, size) != (facts["sha256"], facts["size"]) or _probe(snapshot, cancel_check) != {k: facts[k] for k in ("width", "height", "frame_count", "fps")}:
            raise ValueError("Face composition source changed")
        with av._snapshot(str(replacement), "video", cancel_check) as (refined, refined_digest, _):
            if refined_digest != replacement_sha256:
                raise ValueError("Face replacement bytes changed")
            expected = {k: facts[k] for k in ("width", "height", "frame_count", "fps")}
            if _probe(refined, cancel_check) != {**expected, "width": track["canvas"][0], "height": track["canvas"][1]}:
                raise ValueError("Face replacement clock or canvas differs")
            if (facts["width"] * facts["height"] + track["canvas"][0] * track["canvas"][1]) * facts["frame_count"] * 3 > MAX_RGB_BYTES:
                raise ValueError("Face composition exceeds decoded memory limit")
            original = _read_rgb(snapshot, facts, cancel_check)
            patches = _read_rgb(refined, {**expected, "width": track["canvas"][0], "height": track["canvas"][1]}, cancel_check)
            destination = Path(destination)
            destination.mkdir(mode=0o700)
            output = destination / "composite.mkv"
            _encode(output, _render_frames(original, patches, track, cancel_check), facts["width"], facts["height"], cancel_check, snapshot)
            if _probe(output, cancel_check) != expected:
                raise ValueError("Face composite clock or canvas changed")
            _check_audio(_audio(snapshot, cancel_check), _audio(output, cancel_check))
            receipt = {"schema": "maestro.h3.face-composite", "version": 1,
                       "plan_sha256": plan["plan_sha256"], "source_sha256": digest,
                       "replacement_sha256": refined_digest, "output_sha256": _file_digest(output),
                       "frame_count": facts["frame_count"], "fps": facts["fps"],
                       "audio_packets_preserved": True, "native_h3_generation": False}
            av._check(cancel_check)
            _write_json(destination / "receipt.json", receipt)
            return receipt
