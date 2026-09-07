"""Pure provenance checks for the pinned H3 W4A8 ``comfy_kitchen`` package.

The package digest is SHA-256 over compact JSON mapping every included path,
relative to the ``comfy_kitchen`` package root and written with ``/``
separators, to that file's SHA-256.  JSON keys are sorted, so filesystem walk
order cannot affect the result.  Generated ``__pycache__`` content and
``.pyc``/``.pyo`` files are validated as safe filesystem entries but omitted
from the manifest.

This proves only that the installed package bytes match the pinned manifest.
It does not validate Python, Torch, Triton, CUDA, GPU execution, or kernel
correctness; callers retain those runtime checks.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import importlib.machinery
import json
import ntpath
import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

RUNTIME_REVISION = "b812819a97ac11d01f4a3a16ba47dd38de3b2519"
EXPECTED_PACKAGE_DIGEST = (
    "2028f87be20ad79158b47895280fdc4ecf1491d7c010bfd4058cabf89e2b778b"
)

_MAX_FILES = 512
_MAX_DIRECTORIES = 512
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_DEPTH = 64
_READ_BYTES = 1024 * 1024
_WINDOWS = os.name == "nt"

_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x10
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_SHARE_READ = 0x00000001
_WIN_GENERIC_READ = 0x80000000
_WIN_OPEN_EXISTING = 3
_WIN_FILE_TYPE_DISK = 1

__all__ = [
    "EXPECTED_PACKAGE_DIGEST",
    "RUNTIME_REVISION",
    "locate_pinned_package",
    "marker_package_matches",
    "package_fingerprint",
    "require_pinned_package",
]


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_mode,
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_mode, value.st_dev, value.st_ino


def _safe_component(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("package contains an unsafe path component")
    return name


def _manifest_path(parts: tuple[str, ...]) -> str:
    relative = PurePosixPath(*parts)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("package path is not safely relative")
    return relative.as_posix()


def _canonical_digest(manifest: Mapping[str, str]) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _read_regular_file(
    directory_fd: int,
    name: str,
    expected: os.stat_result,
) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    file_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("package contains a non-regular file")
        if _stat_signature(before) != _stat_signature(expected):
            raise ValueError("package file changed before it could be read")

        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_fd, min(_READ_BYTES, remaining))
            if not chunk:
                raise ValueError("package file was truncated while being read")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            raise ValueError("package file grew while being read")

        after = os.fstat(file_fd)
        if _stat_signature(after) != _stat_signature(before):
            raise ValueError("package file changed while being read")
        return digest.hexdigest()
    finally:
        os.close(file_fd)


def _posix_runtime_entries(
    directory_fd: int,
    *,
    excluded_cache: bool,
) -> dict[str, tuple[str, tuple[int, ...]]]:
    """Snapshot manifest-relevant entries after a descriptor traversal."""

    result: dict[str, tuple[str, tuple[int, ...]]] = {}
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            name = _safe_component(entry.name)
            information = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(information.st_mode):
                raise ValueError("package contains a symbolic link")
            if stat.S_ISDIR(information.st_mode):
                if not excluded_cache and name != "__pycache__":
                    result[name] = ("directory", _directory_identity(information))
                continue
            if not stat.S_ISREG(information.st_mode):
                raise ValueError("package contains a non-regular filesystem entry")
            if not excluded_cache and not name.endswith((".pyc", ".pyo")):
                result[name] = ("file", _stat_signature(information))
    return result


def _walk_package(
    directory_fd: int,
    parts: tuple[str, ...],
    manifest: dict[str, str],
    counts: list[int],
    *,
    excluded_cache: bool,
) -> None:
    if len(parts) > _MAX_DEPTH:
        raise ValueError("package directory nesting exceeds the safety limit")

    entries = sorted(os.scandir(directory_fd), key=lambda item: item.name)
    stable_entries: dict[str, tuple[str, tuple[int, ...]]] = {}
    for entry in entries:
        name = _safe_component(entry.name)
        relative_parts = (*parts, name)
        before = entry.stat(follow_symlinks=False)

        if stat.S_ISLNK(before.st_mode):
            raise ValueError("package contains a symbolic link")

        if stat.S_ISDIR(before.st_mode):
            counts[1] += 1
            if counts[1] > _MAX_DIRECTORIES:
                raise ValueError("package directory count exceeds the safety limit")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
            )
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                if not stat.S_ISDIR(opened.st_mode):
                    raise ValueError("package contains a non-directory path")
                if _directory_identity(opened) != _directory_identity(before):
                    raise ValueError("package directory changed during traversal")
                _walk_package(
                    child_fd,
                    relative_parts,
                    manifest,
                    counts,
                    excluded_cache=excluded_cache or name == "__pycache__",
                )
                if _directory_identity(os.fstat(child_fd)) != _directory_identity(opened):
                    raise ValueError("package directory changed during traversal")
            finally:
                os.close(child_fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _directory_identity(current) != _directory_identity(before):
                raise ValueError("package directory changed during traversal")
            if not excluded_cache and name != "__pycache__":
                stable_entries[name] = (
                    "directory",
                    _directory_identity(current),
                )
            continue

        if not stat.S_ISREG(before.st_mode):
            raise ValueError("package contains a non-regular filesystem entry")

        counts[0] += 1
        if counts[0] > _MAX_FILES:
            raise ValueError("package file count exceeds the safety limit")
        if before.st_size < 0 or before.st_size > _MAX_FILE_BYTES:
            raise ValueError("package file size exceeds the safety limit")
        counts[2] += before.st_size
        if counts[2] > _MAX_TOTAL_BYTES:
            raise ValueError("package total size exceeds the safety limit")

        file_digest = _read_regular_file(directory_fd, name, before)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _stat_signature(current) != _stat_signature(before):
            raise ValueError("package file changed during traversal")

        if excluded_cache or name.endswith((".pyc", ".pyo")):
            continue
        stable_entries[name] = ("file", _stat_signature(current))
        relative = _manifest_path(relative_parts)
        if relative in manifest:
            raise ValueError("package contains duplicate normalized paths")
        manifest[relative] = file_digest

    if _posix_runtime_entries(
        directory_fd,
        excluded_cache=excluded_cache,
    ) != stable_entries:
        raise ValueError("package directory contents changed during traversal")


def _package_fingerprint_posix(root: Path) -> str:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("this platform cannot safely validate package paths")
    if os.scandir not in os.supports_fd or os.open not in os.supports_dir_fd:
        raise ValueError("this platform lacks descriptor-anchored package traversal")

    try:
        before = os.lstat(root)
        if stat.S_ISLNK(before.st_mode):
            raise ValueError("package root must not be a symbolic link")
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("package root must be a directory")

        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
        )
        root_fd = os.open(root, flags)
        try:
            opened = os.fstat(root_fd)
            if _directory_identity(opened) != _directory_identity(before):
                raise ValueError("package root changed before traversal")
            manifest: dict[str, str] = {}
            _walk_package(root_fd, (), manifest, [0, 0, 0], excluded_cache=False)
            if _directory_identity(os.fstat(root_fd)) != _directory_identity(opened):
                raise ValueError("package root changed during traversal")
        finally:
            os.close(root_fd)

        current = os.lstat(root)
        if _directory_identity(current) != _directory_identity(before):
            raise ValueError("package root changed during traversal")
    except ValueError:
        raise
    except (OSError, TypeError) as error:
        raise ValueError("package could not be safely fingerprinted") from error

    return _canonical_digest(manifest)


class _WinFileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _WinByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("creation_time", _WinFileTime),
        ("last_access_time", _WinFileTime),
        ("last_write_time", _WinFileTime),
        ("volume_serial", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("link_count", ctypes.c_uint32),
        ("file_index_high", ctypes.c_uint32),
        ("file_index_low", ctypes.c_uint32),
    ]


class _WinFileBasicInformation(ctypes.Structure):
    _fields_ = [
        ("creation_time", ctypes.c_int64),
        ("last_access_time", ctypes.c_int64),
        ("last_write_time", ctypes.c_int64),
        ("change_time", ctypes.c_int64),
        ("attributes", ctypes.c_uint32),
    ]


class _WinFileId128(ctypes.Structure):
    _fields_ = [("identifier", ctypes.c_ubyte * 16)]


class _WinFileIdInformation(ctypes.Structure):
    _fields_ = [
        ("volume_serial", ctypes.c_uint64),
        ("file_id", _WinFileId128),
    ]


@dataclass(frozen=True)
class _WindowsSnapshot:
    identity: tuple[Any, ...]
    attributes: int
    size: int
    creation_time: int
    last_write_time: int
    change_time: int
    final_path_key: str

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY)

    @property
    def is_reparse_point(self) -> bool:
        return bool(self.attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT)

    @property
    def directory_identity(self) -> tuple[Any, ...]:
        return self.identity, self.attributes

    @property
    def file_signature(self) -> tuple[Any, ...]:
        return (
            self.identity,
            self.attributes,
            self.size,
            self.creation_time,
            self.last_write_time,
            self.change_time,
        )


def _windows_path_key(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


class _WindowsNativeApi:
    """Minimal synchronous Win32 handle adapter used by the pure walker."""

    def __init__(self) -> None:
        if not _WINDOWS or not hasattr(ctypes, "WinDLL"):
            raise ValueError("Windows handle validation is unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create_file = kernel32.CreateFileW
        self._create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._create_file.restype = ctypes.c_void_p

        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = [ctypes.c_void_p]
        self._close_handle.restype = ctypes.c_int

        self._get_basic_information = kernel32.GetFileInformationByHandleEx
        self._get_basic_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._get_basic_information.restype = ctypes.c_int

        self._get_information = kernel32.GetFileInformationByHandle
        self._get_information.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WinByHandleFileInformation),
        ]
        self._get_information.restype = ctypes.c_int

        self._get_file_type = kernel32.GetFileType
        self._get_file_type.argtypes = [ctypes.c_void_p]
        self._get_file_type.restype = ctypes.c_uint32

        self._get_final_path = kernel32.GetFinalPathNameByHandleW
        self._get_final_path.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self._get_final_path.restype = ctypes.c_uint32

        self._read_file = kernel32.ReadFile
        self._read_file.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._read_file.restype = ctypes.c_int

    @staticmethod
    def _raise() -> None:
        raise OSError(ctypes.get_last_error(), "Windows package handle operation failed")

    def open(self, path: Path) -> int:
        handle = self._create_file(
            os.fspath(path),
            _WIN_GENERIC_READ,
            _WIN_FILE_SHARE_READ,
            None,
            _WIN_OPEN_EXISTING,
            _WIN_FILE_FLAG_BACKUP_SEMANTICS | _WIN_FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            self._raise()
        return handle

    def close(self, handle: int) -> None:
        if not self._close_handle(handle):
            self._raise()

    def _final_path_key(self, handle: int) -> str:
        length = 512
        while length <= 32768:
            buffer = ctypes.create_unicode_buffer(length)
            result = self._get_final_path(handle, buffer, length, 0)
            if result == 0:
                self._raise()
            if result < length:
                return _windows_path_key(buffer.value)
            length = result + 1
        raise ValueError("Windows package path exceeds the safety limit")

    def snapshot(self, handle: int) -> _WindowsSnapshot:
        if self._get_file_type(handle) != _WIN_FILE_TYPE_DISK:
            raise ValueError("package contains a non-disk filesystem entry")

        basic = _WinFileBasicInformation()
        if not self._get_basic_information(
            handle,
            0,
            ctypes.byref(basic),
            ctypes.sizeof(basic),
        ):
            self._raise()
        information = _WinByHandleFileInformation()
        if not self._get_information(handle, ctypes.byref(information)):
            self._raise()
        if basic.attributes != information.attributes:
            raise ValueError("package attributes changed during inspection")

        extended_id = _WinFileIdInformation()
        if not self._get_basic_information(
            handle,
            18,
            ctypes.byref(extended_id),
            ctypes.sizeof(extended_id),
        ):
            self._raise()
        identity: tuple[Any, ...] = (
            extended_id.volume_serial,
            bytes(extended_id.file_id.identifier),
        )

        return _WindowsSnapshot(
            identity=identity,
            attributes=basic.attributes,
            size=(information.size_high << 32) | information.size_low,
            creation_time=basic.creation_time,
            last_write_time=basic.last_write_time,
            change_time=basic.change_time,
            final_path_key=self._final_path_key(handle),
        )

    def scan_names(self, path: Path) -> list[str]:
        with os.scandir(path) as entries:
            return sorted(entry.name for entry in entries)

    def read_digest(self, handle: int, size: int) -> str:
        digest = hashlib.sha256()
        remaining = size
        buffer = ctypes.create_string_buffer(_READ_BYTES)
        while remaining:
            requested = min(_READ_BYTES, remaining)
            read = ctypes.c_uint32()
            if not self._read_file(handle, buffer, requested, ctypes.byref(read), None):
                self._raise()
            if not read.value:
                raise ValueError("package file was truncated while being read")
            digest.update(buffer.raw[: read.value])
            remaining -= read.value

        read = ctypes.c_uint32()
        if not self._read_file(handle, buffer, 1, ctypes.byref(read), None):
            self._raise()
        if read.value:
            raise ValueError("package file grew while being read")
        return digest.hexdigest()

    def root_path_key(self, root: Path) -> str:
        return _windows_path_key(os.path.abspath(os.fspath(root)))

    @staticmethod
    def expected_path_key(root_key: str, parts: tuple[str, ...]) -> str:
        return _windows_path_key(ntpath.join(root_key, *parts))


def _checked_windows_snapshot(
    api: Any,
    handle: Any,
    expected_path_key: str,
) -> _WindowsSnapshot:
    snapshot = api.snapshot(handle)
    if snapshot.is_reparse_point:
        raise ValueError("package contains a Windows reparse point")
    if snapshot.final_path_key != expected_path_key:
        raise ValueError("package handle escaped its expected path")
    return snapshot


def _revalidate_windows_path(
    api: Any,
    path: Path,
    expected: _WindowsSnapshot,
    *,
    directory: bool,
) -> None:
    handle = api.open(path)
    try:
        current = _checked_windows_snapshot(api, handle, expected.final_path_key)
        current_signature = (
            current.directory_identity if directory else current.file_signature
        )
        expected_signature = (
            expected.directory_identity if directory else expected.file_signature
        )
        if current_signature != expected_signature:
            raise ValueError("package path changed during traversal")
    finally:
        api.close(handle)


def _walk_package_windows(
    api: Any,
    directory_path: Path,
    root_path_key: str,
    parts: tuple[str, ...],
    manifest: dict[str, str],
    counts: list[int],
    *,
    excluded_cache: bool,
) -> None:
    if len(parts) > _MAX_DEPTH:
        raise ValueError("package directory nesting exceeds the safety limit")

    names = api.scan_names(directory_path)
    for raw_name in names:
        name = _safe_component(raw_name)
        relative_parts = (*parts, name)
        expected_key = api.expected_path_key(root_path_key, relative_parts)
        path = directory_path / name
        handle = api.open(path)
        try:
            before = _checked_windows_snapshot(api, handle, expected_key)
            if before.is_directory:
                counts[1] += 1
                if counts[1] > _MAX_DIRECTORIES:
                    raise ValueError("package directory count exceeds the safety limit")
                _walk_package_windows(
                    api,
                    path,
                    root_path_key,
                    relative_parts,
                    manifest,
                    counts,
                    excluded_cache=excluded_cache or name == "__pycache__",
                )
                after = _checked_windows_snapshot(api, handle, expected_key)
                if after.directory_identity != before.directory_identity:
                    raise ValueError("package directory changed during traversal")
            else:
                counts[0] += 1
                if counts[0] > _MAX_FILES:
                    raise ValueError("package file count exceeds the safety limit")
                if before.size < 0 or before.size > _MAX_FILE_BYTES:
                    raise ValueError("package file size exceeds the safety limit")
                counts[2] += before.size
                if counts[2] > _MAX_TOTAL_BYTES:
                    raise ValueError("package total size exceeds the safety limit")

                file_digest = api.read_digest(handle, before.size)
                after = _checked_windows_snapshot(api, handle, expected_key)
                if after.file_signature != before.file_signature:
                    raise ValueError("package file changed while being read")
                if not excluded_cache and not name.endswith((".pyc", ".pyo")):
                    relative = _manifest_path(relative_parts)
                    if relative in manifest:
                        raise ValueError("package contains duplicate normalized paths")
                    manifest[relative] = file_digest
            _revalidate_windows_path(
                api,
                path,
                before,
                directory=before.is_directory,
            )
        finally:
            api.close(handle)
    if api.scan_names(directory_path) != names:
        raise ValueError("package directory contents changed during traversal")


def _package_fingerprint_windows(root: Path, *, _api: Any | None = None) -> str:
    api = _api or _WindowsNativeApi()
    root_key = api.root_path_key(root)
    handle = api.open(root)
    try:
        before = _checked_windows_snapshot(api, handle, root_key)
        if not before.is_directory:
            raise ValueError("package root must be a directory")
        manifest: dict[str, str] = {}
        _walk_package_windows(
            api,
            root,
            root_key,
            (),
            manifest,
            [0, 0, 0],
            excluded_cache=False,
        )
        after = _checked_windows_snapshot(api, handle, root_key)
        if after.directory_identity != before.directory_identity:
            raise ValueError("package root changed during traversal")
        _revalidate_windows_path(api, root, before, directory=True)
    except ValueError:
        raise
    except (OSError, TypeError) as error:
        raise ValueError("package could not be safely fingerprinted") from error
    finally:
        api.close(handle)

    return _canonical_digest(manifest)


def package_fingerprint(package_root: Path) -> str:
    """Return the canonical digest of a safe, stable package directory.

    POSIX traversal is descriptor-anchored.  Windows traversal uses native
    handles opened with reparse processing disabled, normalized handle paths,
    and stable file identity around reads.  Both refuse links, special files,
    unsafe names, excessive input, and mutation.  Regular hardlinks are valid.
    """

    try:
        root = Path(package_root)
    except (TypeError, OSError) as error:
        raise ValueError("package root is invalid") from error
    if ".." in root.parts:
        raise ValueError("package root must not contain path traversal")
    if _WINDOWS:
        return _package_fingerprint_windows(root)
    return _package_fingerprint_posix(root)


def require_pinned_package(package_root: Path) -> str:
    """Return the fresh digest or raise when package bytes are not pinned."""

    digest = package_fingerprint(package_root)
    if not hmac.compare_digest(digest, EXPECTED_PACKAGE_DIGEST):
        raise ValueError("comfy_kitchen package does not match the pinned runtime")
    return digest


def locate_pinned_package() -> tuple[Path, str]:
    """Locate and validate ``comfy_kitchen`` without importing its code."""

    try:
        spec = importlib.machinery.PathFinder.find_spec("comfy_kitchen", sys.path)
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as error:
        raise ValueError("comfy_kitchen could not be located safely") from error
    if (
        spec is None
        or spec.origin is None
        or spec.submodule_search_locations is None
    ):
        raise ValueError("comfy_kitchen is not a regular Python package")

    try:
        origin = Path(spec.origin).absolute()
        locations = [Path(value).absolute() for value in spec.submodule_search_locations]
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("comfy_kitchen package paths are invalid") from error
    if origin.name != "__init__.py" or len(locations) != 1:
        raise ValueError("comfy_kitchen must have one __init__.py package origin")
    root = origin.parent
    if os.path.normcase(os.fspath(locations[0])) != os.path.normcase(os.fspath(root)):
        raise ValueError("comfy_kitchen package origin is inconsistent")

    digest = require_pinned_package(root)
    return root, digest


def marker_package_matches(marker: Mapping[str, Any], package_root: Path) -> bool:
    """Return whether a schema-v2 marker and fresh package bytes are pinned."""

    if not isinstance(marker, Mapping):
        return False
    if type(marker.get("schema_version")) is not int or marker["schema_version"] != 2:
        return False
    if marker.get("runtime_revision") != RUNTIME_REVISION:
        return False
    marker_digest = marker.get("package_digest")
    if not isinstance(marker_digest, str) or not hmac.compare_digest(
        marker_digest,
        EXPECTED_PACKAGE_DIGEST,
    ):
        return False
    try:
        fresh_digest = package_fingerprint(package_root)
    except (TypeError, ValueError, OSError):
        return False
    return hmac.compare_digest(fresh_digest, EXPECTED_PACKAGE_DIGEST)
