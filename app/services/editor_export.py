"""CPU rendering for a single-source, non-destructive Editor cut.

The caller resolves and rechecks the project-owned source. This module only
encodes an MP4 into an existing staging directory and publishes that staged
file without replacing anything. The original source is never modified.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile
from typing import Callable

from shared.utils.media_encoder import run_encoder


def _seconds(value: object, *, allow_zero: bool = False) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Editor cut times must be finite numbers")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero) or number > 86400:
        raise ValueError("Editor cut time is outside the supported range")
    return f"{number:.9f}".rstrip("0").rstrip(".")


def render_single_source_cut(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_in: float,
    duration: float,
    abort_check: Callable[[], object] | None = None,
    timeout: float = 3600,
    runner: Callable[..., int] | None = None,
) -> str:
    """Encode an H.264/AAC MP4 cut with zero-based video and audio timestamps.

    Accurate input seeking decodes through the requested start. Video is
    re-encoded so the cut need not land on a source keyframe; all audio streams
    are re-encoded to AAC, starting at the same timeline zero. The video end
    may round to one source frame. A temporary sibling is linked into place
    only after encoding succeeds, and an existing destination is never changed.
    """
    start = _seconds(source_in, allow_zero=True)
    length = _seconds(duration)
    if float(length) < 1 / 240:
        raise ValueError("Editor cut is shorter than one supported frame")
    if float(start) + float(length) > 86400:
        raise ValueError("Editor cut exceeds the supported duration")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Editor export timeout must be positive")
    if abort_check is not None and not callable(abort_check):
        raise TypeError("abort_check must be callable")
    if runner is not None and not callable(runner):
        raise TypeError("runner must be callable")

    source_path = Path(os.path.abspath(os.fspath(source)))
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    if not source_path.is_file() or source_path.is_symlink():
        raise FileNotFoundError("Editor source video is unavailable")
    if destination_path.suffix.lower() != ".mp4":
        raise ValueError("Editor exports use MP4")
    if not destination_path.parent.is_dir():
        raise FileNotFoundError("Editor staging directory is unavailable")
    if os.path.realpath(source_path) == os.path.realpath(destination_path):
        raise ValueError("Editor source and destination must differ")
    if os.path.lexists(destination_path):
        raise FileExistsError("Editor export already exists")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".editor-cut-", suffix=".mp4", dir=destination_path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    published = False
    try:
        command = [
            os.environ.get("FFMPEG_BINARY") or "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", start, "-i", str(source_path), "-t", length,
            "-map", "0:v:0", "-map", "0:a?",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-vf", "setpts=PTS-STARTPTS", "-af", "asetpts=PTS-STARTPTS",
            "-fps_mode", "passthrough", "-threads", "2",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(temporary),
        ]
        encode = runner or run_encoder
        if encode(command, timeout=float(timeout), abort_check=abort_check) != 0:
            raise RuntimeError("Editor cut encoding failed")
        if abort_check is not None and abort_check():
            raise InterruptedError("Editor cut was cancelled")
        if temporary.stat().st_size <= 0:
            raise RuntimeError("Editor cut encoding produced no media")
        os.link(temporary, destination_path)
        published = True
        return str(destination_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            if not published:
                # A failed cleanup must not mask the encoding/cancel error.
                pass


__all__ = ["render_single_source_cut"]
