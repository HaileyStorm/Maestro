"""Durable, project-scoped display names for generation identities.

One registry file belongs to one project.  The only persisted generation data
is an opaque ID, its display name, and the entry revision; callers join parts
to the current name at read time instead of rewriting media metadata.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import unicodedata
import uuid


GENERATION_NAMES_SCHEMA = 1
MAX_GENERATION_ID_CHARS = 256
MAX_GENERATION_NAME_CHARS = 96
MAX_REGISTRY_ENTRIES = 4096
_MAX_GENERATION_ID_BYTES = 1024
_MAX_GENERATION_NAME_BYTES = 384
_MAX_REGISTRY_BYTES = 2 * 1024 * 1024
_MAX_REVISION = 2**63 - 1
_DOCUMENT_KEYS = frozenset({"schema", "entries"})
_ENTRY_KEYS = frozenset({"name", "revision"})

# Fixed, local, and deliberately content-neutral.  Defaults depend only on the
# opaque generation ID and current name occupancy, never prompts or media.
_ADJECTIVES = (
    "Amber", "Azure", "Bright", "Calm", "Cedar", "Clear", "Copper",
    "Coral", "Crimson", "Distant", "Emerald", "Gentle", "Golden",
    "Indigo", "Ivory", "Jade", "Lunar", "Mellow", "Misty", "Quiet",
    "Radiant", "Silver", "Solar", "Still", "Violet", "Warm",
)
_NOUNS = (
    "Brook", "Canyon", "Cedar", "Cloud", "Cove", "Dawn", "Field",
    "Forest", "Garden", "Grove", "Harbor", "Hill", "Horizon", "Lake",
    "Meadow", "Mesa", "Moon", "Ocean", "Pine", "River", "Sky",
    "Stone", "Summit", "Valley", "Willow", "Wind",
)
_DEFAULT_NAME_CAPACITY = len(_ADJECTIVES) * len(_NOUNS)


class GenerationNameError(Exception):
    """Base error for generation-name validation or storage failures."""


class GenerationNameValidationError(GenerationNameError, ValueError):
    """An ID or display name is outside the bounded public contract."""


class GenerationNameStorageError(GenerationNameError):
    """The project registry could not be read or durably updated."""


class GenerationNameNotFoundError(GenerationNameError, LookupError):
    """A rename targeted an ID that has not been allocated."""


class GenerationNameConflictError(GenerationNameError):
    """No unique requested or generated display name is available."""


@dataclass(frozen=True, slots=True)
class GenerationName:
    """The complete public result; it intentionally exposes only three fields."""

    id: str
    name: str
    revision: int


def _has_disallowed_character(value: str, *, identifier: bool) -> bool:
    for character in value:
        category = unicodedata.category(character)
        if category.startswith("C") or category in {"Zl", "Zp"}:
            return True
        if identifier and (character.isspace() or character in "/\\"):
            return True
        if not identifier and character.isspace() and character != " ":
            return True
    return False


def _validated_generation_id(value: object) -> str:
    if not isinstance(value, str):
        raise GenerationNameValidationError("generation_id must be text")
    if not 1 <= len(value) <= MAX_GENERATION_ID_CHARS:
        raise GenerationNameValidationError("generation_id length is invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise GenerationNameValidationError("generation_id must use NFC Unicode")
    if _has_disallowed_character(value, identifier=True):
        raise GenerationNameValidationError("generation_id contains an unsafe character")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise GenerationNameValidationError("generation_id is not valid Unicode") from error
    if len(encoded) > _MAX_GENERATION_ID_BYTES:
        raise GenerationNameValidationError("generation_id is too large")
    return value


def _validated_name(value: object) -> str:
    if not isinstance(value, str):
        raise GenerationNameValidationError("name must be text")
    if not 1 <= len(value) <= MAX_GENERATION_NAME_CHARS:
        raise GenerationNameValidationError("name length is invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise GenerationNameValidationError("name must use NFC Unicode")
    if value != value.strip() or "  " in value:
        raise GenerationNameValidationError("name whitespace is invalid")
    if _has_disallowed_character(value, identifier=False):
        raise GenerationNameValidationError("name contains a control character")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise GenerationNameValidationError("name is not valid Unicode") from error
    if len(encoded) > _MAX_GENERATION_NAME_BYTES:
        raise GenerationNameValidationError("name is too large")
    return value


def _default_start_index(generation_id: str) -> int:
    digest = hashlib.sha256(generation_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _DEFAULT_NAME_CAPACITY


def _windows_replace_write_through(source: Path, target: Path) -> None:
    """Atomically replace *target* and request a durable Windows namespace write."""
    import ctypes

    move_file_ex = ctypes.windll.kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file_ex.restype = ctypes.c_int
    replace_existing = 0x1
    write_through = 0x8
    if not move_file_ex(str(source), str(target), replace_existing | write_through):
        raise ctypes.WinError(ctypes.get_last_error())


def _decode_document(raw: bytes) -> dict[str, GenerationName]:
    if len(raw) > _MAX_REGISTRY_BYTES:
        raise GenerationNameStorageError("generation-name registry is too large")

    def object_without_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GenerationNameStorageError("generation-name keys are duplicated")
            result[key] = value
        return result

    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except GenerationNameStorageError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GenerationNameStorageError("generation-name registry is malformed") from error
    if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
        raise GenerationNameStorageError("generation-name schema is invalid")
    schema = document.get("schema")
    entries = document.get("entries")
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema != GENERATION_NAMES_SCHEMA
        or not isinstance(entries, dict)
        or len(entries) > MAX_REGISTRY_ENTRIES
    ):
        raise GenerationNameStorageError("generation-name values are invalid")

    decoded: dict[str, GenerationName] = {}
    occupied: set[str] = set()
    for generation_id, entry in entries.items():
        try:
            generation_id = _validated_generation_id(generation_id)
            if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
                raise GenerationNameValidationError("entry shape is invalid")
            name = _validated_name(entry.get("name"))
            revision = entry.get("revision")
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or not 1 <= revision <= _MAX_REVISION
            ):
                raise GenerationNameValidationError("entry revision is invalid")
        except GenerationNameValidationError as error:
            raise GenerationNameStorageError("generation-name entry is invalid") from error
        collision_key = name.casefold()
        if collision_key in occupied:
            raise GenerationNameStorageError("generation names are duplicated")
        occupied.add(collision_key)
        decoded[generation_id] = GenerationName(
            id=generation_id,
            name=name,
            revision=revision,
        )
    return decoded


class GenerationNameRegistry:
    """Cross-process-safe durable name registry for exactly one project."""

    def __init__(self, path: str | os.PathLike[str]):
        try:
            raw_path = os.fspath(path)
        except TypeError as error:
            raise GenerationNameValidationError("registry path must be path-like") from error
        if not isinstance(raw_path, str):
            raise GenerationNameValidationError("registry path must be text")
        if not raw_path or "\0" in raw_path:
            raise GenerationNameValidationError("registry path is invalid")
        self.path = Path(os.path.abspath(raw_path))
        if self.path.name in {"", ".", ".."}:
            raise GenerationNameValidationError("registry path must name a file")
        self._lock = threading.RLock()

    @staticmethod
    def _require_safe_directory_chain(directory: Path) -> None:
        lineage = [directory]
        while lineage[-1] != lineage[-1].parent:
            lineage.append(lineage[-1].parent)
        for candidate in reversed(lineage):
            try:
                metadata = os.lstat(candidate)
            except OSError as error:
                raise GenerationNameStorageError(
                    "generation-name directory is unavailable",
                ) from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise GenerationNameStorageError("generation-name directory is unsafe")

    def _ensure_safe_directory_locked(self) -> Path:
        directory = self.path.parent
        while True:
            try:
                metadata = os.lstat(directory)
                break
            except FileNotFoundError:
                raise GenerationNameStorageError(
                    "project directory must already exist",
                )
            except OSError as error:
                raise GenerationNameStorageError(
                    "generation-name directory is unavailable",
                ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise GenerationNameStorageError("generation-name directory is unsafe")
        self._require_safe_directory_chain(directory)
        return directory

    @contextmanager
    def _guard_windows_directory_chain_locked(self, directory: Path):
        """Prevent rename/delete of any Windows path component in a transaction."""
        import ctypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", ctypes.c_uint32),
                ("high", ctypes.c_uint32),
            ]

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", ctypes.c_uint32),
                ("creation_time", FileTime),
                ("last_access_time", FileTime),
                ("last_write_time", FileTime),
                ("volume_serial_number", ctypes.c_uint32),
                ("file_size_high", ctypes.c_uint32),
                ("file_size_low", ctypes.c_uint32),
                ("number_of_links", ctypes.c_uint32),
                ("file_index_high", ctypes.c_uint32),
                ("file_index_low", ctypes.c_uint32),
            ]

        kernel32 = ctypes.windll.kernel32
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [ctypes.c_void_p, ctypes.POINTER(ByHandleFileInformation)]
        get_information.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int

        share_read_only = 0x1
        open_existing = 3
        backup_semantics = 0x02000000
        open_reparse_point = 0x00200000
        directory_attribute = 0x10
        reparse_attribute = 0x400
        invalid_handle = ctypes.c_void_p(-1).value
        lineage = [directory]
        while lineage[-1] != lineage[-1].parent:
            lineage.append(lineage[-1].parent)
        handles = []

        def require_safe_directory_handle(handle) -> None:
            information = ByHandleFileInformation()
            if not get_information(handle, ctypes.byref(information)):
                raise ctypes.WinError(ctypes.get_last_error())
            if (
                not information.attributes & directory_attribute
                or information.attributes & reparse_attribute
            ):
                raise GenerationNameStorageError("generation-name directory is unsafe")

        try:
            for candidate in reversed(lineage):
                handle = create_file(
                    str(candidate),
                    0,
                    share_read_only,
                    None,
                    open_existing,
                    backup_semantics | open_reparse_point,
                    None,
                )
                if handle == invalid_handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
                require_safe_directory_handle(handle)
            self._require_safe_directory_chain(directory)
            yield
            for handle in handles:
                require_safe_directory_handle(handle)
            self._require_safe_directory_chain(directory)
        except GenerationNameStorageError:
            raise
        except OSError as error:
            raise GenerationNameStorageError(
                "generation-name directory cannot be guarded",
            ) from error
        finally:
            for handle in reversed(handles):
                close_handle(handle)

    def _safe_directory_exists_locked(self) -> bool:
        directory = self.path.parent
        cursor = directory
        while True:
            try:
                os.lstat(cursor)
                break
            except FileNotFoundError:
                if cursor == cursor.parent:
                    raise GenerationNameStorageError(
                        "generation-name directory is unavailable",
                    )
                cursor = cursor.parent
            except OSError as error:
                raise GenerationNameStorageError(
                    "generation-name directory is unavailable",
                ) from error
        self._require_safe_directory_chain(cursor)
        if cursor != directory:
            return False
        self._require_safe_directory_chain(directory)
        return True

    def _validate_open_directory_locked(self, descriptor: int) -> None:
        opened = os.fstat(descriptor)
        try:
            current = os.lstat(self.path.parent)
        except OSError as error:
            raise GenerationNameStorageError(
                "generation-name directory changed",
            ) from error
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise GenerationNameStorageError("generation-name directory changed")

    @contextmanager
    def _open_directory_locked(self):
        directory = self._ensure_safe_directory_locked()
        if os.name == "nt":
            with self._guard_windows_directory_chain_locked(directory):
                yield None
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(directory, flags)
        except OSError as error:
            raise GenerationNameStorageError(
                "generation-name directory cannot be opened safely",
            ) from error
        try:
            self._validate_open_directory_locked(descriptor)
            yield descriptor
            self._validate_open_directory_locked(descriptor)
        finally:
            os.close(descriptor)

    def _read_locked(self, directory_descriptor: int | None) -> dict[str, GenerationName]:
        directory = self.path.parent
        if directory_descriptor is not None:
            self._validate_open_directory_locked(directory_descriptor)
            try:
                descriptor = os.open(
                    self.path.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError:
                return {}
            except OSError as error:
                raise GenerationNameStorageError(
                    "generation-name registry cannot be opened",
                ) from error
            before = None
        else:
            self._require_safe_directory_chain(directory)
            try:
                before = os.lstat(self.path)
            except FileNotFoundError:
                return {}
            except OSError as error:
                raise GenerationNameStorageError(
                    "generation-name metadata is unavailable",
                ) from error
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise GenerationNameStorageError("generation-name registry is unsafe")
            try:
                descriptor = os.open(
                    self.path,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError as error:
                raise GenerationNameStorageError(
                    "generation-name registry cannot be opened",
                ) from error
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > _MAX_REGISTRY_BYTES
            ):
                raise GenerationNameStorageError("generation-name registry is invalid")
            if os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600:
                raise GenerationNameStorageError("generation-name registry is not private")
            if before is not None and (
                opened.st_dev != before.st_dev or opened.st_ino != before.st_ino
            ):
                raise GenerationNameStorageError("generation-name registry changed")
            chunks: list[bytes] = []
            remaining = _MAX_REGISTRY_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if directory_descriptor is not None:
            self._validate_open_directory_locked(directory_descriptor)
        return _decode_document(raw)

    @contextmanager
    def _cross_process_lock_locked(self):
        with self._open_directory_locked() as directory_descriptor:
            directory = self.path.parent
            lock_name = f".{self.path.name}.lock"
            lock_path = directory / lock_name
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = None
            try:
                if directory_descriptor is None:
                    descriptor = os.open(lock_path, flags, 0o600)
                    after = os.lstat(lock_path)
                else:
                    descriptor = os.open(
                        lock_name,
                        flags,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    after = os.stat(
                        lock_name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or stat.S_ISLNK(after.st_mode)
                    or opened.st_dev != after.st_dev
                    or opened.st_ino != after.st_ino
                ):
                    raise GenerationNameStorageError("generation-name lock is unsafe")
                if os.name != "nt":
                    os.fchmod(descriptor, 0o600)
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    self._validate_open_directory_locked(directory_descriptor)
                else:
                    import msvcrt

                    if opened.st_size < 1:
                        os.write(descriptor, b"\0")
                        os.fsync(descriptor)
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                yield directory_descriptor
            except GenerationNameStorageError:
                raise
            except OSError as error:
                raise GenerationNameStorageError("generation-name lock is unavailable") from error
            finally:
                if descriptor is not None:
                    try:
                        if os.name != "nt":
                            import fcntl

                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        else:
                            import msvcrt

                            os.lseek(descriptor, 0, os.SEEK_SET)
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                    os.close(descriptor)

    @staticmethod
    def _encoded(entries: dict[str, GenerationName]) -> bytes:
        document = {
            "schema": GENERATION_NAMES_SCHEMA,
            "entries": {
                generation_id: {
                    "name": entry.name,
                    "revision": entry.revision,
                }
                for generation_id, entry in sorted(entries.items())
            },
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
        if len(encoded) > _MAX_REGISTRY_BYTES:
            raise GenerationNameStorageError("generation-name registry is too large")
        return encoded

    def _write_locked(
        self,
        entries: dict[str, GenerationName],
        directory_descriptor: int | None,
    ) -> None:
        directory = self.path.parent
        if directory_descriptor is not None:
            self._validate_open_directory_locked(directory_descriptor)
        else:
            self._require_safe_directory_chain(directory)
        encoded = self._encoded(entries)
        temporary_name = f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        temporary = directory / temporary_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            if directory_descriptor is None:
                descriptor = os.open(temporary, flags, 0o600)
            else:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise GenerationNameStorageError("generation-name write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None

            try:
                if directory_descriptor is None:
                    target = os.lstat(self.path)
                else:
                    target = os.stat(
                        self.path.name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
            except FileNotFoundError:
                target = None
            if target is not None and (
                stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode)
            ):
                raise GenerationNameStorageError("generation-name target is unsafe")
            if directory_descriptor is None:
                _windows_replace_write_through(temporary, self.path)
            else:
                os.replace(
                    temporary_name,
                    self.path.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                )
                os.fsync(directory_descriptor)
                self._validate_open_directory_locked(directory_descriptor)
        except GenerationNameStorageError:
            raise
        except (OSError, UnicodeError) as error:
            raise GenerationNameStorageError("generation-name update was not durable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                if directory_descriptor is None:
                    temporary.unlink()
                else:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    @staticmethod
    def _allocate_default_name(
        generation_id: str,
        entries: dict[str, GenerationName],
    ) -> str:
        occupied = {entry.name.casefold() for entry in entries.values()}
        start = _default_start_index(generation_id)
        for offset in range(_DEFAULT_NAME_CAPACITY):
            index = (start + offset) % _DEFAULT_NAME_CAPACITY
            adjective = _ADJECTIVES[index // len(_NOUNS)]
            noun = _NOUNS[index % len(_NOUNS)]
            candidate = f"{adjective} {noun}"
            if candidate.casefold() not in occupied:
                return candidate
        raise GenerationNameConflictError("default generation-name capacity is exhausted")

    def lookup(self, generation_id: object) -> GenerationName | None:
        """Read the current name for an ID so every part observes later renames."""
        generation_id = _validated_generation_id(generation_id)
        with self._lock:
            if not self._safe_directory_exists_locked():
                return None
            with self._cross_process_lock_locked() as directory_descriptor:
                return self._read_locked(directory_descriptor).get(generation_id)

    def allocate(self, generation_id: object) -> GenerationName:
        """Idempotently allocate a collision-checked two-word default name."""
        generation_id = _validated_generation_id(generation_id)
        with self._lock:
            with self._cross_process_lock_locked() as directory_descriptor:
                entries = self._read_locked(directory_descriptor)
                existing = entries.get(generation_id)
                if existing is not None:
                    return existing
                if len(entries) >= MAX_REGISTRY_ENTRIES:
                    raise GenerationNameConflictError("generation-name registry is full")
                created = GenerationName(
                    id=generation_id,
                    name=self._allocate_default_name(generation_id, entries),
                    revision=1,
                )
                updated = dict(entries)
                updated[generation_id] = created
                self._write_locked(updated, directory_descriptor)
                return created

    def rename(self, generation_id: object, name: object) -> GenerationName:
        """Durably rename one entry; repeated later renames remain supported."""
        generation_id = _validated_generation_id(generation_id)
        name = _validated_name(name)
        with self._lock:
            with self._cross_process_lock_locked() as directory_descriptor:
                entries = self._read_locked(directory_descriptor)
                current = entries.get(generation_id)
                if current is None:
                    raise GenerationNameNotFoundError("generation ID is not registered")
                if current.name == name:
                    return current
                folded = name.casefold()
                if any(
                    other_id != generation_id and entry.name.casefold() == folded
                    for other_id, entry in entries.items()
                ):
                    raise GenerationNameConflictError("generation name is already in use")
                if current.revision >= _MAX_REVISION:
                    raise GenerationNameStorageError("generation-name revision is exhausted")
                renamed = GenerationName(
                    id=generation_id,
                    name=name,
                    revision=current.revision + 1,
                )
                updated = dict(entries)
                updated[generation_id] = renamed
                self._write_locked(updated, directory_descriptor)
                return renamed
