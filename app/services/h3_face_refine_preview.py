"""Bounded CPU facts and exact PNG frames for an already authorized source.

This helper does not resolve Gallery names, authorize projects or publish media.
The HTTP adapter must check current access, finality, revision and privacy both
before invoking it and before returning its result. No GPU or model is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import os
import shutil
import time

from services import h3_face_refine as face
from services import h3_gallery_av_guide as av

PREVIEW_SECONDS = 30


class FacePreviewError(ValueError):
    """Unavailable or unsupported source; errors never expose paths."""


class FacePreviewCancelled(FacePreviewError):
    """The owned media child has stopped and been reaped."""


@dataclass(frozen=True)
class FacePreviewSource:
    sha256: str
    size: int
    width: int
    height: int
    frame_count: int
    fps: str
    audio_streams: tuple[tuple[int, str], ...]

    def public_facts(self):
        """Measured facts only; the caller adds its authorized Gallery binding."""
        return {"width": self.width, "height": self.height,
                "frame_count": self.frame_count, "fps": self.fps,
                "audio_streams": [{"ordinal": ordinal, "label": label}
                                  for ordinal, label in self.audio_streams]}


def _audio_streams(snapshot, cancel_check):
    binary = shutil.which("ffprobe")
    if not binary:
        raise FacePreviewError("CPU media tools are unavailable")
    encoded = bytearray()
    av._stream([binary, "-v", "error", "-max_alloc", str(av.MAX_ENCODED_BYTES),
                *av._input(snapshot), "-count_packets", "-show_entries",
                "stream=codec_type,codec_name,channels,sample_rate,sample_aspect_ratio,width,height,nb_read_packets:"
                "stream_tags=rotate:stream_side_data=side_data_type,rotation", "-of", "json"],
               1024**2, encoded.extend, cancel_check)
    streams = json.loads(encoded)["streams"]
    if type(streams) is not list or len(streams) > 32:
        raise FacePreviewError("Source audio tracks are unsupported")
    video = next(stream for stream in streams if stream.get("codec_type") == "video")
    width, height, count = video["width"], video["height"], int(video["nb_read_packets"])
    if (type(width) is not int or type(height) is not int
            or min(width, height) < 8 or max(width, height) > 4096
            or not 124 <= count <= 345 or count % 17 != 5
            or count * width * height * 3 > face.MAX_RGB_BYTES):
        raise FacePreviewError("Source decoded size or length is unsupported for face repair")
    if (video.get("sample_aspect_ratio", "1:1") not in ("1:1", "0:1", "N/A")
            or float(video.get("tags", {}).get("rotate", 0)) != 0
            or any(item.get("side_data_type") == "Display Matrix"
                   for item in video.get("side_data_list", []))):
        raise FacePreviewError("Source display geometry is unsupported for face repair")
    streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(streams) > 16:
        raise FacePreviewError("Source audio tracks are unsupported")
    result = []
    for ordinal, stream in enumerate(streams):
        channels, rate = stream["channels"], int(stream["sample_rate"])
        if type(channels) is not int or not 1 <= channels <= 64 or not 1 <= rate <= 384000:
            raise FacePreviewError("Source audio tracks are unsupported")
        # Never echo arbitrary titles, languages or other source metadata.
        codec = stream.get("codec_name")
        codec = codec if codec in av._CODECS["audio"] else "audio"
        result.append((ordinal, f"Track {ordinal + 1} ({codec}, {channels} channels, {rate} Hz)"))
    return tuple(result)


def _frame(snapshot, facts, index, cancel_check):
    binary = shutil.which("ffmpeg")
    if not binary:
        raise FacePreviewError("CPU media tools are unavailable")
    pixels = bytearray()
    unit = facts["width"] * facts["height"] * 3
    # Select by decoded frame ordinal, never timestamp seeking, fps conversion,
    # resizing or a fallback frame. Use the worker's RGB decoding convention.
    count = av._stream([binary, "-v", "error", "-nostdin", "-max_alloc",
        str(av.MAX_ENCODED_BYTES), "-threads", "2", *av._input(snapshot),
        "-map", "0:v:0", "-an", "-sn", "-dn", "-threads", "2",
        "-filter_threads", "1", "-vf", f"select=eq(n\\,{index})",
        "-frames:v", "1", "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "pipe:1"], unit, pixels.extend, cancel_check)
    if count != unit:
        raise FacePreviewError("The requested source frame is unavailable")
    from PIL import Image
    output = io.BytesIO()
    Image.frombytes("RGB", (facts["width"], facts["height"]), bytes(pixels)).save(output, format="PNG")
    av._check(cancel_check)
    png = output.getvalue()
    if len(png) > unit + 65536:
        raise FacePreviewError("Source preview exceeds its limit")
    return png


def _read(path, frame_index, expected_source, cancel_check):
    if type(path) is not str or not path or "\0" in path:
        raise FacePreviewError("Source preview binding is invalid")
    if expected_source is not None and type(expected_source) is not FacePreviewSource:
        raise FacePreviewError("Source preview binding is invalid")
    deadline, timed_out = time.monotonic() + PREVIEW_SECONDS, False
    def cancelled():
        nonlocal timed_out
        timed_out = time.monotonic() >= deadline
        return timed_out or (cancel_check is not None and cancel_check())
    try:
        # Snapshot checks copying, and this outer identity check also covers
        # changes made while the private snapshot is being inspected/decoded.
        av._check(cancelled)
        before = av._identity(os.lstat(path))
        with av._snapshot(path, "video", cancelled) as (snapshot, digest, size):
            if expected_source is not None and (digest != expected_source.sha256 or size != expected_source.size):
                raise FacePreviewError("Source preview changed; refresh the clip")
            audio_streams = _audio_streams(snapshot, cancelled)
            facts = face._probe(snapshot, cancelled)
            count = facts["frame_count"]
            if not 124 <= count <= 345 or count % 17 != 5:
                raise FacePreviewError("Source length is unsupported for H3 Base face repair")
            source = FacePreviewSource(digest, size, **facts,
                                       audio_streams=audio_streams)
            if expected_source is not None and source != expected_source:
                raise FacePreviewError("Source preview changed; refresh the clip")
            if frame_index is not None and not 0 <= frame_index < count:
                raise FacePreviewError("Choose a frame within the source clip")
            png = None if frame_index is None else _frame(snapshot, facts, frame_index, cancelled)
            av._check(cancelled)
            if av._identity(os.lstat(path)) != before:
                raise FacePreviewError("Source preview changed; refresh the clip")
            return source, png
    except av.H3GalleryAVGuideCancelled:
        if timed_out:
            raise FacePreviewError("Source preview timed out") from None
        raise FacePreviewCancelled("Source preview cancelled") from None
    except FacePreviewError:
        raise
    except (OSError, ValueError, KeyError, TypeError, OverflowError, StopIteration):
        raise FacePreviewError("Source preview is unavailable or unsupported") from None


def read_face_source(path: str, *, cancel_check=None) -> FacePreviewSource:
    """Measure a bounded final source; no saved generation settings are read."""
    return _read(path, None, None, cancel_check)[0]


def read_face_frame(path: str, frame_index: int, *, expected_source=None, cancel_check=None):
    """Return (measured source, PNG), or fail without another source/frame."""
    if type(frame_index) is not int or frame_index < 0:
        raise FacePreviewError("Choose an integer source frame")
    return _read(path, frame_index, expected_source, cancel_check)
