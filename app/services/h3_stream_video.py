"""Transient, cumulative-only H3 video encoding inside the VAE call lifetime.

The server supplies an already authorized private staging directory. A receipt
is valid only for its owning sink and exact verified file; it cannot be used as
queue JSON or as AV/publication authority. Audio and finality belong to WGP.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import torch
from models.minimax_h3.packing import MINIMAX_H3_PIXEL_MEAN, MINIMAX_H3_PIXEL_STD
from shared.utils.audio_video import _CancellableVideoWriter, _get_codec_params
from shared.utils.media_encoder import run_encoder
from shared.utils.video_decode import _resolve_media_binary

from services.h3_native_continuation import is_legal_h3_video_frame_count
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    _private_directory_identity,
    _verify_directory_identity,
)


@dataclass(frozen=True, slots=True)
class H3EncodedVideo:
    """One verified video owned by a live synchronous sink, never a final AV file."""

    generated_frames: int
    published_frames: int
    height: int
    width: int
    container: str
    size: int
    sha256: str
    _owner: H3VideoSink = field(repr=False, compare=False)

    def __getstate__(self):
        raise TypeError("H3 encoded video is transient and cannot be serialized")

    def verify(self):
        self._owner.verify(self)

    def transfer_to(self, destination):
        """Move once to a server-selected direct child of the same private root."""
        self._owner.transfer(self, destination)


class H3VideoSink:
    """Consume raw native chunks once, encode and probe before issuing a receipt.

    Call ``cleanup`` in the outer generation's finally block, including after
    success. No frame tensor is retained after ``__call__``. The deadline also
    covers gaps between VAE calls, encoder finalization, probing and transfer.
    Cancellation callbacks must be quick and safe for the encoder monitor.
    """

    def __init__(
        self, staging_directory, *, generated_frames, published_frames,
        height, width, codec_type, container, abort_check, timeout,
    ):
        if (
            any(type(v) is not int for v in (generated_frames, published_frames, height, width))
            or not is_legal_h3_video_frame_count(generated_frames)
            or not 1 <= published_frames <= generated_frames
            or generated_frames - published_frames > 16
            or any(v % 32 or not 32 <= v <= 8192 for v in (height, width))
        ):
            raise ValueError("H3 stream requires exact native canvas and publication frames")
        if container not in {"mp4", "mkv", "webm", "mov"}:
            raise ValueError("H3 stream video container is unsupported")
        if not callable(abort_check):
            raise TypeError("H3 stream cancellation check must be callable")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("H3 stream timeout must be finite and positive")
        self.root = Path(staging_directory)
        if not self.root.is_absolute():
            raise ValueError("H3 stream requires an absolute private staging directory")
        try:
            self._root_identity = _private_directory_identity(self.root)
        except QueueRecoveryRuntimeError:
            raise ValueError("H3 stream staging directory is unsafe") from None
        self.generated_frames, self.published_frames = generated_frames, published_frames
        self.height, self.width, self.container = height, width, container
        self._codec_params = _get_codec_params(codec_type, container)
        self.abort_check, self.timeout = abort_check, float(timeout)
        self._deadline = None
        self._temporary = self._writer = self._path = None
        self._seen = self._published = 0
        self._phase = "new"
        self._receipt = self._file_identity = None

    def __getstate__(self):
        raise TypeError("H3 video sink is transient and cannot be serialized")

    def _remaining(self):
        if self.abort_check():
            raise InterruptedError("H3 video encoding cancelled")
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("H3 video encoding timed out")
        try:
            if _private_directory_identity(self.root) != self._root_identity:
                raise QueueRecoveryRuntimeError("directory changed")
        except QueueRecoveryRuntimeError:
            raise ValueError("H3 stream staging directory changed or is unsafe") from None
        return remaining

    def __enter__(self):
        if self._phase != "new":
            raise ValueError("H3 video sink is single-use")
        self._deadline = time.monotonic() + self.timeout
        self._remaining()
        self._temporary = tempfile.TemporaryDirectory(prefix=".h3-stream-", dir=self.root)
        self._path = Path(self._temporary.name) / f"video.{self.container}"
        try:
            self._writer = _CancellableVideoWriter(
                str(self._path), 24, self._codec_params,
                abort_check=self.abort_check, timeout=self._remaining(),
            )
            self._writer.__enter__()
            self._phase = "encoding"
        except BaseException:
            self.cleanup()
            raise
        return self

    def __call__(self, raw):
        if self._phase != "encoding":
            raise ValueError("H3 video sink is not accepting decoder chunks")
        self._remaining()
        if (
            not isinstance(raw, torch.Tensor) or raw.ndim != 5
            or raw.shape[:2] != (1, 3) or raw.shape[-2:] != (self.height, self.width)
            or not 1 <= raw.shape[2] <= 22 or not raw.is_floating_point()
            or raw.layout != torch.strided or raw.device.type == "meta"
            or self._seen + raw.shape[2] > self.generated_frames
        ):
            raise ValueError("H3 decoder chunk differs from the bound native video geometry")
        take = min(raw.shape[2], max(0, self.published_frames - self._seen))
        if take:
            # Match native float32 pixel normalization, then WGP's truncating
            # uint8 conversion. Keep each arithmetic step and its order exact.
            mean = raw.new_tensor(MINIMAX_H3_PIXEL_MEAN, dtype=torch.float32).view(1, 3, 1, 1, 1)
            std = raw.new_tensor(MINIMAX_H3_PIXEL_STD, dtype=torch.float32).view(1, 3, 1, 1, 1)
            pixels = (raw[:, :, :take].float() * std + mean).clamp(0, 1).mul(2).sub(1)
            pixels = pixels.clamp_(-1, 1).sub_(-1).mul_(127.5).to(torch.uint8)
            for frame in pixels[0].permute(1, 2, 3, 0).cpu():
                self._remaining()
                self._writer.append_data(frame.numpy())
            self._published += take
        self._seen += raw.shape[2]

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is not None:
                self._writer.__exit__(exc_type, exc, traceback)
                return False
            self._remaining()
            if (self._seen, self._published) != (self.generated_frames, self.published_frames):
                raise ValueError("H3 decoder did not consume the complete generated timeline")
            self._writer.__exit__(None, None, None)
            self._probe()
            size, digest, identity = self._fingerprint()
            self._file_identity = identity
            self._receipt = H3EncodedVideo(
                self.generated_frames, self.published_frames, self.height, self.width,
                self.container, size, digest, self,
            )
            self._phase = "verified"
        except BaseException as error:
            self._writer.__exit__(type(error), error, error.__traceback__)
            self.cleanup()
            raise
        finally:
            if exc_type is not None:
                self.cleanup()
        return False

    @property
    def receipt(self):
        if self._phase != "verified":
            raise ValueError("H3 encoded video has no verified receipt")
        return self._receipt

    def _probe(self):
        executable = _resolve_media_binary("ffprobe")
        if executable is None:
            raise RuntimeError("H3 video verification requires ffprobe")
        with tempfile.TemporaryFile(dir=self._temporary.name) as output:
            code = run_encoder(
                [executable, "-v", "error", "-count_frames", "-show_entries",
                 "stream=codec_type,width,height,r_frame_rate,avg_frame_rate,nb_read_frames",
                 "-of", "json", str(self._path)],
                timeout=self._remaining(), abort_check=self.abort_check, stdout=output,
            )
            if code != 0 or output.tell() > 16384:
                raise ValueError("H3 encoded video probe failed")
            output.seek(0)
            try:
                streams = json.loads(output.read(16385))["streams"]
                video = streams[0]
                valid = (
                    len(streams) == 1 and video["codec_type"] == "video"
                    and (video["height"], video["width"]) == (self.height, self.width)
                    and Fraction(video["r_frame_rate"]) == Fraction(video["avg_frame_rate"]) == 24
                    and int(video["nb_read_frames"]) == self.published_frames
                )
            except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
                valid = False
            if not valid:
                raise ValueError("H3 encoded video differs from the bound publication geometry or clock")
        self._remaining()

    @staticmethod
    def _identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _fingerprint(self):
        self._remaining()
        before = os.lstat(self._path)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
            raise ValueError("H3 encoded video is not a regular nonempty file")
        digest = hashlib.sha256()
        descriptor = os.open(self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            if self._identity(os.fstat(source.fileno())) != self._identity(before):
                raise ValueError("H3 encoded video changed before verification")
            while chunk := source.read(1024 * 1024):
                self._remaining()
                digest.update(chunk)
            after = os.fstat(source.fileno())
        if self._identity(after) != self._identity(before) or self._identity(os.lstat(self._path)) != self._identity(before):
            raise ValueError("H3 encoded video changed during verification")
        return before.st_size, digest.hexdigest(), self._identity(before)

    def verify(self, receipt):
        if self._phase != "verified" or receipt is not self._receipt:
            raise ValueError("H3 encoded video receipt does not belong to this active sink")
        size, digest, identity = self._fingerprint()
        if (size, digest, identity) != (receipt.size, receipt.sha256, self._file_identity):
            raise ValueError("H3 encoded video changed after verification")

    def transfer(self, receipt, destination):
        destination = Path(destination)
        if destination.parent != self.root or destination.suffix != f".{self.container}":
            raise ValueError("H3 encoded video destination must be in the bound private directory")
        self.verify(receipt)
        self._remaining()
        os.replace(self._path, destination)
        self._phase = "transferred"
        self.cleanup()

    def cleanup(self):
        """Discard only this sink's temporary; safe after a completed transfer."""
        self._receipt = None
        self._phase = "closed"
        if self._writer is not None:
            self._writer.__exit__(RuntimeError, RuntimeError("H3 sink closed"), None)
            self._writer = None
        if self._temporary is not None:
            _verify_directory_identity(self.root, self._root_identity)
            self._temporary.cleanup()
            self._temporary = None
