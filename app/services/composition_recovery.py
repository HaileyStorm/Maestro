"""Explicit, authenticated composition attempts; no automatic native replay."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import uuid

from services.composition_package import (
    CompositionAttemptUnresolved, CompositionError, atomic_json, digest,
    read_receipt, sign_receipt,
)
from services.queue_recovery_runtime import sha256_file

_workers = {}
_lock = threading.RLock()
APP_INCARNATION = uuid.uuid4().hex


def process_identity(pid):
    """Linux process birth identity; inaccessible and reused identities hold."""
    if type(pid) is not int or pid < 1 or os.name != "posix":
        raise CompositionAttemptUnresolved("Process identity is unavailable")
    root = Path("/proc") / str(pid)
    try:
        raw = (root / "stat").read_text()
        fields = raw[raw.rindex(")") + 2:].split()
        return {"pid": pid, "uid": root.stat().st_uid,
                "start_ticks": int(fields[19]), "exe": os.readlink(root / "exe"),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except FileNotFoundError:
        if not root.exists():
            return None
        raise CompositionAttemptUnresolved("Process identity is unreadable") from None
    except (OSError, ValueError, IndexError):
        raise CompositionAttemptUnresolved("Process identity is unreadable") from None


def validate_process_identity(value):
    if (type(value) is not dict or set(value) != {"pid", "uid", "start_ticks", "exe", "boot_id"}
            or any(type(value[k]) is not int or value[k] < (1 if k != "uid" else 0)
                   for k in ("pid", "uid", "start_ticks"))
            or type(value["exe"]) is not str or not Path(value["exe"]).is_absolute()
            or type(value["boot_id"]) is not str or not value["boot_id"]):
        raise CompositionAttemptUnresolved("Process identity is not sealed")
    return value


def exact_process_state(identity):
    validate_process_identity(identity)
    current = process_identity(identity["pid"])
    if current is not None and current != identity:
        raise CompositionAttemptUnresolved("Process identity changed")
    return current is not None


def start_worker():
    if not sys.platform.startswith("linux"):
        return None
    identity = process_identity(os.getpid())
    validate_process_identity(identity)
    token = uuid.uuid4().hex
    with _lock:
        _workers[token] = (threading.current_thread(), threading.Event())
    return {"app": identity, "app_incarnation": APP_INCARNATION, "worker_token": token}


def finish_worker(worker):
    if worker is None:
        return
    with _lock:
        entry = _workers.get(worker["worker_token"])
        if entry is not None:
            entry[1].set()


def _joined_worker(worker):
    if worker.get("app_incarnation") != APP_INCARNATION:
        return False
    with _lock:
        entry = _workers.get(worker.get("worker_token"))
    if entry is None or not entry[1].is_set() or entry[0] is threading.current_thread():
        return False
    entry[0].join(timeout=0.1)
    return not entry[0].is_alive()


def validate_layout(layout):
    if (type(layout) is not dict or set(layout) != {"frame_directory", "encoder_destination"}
            or any(type(path) is not str or not Path(path).is_absolute() for path in layout.values())
            or not re.fullmatch(r"maestro_frames_[0-9a-f]{32}", Path(layout["frame_directory"]).name)
            or not re.fullmatch(r"maestro_[0-9a-f]{32}\.mp4", Path(layout["encoder_destination"]).name)
            or Path(layout["frame_directory"]).parent != Path(layout["encoder_destination"]).parent):
        raise CompositionAttemptUnresolved("Native paths are not sealed")
    for path in layout.values():
        if Path(path).is_symlink():
            raise CompositionAttemptUnresolved("Native paths changed")


def no_path_encoder(layout, uid):
    """Pre-spawn paths also fence the crash before a child PID is recorded."""
    validate_layout(layout)
    expected = {str(Path(layout["frame_directory"]) / "frame_%04d.png"), layout["encoder_destination"]}
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                args = (entry / "cmdline").read_bytes().split(b"\0")
                if expected.intersection(os.fsdecode(arg) for arg in args if arg):
                    return False
            except FileNotFoundError:
                if entry.exists():
                    raise CompositionAttemptUnresolved("Encoder inventory is unreadable")
    except OSError:
        raise CompositionAttemptUnresolved("Encoder inventory is unreadable") from None
    return True


def prove_stopped(receipt, *, idle_barrier):
    execution = receipt.get("execution")
    if type(execution) is not dict or set(execution) != {"worker", "blender", "layout", "encoder"}:
        raise CompositionAttemptUnresolved("Legacy attempt has no stopped fence")
    worker = execution["worker"]
    if (type(worker) is not dict or set(worker) != {"app", "app_incarnation", "worker_token"}
            or any(type(worker[k]) is not str or not re.fullmatch(r"[0-9a-f]{32}", worker[k])
                   for k in ("app_incarnation", "worker_token"))):
        raise CompositionAttemptUnresolved("Worker identity is unavailable")
    app_alive = exact_process_state(worker["app"])
    if app_alive and not _joined_worker(worker):
        raise CompositionAttemptUnresolved("The previous worker has not drained")
    blender_alive = exact_process_state(execution["blender"])
    if blender_alive and idle_barrier(execution["blender"]) != execution["blender"]:
        raise CompositionAttemptUnresolved("Blender incarnation changed")
    encoder = execution["encoder"]
    if encoder is not None and exact_process_state(encoder):
        raise CompositionAttemptUnresolved("The previous encoder has not drained")
    if not no_path_encoder(execution["layout"], worker["app"]["uid"]):
        raise CompositionAttemptUnresolved("A previous encoder still owns native paths")
    return True


def read_units(package, directory, binding, secret):
    """Inspect the entire sealed closure before any stop probe or new dispatch."""
    completed, interrupted = {}, []
    for segment in package["segments"]:
        sid = segment["id"]
        path, receipt_path = Path(directory) / (sid + ".mp4"), Path(directory) / (sid + ".receipt.json")
        if not receipt_path.exists():
            if path.exists() or path.is_symlink() or receipt_path.is_symlink():
                raise CompositionAttemptUnresolved("Unsealed composition unit exists")
            continue
        receipt = read_receipt(receipt_path, secret)
        if (receipt.get("package_sha256") != digest(package) or receipt.get("binding") != binding
                or receipt.get("segment_id") != sid or receipt.get("segment_sha256") != digest(segment)
                or receipt.get("blender_mcp_revision") != binding["renderer_identity"]["blender_mcp_revision"]):
            raise CompositionError("Original segment binding changed")
        if receipt.get("state") == "completed":
            size, sha = sha256_file(path)
            if size != receipt.get("size") or sha != receipt.get("sha256"):
                raise CompositionError("Completed original segment changed")
            completed[sid] = {"size": size, "sha256": sha,
                              "receipt_sha256": sha256_file(receipt_path)[1],
                              "producer_binding": copy.deepcopy((receipt.get("reused_from") or {}).get("producer_binding", binding))}
        elif receipt.get("state") == "attempting":
            interrupted.append(receipt)
        else:
            raise CompositionAttemptUnresolved("Original segment state is unknown")
    return completed, interrupted


def prepare_attempt(package, source, destination, *, source_binding, binding, secret, idle_barrier, request_manifest):
    completed, interrupted = read_units(package, source, source_binding, secret)
    for receipt in interrupted:
        prove_stopped(receipt, idle_barrier=idle_barrier)
    if not interrupted:
        raise CompositionAttemptUnresolved("No interrupted native attempt is available")
    destination = Path(destination)
    intent = {"schema": "maestro/composition-recovery/v1", "package_sha256": digest(package),
              "source_attempt": Path(source).name, "source_binding": source_binding,
              "binding": binding, "completed": completed, "request_manifest": copy.deepcopy(request_manifest)}
    intent_path = destination / "attempt.intent.json"
    if intent_path.exists() or intent_path.is_symlink():
        if read_receipt(intent_path, secret) != intent:
            raise CompositionError("Fresh attempt intent changed")
    else:
        atomic_json(intent_path, sign_receipt(intent, secret))
    for segment in package["segments"]:
        sid = segment["id"]
        if sid not in completed:
            continue
        original = Path(source) / (sid + ".mp4")
        target = destination / (sid + ".mp4")
        descriptor = completed[sid]
        if not target.exists():
            with original.open("rb") as src, target.open("xb") as dst:
                os.chmod(target, 0o600)
                shutil.copyfileobj(src, dst)
                dst.flush(); os.fsync(dst.fileno())
        if sha256_file(target) != (descriptor["size"], descriptor["sha256"]):
            raise CompositionError("Reused unit copy changed")
        receipt = {"package_sha256": digest(package), "binding": copy.deepcopy(binding),
                   "blender_mcp_revision": binding["renderer_identity"]["blender_mcp_revision"],
                   "segment_id": sid, "segment_sha256": digest(segment), "state": "completed",
                   "size": descriptor["size"], "sha256": descriptor["sha256"],
                   "reused_from": {"binding": source_binding, **descriptor}}
        receipt_path = destination / (sid + ".receipt.json")
        if receipt_path.exists() and read_receipt(receipt_path, secret) != receipt:
            raise CompositionError("Reused unit receipt changed")
        atomic_json(receipt_path, sign_receipt(receipt, secret))
    return intent


def validate_attempt(package, directory, original, original_binding, binding, secret, request_manifest):
    directory, original = Path(directory), Path(original)
    if directory == original:
        return
    intent = read_receipt(directory / "attempt.intent.json", secret)
    source_name = intent.get("source_attempt")
    if (set(intent) != {"schema", "package_sha256", "source_attempt", "source_binding", "binding", "completed", "request_manifest"}
            or intent.get("request_manifest") != request_manifest
            or intent.get("schema") != "maestro/composition-recovery/v1"
            or intent.get("package_sha256") != digest(package)
            or intent.get("binding") != binding
            or type(source_name) is not str
            or (source_name != original.name and not re.fullmatch(r"attempt-[0-9a-f]{32}", source_name))):
        raise CompositionError("Fresh attempt binding changed")
    source = original if source_name == original.name else original / source_name
    if source == directory or source.is_symlink() or not source.is_dir():
        raise CompositionError("Original attempt directory changed")
    source_binding = intent.get("source_binding")
    if type(source_binding) is not dict or any(source_binding.get(k) != v for k,v in original_binding.items() if k != "renderer_identity"):
        raise CompositionError("Original attempt identity changed")
    completed, _interrupted = read_units(package, source, source_binding, secret)
    if completed != intent.get("completed"):
        raise CompositionError("Original completed closure changed")
    for sid, descriptor in completed.items():
        receipt = read_receipt(directory / (sid + ".receipt.json"), secret)
        if receipt.get("reused_from") != {"binding": source_binding, **descriptor}:
            raise CompositionError("Reused producer lineage changed")


_reconciling = set()


def reconcile_once(key, operation):
    with _lock:
        if key in _reconciling:
            return False
        _reconciling.add(key)
    def run():
        try:
            operation()
        finally:
            with _lock:
                _reconciling.discard(key)
    try:
        threading.Thread(target=run, name="composition-recovery", daemon=False).start()
    except BaseException:
        with _lock:
            _reconciling.discard(key)
        raise
    return True
