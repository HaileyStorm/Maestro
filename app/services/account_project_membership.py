"""Sealed account membership for stable Maestro project instances.

The immutable migration ledger records census-specific digests.  This mutable
store deliberately translates each eligible marker through Maestro's existing
runtime project identity function; migration digests remain provenance only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from .account_auth import (
    AccountStoreCorruptError,
    _AccountStoreLock,
    _atomic_replace_private_file,
    _read_private_file,
)
from .account_project_migration import (
    AccountProjectMigrationLedger,
    ProjectMigrationError,
)
from .queue_recovery_adapter import project_instance_digest

PROJECT_MEMBERSHIP_STORE_VERSION = 2
PROJECT_ROLES = frozenset({"owner", "editor", "viewer"})
PROJECT_PERMISSIONS = frozenset(
    {
        "project.list",
        "project.open",
        "project.read",
        "project.mutate",
        "project.generate",
        "project.membership.manage",
        "project.lifecycle",
        "project.delete",
    }
)
_ROLE_PERMISSIONS = {
    "viewer": frozenset({"project.list", "project.open", "project.read"}),
    "editor": frozenset(
        {
            "project.list",
            "project.open",
            "project.read",
            "project.mutate",
            "project.generate",
        }
    ),
    "owner": PROJECT_PERMISSIONS,
}
_STORE_SEAL_DOMAIN = b"maestro-account-project-membership-store-v2\0"
_STORE_BINDING_DOMAIN = b"maestro-account-project-membership-path-v2\0"
_MIGRATION_SEAL_DOMAIN = b"maestro-project-migration-ledger-v2\0"
_MAX_STORE_BYTES = 8 * 1024 * 1024
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_ID = re.compile(r"project:v1:[0-9a-f]{64}\Z")
_PROJECT_STATES = frozenset({"active", "deleting", "deleted"})
_MIGRATION_KEYS = {
    "schema_version",
    "generation",
    "root_binding",
    "migration_kind",
    "owner_account_id",
    "entries",
    "excluded_non_projects",
    "census_digest",
    "seal",
}
_MIGRATION_ROW_KEYS = {
    "source",
    "name",
    "name_digest",
    "candidate_kind",
    "marker_state",
    "marker",
    "marker_digest",
    "project_digest",
    "disposition",
    "account_bindings",
    "reason",
    "recoverable",
}
_MIGRATION_KINDS = frozenset(
    {
        "directory",
        "symlink",
    }
)
_MIGRATION_REASONS = frozenset(
    {
        "eligible",
        "missing_marker",
        "reserved_default_collision",
        "unsafe_name",
        "symlink",
        "unsafe_directory",
        "unsafe_marker",
        "corrupt_marker",
        "duplicate_marker",
    }
)


class ProjectMembershipError(RuntimeError):
    """Base error for membership-store operations."""


class ProjectMembershipStoreUnavailableError(ProjectMembershipError):
    """The initialized store is missing or cannot be safely accessed."""


class ProjectMembershipStoreCorruptError(ProjectMembershipStoreUnavailableError):
    """The store exists but does not pass structural and seal checks."""


class ProjectMembershipConflictError(ProjectMembershipError):
    """A requested mutation conflicts with current revision or lifecycle state."""


class ProjectMembershipNotFoundError(ProjectMembershipError):
    """No active binding record exists for the requested project."""


def role_allows(role: str, permission: str) -> bool:
    """Return the closed role/permission contract used by route integration."""
    if role not in PROJECT_ROLES:
        raise ValueError("project role is invalid")
    if permission not in PROJECT_PERMISSIONS:
        raise ValueError("project permission is invalid")
    return permission in _ROLE_PERMISSIONS[role]


def permissions_for_role(role: str) -> frozenset[str]:
    if role not in PROJECT_ROLES:
        raise ValueError("project role is invalid")
    return _ROLE_PERMISSIONS[role]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _valid_account_id(value: Any) -> bool:
    return isinstance(value, str) and _HEX32.fullmatch(value) is not None


class AccountProjectMembershipStore:
    """Atomic mutable project bindings initialized from one migration ledger."""

    def __init__(
        self,
        path: os.PathLike[str] | str,
        secret: bytes,
        *,
        max_store_bytes: int = _MAX_STORE_BYTES,
    ) -> None:
        raw = os.fspath(path)
        if not raw or "\0" in raw:
            raise ValueError("project membership store path is invalid")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("project membership secret must contain at least 32 bytes")
        if not 4096 <= int(max_store_bytes) <= _MAX_STORE_BYTES:
            raise ValueError("project membership store size limit is invalid")
        self.path = os.path.abspath(raw)
        self._secret = secret
        self._max_store_bytes = int(max_store_bytes)
        self._lock = _AccountStoreLock(self.path)
        self._thread_lock = threading.RLock()

    def project_identity(
        self,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
    ) -> str:
        """Resolve a marker and/or canonical runtime digest to one identity."""
        resolved: str | None = None
        if marker is not None:
            if not isinstance(marker, str) or _HEX32.fullmatch(marker) is None:
                raise ValueError("project marker is invalid")
            resolved = project_instance_digest(self._secret, marker)
        if project_instance is not None:
            if (
                not isinstance(project_instance, str)
                or _PROJECT_ID.fullmatch(project_instance) is None
            ):
                raise ValueError("project instance digest is invalid")
            if resolved is not None and not hmac.compare_digest(
                resolved, project_instance
            ):
                raise ValueError("project marker and digest do not match")
            resolved = project_instance
        if resolved is None:
            raise ValueError("project marker or digest is required")
        return resolved

    def initialize_from_ledger(
        self,
        ledger: dict[str, Any],
        *,
        require_existing: bool = False,
    ) -> dict[str, Any]:
        """Create the store once, or replay the exact same census idempotently."""
        proposal = self._proposal_from_ledger(ledger)
        with self._thread_lock, self._guard_lock():
            current = self._load_unlocked()
            if current is None and require_existing:
                raise ProjectMembershipStoreUnavailableError(
                    "initialized project membership store is missing",
                )
            if current is not None:
                if not hmac.compare_digest(
                    current["migration"]["ledger_fingerprint"],
                    proposal["migration"]["ledger_fingerprint"],
                ):
                    raise ProjectMembershipConflictError(
                        "project membership store has a different migration lineage",
                    )
                return deepcopy(current)
            self._save_unlocked(proposal, initial=True)
            saved = self._load_unlocked()
            if saved is None:
                raise ProjectMembershipStoreUnavailableError(
                    "project membership store was not durably initialized",
                )
            return deepcopy(saved)

    def load(self, *, required: bool = False) -> dict[str, Any] | None:
        with self._thread_lock, self._guard_lock():
            value = self._load_unlocked()
            if value is None and required:
                raise ProjectMembershipStoreUnavailableError(
                    "initialized project membership store is missing",
                )
            return None if value is None else deepcopy(value)

    def lookup(
        self,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any] | None:
        identity = self.project_identity(
            marker=marker, project_instance=project_instance
        )
        payload = self.load(required=True)
        assert payload is not None
        record = self._find(payload, identity)
        if record is None or (not include_inactive and record["state"] != "active"):
            return None
        return deepcopy(record)

    def list_for_account(
        self,
        account_id: str,
        *,
        permission: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        self._require_account(account_id)
        if permission is not None and permission not in PROJECT_PERMISSIONS:
            raise ValueError("project permission is invalid")
        payload = self.load(required=True)
        assert payload is not None
        result = []
        for record in payload["projects"]:
            if not include_inactive and record["state"] != "active":
                continue
            binding = next(
                (
                    item
                    for item in record["bindings"]
                    if item["account_id"] == account_id
                ),
                None,
            )
            if binding is None or (
                permission is not None and not role_allows(binding["role"], permission)
            ):
                continue
            item = deepcopy(record)
            item["account_role"] = binding["role"]
            result.append(item)
        return result

    def bind(
        self,
        account_id: str,
        role: str,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._require_account(account_id)
        self._require_role(role)
        identity = self.project_identity(
            marker=marker, project_instance=project_instance
        )

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            record = self._find(payload, identity)
            if record is None:
                if role != "owner":
                    raise ProjectMembershipConflictError(
                        "a new project must receive an owner binding",
                    )
                self._expect_revision(expected_revision, None)
                record = {
                    "project_instance": identity,
                    "state": "active",
                    "revision": 1,
                    "bindings": [{"account_id": account_id, "role": role}],
                    "origin": "runtime",
                    "provenance": None,
                    "deletion": None,
                }
                payload["projects"].append(record)
                payload["projects"].sort(key=lambda item: item["project_instance"])
                return record
            self._expect_revision(expected_revision, record["revision"])
            if record["state"] != "active":
                raise ProjectMembershipConflictError("project is not active")
            current = next(
                (
                    item
                    for item in record["bindings"]
                    if item["account_id"] == account_id
                ),
                None,
            )
            if current is not None and current["role"] == role:
                return record
            if current is not None and current["role"] == "owner" and role != "owner":
                self._require_other_owner(record, account_id)
                current["role"] = role
            elif current is not None:
                current["role"] = role
            else:
                record["bindings"].append({"account_id": account_id, "role": role})
            record["bindings"].sort(key=lambda item: item["account_id"])
            record["revision"] += 1
            return record

        return self._mutate(mutate)

    def unbind(
        self,
        account_id: str,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._require_account(account_id)
        identity = self.project_identity(
            marker=marker, project_instance=project_instance
        )

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            record = self._required_active(payload, identity)
            self._expect_revision(expected_revision, record["revision"])
            current = next(
                (
                    item
                    for item in record["bindings"]
                    if item["account_id"] == account_id
                ),
                None,
            )
            if current is None:
                return record
            if current["role"] == "owner":
                self._require_other_owner(record, account_id)
            record["bindings"].remove(current)
            record["revision"] += 1
            return record

        return self._mutate(mutate)

    def begin_deletion(
        self,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
        operation_id: str | None = None,
        expected_revision: int,
    ) -> dict[str, Any]:
        identity = self.project_identity(
            marker=marker, project_instance=project_instance
        )
        if operation_id is not None and _HEX32.fullmatch(operation_id) is None:
            raise ValueError("project deletion operation id is invalid")

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            record = self._find(payload, identity)
            if record is None:
                raise ProjectMembershipNotFoundError("project membership was not found")
            if record["state"] == "deleting":
                if operation_id is not None and not hmac.compare_digest(
                    record["deletion"]["operation_id"],
                    operation_id,
                ):
                    raise ProjectMembershipConflictError(
                        "a different project deletion is already pending",
                    )
                self._expect_revision(
                    expected_revision, record["deletion"]["base_revision"]
                )
                return record
            self._expect_revision(expected_revision, record["revision"])
            if record["state"] != "active":
                raise ProjectMembershipConflictError("project is already deleted")
            if (
                record["deletion"] is not None
                and operation_id is not None
                and hmac.compare_digest(
                    record["deletion"]["operation_id"],
                    operation_id,
                )
            ):
                raise ProjectMembershipConflictError(
                    "project deletion operation was already cancelled",
                )
            base_revision = record["revision"]
            record["state"] = "deleting"
            record["revision"] += 1
            record["deletion"] = {
                "operation_id": operation_id or uuid.uuid4().hex,
                "status": "pending",
                "base_revision": base_revision,
            }
            return record

        return self._mutate(mutate)

    def finish_deletion(
        self,
        operation_id: str,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
    ) -> dict[str, Any]:
        identity = self.project_identity(
            marker=marker, project_instance=project_instance
        )
        if not isinstance(operation_id, str) or _HEX32.fullmatch(operation_id) is None:
            raise ValueError("project deletion operation id is invalid")

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            record = self._find(payload, identity)
            if record is None:
                raise ProjectMembershipNotFoundError("project membership was not found")
            deletion = record["deletion"]
            if deletion is None or not hmac.compare_digest(
                deletion["operation_id"],
                operation_id,
            ):
                raise ProjectMembershipConflictError("project deletion does not match")
            if record["state"] == "deleted":
                return record
            if record["state"] != "deleting":
                raise ProjectMembershipConflictError("project deletion is not pending")
            record["state"] = "deleted"
            record["revision"] += 1
            deletion["status"] = "completed"
            return record

        return self._mutate(mutate)

    def cancel_deletion(
        self,
        operation_id: str,
        *,
        marker: str | None = None,
        project_instance: str | None = None,
    ) -> dict[str, Any]:
        identity = self.project_identity(
            marker=marker, project_instance=project_instance
        )
        if not isinstance(operation_id, str) or _HEX32.fullmatch(operation_id) is None:
            raise ValueError("project deletion operation id is invalid")

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            record = self._find(payload, identity)
            if record is None:
                raise ProjectMembershipNotFoundError("project membership was not found")
            deletion = record["deletion"]
            if record["state"] == "active":
                if (
                    deletion is not None
                    and deletion["status"] == "cancelled"
                    and hmac.compare_digest(deletion["operation_id"], operation_id)
                ):
                    return record
                raise ProjectMembershipConflictError(
                    "project deletion operation does not match",
                )
            if (
                record["state"] != "deleting"
                or deletion is None
                or not hmac.compare_digest(deletion["operation_id"], operation_id)
            ):
                raise ProjectMembershipConflictError(
                    "project deletion is not cancellable"
                )
            record["state"] = "active"
            record["revision"] += 1
            deletion["status"] = "cancelled"
            return record

        return self._mutate(mutate)

    def _proposal_from_ledger(self, ledger: dict[str, Any]) -> dict[str, Any]:
        try:
            ledger = AccountProjectMigrationLedger(
                self.path + ".source-validation",
                self._secret,
            )._validate(deepcopy(ledger))
            if (
                not isinstance(ledger, dict)
                or set(ledger) != _MIGRATION_KEYS
                or ledger.get("schema_version") != 2
                or ledger.get("generation") != 1
                or ledger.get("migration_kind") != "first_owner_cutover"
                or not _valid_account_id(ledger.get("owner_account_id"))
                or not isinstance(ledger.get("entries"), list)
                or not ledger["entries"]
                or _HEX64.fullmatch(ledger.get("root_binding", "")) is None
                or _HEX64.fullmatch(ledger.get("census_digest", "")) is None
                or _HEX64.fullmatch(ledger.get("seal", "")) is None
                or not hmac.compare_digest(
                    ledger["census_digest"],
                    hashlib.sha256(
                        _canonical(
                            {
                                "entries": ledger["entries"],
                                "excluded_non_projects": ledger[
                                    "excluded_non_projects"
                                ],
                            }
                        )
                    ).hexdigest(),
                )
                or not hmac.compare_digest(
                    ledger["seal"],
                    hmac.new(
                        self._secret,
                        _MIGRATION_SEAL_DOMAIN
                        + _canonical(
                            {
                                key: value
                                for key, value in ledger.items()
                                if key != "seal"
                            }
                        ),
                        hashlib.sha256,
                    ).hexdigest(),
                )
            ):
                raise ValueError("migration ledger is invalid")
            owner = ledger["owner_account_id"]
            projects: list[dict[str, Any]] = []
            quarantined: list[dict[str, Any]] = []
            sources: set[tuple[str, str]] = set()
            identities: set[str] = set()
            for row in ledger["entries"]:
                provenance = self._migration_provenance(row)
                source_key = (row["source"], row["name"])
                if source_key in sources:
                    raise ValueError("migration ledger repeats a census row")
                sources.add(source_key)
                if row["disposition"] == "owned":
                    if (
                        row["marker_state"] != "valid"
                        or _HEX32.fullmatch(row.get("marker", "")) is None
                        or row["account_bindings"]
                        != [{"account_id": owner, "role": "owner"}]
                        or row["recoverable"] is not False
                    ):
                        raise ValueError("owned migration row is invalid")
                    identity = project_instance_digest(self._secret, row["marker"])
                    if identity in identities:
                        raise ValueError("migration ledger repeats a runtime project")
                    identities.add(identity)
                    provenance["classification"] = "bound"
                    projects.append(
                        {
                            "project_instance": identity,
                            "state": "active",
                            "revision": 1,
                            "bindings": [{"account_id": owner, "role": "owner"}],
                            "origin": "migration",
                            "provenance": provenance,
                            "deletion": None,
                        }
                    )
                elif row["disposition"] == "quarantined":
                    if row["account_bindings"] or row["recoverable"] is not True:
                        raise ValueError("quarantined migration row is invalid")
                    provenance["classification"] = "quarantined"
                    quarantined.append(provenance)
                else:
                    raise ValueError("migration row disposition is invalid")
            projects.sort(key=lambda item: item["project_instance"])
            quarantined.sort(
                key=lambda item: (item["source"], os.fsencode(item["name"]))
            )
            fingerprint = hashlib.sha256(_canonical(ledger)).hexdigest()
            payload = {
                "version": PROJECT_MEMBERSHIP_STORE_VERSION,
                "generation": 0,
                "store_binding": self._store_binding(),
                "migration": {
                    "ledger_schema_version": ledger["schema_version"],
                    "ledger_fingerprint": fingerprint,
                    "ledger_generation": ledger["generation"],
                    "root_binding": ledger["root_binding"],
                    "census_digest": ledger["census_digest"],
                    "owner_account_id": owner,
                    "classified_entries": len(ledger["entries"]),
                    "bound_entries": len(projects),
                    "quarantined_entries": len(quarantined),
                    "excluded_non_projects": deepcopy(ledger["excluded_non_projects"]),
                },
                "projects": projects,
                "quarantine": quarantined,
            }
            return payload
        except ProjectMembershipError:
            raise
        except ProjectMigrationError as error:
            raise ProjectMembershipConflictError(
                "migration ledger cannot initialize project membership",
            ) from error
        except (
            KeyError,
            TypeError,
            ValueError,
            RecursionError,
            OverflowError,
        ) as error:
            raise ProjectMembershipConflictError(
                "migration ledger cannot initialize project membership",
            ) from error

    @staticmethod
    def _migration_provenance(row: Any) -> dict[str, Any]:
        if (
            not isinstance(row, dict)
            or set(row) != _MIGRATION_ROW_KEYS
            or row.get("source") not in {"default", "entry"}
            or not isinstance(row.get("name"), str)
            or not row["name"]
            or any(
                _HEX64.fullmatch(row.get(key, "")) is None
                for key in (
                    "name_digest",
                    "marker_digest",
                    "project_digest",
                )
            )
            or row.get("candidate_kind") not in _MIGRATION_KINDS
            or row.get("marker_state") not in {"missing", "valid", "corrupt", "unsafe"}
            or (row.get("marker_state") == "valid")
            != (
                isinstance(row.get("marker"), str)
                and _HEX32.fullmatch(row["marker"]) is not None
            )
            or row.get("disposition") not in {"owned", "quarantined"}
            or row.get("reason") not in _MIGRATION_REASONS
            or type(row.get("recoverable")) is not bool
            or not isinstance(row.get("account_bindings"), list)
        ):
            raise ValueError("migration census row is invalid")
        return {
            "classification": "pending",
            "source": row["source"],
            "name": row["name"],
            "name_digest": row["name_digest"],
            "marker_digest": row["marker_digest"],
            "migration_project_digest": row["project_digest"],
            "marker_state": row["marker_state"],
            "reason": row["reason"],
            "recoverable": row["recoverable"],
        }

    def _store_binding(self) -> str:
        return hmac.new(
            self._secret,
            _STORE_BINDING_DOMAIN + os.fsencode(os.path.normcase(self.path)),
            hashlib.sha256,
        ).hexdigest()

    def _seal(self, payload: dict[str, Any]) -> str:
        unsigned = {key: value for key, value in payload.items() if key != "seal"}
        return hmac.new(
            self._secret,
            _STORE_SEAL_DOMAIN + _canonical(unsigned),
            hashlib.sha256,
        ).hexdigest()

    def _mutate(self, callback) -> dict[str, Any]:
        with self._thread_lock, self._guard_lock():
            payload = self._load_unlocked()
            if payload is None:
                raise ProjectMembershipStoreUnavailableError(
                    "initialized project membership store is missing",
                )
            encoded_before = _canonical(
                {key: value for key, value in payload.items() if key != "seal"},
            )
            result = callback(payload)
            before_generation = payload["generation"]
            # Idempotent callbacks leave the payload untouched and avoid writes.
            encoded_after = _canonical(
                {key: value for key, value in payload.items() if key != "seal"},
            )
            if encoded_before != encoded_after:
                payload["generation"] = before_generation + 1
                self._save_unlocked(payload, initial=False, increment=False)
                stored = self._load_unlocked()
                assert stored is not None
                found = self._find(stored, result["project_instance"])
                assert found is not None
                return deepcopy(found)
            return deepcopy(result)

    def _load_unlocked(self) -> dict[str, Any] | None:
        try:
            encoded = _read_private_file(self.path, max_bytes=self._max_store_bytes)
        except AccountStoreCorruptError as error:
            raise ProjectMembershipStoreCorruptError(
                "project membership store cannot be verified",
            ) from error
        if encoded is None:
            return None
        try:
            payload = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_unique_object,
            )
            return self._validate(payload)
        except ProjectMembershipError:
            raise
        except (UnicodeDecodeError, TypeError, ValueError, RecursionError) as error:
            raise ProjectMembershipStoreCorruptError(
                "project membership store is corrupt",
            ) from error

    def _save_unlocked(
        self,
        payload: dict[str, Any],
        *,
        initial: bool,
        increment: bool = True,
    ) -> None:
        value = deepcopy(payload)
        value.pop("seal", None)
        if initial:
            value["generation"] = 1
        elif increment:
            value["generation"] += 1
        value["seal"] = self._seal(value)
        self._validate(value)
        encoded = json.dumps(
            value,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > self._max_store_bytes:
            raise ProjectMembershipConflictError(
                "project membership store is at capacity"
            )
        try:
            _atomic_replace_private_file(self.path, encoded)
        except AccountStoreCorruptError as error:
            raise ProjectMembershipStoreUnavailableError(
                "project membership store update was not durable",
            ) from error

    def _validate(self, payload: Any) -> dict[str, Any]:
        try:
            if (
                not isinstance(payload, dict)
                or set(payload)
                != {
                    "version",
                    "generation",
                    "store_binding",
                    "migration",
                    "projects",
                    "quarantine",
                    "seal",
                }
                or payload["version"] != PROJECT_MEMBERSHIP_STORE_VERSION
                or not isinstance(payload["generation"], int)
                or isinstance(payload["generation"], bool)
                or payload["generation"] < 1
                or _HEX64.fullmatch(payload["store_binding"]) is None
                or not hmac.compare_digest(
                    payload["store_binding"], self._store_binding()
                )
                or _HEX64.fullmatch(payload["seal"]) is None
                or not hmac.compare_digest(payload["seal"], self._seal(payload))
                or not isinstance(payload["projects"], list)
                or not isinstance(payload["quarantine"], list)
            ):
                raise ProjectMembershipStoreCorruptError(
                    "project membership store is corrupt",
                )
            self._validate_migration(payload["migration"], payload)
            identities = set()
            for record in payload["projects"]:
                self._validate_project(record)
                if record["project_instance"] in identities:
                    raise ProjectMembershipStoreCorruptError(
                        "project membership store repeats a project",
                    )
                identities.add(record["project_instance"])
            for item in payload["quarantine"]:
                self._validate_provenance(item, classification="quarantined")
            return payload
        except ProjectMembershipError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            RecursionError,
            OverflowError,
        ) as error:
            raise ProjectMembershipStoreCorruptError(
                "project membership store is corrupt",
            ) from error

    @staticmethod
    def _validate_migration(migration: Any, payload: dict[str, Any]) -> None:
        keys = {
            "ledger_schema_version",
            "ledger_fingerprint",
            "ledger_generation",
            "root_binding",
            "census_digest",
            "owner_account_id",
            "classified_entries",
            "bound_entries",
            "quarantined_entries",
            "excluded_non_projects",
        }
        if (
            not isinstance(migration, dict)
            or set(migration) != keys
            or any(
                _HEX64.fullmatch(migration.get(key, "")) is None
                for key in (
                    "ledger_fingerprint",
                    "root_binding",
                    "census_digest",
                )
            )
            or migration.get("ledger_schema_version") != 2
            or migration.get("ledger_generation") != 1
            or not _valid_account_id(migration.get("owner_account_id"))
            or any(
                not isinstance(migration.get(key), int)
                or isinstance(migration.get(key), bool)
                or migration[key] < 0
                for key in (
                    "classified_entries",
                    "bound_entries",
                    "quarantined_entries",
                )
            )
            or migration["bound_entries"]
            != sum(
                isinstance(item, dict) and item.get("origin") == "migration"
                for item in payload["projects"]
            )
            or migration["quarantined_entries"] != len(payload["quarantine"])
            or migration["classified_entries"]
            != migration["bound_entries"] + migration["quarantined_entries"]
            or not AccountProjectMembershipStore._valid_excluded_non_projects(
                migration["excluded_non_projects"]
            )
        ):
            raise ProjectMembershipStoreCorruptError(
                "project membership migration metadata is corrupt",
            )

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
            or _HEX64.fullmatch(value.get("digest", "")) is None
        ):
            return False
        return (
            all(
                isinstance(count, int) and not isinstance(count, bool) and count > 0
                for count in value["kinds"].values()
            )
            and sum(value["kinds"].values()) == value["count"]
        )

    @classmethod
    def _validate_project(cls, record: Any) -> None:
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "project_instance",
                "state",
                "revision",
                "bindings",
                "origin",
                "provenance",
                "deletion",
            }
            or _PROJECT_ID.fullmatch(record.get("project_instance", "")) is None
            or record.get("state") not in _PROJECT_STATES
            or not isinstance(record.get("revision"), int)
            or isinstance(record.get("revision"), bool)
            or record["revision"] < 1
            or record.get("origin") not in {"migration", "runtime"}
            or not isinstance(record.get("bindings"), list)
            or not record["bindings"]
        ):
            raise ProjectMembershipStoreCorruptError("project record is corrupt")
        accounts, owners = set(), 0
        for binding in record["bindings"]:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"account_id", "role"}
                or not _valid_account_id(binding.get("account_id"))
                or binding.get("role") not in PROJECT_ROLES
                or binding["account_id"] in accounts
            ):
                raise ProjectMembershipStoreCorruptError("project binding is corrupt")
            accounts.add(binding["account_id"])
            owners += binding["role"] == "owner"
        if owners < 1:
            raise ProjectMembershipStoreCorruptError("project has no owner")
        if record["origin"] == "migration":
            cls._validate_provenance(record["provenance"], classification="bound")
        elif record["provenance"] is not None:
            raise ProjectMembershipStoreCorruptError(
                "runtime project has migration provenance"
            )
        deletion = record["deletion"]
        if record["state"] == "active":
            if deletion is not None and (
                not isinstance(deletion, dict)
                or set(deletion) != {"operation_id", "status", "base_revision"}
                or _HEX32.fullmatch(deletion.get("operation_id", "")) is None
                or deletion.get("status") != "cancelled"
                or not isinstance(deletion.get("base_revision"), int)
                or isinstance(deletion.get("base_revision"), bool)
                or deletion["base_revision"] < 1
            ):
                raise ProjectMembershipStoreCorruptError(
                    "active project has invalid deletion state",
                )
        elif (
            not isinstance(deletion, dict)
            or set(deletion) != {"operation_id", "status", "base_revision"}
            or _HEX32.fullmatch(deletion.get("operation_id", "")) is None
            or deletion.get("status")
            != ("pending" if record["state"] == "deleting" else "completed")
            or not isinstance(deletion.get("base_revision"), int)
            or isinstance(deletion.get("base_revision"), bool)
            or deletion["base_revision"] < 1
        ):
            raise ProjectMembershipStoreCorruptError(
                "project deletion state is corrupt"
            )

    @staticmethod
    def _validate_provenance(value: Any, *, classification: str) -> None:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "classification",
                "source",
                "name",
                "name_digest",
                "marker_digest",
                "migration_project_digest",
                "marker_state",
                "reason",
                "recoverable",
            }
            or value.get("classification") != classification
            or value.get("source") not in {"default", "entry"}
            or not isinstance(value.get("name"), str)
            or not value["name"]
            or any(
                _HEX64.fullmatch(value.get(key, "")) is None
                for key in (
                    "name_digest",
                    "marker_digest",
                    "migration_project_digest",
                )
            )
            or value.get("marker_state")
            not in {"missing", "valid", "corrupt", "unsafe"}
            or not isinstance(value.get("reason"), str)
            or type(value.get("recoverable")) is not bool
            or value["recoverable"] != (classification == "quarantined")
        ):
            raise ProjectMembershipStoreCorruptError("migration provenance is corrupt")

    @contextmanager
    def _guard_lock(self):
        try:
            with self._lock:
                yield
        except AccountStoreCorruptError as error:
            raise ProjectMembershipStoreUnavailableError(
                "project membership store lock is unavailable",
            ) from error

    @staticmethod
    def _find(payload: dict[str, Any], identity: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in payload["projects"]
                if item["project_instance"] == identity
            ),
            None,
        )

    @classmethod
    def _required_active(cls, payload: dict[str, Any], identity: str) -> dict[str, Any]:
        record = cls._find(payload, identity)
        if record is None or record["state"] != "active":
            raise ProjectMembershipNotFoundError(
                "active project membership was not found"
            )
        return record

    @staticmethod
    def _expect_revision(expected: int | None, current: int | None) -> None:
        if expected is None:
            return
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise ValueError("expected project revision is invalid")
        if expected != current:
            raise ProjectMembershipConflictError("project membership revision changed")

    @staticmethod
    def _require_account(account_id: str) -> None:
        if not _valid_account_id(account_id):
            raise ValueError("account id is invalid")

    @staticmethod
    def _require_role(role: str) -> None:
        if role not in PROJECT_ROLES:
            raise ValueError("project role is invalid")

    @staticmethod
    def _require_other_owner(record: dict[str, Any], excluded: str) -> None:
        if not any(
            item["role"] == "owner" and item["account_id"] != excluded
            for item in record["bindings"]
        ):
            raise ProjectMembershipConflictError("project must retain an owner")
