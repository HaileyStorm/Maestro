"""Server-bound local GPU lease lifecycle; import performs no coordinator I/O.

The manager persists each intent, waits here before acquiring Gen, calls
before_start immediately before model work, and polls the callable guard during
execution. Only after its exact owned child is reaped may it close the lease.
Bindings and intent records are server-owned, never HTTP request configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import importlib.util
import hashlib
import json
import math
import os
try:
    import grp
    import pwd
except ImportError:
    grp = pwd = None
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

INTENT_SCHEMA = "maestro.h3-prompt-rewriter.gpu-intent.v1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_IDENTITY = ("request_id", "lease_id", "project_id", "agent_id", "workspace",
             "authority_epoch", "decision_generation", "start", "end")


class H3GpuLeaseError(RuntimeError):
    pass


class H3GpuLeaseCancelled(H3GpuLeaseError):
    pass


class H3GpuLeaseUnavailable(H3GpuLeaseError):
    pass


class H3GpuLeasePublicationUncertain(H3GpuLeaseError):
    """Reconcile the exact request; never publish it again."""


class H3GpuLeaseNotPublished(H3GpuLeaseError):
    """The supported client definitively rejected publication."""


class H3GpuLeaseWithdrawalUnconfirmed(H3GpuLeaseError):
    """Keep the durable cleanup intent; confirmation may be retried without publication."""


@dataclass(frozen=True)
class H3GpuLeaseBinding:
    coordinator_root: Path
    project_id: str
    workspace: Path

    def commitment(self):
        raw = json.dumps({"coordinator_root": str(self.coordinator_root), "project_id": self.project_id,
            "workspace": str(self.workspace)}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return hashlib.sha256(raw).hexdigest()

    def validate(self):
        if sys.platform != "linux" or os.name != "posix":
            raise H3GpuLeaseUnavailable("Local GPU leases require the supported Linux coordinator host")
        if _SAFE_ID.fullmatch(self.project_id) is None:
            raise H3GpuLeaseError("Invalid server project binding")
        for path in (self.coordinator_root, self.workspace):
            if not isinstance(path, Path) or not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
                raise H3GpuLeaseUnavailable("Configure existing canonical coordinator and project directories")


class _InstalledClient:
    def __init__(self, binding):
        self.binding = binding
        self.reader = None
        self.event_type = None

    def _load(self):
        if self.reader is None:
            root = self.binding.coordinator_root
            for directory in (root, root / "lib", root / "scripts"):
                _trusted_source(directory, directory=True)
            for name in ("lib/client_authority.py", "scripts/gpu_coord_cli.py", "lib/queue_visibility.py", "lib/gpu_coord.py"):
                _trusted_source(root / name)
            reader = _module(root / "lib/client_authority.py")
            previous = sys.modules.get("gpu_coord")
            try:
                sys.modules["gpu_coord"] = _module(root / "lib/gpu_coord.py")
                self.event_type = _module(root / "lib/queue_visibility.py").ResponseEvents
            finally:
                if previous is None:
                    sys.modules.pop("gpu_coord", None)
                else:
                    sys.modules["gpu_coord"] = previous
            self.reader = reader
        return self.reader

    def events(self, request_id):
        self._load()
        return self.event_type(self.binding.coordinator_root / "outbox", f"{request_id}.json")

    def validate_binding(self):
        self._load()
        registry, _ = _snapshot(self.binding.coordinator_root / "registry/projects.json")
        projects = registry.get("projects")
        if (registry.get("schema") != "gpu-coord/registry/v1" or not isinstance(projects, list)
                or any(not isinstance(item, dict) for item in projects)):
            raise H3GpuLeaseUnavailable("Coordinator project registry is invalid")
        selected = [item for item in projects if item.get("project_id") == self.binding.project_id]
        if len(selected) != 1 or selected[0].get("workspace") != str(self.binding.workspace):
            raise H3GpuLeaseUnavailable("Register this exact server project and workspace before requesting GPU work")

    def _command(self, arguments, *, read=False, deadline=None):
        self._load()
        timeout = 5 if deadline is None else min(5, _remaining(deadline))
        result = subprocess.run([sys.executable, "-I", str(self.binding.coordinator_root / "scripts/gpu_coord_cli.py"), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if read else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=timeout, check=False, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "GPU_COORD_ROOT": str(self.binding.coordinator_root),
                 "PYTHONNOUSERSITE": "1", "CUDA_VISIBLE_DEVICES": ""})
        if arguments[0] == "request" and result.returncode in {2, 3}:
            raise H3GpuLeaseNotPublished("Supported request client rejected publication; this ID must not be withdrawn")
        if result.returncode or (read and len(result.stdout) > 4 * 1024 * 1024):
            raise H3GpuLeaseUnavailable("GPU coordinator client did not complete; reconcile the saved intent")
        return json.loads(result.stdout) if read else None

    def request(self, intent):
        root, request_id = self.binding.coordinator_root, intent["request_id"]
        if any(path.exists() for path in (root / "inbox" / f"{request_id}.json",
                root / "inbox" / f"{request_id}.reply.json", root / "outbox" / f"{request_id}.json")):
            raise H3GpuLeaseNotPublished("Request ID is occupied; no request was published")
        self._command(["request", "--request-id", intent["request_id"], "--project-id", intent["project_id"],
            "--agent-id", intent["agent_id"], "--minutes", str(math.ceil(intent["minimum_remaining_seconds"] / 60)),
            "--notes", "Maestro H3 PromptEnhance: local model loading and prompt rewrite; includes orderly stop margin",
            "--callback-workspace", str(self.binding.workspace), "--wait", "0"])

    def poll(self, request_id):
        try:
            return _snapshot(self.binding.coordinator_root / "outbox" / f"{request_id}.json")[0]
        except FileNotFoundError:
            return None

    def owns_request(self, intent, *, deadline):
        _remaining(deadline)
        inbox = self.binding.coordinator_root / "inbox"
        paths = [inbox / f'{intent["request_id"]}.json']
        for path in (inbox / "archive").glob(f'*-{intent["request_id"]}.json'):
            _remaining(deadline)
            paths.append(path)
            if len(paths) > 64:
                raise H3GpuLeaseUnavailable("Request history exceeds the bounded ownership check")
        found = False
        for path in paths:
            _remaining(deadline)
            try:
                value, _ = _snapshot(path)
            except FileNotFoundError:
                continue
            if (value.get("schema") != "gpu-coord/request/v1"
                    or any(value.get(key) != intent[key] for key in ("request_id", "project_id", "agent_id"))
                    or value.get("callback_workspace") != str(self.binding.workspace)):
                return False
            found = True
        _remaining(deadline)
        return found

    def authority(self, intent, minimum):
        return self._load().acquire_authority_bundle(self.binding.coordinator_root,
            request_id=intent["request_id"], project_id=intent["project_id"], agent_id=intent["agent_id"],
            workspace=self.binding.workspace, minimum_remaining_seconds=minimum)["receipt"]

    def withdraw(self, request_id, *, deadline):
        self._command(["reply", request_id, "--action", "withdraw", "--message", "H3 prompt rewrite finished or cancelled"], deadline=deadline)

    def no_authority(self, request_id, *, deadline):
        _remaining(deadline)
        path = self.binding.coordinator_root / "schedule/leases.json"
        ledger, generation = _snapshot(path)
        _remaining(deadline)
        response = self.poll(request_id)
        if (not isinstance(response, dict) or response.get("request_id") != request_id
                or response.get("type") != "denied" or response.get("lease_id", "missing") is not None):
            return False
        status = self._command(["status"], read=True, deadline=deadline)
        coordinator = status.get("coordinator", {})
        authority, leases = status.get("authority", {}), ledger.get("leases")
        age = time.time() - _instant(coordinator.get("updated_at"))
        valid = (status.get("live") is True and authority.get("live") is True and authority.get("error") is None
            and isinstance(authority.get("epoch"), str) and bool(authority["epoch"])
            and coordinator.get("schema") == "gpu-coord/coordinator-state/v1" and 0 <= age <= 15
            and type(coordinator.get("leader_pid")) is int and coordinator["leader_pid"] > 0
            and coordinator.get("leader") is True and coordinator.get("telemetry", {}).get("healthy") is True
            and all(coordinator.get(key) is None for key in ("ingress_error", "authority_epoch_error", "lease_ledger_error", "registry_error"))
            and ledger.get("schema") == "gpu-coord/leases/v1" and ledger.get("authority_epoch") == authority["epoch"]
            and ledger.get("updated_at") == coordinator.get("updated_at")
            and isinstance(leases, list) and all(isinstance(lease, dict) for lease in leases)
            and all(lease.get("status") in {"cancelled", "complete", "denied"}
                    for lease in leases if lease.get("request_id") == request_id))
        _remaining(deadline)
        repeated = self.poll(request_id)
        _remaining(deadline)
        _, after = _snapshot(path)
        _remaining(deadline)
        age = time.time() - _instant(coordinator.get("updated_at"))
        return valid and 0 <= age <= 15 and repeated == response and after == generation


def _trusted_source(path, *, directory=False):
    info = path.lstat()
    regular = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not regular or info.st_uid not in {0, os.getuid()} or info.st_mode & 0o002:
        raise H3GpuLeaseUnavailable("Install the supported owner-controlled GPU coordinator client")
    if info.st_mode & 0o020:
        trusted = {0, os.getuid()}
        members = grp.getgrgid(info.st_gid).gr_mem
        if (any(pwd.getpwnam(name).pw_uid not in trusted for name in members)
                or any(user.pw_gid == info.st_gid and user.pw_uid not in trusted for user in pwd.getpwall())):
            raise H3GpuLeaseUnavailable("Coordinator source group contains an untrusted host account")


def _module(path):
    name = "_maestro_h3_" + path.stem + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise H3GpuLeaseWithdrawalUnconfirmed("GPU confirmation deadline elapsed")
    return remaining


def _snapshot(path):
    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024 * 1024:
            raise H3GpuLeaseUnavailable("Coordinator snapshot is not a bounded regular file")
        raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024 or identity(before) != identity(os.fstat(stream.fileno())):
            raise H3GpuLeaseUnavailable("Coordinator snapshot changed while reading")
    if identity(before) != identity(path.lstat()):
        raise H3GpuLeaseUnavailable("Coordinator snapshot generation changed")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise H3GpuLeaseUnavailable("Coordinator snapshot has duplicate fields")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise H3GpuLeaseUnavailable("Coordinator snapshot is not an object")
    return value, (identity(before), raw)


def _seconds(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise H3GpuLeaseError("Invalid finite lifecycle time bound")
    return float(value)


def _instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise H3GpuLeaseError("GPU lease time lacks a timezone")
    return parsed.timestamp()


class H3PromptRewriterGpuLease:
    def __init__(self, binding, *, request_id, agent_id, persist_intent,
                 planned_seconds=600, stop_margin_seconds=150,
                 _client=None, _monotonic=time.monotonic, _wall=time.time, _sleep=time.sleep):
        binding.validate()
        if any(type(value) is not str or _SAFE_ID.fullmatch(value) is None for value in (request_id, agent_id)):
            raise H3GpuLeaseError("Request and agent IDs must be exact safe server-owned identifiers")
        minimum = max(750, _seconds(planned_seconds, 86400) + _seconds(stop_margin_seconds, 86400))
        if minimum > 86400 or not callable(persist_intent):
            raise H3GpuLeaseError("A bounded operation and durable intent writer are required")
        self.binding, self.persist_intent = binding, persist_intent
        self._intent = {"schema": INTENT_SCHEMA, "request_id": request_id, "agent_id": agent_id,
            "binding_sha256": binding.commitment(), "project_id": binding.project_id,
            "minimum_remaining_seconds": minimum, "stop_margin_seconds": stop_margin_seconds,
            "state": "request_attempted"}
        self.client = _client if _client is not None else _InstalledClient(binding)
        self.monotonic, self.wall, self.sleep = _monotonic, _wall, _sleep
        self.request_attempted = self.withdraw_attempted = self.closed = self.cleanup_only = self.lost = False
        self.receipt = self.identity = self.checked_at = None

    @property
    def intent(self):
        return dict(self._intent)

    def _persist(self, state):
        value = {**self.intent, "state": state}
        if self.persist_intent(dict(value)) is not True:
            raise H3GpuLeaseError("Durable GPU intent was not confirmed; nothing may be published")
        self._intent = value

    @classmethod
    def restore_from_intent(cls, binding, intent, *, persist_intent, **dependencies):
        expected = {"schema", "request_id", "agent_id", "binding_sha256", "project_id",
                    "minimum_remaining_seconds", "stop_margin_seconds", "state"}
        if (type(intent) is not dict or set(intent) != expected or intent["schema"] != INTENT_SCHEMA
                or intent["binding_sha256"] != binding.commitment() or intent["project_id"] != binding.project_id
                or intent["state"] not in {"request_attempted", "withdraw_attempted", "closed", "unpublished"}):
            raise H3GpuLeaseError("Saved intent does not bind this server project")
        value = cls(binding, request_id=intent["request_id"], agent_id=intent["agent_id"],
            planned_seconds=intent["minimum_remaining_seconds"] - intent["stop_margin_seconds"],
            stop_margin_seconds=intent["stop_margin_seconds"], persist_intent=persist_intent, **dependencies)
        if value.intent["minimum_remaining_seconds"] != intent["minimum_remaining_seconds"]:
            raise H3GpuLeaseError("Saved intent duration changed")
        value._intent = dict(intent)
        value.cleanup_only = True
        value.request_attempted = intent["state"] != "unpublished"
        value.withdraw_attempted = intent["state"] in {"withdraw_attempted", "closed"}
        value.closed = intent["state"] == "closed"
        return value

    def acquire(self, *, cancel_check=lambda: False, wait_seconds=3600):
        if self.cleanup_only or self.lost or self.withdraw_attempted or self.closed:
            raise H3GpuLeaseError("This intent is cleanup-only or no longer usable")
        duration = _seconds(wait_seconds, 3600)
        if cancel_check():
            self.cleanup_only = self.request_attempted
            raise H3GpuLeaseCancelled("GPU wait cancelled")
        if not self.request_attempted:
            self.client.validate_binding()
            self._persist("request_attempted")
            self.request_attempted = True
            try:
                self.client.request(dict(self.intent))
            except H3GpuLeaseNotPublished:
                self.cleanup_only, self.request_attempted = True, False
                self._persist("unpublished")
                raise
            except Exception as error:
                self.cleanup_only = True
                raise H3GpuLeasePublicationUncertain("Saved request requires reconciliation; it will not be resent") from error
        deadline, next_poll = self.monotonic() + duration, self.monotonic()
        with self.client.events(self.intent["request_id"]) as events:
            changed = True
            while self.monotonic() < deadline:
                if cancel_check():
                    self.cleanup_only = True
                    raise H3GpuLeaseCancelled("GPU wait cancelled")
                if changed or self.monotonic() >= next_poll:
                    response = self.client.poll(self.intent["request_id"])
                    next_poll = self.monotonic() + (60 if events.event_driven else 5)
                    if response is not None:
                        if response.get("request_id") != self.intent["request_id"]:
                            raise H3GpuLeaseError("Coordinator response request binding changed")
                        if response.get("type") == "granted":
                            self._refresh(self.intent["minimum_remaining_seconds"])
                            if cancel_check():
                                self.cleanup_only = True
                                raise H3GpuLeaseCancelled("GPU wait cancelled")
                            return self
                        if response.get("type") not in {"ack", "message"}:
                            raise H3GpuLeaseError("Coordinator did not grant this request")
                changed = events.wait(min(0.25, max(0, deadline - self.monotonic())))
        raise H3GpuLeaseError("GPU wait reached its bound; reconcile or close the same saved request")

    def _refresh(self, minimum):
        try:
            receipt = self.client.authority(dict(self.intent), minimum)
            if (receipt.get("schema") != "gpu-coord/client-authority-snapshot/v1" or receipt.get("authorized") is not True
                    or any(receipt.get(key) != self.intent[key] for key in ("request_id", "project_id", "agent_id"))
                    or receipt.get("workspace") != str(self.binding.workspace)
                    or type(receipt.get("decision_generation")) is not int or receipt["decision_generation"] < 1
                    or not all(isinstance(receipt.get(key), str) and receipt[key] for key in ("lease_id", "authority_epoch"))
                    or not _instant(receipt["start"]) <= self.wall() < _instant(receipt["end"])
                    or _instant(receipt["end"]) - self.wall() <= minimum):
                raise H3GpuLeaseError("Coherent GPU authority does not bind the operation")
            identity = tuple(receipt[key] for key in _IDENTITY)
            if self.identity is not None and self.identity != identity:
                raise H3GpuLeaseError("GPU lease identity, epoch, generation, or interval changed")
            self.receipt, self.identity, self.checked_at = dict(receipt), identity, self.monotonic()
        except Exception as error:
            self.lost = True
            raise H3GpuLeaseError("GPU authority was lost; stop the owned runtime child") from error
        return dict(self.receipt)

    def before_start(self):
        if self.identity is None or self.lost or self.cleanup_only or self.withdraw_attempted or self.closed:
            raise H3GpuLeaseError("GPU work requires a current acquired lease")
        return self._refresh(self.intent["minimum_remaining_seconds"])

    def __call__(self):
        if self.identity is None or self.lost or self.cleanup_only or self.withdraw_attempted or self.closed:
            raise H3GpuLeaseError("GPU authority is unavailable")
        elapsed = self.monotonic() - self.checked_at
        if elapsed < 0 or not _instant(self.receipt["start"]) <= self.wall() < _instant(self.receipt["end"]):
            self.lost = True
            raise H3GpuLeaseError("GPU lease clock or expiry boundary failed")
        if elapsed >= 2:
            self._refresh(self.intent["stop_margin_seconds"])
        return True

    def close(self, *, child_reaped, confirm_seconds=10):
        if child_reaped is not True:
            raise H3GpuLeaseError("Reap the exact owned runtime child before withdrawing")
        duration = _seconds(confirm_seconds, 60)
        if self.closed or not self.request_attempted:
            return True
        deadline = self.monotonic() + duration
        if not self.withdraw_attempted:
            try:
                owned = self.client.owns_request(self.intent, deadline=deadline) is True
            except Exception:
                owned = False
            if not owned or self.monotonic() >= deadline:
                raise H3GpuLeaseWithdrawalUnconfirmed("Exact request ownership is unconfirmed; retain intent without withdrawing")
            self._persist("withdraw_attempted")
            self.withdraw_attempted = True
            try:
                self.client.withdraw(self.intent["request_id"], deadline=deadline)
            except Exception:
                # Publication may have succeeded; confirm without sending again.
                pass
        while self.monotonic() < deadline:
            try:
                confirmed = self.client.no_authority(self.intent["request_id"], deadline=deadline) is True
            except Exception:
                confirmed = False
            if confirmed and self.monotonic() < deadline:
                self._persist("closed")
                self.closed = True
                return True
            self.sleep(min(1, max(0, deadline - self.monotonic())))
        raise H3GpuLeaseWithdrawalUnconfirmed("GPU withdrawal is unconfirmed; retain the saved cleanup intent without resending")
