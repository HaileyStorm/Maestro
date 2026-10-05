"""CPU rendering for non-destructive Editor cuts and video sequences.

The caller resolves and rechecks every project-owned source. This module only
encodes an MP4 into an existing staging directory and publishes that staged
file without replacing anything. The original source is never modified.
"""

from __future__ import annotations

import math
import os
import json
import re
import subprocess
from pathlib import Path
import tempfile
from typing import Callable

from shared.utils.media_encoder import run_encoder


def _image_filter(layer: dict, directory: Path, *, width: int, height: int,
                   duration: float, fps: float, first_input: int, base: str, index: int) -> tuple[list[str], list[str], str]:
    from services.editor_projects import inspect_editor_still
    from PIL import Image, ImageOps
    start = _seconds(layer["start"], allow_zero=True)
    length = _seconds(layer["duration"])
    size, opacity = layer["size"], layer["opacity"]
    if (type(width) is not int or type(height) is not int or not 64 <= width <= 7680 or not 64 <= height <= 4320
            or type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 120
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in (size, opacity))
            or not 0.1 <= size <= 1 or not 0 <= opacity <= 1
            or layer["position"] not in {"top", "center", "bottom"}
            or float(start) + float(length) > duration + 1e-6 or float(length) < 1 / fps - 1e-9):
        raise ValueError("Editor image canvas, appearance or time range is invalid")
    path = Path(os.path.abspath(os.fspath(layer["path"])))
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Editor image source is unavailable")
    media = inspect_editor_still(path)
    if (media["width"], media["height"]) != (layer["width"], layer["height"]):
        raise ValueError("Editor image dimensions changed")
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    ratio = min(width * 0.9 * size / image.width, height * 0.88 * size / image.height)
    image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
    image.putalpha(image.getchannel("A").point([round(alpha * opacity) for alpha in range(256)]))
    staged = directory / f"image-layer-{index}.png"
    image.save(staged)
    x = (width - image.width) // 2
    margin = round(height * 0.06)
    y = {"top": margin, "center": (height - image.height) // 2, "bottom": height - margin - image.height}[layer["position"]]
    end = _seconds(float(start) + float(length))
    return ["-i", str(staged)], [f"[{base}][{first_input}:v]overlay=x={x}:y={y}:"
        f"eof_action=repeat:enable='gte(t,{start})*lt(t,{end})'[image_v_{index}]"], f"image_v_{index}"


def editor_image_plans(value: list[dict] | dict | None) -> list[dict]:
    """Canonical ordered rows, including sealed jobs from the single-image release."""
    plans = [value] if isinstance(value, dict) else (value if value is not None else [])
    if not isinstance(plans, list) or len(plans) > 8 or any(not isinstance(item, dict) for item in plans):
        raise ValueError("Editor supports up to eight image layers")
    return plans

def _image_filters(layers: list[dict] | dict | None, directory: Path, *, width: int, height: int,
                   duration: float, fps: float, first_input: int, base: str) -> tuple[list[str], list[str], str]:
    plans = editor_image_plans(layers)
    inputs, filters, label = [], [], base
    for index, item in enumerate(plans):
        added, composed, label = _image_filter(item, directory, width=width, height=height,
            duration=duration, fps=fps, first_input=first_input + index, base=label, index=index)
        inputs.extend(added)
        filters.extend(composed)
    return inputs, filters, label

def _title_filters(layers: list[dict] | None, directory: Path, *, width: int, height: int,
                   duration: float, fps: float, first_input: int, base: str) -> tuple[list[str], list[str], str]:
    """Rasterize literal title text; only validated numbers enter FFmpeg filters."""
    from services.editor_projects import editor_text_layers
    from PIL import Image, ImageDraw, ImageFont

    plans = editor_text_layers({"tracks": [{"id": "titles-main", "type": "text", "items": layers or []}]})
    if not plans:
        return [], [], base
    if (type(width) is not int or type(height) is not int
            or not 64 <= width <= 7680 or not 64 <= height <= 4320
            or type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 120
            or any(item["start"] + item["duration"] > duration + 1e-6
                   or item["duration"] < 1 / fps - 1e-9 for item in plans)):
        raise ValueError("Editor title canvas or time range is invalid")
    font_path = Path(__file__).resolve().parents[2] / "ui/public/editor-fonts/DejaVuSans.ttf"
    inputs, filters = [], []
    label = base
    for item in plans:
        if not item["text"].strip():
            continue
        lines = item["text"].split("\n")
        size = max(1, round(height * 0.05))
        padding = max(1, round(height * 0.015))
        while True:
            font = ImageFont.truetype(str(font_path), size)
            text_width = max(font.getlength(line) for line in lines)
            if text_width + 2 * padding <= width * 0.9 or size == 1:
                break
            size -= 1
        line_height = max(1, round(size * 1.2))
        box_width = math.ceil(text_width) + 2 * padding
        box_height = len(lines) * line_height + 2 * padding
        image = Image.new("RGBA", (box_width, box_height), (0, 0, 0, round(255 * 0.6)))
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text(((box_width - font.getlength(line)) / 2, padding + index * line_height),
                      line, font=font, fill="white", anchor="lt")
        if box_width > width * 0.9:
            box_width = max(1, math.floor(width * 0.9))
            image = image.resize((box_width, box_height), Image.Resampling.LANCZOS)
        number = len(inputs) // 2
        path = directory / f"title-{number}.png"
        image.save(path)
        inputs += ["-i", str(path)]
        x = (width - box_width) // 2
        margin = round(height * 0.06)
        y = {"top": margin, "center": (height - box_height) // 2,
             "bottom": height - margin - box_height}[item["position"]]
        start, end = _seconds(item["start"], allow_zero=True), _seconds(item["start"] + item["duration"])
        output = f"title_v{number}"
        filters.append(f"[{label}][{first_input + number}:v]overlay=x={x}:y={y}:"
                       f"eof_action=repeat:enable='gte(t,{start})*lt(t,{end})'[{output}]")
        label = output
    return inputs, filters, label


def _seconds(value: object, *, allow_zero: bool = False) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Editor cut times must be finite numbers")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero) or number > 86400:
        raise ValueError("Editor cut time is outside the supported range")
    return f"{number:.9f}".rstrip("0").rstrip(".")


def _audio_mix_filters(layer: dict | None, *, duration: float, first_input: int,
                       bases: list[str], layouts: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Add a sample-timed finite bed without attenuating or losing source streams."""
    if layer is None:
        return [], [], []
    start = _seconds(layer["start"], allow_zero=True)
    source_in = _seconds(layer["source_in"], allow_zero=True)
    length = _seconds(layer["duration"])
    gain = layer["volume"]
    fades = [layer.get(key, 0.0) for key in ("fade_in", "fade_out")]
    if (type(layer["muted"]) is not bool or type(gain) not in (int, float)
            or not math.isfinite(gain) or not 0 <= gain <= 1
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   or not 0 <= value <= layer["duration"] for value in fades)
            or float(start) + float(length) > duration + 1e-6):
        raise ValueError("Editor audio interval, volume or fades are invalid")
    path = Path(os.path.abspath(os.fspath(layer["path"])))
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("Editor audio source is unavailable")
    if layer["muted"] or gain == 0:
        return [], [], []
    if not bases:
        bases, layouts = ["bed_silence"], ["stereo"]
        filters = [f"anullsrc=r=48000:cl=stereo,atrim=duration={_seconds(duration)}[bed_silence]"]
    else:
        filters = []
    if len(bases) != len(layouts) or any(not re.fullmatch(r"[A-Za-z0-9._()+-]{1,64}", layout) for layout in layouts):
        raise ValueError("Editor source audio layout is unavailable")
    inputs = ["-ss", source_in, "-t", length, "-i", str(path)]
    samples = round(float(start) * 48000)
    total_samples = round(duration * 48000)
    # Fades are relative to the trimmed source, before timeline delay. When
    # they overlap, the two linear gain ramps multiply. Zero skips the filter.
    envelope = ""
    for kind, value in zip(("in", "out"), fades):
        if value > 0:
            fade_samples = max(1, round(value * 48000))
            begin = 0 if kind == "in" else max(0, round(float(length) * 48000) - fade_samples)
            envelope += f"afade=t={kind}:ss={begin}:ns={fade_samples}:curve=tri,"
    filters.append(f"[{first_input}:a:0]asetpts=PTS-STARTPTS,aresample=48000,"
                   f"atrim=duration={length},{envelope}volume={gain},adelay={samples}S:all=1,"
                   f"apad,atrim=end_sample={total_samples},asplit={len(bases)}"
                   + "".join(f"[bed{index}]" for index in range(len(bases))))
    maps = []
    for index, (base, layout) in enumerate(zip(bases, layouts)):
        filters.append(f"[{base}]asetpts=PTS-STARTPTS,aresample=48000,"
                       f"aformat=sample_fmts=fltp:channel_layouts={layout},apad,"
                       f"atrim=end_sample={total_samples}[original{index}]")
        filters.append(f"[bed{index}]aformat=sample_fmts=fltp:channel_layouts={layout}[bed_layout{index}]")
        filters.append(f"[original{index}][bed_layout{index}]amix=inputs=2:duration=first:"
                       f"dropout_transition=0:normalize=0[mixed{index}]")
        maps += ["-map", f"[mixed{index}]"]
    return inputs, filters, maps


def _source_audio_layouts(source: Path) -> list[str]:
    result = subprocess.run([os.environ.get("FFPROBE_BINARY") or "ffprobe", "-v", "error",
                             "-select_streams", "a", "-show_entries", "stream=channel_layout,channels",
                             "-of", "json", str(source)], capture_output=True, text=True, timeout=60, check=True)
    streams = json.loads(result.stdout)["streams"]
    # Some PCM containers omit the speaker mask. Retain their channel count
    # using FFmpeg's standard default, as the existing AAC export does.
    return [item.get("channel_layout") or (f"{item['channels']}c" if type(item.get("channels")) is int
            and 1 <= item["channels"] <= 64 else "") for item in streams]


def render_single_source_cut(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_in: float,
    duration: float,
    text_layers: list[dict] | None = None,
    image_layer: list[dict] | dict | None = None,
    canvas: dict | None = None,
    audio_layer: dict | None = None,
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
    if canvas is not None:
        if (not isinstance(canvas, dict)
                or type(canvas.get("width")) is not int or type(canvas.get("height")) is not int
                or not 64 <= canvas["width"] <= 7680 or not 64 <= canvas["height"] <= 4320
                or canvas["width"] % 2 or canvas["height"] % 2
                or type(canvas.get("fps", 30)) not in (int, float)
                or not math.isfinite(canvas.get("fps", 30)) or not 1 <= canvas.get("fps", 30) <= 120):
            raise ValueError("Editor cut canvas is invalid")

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
    titles = tempfile.TemporaryDirectory(prefix=".editor-titles-", dir=destination_path.parent)
    try:
        image_inputs, image_filters, image_label = _image_filters(
            image_layer, Path(titles.name), width=(canvas or {}).get("width", 0),
            height=(canvas or {}).get("height", 0), duration=float(length), fps=(canvas or {}).get("fps", 30), first_input=1, base="cut",
        )
        title_inputs, title_filters, label = _title_filters(
            text_layers, Path(titles.name), width=(canvas or {}).get("width", 0),
            height=(canvas or {}).get("height", 0), duration=float(length), fps=(canvas or {}).get("fps", 30), first_input=1 + len(image_inputs) // 2, base=image_label,
        )
        video_options = (["-filter_complex_threads", "2", "-filter_complex",
                          ";".join([f"[0:v:0]setpts=PTS-STARTPTS,scale={canvas['width']}:{canvas['height']}:"
                                    "force_original_aspect_ratio=decrease:force_divisible_by=2,"
                                    f"pad={canvas['width']}:{canvas['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1[cut]",
                                    *image_filters, *title_filters]), "-map", f"[{label}]"]
                         if canvas is not None else ["-map", "0:v:0", "-vf", "setpts=PTS-STARTPTS"])
        layouts = _source_audio_layouts(source_path) if audio_layer and not audio_layer["muted"] and audio_layer["volume"] > 0 else []
        bed_inputs, bed_filters, audio_options = _audio_mix_filters(
            audio_layer, duration=float(length), first_input=1 + len(image_inputs) // 2 + len(title_inputs) // 2,
            bases=[f"0:a:{index}" for index in range(len(layouts))], layouts=layouts,
        )
        if bed_filters:
            if canvas is not None:
                video_options[3] += ";" + ";".join(bed_filters)
            else:
                video_options += ["-filter_complex_threads", "2", "-filter_complex", ";".join(bed_filters)]
        else:
            audio_options = ["-map", "0:a?", "-af", "asetpts=PTS-STARTPTS"]
        command = [
            os.environ.get("FFMPEG_BINARY") or "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            # Bound the source before the joined video/audio graph so decoded
            # frames beyond the cut cannot make FFmpeg finish mixed audio early.
            "-ss", start, "-t", length, "-i", str(source_path), *image_inputs, *title_inputs, *bed_inputs, "-t", length,
            *video_options, *audio_options,
            "-map_metadata", "-1", "-map_chapters", "-1",
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
        titles.cleanup()
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            if not published:
                # A failed cleanup must not mask the encoding/cancel error.
                pass


def editor_clip_frames(duration: float, fps: float) -> int:
    """Use the encoder's nine-decimal duration for its exact frame clock."""
    if type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError("Editor frame rate is invalid")
    return max(1, round(float(_seconds(duration)) * fps))


def render_video_sequence(
    clips: list[dict], destination: str | os.PathLike[str], *,
    width: int, height: int, fps: float,
    text_layers: list[dict] | None = None,
    image_layer: list[dict] | dict | None = None,
    audio_layer: dict | None = None,
    abort_check: Callable[[], object] | None = None,
    timeout: float = 3600, runner: Callable[..., int] | None = None,
) -> str:
    """CPU sequence with canvas fit, exact frame boundaries and padded audio.

    Each clip contributes its first audio stream, or silence when absent.
    Sources remain read-only; the caller seals and rechecks their identities.
    """
    if not isinstance(clips, list) or not 1 <= len(clips) <= 8:
        raise ValueError("Editor sequence needs one through eight clips")
    if (type(width) is not int or type(height) is not int
            or not 64 <= width <= 7680 or not 64 <= height <= 4320
            or width % 2 or height % 2
            or type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 120):
        raise ValueError("Editor sequence canvas is invalid")
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or (abort_check is not None and not callable(abort_check))
            or (runner is not None and not callable(runner))):
        raise ValueError("Editor sequence execution options are invalid")
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    if destination_path.suffix.lower() != ".mp4" or not destination_path.parent.is_dir():
        raise ValueError("Editor sequence needs an existing MP4 staging directory")
    if os.path.lexists(destination_path):
        raise FileExistsError("Editor export already exists")
    command = [os.environ.get("FFMPEG_BINARY") or "ffmpeg",
               "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    filters, concat = [], []
    total_frames = 0
    for index, clip in enumerate(clips):
        source_path = Path(os.path.abspath(os.fspath(clip["path"])))
        if not source_path.is_file() or source_path.is_symlink():
            raise FileNotFoundError("Editor source video is unavailable")
        if os.path.realpath(source_path) == os.path.realpath(destination_path):
            raise ValueError("Editor source and destination must differ")
        start, length = _seconds(clip["source_in"], allow_zero=True), _seconds(clip["duration"])
        if float(start) + float(length) > 86400 or type(clip.get("has_audio")) is not bool:
            raise ValueError("Editor sequence source range is invalid")
        frames = editor_clip_frames(clip["duration"], fps)
        total_frames += frames
        seconds = f"{frames / fps:.9f}"
        # Decode the range at the input, then give every stream an exact local
        # clock before concat. Pads hold only the final fractional frame.
        command += ["-ss", start, "-t", length, "-i", str(source_path)]
        filters.append(
            f"[{index}:v:0]setpts=PTS-STARTPTS,fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration={1 / fps:.9f},trim=end_frame={frames},"
            f"settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
        )
        if clip["has_audio"]:
            audio = f"[{index}:a:0]asetpts=PTS-STARTPTS,aresample=48000"
        else:
            audio = "anullsrc=r=48000:cl=stereo"
        filters.append(f"{audio},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"apad,atrim=duration={seconds},asetpts=PTS-STARTPTS[a{index}]")
        concat.append(f"[v{index}][a{index}]")
    if total_frames / fps > 86400:
        raise ValueError("Editor sequence exceeds the supported duration")
    filters.append("".join(concat) + f"concat=n={len(clips)}:v=1:a=1[v][a]")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".editor-sequence-", suffix=".mp4", dir=destination_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    titles = tempfile.TemporaryDirectory(prefix=".editor-titles-", dir=destination_path.parent)
    try:
        image_inputs, image_filters, image_label = _image_filters(
            image_layer, Path(titles.name), width=width, height=height,
            duration=total_frames / fps, fps=fps, first_input=len(clips), base="v",
        )
        command += image_inputs
        filters += image_filters
        title_inputs, title_filters, label = _title_filters(
            text_layers, Path(titles.name), width=width, height=height,
            duration=total_frames / fps, fps=fps, first_input=len(clips) + len(image_inputs) // 2, base=image_label,
        )
        command += title_inputs
        filters += title_filters
        bed_inputs, bed_filters, audio_maps = _audio_mix_filters(
            audio_layer, duration=total_frames / fps, first_input=len(clips) + len(image_inputs) // 2 + len(title_inputs) // 2,
            bases=["a"], layouts=["stereo"],
        )
        command += bed_inputs
        filters += bed_filters
        command += ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters),
                    "-map", f"[{label}]", *(audio_maps or ["-map", "[a]"]), "-map_metadata", "-1", "-map_chapters", "-1",
                    "-threads", "2", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(temporary)]
        if (runner or run_encoder)(command, timeout=float(timeout), abort_check=abort_check) != 0:
            raise RuntimeError("Editor sequence encoding failed")
        if abort_check is not None and abort_check():
            raise InterruptedError("Editor sequence was cancelled")
        if temporary.stat().st_size <= 0:
            raise RuntimeError("Editor sequence encoding produced no media")
        os.link(temporary, destination_path)
        return str(destination_path)
    finally:
        titles.cleanup()
        try:
            temporary.unlink()
        except OSError:
            pass


__all__ = ["render_single_source_cut", "render_video_sequence", "editor_clip_frames"]
