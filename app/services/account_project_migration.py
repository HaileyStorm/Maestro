"""Content-free, immutable first-owner project census and binding ledger."""
from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import stat
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

LEDGER_SCHEMA_VERSION = 2
PROJECT_MARKER_NAME = ".maestro-project-instance"
_MAX_LEDGER_BYTES = 4 * 1024 * 1024
_MAX_MARKER_BYTES = 64
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DOMAINS = {
    "seal": b"maestro-project-migration-ledger-v2\0",
    "root": b"maestro-project-migration-root-v2\0",
    "name": b"maestro-project-migration-name-v2\0",
    "project": b"maestro-project-migration-identity-v2\0",
    "marker": b"maestro-project-migration-marker-v2\0",
}
_REASONS = frozenset({
    "eligible", "missing_marker", "reserved_default_collision", "unsafe_name",
    "symlink", "unsafe_directory", "unsafe_marker", "corrupt_marker",
    "duplicate_marker",
})


class ProjectMigrationError(RuntimeError):
    pass


class ProjectMigrationSafetyError(ProjectMigrationError):
    pass


class ProjectMigrationCorruptError(ProjectMigrationError):
    pass


class ProjectMigrationConflictError(ProjectMigrationError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _mac(secret: bytes, domain: str, value: bytes) -> str:
    return hmac.new(secret, _DOMAINS[domain] + value, hashlib.sha256).hexdigest()


def _directory_info(path: Path) -> os.stat_result:
    lineage = [path]
    while lineage[-1] != lineage[-1].parent:
        lineage.append(lineage[-1].parent)
    try:
        for candidate in reversed(lineage):
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ProjectMigrationSafetyError("migration directory is unsafe")
    except ProjectMigrationError:
        raise
    except OSError as error:
        raise ProjectMigrationSafetyError("migration directory is unavailable") from error
    return info


def _bounded_read(descriptor: int, limit: int) -> bytes:
    chunks, remaining = [], limit + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > limit:
        raise ProjectMigrationCorruptError("migration metadata is too large")
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _safe_name(name: str) -> bool:
    try:
        encoded = name.encode()
    except UnicodeEncodeError:
        return False
    return (
        0 < len(encoded) <= 255 and name not in {".", ".."}
        and not name.startswith((".", "_"))
        and "/" not in name and "\\" not in name
        and unicodedata.normalize("NFC", name) == name
        and not any(unicodedata.category(char).startswith("C") for char in name)
    )


class AccountProjectMigrationLedger:
    """Atomically publish or exactly replay an observation-only cutover."""

    def __init__(self, path: os.PathLike[str] | str, secret: bytes) -> None:
        raw = os.fspath(path)
        if not raw or "\0" in raw:
            raise ValueError("migration ledger path is invalid")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("migration ledger secret must contain at least 32 bytes")
        self.path = Path(os.path.abspath(raw))
        if self.path.name in {"", ".", ".."}:
            raise ValueError("migration ledger path must name a file")
        self._secret = secret
        self._thread_lock = threading.RLock()

    def migrate(
        self, projects_root: os.PathLike[str] | str, owner_account_id: str,
        *, require_existing: bool = False,
    ) -> dict[str, Any]:
        return self.migrate_inventory(
            projects_root, owner_account_id, require_existing=require_existing,
        )

    def migrate_inventory(
        self, projects_root: os.PathLike[str] | str, owner_account_id: str,
        *, require_existing: bool = False,
        expected_inventory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit an explicit inventory after the caller creates the owner."""
        root = self._inventory_root(projects_root, owner_account_id)
        with self._thread_lock, self._lock() as directory_fd:
            proposal = self._build(root, owner_account_id)
            if expected_inventory is not None:
                try:
                    expected = self._validate(deepcopy(expected_inventory))
                except ProjectMigrationError as error:
                    raise ProjectMigrationSafetyError(
                        "migration preview is invalid",
                    ) from error
                if not hmac.compare_digest(
                    _canonical(expected), _canonical(proposal),
                ):
                    raise ProjectMigrationSafetyError(
                        "projects changed after migration preview",
                    )
            current = self._read(directory_fd)
            if current is None and require_existing:
                raise ProjectMigrationCorruptError(
                    "initialized project migration ledger is missing",
                )
            if current is not None:
                if not hmac.compare_digest(_canonical(current), _canonical(proposal)):
                    raise ProjectMigrationConflictError(
                        "published migration conflicts with the current census",
                    )
                return deepcopy(current)
            self._write(_canonical(proposal) + b"\n", directory_fd)
            current = self._read(directory_fd)
            if current != proposal:
                raise ProjectMigrationSafetyError("migration publication was not durable")
            return deepcopy(current)

    def inspect_inventory(
        self,
        projects_root: os.PathLike[str] | str,
        owner_account_id: str,
    ) -> dict[str, Any]:
        """Build a read-only census without reading or publishing a ledger."""
        root = self._inventory_root(projects_root, owner_account_id)
        with self._thread_lock:
            return deepcopy(self._build(root, owner_account_id))

    def load_if_present(self) -> dict[str, Any] | None:
        """Read an existing ledger without creating synchronization state."""
        with self._thread_lock:
            return self._read(None)

    def matches_root(
        self,
        ledger: dict[str, Any],
        projects_root: os.PathLike[str] | str,
    ) -> bool:
        """Return whether a sealed ledger belongs to the current output root."""
        current = self._validate(deepcopy(ledger))
        raw_root = os.fspath(projects_root)
        if not raw_root or "\0" in raw_root or not os.path.isabs(raw_root):
            raise ValueError("projects root must be an explicit absolute path")
        root = Path(os.path.abspath(raw_root))
        return hmac.compare_digest(
            current["root_binding"],
            self._root_binding(root),
        )

    def _inventory_root(
        self,
        projects_root: os.PathLike[str] | str,
        owner_account_id: str,
    ) -> Path:
        if not isinstance(owner_account_id, str) or not _HEX32.fullmatch(owner_account_id):
            raise ValueError("migration owner account id is invalid")
        raw_root = os.fspath(projects_root)
        if not raw_root or "\0" in raw_root or not os.path.isabs(raw_root):
            raise ValueError("projects root must be an explicit absolute path")
        root = Path(os.path.abspath(raw_root))
        try:
            nested = os.path.commonpath((str(root), str(self.path))) == str(root)
        except ValueError:
            nested = False
        if nested:
            raise ValueError("migration ledger must be outside the projects root")
        return root

    def load(self, *, required: bool = False) -> dict[str, Any] | None:
        with self._thread_lock, self._lock() as directory_fd:
            value = self._read(directory_fd)
            if value is None and required:
                raise ProjectMigrationCorruptError(
                    "initialized project migration ledger is missing",
                )
            return None if value is None else deepcopy(value)

    def _seal(self, value: dict[str, Any]) -> str:
        return _mac(
            self._secret, "seal",
            _canonical({key: item for key, item in value.items() if key != "seal"}),
        )

    def _root_binding(self, root: Path) -> str:
        return _mac(
            self._secret,
            "root",
            os.fsencode(os.path.normcase(str(root))),
        )

    def _build(self, root: Path, owner: str) -> dict[str, Any]:
        root_info = _directory_info(root)
        identity = (root_info.st_dev, root_info.st_ino)
        root_fd = None
        try:
            if os.name != "nt":
                root_fd = os.open(
                    root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                opened = os.fstat(root_fd)
                if (opened.st_dev, opened.st_ino) != identity:
                    raise ProjectMigrationSafetyError("projects root changed")
            names = sorted(os.listdir(root_fd if root_fd is not None else root), key=os.fsencode)
            rows, excluded = self._census(root, root_fd, names, owner)
            refreshed_names = sorted(
                os.listdir(root_fd if root_fd is not None else root), key=os.fsencode,
            )
            if refreshed_names != names:
                raise ProjectMigrationSafetyError("projects root changed during census")
            verified_rows, verified_excluded = self._census(
                root, root_fd, refreshed_names, owner,
            )
            if not hmac.compare_digest(
                _canonical([rows, excluded]),
                _canonical([verified_rows, verified_excluded]),
            ):
                raise ProjectMigrationSafetyError("project evidence changed during census")
            rows, excluded = verified_rows, verified_excluded
            current = _directory_info(root)
            if (current.st_dev, current.st_ino) != identity:
                raise ProjectMigrationSafetyError("projects root changed")
            if root_fd is not None:
                opened = os.fstat(root_fd)
                if (opened.st_dev, opened.st_ino) != identity:
                    raise ProjectMigrationSafetyError("projects root changed")
        except ProjectMigrationError:
            raise
        except OSError as error:
            raise ProjectMigrationSafetyError("projects root census failed") from error
        finally:
            if root_fd is not None:
                os.close(root_fd)
        document = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "generation": 1,
            "root_binding": self._root_binding(root),
            "migration_kind": "first_owner_cutover",
            "owner_account_id": owner,
            "entries": rows,
            "excluded_non_projects": excluded,
            "census_digest": hashlib.sha256(_canonical({
                "entries": rows,
                "excluded_non_projects": excluded,
            })).hexdigest(),
        }
        document["seal"] = self._seal(document)
        return self._validate(document)

    def _census(
        self, root: Path, root_fd: int | None, names: list[str], owner: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = [self._default(root, root_fd, owner)]
        excluded_evidence: list[dict[str, str]] = []
        kind_counts: dict[str, int] = {}
        for name in names:
            try:
                info = (
                    os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                    if root_fd is not None else os.lstat(root / name)
                )
            except OSError as error:
                raise ProjectMigrationSafetyError(
                    "projects root entry is unavailable",
                ) from error
            if stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                rows.append(self._entry(root, root_fd, name, owner))
                continue
            kind = "regular_file" if stat.S_ISREG(info.st_mode) else "other"
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            excluded_evidence.append({
                "kind": kind,
                "name_digest": _mac(self._secret, "name", os.fsencode(name)),
            })
        self._deduplicate(rows)
        return rows, {
            "count": len(excluded_evidence),
            "kinds": dict(sorted(kind_counts.items())),
            "digest": hashlib.sha256(_canonical(excluded_evidence)).hexdigest(),
        }

    def _row(self, source: str, name: str, owner: str) -> dict[str, Any]:
        encoded = os.fsencode(name)
        return {
            "source": source, "name": name,
            "name_digest": _mac(self._secret, "name", encoded),
            "candidate_kind": "directory", "marker_state": "missing",
            "marker": None,
            "marker_digest": _mac(self._secret, "marker", b"missing"),
            "project_digest": _mac(
                self._secret, "project",
                b"source-name\0" + source.encode("ascii") + b"\0" + encoded,
            ),
            "disposition": "owned",
            "account_bindings": [{"account_id": owner, "role": "owner"}],
            "reason": "missing_marker", "recoverable": False,
        }

    def _default(self, root: Path, root_fd: int | None, owner: str) -> dict[str, Any]:
        row = self._row("default", "default", owner)
        self._marker(row, root, root_fd)
        if row["marker_state"] == "missing":
            self._quarantine(row, "directory", "missing_marker")
        return row

    def _entry(
        self, root: Path, root_fd: int | None, name: str, owner: str,
    ) -> dict[str, Any]:
        row = self._row("entry", name, owner)
        try:
            info = (
                os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if root_fd is not None else os.lstat(root / name)
            )
        except OSError as error:
            raise ProjectMigrationSafetyError(
                "project candidate is unavailable",
            ) from error
        if stat.S_ISLNK(info.st_mode):
            return self._quarantine(row, "symlink", "symlink")
        if not stat.S_ISDIR(info.st_mode):
            raise ProjectMigrationSafetyError("project candidate changed type")
        if name == "default":
            return self._quarantine(row, "directory", "reserved_default_collision")
        if not _safe_name(name):
            return self._quarantine(row, "directory", "unsafe_name")
        child_fd = None
        try:
            if root_fd is not None:
                child_fd = os.open(
                    name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd,
                )
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    return self._quarantine(row, "directory", "unsafe_directory")
            self._marker(row, root / name, child_fd)
            refreshed = (
                os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if root_fd is not None else os.lstat(root / name)
            )
            if not stat.S_ISDIR(refreshed.st_mode) or (
                refreshed.st_dev, refreshed.st_ino
            ) != (info.st_dev, info.st_ino):
                return self._quarantine(row, "directory", "unsafe_directory")
            if row["marker_state"] == "missing":
                self._quarantine(row, "directory", "missing_marker")
        except OSError:
            return self._quarantine(row, "directory", "unsafe_directory")
        finally:
            if child_fd is not None:
                os.close(child_fd)
        return row

    def _marker(self, row: dict[str, Any], directory: Path, dir_fd: int | None) -> None:
        target: str | Path = PROJECT_MARKER_NAME if dir_fd is not None else directory / PROJECT_MARKER_NAME
        kwargs = {"dir_fd": dir_fd} if dir_fd is not None else {}
        try:
            before = (
                os.stat(PROJECT_MARKER_NAME, dir_fd=dir_fd, follow_symlinks=False)
                if dir_fd is not None else os.lstat(target)
            )
        except FileNotFoundError:
            return
        except OSError:
            self._quarantine(row, "directory", "unsafe_marker")
            return
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 64:
            self._quarantine(row, "directory", "unsafe_marker")
            return
        descriptor = None
        try:
            descriptor = os.open(
                target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0), **kwargs,
            )
            opened = os.fstat(descriptor)
            raw = _bounded_read(descriptor, _MAX_MARKER_BYTES)
            after_fd = os.fstat(descriptor)
            after = (
                os.stat(PROJECT_MARKER_NAME, dir_fd=dir_fd, follow_symlinks=False)
                if dir_fd is not None else os.lstat(target)
            )
            ids = {(item.st_dev, item.st_ino) for item in (before, opened, after_fd, after)}
            if (
                len(ids) != 1 or len(raw) != after_fd.st_size
                or any(not stat.S_ISREG(item.st_mode) or item.st_nlink != 1
                       for item in (opened, after_fd, after))
            ):
                raise OSError("marker changed")
        except (OSError, ProjectMigrationCorruptError):
            self._quarantine(row, "directory", "unsafe_marker")
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)
        row["marker_digest"] = _mac(self._secret, "marker", raw)
        try:
            marker = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            marker = ""
        if not _HEX32.fullmatch(marker):
            self._quarantine(row, "directory", "corrupt_marker")
            row["marker_state"] = "corrupt"
            return
        row.update({
            "marker_state": "valid", "marker": marker,
            "project_digest": _mac(
                self._secret, "project", b"marker\0" + marker.encode(),
            ),
            "reason": "eligible",
        })

    @staticmethod
    def _quarantine(row: dict[str, Any], kind: str, reason: str) -> dict[str, Any]:
        row.update({
            "candidate_kind": kind, "disposition": "quarantined",
            "account_bindings": [], "reason": reason, "recoverable": True,
        })
        if reason == "unsafe_marker":
            row["marker_state"] = "unsafe"
        return row

    def _deduplicate(self, rows: list[dict[str, Any]]) -> None:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row["marker_state"] == "valid":
                groups.setdefault(row["project_digest"], []).append(row)
        for digest, group in groups.items():
            if len(group) > 1:
                for row in group:
                    self._quarantine(row, row["candidate_kind"], "duplicate_marker")
                    row["project_digest"] = _mac(
                        self._secret, "project",
                        b"duplicate\0" + digest.encode() + b"\0" + row["name_digest"].encode(),
                    )

    def _validate(self, value: Any) -> dict[str, Any]:
        keys = {
            "schema_version", "generation", "root_binding", "migration_kind",
            "owner_account_id", "entries", "excluded_non_projects",
            "census_digest", "seal",
        }
        try:
            valid = (
                isinstance(value, dict) and set(value) == keys
                and type(value["schema_version"]) is int
                and value["schema_version"] == LEDGER_SCHEMA_VERSION
                and type(value["generation"]) is int and value["generation"] == 1
                and value["migration_kind"] == "first_owner_cutover"
                and bool(_HEX32.fullmatch(value["owner_account_id"]))
                and bool(_HEX64.fullmatch(value["root_binding"]))
                and bool(_HEX64.fullmatch(value["census_digest"]))
                and bool(_HEX64.fullmatch(value["seal"]))
                and isinstance(value["entries"], list) and value["entries"]
                and self._valid_excluded_non_projects(
                    value["excluded_non_projects"],
                )
                and hmac.compare_digest(value["seal"], self._seal(value))
                and hmac.compare_digest(
                    value["census_digest"],
                    hashlib.sha256(_canonical({
                        "entries": value["entries"],
                        "excluded_non_projects": value["excluded_non_projects"],
                    })).hexdigest(),
                )
            )
            if not valid:
                raise ProjectMigrationCorruptError("project migration ledger is corrupt")
            sources, projects = set(), set()
            for index, row in enumerate(value["entries"]):
                self._validate_row(row, value["owner_account_id"])
                source, project = (row["source"], row["name"]), row["project_digest"]
                if source in sources or project in projects:
                    raise ProjectMigrationCorruptError("project migration ledger is corrupt")
                sources.add(source)
                projects.add(project)
                if (
                    (index == 0 and (
                        source != ("default", "default")
                        or row["candidate_kind"] != "directory"
                    ))
                    or (index > 0 and row["source"] != "entry")
                ):
                    raise ProjectMigrationCorruptError("project migration ledger is corrupt")
            return value
        except ProjectMigrationCorruptError:
            raise
        except (KeyError, TypeError, ValueError, RecursionError, OverflowError) as error:
            raise ProjectMigrationCorruptError("project migration ledger is corrupt") from error

    @staticmethod
    def _valid_excluded_non_projects(value: Any) -> bool:
        if (
            not isinstance(value, dict)
            or set(value) != {"count", "kinds", "digest"}
            or not isinstance(value.get("count"), int)
            or isinstance(value.get("count"), bool)
            or value["count"] < 0
            or not isinstance(value.get("kinds"), dict)
            or set(value["kinds"]) - {"regular_file", "other"}
            or not _HEX64.fullmatch(value.get("digest", ""))
        ):
            return False
        if any(
            not isinstance(count, int) or isinstance(count, bool) or count <= 0
            for count in value["kinds"].values()
        ):
            return False
        return sum(value["kinds"].values()) == value["count"]

    def _validate_row(self, row: Any, owner: str) -> None:
        keys = {
            "source", "name", "name_digest", "candidate_kind", "marker_state",
            "marker", "marker_digest", "project_digest", "disposition",
            "account_bindings", "reason", "recoverable",
        }
        if (
            not isinstance(row, dict) or set(row) != keys
            or row["source"] not in {"default", "entry"}
            or not isinstance(row["name"], str) or not row["name"]
            or not all(_HEX64.fullmatch(row[key]) for key in (
                "name_digest", "marker_digest", "project_digest",
            ))
            or not hmac.compare_digest(
                row["name_digest"],
                _mac(self._secret, "name", os.fsencode(row["name"])),
            )
            or row["candidate_kind"] not in {
                "directory", "symlink",
            }
            or row["marker_state"] not in {"missing", "valid", "corrupt", "unsafe"}
            or (row["marker"] is not None and not _HEX32.fullmatch(row["marker"]))
            or (row["marker_state"] == "valid") != (row["marker"] is not None)
            or row["disposition"] not in {"owned", "quarantined"}
            or row["reason"] not in _REASONS or type(row["recoverable"]) is not bool
            or not isinstance(row["account_bindings"], list)
        ):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        owned = row["disposition"] == "owned"
        expected = [{"account_id": owner, "role": "owner"}]
        if owned != (row["account_bindings"] == expected and not row["recoverable"]):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if owned and (row["marker_state"] != "valid" or row["reason"] != "eligible"):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if not owned and (row["account_bindings"] or not row["recoverable"]):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if row["candidate_kind"] == "symlink":
            if (
                row["source"] != "entry" or owned or row["reason"] != "symlink"
                or row["marker_state"] != "missing" or row["marker"] is not None
            ):
                raise ProjectMigrationCorruptError("project migration ledger is corrupt")
            return
        if row["reason"] in {"eligible", "symlink"} and not owned:
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        expected_marker_states = {
            "missing_marker": "missing",
            "reserved_default_collision": "missing",
            "unsafe_name": "missing",
            "unsafe_marker": "unsafe",
            "corrupt_marker": "corrupt",
            "duplicate_marker": "valid",
        }
        expected_state = expected_marker_states.get(row["reason"])
        if expected_state is not None and row["marker_state"] != expected_state:
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if row["reason"] == "reserved_default_collision" and not (
            row["source"] == "entry" and row["name"] == "default"
        ):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if (
            row["source"] == "entry" and row["name"] == "default"
            and row["reason"] != "reserved_default_collision"
        ):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if row["reason"] == "unsafe_name" and _safe_name(row["name"]):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if row["reason"] == "missing_marker" and not _safe_name(row["name"]):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if (
            row["source"] == "entry" and not _safe_name(row["name"])
            and row["reason"] != "unsafe_name"
        ):
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")
        if row["source"] == "default" and row["reason"] not in {
            "eligible", "missing_marker", "unsafe_marker", "corrupt_marker",
            "duplicate_marker",
        }:
            raise ProjectMigrationCorruptError("project migration ledger is corrupt")

    @contextmanager
    def _lock(self) -> Iterator[int | None]:
        directory_info = _directory_info(self.path.parent)
        path = self.path.parent / f".{self.path.name}.lock"
        descriptor, backend, directory_fd = None, None, None
        try:
            if os.name != "nt":
                directory_fd = os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                opened_directory = os.fstat(directory_fd)
                if (opened_directory.st_dev, opened_directory.st_ino) != (
                    directory_info.st_dev, directory_info.st_ino,
                ):
                    raise ProjectMigrationSafetyError("migration directory changed")
                descriptor = os.open(
                    path.name,
                    os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                    0o600, dir_fd=directory_fd,
                )
                current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            else:
                descriptor = os.open(
                    path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600,
                )
                current = os.lstat(path)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or (os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600)
            ):
                raise ProjectMigrationSafetyError("migration lock is unsafe")
            if os.name == "nt":
                import msvcrt

                if not opened.st_size:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                backend = "msvcrt"
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
                backend = "fcntl"
            yield directory_fd
        except ProjectMigrationError:
            raise
        except OSError as error:
            raise ProjectMigrationSafetyError("migration lock is unavailable") from error
        finally:
            if descriptor is not None:
                try:
                    if backend == "fcntl":
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    elif backend == "msvcrt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                finally:
                    os.close(descriptor)
            if directory_fd is not None:
                os.close(directory_fd)

    def _read(self, directory_fd: int | None) -> dict[str, Any] | None:
        if directory_fd is None:
            _directory_info(self.path.parent)
        try:
            before = (
                os.stat(self.path.name, dir_fd=directory_fd, follow_symlinks=False)
                if directory_fd is not None else os.lstat(self.path)
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ProjectMigrationSafetyError("migration ledger is unavailable") from error
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ProjectMigrationCorruptError("project migration ledger is unsafe")
        descriptor = None
        try:
            descriptor = os.open(
                self.path.name if directory_fd is not None else self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                **({"dir_fd": directory_fd} if directory_fd is not None else {}),
            )
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600)
            ):
                raise ProjectMigrationCorruptError("project migration ledger is unsafe")
            raw = _bounded_read(descriptor, _MAX_LEDGER_BYTES)
            after = os.fstat(descriptor)
            current = (
                os.stat(self.path.name, dir_fd=directory_fd, follow_symlinks=False)
                if directory_fd is not None else os.lstat(self.path)
            )
            if (
                (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or len(raw) != after.st_size
            ):
                raise ProjectMigrationCorruptError("project migration ledger changed")
        except ProjectMigrationError:
            raise
        except OSError as error:
            raise ProjectMigrationSafetyError("migration ledger cannot be read") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            value = json.loads(raw.decode(), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as error:
            raise ProjectMigrationCorruptError("project migration ledger is corrupt") from error
        if _canonical(value) + b"\n" != raw:
            raise ProjectMigrationCorruptError("project migration ledger is not canonical")
        return self._validate(value)

    def _write(self, encoded: bytes, directory_fd: int | None) -> None:
        directory = self.path.parent
        if directory_fd is None:
            _directory_info(directory)
        temporary_name = f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        temporary = directory / temporary_name
        descriptor = None
        try:
            descriptor = os.open(
                temporary_name if directory_fd is not None else temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600, **({"dir_fd": directory_fd} if directory_fd is not None else {}),
            )
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short migration write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if directory_fd is not None:
                os.fsync(directory_fd)
                self._publish_no_replace(
                    temporary_name, self.path.name, directory_fd,
                )
                try:
                    os.fsync(directory_fd)
                except OSError:
                    os.rename(
                        self.path.name, temporary_name,
                        src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                    )
                    os.fsync(directory_fd)
                    raise
            else:
                self._publish_no_replace_windows(temporary, self.path)
            try:
                published = (
                    os.stat(self.path.name, dir_fd=directory_fd, follow_symlinks=False)
                    if directory_fd is not None else os.lstat(self.path)
                )
            except OSError as error:
                raise ProjectMigrationSafetyError(
                    "migration publication was not durable",
                ) from error
            if not stat.S_ISREG(published.st_mode) or published.st_nlink != 1:
                raise ProjectMigrationConflictError(
                    "migration ledger publication is unsafe",
                )
        except FileExistsError as error:
            raise ProjectMigrationConflictError(
                "migration ledger appeared during publication",
            ) from error
        except ProjectMigrationError:
            raise
        except OSError as error:
            raise ProjectMigrationSafetyError("migration ledger update was not durable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                if directory_fd is not None:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                else:
                    temporary.unlink()
            except OSError:
                pass

    @staticmethod
    def _publish_no_replace(
        temporary_name: str, destination_name: str, directory_fd: int,
    ) -> None:
        """Atomic POSIX rename that refuses an existing destination."""
        try:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            if hasattr(libc, "renameat2"):
                rename = libc.renameat2
                exclusive_flag = 1  # Linux RENAME_NOREPLACE
            elif hasattr(libc, "renameatx_np"):
                rename = libc.renameatx_np
                exclusive_flag = 0x4  # Darwin RENAME_EXCL
            else:
                raise AttributeError("no atomic exclusive rename primitive")
            rename.argtypes = (
                ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename.restype = ctypes.c_int
            result = rename(
                directory_fd, os.fsencode(temporary_name),
                directory_fd, os.fsencode(destination_name), exclusive_flag,
            )
            if result:
                code = ctypes.get_errno()
                if code == errno.EEXIST:
                    raise ProjectMigrationConflictError(
                        "migration ledger appeared during publication",
                    )
                raise OSError(code, "exclusive rename failed")
        except ProjectMigrationError:
            raise
        except (AttributeError, OSError, ValueError) as error:
            raise ProjectMigrationSafetyError(
                "atomic no-replace publication is unavailable",
            ) from error

    @staticmethod
    def _publish_no_replace_windows(temporary: Path, destination: Path) -> None:
        """Windows write-through move without replace-existing semantics."""
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            move = kernel32.MoveFileExW
            move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
            move.restype = wintypes.BOOL
            if not move(str(temporary), str(destination), 0x8):
                code = ctypes.get_last_error()
                if code in {80, 183}:
                    raise ProjectMigrationConflictError(
                        "migration ledger appeared during publication",
                    )
                raise OSError(code, "write-through publication failed")
        except ProjectMigrationError:
            raise
        except (AttributeError, OSError, ValueError) as error:
            raise ProjectMigrationSafetyError(
                "atomic no-replace publication is unavailable",
            ) from error


def migrate_existing_projects(
    *, projects_root: os.PathLike[str] | str, ledger_path: os.PathLike[str] | str,
    owner_account_id: str, secret: bytes, require_existing: bool = False,
) -> dict[str, Any]:
    """Explicit integration seam; callers must quiesce before invoking it."""
    return AccountProjectMigrationLedger(ledger_path, secret).migrate(
        projects_root, owner_account_id, require_existing=require_existing,
    )


def migrate_inventory(
    *, projects_root: os.PathLike[str] | str, ledger_path: os.PathLike[str] | str,
    owner_account_id: str, secret: bytes, require_existing: bool = False,
) -> dict[str, Any]:
    """Named controlled-commit seam for account/launch integration."""
    return AccountProjectMigrationLedger(ledger_path, secret).migrate_inventory(
        projects_root, owner_account_id, require_existing=require_existing,
    )
