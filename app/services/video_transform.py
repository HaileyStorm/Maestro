"""CPU-only transforms for user-owned video outputs.

The transform entry points in this module deliberately stay outside the model
and generation paths.  They operate on an already published source, encode to
a sibling temporary file through the shared owned-process runner, and publish
only after the complete file is ready.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from shared.utils.media_encoder import run_encoder

DEFAULT_TIMEOUT_SECONDS = 3600.0
BROWSER_COPY_MAX_INPUT_BYTES = 2 * 1024**3
BROWSER_COPY_MAX_DURATION_SECONDS = 600.0
BROWSER_COPY_MAX_WIDTH = 4096
BROWSER_COPY_MAX_HEIGHT = 4096
BROWSER_COPY_MAX_PIXELS = 10_000_000
BROWSER_COPY_MAX_FRAME_RATE = 60.0
BROWSER_COPY_MAX_OUTPUT_BYTES = 1024**3
BROWSER_COPY_MIN_FREE_BYTES = 64 * 1024**2
BROWSER_COPY_TIMEOUT_SECONDS = 1200.0
BROWSER_COPY_THREADS = 2
_BROWSER_COPY_PROBE_TIMEOUT_SECONDS = 30.0
_BROWSER_COPY_PROBE_POLL_SECONDS = 0.1
_BROWSER_COPY_PROBE_KILL_WAIT_SECONDS = 0.5
_BROWSER_COPY_PROBE_OUTPUT_BYTES = 1024 * 1024
_BROWSER_COPY_OUTPUT_BYTES_PER_SECOND = 1_500_000
_BROWSER_COPY_OUTPUT_OVERHEAD_BYTES = 64 * 1024**2
_Runner = Callable[..., int]
_AbortCheck = Callable[[], object]


class BrowserCopyError(ValueError):
    """A browser-compatible copy could not be safely prepared."""


class BrowserCopyLimitError(BrowserCopyError):
    """The selected video exceeds a fixed browser-copy bound."""


class BrowserCopySpaceError(BrowserCopyError):
    """The project filesystem cannot safely hold the browser-copy result."""


def _validate_timeout(timeout: object) -> float:
    if isinstance(timeout, bool):
        raise ValueError("timeout must be a finite positive number")
    try:
        value = float(timeout)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout must be a finite positive number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number")
    return value


def _path(value: str | os.PathLike[str], *, label: str) -> Path:
    try:
        raw = os.fspath(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a path-like string") from None
    if raw in ("", b""):
        raise ValueError(f"{label} must not be empty")
    try:
        candidate = Path(raw).expanduser()
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a path-like string") from None
    return Path(os.path.abspath(candidate))


def _video_codec(destination: Path) -> str:
    # WebM cannot carry H.264, while all normal browser delivery containers
    # used by Maestro can.  The caller chooses the destination suffix; no
    # source media is rewritten in place.
    return "libvpx-vp9" if destination.suffix.lower() == ".webm" else "libx264"


def _build_command(ffmpeg: str, source: Path, temporary: Path) -> list[str]:
    destination_suffix = temporary.suffix.lower()
    video_codec = _video_codec(temporary)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-vf",
        "hflip",
        # Keep the source time base/frame timestamps instead of asking ffmpeg
        # to synthesize a constant-rate stream, and keep this CPU helper from
        # consuming every host thread during a long review export.
        "-fps_mode",
        "passthrough",
        "-threads",
        "2",
        "-c:v",
        video_codec,
        "-c:a",
        "copy",
    ]
    if video_codec == "libx264":
        command[command.index("-c:v") + 2 : command.index("-c:v") + 2] = [
            "-preset",
            "medium",
            "-crf",
            "18",
        ]
    else:
        command.extend(["-deadline", "good", "-cpu-used", "2", "-crf", "30"])
    if destination_suffix in {".mp4", ".m4v", ".mov"}:
        command.extend(["-movflags", "+faststart"])
    command.append(str(temporary))
    return command


def _publish_without_overwrite(temporary: Path, destination: Path) -> None:
    """Atomically create ``destination`` while refusing an existing path.

    A same-directory hard-link is an atomic create-if-absent operation on the
    local filesystems used by Maestro.  Removing the temporary name after the
    link leaves the fully encoded inode at the requested destination and never
    gives ``os.replace`` permission to clobber a concurrent output.
    """

    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise FileExistsError("destination already exists") from None
    except OSError as error:
        raise RuntimeError("transformed video could not be published atomically") from error
    os.unlink(temporary)


def horizontal_flip(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    abort_check: _AbortCheck | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner: _Runner | None = None,
) -> str:
    """Write a horizontally flipped copy of ``source`` to ``destination``.

    The first video stream is filtered with CPU ``hflip`` and every optional
    audio stream is mapped and stream-copied unchanged.  Metadata and chapters
    are copied where the destination container supports them.  ``destination``
    must not exist, and ``source`` is never modified.  ``runner`` is an
    injectable replacement for :func:`run_encoder` with the same keyword
    arguments, useful for cancellation/failure tests.
    """

    timeout_value = _validate_timeout(timeout)
    if abort_check is not None and not callable(abort_check):
        raise TypeError("abort_check must be callable")
    if runner is not None and not callable(runner):
        raise TypeError("runner must be callable")

    source_path = _path(source, label="source")
    destination_path = _path(destination, label="destination")
    if not source_path.is_file():
        raise FileNotFoundError("source video does not exist")
    if not destination_path.parent.is_dir():
        raise FileNotFoundError("destination directory does not exist")
    if os.path.realpath(source_path) == os.path.realpath(destination_path):
        raise ValueError("source and destination must be different")
    if os.path.lexists(destination_path):
        raise FileExistsError("destination already exists")

    suffix = destination_path.suffix or source_path.suffix or ".mkv"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".horizontal-flip-",
        suffix=suffix,
        dir=str(destination_path.parent),
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    published = False
    try:
        command = _build_command(
            os.environ.get("FFMPEG_BINARY") or "ffmpeg",
            source_path,
            temporary_path,
        )
        encode = run_encoder if runner is None else runner
        result = encode(command, timeout=timeout_value, abort_check=abort_check)
        if result != 0:
            raise RuntimeError("horizontal flip encoding failed")
        try:
            encoded_size = temporary_path.stat().st_size
        except OSError:
            encoded_size = 0
        if encoded_size <= 0:
            raise RuntimeError("horizontal flip encoding produced no output")
        _publish_without_overwrite(temporary_path, destination_path)
        published = True
        return str(destination_path)
    finally:
        if not published:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _stop_browser_copy_probe(process) -> None:
    """Kill a still-running ffprobe and wait briefly for process cleanup."""

    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_BROWSER_COPY_PROBE_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    stream = getattr(process, "stdout", None)
    if stream is not None:
        try:
            stream.close()
        except OSError:
            pass


def _probe_browser_copy_media(
    path: Path,
    *,
    abort_check: _AbortCheck | None = None,
) -> dict[str, object]:
    """Read only technical stream properties with bounded ffprobe time/output."""

    try:
        info = os.lstat(path)
    except OSError:
        raise BrowserCopyError("The selected video is unavailable.") from None
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise BrowserCopyError("The selected video is unavailable.")
    if info.st_size > BROWSER_COPY_MAX_INPUT_BYTES:
        raise BrowserCopyLimitError("The selected video exceeds the browser-copy size limit.")

    command = [
        os.environ.get("FFPROBE_BINARY") or "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,duration:format=duration,format_name",
        "-of",
        "json",
        str(path),
    ]
    if abort_check is not None and abort_check():
        raise InterruptedError("Encoder operation cancelled")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise BrowserCopyError("The selected video could not be inspected.") from None
    deadline = time.monotonic() + _BROWSER_COPY_PROBE_TIMEOUT_SECONDS
    try:
        while True:
            if abort_check is not None and abort_check():
                raise InterruptedError("Encoder operation cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrowserCopyError("The selected video could not be inspected.")
            try:
                stdout, _ = process.communicate(
                    timeout=min(remaining, _BROWSER_COPY_PROBE_POLL_SECONDS),
                )
                break
            except subprocess.TimeoutExpired:
                continue
    except InterruptedError:
        _stop_browser_copy_probe(process)
        raise
    except BrowserCopyError:
        _stop_browser_copy_probe(process)
        raise
    except OSError:
        _stop_browser_copy_probe(process)
        raise BrowserCopyError("The selected video could not be inspected.") from None
    if process.returncode != 0 or len(stdout) > _BROWSER_COPY_PROBE_OUTPUT_BYTES:
        raise BrowserCopyError("The selected video could not be inspected.")
    try:
        payload = json.loads(stdout)
        streams = payload.get("streams")
        format_info = payload.get("format")
        if not isinstance(streams, list) or not isinstance(format_info, dict):
            raise TypeError
        video = next(
            (stream for stream in streams
             if isinstance(stream, dict) and stream.get("codec_type") == "video"),
            None,
        )
        if not isinstance(video, dict):
            raise TypeError
        try:
            duration = float(format_info.get("duration") or 0.0)
        except (TypeError, ValueError, OverflowError):
            duration = 0.0
        if not math.isfinite(duration) or duration <= 0:
            try:
                duration = float(video.get("duration") or 0.0)
            except (TypeError, ValueError, OverflowError):
                duration = 0.0
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        audio_codecs = tuple(
            str(stream.get("codec_name") or "")
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        )
        rate = str(video.get("avg_frame_rate") or "0/1")
        numerator, denominator = rate.split("/", 1)
        frame_rate = float(numerator) / float(denominator)
        format_names = str(format_info.get("format_name") or "")
    except (TypeError, ValueError, OverflowError, ZeroDivisionError, json.JSONDecodeError):
        raise BrowserCopyError("The selected video could not be inspected.") from None
    if (
        not math.isfinite(duration)
        or duration <= 0
        or width <= 0
        or height <= 0
        or not math.isfinite(frame_rate)
        or frame_rate <= 0
    ):
        raise BrowserCopyError("The selected video has invalid media properties.")
    if (
        width > BROWSER_COPY_MAX_WIDTH
        or height > BROWSER_COPY_MAX_HEIGHT
        or width * height > BROWSER_COPY_MAX_PIXELS
        or frame_rate > BROWSER_COPY_MAX_FRAME_RATE
    ):
        raise BrowserCopyLimitError("The selected video exceeds the browser-copy resolution or frame-rate limit.")
    return {
        "size": info.st_size,
        "duration": duration,
        "width": width,
        "height": height,
        "frame_rate": frame_rate,
        "audio_codecs": audio_codecs,
        "format_names": format_names,
        "video_codec": str(video.get("codec_name") or ""),
        "pixel_format": str(video.get("pix_fmt") or ""),
    }


def _browser_copy_output_limit(duration: float) -> int:
    estimated = math.ceil(duration * _BROWSER_COPY_OUTPUT_BYTES_PER_SECOND)
    estimated += _BROWSER_COPY_OUTPUT_OVERHEAD_BYTES
    return min(BROWSER_COPY_MAX_OUTPUT_BYTES, estimated)


def validate_browser_copy(
    source: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    *,
    abort_check: _AbortCheck | None = None,
) -> dict[str, object]:
    """Validate a Gallery source and its bounded CPU-copy disk budget."""

    if abort_check is not None and not callable(abort_check):
        raise TypeError("abort_check must be callable")
    source_path = _path(source, label="source")
    summary = _probe_browser_copy_media(source_path, abort_check=abort_check)
    duration = float(summary["duration"])
    if duration > BROWSER_COPY_MAX_DURATION_SECONDS:
        raise BrowserCopyLimitError("The selected video exceeds the browser-copy duration limit.")
    output_dir = _path(output_directory, label="output directory")
    if not output_dir.is_dir():
        raise BrowserCopySpaceError("The project output location is unavailable.")
    output_limit = _browser_copy_output_limit(duration)
    try:
        free_bytes = shutil.disk_usage(output_dir).free
    except OSError:
        raise BrowserCopySpaceError("Project disk space could not be checked.") from None
    if free_bytes < output_limit + BROWSER_COPY_MIN_FREE_BYTES:
        raise BrowserCopySpaceError("There is not enough free project disk space for this copy.")
    return {**summary, "output_limit_bytes": output_limit}


def _browser_copy_command(
    ffmpeg: str,
    source: Path,
    temporary: Path,
    *,
    output_limit: int,
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-threads:v",
        str(BROWSER_COPY_THREADS),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-maxrate",
        "10M",
        "-bufsize",
        "20M",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-threads:a",
        "1",
        "-movflags",
        "+faststart",
        "-fs",
        str(output_limit),
        "-f",
        "mp4",
        str(temporary),
    ]


def browser_compatible_copy(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    abort_check: _AbortCheck | None = None,
    timeout: float = BROWSER_COPY_TIMEOUT_SECONDS,
    runner: _Runner | None = None,
) -> str:
    """Create a bounded H.264/yuv420p/AAC MP4 without changing ``source``."""

    timeout_value = _validate_timeout(timeout)
    if abort_check is not None and not callable(abort_check):
        raise TypeError("abort_check must be callable")
    if runner is not None and not callable(runner):
        raise TypeError("runner must be callable")

    source_path = _path(source, label="source")
    destination_path = _path(destination, label="destination")
    if destination_path.suffix.lower() != ".mp4":
        raise BrowserCopyError("Browser-compatible copies must use the MP4 format.")
    if os.path.realpath(source_path) == os.path.realpath(destination_path):
        raise BrowserCopyError("The source and destination must be different.")
    if os.path.lexists(destination_path):
        raise FileExistsError("destination already exists")
    if not destination_path.parent.is_dir():
        raise BrowserCopySpaceError("The project output location is unavailable.")

    source_info = validate_browser_copy(
        source_path,
        destination_path.parent,
        abort_check=abort_check,
    )
    output_limit = int(source_info["output_limit_bytes"])
    if abort_check is not None and abort_check():
        raise InterruptedError("Encoder operation cancelled")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".browser-copy-",
        suffix=".mp4",
        dir=str(destination_path.parent),
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    published = False
    try:
        command = _browser_copy_command(
            os.environ.get("FFMPEG_BINARY") or "ffmpeg",
            source_path,
            temporary_path,
            output_limit=output_limit,
        )
        encode = run_encoder if runner is None else runner
        result = encode(command, timeout=timeout_value, abort_check=abort_check)
        if result != 0:
            raise BrowserCopyError("Browser-compatible video encoding failed.")
        if abort_check is not None and abort_check():
            raise InterruptedError("Encoder operation cancelled")
        try:
            output_size = temporary_path.stat().st_size
        except OSError:
            output_size = 0
        if output_size <= 0 or output_size > output_limit:
            raise BrowserCopyLimitError("The browser-compatible video exceeded its output limit.")

        output_info = _probe_browser_copy_media(
            temporary_path,
            abort_check=abort_check,
        )
        audio_codecs = output_info["audio_codecs"]
        output_formats = set(str(output_info["format_names"]).split(","))
        if (
            not output_formats.intersection({"mov", "mp4"})
            or output_info["video_codec"] != "h264"
            or output_info["pixel_format"] != "yuv420p"
            or len(audio_codecs) != len(source_info["audio_codecs"])
            or any(codec != "aac" for codec in audio_codecs)
        ):
            raise BrowserCopyError("The browser-compatible video did not pass media verification.")
        source_duration = float(source_info["duration"])
        fps = float(source_info["frame_rate"])
        duration_tolerance = max(0.1, min(1.0, 2.0 / fps if fps > 0 else 0.5))
        if abs(float(output_info["duration"]) - source_duration) > duration_tolerance:
            raise BrowserCopyError("The browser-compatible video did not retain its duration.")

        _publish_without_overwrite(temporary_path, destination_path)
        published = True
        return str(destination_path)
    finally:
        if not published:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # Preserve the original encoder/cancellation error.  A stale
                # sibling is still harmless and remains visibly recoverable.
                pass


__all__ = [
    "BROWSER_COPY_MAX_DURATION_SECONDS",
    "BROWSER_COPY_MAX_FRAME_RATE",
    "BROWSER_COPY_MAX_HEIGHT",
    "BROWSER_COPY_MAX_INPUT_BYTES",
    "BROWSER_COPY_MAX_OUTPUT_BYTES",
    "BROWSER_COPY_MAX_PIXELS",
    "BROWSER_COPY_MAX_WIDTH",
    "BROWSER_COPY_MIN_FREE_BYTES",
    "BROWSER_COPY_THREADS",
    "BROWSER_COPY_TIMEOUT_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "BrowserCopyError",
    "BrowserCopyLimitError",
    "BrowserCopySpaceError",
    "browser_compatible_copy",
    "horizontal_flip",
    "validate_browser_copy",
]
