"""Reproducibly build Maestro's narrowly patched MMGP wheel."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile


NAME = "mmgp"
UPSTREAM_VERSION = "3.7.12"
VERSION = "3.7.12+maestro1"
UPSTREAM_URL = (
    "https://files.pythonhosted.org/packages/d1/da/"
    "df5d4be821577120eb4370dbbce9bbdd87e1fb4aa65e37c8dba0916ae1ea/"
    "mmgp-3.7.12-py3-none-any.whl"
)
UPSTREAM_SHA256 = "2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf"
UPSTREAM_OFFLOAD_SHA256 = "5bc8514f4bc87ae8eef04f5fe78f89a33822f60a5fa451e0c47768f436c01522"
PATCHED_OFFLOAD_SHA256 = "972451f19d3471bf96c47e241bffb1c6dca1a64e5fab3c75755fc5cd2fab5f15"
PATCH_FILENAME = "dora-input-scale.patch"
PATCH_SHA256 = "cc79f5ef357f091108adee09822f4cd6158727fb291631cdd67c0caabbb215e2"
UPSTREAM_WHEEL_GENERATOR = b"Generator: setuptools (83.0.0)\n"
WHEEL_GENERATOR = b"Generator: Maestro MMGP backend\n"

OLD_DIST_INFO = "mmgp-3.7.12.dist-info"
NEW_DIST_INFO = "mmgp-3.7.12+maestro1.dist-info"
WHEEL_FILENAME = "mmgp-3.7.12+maestro1-py3-none-any.whl"
PROVENANCE_FILENAME = f"{NEW_DIST_INFO}/maestro-provenance.json"
RECORD_FILENAME = f"{NEW_DIST_INFO}/RECORD"

MAX_WHEEL_BYTES = 2 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 30
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

EXPECTED_UPSTREAM_FILES = frozenset(
    {
        "__init__.py",
        "mmgp/__init__.py",
        "mmgp/fp8_quanto_bridge.py",
        "mmgp/offload.py",
        "mmgp/quant_router.py",
        "mmgp/safetensors2.py",
        f"{OLD_DIST_INFO}/licenses/LICENSE.md",
        f"{OLD_DIST_INFO}/METADATA",
        f"{OLD_DIST_INFO}/WHEEL",
        f"{OLD_DIST_INFO}/top_level.txt",
        f"{OLD_DIST_INFO}/RECORD",
    }
)

_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)


class BuildError(RuntimeError):
    """The pinned source or package recipe failed validation."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _safe_archive_name(name: str) -> None:
    if not name or "\\" in name or name.startswith("/") or "\x00" in name:
        raise BuildError(f"unsafe wheel member: {name!r}")
    path = PurePosixPath(name)
    if name.endswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise BuildError(f"unsafe wheel member: {name!r}")
    if path.as_posix() != name:
        raise BuildError(f"non-canonical wheel member: {name!r}")


def _read_wheel(wheel: bytes, expected_names: frozenset[str]) -> tuple[dict[str, bytes], dict[str, int]]:
    if len(wheel) > MAX_WHEEL_BYTES:
        raise BuildError("wheel exceeds the compressed-size limit")
    contents: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    folded_names: set[str] = set()
    expanded_size = 0
    try:
        with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
            for info in archive.infolist():
                _safe_archive_name(info.filename)
                folded = info.filename.casefold()
                if info.filename in contents or folded in folded_names:
                    raise BuildError(f"duplicate wheel member: {info.filename!r}")
                if info.filename not in expected_names:
                    raise BuildError(f"unexpected wheel member: {info.filename!r}")
                if info.flag_bits & 0x1:
                    raise BuildError(f"encrypted wheel member: {info.filename!r}")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise BuildError(f"unsupported compression for {info.filename!r}")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise BuildError(f"wheel member exceeds size limit: {info.filename!r}")
                expanded_size += info.file_size
                if expanded_size > MAX_EXPANDED_BYTES:
                    raise BuildError("wheel exceeds the expanded-size limit")
                mode = info.external_attr >> 16
                if mode and stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                    raise BuildError(f"non-regular wheel member: {info.filename!r}")
                contents[info.filename] = archive.read(info)
                modes[info.filename] = mode or (stat.S_IFREG | 0o644)
                folded_names.add(folded)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, BuildError):
            raise
        raise BuildError(f"invalid wheel archive: {exc}") from exc
    if set(contents) != set(expected_names):
        missing = sorted(set(expected_names) - set(contents))
        raise BuildError(f"wheel is missing expected members: {missing}")
    return contents, modes


def _parse_record(record: bytes) -> dict[str, tuple[str, str]]:
    try:
        text = record.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BuildError("wheel RECORD is not valid UTF-8 CSV") from exc
    parsed: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3:
            raise BuildError("wheel RECORD row does not have three fields")
        name, digest, size = row
        _safe_archive_name(name)
        if name in parsed:
            raise BuildError(f"duplicate RECORD entry: {name!r}")
        parsed[name] = (digest, size)
    return parsed


def _validate_record(contents: dict[str, bytes], record_name: str) -> None:
    if record_name not in contents:
        raise BuildError("wheel RECORD is missing")
    parsed = _parse_record(contents[record_name])
    if set(parsed) != set(contents):
        raise BuildError("wheel RECORD does not describe every member exactly once")
    for name, data in contents.items():
        digest, size = parsed[name]
        if name == record_name:
            if digest or size:
                raise BuildError("wheel RECORD must leave its own hash and size empty")
            continue
        if digest != _record_digest(data) or size != str(len(data)):
            raise BuildError(f"wheel RECORD mismatch for {name!r}")


def _download_upstream_wheel() -> bytes:
    request = Request(UPSTREAM_URL, headers={"User-Agent": "Maestro-MMGP-builder/1"})
    started = time.monotonic()
    try:
        with urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            final_url = urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "files.pythonhosted.org":
                raise BuildError("upstream wheel resolved outside files.pythonhosted.org")
            declared_size = response.headers.get("Content-Length")
            if declared_size is not None and int(declared_size) > MAX_WHEEL_BYTES:
                raise BuildError("upstream wheel exceeds the download-size limit")
            chunks: list[bytes] = []
            size = 0
            while True:
                if time.monotonic() - started > NETWORK_TIMEOUT_SECONDS:
                    raise BuildError("upstream wheel download exceeded its time limit")
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_WHEEL_BYTES:
                    raise BuildError("upstream wheel exceeds the download-size limit")
                chunks.append(chunk)
    except (OSError, ValueError) as exc:
        if isinstance(exc, BuildError):
            raise
        raise BuildError(f"could not download the pinned upstream wheel: {exc}") from exc
    wheel = b"".join(chunks)
    if _sha256(wheel) != UPSTREAM_SHA256:
        raise BuildError("upstream wheel SHA-256 does not match the pinned digest")
    return wheel


def _apply_unified_patch(source: bytes, patch: bytes) -> bytes:
    if not source.endswith(b"\r\n") or b"\n" in source.replace(b"\r\n", b""):
        raise BuildError("MMGP offload.py no longer has the expected CRLF format")
    try:
        source_lines = source.decode("utf-8").splitlines()
        patch_lines = patch.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BuildError("MMGP source or patch is not valid UTF-8") from exc
    if patch_lines[:2] != ["--- a/mmgp/offload.py", "+++ b/mmgp/offload.py"]:
        raise BuildError("patch targets an unexpected file")

    output: list[str] = []
    source_cursor = 0
    patch_cursor = 2
    hunk_count = 0
    while patch_cursor < len(patch_lines):
        match = _HUNK_HEADER.fullmatch(patch_lines[patch_cursor])
        if match is None:
            raise BuildError("malformed unified patch hunk header")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        target_cursor = old_start - 1
        if target_cursor < source_cursor or target_cursor > len(source_lines):
            raise BuildError("unified patch hunk is out of order")
        output.extend(source_lines[source_cursor:target_cursor])
        source_cursor = target_cursor
        patch_cursor += 1
        old_seen = 0
        new_seen = 0
        while patch_cursor < len(patch_lines) and not patch_lines[patch_cursor].startswith("@@ "):
            line = patch_lines[patch_cursor]
            if not line or line[0] not in {" ", "+", "-"}:
                raise BuildError("unsupported unified patch line")
            marker, body = line[0], line[1:]
            if marker in {" ", "-"}:
                if source_cursor >= len(source_lines) or source_lines[source_cursor] != body:
                    raise BuildError("unified patch preimage does not match MMGP source")
                source_cursor += 1
                old_seen += 1
            if marker in {" ", "+"}:
                output.append(body)
                new_seen += 1
            patch_cursor += 1
        if old_seen != old_count or new_seen != new_count:
            raise BuildError("unified patch hunk counts do not match its contents")
        hunk_count += 1
    if hunk_count != 2:
        raise BuildError("MMGP protocol patch must contain exactly two hunks")
    output.extend(source_lines[source_cursor:])
    return ("\r\n".join(output) + "\r\n").encode("utf-8")


def _patch_offload(source: bytes) -> bytes:
    if _sha256(source) != UPSTREAM_OFFLOAD_SHA256:
        raise BuildError("mmgp/offload.py does not match the exact upstream preimage")
    patch = (Path(__file__).resolve().parent / PATCH_FILENAME).read_bytes()
    if _sha256(patch) != PATCH_SHA256:
        raise BuildError("MMGP protocol patch does not match the recipe digest")
    patched = _apply_unified_patch(source, patch)
    if _sha256(patched) != PATCHED_OFFLOAD_SHA256:
        raise BuildError("patched mmgp/offload.py does not match the recipe postimage")
    return patched


def _replace_metadata_version(metadata: bytes) -> bytes:
    old = f"Version: {UPSTREAM_VERSION}\r\n".encode("ascii")
    new = f"Version: {VERSION}\r\n".encode("ascii")
    if metadata.count(old) != 1 or metadata.count(b"Name: mmgp\r\n") != 1:
        raise BuildError("MMGP metadata does not have the expected name and version")
    return metadata.replace(old, new, 1)


def _replace_wheel_generator(wheel_metadata: bytes) -> bytes:
    if wheel_metadata.count(UPSTREAM_WHEEL_GENERATOR) != 1:
        raise BuildError("MMGP WHEEL metadata does not have the expected generator")
    return wheel_metadata.replace(UPSTREAM_WHEEL_GENERATOR, WHEEL_GENERATOR, 1)


def _mapped_name(name: str) -> str:
    if name == OLD_DIST_INFO or name.startswith(OLD_DIST_INFO + "/"):
        return NEW_DIST_INFO + name[len(OLD_DIST_INFO) :]
    return name


def _provenance() -> bytes:
    recipe_root = Path(__file__).resolve().parent
    recipe_inputs = {
        "backend.py": _sha256((recipe_root / "backend.py").read_bytes()),
        PATCH_FILENAME: PATCH_SHA256,
        "pyproject.toml": _sha256((recipe_root / "pyproject.toml").read_bytes()),
    }
    recipe_identity = json.dumps(
        recipe_inputs, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    document = {
        "package": NAME,
        "patch_filename": PATCH_FILENAME,
        "patch_sha256": PATCH_SHA256,
        "patched_offload_sha256": PATCHED_OFFLOAD_SHA256,
        "protocol": "_mm_effective_weight_input_scale",
        "recipe_inputs_sha256": recipe_inputs,
        "recipe_sha256": _sha256(recipe_identity),
        "schema": "maestro/mmgp-wheel-provenance/v1",
        "upstream_offload_sha256": UPSTREAM_OFFLOAD_SHA256,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_url": UPSTREAM_URL,
        "upstream_version": UPSTREAM_VERSION,
        "version": VERSION,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _make_record(contents: dict[str, bytes], record_name: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name in sorted(contents):
        writer.writerow((name, _record_digest(contents[name]), str(len(contents[name]))))
    writer.writerow((record_name, "", ""))
    return stream.getvalue().encode("utf-8")


def _serialize_wheel(contents: dict[str, bytes], modes: dict[str, int]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = modes.get(name, stat.S_IFREG | 0o644) << 16
            archive.writestr(info, contents[name])
    return buffer.getvalue()


def _build_wheel_bytes(upstream_wheel: bytes) -> bytes:
    if _sha256(upstream_wheel) != UPSTREAM_SHA256:
        raise BuildError("upstream wheel SHA-256 does not match the pinned digest")
    upstream, upstream_modes = _read_wheel(upstream_wheel, EXPECTED_UPSTREAM_FILES)
    old_record = f"{OLD_DIST_INFO}/RECORD"
    _validate_record(upstream, old_record)

    output: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for old_name, old_data in upstream.items():
        if old_name == old_record:
            continue
        new_name = _mapped_name(old_name)
        data = old_data
        if old_name == "mmgp/offload.py":
            data = _patch_offload(data)
        elif old_name == f"{OLD_DIST_INFO}/METADATA":
            data = _replace_metadata_version(data)
        elif old_name == f"{OLD_DIST_INFO}/WHEEL":
            data = _replace_wheel_generator(data)
        output[new_name] = data
        modes[new_name] = upstream_modes[old_name]

    output[PROVENANCE_FILENAME] = _provenance()
    modes[PROVENANCE_FILENAME] = stat.S_IFREG | 0o644
    output[RECORD_FILENAME] = _make_record(output, RECORD_FILENAME)
    modes[RECORD_FILENAME] = stat.S_IFREG | 0o644

    expected_output = frozenset(output)
    wheel = _serialize_wheel(output, modes)
    checked, _ = _read_wheel(wheel, expected_output)
    _validate_record(checked, RECORD_FILENAME)
    if _sha256(checked["mmgp/offload.py"]) != PATCHED_OFFLOAD_SHA256:
        raise BuildError("output wheel contains an unexpected MMGP postimage")
    if f"Version: {VERSION}\r\n".encode("ascii") not in checked[f"{NEW_DIST_INFO}/METADATA"]:
        raise BuildError("output wheel metadata has the wrong version")
    expected_wheel_metadata = _replace_wheel_generator(
        upstream[f"{OLD_DIST_INFO}/WHEEL"]
    )
    if checked[f"{NEW_DIST_INFO}/WHEEL"] != expected_wheel_metadata:
        raise BuildError("output WHEEL metadata changed outside the generator field")
    for old_name, old_data in upstream.items():
        if old_name in {
            old_record,
            "mmgp/offload.py",
            f"{OLD_DIST_INFO}/METADATA",
            f"{OLD_DIST_INFO}/WHEEL",
        }:
            continue
        if checked[_mapped_name(old_name)] != old_data:
            raise BuildError(f"unaffected upstream member changed: {old_name!r}")
    return wheel


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory=None) -> str:
    """Build and return the filename of Maestro's deterministic MMGP wheel."""
    if config_settings:
        raise BuildError("this fixed package recipe does not accept build settings")
    if metadata_directory is not None:
        raise BuildError("prepared external metadata is not supported")
    destination = Path(wheel_directory)
    destination.mkdir(parents=True, exist_ok=True)
    wheel = _build_wheel_bytes(_download_upstream_wheel())
    output = destination / WHEEL_FILENAME
    output.write_bytes(wheel)
    return WHEEL_FILENAME
