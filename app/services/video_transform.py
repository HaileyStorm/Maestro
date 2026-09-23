"""CPU-only transforms for user-owned video outputs.

The transform entry points in this module deliberately stay outside the model
and generation paths.  They operate on an already published source, encode to
a sibling temporary file through the shared owned-process runner, and publish
only after the complete file is ready.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from shared.utils.media_encoder import run_encoder


DEFAULT_TIMEOUT_SECONDS = 3600.0
_Runner = Callable[..., int]
_AbortCheck = Callable[[], object]


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
            except OSError:
                # Preserve the original encoder/cancellation error.  A stale
                # sibling is still harmless and remains visibly recoverable.
                pass


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "horizontal_flip"]
