"""Atomic runtime state for scheduled public research.

All state lives beneath the ignored ``app/storage/research`` tree.  Research
and implementation have distinct locks and state records so observation can
never silently become a workspace-writing action.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence
import uuid

from services.research_sources import sanitize_untrusted


SCHEMA_VERSION = 1
EVENT_LIMIT = 256
MAX_CURRENT_FINDINGS = 256
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_EVENT_BYTES = 32 * 1024
MAX_STATE_BYTES = 256 * 1024
SUMMARY_LIMIT = 3
DEFAULT_READINESS_THRESHOLD = 3
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash-0731"
_LUNA_MODEL = "gpt-5.6-luna"
_MAX_DEEPSEEK_TOOL_CALLS = 6


class ResearchStoreError(RuntimeError):
    pass


class ResearchRunLocked(ResearchStoreError):
    pass


class ResearchNotReady(ResearchStoreError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime | str) -> str:
    return as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def next_due_after(enabled_at: datetime | str, after: datetime | str) -> datetime:
    """Return the first anchored cadence slot strictly later than ``after``.

    Slots are every six hours through day seven, daily through day fourteen,
    then weekly.  Skipped slots are not replayed, so downtime cannot create a
    catch-up burst and a process restart cannot reset the schedule.
    """
    enabled = as_utc(enabled_at)
    current = as_utc(after)
    if current < enabled:
        return enabled + timedelta(hours=6)
    elapsed = current - enabled
    if elapsed < timedelta(days=7):
        slot = int(elapsed.total_seconds() // timedelta(hours=6).total_seconds()) + 1
        return enabled + timedelta(hours=6 * min(slot, 28))
    if elapsed < timedelta(days=14):
        day = int(elapsed.total_seconds() // timedelta(days=1).total_seconds()) + 1
        return enabled + timedelta(days=max(8, min(day, 14)))
    week = int((elapsed - timedelta(days=14)).total_seconds() // timedelta(days=7).total_seconds()) + 1
    return enabled + timedelta(days=14 + 7 * week)


def cadence_label(enabled_at: datetime | str, at: datetime | str) -> str:
    elapsed = as_utc(at) - as_utc(enabled_at)
    if elapsed < timedelta(days=7):
        return "every_6_hours"
    if elapsed < timedelta(days=14):
        return "daily"
    return "weekly"


def _valid_saved_transport_proof(proof: Any, calls: int) -> bool:
    if not isinstance(proof, Mapping) or set(proof) != {
        "transport", "receipt_sha256", "event_count", "source_proof_count",
        "tool_call_count", "exact_gate_eligible", "proof_digest",
    }:
        return False
    receipt = proof.get("receipt_sha256")
    events = proof.get("event_count")
    sources = proof.get("source_proof_count")
    if (
        proof.get("transport") != "nous_chat_completions_mcp_hardened_v1"
        or not isinstance(receipt, str) or _SHA256_RE.fullmatch(receipt) is None
        or not isinstance(events, int) or isinstance(events, bool) or events < 1
        or not isinstance(sources, int) or isinstance(sources, bool) or sources < 1
        or proof.get("tool_call_count") != calls
        or proof.get("exact_gate_eligible") is not True
    ):
        return False
    retained = {key: proof[key] for key in proof if key != "proof_digest"}
    expected = hashlib.sha256(
        json.dumps(retained, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return proof.get("proof_digest") == expected


def _valid_resolution_record(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"resolved_at", "reviewer", "summary", "resolved_conflicts"}
        and isinstance(value.get("resolved_at"), str) and 0 < len(value["resolved_at"]) <= 40
        and isinstance(value.get("reviewer"), str) and 0 < len(value["reviewer"]) <= 80
        and isinstance(value.get("summary"), str) and 0 < len(value["summary"]) <= 500
        and isinstance(value.get("resolved_conflicts"), list)
        and 0 < len(value["resolved_conflicts"]) <= 10
        and all(
            isinstance(item, str) and 0 < len(item) <= 300
            for item in value["resolved_conflicts"]
        )
    )


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "schedule": {
            "enabled": False,
            "enabled_at": None,
            "last_cycle_at": None,
            "next_due_at": None,
            "batch_size": 6,
        },
        "research_run": {
            "active": False,
            "run_id": None,
            "started_at": None,
            "phase": None,
            "queued_candidate_count": 0,
        },
        "implementation_run": {
            "active": False,
            "run_id": None,
            "packet_id": None,
            "started_at": None,
            "completed_at": None,
            "status": "never_run",
            "summary": "",
        },
        "last_cycle": None,
    }


def _default_index() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "findings": {},
    }


def _bounded_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


class ResearchStore:
    CANONICAL_ROOT = Path(__file__).resolve().parents[1] / "storage" / "research"

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        event_limit: int = EVENT_LIMIT,
        allow_test_root: bool = False,
    ):
        candidate = self.CANONICAL_ROOT if root is None else Path(root)
        candidate = Path(os.path.abspath(candidate))
        if candidate != self.CANONICAL_ROOT:
            temporary = Path(tempfile.gettempdir()).resolve()
            try:
                candidate.relative_to(temporary)
            except ValueError as error:
                raise ResearchStoreError("research root must be the canonical runtime root") from error
            if not allow_test_root:
                raise ResearchStoreError("non-canonical research roots require explicit test approval")
        self.root = candidate
        self.event_limit = max(16, min(int(event_limit), 2_048))
        self.state_path = self.root / "state.json"
        self.index_path = self.root / "current.json"
        self.events_path = self.root / "events"

    @classmethod
    def default(cls) -> "ResearchStore":
        return cls()

    @contextmanager
    def _root_fd(self, *, create: bool) -> Iterator[int]:
        """Open every directory component without following symlinks."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(os.sep, flags)
        try:
            parts = self.root.parts[1:] if self.root.is_absolute() else self.root.parts
            for position, part in enumerate(parts):
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create or position != len(parts) - 1:
                        raise ResearchStoreError("research runtime directory does not exist")
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        except OSError as error:
            os.close(descriptor)
            raise ResearchStoreError("research runtime path is not a safe directory") from error
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_fd(descriptor: int, limit: int) -> bytes:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise ResearchStoreError("research state file has an invalid type or size")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise ResearchStoreError("research state file exceeds the byte limit")
        return payload

    def _load_json(self, name: str, default: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
        try:
            with self._root_fd(create=False) as root_fd:
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=root_fd,
                    )
                except FileNotFoundError:
                    return json.loads(json.dumps(default))
                try:
                    payload = self._read_fd(descriptor, limit)
                finally:
                    os.close(descriptor)
            value = json.loads(payload.decode("utf-8"))
        except ResearchStoreError:
            if not self.root.exists():
                return json.loads(json.dumps(default))
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResearchStoreError(f"invalid research state file {name}") from error
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
            raise ResearchStoreError(f"unsupported research state schema in {name}")
        return value

    @staticmethod
    def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
        if set(value) != expected:
            raise ResearchStoreError(f"{label} has an invalid shape")

    @staticmethod
    def _optional_text(value: Any, label: str, limit: int = 500) -> None:
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ResearchStoreError(f"{label} has an invalid value")

    def _validate_state(self, state: Mapping[str, Any]) -> None:
        self._exact_keys(
            state,
            {"schema_version", "schedule", "research_run", "implementation_run", "last_cycle"},
            "research state",
        )
        if state.get("schema_version") != SCHEMA_VERSION:
            raise ResearchStoreError("research state schema is unsupported")
        schedule = state.get("schedule")
        research = state.get("research_run")
        implementation = state.get("implementation_run")
        if not all(isinstance(item, Mapping) for item in (schedule, research, implementation)):
            raise ResearchStoreError("research state sections must be objects")
        self._exact_keys(
            schedule,
            {"enabled", "enabled_at", "last_cycle_at", "next_due_at", "batch_size"},
            "research schedule",
        )
        if not isinstance(schedule.get("enabled"), bool):
            raise ResearchStoreError("research schedule enabled must be boolean")
        for key in ("enabled_at", "last_cycle_at", "next_due_at"):
            self._optional_text(schedule.get(key), f"research schedule {key}", 40)
            if schedule.get(key) is not None:
                try:
                    as_utc(schedule[key])
                except (TypeError, ValueError) as error:
                    raise ResearchStoreError(f"research schedule {key} is invalid") from error
        batch_size = schedule.get("batch_size")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 24:
            raise ResearchStoreError("research schedule batch_size is invalid")
        self._exact_keys(
            research,
            {"active", "run_id", "started_at", "phase", "queued_candidate_count"},
            "research run",
        )
        if not isinstance(research.get("active"), bool):
            raise ResearchStoreError("research run active must be boolean")
        for key in ("run_id", "started_at", "phase"):
            self._optional_text(research.get(key), f"research run {key}", 100)
        queued = research.get("queued_candidate_count")
        if not isinstance(queued, int) or isinstance(queued, bool) or not 0 <= queued <= 24:
            raise ResearchStoreError("research queued count is invalid")
        self._exact_keys(
            implementation,
            {"active", "run_id", "packet_id", "started_at", "completed_at", "status", "summary"},
            "implementation run",
        )
        if not isinstance(implementation.get("active"), bool):
            raise ResearchStoreError("implementation run active must be boolean")
        for key, limit in (("run_id", 100), ("packet_id", 80), ("started_at", 40), ("completed_at", 40)):
            self._optional_text(implementation.get(key), f"implementation {key}", limit)
        if implementation.get("status") not in {
            "never_run", "running", "completed", "failed", "cancelled", "interrupted_requires_review",
        }:
            raise ResearchStoreError("implementation status is invalid")
        if not isinstance(implementation.get("summary"), str) or len(implementation["summary"]) > 240:
            raise ResearchStoreError("implementation summary is invalid")
        last_cycle = state.get("last_cycle")
        if last_cycle is not None:
            if not isinstance(last_cycle, Mapping):
                raise ResearchStoreError("last research cycle must be an object or null")
            allowed = {
                "run_id", "started_at", "completed_at", "status", "discovered", "analyzed",
                "provider_failures", "source_failures", "ready_for_review", "batch_size",
                "source_failure_summaries", "provider_failure_summaries", "failure",
                "deepseek_disabled_reason",
            }
            if not set(last_cycle).issubset(allowed):
                raise ResearchStoreError("last research cycle has unknown fields")
            base_fields = {
                "run_id", "started_at", "completed_at", "status", "discovered", "analyzed",
                "provider_failures", "source_failures", "ready_for_review", "batch_size",
            }
            status = last_cycle.get("status")
            if status == "completed":
                expected = base_fields | {
                    "source_failure_summaries", "provider_failure_summaries", "deepseek_disabled_reason",
                }
            elif status == "failed":
                expected = base_fields | {"failure"}
            else:
                raise ResearchStoreError("last research cycle status is invalid")
            if set(last_cycle) != expected:
                raise ResearchStoreError("last research cycle is incomplete for its status")
            for field, limit in (("run_id", 100), ("started_at", 40), ("completed_at", 40), ("deepseek_disabled_reason", 500)):
                self._optional_text(last_cycle.get(field), f"last research cycle {field}", limit)
            if not isinstance(last_cycle["run_id"], str) or not last_cycle["run_id"]:
                raise ResearchStoreError("last research cycle run_id is invalid")
            for field in ("started_at", "completed_at"):
                if not isinstance(last_cycle[field], str) or not last_cycle[field]:
                    raise ResearchStoreError(f"last research cycle {field} is invalid")
                try:
                    as_utc(last_cycle[field])
                except (TypeError, ValueError) as error:
                    raise ResearchStoreError(f"last research cycle {field} is invalid") from error
            disabled_reason = last_cycle.get("deepseek_disabled_reason")
            if disabled_reason is not None and sanitize_untrusted(disabled_reason)[0] != disabled_reason:
                raise ResearchStoreError("last research cycle DeepSeek reason is unsafe")
            numeric_limits = {
                "discovered": (0, 24),
                "analyzed": (0, 24),
                "provider_failures": (0, 24),
                "source_failures": (0, 8),
                "ready_for_review": (0, MAX_CURRENT_FINDINGS),
                "batch_size": (1, 24),
            }
            for field, (minimum, maximum) in numeric_limits.items():
                if field not in last_cycle:
                    continue
                value = last_cycle[field]
                if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                    raise ResearchStoreError(f"last research cycle {field} is invalid")
            summary_shapes = {
                "source_failure_summaries": {
                    "source_lane": 80, "error_type": 80, "message": 240, "content_flags": 160,
                },
                "provider_failure_summaries": {
                    "source_id": 240, "error_type": 80, "message": 500,
                },
            }
            for field, shape in summary_shapes.items():
                if field not in last_cycle:
                    continue
                summaries = last_cycle[field]
                if not isinstance(summaries, list) or len(summaries) > 3:
                    raise ResearchStoreError(f"last research cycle {field} is invalid")
                for item in summaries:
                    if not isinstance(item, Mapping) or set(item) != set(shape) or not all(
                        isinstance(item[key], str) and len(item[key]) <= limit
                        for key, limit in shape.items()
                    ):
                        raise ResearchStoreError(f"last research cycle {field} entry is invalid")
                    if any(sanitize_untrusted(item[key])[0] != item[key] for key in shape):
                        raise ResearchStoreError(f"last research cycle {field} entry is unsafe")
            failure = last_cycle.get("failure")
            if failure is not None and (
                not isinstance(failure, Mapping)
                or set(failure) != {"error_type", "message"}
                or not isinstance(failure.get("error_type"), str) or len(failure["error_type"]) > 80
                or not isinstance(failure.get("message"), str) or len(failure["message"]) > 500
            ):
                raise ResearchStoreError("last research cycle failure is invalid")
            if failure is not None and any(
                sanitize_untrusted(failure[key])[0] != failure[key]
                for key in ("error_type", "message")
            ):
                raise ResearchStoreError("last research cycle failure is unsafe")
            encoded = json.dumps(last_cycle, sort_keys=True, ensure_ascii=True).encode("utf-8")
            if len(encoded) > 32 * 1024:
                raise ResearchStoreError("last research cycle exceeds the byte limit")

    def load_state(self) -> dict[str, Any]:
        value = self._load_json("state.json", _default_state(), limit=MAX_STATE_BYTES)
        self._validate_state(value)
        return value

    def load_index(self) -> dict[str, Any]:
        value = self._load_json("current.json", _default_index(), limit=MAX_INDEX_BYTES)
        self._validate_index(value)
        return value

    def _validate_index(self, index: Mapping[str, Any]) -> None:
        self._exact_keys(index, {"schema_version", "updated_at", "findings"}, "research index")
        if index.get("schema_version") != SCHEMA_VERSION:
            raise ResearchStoreError("research index schema is unsupported")
        self._optional_text(index.get("updated_at"), "research index updated_at", 40)
        findings = index.get("findings")
        if not isinstance(findings, Mapping) or len(findings) > MAX_CURRENT_FINDINGS:
            raise ResearchStoreError("research current index findings must be a bounded object")
        allowed = {
            "finding_id", "identity_basis", "identity_aliases", "status", "title", "kind",
            "decision", "target_area", "summary", "value", "evidence", "risks", "conflicts",
            "conflict_resolution", "conflict_resolution_history", "source_urls", "source_ids", "provider_provenance",
            "created_at", "updated_at", "observation_count",
        }
        for key, finding in findings.items():
            if not isinstance(key, str) or len(key) > 80 or not isinstance(finding, Mapping):
                raise ResearchStoreError("research finding identity is invalid")
            if set(finding) != allowed or finding.get("finding_id") != key:
                raise ResearchStoreError("research finding has an invalid shape")
            if finding.get("status") != "ready_for_review" or finding.get("decision") not in {"add", "extend", "replace", "reject", "watch"}:
                raise ResearchStoreError("research finding status or decision is invalid")
            for field, limit in (("title", 180), ("target_area", 160), ("summary", 500), ("value", 500)):
                value = finding.get(field)
                if not isinstance(value, str) or len(value) > limit:
                    raise ResearchStoreError(f"research finding {field} is invalid")
            for field, limit in (("identity_basis", 300), ("kind", 32), ("created_at", 40), ("updated_at", 40)):
                value = finding.get(field)
                if not isinstance(value, str) or len(value) > limit:
                    raise ResearchStoreError(f"research finding {field} is invalid")
            count = finding.get("observation_count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ResearchStoreError("research finding observation_count is invalid")
            for field, limit in (("identity_aliases", 32), ("evidence", 8), ("risks", 8), ("conflicts", 10), ("source_urls", 8), ("source_ids", 24), ("provider_provenance", 24)):
                value = finding.get(field)
                if not isinstance(value, list) or len(value) > limit:
                    raise ResearchStoreError(f"research finding {field} is invalid")
            if not finding["provider_provenance"]:
                raise ResearchStoreError("research finding has no verified provider provenance")
            for field, chars in (("identity_aliases", 300), ("evidence", 300), ("risks", 300), ("conflicts", 300), ("source_urls", 1_024), ("source_ids", 240)):
                if not all(isinstance(item, str) and 0 < len(item) <= chars for item in finding[field]):
                    raise ResearchStoreError(f"research finding {field} entries are invalid")
            resolution = finding.get("conflict_resolution")
            history = finding.get("conflict_resolution_history")
            if resolution is not None and not _valid_resolution_record(resolution):
                raise ResearchStoreError("research conflict resolution is invalid")
            if (
                not isinstance(history, list) or len(history) > 10
                or not all(_valid_resolution_record(item) for item in history)
                or (resolution is not None and (not history or resolution != history[-1]))
            ):
                raise ResearchStoreError("research conflict resolution history is invalid")
            for provenance in finding.get("provider_provenance", []):
                if not isinstance(provenance, Mapping) or set(provenance) != {
                    "source_id", "source_digest", "selected_provider", "fallback_used", "deepseek_attempt",
                }:
                    raise ResearchStoreError("research provider provenance is invalid")
                source_id = provenance.get("source_id")
                source_digest = provenance.get("source_digest")
                if (
                    not isinstance(source_id, str) or not 0 < len(source_id) <= 240
                    or not isinstance(source_digest, str) or _SHA256_RE.fullmatch(source_digest) is None
                    or not isinstance(provenance.get("fallback_used"), bool)
                    or not isinstance(provenance.get("deepseek_attempt"), Mapping)
                ):
                    raise ResearchStoreError("research provider provenance values are invalid")
                if provenance.get("selected_provider") not in {
                    _DEEPSEEK_MODEL, _LUNA_MODEL,
                }:
                    raise ResearchStoreError("research selected provider is invalid")
                attempt = provenance["deepseek_attempt"]
                if set(attempt) != {
                    "provider", "model", "effort", "status", "tool_calls", "exact_failure", "transport_proof",
                }:
                    raise ResearchStoreError("research DeepSeek provenance has an invalid shape")
                if (
                    attempt.get("provider") != "nous_mcp"
                    or attempt.get("model") != "deepseek/deepseek-v4-flash-0731"
                    or attempt.get("effort") != "max"
                    or attempt.get("status") not in {"succeeded", "failed", "unavailable"}
                ):
                    raise ResearchStoreError("research DeepSeek provenance is invalid")
                calls = attempt.get("tool_calls")
                failure = attempt.get("exact_failure")
                status = attempt.get("status")
                if (
                    not isinstance(calls, int) or isinstance(calls, bool)
                    or not 0 <= calls <= _MAX_DEEPSEEK_TOOL_CALLS
                    or not isinstance(failure, str) or len(failure) > 1_000
                    or sanitize_untrusted(failure)[0] != failure
                ):
                    raise ResearchStoreError("research DeepSeek attempt values are invalid")
                if status == "succeeded":
                    if (
                        provenance.get("selected_provider") != _DEEPSEEK_MODEL
                        or provenance.get("fallback_used") is not False
                        or failure
                        or not 1 <= calls <= _MAX_DEEPSEEK_TOOL_CALLS
                        or not _valid_saved_transport_proof(attempt.get("transport_proof"), calls)
                    ):
                        raise ResearchStoreError("research DeepSeek success proof is invalid")
                elif (
                    provenance.get("selected_provider") != _LUNA_MODEL
                    or provenance.get("fallback_used") is not True
                    or calls != 0 or not failure or attempt.get("transport_proof") is not None
                ):
                    raise ResearchStoreError("research Luna fallback provenance is invalid")

    def _atomic_json(self, name: str, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            with self._root_fd(create=True) as root_fd:
                descriptor = os.open(
                    temporary,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_fd,
                )
                try:
                    data = payload.encode("utf-8")
                    view = memoryview(data)
                    while view:
                        written = os.write(descriptor, view)
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                os.fsync(root_fd)
        except Exception:
            try:
                with self._root_fd(create=False) as root_fd:
                    os.unlink(temporary, dir_fd=root_fd)
            except OSError:
                pass
            raise

    def save_state(self, state: Mapping[str, Any]) -> None:
        self._validate_state(state)
        if len(json.dumps(state, sort_keys=True, ensure_ascii=True).encode("utf-8")) > MAX_STATE_BYTES:
            raise ResearchStoreError("research state exceeds the byte limit")
        self._atomic_json("state.json", state)

    def save_index(self, index: Mapping[str, Any]) -> None:
        self._validate_index(index)
        encoded = json.dumps(index, sort_keys=True, ensure_ascii=True).encode("utf-8")
        if len(encoded) > MAX_INDEX_BYTES:
            raise ResearchStoreError("research current index exceeds the byte limit")
        self._atomic_json("current.json", index)

    def _lock_record(self, root_fd: int, name: str) -> tuple[dict[str, Any], tuple[int, int]]:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4_096:
                raise ResearchRunLocked("research lock is malformed")
            value = json.loads(self._read_fd(descriptor, 4_096).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("lock is not an object")
            return value, (metadata.st_dev, metadata.st_ino)
        finally:
            os.close(descriptor)

    @contextmanager
    def lock(self, name: str = "research-run") -> Iterator[None]:
        if name not in {"research-run", "implementation-run", "state"}:
            raise ValueError("unsupported research lock name")
        lock_name = f".{name}.lock"
        token = uuid.uuid4().hex
        payload = json.dumps({
            "token": token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_unix": time.time(),
        }, sort_keys=True).encode("utf-8")
        with self._root_fd(create=True) as root_fd:
            acquired_identity: tuple[int, int] | None = None
            try:
                descriptor = os.open(
                    lock_name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_fd,
                )
            except FileExistsError as error:
                # The implementation runner uses the same O_EXCL lease protocol.
                # POSIX has no portable compare-and-unlink operation, so ambiguous
                # crash leftovers fail closed instead of risking a live lease.
                raise ResearchRunLocked(f"{name} is already active or requires lock review") from error
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(descriptor, view):]
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                acquired_identity = (metadata.st_dev, metadata.st_ino)
            finally:
                os.close(descriptor)
            os.fsync(root_fd)
            try:
                yield
            finally:
                try:
                    current, identity = self._lock_record(root_fd, lock_name)
                    if identity == acquired_identity and current.get("token") == token:
                        os.unlink(lock_name, dir_fd=root_fd)
                        os.fsync(root_fd)
                except (FileNotFoundError, OSError, ValueError, ResearchStoreError):
                    pass

    def append_event(self, event_type: str, payload: Mapping[str, Any], *, now: datetime | None = None) -> None:
        stamp = now or utc_now()
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": _bounded_text(event_type, 80),
            "at": iso_utc(stamp),
            "payload": dict(payload),
        }
        name = f"{int(stamp.timestamp() * 1_000_000_000):020d}-{uuid.uuid4().hex}.json"
        data = (json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
        if len(data) > MAX_EVENT_BYTES:
            raise ResearchStoreError("research event exceeds the byte limit")
        with self._root_fd(create=True) as root_fd:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                events_fd = os.open("events", directory_flags, dir_fd=root_fd)
            except FileNotFoundError:
                os.mkdir("events", mode=0o700, dir_fd=root_fd)
                events_fd = os.open("events", directory_flags, dir_fd=root_fd)
            try:
                descriptor = os.open(
                    name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=events_fd,
                )
                try:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(descriptor, view):]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                events = sorted(item for item in os.listdir(events_fd) if item.endswith(".json"))
                for obsolete in events[: max(0, len(events) - self.event_limit)]:
                    try:
                        os.unlink(obsolete, dir_fd=events_fd)
                    except FileNotFoundError:
                        pass
                os.fsync(events_fd)
            finally:
                os.close(events_fd)

    def enable(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        if batch_size is not None and (
            not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 24
        ):
            raise ValueError("batch_size must be between 1 and 24")
        current = now or utc_now()
        if dry_run:
            state = self.load_state()
            schedule = state["schedule"]
            effective_batch = batch_size if batch_size is not None else int(schedule.get("batch_size") or 6)
            if not schedule.get("enabled"):
                schedule.update({
                    "enabled": True,
                    "enabled_at": iso_utc(current),
                    "last_cycle_at": None,
                    "next_due_at": iso_utc(next_due_after(current, current)),
                    "batch_size": effective_batch,
                })
            elif batch_size is not None:
                schedule["batch_size"] = batch_size
            return state
        with self.lock("state"):
            state = self.load_state()
            schedule = state["schedule"]
            effective_batch = batch_size if batch_size is not None else int(schedule.get("batch_size") or 6)
            changed = not bool(schedule.get("enabled"))
            if not schedule.get("enabled"):
                schedule.update({
                    "enabled": True,
                    "enabled_at": iso_utc(current),
                    "last_cycle_at": None,
                    "next_due_at": iso_utc(next_due_after(current, current)),
                    "batch_size": effective_batch,
                })
            elif batch_size is not None and schedule.get("batch_size") != batch_size:
                schedule["batch_size"] = batch_size
                changed = True
            if changed:
                self.save_state(state)
        if changed:
            self.append_event("schedule_enabled", {"next_due_at": schedule["next_due_at"]}, now=current)
        return state

    def disable(self, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
        current = now or utc_now()
        if dry_run:
            state = self.load_state()
            state["schedule"]["enabled"] = False
            state["schedule"]["next_due_at"] = None
            return state
        with self.lock("state"):
            state = self.load_state()
            changed = bool(state["schedule"].get("enabled")) or state["schedule"].get("next_due_at") is not None
            state["schedule"]["enabled"] = False
            state["schedule"]["next_due_at"] = None
            if changed:
                self.save_state(state)
        if changed:
            self.append_event("schedule_disabled", {}, now=current)
        return state

    def due(self, *, now: datetime | None = None) -> tuple[bool, str]:
        state = self.load_state()
        schedule = state["schedule"]
        if not schedule.get("enabled"):
            return False, "schedule_disabled"
        next_due = schedule.get("next_due_at")
        if not next_due:
            return False, "next_due_missing"
        if (now or utc_now()) < as_utc(next_due):
            return False, "not_due"
        return True, "due"

    def mark_research_started(self, run_id: str, *, now: datetime, queued: int = 0) -> dict[str, Any]:
        with self.lock("state"):
            state = self.load_state()
            if state["implementation_run"].get("active"):
                raise ResearchRunLocked("research cannot start while implementation is active")
            state["research_run"] = {
                "active": True,
                "run_id": _bounded_text(run_id, 80),
                "started_at": iso_utc(now),
                "phase": "discovery",
                "queued_candidate_count": max(0, int(queued)),
            }
            self.save_state(state)
        return state

    def update_research_progress(self, *, phase: str, queued: int) -> dict[str, Any]:
        with self.lock("state"):
            state = self.load_state()
            state["research_run"]["phase"] = _bounded_text(phase, 80)
            state["research_run"]["queued_candidate_count"] = max(0, int(queued))
            self.save_state(state)
        return state

    def mark_research_finished(
        self,
        summary: Mapping[str, Any],
        *,
        now: datetime,
        advance_schedule: bool,
    ) -> dict[str, Any]:
        with self.lock("state"):
            state = self.load_state()
            state["research_run"] = {
                "active": False,
                "run_id": None,
                "started_at": None,
                "phase": None,
                "queued_candidate_count": 0,
            }
            state["last_cycle"] = dict(summary)
            if advance_schedule and state["schedule"].get("enabled"):
                enabled_at = state["schedule"].get("enabled_at") or iso_utc(now)
                state["schedule"]["last_cycle_at"] = iso_utc(now)
                state["schedule"]["next_due_at"] = iso_utc(next_due_after(enabled_at, now))
            self.save_state(state)
        return state

    def _pending_findings(self, *, implementation_eligible: bool = False) -> list[dict[str, Any]]:
        findings = self.load_index()["findings"]
        return sorted(
            (
                value for value in findings.values()
                if isinstance(value, dict) and value.get("status") == "ready_for_review"
                and (
                    not implementation_eligible
                    or (
                        value.get("decision") in {"add", "extend", "replace"}
                        and not value.get("conflicts")
                    )
                )
            ),
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("finding_id") or "")),
        )

    def read_model(self, *, readiness_threshold: int = DEFAULT_READINESS_THRESHOLD) -> dict[str, Any]:
        threshold = max(1, int(readiness_threshold))
        state = self.load_state()
        pending = self._pending_findings()
        implementation_chunks = self._pending_findings(implementation_eligible=True)
        ready = len(implementation_chunks) >= threshold
        if state["implementation_run"].get("active"):
            readiness_reason = "implementation_run_active"
        elif ready:
            readiness_reason = "threshold_met"
        else:
            readiness_reason = f"waiting_for_{threshold - len(implementation_chunks)}_more_eligible_findings"
        recent = [
            {
                "finding_id": item.get("finding_id"),
                "title": _bounded_text(item.get("title"), 100),
                "decision": _bounded_text(item.get("decision"), 24),
                "summary": _bounded_text(item.get("summary"), 180),
            }
            for item in pending[-SUMMARY_LIMIT:]
        ]
        schedule = state["schedule"]
        enabled_at = schedule.get("enabled_at")
        implementation = state["implementation_run"]
        implementation_projection = {
            "active": bool(implementation.get("active")),
            "run_id": _bounded_text(implementation.get("run_id"), 100) or None,
            "packet_id": _bounded_text(implementation.get("packet_id"), 80) or None,
            "started_at": _bounded_text(implementation.get("started_at"), 40) or None,
            "completed_at": _bounded_text(implementation.get("completed_at"), 40) or None,
            "status": implementation.get("status")
            if implementation.get("status") in {
                "never_run", "running", "completed", "failed", "cancelled", "interrupted_requires_review",
            } else "failed",
            "summary": _bounded_text(implementation.get("summary"), 240),
        }
        last_cycle = state.get("last_cycle")
        last_cycle_projection = None
        if isinstance(last_cycle, Mapping):
            allowed_status = {"completed", "failed"}
            last_cycle_projection = {
                "run_id": _bounded_text(last_cycle.get("run_id"), 100) or None,
                "status": last_cycle.get("status") if last_cycle.get("status") in allowed_status else "failed",
                "completed_at": _bounded_text(last_cycle.get("completed_at"), 40) or None,
                "discovered": max(0, min(24, int(last_cycle.get("discovered") or 0))),
                "analyzed": max(0, min(24, int(last_cycle.get("analyzed") or 0))),
                "provider_failures": max(0, min(24, int(last_cycle.get("provider_failures") or 0))),
                "source_failures": max(0, min(8, int(last_cycle.get("source_failures") or 0))),
                "ready_for_review": max(0, min(MAX_CURRENT_FINDINGS, int(last_cycle.get("ready_for_review") or 0))),
                "batch_size": max(1, min(24, int(last_cycle.get("batch_size") or 6))),
                "deepseek_disabled_reason": _bounded_text(last_cycle.get("deepseek_disabled_reason"), 180) or None,
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "schedule_enabled": bool(schedule.get("enabled")),
            "configured_batch_size": int(schedule.get("batch_size") or 6),
            "cadence": cadence_label(enabled_at, utc_now()) if enabled_at else None,
            "last_cycle_at": schedule.get("last_cycle_at"),
            "last_cycle": last_cycle_projection,
            "next_due_at": schedule.get("next_due_at"),
            "queued_candidate_count": int(state["research_run"].get("queued_candidate_count") or 0),
            "research_active": bool(state["research_run"].get("active")),
            "research_phase": state["research_run"].get("phase"),
            "implementation_active": implementation_projection["active"],
            "implementation_chunk_count": len(implementation_chunks),
            "implementation_ready": ready and not state["implementation_run"].get("active"),
            "readiness_threshold": threshold,
            "readiness_reason": readiness_reason,
            "recent_pending": recent,
            "last_implementation_run": implementation_projection,
        }

    def build_implementation_packet(
        self,
        *,
        finding_ids: Sequence[str] | None = None,
        readiness_threshold: int = DEFAULT_READINESS_THRESHOLD,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        pending = self._pending_findings(implementation_eligible=True)
        if finding_ids is not None:
            wanted = {str(item) for item in finding_ids}
            pending = [item for item in pending if item.get("finding_id") in wanted]
            missing = wanted - {str(item.get("finding_id")) for item in pending}
            if missing:
                raise ResearchStoreError("implementation packet requested unknown or non-ready findings")
        threshold = max(1, int(readiness_threshold))
        if len(pending) < threshold and not force:
            raise ResearchNotReady(f"{len(pending)} findings available; threshold is {threshold}")
        created = now or utc_now()
        chunks = [
            {
                "finding_id": item["finding_id"],
                "title": item["title"],
                "decision": item["decision"],
                "target_area": item["target_area"],
                "summary": item["summary"],
                "value": item["value"],
                "risks": item["risks"],
                "evidence": item["evidence"],
                "conflicts": item.get("conflicts", []),
                "provider_provenance": item["provider_provenance"],
            }
            for item in pending
        ]
        basis = json.dumps(chunks, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        packet_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return {
            "schema_version": SCHEMA_VERSION,
            "packet_id": packet_id,
            "created_at": iso_utc(created),
            "forced_below_threshold": bool(force and len(chunks) < threshold),
            "readiness_threshold": threshold,
            "chunk_count": len(chunks),
            "contract": "review_and_plan_only_until_an_explicit_implementation_run_begins",
            "chunks": chunks,
        }

    def begin_implementation_run(
        self,
        packet: Mapping[str, Any],
        *,
        run_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or utc_now()
        with self.lock("state"):
            state = self.load_state()
            if state["implementation_run"].get("active"):
                raise ResearchRunLocked("implementation run is already active")
            if state["research_run"].get("active"):
                raise ResearchRunLocked("implementation cannot start while research is active")
            state["implementation_run"] = {
                "active": True,
                "run_id": _bounded_text(run_id, 80),
                "packet_id": _bounded_text(packet.get("packet_id"), 80),
                "started_at": iso_utc(current),
                "completed_at": None,
                "status": "running",
                "summary": "",
            }
            self.save_state(state)
        self.append_event("implementation_started", {
            "run_id": state["implementation_run"]["run_id"],
            "packet_id": state["implementation_run"]["packet_id"],
        }, now=current)
        return state["implementation_run"]

    def finish_implementation_run(
        self,
        *,
        status: str,
        summary: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "failed", "cancelled", "interrupted_requires_review"}:
            raise ValueError("unsupported implementation terminal status")
        current = now or utc_now()
        with self.lock("state"):
            state = self.load_state()
            previous = state["implementation_run"]
            if not previous.get("active"):
                raise ResearchStoreError("no implementation run is active")
            previous.update({
                "active": False,
                "completed_at": iso_utc(current),
                "status": status,
                "summary": _bounded_text(summary, 240),
            })
            self.save_state(state)
        self.append_event("implementation_finished", {
            "run_id": previous.get("run_id"),
            "status": status,
            "summary": previous["summary"],
        }, now=current)
        return dict(previous)

    def resolve_finding_conflicts(
        self,
        finding_id: str,
        *,
        resolution_summary: str,
        reviewer: str = "local_owner",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        summary = _bounded_text(resolution_summary, 500)
        reviewer_name = _bounded_text(reviewer, 80)
        if not summary or not reviewer_name:
            raise ValueError("conflict resolution requires a reviewer and summary")
        with self.lock("research-run"):
            index = self.load_index()
            finding = index["findings"].get(str(finding_id))
            if not isinstance(finding, dict):
                raise ResearchStoreError("research finding does not exist")
            if not finding.get("conflicts"):
                raise ResearchStoreError("research finding has no unresolved conflicts")
            unresolved = list(finding["conflicts"])
            resolved_claims: list[str] = []
            for claim in unresolved:
                bounded = _bounded_text(claim, 300)
                if bounded and bounded not in resolved_claims:
                    resolved_claims.append(bounded)
                if len(resolved_claims) >= 10:
                    break
            finding["conflicts"] = []
            resolution = {
                "resolved_at": iso_utc(now or utc_now()),
                "reviewer": reviewer_name,
                "summary": summary,
                "resolved_conflicts": resolved_claims,
            }
            history = list(finding.get("conflict_resolution_history") or [])
            history.append(resolution)
            finding["conflict_resolution_history"] = history[-10:]
            finding["conflict_resolution"] = resolution
            self.save_index(index)
        self.append_event("finding_conflicts_resolved", {
            "finding_id": _bounded_text(finding_id, 80),
            "reviewer": reviewer_name,
            "resolution_summary": summary,
            "resolved_conflicts": unresolved,
        }, now=now or utc_now())
        return json.loads(json.dumps(finding))
