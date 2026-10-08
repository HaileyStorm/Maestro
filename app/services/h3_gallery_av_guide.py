"""CPU Gallery interval media; authorization and native admission stay upstream.

Records contain exactly GALLERY_AV_SOURCE_FIELDS. Bindings contain sources,
target_frames and plan_sha256; neither representation contains resolved paths.
Video contributes RGB only. Audio contributes stereo 32 kHz float32 only.
Counts come from full deterministic conversion, not container duration estimates.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time

from services.h3_guide_plan import plan_h3_guide_inputs, validate_h3_guide_plan

MAX_ENCODED_BYTES = 64 * 1024**2
MAX_TOTAL_ENCODED_BYTES = 8 * MAX_ENCODED_BYTES
MAX_DECODED_BYTES = 2 * 1024**3
MAX_FRAMES = 345
MAX_SAMPLES = 15 * 32000
MAX_PIXELS = 16 * 1024**2
CHILD_SECONDS = 30
GALLERY_AV_SOURCE_FIELDS = frozenset({
    "workspace", "name", "revision", "kind", "sha256", "size", "width",
    "height", "frame_count", "sample_count", "frame_index", "source_private", "source_explicit",
})
_DEMUXERS = {".mp4": "mov", ".mov": "mov", ".mkv": "matroska", ".webm": "matroska",
             ".wav": "wav", ".flac": "flac", ".mp3": "mp3", ".m4a": "mov"}
_EXTENSIONS = {"video": {".mp4", ".mov", ".mkv", ".webm"},
               "audio": {".wav", ".flac", ".mp3", ".m4a"}}
_CODECS = {"video": {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "prores"},
           "audio": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "aac", "mp3", "flac", "opus", "vorbis"}}


class H3GalleryAVGuideError(ValueError):
    """Invalid or unavailable private guide media; messages omit source paths."""


class H3GalleryAVGuideCancelled(H3GalleryAVGuideError):
    """The owned CPU media operation stopped and its children were reaped."""


@dataclass(frozen=True)
class GalleryAVProbe:
    sha256: str
    size: int
    kind: str
    width: int
    height: int
    frame_count: int
    sample_count: int


def _check(cancel_check):
    try:
        stopped = cancel_check is not None and cancel_check()
    except InterruptedError:
        stopped = True
    except Exception:
        raise H3GalleryAVGuideError("Guide cancellation status is unavailable") from None
    if stopped:
        raise H3GalleryAVGuideCancelled("Guide media operation cancelled")


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_nlink)


@contextmanager
def _snapshot(path, kind, cancel_check):
    if type(path) is not str or not path or "\0" in path or type(kind) is not str or kind not in _EXTENSIONS:
        raise H3GalleryAVGuideError("Guide media type is invalid")
    extension = Path(path).suffix.lower()
    if extension not in _EXTENSIONS[kind]:
        raise H3GalleryAVGuideError("Guide media extension is unsupported")
    _check(cancel_check)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_ENCODED_BYTES
                or _identity(before) != _identity(os.lstat(path))):
            raise H3GalleryAVGuideError("Guide media is not a bounded regular file")
        with tempfile.TemporaryDirectory(prefix="maestro-h3-av-") as directory:
            target = Path(directory) / ("source" + extension)
            digest, size, deadline = hashlib.sha256(), 0, time.monotonic() + CHILD_SECONDS
            with target.open("xb") as output:
                os.chmod(target, 0o600)
                while True:
                    _check(cancel_check)
                    if time.monotonic() > deadline:
                        raise H3GalleryAVGuideError("Guide snapshot timed out")
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > before.st_size:
                        raise H3GalleryAVGuideError("Guide media changed while copying")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if (size != before.st_size or _identity(os.fstat(descriptor)) != _identity(before)
                    or _identity(os.lstat(path)) != _identity(before)):
                raise H3GalleryAVGuideError("Guide media changed while copying")
            os.chmod(target, 0o400)
            # All children finish before this context removes the owned snapshot.
            yield target, "sha256:" + digest.hexdigest(), size
    except OSError:
        raise H3GalleryAVGuideError("Guide media is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _input(snapshot):
    demuxer = _DEMUXERS[snapshot.suffix]
    options = ["-protocol_whitelist", "file", "-format_whitelist", demuxer, "-f", demuxer]
    # MOV otherwise permits sample data in another local file. Ordinary
    # moov-at-end files remain seekable in the private snapshot.
    if demuxer == "mov":
        options += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return options + ["-i", str(snapshot)]


def _stream(command, limit, consume, cancel_check):
    _check(cancel_check)
    process = None
    reader = None
    stopped = threading.Event()
    chunks = queue.Queue(maxsize=8)
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True)
        def read():
            try:
                while not stopped.is_set():
                    chunk = process.stdout.read(65536)
                    while not stopped.is_set():
                        try:
                            chunks.put(chunk, timeout=0.05)
                            break
                        except queue.Full:
                            pass
                    if not chunk:
                        break
            finally:
                process.stdout.close()
        reader = threading.Thread(target=read, name="h3-av-pipe", daemon=False)
        reader.start()
        count, deadline = 0, time.monotonic() + CHILD_SECONDS
        while True:
            _check(cancel_check)
            if time.monotonic() > deadline:
                raise H3GalleryAVGuideError("Guide decoder timed out")
            try:
                chunk = chunks.get(timeout=0.05)
            except queue.Empty:
                if not reader.is_alive():
                    raise H3GalleryAVGuideError("Guide decoder pipe failed")
                continue
            if not chunk:
                break
            count += len(chunk)
            if count > limit:
                raise H3GalleryAVGuideError("Guide decoded media exceeds its limit")
            consume(chunk)
        # EOF can precede decoder exit. Keep Stop effective during teardown.
        while True:
            _check(cancel_check)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise H3GalleryAVGuideError("Guide decoder timed out")
            try:
                returncode = process.wait(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        _check(cancel_check)
        if returncode != 0:
            raise H3GalleryAVGuideError("Guide media cannot be decoded")
        return count
    except (OSError, subprocess.TimeoutExpired):
        raise H3GalleryAVGuideError("Guide decoder is unavailable") from None
    finally:
        stopped.set()
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
            if reader is not None and reader.ident is not None:
                reader.join()
            elif process.stdout is not None:
                process.stdout.close()


def _inspect(snapshot, kind, cancel_check):
    binary = shutil.which("ffprobe")
    if not binary:
        raise H3GalleryAVGuideError("CPU media tools are unavailable")
    data = bytearray()
    _stream([binary, "-v", "error", "-max_alloc", str(MAX_ENCODED_BYTES), *_input(snapshot),
             "-show_streams", "-show_format", "-of", "json"], 1024**2, data.extend, cancel_check)
    try:
        metadata = json.loads(data)
        streams = metadata["streams"]
        selected = next(stream for stream in streams if stream.get("codec_type") == kind)
        duration = float(selected.get("duration", metadata["format"].get("duration")))
        if (len(streams) > 16 or selected.get("codec_name") not in _CODECS[kind]
                or not math.isfinite(duration) or not 0 < duration <= 15):
            raise ValueError()
        width, height = (selected["width"], selected["height"]) if kind == "video" else (0, 0)
        if kind == "video" and (type(width) is not int or type(height) is not int
                                or not 1 <= width <= 8192 or not 1 <= height <= 8192
                                or width * height > MAX_PIXELS):
            raise ValueError()
        return width, height
    except (KeyError, TypeError, ValueError, StopIteration):
        raise H3GalleryAVGuideError("Guide media geometry is unsupported") from None


def _conversion(snapshot, kind, height=32, width=32):
    binary = shutil.which("ffmpeg")
    if not binary:
        raise H3GalleryAVGuideError("CPU media tools are unavailable")
    command = [binary, "-nostdin", "-v", "error", "-max_alloc", str(MAX_ENCODED_BYTES),
               "-threads", "1", "-hwaccel", "none", *_input(snapshot), "-threads", "1",
               "-filter_threads", "1", "-filter_complex_threads", "1"]
    if kind == "video":
        return command + ["-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
            f"fps=fps=24:round=near,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    return command + ["-map", "0:a:0", "-vn", "-sn", "-dn", "-af", "aresample=32000",
                      "-ac", "2", "-ar", "32000", "-f", "f32le", "pipe:1"]


def _probe_snapshot(snapshot, sha256, size, kind, cancel_check):
    width, height = _inspect(snapshot, kind, cancel_check)
    unit = 32 * 32 * 3 if kind == "video" else 8
    maximum = MAX_FRAMES if kind == "video" else MAX_SAMPLES
    count = _stream(_conversion(snapshot, kind), maximum * unit, lambda chunk: None, cancel_check)
    if count < unit or count % unit:
        raise H3GalleryAVGuideError("Guide normalized count is invalid")
    return GalleryAVProbe(sha256, size, kind, width, height,
                          count // unit if kind == "video" else 0,
                          count // unit if kind == "audio" else 0)


def probe_gallery_av(path: str, kind: str, cancel_check=None) -> GalleryAVProbe:
    with _snapshot(path, kind, cancel_check) as (snapshot, sha256, size):
        return _probe_snapshot(snapshot, sha256, size, kind, cancel_check)


def _records(sources):
    if type(sources) is not list or not 1 <= len(sources) <= 8:
        raise H3GalleryAVGuideError("Choose one through eight guide sources")
    total = 0
    for item in sources:
        if type(item) is not dict or set(item) != GALLERY_AV_SOURCE_FIELDS:
            raise H3GalleryAVGuideError("Guide source fields are invalid")
        for key in ("workspace", "name", "revision"):
            value = item[key]
            if (type(value) is not str or not 1 <= len(value) <= 256 or value in {".", ".."}
                    or any(c in value for c in ("/", "\\", "\0"))):
                raise H3GalleryAVGuideError("Guide source identity is invalid")
        if (item["workspace"] != sources[0]["workspace"] or type(item["sha256"]) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) is None
                or type(item["source_private"]) is not bool or type(item["source_explicit"]) is not bool
                or type(item["frame_index"]) is not int or type(item["kind"]) is not str
                or item["kind"] not in _EXTENSIONS):
            raise H3GalleryAVGuideError("Guide source commitment is invalid")
        for key in ("size", "width", "height", "frame_count", "sample_count"):
            if type(item[key]) is not int or item[key] < 0:
                raise H3GalleryAVGuideError("Guide source facts are invalid")
        if not 0 < item["size"] <= MAX_ENCODED_BYTES or Path(item["name"]).suffix.lower() not in _EXTENSIONS[item["kind"]]:
            raise H3GalleryAVGuideError("Guide encoded media exceeds its limit")
        if item["kind"] == "video":
            valid = (1 <= item["width"] <= 8192 and 1 <= item["height"] <= 8192
                     and item["width"] * item["height"] <= MAX_PIXELS
                     and 1 <= item["frame_count"] <= MAX_FRAMES and item["sample_count"] == 0)
        else:
            valid = item["width"] == item["height"] == item["frame_count"] == 0 and 1 <= item["sample_count"] <= MAX_SAMPLES
        if not valid:
            raise H3GalleryAVGuideError("Guide normalized geometry exceeds its limit")
        total += item["size"]
    if total > MAX_TOTAL_ENCODED_BYTES:
        raise H3GalleryAVGuideError("Guide total encoded media exceeds its limit")
    return copy.deepcopy(sources)


def build_gallery_av_guide_plan(sources: list[dict], target_frames: int) -> dict:
    records = _records(sources)
    inputs = [{"frame_idx": item["frame_index"],
               "visual": {"sha256": item["sha256"], "count": item["frame_count"]} if item["kind"] == "video" else None,
               "audio": {"sha256": item["sha256"], "count": (item["sample_count"] + 799) // 800} if item["kind"] == "audio" else None}
              for item in records]
    try:
        return plan_h3_guide_inputs(target_frames, inputs)
    except ValueError:
        raise H3GalleryAVGuideError("Guide interval plan is invalid") from None


def make_gallery_av_guide_sources(sources, plan) -> dict:
    records = _records(sources)
    try:
        validated = validate_h3_guide_plan(plan)
    except (ValueError, TypeError):
        raise H3GalleryAVGuideError("Guide plan commitment is invalid") from None
    if validated != build_gallery_av_guide_plan(records, validated["target_frames"]):
        raise H3GalleryAVGuideError("Guide plan differs from the selected sources")
    return {"sources": records, "target_frames": validated["target_frames"], "plan_sha256": validated["plan_sha256"]}


def decode_gallery_av_guide_sources(paths: list[str], source_binding: dict, plan: dict, *, height: int, width: int, cancel_check=None):
    if (type(source_binding) is not dict or set(source_binding) != {"sources", "target_frames", "plan_sha256"}
            or type(source_binding["sources"]) is not list
            or type(paths) is not list or len(paths) != len(source_binding["sources"])
            or any(type(path) is not str or not path for path in paths)):
        raise H3GalleryAVGuideError("Guide binding is invalid")
    expected = make_gallery_av_guide_sources(source_binding["sources"], plan)
    if source_binding != expected:
        raise H3GalleryAVGuideError("Guide binding differs from the interval plan")
    if (type(height) is not int or type(width) is not int or min(height, width) < 32
            or max(height, width) > 4096 or height % 32 or width % 32):
        raise H3GalleryAVGuideError("Guide canvas is invalid")
    sizes = [item["frame_count"] * height * width * 12 + item["sample_count"] * 8 for item in expected["sources"]]
    # The final validator creates an absolute-value copy of the largest item.
    # Reserve that working memory as well as the bounded pipe buffers.
    if sum(sizes) + max(sizes) + 8 * 1024**2 > MAX_DECODED_BYTES:
        raise H3GalleryAVGuideError("Guide total decoded media exceeds its limit")
    _check(cancel_check)
    import torch
    from models.minimax_h3.timeline_guides import H3TimelineGuideMedia, H3TimelineGuidePayload, validate_timeline_guide_payload
    media = []
    for path, item in zip(paths, expected["sources"]):
        _check(cancel_check)
        with _snapshot(path, item["kind"], cancel_check) as (snapshot, sha256, size):
            if sha256 != item["sha256"] or size != item["size"]:
                raise H3GalleryAVGuideError("Guide source bytes changed")
            facts = asdict(_probe_snapshot(snapshot, sha256, size, item["kind"], cancel_check))
            if any(item[key] != value for key, value in facts.items()):
                raise H3GalleryAVGuideError("Guide source normalized counts changed")
            video = item["kind"] == "video"
            count = item["frame_count"] * height * width * 3 if video else item["sample_count"] * 2
            tensor = torch.empty(count, dtype=torch.float32, device="cpu")
            offset, pending = 0, bytearray()
            def consume(chunk):
                nonlocal offset
                pending.extend(chunk)
                unit = 1 if video else 4
                length = len(pending) // unit * unit
                if length:
                    values = torch.frombuffer(bytearray(pending[:length]), dtype=torch.uint8 if video else torch.float32)
                    tensor[offset:offset + values.numel()].copy_(values)
                    offset += values.numel()
                    del pending[:length]
            _stream(_conversion(snapshot, item["kind"], height, width), count * (1 if video else 4), consume, cancel_check)
            if pending or offset != count or not torch.isfinite(tensor).all():
                raise H3GalleryAVGuideError("Guide normalized payload count is invalid")
            if video:
                tensor.div_(127.5).sub_(1)
                media.append(H3TimelineGuideMedia(visual=tensor.reshape(item["frame_count"], height, width, 3).permute(3, 0, 1, 2)))
            else:
                tensor.clamp_(-1, 1)
                media.append(H3TimelineGuideMedia(waveform=tensor.reshape(item["sample_count"], 2).transpose(0, 1)))
    payload = H3TimelineGuidePayload(copy.deepcopy(plan), tuple(media))
    validate_timeline_guide_payload(payload, frame_num=expected["target_frames"], height=height, width=width)
    _check(cancel_check)
    return payload
