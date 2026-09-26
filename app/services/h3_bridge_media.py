"""CPU-only preparation and assembly for native MiniMax H3 Bridge media.

This module binds the inert H3 Bridge plan to already-authorized local files.
It validates source hashes and a deterministic constant-frame-rate mapping to
H3's 24 fps clock, creates visual-only Ref2VA guide clips, and assembles the
plan's published bridge interval. It never inspects or classifies media
content.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from services.h3_bridge_plan import H3BridgePlanError, validate_h3_bridge_plan
from services.h3_native_continuation import (
    H3_AUDIO_TICKS_PER_SECOND,
    H3_NATIVE_FPS,
    audio_tick_at_frame,
)

GUIDE_FRAMES = 56
REFERENCE_MIN_FRAMES = 48
REFERENCE_MAX_FRAMES = 15 * H3_NATIVE_FPS
REFERENCE_MAX_TOTAL_FRAMES = 15 * H3_NATIVE_FPS
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_MEDIA_BYTES = 8 * 1024**3
_MAX_FRAME_COUNT = 10_000_000
_MAX_OUTPUT_DIMENSION = 4096
_MAX_OUTPUT_PIXELS = 12_000_000
_MAX_SOURCE_DURATION_SECONDS = 30 * 60
_MAX_SOURCE_FPS = Fraction(240, 1)
_AUDIO_SAMPLE_RATE = 48_000
_COMMAND_TIMEOUT_SECONDS = 3600.0
_FRAME_RATE = Fraction(H3_NATIVE_FPS, 1)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_END_OF_STREAM = object()

CancelCheck = Callable[[], object]


class H3BridgeMediaError(ValueError):
    """Raised when a bridge media binding or conversion is invalid."""


class H3BridgeMediaCancelled(H3BridgeMediaError):
    """Raised when the caller cancels a bridge media operation."""


@dataclass(frozen=True, slots=True)
class SourceProbe:
    """Path-free media facts on native and H3's normalized frame clocks."""

    sha256: str
    fps: Fraction
    frame_count: int
    frames_24fps: int
    width: int
    height: int
    has_audio: bool
    audio_channels: int | None
    audio_duration: Fraction | None

    @property
    def duration(self) -> Fraction:
        """Return the source duration on its native constant-rate clock."""

        return Fraction(self.frame_count, 1) / self.fps

    @property
    def duration_24fps(self) -> Fraction:
        """Return the normalized video duration used by the H3 plan."""

        return Fraction(self.frames_24fps, H3_NATIVE_FPS)


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is None:
        return
    try:
        cancelled = bool(cancel_check())
    except Exception:  # noqa: BLE001 - hide arbitrary callback details from public errors
        raise H3BridgeMediaError(
            "bridge cancellation state could not be checked"
        ) from None
    if cancelled:
        raise H3BridgeMediaCancelled("bridge media operation was cancelled")


def _path(value: object, *, label: str) -> Path:
    try:
        raw = os.fspath(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise H3BridgeMediaError(f"{label} must be a local file path") from None
    if not isinstance(raw, str) or not raw:
        raise H3BridgeMediaError(f"{label} must be a local file path")
    try:
        candidate = Path(os.path.abspath(os.path.expanduser(raw)))
        metadata = os.lstat(candidate)
    except (OSError, ValueError):
        raise H3BridgeMediaError(f"{label} is unavailable") from None
    if (
        not os.path.isfile(candidate)
        or os.path.islink(candidate)
        or not metadata.st_size
    ):
        raise H3BridgeMediaError(f"{label} must be a nonempty regular file")
    if metadata.st_size > _MAX_MEDIA_BYTES:
        raise H3BridgeMediaError("media file exceeds the 8 GiB size limit")
    return candidate


def _file_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _hash_file(path: Path, cancel_check: CancelCheck | None) -> str:
    _check_cancel(cancel_check)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        named = os.lstat(path)
        if before.st_size > _MAX_MEDIA_BYTES:
            os.close(descriptor)
            raise H3BridgeMediaError("media file exceeds the 8 GiB size limit")
        if not os.path.isfile(path) or _file_signature(before) != _file_signature(
            named
        ):
            os.close(descriptor)
            raise H3BridgeMediaError("media source changed while it was being opened")
        digest = hashlib.sha256()
        bytes_read = 0
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            while True:
                _check_cancel(cancel_check)
                chunk = source.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > _MAX_MEDIA_BYTES:
                    raise H3BridgeMediaError("media file exceeds the 8 GiB size limit")
                digest.update(chunk)
            after = os.fstat(source.fileno())
        named_after = os.lstat(path)
    except H3BridgeMediaError:
        raise
    except OSError:
        raise H3BridgeMediaError("media source could not be read") from None
    if after.st_size > _MAX_MEDIA_BYTES:
        raise H3BridgeMediaError("media file exceeds the 8 GiB size limit")
    if _file_signature(before) != _file_signature(after) or _file_signature(
        after
    ) != _file_signature(named_after):
        raise H3BridgeMediaError("media source changed while it was being read")
    return "sha256:" + digest.hexdigest()


def _tools() -> tuple[str, str]:
    ffmpeg = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("FFPROBE_BINARY")
    if not ffprobe and ffmpeg:
        ffmpeg_path = Path(ffmpeg)
        candidate_name = ffmpeg_path.name.replace("ffmpeg", "ffprobe", 1)
        if candidate_name != ffmpeg_path.name:
            candidate = ffmpeg_path.with_name(candidate_name)
            if candidate.is_file():
                ffprobe = str(candidate)
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise H3BridgeMediaError(
            "CPU ffmpeg and ffprobe are required for H3 Bridge media"
        )
    return ffmpeg, ffprobe


def _run_command(
    command: list[str],
    *,
    stage: str,
    cancel_check: CancelCheck | None,
    capture_stdout: bool = False,
) -> bytes:
    _check_cancel(cancel_check)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise H3BridgeMediaError(f"{stage} could not start") from None
    deadline = time.monotonic() + _COMMAND_TIMEOUT_SECONDS
    output = b""
    try:
        while True:
            _check_cancel(cancel_check)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise H3BridgeMediaError(f"{stage} timed out")
            try:
                output, _ = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise H3BridgeMediaError(f"{stage} failed")
    except BaseException:
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        raise
    return output or b""


def _fraction(value: object) -> Fraction | None:
    if not isinstance(value, (str, int)) or not str(value).strip():
        return None
    try:
        parsed = Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _tagged_duration(stream: dict[str, Any]) -> Fraction | None:
    tags = stream.get("tags")
    text = tags.get("DURATION") if isinstance(tags, dict) else None
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    if match is None:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    seconds = Fraction(match.group(3))
    return Fraction(hours * 3600 + minutes * 60, 1) + seconds


def _stream_duration(stream: dict[str, Any]) -> Fraction | None:
    duration = _fraction(stream.get("duration"))
    if duration is not None:
        return duration
    duration_ticks = stream.get("duration_ts")
    time_base = _fraction(stream.get("time_base"))
    if type(duration_ticks) is int and duration_ticks > 0 and time_base is not None:
        return Fraction(duration_ticks, 1) * time_base
    return _tagged_duration(stream)


def _validate_declared_duration(duration: Fraction) -> None:
    if duration > _MAX_SOURCE_DURATION_SECONDS:
        raise H3BridgeMediaError("source media exceeds the 30-minute duration limit")
    normalized_frames = _round_half_up(duration * H3_NATIVE_FPS)
    if Fraction(normalized_frames, H3_NATIVE_FPS) > _MAX_SOURCE_DURATION_SECONDS:
        raise H3BridgeMediaError("source media exceeds the 30-minute duration limit")


def _validate_probe_dimensions(probe: SourceProbe) -> None:
    if (
        type(probe.width) is not int
        or type(probe.height) is not int
        or probe.width <= 0
        or probe.height <= 0
        or probe.width > _MAX_OUTPUT_DIMENSION
        or probe.height > _MAX_OUTPUT_DIMENSION
        or probe.width * probe.height > _MAX_OUTPUT_PIXELS
    ):
        raise H3BridgeMediaError(
            "source dimensions exceed 4096 pixels per side or 12 megapixels"
        )


def validate_bridge_source_dimensions(
    a_probe: SourceProbe, b_probe: SourceProbe
) -> None:
    """Reject A/B dimensions outside the shared H3 Bridge admission bound."""

    if not isinstance(a_probe, SourceProbe):
        raise H3BridgeMediaError("clip A media probe is invalid")
    if not isinstance(b_probe, SourceProbe):
        raise H3BridgeMediaError("clip B media probe is invalid")
    _validate_probe_dimensions(a_probe)
    _validate_probe_dimensions(b_probe)


def _metadata_video_duration(video: dict[str, Any], media_format: object) -> Fraction:
    duration = _stream_duration(video)
    if duration is None and isinstance(media_format, dict):
        duration = _fraction(media_format.get("duration"))
    if duration is None:
        raise H3BridgeMediaError("source duration is unavailable or ambiguous")
    return duration


def _validated_plan(plan: object) -> dict[str, Any]:
    try:
        return validate_h3_bridge_plan(plan)
    except H3BridgePlanError as error:
        raise H3BridgeMediaError(f"bridge plan is invalid: {error}") from None


def _verify_cfr_timestamps(
    ffprobe: str,
    path: Path,
    *,
    fps: Fraction,
    time_base: Fraction,
    cancel_check: CancelCheck | None,
    max_frame_count: int = _MAX_FRAME_COUNT,
) -> int:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp",
        "-of",
        "csv=p=0:nk=1",
        os.fspath(path),
    ]
    _check_cancel(cancel_check)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise H3BridgeMediaError("video frame timestamps could not be read") from None
    assert process.stdout is not None
    rows: queue.Queue[bytes | object] = queue.Queue()

    def read_rows() -> None:
        try:
            for row in process.stdout:
                rows.put(row)
        finally:
            rows.put(_END_OF_STREAM)

    reader = threading.Thread(target=read_rows, name="h3-bridge-ffprobe", daemon=True)
    reader.start()
    deadline = time.monotonic() + _COMMAND_TIMEOUT_SECONDS
    count = 0
    first_timestamp: int | None = None
    try:
        while True:
            _check_cancel(cancel_check)
            if time.monotonic() >= deadline:
                raise H3BridgeMediaError("video frame timestamp probe timed out")
            try:
                row = rows.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if row is _END_OF_STREAM:
                break
            # ffprobe's CSV writer may append frame side-data (for example
            # x264 SEI text) to a timestamp row even with nk=1. The timestamp
            # is the first field; the remaining CSV fields are not clock data.
            value = row.decode("ascii", "strict").split(",", 1)[0].strip()
            if not value:
                continue
            timestamp = int(value)
            if first_timestamp is None:
                first_timestamp = timestamp
            expected_ticks = Fraction(count, 1) / (fps * time_base)
            expected_tick = (
                2 * expected_ticks.numerator + expected_ticks.denominator
            ) // (2 * expected_ticks.denominator)
            if timestamp - first_timestamp != expected_tick:
                raise H3BridgeMediaError("variable-frame-rate video is unsupported")
            count += 1
            if count > max_frame_count:
                if max_frame_count < _MAX_FRAME_COUNT:
                    raise H3BridgeMediaError(
                        "source media exceeds the 30-minute duration limit"
                    )
                raise H3BridgeMediaError("video exceeds the supported frame bound")
        return_code = process.wait(timeout=5)
        if return_code != 0 or count == 0:
            raise H3BridgeMediaError("video frame timestamps are invalid")
        return count
    except H3BridgeMediaError:
        raise
    except (UnicodeError, ValueError, OSError, subprocess.SubprocessError):
        raise H3BridgeMediaError("video frame timestamps are invalid") from None
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.SubprocessError:
            pass
        process.stdout.close()
        reader.join(timeout=1)


def _round_half_up(value: Fraction) -> int:
    if value < 0:
        return -_round_half_up(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _probe(
    path: Path,
    *,
    cancel_check: CancelCheck | None,
    expected_sha256: str | None = None,
) -> SourceProbe:
    try:
        before_metadata = os.lstat(path)
    except OSError:
        raise H3BridgeMediaError("media source is unavailable") from None
    if not os.path.isfile(path) or os.path.islink(path) or not before_metadata.st_size:
        raise H3BridgeMediaError("media source must be a nonempty regular file")
    if before_metadata.st_size > _MAX_MEDIA_BYTES:
        raise H3BridgeMediaError("media file exceeds the 8 GiB size limit")

    ffmpeg, ffprobe = _tools()
    del ffmpeg
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        os.fspath(path),
    ]
    try:
        payload = json.loads(
            _run_command(
                command,
                stage="media probe",
                cancel_check=cancel_check,
                capture_stdout=True,
            ).decode("utf-8")
        )
    except (UnicodeError, json.JSONDecodeError):
        raise H3BridgeMediaError("media probe returned invalid metadata") from None
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        raise H3BridgeMediaError("media probe returned invalid streams")
    videos = [
        item
        for item in streams
        if isinstance(item, dict) and item.get("codec_type") == "video"
    ]
    audios = [
        item
        for item in streams
        if isinstance(item, dict) and item.get("codec_type") == "audio"
    ]
    if len(videos) != 1 or len(audios) > 1:
        raise H3BridgeMediaError(
            "H3 Bridge requires one video and at most one audio stream"
        )
    video = videos[0]
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    time_base = _fraction(video.get("time_base"))
    if fps is None or time_base is None:
        raise H3BridgeMediaError("video frame rate is ambiguous")
    if fps > _MAX_SOURCE_FPS:
        raise H3BridgeMediaError(
            "source frame rate exceeds the 240 fps admission limit"
        )
    declared_duration = _metadata_video_duration(video, payload.get("format"))
    _validate_declared_duration(declared_duration)
    media_format = payload.get("format")
    format_duration = (
        _fraction(media_format.get("duration"))
        if isinstance(media_format, dict)
        else None
    )
    if format_duration is not None:
        _validate_declared_duration(format_duration)
    width, height = video.get("width"), video.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise H3BridgeMediaError("video dimensions are invalid")
    if (
        width > _MAX_OUTPUT_DIMENSION
        or height > _MAX_OUTPUT_DIMENSION
        or width * height > _MAX_OUTPUT_PIXELS
    ):
        raise H3BridgeMediaError(
            "source dimensions exceed 4096 pixels per side or 12 megapixels"
        )
    audio_channels: int | None = None
    audio_duration: Fraction | None = None
    if audios:
        channels = audios[0].get("channels")
        if type(channels) is not int or channels not in (1, 2):
            raise H3BridgeMediaError("H3 Bridge supports mono or stereo source audio")
        audio_channels = channels
        audio_duration = _stream_duration(audios[0])

    try:
        after_metadata = os.lstat(path)
    except OSError:
        raise H3BridgeMediaError(
            "media source changed while metadata was being read"
        ) from None
    if _file_signature(before_metadata) != _file_signature(after_metadata):
        raise H3BridgeMediaError("media source changed while metadata was being read")
    digest = _hash_file(path, cancel_check)
    if expected_sha256 is not None and not hmac.compare_digest(digest, expected_sha256):
        raise H3BridgeMediaError("media source hash does not match the bridge plan")

    max_source_frames = min(
        _MAX_FRAME_COUNT,
        (_MAX_SOURCE_DURATION_SECONDS * fps.numerator) // fps.denominator,
    )
    frame_count = _verify_cfr_timestamps(
        ffprobe,
        path,
        fps=fps,
        time_base=time_base,
        cancel_check=cancel_check,
        max_frame_count=max_source_frames,
    )
    if frame_count > _MAX_FRAME_COUNT:
        raise H3BridgeMediaError("video exceeds the supported frame bound")
    frames_24fps = _round_half_up(Fraction(frame_count * H3_NATIVE_FPS, 1) / fps)
    if not 1 <= frames_24fps <= _MAX_FRAME_COUNT:
        raise H3BridgeMediaError(
            "normalized video frame count is outside the supported bound"
        )
    if (
        Fraction(frame_count, 1) / fps > _MAX_SOURCE_DURATION_SECONDS
        or Fraction(frames_24fps, H3_NATIVE_FPS) > _MAX_SOURCE_DURATION_SECONDS
    ):
        raise H3BridgeMediaError("source media exceeds the 30-minute duration limit")
    if not hmac.compare_digest(_hash_file(path, cancel_check), digest):
        raise H3BridgeMediaError("media source changed while it was being probed")
    return SourceProbe(
        sha256=digest,
        fps=fps,
        frame_count=frame_count,
        frames_24fps=frames_24fps,
        width=width,
        height=height,
        has_audio=bool(audios),
        audio_channels=audio_channels,
        audio_duration=audio_duration,
    )


def probe_source(
    path: str | os.PathLike[str], cancel_check: CancelCheck | None = None
) -> SourceProbe:
    """Hash and probe one local clip using a deterministic H3 24 fps mapping."""

    try:
        source_path = _path(path, label="source")
        return _probe(source_path, cancel_check=cancel_check)
    except H3BridgeMediaError:
        raise
    except Exception:  # noqa: BLE001 - keep probe failures path-free and actionable
        raise H3BridgeMediaError("media source could not be probed") from None


def _plan_source(plan: dict[str, Any], name: str) -> dict[str, Any]:
    source = plan["sources"][name]
    if not isinstance(source, dict) or not _SHA256.fullmatch(source.get("sha256", "")):
        raise H3BridgeMediaError("bridge plan source commitment is invalid")
    start = source.get("start_frame")
    end = source.get("end_frame_exclusive")
    count = source.get("frame_count")
    if type(start) is not int or type(end) is not int or type(count) is not int:
        raise H3BridgeMediaError("bridge plan source range is invalid")
    if start < 0 or end <= start or end - start != count:
        raise H3BridgeMediaError("bridge plan source range is invalid")
    return source


def _verify_source_range(source: dict[str, Any], probe: SourceProbe) -> None:
    start = source["start_frame"]
    end = source["end_frame_exclusive"]
    if end > probe.frames_24fps:
        raise H3BridgeMediaError(
            "bridge plan source range exceeds the normalized video"
        )
    if end - start < GUIDE_FRAMES:
        raise H3BridgeMediaError(
            "each H3 reference range must contain at least 56 frames"
        )


def _fps_filter() -> str:
    return f"fps=fps={H3_NATIVE_FPS}:round=near:start_time=0"


def _encode_guide(
    ffmpeg: str,
    source: Path,
    destination: Path,
    *,
    start_frame: int,
    end_frame: int,
    cancel_check: CancelCheck | None,
) -> None:
    video_filter = (
        f"setpts=PTS-STARTPTS,{_fps_filter()},"
        f"trim=start_frame={start_frame}:end_frame={end_frame},"
        f"setpts=N/({H3_NATIVE_FPS}*TB),format=yuv420p"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-hwaccel",
        "none",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-i",
        os.fspath(source),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        video_filter,
        "-frames:v",
        str(end_frame - start_frame),
        "-r",
        str(H3_NATIVE_FPS),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "12",
        "-pix_fmt",
        "yuv420p",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-movflags",
        "+faststart",
        os.fspath(destination),
    ]
    _run_command(command, stage="reference guide encoding", cancel_check=cancel_check)


def _guide_directory(temp_dir: str | os.PathLike[str]) -> Path:
    try:
        parent = Path(os.path.abspath(os.path.expanduser(os.fspath(temp_dir))))
        metadata = os.lstat(parent)
    except (TypeError, ValueError, OSError):
        raise H3BridgeMediaError("guide temporary directory is unavailable") from None
    if not os.path.isdir(parent) or os.path.islink(parent):
        raise H3BridgeMediaError("guide temporary directory must be a local directory")
    if not metadata.st_mode:
        raise H3BridgeMediaError("guide temporary directory is unavailable")
    try:
        return Path(tempfile.mkdtemp(prefix=".h3-bridge-guides-", dir=parent))
    except OSError:
        raise H3BridgeMediaError(
            "guide temporary directory could not be created"
        ) from None


def prepare_guides(
    a_path: str | os.PathLike[str],
    b_path: str | os.PathLike[str],
    plan: object,
    temp_dir: str | os.PathLike[str],
    cancel_check: CancelCheck | None = None,
) -> tuple[Path, Path]:
    """Write two 56-frame visual guides with exact plan ranges at their edges."""

    guide_root: Path | None = None
    try:
        validated = _validated_plan(plan)
        a_source = _plan_source(validated, "a_tail")
        b_source = _plan_source(validated, "b_head")
        a_file = _path(a_path, label="clip A")
        b_file = _path(b_path, label="clip B")
        a_probe = _probe(
            a_file, cancel_check=cancel_check, expected_sha256=a_source["sha256"]
        )
        b_probe = _probe(
            b_file, cancel_check=cancel_check, expected_sha256=b_source["sha256"]
        )
        validate_bridge_source_dimensions(a_probe, b_probe)
        _verify_source_range(a_source, a_probe)
        _verify_source_range(b_source, b_probe)
        guide_total = 2 * GUIDE_FRAMES
        if (
            not REFERENCE_MIN_FRAMES <= GUIDE_FRAMES <= REFERENCE_MAX_FRAMES
            or guide_total > REFERENCE_MAX_TOTAL_FRAMES
        ):
            raise H3BridgeMediaError(
                "H3 reference guide duration is outside the native limit"
            )
        _check_cancel(cancel_check)
        guide_root = _guide_directory(temp_dir)
        a_guide = guide_root / "a-tail.mp4"
        b_guide = guide_root / "b-head.mp4"
        ffmpeg, _ = _tools()
        _encode_guide(
            ffmpeg,
            a_file,
            a_guide,
            start_frame=a_source["end_frame_exclusive"] - GUIDE_FRAMES,
            end_frame=a_source["end_frame_exclusive"],
            cancel_check=cancel_check,
        )
        _encode_guide(
            ffmpeg,
            b_file,
            b_guide,
            start_frame=b_source["start_frame"],
            end_frame=b_source["start_frame"] + GUIDE_FRAMES,
            cancel_check=cancel_check,
        )
        a_guide_probe = _probe(a_guide, cancel_check=cancel_check)
        b_guide_probe = _probe(b_guide, cancel_check=cancel_check)
        if any(
            probe.frames_24fps != GUIDE_FRAMES
            or probe.fps != _FRAME_RATE
            or probe.has_audio
            for probe in (a_guide_probe, b_guide_probe)
        ):
            raise H3BridgeMediaError(
                "prepared guide media did not match its frame contract"
            )
        if not hmac.compare_digest(
            _hash_file(a_file, cancel_check), a_source["sha256"]
        ):
            raise H3BridgeMediaError(
                "clip A changed while its guide was being prepared"
            )
        if not hmac.compare_digest(
            _hash_file(b_file, cancel_check), b_source["sha256"]
        ):
            raise H3BridgeMediaError(
                "clip B changed while its guide was being prepared"
            )
        return a_guide, b_guide
    except H3BridgeMediaError:
        if guide_root is not None:
            shutil.rmtree(guide_root, ignore_errors=True)
        raise
    except Exception:  # noqa: BLE001 - keep conversion failures path-free
        if guide_root is not None:
            shutil.rmtree(guide_root, ignore_errors=True)
        raise H3BridgeMediaError("H3 reference guides could not be prepared") from None


def _seconds(frames: int) -> str:
    return f"{float(Fraction(frames, H3_NATIVE_FPS)):.12f}"


def _ticks_seconds(ticks: int) -> str:
    return f"{float(Fraction(ticks, H3_AUDIO_TICKS_PER_SECOND)):.12f}"


def _audio_segment_filter(
    input_index: int,
    *,
    label: str,
    source_has_audio: bool,
    duration_frames: int,
    start_frame: int = 0,
    use_generated_audio: bool = False,
    generated_start_tick: int | None = None,
    generated_end_tick: int | None = None,
) -> str:
    duration = _seconds(duration_frames)
    if not source_has_audio:
        return (
            f"anullsrc=r={_AUDIO_SAMPLE_RATE}:cl=stereo,"
            f"atrim=duration={duration},asetpts=PTS-STARTPTS[{label}]"
        )
    start = _seconds(start_frame)
    audio = f"[{input_index}:a:0]asetpts=PTS-STARTPTS,aresample={_AUDIO_SAMPLE_RATE},"
    audio += "aformat=sample_fmts=fltp:channel_layouts=stereo,"
    if use_generated_audio:
        if generated_start_tick is None or generated_end_tick is None:
            raise H3BridgeMediaError("generated audio trim boundaries are missing")
        audio += (
            f"atrim=start={_ticks_seconds(generated_start_tick)}:"
            f"end={_ticks_seconds(generated_end_tick)},"
        )
    else:
        audio += f"atrim=start={start}:duration={duration},"
    return audio + f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS[{label}]"


def _audio_policy(plan: dict[str, Any], generated: SourceProbe) -> str:
    audio = plan["audio"]
    mode = audio["bridge_mode"]
    if mode == "drive_track":
        raise H3BridgeMediaError(
            "drive-track audio requires an authorized track binding"
        )
    if mode not in {"generated", "silent"}:
        raise H3BridgeMediaError("bridge audio mode is unsupported")
    left, right = audio["left_seam"], audio["right_seam"]
    if (
        left["mode"] != "hard_cut"
        or left["owner"] != "clip_a"
        or left["overlap_audio_ticks"] != 0
        or right["mode"] != "hard_cut"
        or right["owner"] != "clip_b"
        or right["overlap_audio_ticks"] != 0
    ):
        raise H3BridgeMediaError("only clip-owned hard-cut audio seams are supported")
    if mode == "generated" and not generated.has_audio:
        raise H3BridgeMediaError("the generated bridge audio stream is missing")
    return mode


def _validate_dimensions(
    probes: tuple[SourceProbe, SourceProbe, SourceProbe],
) -> tuple[int, int]:
    for probe in probes:
        _validate_probe_dimensions(probe)
    width = max(probe.width for probe in probes)
    height = max(probe.height for probe in probes)
    width += width % 2
    height += height % 2
    if (
        width > _MAX_OUTPUT_DIMENSION
        or height > _MAX_OUTPUT_DIMENSION
        or width * height > _MAX_OUTPUT_PIXELS
    ):
        raise H3BridgeMediaError(
            "assembled output dimensions exceed 4096 pixels per side or 12 megapixels"
        )
    return width, height


def _video_segment_filter(
    input_index: int,
    *,
    label: str,
    start_frame: int,
    end_frame: int,
    width: int,
    height: int,
) -> str:
    video = f"[{input_index}:v:0]setpts=PTS-STARTPTS,{_fps_filter()},"
    video += f"trim=start_frame={start_frame}:end_frame={end_frame},"
    video += f"setpts=N/({H3_NATIVE_FPS}*TB),"
    video += (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format=yuv420p[{label}]"
    )
    return video


def _atomic_stage(temporary: Path, destination: Path) -> None:
    linked = False
    try:
        with temporary.open("rb") as encoded:
            os.fsync(encoded.fileno())
        os.link(temporary, destination)
        linked = True
        os.unlink(temporary)
        if os.name == "posix":
            descriptor = os.open(
                destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except FileExistsError:
        raise H3BridgeMediaError("staging output already exists") from None
    except OSError:
        try:
            if linked:
                destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise H3BridgeMediaError(
            "assembled media could not be staged atomically"
        ) from None


def assemble_bridge(
    a_path: str | os.PathLike[str],
    b_path: str | os.PathLike[str],
    generated_path: str | os.PathLike[str],
    plan: object,
    staging_path: str | os.PathLike[str],
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    """Assemble A through the published bridge interval and into B atomically.

    The caller retains ownership of the final output publish and its metadata.
    This function only creates ``staging_path`` atomically and returns a
    path-free media summary.
    """

    temporary: Path | None = None
    staged = False
    try:
        validated = _validated_plan(plan)
        a_source = _plan_source(validated, "a_tail")
        b_source = _plan_source(validated, "b_head")
        a_file = _path(a_path, label="clip A")
        b_file = _path(b_path, label="clip B")
        generated_file = _path(generated_path, label="generated bridge")
        destination = Path(os.path.abspath(os.path.expanduser(os.fspath(staging_path))))
        if not destination.suffix or not destination.parent.is_dir():
            raise H3BridgeMediaError("staging output path is invalid")
        if os.path.lexists(destination):
            raise H3BridgeMediaError("staging output already exists")
        for source_path in (a_file, b_file, generated_file):
            try:
                if destination.resolve(strict=False) == source_path.resolve(
                    strict=True
                ):
                    raise H3BridgeMediaError(
                        "staging output must differ from its inputs"
                    )
            except OSError:
                raise H3BridgeMediaError("staging output path is invalid") from None

        a_probe = _probe(
            a_file, cancel_check=cancel_check, expected_sha256=a_source["sha256"]
        )
        b_probe = _probe(
            b_file, cancel_check=cancel_check, expected_sha256=b_source["sha256"]
        )
        validate_bridge_source_dimensions(a_probe, b_probe)
        _verify_source_range(a_source, a_probe)
        _verify_source_range(b_source, b_probe)
        generated_probe = _probe(generated_file, cancel_check=cancel_check)
        generation = validated["generation"]
        generated_frames = generation["generated_frames"]
        if generated_probe.frames_24fps != generated_frames:
            raise H3BridgeMediaError(
                "generated bridge frame count does not match its plan"
            )
        bridge_audio_mode = _audio_policy(validated, generated_probe)
        published = generation["published_range"]
        published_start = published["start_frame"]
        published_end = published["end_frame_exclusive"]
        published_frames = published_end - published_start
        audio_start_tick = audio_tick_at_frame(published_start)
        audio_end_tick = audio_tick_at_frame(published_end)
        selected_audio_ticks = audio_end_tick - audio_start_tick
        if selected_audio_ticks != generation["published_audio_ticks"]:
            raise H3BridgeMediaError("bridge plan audio tick range is inconsistent")
        if bridge_audio_mode == "generated":
            if generated_probe.audio_duration is None:
                raise H3BridgeMediaError(
                    "generated bridge audio duration could not be verified"
                )
            selected_audio_end = Fraction(audio_end_tick, H3_AUDIO_TICKS_PER_SECOND)
            if generated_probe.audio_duration < selected_audio_end:
                raise H3BridgeMediaError(
                    "generated bridge audio does not cover the published tick range"
                )
        a_cut = a_source["end_frame_exclusive"]
        b_cut = b_source["start_frame"]
        a_frames = a_cut
        b_frames = b_probe.frames_24fps - b_cut
        if a_frames <= 0 or b_frames <= 0 or published_frames <= 0:
            raise H3BridgeMediaError(
                "assembled bridge ranges must contain video frames"
            )
        width, height = _validate_dimensions((a_probe, generated_probe, b_probe))
        audio_present = (
            a_probe.has_audio or b_probe.has_audio or bridge_audio_mode == "generated"
        )
        ffmpeg, _ = _tools()
        suffix = destination.suffix.lower()
        if suffix not in {".mp4", ".m4v", ".mov", ".mkv"}:
            raise H3BridgeMediaError("staging output container is unsupported")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.h3-",
            suffix=suffix,
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)

        video_filters = [
            _video_segment_filter(
                0,
                label="va",
                start_frame=0,
                end_frame=a_cut,
                width=width,
                height=height,
            ),
            _video_segment_filter(
                1,
                label="vb",
                start_frame=published_start,
                end_frame=published_end,
                width=width,
                height=height,
            ),
            _video_segment_filter(
                2,
                label="vc",
                start_frame=b_cut,
                end_frame=b_probe.frames_24fps,
                width=width,
                height=height,
            ),
            "[va][vb][vc]concat=n=3:v=1:a=0[vout]",
        ]
        if audio_present:
            video_filters.extend(
                [
                    _audio_segment_filter(
                        0,
                        label="aa",
                        source_has_audio=a_probe.has_audio,
                        duration_frames=a_frames,
                    ),
                    _audio_segment_filter(
                        1,
                        label="ab",
                        source_has_audio=bridge_audio_mode == "generated",
                        duration_frames=published_frames,
                        start_frame=published_start,
                        use_generated_audio=bridge_audio_mode == "generated",
                        generated_start_tick=audio_start_tick,
                        generated_end_tick=audio_end_tick,
                    ),
                    _audio_segment_filter(
                        2,
                        label="ac",
                        source_has_audio=b_probe.has_audio,
                        duration_frames=b_frames,
                        start_frame=b_cut,
                    ),
                    "[aa][ab][ac]concat=n=3:v=0:a=1[aout]",
                ]
            )
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-hwaccel",
            "none",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-filter_complex_threads",
            "1",
            "-i",
            os.fspath(a_file),
            "-i",
            os.fspath(generated_file),
            "-i",
            os.fspath(b_file),
            "-filter_complex",
            ";".join(video_filters),
            "-map",
            "[vout]",
        ]
        if audio_present:
            command.extend(["-map", "[aout]"])
        command.extend(
            [
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-r",
                str(H3_NATIVE_FPS),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-threads:v",
                "1",
            ]
        )
        if audio_present:
            command.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    str(_AUDIO_SAMPLE_RATE),
                    "-ac",
                    "2",
                ]
            )
        if suffix in {".mp4", ".m4v", ".mov"}:
            command.extend(["-movflags", "+faststart"])
        command.append(os.fspath(temporary))
        _run_command(command, stage="bridge assembly", cancel_check=cancel_check)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise H3BridgeMediaError("bridge assembly produced no media")

        # Bind the completed render to the same source bytes used for its plan.
        if not hmac.compare_digest(
            _hash_file(a_file, cancel_check), a_source["sha256"]
        ):
            raise H3BridgeMediaError("clip A changed during bridge assembly")
        if not hmac.compare_digest(
            _hash_file(b_file, cancel_check), b_source["sha256"]
        ):
            raise H3BridgeMediaError("clip B changed during bridge assembly")
        generated_sha256 = _hash_file(generated_file, cancel_check)
        if not hmac.compare_digest(generated_sha256, generated_probe.sha256):
            raise H3BridgeMediaError("generated bridge changed during assembly")

        output_probe = _probe(temporary, cancel_check=cancel_check)
        expected_frames = a_frames + published_frames + b_frames
        if (
            output_probe.fps != _FRAME_RATE
            or output_probe.frames_24fps != expected_frames
            or output_probe.has_audio != audio_present
        ):
            raise H3BridgeMediaError("assembled media failed its frame or audio check")
        output_sha256 = output_probe.sha256
        _check_cancel(cancel_check)
        _atomic_stage(temporary, destination)
        staged = True
        temporary = None
        return {
            "fps": H3_NATIVE_FPS,
            "frame_count": expected_frames,
            "clip_a_frames": a_frames,
            "bridge_published_frames": published_frames,
            "clip_b_frames": b_frames,
            "audio_present": audio_present,
            "bridge_audio_mode": bridge_audio_mode,
            "bridge_audio_ticks": generation["published_audio_ticks"],
            "bridge_audio_selected_start_tick": (
                audio_start_tick if bridge_audio_mode == "generated" else None
            ),
            "bridge_audio_selected_end_tick": (
                audio_end_tick if bridge_audio_mode == "generated" else None
            ),
            "bridge_audio_selected_ticks": (
                selected_audio_ticks if bridge_audio_mode == "generated" else 0
            ),
            "bridge_audio_video_duration_frames": published_frames,
            "bridge_audio_video_duration_fps": H3_NATIVE_FPS,
            "generated_sha256": generated_sha256,
            "output_sha256": output_sha256,
        }
    except H3BridgeMediaError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if staged:
            try:
                Path(
                    os.path.abspath(os.path.expanduser(os.fspath(staging_path)))
                ).unlink(missing_ok=True)
            except (OSError, TypeError, ValueError):
                pass
        raise
    except Exception:  # noqa: BLE001 - keep assembly failures path-free
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if staged:
            try:
                Path(
                    os.path.abspath(os.path.expanduser(os.fspath(staging_path)))
                ).unlink(missing_ok=True)
            except (OSError, TypeError, ValueError):
                pass
        raise H3BridgeMediaError("H3 bridge assembly could not be completed") from None
