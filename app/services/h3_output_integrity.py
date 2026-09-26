"""Content-free CPU checks for a finished H3 video artifact.

The structural result may gate completion. Sampled brightness, motion and
audio level are evidence for review, never a judgment about creative intent.
No path or decoded media is returned or persisted by this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
from typing import Any, Callable


def _rate(value: Any) -> float:
    try:
        parts = str(value or "").split("/", 1)
        number = float(parts[0])
        rate = number / float(parts[1]) if len(parts) == 2 else number
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return rate if math.isfinite(rate) and rate > 0 else 0.0


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def probe_h3_output(
    path: str | os.PathLike[str],
    *,
    expected_resolution: tuple[int, int] | None = None,
    expected_fps: float | None = None,
    expected_frames: int | None = None,
    require_audio: bool = False,
    audio_sample_rate: int | None = None,
    audio_channels: int | None = None,
    sample_signal: bool = False,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Probe one regular video file without exposing its path or content.

    Expected fields come from the executed job, not from filename or prompt.
    Signal samples are advisory because a dark, static or silent scene can be
    intentional. A failed or unavailable structural probe is not verified.
    """
    artifact = Path(path)
    try:
        if not stat.S_ISREG(artifact.lstat().st_mode):
            raise OSError("not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(artifact, flags), "rb") as source:
            identity = os.fstat(source.fileno())
            if not stat.S_ISREG(identity.st_mode) or identity.st_size <= 0:
                raise OSError("not a nonempty regular file")
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return {"validation": "invalid", "checks": {"artifact_nonempty": False}}

    result: dict[str, Any] = {
        "validation": "unverified",
        "artifact_size_bytes": identity.st_size,
        "artifact_sha256": f"sha256:{digest.hexdigest()}",
        "checks": {"artifact_nonempty": True},
    }
    ffprobe = which("ffprobe")
    if not ffprobe:
        result["probe_available"] = False
        return result
    result["probe_available"] = True
    try:
        completed = run(
            [ffprobe, "-v", "error", "-count_frames", "-show_streams",
             "-show_format", "-of", "json", str(artifact)],
            check=True, capture_output=True, text=True, timeout=300,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("invalid probe response")
    except (OSError, subprocess.SubprocessError, ValueError):
        return result

    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    videos = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"]
    audios = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"]
    video = videos[0] if videos else {}
    audio = audios[0] if audios else {}
    container = payload.get("format")
    container = container if isinstance(container, dict) else {}
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        frame_count = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
        sample_rate = int(audio.get("sample_rate") or 0)
        channels = int(audio.get("channels") or 0)
    except (TypeError, ValueError, OverflowError):
        width = height = frame_count = sample_rate = channels = 0
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    duration = _number(video.get("duration")) or _number(container.get("duration"))
    checks = result["checks"]
    checks.update({
        "one_video_stream": len(videos) == 1,
        "dimensions_positive": width > 0 and height > 0,
        "fps_positive": fps > 0,
        "frames_positive": frame_count > 0,
        "duration_positive": duration > 0,
        "duration_frame_grid": fps > 0 and frame_count > 0
            and abs(duration * fps - frame_count) <= 1.5,
    })
    if expected_resolution is not None:
        checks["expected_dimensions"] = (width, height) == expected_resolution
    if expected_fps is not None:
        checks["expected_fps"] = (
            _number(expected_fps) > 0
            and abs(fps - expected_fps) <= max(0.001, expected_fps * 0.0001)
        )
    if expected_frames is not None:
        checks["expected_frames"] = frame_count == expected_frames
    if require_audio:
        checks["one_audio_stream"] = len(audios) == 1
    if audio_sample_rate is not None:
        checks["expected_audio_sample_rate"] = sample_rate == audio_sample_rate
    if audio_channels is not None:
        checks["expected_audio_channels"] = channels == audio_channels
    result.update({
        "width": width, "height": height, "fps": round(fps, 6),
        "frame_count": frame_count, "duration_seconds": round(duration, 6),
        "video_stream_count": len(videos), "audio_stream_count": len(audios),
        "audio_sample_rate": sample_rate, "audio_channels": channels,
        "validation": "valid" if all(checks.values()) else "invalid",
    })

    if not sample_signal or frame_count < 2:
        return result
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        result["signal_probe_available"] = False
        return result
    result["signal_probe_available"] = True
    indices = sorted({0, frame_count // 2, frame_count - 1})
    selector = "+".join(f"eq(n\\,{index})" for index in indices)
    try:
        frames = run(
            [ffmpeg, "-v", "error", "-i", str(artifact), "-vf",
             f"select='{selector}',scale=32:32,format=gray", "-vsync", "0",
             "-an", "-f", "rawvideo", "-"],
            check=True, capture_output=True, timeout=60,
        ).stdout
        size = 32 * 32
        if len(frames) == size * len(indices):
            samples = [frames[offset:offset + size] for offset in range(0, len(frames), size)]
            result["sampled_video_frames"] = len(samples)
            result["sampled_max_mean_luma"] = round(max(
                sum(frame) / (size * 255.0) for frame in samples
            ), 6)
            result["sampled_max_motion_delta"] = round(max(
                sum(abs(left - right) for left, right in zip(a, b)) / (size * 255.0)
                for a, b in zip(samples, samples[1:])
            ), 6)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if audios:
        try:
            samples = run(
                [ffmpeg, "-v", "error", "-i", str(artifact), "-map", "0:a:0",
                 "-vn", "-t", "5", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                check=True, capture_output=True, timeout=30,
            ).stdout
            values = [item[0] for item in struct.iter_unpack("<f", samples)]
            if values:
                mean = sum(values) / len(values)
                rms = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
                result["sampled_audio_ac_rms"] = round(rms, 8)
        except (OSError, subprocess.SubprocessError, ValueError, struct.error):
            pass
    return result
