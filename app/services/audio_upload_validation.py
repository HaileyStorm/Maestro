"""Content-neutral validation and storage for uploaded WAV audio.

The upload route uses this module before it creates an upload path or access
sidecar.  Validation intentionally inspects only the RIFF container contract;
it never decodes, transcribes, fingerprints, or otherwise examines audio
content.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import secrets
import stat
import struct
from typing import Any, Callable, Final

from services.win_safe_files import safe_direct_file_under


MIN_WAV_SAMPLE_RATE: Final = 8_000
MAX_WAV_SAMPLE_RATE: Final = 192_000
MAX_WAV_CHANNELS: Final = 2
MAX_WAV_DURATION_SECONDS: Final = 6 * 60 * 60

_PCM_FORMAT: Final = 0x0001
_IEEE_FLOAT_FORMAT: Final = 0x0003
_EXTENSIBLE_FORMAT: Final = 0xFFFE
_EXTENSIBLE_GUID_SUFFIX: Final = bytes.fromhex(
    "00001000800000aa00389b71"
)
_CLEANUP_MARKER_PREFIX: Final = ".maestro-audio-cleanup-"
_CLEANUP_MARKER_SUFFIX: Final = ".json"
_CLEANUP_SCHEMA_VERSION: Final = 1
_MAX_CLEANUP_MARKERS: Final = 256
_MAX_CLEANUP_TARGETS: Final = 16
_MAX_CLEANUP_MARKER_BYTES: Final = 4096
_UPLOAD_ARTIFACT_RE: Final = re.compile(
    r"^[0-9a-f]{8}\.(?:wav|mp3|flac|ogg|m4a|aac|mp4|mov|mkv|webm|avi|m4v)"
    r"(?:\.access\.json)?$"
)


class WavUploadValidationError(ValueError):
    """A content-free, safe-to-display WAV validation failure."""

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class AudioUploadStorageError(OSError):
    """A content-free upload storage failure."""


@dataclass(frozen=True)
class WavUploadMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width_bits: int
    codec: str


_INVALID_WAV = "Audio upload is not a valid WAV file."
_UNSUPPORTED_WAV = (
    "WAV audio must use PCM or IEEE float encoding with 1-2 channels "
    "at 8-192 kHz."
)
_EMPTY_WAV = "WAV audio contains no samples."
_LONG_WAV = "WAV audio is too long (maximum 6 hours)."


def _invalid() -> WavUploadValidationError:
    return WavUploadValidationError(_INVALID_WAV)


def _parse_format_chunk(chunk: memoryview) -> tuple[int, int, int, int, str]:
    if len(chunk) < 16:
        raise _invalid()
    try:
        format_tag, channels, sample_rate, byte_rate, block_align, bits = (
            struct.unpack_from("<HHIIHH", chunk, 0)
        )
    except struct.error:
        raise _invalid() from None

    codec_tag = format_tag
    valid_bits = bits
    extension_size = 0
    if len(chunk) != 16:
        if len(chunk) < 18:
            raise _invalid()
        try:
            extension_size = struct.unpack_from("<H", chunk, 16)[0]
        except struct.error:
            raise _invalid() from None
        if 18 + extension_size != len(chunk):
            raise _invalid()
    if format_tag == _EXTENSIBLE_FORMAT:
        if len(chunk) < 40:
            raise _invalid()
        try:
            valid_bits = struct.unpack_from("<H", chunk, 18)[0]
            subformat_tag = struct.unpack_from("<I", chunk, 24)[0]
        except struct.error:
            raise _invalid() from None
        if (
            extension_size < 22
            or bytes(chunk[28:40]) != _EXTENSIBLE_GUID_SUFFIX
        ):
            raise _invalid()
        codec_tag = subformat_tag

    if codec_tag not in {_PCM_FORMAT, _IEEE_FLOAT_FORMAT}:
        raise WavUploadValidationError(_UNSUPPORTED_WAV)
    if not 1 <= channels <= MAX_WAV_CHANNELS:
        raise WavUploadValidationError(_UNSUPPORTED_WAV)
    if not MIN_WAV_SAMPLE_RATE <= sample_rate <= MAX_WAV_SAMPLE_RATE:
        raise WavUploadValidationError(_UNSUPPORTED_WAV)

    supported_bits = (
        {8, 16, 24, 32}
        if codec_tag == _PCM_FORMAT
        else {32, 64}
    )
    if bits not in supported_bits:
        raise WavUploadValidationError(_UNSUPPORTED_WAV)
    if not 1 <= valid_bits <= bits:
        raise WavUploadValidationError(_UNSUPPORTED_WAV)

    expected_block_align = channels * ((bits + 7) // 8)
    if (
        block_align != expected_block_align
        or byte_rate != sample_rate * block_align
    ):
        raise _invalid()
    return (
        channels,
        sample_rate,
        block_align,
        bits,
        "pcm" if codec_tag == _PCM_FORMAT else "ieee_float",
    )


def validate_wav_upload(
    content: bytes,
    *,
    max_bytes: int,
) -> WavUploadMetadata:
    """Validate one bounded RIFF/WAVE container without decoding samples."""
    if not isinstance(content, bytes) or not 1 <= len(content) <= max_bytes:
        raise _invalid()
    view = memoryview(content)
    if (
        len(view) < 12
        or bytes(view[:4]) != b"RIFF"
        or bytes(view[8:12]) != b"WAVE"
    ):
        raise _invalid()
    try:
        riff_size = struct.unpack_from("<I", view, 4)[0]
    except struct.error:
        raise _invalid() from None
    riff_end = 8 + riff_size
    if riff_end < 12 or riff_end != len(view):
        raise _invalid()

    format_contract: tuple[int, int, int, int, str] | None = None
    data_bytes = 0
    data_seen = False
    offset = 12
    while offset < riff_end:
        if offset + 8 > riff_end:
            raise _invalid()
        chunk_id = bytes(view[offset:offset + 4])
        try:
            chunk_size = struct.unpack_from("<I", view, offset + 4)[0]
        except struct.error:
            raise _invalid() from None
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        padded_end = payload_end + (chunk_size & 1)
        if payload_end > riff_end or padded_end > riff_end:
            raise _invalid()

        if chunk_id == b"fmt ":
            if format_contract is not None:
                raise _invalid()
            format_contract = _parse_format_chunk(view[payload_start:payload_end])
        elif chunk_id == b"data":
            if format_contract is None or data_seen:
                raise _invalid()
            data_seen = True
            data_bytes = chunk_size
        offset = padded_end

    if offset != riff_end or format_contract is None:
        raise _invalid()
    channels, sample_rate, block_align, bits, codec = format_contract
    if data_bytes <= 0:
        raise WavUploadValidationError(_EMPTY_WAV)
    if data_bytes % block_align:
        raise _invalid()
    frames = data_bytes // block_align
    duration_seconds = frames / sample_rate
    if duration_seconds > MAX_WAV_DURATION_SECONDS:
        raise WavUploadValidationError(_LONG_WAV)
    return WavUploadMetadata(
        duration_seconds=round(duration_seconds, 3),
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bits=bits,
        codec=codec,
    )


def write_regular_upload(
    directory: str,
    basename: str,
    content: bytes,
) -> str:
    """Create one private regular file without following a symlink target."""
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.path.islink(directory) or not os.path.isdir(directory):
            raise OSError("unsafe upload directory")
        candidate = safe_direct_file_under(directory, basename)
        if candidate is None:
            raise OSError("unsafe upload basename")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(candidate, flags, 0o600)
        created = True
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("upload target is not regular")
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            created = False
            return candidate
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if created:
                try:
                    os.remove(candidate)
                except OSError:
                    pass
    except (OSError, TypeError, ValueError):
        raise AudioUploadStorageError(
            "Audio upload could not be stored."
        ) from None


def _cleanup_target_name(directory: str, path: object) -> str | None:
    try:
        supplied = os.path.abspath(os.fspath(path))
    except (TypeError, ValueError, OSError):
        return None
    name = os.path.basename(supplied)
    candidate = safe_direct_file_under(directory, name)
    if (
        candidate is None
        or os.path.normcase(candidate) != os.path.normcase(supplied)
        or _UPLOAD_ARTIFACT_RE.fullmatch(name) is None
    ):
        return None
    return name


def record_pending_audio_cleanup(
    directory: str,
    paths: list[str] | tuple[str, ...],
) -> str:
    """Durably account for generated upload artifacts that remain locked."""
    names = sorted({
        name
        for path in paths
        if (name := _cleanup_target_name(directory, path)) is not None
    })
    if not names or len(names) > _MAX_CLEANUP_TARGETS:
        raise AudioUploadStorageError(
            "Audio upload cleanup could not be recorded."
        )
    payload = json.dumps(
        {"version": _CLEANUP_SCHEMA_VERSION, "targets": names},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if len(payload) > _MAX_CLEANUP_MARKER_BYTES:
        raise AudioUploadStorageError(
            "Audio upload cleanup could not be recorded."
        )
    token = secrets.token_hex(8)
    final_name = f"{_CLEANUP_MARKER_PREFIX}{token}{_CLEANUP_MARKER_SUFFIX}"
    temp_name = f"{final_name}.{secrets.token_hex(4)}.tmp"
    temp_path = write_regular_upload(directory, temp_name, payload)
    final_path = safe_direct_file_under(directory, final_name)
    if final_path is None:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise AudioUploadStorageError(
            "Audio upload cleanup could not be recorded."
        )
    try:
        os.replace(temp_path, final_path)
        return final_path
    except OSError:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise AudioUploadStorageError(
            "Audio upload cleanup could not be recorded."
        ) from None


def _read_cleanup_marker(path: str) -> tuple[str, ...] | None:
    try:
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CLEANUP_MARKER_BYTES:
            return None
        with open(path, "r", encoding="ascii") as handle:
            payload: Any = json.load(handle)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    targets = payload.get("targets")
    if (
        not isinstance(targets, list)
        or not 1 <= len(targets) <= _MAX_CLEANUP_TARGETS
        or any(
            not isinstance(name, str)
            or _UPLOAD_ARTIFACT_RE.fullmatch(name) is None
            for name in targets
        )
        or targets != sorted(set(targets))
    ):
        return None
    return tuple(targets)


def retry_pending_audio_cleanup(
    directory: str,
    delete: Callable[..., dict],
) -> int:
    """Retry bounded durable cleanup markers; retain any still-locked work."""
    if os.path.islink(directory) or not os.path.isdir(directory):
        return 0
    try:
        marker_names = sorted(
            name
            for name in os.listdir(directory)
            if name.startswith(_CLEANUP_MARKER_PREFIX)
            and name.endswith(_CLEANUP_MARKER_SUFFIX)
        )[:_MAX_CLEANUP_MARKERS]
    except OSError:
        return 0
    cleared = 0
    for marker_name in marker_names:
        marker_path = safe_direct_file_under(directory, marker_name)
        if marker_path is None:
            continue
        targets = _read_cleanup_marker(marker_path)
        if targets is None:
            continue
        all_cleared = True
        for target_name in targets:
            target = safe_direct_file_under(directory, target_name)
            if target is None:
                all_cleared = False
                continue
            result = delete(target, retries=1)
            if not result.get("deleted") and result.get("reason") != "not_found":
                all_cleared = False
        if not all_cleared:
            continue
        marker_result = delete(marker_path, retries=1)
        if marker_result.get("deleted") or marker_result.get("reason") == "not_found":
            cleared += 1
    return cleared
