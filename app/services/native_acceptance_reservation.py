"""Temporary identity-only admission for an owner-private native acceptance run.

Absent a pinned startup plan this module leaves ordinary scheduling untouched.
A configured reservation lasts until process exit, including after its job ends.
It is an admission boundary, never GPU lease authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import weakref
import uuid


class NativeAcceptanceReserved(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def job_identity(job):
    result = {key: job.get(key) for key in ("id", "created_at", "workspace")}
    for key, alias in (
        ("project_instance", "_recovery_project_digest"),
        ("owner_principal", "_recovery_owner_digest"),
        ("request_manifest", "_recovery_manifest_pointer"),
    ):
        if key not in job and alias not in job:
            raise ValueError("Native acceptance identity is unavailable")
        value = job[key] if key in job else job[alias]
        if value is None or (key in job and alias in job
                and digest(value) != digest(job[alias])):
            raise ValueError("Native acceptance identity conflicts")
        result[key + "_sha256"] = digest(value)
    return result


class _WorkerAdmission:
    """One callback capability; the parent admission record is its lifetime."""
    def __init__(self, reservation, admission, native_slot):
        self.reservation = reservation
        self.admission = admission
        self.native_slot = native_slot
        self.used = False


class _PlanningAdmission:
    """Private binding to the real durable Director parent, never a child job."""
    def __init__(self, reservation, pid, parent, params, selection):
        self.reservation, self.pid, self.parent = reservation, pid, parent
        self.params, self.selection = params, selection
        self.thread = None
        self.generation_lock = None


class _ImageAdmission:
    """One retained image phase; children acquire their own scheduler permits."""
    def __init__(self, reservation, pid, parent, pipeline, settings):
        self.reservation, self.pid, self.parent, self.pipeline = reservation, pid, parent, pipeline
        self.params = pipeline["params"]
        self.settings = json.loads(json.dumps(settings))
        self.anchor_original = {key: self.params[key] for key in ("generated_reference_image_filename",) if key in self.params}
        self.anchor = None
        self.thread = None
        self.children = {}
        self.closed = False


class _SavedImageAdmission(_ImageAdmission):
    """Two fresh detached image operations on one terminal saved parent."""
    def __init__(self, reservation, pid, parent, pipeline, settings, descriptor):
        super().__init__(reservation, pid, parent, pipeline, settings)
        self.descriptor = json.loads(json.dumps(descriptor))
        self.next_slot = 0
        self.started = False


class _SavedVideoAdmission(_ImageAdmission):
    """One fresh detached clip repair; the scheduler owns model admission."""
    def __init__(self, reservation, pid, parent, pipeline, prepared, descriptor, operation_id):
        super().__init__(reservation, pid, parent, pipeline, prepared["request"])
        self.descriptor = json.loads(json.dumps(descriptor))
        self.slot = dict(prepared["unit"])
        self.operation_id = operation_id
        self.input_seal = json.loads(json.dumps(prepared["input_seal"]))
        self.original_terminal = json.loads(json.dumps(prepared.get("original_terminal")))
        self.parent_status = pipeline.get("status")
        self.child_snapshot = None
        self.started = True
        self.thread = weakref.ref(threading.current_thread())


class Reservation:
    def __init__(self, target=None, dispatch_check=None, *, release_action="start-next"):
        if type(release_action) is not str or release_action not in {"start-next", "resume", "recovery-retry", "continue", "rerun-image", "rerun-video"}:
            raise ValueError("Invalid native acceptance release action")
        self.target = json.loads(json.dumps(target)) if target is not None else None
        if self.target is not None and "running_execution_attempt" in self.target:
            baseline = self.target.get("execution_attempt")
            running = self.target["running_execution_attempt"]
            if (type(baseline) is not int or baseline < 1
                    or type(running) is not int or running != baseline + 1):
                raise ValueError("Invalid native acceptance running attempt")
        self.release_action = release_action
        if release_action == "recovery-retry":
            if (self.target is None or self.target.get("kind") != "generation"
                    or self.target.get("running_execution_attempt") != self.target.get("execution_attempt", 0) + 1
                    or type(self.target.get("running_recovery_attempt")) is not int
                    or self.target["running_recovery_attempt"] != self.target.get("recovery_attempt", -1) + 1):
                raise ValueError("Invalid native acceptance failed retry")
        self._retry_job = None
        self._retry_committed = False
        self._permits = {}
        self._workers = {}
        self._planning = {}
        self._images = None
        self._director_manifests = {}
        self._lock = threading.RLock()
        self._dispatch_check = dispatch_check

    def dispatch_ready(self):
        if self._dispatch_check is None:
            return True
        try:
            return self._dispatch_check() is True
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            return False

    def permits_job_release(self, job):
        if self.target is None:
            return job.get("_native_acceptance_manual_recovery") is not True
        return self.eligible(job)

    def prepare_job_release(self, job, candidate):
        """Stamp only server-owned operational state before durable release."""
        if not self.permits_job_release(job):
            return False
        if self.target is not None:
            candidate["_native_acceptance_manual_recovery"] = True
        return True

    @contextmanager
    def failed_recovery_retry(self, job, *, held_director=False):
        """Bind one owner-validated durable retry before publishing its worker.

        A spent transition cannot be resent, including after uncertain
        persistence. Never hold our lock across the lifecycle checkpoint.
        """
        from services.job_lifecycle import held_director_recovery_retry_allowed
        if type(held_director) is not bool:
            raise ValueError("Held Director retry option must be boolean")
        if self.target is None:
            yield False
            return
        with self._lock:
            if (self.release_action != "recovery-retry" or self._retry_job is not None
                    or not self.dispatch_ready()
                    or not (held_director_recovery_retry_allowed(job) if held_director
                            else job.get("status") == "failed")
                    or not self._matches_job(job, self.target["execution_attempt"])
                    or job.get("output_files") or job.get("clip_output_files")
                    or (job.get("recovery_cursor") or {}).get("completed_units")
                    or job.get("_director_video_lora_task_retention_pending")):
                raise NativeAcceptanceReserved("Failed retry reservation changed; reconcile without resending")
            self._retry_job = job
        yield True
        with self._lock:
            if (job.get("status") != "queued" or job.get("queue_held") is not False
                    or job.get("_native_acceptance_manual_recovery") is not True
                    or not self._matches_job(job, self.target["running_execution_attempt"],
                        self.target["running_recovery_attempt"])):
                raise NativeAcceptanceReserved("Failed retry acknowledgement changed; reconcile without resending")
            self._retry_committed = True

    def _retry_matches(self, job, *, completed_director=False):
        return (self._retry_job is job and self._retry_committed
            and self._matches_job(job, self.target["running_execution_attempt"],
                self.target["running_recovery_attempt"], completed_director=completed_director))

    def permits_original_retry_attachment(self, job):
        """Read-only Director attachment; never grants model/release authority."""
        if (self.target is None or not self.dispatch_ready()
                or job.get("kind") != "director_child"
                or job.get("_native_acceptance_manual_recovery") is not True):
            return False
        return self._retry_matches(job, completed_director=True)

    def eligible(self, job):
        """Admit the frozen attempt or its one committed failed-retry successor."""
        if self.target is None:
            return job.get("_native_acceptance_manual_recovery") is not True
        if not self.dispatch_ready():
            return False
        if self.target.get("kind") == "director_planning":
            return False
        if self.target.get("kind") in {"director_images", "director_saved_images", "director_saved_video"}:
            with self._lock:
                token = self._images
                return (token is not None and self._images_matches(token)
                    and any(self._image_child_matches(job, entry)
                        for entry in token.children.values()))
        if self._retry_job is not None:
            return self._retry_matches(job)
        return self._matches_job(job, self.target.get("execution_attempt"))

    def bind_images(self, pid, parent, pipeline, slots, settings):
        if self.target is None:
            return None
        with self._lock:
            token = _ImageAdmission(self, pid, parent, pipeline, settings)
            if (self._images is not None or pipeline.get("status") != "paused"
                    or pipeline.get("pause_reason") != "review_prompts"
                    or slots != self.target.get("image_slots")
                    or not self._images_matches(token) or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Director image reservation did not verify")
            self._images = token
            return token

    def _images_matches(self, token):
        try:
            if type(token) is _SavedVideoAdmission:
                return self._saved_video_matches(token)
            if type(token) is _SavedImageAdmission:
                return self._saved_images_matches(token)
            params = dict(token.params)
            if token.anchor is not None:
                if params.pop("generated_reference_image_filename", None) != token.anchor:
                    return False
                params.update(token.anchor_original)
            return (not token.closed and self.target.get("kind") == "director_images"
                and token.reservation is self and token.pid == self.target["pipeline_id"]
                and token.pipeline.get("params") is token.params
                and (token.thread is None or token.thread() is not None and token.thread().is_alive())
                and token.pipeline.get("created_at") == token.parent.get("created_at")
                and str(token.pipeline.get("workspace") or "default") == str(token.parent.get("workspace") or "default")
                and token.pipeline.get("status") not in {"completed", "failed", "cancelled", "blocked"}
                and token.parent.get("kind") == "director_pipeline"
                and job_identity(token.parent) == self.target["identity"]
                and digest(params) == self.target["request_sha256"]
                and digest(token.settings) == self.target["image_settings_sha256"]
                and digest({"clip_plans": token.pipeline.get("clip_plans"),
                    "planned_clips": token.pipeline.get("_planned_clips")}) == self.target["plans_sha256"])
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def _saved_images_matches(self, token):
        params = dict(token.params)
        if token.anchor is not None:
            if params.pop("generated_reference_image_filename", None) != token.anchor:
                return False
            params.update(token.anchor_original)
        cursor = token.parent.get("recovery_cursor") or {}
        return (not token.closed and self.target.get("kind") == "director_saved_images"
            and token.reservation is self and token.pid == self.target["pipeline_id"]
            and token.pipeline.get("params") is token.params
            and token.pipeline.get("status") == "cancelled"
            and token.pipeline.get("created_at") == token.parent.get("created_at")
            and str(token.pipeline.get("workspace") or "default") == str(token.parent.get("workspace") or "default")
            and token.parent.get("kind") == "director_pipeline"
            and job_identity(token.parent) == self.target["identity"]
            and cursor.get("state") == token.descriptor and not cursor.get("pending_state")
            and digest(params) == self.target["request_sha256"]
            and digest(token.settings) == self.target["image_settings_sha256"]
            and digest({"clip_plans": token.pipeline.get("clip_plans"),
                "planned_clips": token.pipeline.get("_planned_clips")}) == self.target["plans_sha256"]
            and (token.thread is None or token.thread() is not None and token.thread().is_alive()))

    def _saved_video_matches(self, token):
        cursor = token.parent.get("recovery_cursor") or {}
        return (not token.closed and self.target.get("kind") == "director_saved_video"
            and token.reservation is self and token.pid == self.target["pipeline_id"]
            and token.pipeline.get("params") is token.params
            and token.pipeline.get("status") == token.parent_status
            and (token.parent_status == "cancelled" and token.original_terminal is None
                and "original_terminal_sha256" not in self.target
                or token.parent_status == "failed" and isinstance(token.original_terminal, dict)
                and digest(token.original_terminal) == self.target.get("original_terminal_sha256"))
            and token.pipeline.get("created_at") == token.parent.get("created_at")
            and str(token.pipeline.get("workspace") or "default") == str(token.parent.get("workspace") or "default")
            and token.parent.get("kind") == "director_pipeline"
            and job_identity(token.parent) == self.target["identity"]
            and cursor.get("state") == token.descriptor and not cursor.get("pending_state")
            and digest(token.params) == self.target["request_sha256"]
            and digest(token.settings) == self.target["generated_request_sha256"]
            and digest(token.input_seal) == self.target["input_seal_sha256"]
            and all((token.input_seal.get("owner_project") if token.original_terminal is not None
                    else token.input_seal.get("identity") or {}).get(key) == self.target["identity"][key]
                for key in ("project_instance_sha256", "owner_principal_sha256"))
            and token.slot == self.target["video_slot"]
            and digest({"clip_plans": token.pipeline.get("clip_plans"),
                "planned_clips": token.pipeline.get("_planned_clips")}) == self.target["plans_sha256"]
            and token.thread() is not None and token.thread().is_alive())

    def bind_saved_video(self, pid, parent, pipeline, state, descriptor, prepared, clip_index, *, operation_id):
        with self._lock:
            token = _SavedVideoAdmission(self, pid, parent, pipeline, prepared, descriptor, operation_id)
            if (self._images is not None or clip_index != 0 or type(clip_index) is not int
                    or operation_id is not None and (type(operation_id) is not str or not operation_id)
                    or descriptor.get("sha256") != self.target.get("state_sha256")
                    or state.get("status") != token.parent_status
                    or state.get("status") == "cancelled" and state.get("phase") != "cancelled"
                    or len(state.get("clips") or []) != 1 or state["clips"][0].get("video_filename")
                    or state.get("_params_snapshot", {}).get("hold_video") is not True
                    or state.get("_params_snapshot", {}).get("auto_mode") is not False
                    or not self._images_matches(token) or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Saved Director video checkpoint changed")
            self._images = token
            return token

    def seal_saved_video_child_request(self, token, params):
        self._require_image_worker(token)
        if type(token) is not _SavedVideoAdmission or token.children or token.child_snapshot is not None:
            raise NativeAcceptanceReserved("Saved video child admission changed")
        expected = dict(token.settings)
        for key in ("_director_recovery_unit", "_director_recovery_parent_id", "_director_recovery_job_id", "_director_recovery_attempt"):
            expected.pop(key, None)
        expected["_maestro_session_id"] = token.params.get("_maestro_session_id")
        expected["_maestro_access_policy"] = token.params.get("_maestro_access_policy") or {}
        if token.operation_id is not None:
            expected["_director_repair_operation_id"] = token.operation_id
        snapshot = params.get("_director_video_lora_child_snapshot")
        observed = dict(params)
        observed.pop("_director_video_lora_child_snapshot", None)
        expected.pop("_director_video_lora_child_snapshot", None)
        if (not isinstance(snapshot, dict) or observed != expected
                or params.get("_director_video_lora_parent_snapshot") != token.settings.get("_director_video_lora_parent_snapshot")):
            raise NativeAcceptanceReserved("Saved video creative request changed during admission")
        token.child_snapshot = json.loads(json.dumps(snapshot))

    def validate_saved_video_child(self, token, job, unit, attempt):
        self._require_image_worker(token)
        params = dict(job.get("params") or {})
        for key in ("_h3_offload_plan", "_maestro_credit_owner_lineage"):
            params.pop(key, None)
        account_key = "_maestro_credit_account_id"
        if account_key in params and account_key not in token.settings:
            if (type(job.get("_credit_account_id")) is not str or not job["_credit_account_id"]
                    or params[account_key] != job["_credit_account_id"]):
                raise NativeAcceptanceReserved("Saved video server account lineage changed")
            params.pop(account_key)
        expected_id = f"director-{token.pid}-0-{digest({'parent': token.pid, 'unit': token.slot, 'attempt': 0})[:24]}"
        expected_params = dict(token.settings)
        if token.child_snapshot is not None:
            expected_params["_director_video_lora_child_snapshot"] = token.child_snapshot
        if token.operation_id is not None:
            expected_params["_director_repair_operation_id"] = token.operation_id
        if (token.children or unit != token.slot or type(attempt) is not int or attempt != 0
                or job.get("id") != expected_id or job.get("kind") != "director_child"
                or params != expected_params
                or params.get("generation_mode") != "video"
                or params.get("model_type") != "minimax_h3"
                or params.get("_director_detached_operation") is not True
                or str(job.get("workspace") or "default") != str(token.pipeline.get("workspace") or "default")
                or os.path.realpath(job.get("out_dir") or "") != os.path.realpath(token.pipeline.get("out_dir") or "")):
            raise NativeAcceptanceReserved("Saved Director video child request changed")
        return digest(unit), dict(unit)

    def verify_saved_video_completion(self, token, evidence):
        with self._lock:
            self._require_image_worker(token)
            entry = token.children.get(digest(token.slot))
            job = entry[0] if entry is not None else {}
            outputs = evidence.get("outputs") if isinstance(evidence, dict) else None
            artifacts = evidence.get("artifacts") if isinstance(evidence, dict) else None
            if (entry is None or not self._image_child_matches(job, entry)
                    or job.get("status") != "completed" or job.get("_director_saved_video_worker_active")
                    or id(job) in self._permits or type(outputs) is not list or not outputs
                    or any(type(name) is not str or not name for name in outputs)
                    or job.get("output_files") != outputs or type(artifacts) is not list
                    or not artifacts or not any(isinstance(a, dict) and a.get("basename") == outputs[-1]
                        for a in artifacts)):
                raise NativeAcceptanceReserved("Saved Director video lacks drained producer evidence")
            token.verified_output = outputs[-1]
            return self.children_identity(token, token.slot)

    def publish_saved_video(self, token):
        with self._lock:
            descriptor = (token.parent.get("recovery_cursor") or {}).get("state")
            if (type(token) is not _SavedVideoAdmission or token is not self._images
                    or token.closed or token.thread() is not threading.current_thread()
                    or not getattr(token, "verified_output", None) or not isinstance(descriptor, dict)
                    or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Saved Director video publication changed")
            token.descriptor = json.loads(json.dumps(descriptor))
            token.closed = True

    def bind_saved_images(self, pid, parent, pipeline, state, descriptor, settings, clip_index):
        with self._lock:
            token = self._images
            if token is None:
                token = _SavedImageAdmission(self, pid, parent, pipeline, settings, descriptor)
                if (descriptor.get("sha256") != self.target.get("state_sha256")
                        or state.get("status") != "cancelled" or state.get("phase") != "cancelled"
                        or len(state.get("clips") or []) != 1
                        or state.get("generated_reference_image_filename")
                        or state["clips"][0].get("start_image_filename")
                        or state["clips"][0].get("video_filename")
                        or state.get("output_files")
                        or state.get("_params_snapshot", {}).get("hold_video") is not True
                        or state.get("_params_snapshot", {}).get("auto_mode") is not False):
                    raise NativeAcceptanceReserved("Saved Director image checkpoint changed")
            if (type(token) is not _SavedImageAdmission or token.pid != pid
                    or token.parent is not parent or token.pipeline is not pipeline
                    or descriptor != token.descriptor or settings != token.settings
                    or type(clip_index) is not int or clip_index != 0
                    or token.thread is not None or token.started or token.next_slot >= 2
                    or not self._images_matches(token) or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Saved Director image operation is unavailable")
            self._images = token
            token.thread = weakref.ref(threading.current_thread())
            token.started = True
            return token

    def saved_image_unit(self, token):
        with self._lock:
            self._require_image_worker(token)
            if type(token) is not _SavedImageAdmission or not token.started:
                raise NativeAcceptanceReserved("Saved Director image operation changed")
            return dict(self.target["image_slots"][token.next_slot])

    def verify_saved_image_completion(self, token, evidence):
        with self._lock:
            unit = self.saved_image_unit(token)
            entry = token.children.get(digest(unit))
            job = entry[0] if entry is not None else {}
            units = (job.get("recovery_cursor") or {}).get("completed_units") or []
            outputs = evidence.get("outputs") if isinstance(evidence, dict) else None
            if (entry is None or not self._image_child_matches(job, entry)
                    or job.get("status") != "completed" or job.get("_director_saved_image_worker_active")
                    or id(job) in self._permits
                    or not isinstance(outputs, list) or len(outputs) != 1
                    or len(units) != 1 or units[0].get("kind") != "ordinary_repeat"
                    or units[0].get("state") != "completed" or units[0].get("dependencies")
                    or len(evidence.get("artifacts") or []) != 1
                    or evidence["artifacts"] != units[0].get("artifacts")
                    or job.get("output_files") != outputs
                    or type(outputs[0]) is not str or os.path.basename(outputs[0]) != outputs[0]
                    or outputs[0].startswith(".")
                    or Path(outputs[0]).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
                    or token.next_slot == 1 and outputs[0] == token.anchor):
                raise NativeAcceptanceReserved("Saved Director image lacks drained producer evidence")
            token.verified_output = outputs[0]
            return job, unit, outputs[0]

    def children_identity(self, token, unit):
        with self._lock:
            self._require_image_worker(token)
            entry = token.children.get(digest(unit))
            if entry is None or not self._image_child_matches(entry[0], entry):
                raise NativeAcceptanceReserved("Director image child identity changed")
            return json.loads(json.dumps(entry[1]))

    def publish_saved_image_intent(self, token, descriptor):
        with self._lock:
            if (type(token) not in {_SavedImageAdmission, _SavedVideoAdmission} or token is not self._images
                    or token.closed or not token.started or token.thread() is not threading.current_thread()
                    or (token.parent.get("recovery_cursor") or {}).get("state") != descriptor
                    or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Saved Director image intent changed")
            token.descriptor = json.loads(json.dumps(descriptor))

    def publish_saved_image(self, token, descriptor, filename):
        with self._lock:
            # The committed parent descriptor advances before this capability.
            if (type(token) is not _SavedImageAdmission or token is not self._images
                    or token.closed or not token.started or token.thread() is not threading.current_thread()
                    or filename != getattr(token, "verified_output", None)
                    or (token.parent.get("recovery_cursor") or {}).get("state") != descriptor
                    or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Saved Director image publication changed")
            if token.next_slot == 0:
                token.anchor = filename
                token.params["generated_reference_image_filename"] = filename
            token.descriptor = json.loads(json.dumps(descriptor))
            token.next_slot += 1
            token.started = False
            token.thread = None
            token.closed = token.next_slot == 2

    def activate_images(self, token, worker):
        with self._lock:
            if (type(token) is not _ImageAdmission or token is not self._images
                    or token.thread is not None or worker is not threading.current_thread()
                    or not self._images_matches(token) or not self.dispatch_ready()):
                raise NativeAcceptanceReserved("Director image worker admission changed")
            token.thread = weakref.ref(worker)

    def _require_image_worker(self, token):
        if (type(token) not in {_ImageAdmission, _SavedImageAdmission, _SavedVideoAdmission} or token is not self._images
                or token.thread is None or token.thread() is not threading.current_thread()
                or not self._images_matches(token) or not self.dispatch_ready()):
            raise NativeAcceptanceReserved("Director image parent admission changed")

    def validate_image_child(self, token, job, unit, attempt):
        with self._lock:
            if type(token) is _SavedVideoAdmission:
                return self.validate_saved_video_child(token, job, unit, attempt)
            self._require_image_worker(token)
            slot = {key: unit.get(key) for key in ("kind", "variant", "index")}
            key = digest(slot)
            params = job.get("params") or {}
            if type(token) is _SavedImageAdmission:
                expected = self.saved_image_unit(token)
                child_id = f"director-{token.pid}-0-{digest({'parent': token.pid, 'unit': expected, 'attempt': 0})[:24]}"
                if slot != expected or job.get("id") != child_id or params.get("_director_detached_operation") is not True:
                    raise NativeAcceptanceReserved("Saved Director image child identity changed")
            if (slot not in self.target["image_slots"] or key in token.children
                    or any(type(slot[name]) is not int for name in ("variant", "index"))
                    or type(attempt) is not int or attempt != 0
                    or job.get("kind") != "director_child"
                    or params.get("generation_mode") != "image"
                    or params.get("_director_pipeline_id") != token.pid
                    or str(job.get("workspace") or "default") != str(token.pipeline.get("workspace") or "default")
                    or os.path.realpath(job.get("out_dir") or "") != os.path.realpath(token.pipeline.get("out_dir") or "")
                    or params.get("repeat_generation") != 1 or params.get("video_length") != 1):
                raise NativeAcceptanceReserved("Director image child slot changed")
            refs = params.get("image_refs") or []
            if slot["kind"] == "image_anchor":
                valid_refs = token.anchor is None and not refs
            else:
                valid_refs = (token.anchor is not None and refs == [
                    os.path.realpath(os.path.join(token.pipeline["out_dir"], token.anchor))])
            role = "creator" if slot["kind"] == "image_anchor" else "editor"
            if (not valid_refs
                    or any(params.get(key) != value for key, value in token.settings[role].items())):
                raise NativeAcceptanceReserved("Director image child request changed")
            return key, slot

    def enroll_image_child(self, token, job, unit, attempt):
        with self._lock:
            key, slot = self.validate_image_child(token, job, unit, attempt)
            recovery = job.get("recovery_unit") or {}
            identity = job_identity(job)
            if (type(job.get("execution_attempt")) is not int or job["execution_attempt"] != 1
                    or type(job.get("recovery_attempt", 0)) is not int or job.get("recovery_attempt", 0) != 0
                    or recovery.get("director_kind") != slot["kind"]
                    or recovery.get("variant") != slot["variant"] or recovery.get("index") != slot["index"]
                    or recovery.get("attempt") != attempt
                    or any(identity[key] != self.target["identity"][key] for key in (
                        "project_instance_sha256", "owner_principal_sha256"))):
                raise NativeAcceptanceReserved("Director image child provenance changed")
            token.children[key] = (job, job_identity(job), job["execution_attempt"],
                job.get("recovery_attempt", 0), digest(job.get("params")))

    def _image_child_matches(self, job, entry):
        try:
            identity = job_identity(job)
            params_sha256 = digest(job.get("params"))
            lineage = self._director_manifests.get(id(job))
            if lineage is not None and type(self._images) is _SavedVideoAdmission:
                original_job, parent_ref, manifests, params_hashes = lineage
                parent = parent_ref()
                if (original_job is not job or identity["request_manifest_sha256"] not in manifests
                        or params_sha256 not in params_hashes
                        or job.get("status") != "completed" and (parent is None or not parent.is_alive())):
                    return False
                identity["request_manifest_sha256"] = entry[1]["request_manifest_sha256"]
                params_sha256 = entry[4]
            return (entry[0] is job and identity == entry[1]
                and type(job.get("execution_attempt")) is int and job["execution_attempt"] == entry[2]
                and type(job.get("recovery_attempt", 0)) is int and job.get("recovery_attempt", 0) == entry[3]
                and params_sha256 == entry[4])
        except (ValueError, TypeError, KeyError):
            return False

    def seal_image_anchor(self, token, filename, verified_outputs):
        with self._lock:
            self._require_image_worker(token)
            entry = token.children.get(digest({"kind": "image_anchor", "variant": 0, "index": 0}))
            if (token.anchor is not None or entry is None or entry[0].get("status") != "completed"
                    or not self._image_child_matches(entry[0], entry)
                    or type(filename) is not str or os.path.basename(filename) != filename
                    or filename not in verified_outputs):
                raise NativeAcceptanceReserved("Director image anchor is not sealed")
            token.anchor = filename
            token.params["generated_reference_image_filename"] = filename

    def finish_images(self, token):
        with self._lock:
            self._require_image_worker(token)
            if (len(token.children) != len(self.target["image_slots"])
                    or any(entry[0].get("status") != "completed" or not self._image_child_matches(entry[0], entry)
                        for entry in token.children.values())):
                raise NativeAcceptanceReserved("Director image children are not complete")
            token.closed = True

    def abandon_images(self, token):
        with self._lock:
            if type(token) in {_ImageAdmission, _SavedImageAdmission, _SavedVideoAdmission} and token is self._images:
                token.closed = True

    def bind_planning(self, pid, parent, params, selection):
        if self.target is None:
            return None
        token = _PlanningAdmission(self, pid, parent, params, selection)
        if not self._planning_matches(token) or not self.dispatch_ready():
            raise NativeAcceptanceReserved("Director planning reservation did not verify")
        return token

    def _planning_matches(self, token):
        try:
            return (self.target.get("kind") == "director_planning"
                and token.pid == self.target["pipeline_id"]
                and token.parent.get("kind") == "director_pipeline"
                and job_identity(token.parent) == self.target["identity"]
                and digest(token.params) == self.target["request_sha256"]
                and digest(token.selection) == self.target["llm_selection_sha256"]
                and token.parent.get("status") not in {"completed", "failed", "cancelled"})
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def admit_planning(self, token, generation_lock):
        if self.target is None:
            return
        if (type(token) is not _PlanningAdmission or token.reservation is not self
                or not generation_lock.locked() or not self._planning_matches(token)
                or not self.dispatch_ready()):
            raise NativeAcceptanceReserved("Director parent admission changed")
        with self._lock:
            current = threading.current_thread()
            if (current in self._planning
                    or (token.thread is not None and token.thread() is not current)):
                raise NativeAcceptanceReserved("Director parent admission belongs to another worker")
            token.thread, token.generation_lock = weakref.ref(current), generation_lock
            self._planning[current] = token

    def release_planning(self, token):
        with self._lock:
            current = threading.current_thread()
            if self._planning.get(current) is token:
                del self._planning[current]

    def _matches_job(self, job, execution_attempt, recovery_attempt=None, *, completed_director=False):
        try:
            identity = job_identity(job)
            lineage = self._director_manifests.get(id(job))
            if lineage is not None:
                original_job, parent_ref, manifests, _ = lineage
                parent = parent_ref()
                terminal_adoption = (completed_director and job.get("kind") == "director_child"
                    and job.get("status") == "completed")
                if (original_job is not job or not terminal_adoption and (parent is None or not parent.is_alive())
                        or identity["request_manifest_sha256"] not in manifests):
                    return False
                identity["request_manifest_sha256"] = self.target["identity"]["request_manifest_sha256"]
            return (identity == self.target["identity"]
                and type(job.get("execution_attempt")) is int
                and type(job.get("recovery_attempt", 0)) is int
                and job.get("execution_attempt") == execution_attempt
                and job.get("recovery_attempt", 0) == (
                    self.target["recovery_attempt"] if recovery_attempt is None else recovery_attempt))
        except (ValueError, TypeError, KeyError):
            return False

    @contextmanager
    def director_manifest_checkpoint(self, job, predecessor, successor, *, successor_params=None):
        """Keep validated execution-record retention within its live admission.

        The Director owner seals and validates both manifests before entering.
        Only the validated manifest and record hashes may advance; the startup
        target and the enrolled creative request remain unchanged.
        Do not hold our lock across the durable lifecycle callback, which takes
        the queue lock. Readers see one immutable, exact-job lineage tuple.
        """
        if self.target is None:
            yield
            return
        with self._lock:
            admission = self._permits.get(id(job))
            worker = self._workers.get(threading.current_thread())
            if (self.target.get("kind") not in {"generation", "director_saved_video"}
                    or self.target.get("kind") == "director_saved_video" and (type(successor_params) is not dict
                        or type(self._images) is not _SavedVideoAdmission
                        or not any(entry[0] is job for entry in self._images.children.values()))
                    or job.get("kind") != "director_child" or job.get("status") != "running"
                    or job.get("_recovery_manifest_pointer") != predecessor
                    or type(predecessor) is not dict or type(successor) is not dict
                    or not predecessor or not successor or admission is None
                    or not self._parent_valid(admission)
                    or not (admission[0]() is threading.current_thread()
                            or worker is not None and worker.admission is admission)):
                raise NativeAcceptanceReserved("Director manifest admission changed")
            previous = self._director_manifests.get(id(job))
            before = job_identity(job)
            attempts = (job.get("execution_attempt"), job.get("recovery_attempt", 0))
            after = {**before, "request_manifest_sha256": digest(successor)}
            self._director_manifests[id(job)] = (
                job, admission[0], (digest(predecessor), digest(successor)),
                (digest(job.get("params")), digest(successor_params if successor_params is not None else job.get("params"))))
        try:
            yield
            if (job.get("_recovery_manifest_pointer") != successor
                    or job_identity(job) != after
                    or (job.get("execution_attempt"), job.get("recovery_attempt", 0)) != attempts
                    or successor_params is not None and digest(job.get("params")) != digest(successor_params)):
                raise NativeAcceptanceReserved("Director manifest checkpoint was not published")
        except BaseException:
            with self._lock:
                if previous is None:
                    self._director_manifests.pop(id(job), None)
                else:
                    self._director_manifests[id(job)] = previous
            raise
        else:
            with self._lock:
                self._director_manifests[id(job)] = (job, admission[0], (digest(successor),), (digest(job.get("params")),))

    def admitted(self, job, generation_lock):
        if not self.permits_job_release(job) or not generation_lock.locked():
            raise NativeAcceptanceReserved("Native acceptance admission changed")
        if self.target is None:
            return
        with self._lock:
            current = threading.current_thread()
            worker = self._workers.get(current)
            # A delegated output callback may park and reacquire the slot.
            # Its new admission still belongs to the original scheduler;
            # reacquisition cannot permanently authorize the Listener thread.
            parent = worker.admission[0] if worker is not None else weakref.ref(current)
            self._permits[id(job)] = (parent, job, generation_lock)

    def released(self, job):
        with self._lock:
            self._permits.pop(id(job), None)

    def _parent_valid(self, admission):
        thread, job, lock = admission
        parent = thread()
        running = self.target.get("running_execution_attempt")
        if running is None or self.release_action == "recovery-retry":
            matches = self.eligible(job)
        else:
            # Composition starts advance the durable attempt after acquiring
            # the generation slot. Only this original live admission may use
            # the declared next attempt; it cannot enter queue admission again.
            matches = (job.get("status") == "running"
                and self._matches_job(job, running))
        return (self._permits.get(id(job)) is admission
            and parent is not None and parent.is_alive() and lock.locked()
            and matches
            and job.get("status") in {"queued", "preparing", "running"})

    def capture_worker(self, job, generation_lock, native_slot):
        """Mint on the admitted scheduler, never on an arbitrary worker."""
        if self.target is None:
            return None
        self.require_model_admission()
        if native_slot is None:
            raise NativeAcceptanceReserved("Native acceptance GPU slot is unavailable")
        # Native slot acquisition already takes this guard before checking
        # reservation state; keep that order to avoid cross-thread deadlocks.
        with native_slot._guard, self._lock:
            admission = self._permits.get(id(job))
            if (admission is None or admission[0]() is not threading.current_thread()
                    or admission[1] is not job or admission[2] is not generation_lock
                    or not self._parent_valid(admission)
                    or native_slot._closed or not native_slot._acquired):
                raise NativeAcceptanceReserved("Native acceptance worker admission changed")
            return _WorkerAdmission(self, admission, native_slot)

    def _worker_valid(self, token):
        # Caller holds the exact native slot guard, then the reservation lock.
        return (self._parent_valid(token.admission)
            and not token.native_slot._closed and token.native_slot._acquired)

    @contextmanager
    def worker(self, token):
        """Delegate only for this callback; reusable Listener identity is not authority."""
        if self.target is None:
            yield
            return
        if type(token) is not _WorkerAdmission or token.reservation is not self:
            raise NativeAcceptanceReserved("Native acceptance worker capability is unavailable")
        if not self.dispatch_ready():
            raise NativeAcceptanceReserved("The native acceptance guardian is not ready")
        current = threading.current_thread()
        with token.native_slot._guard, self._lock:
            if token.used or current in self._workers or not self._worker_valid(token):
                raise NativeAcceptanceReserved("Native acceptance worker admission expired")
            token.used = True
            self._workers[current] = token
        try:
            yield
        finally:
            with self._lock:
                if self._workers.get(current) is token:
                    del self._workers[current]

    def require_model_admission(self, *, workload=None):
        if self.target is None:
            return
        if workload == "llm" and self.target.get("kind") in {"director_images", "director_saved_images", "director_saved_video"}:
            raise NativeAcceptanceReserved("LLM work is outside the Director media reservation")
        if not self.dispatch_ready():
            raise NativeAcceptanceReserved("The native acceptance guardian is not ready")
        current = threading.current_thread()
        with self._lock:
            planning = self._planning.get(current)
            if (planning is not None and planning.thread() is current
                    and planning.generation_lock.locked() and self._planning_matches(planning)):
                return
            if any(admission[0]() is current and self._parent_valid(admission)
                    for admission in self._permits.values()):
                return
            token = self._workers.get(current)
        if token is not None:
            with token.native_slot._guard, self._lock:
                if self._workers.get(current) is token and self._worker_valid(token):
                    return
        raise NativeAcceptanceReserved("Model work is reserved for the current acceptance check")

    def wrap_lock(self, lock):
        return _ReservedLock(lock, self) if self.target is not None else lock

    def require_director_repair_target(self, pipeline_id, plan, job):
        """Admit only a waiter for the reserved original held video."""
        if self.target is None:
            return
        if (self.target.get("kind", "generation") != "generation"
                or not self.dispatch_ready() or not isinstance(job, dict)
                or not (self.permits_original_retry_attachment(job)
                    or self._matches_job(job, self.target["execution_attempt"], completed_director=True))
                or (job.get("params") or {}).get("_director_pipeline_id") != pipeline_id
                or job.get("kind") != "director_child"
                or plan.get("image_indices") or plan.get("video_indices") != [0]
                or plan.get("clip_count") != 1 or plan.get("should_rejoin")):
            raise NativeAcceptanceReserved("Repair must attach only the reserved original video")

    def permits_http_action(self, method, path):
        if self.target is None or method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return True
        # Model admission must not lock the owner out of their account. These
        # routes still pass the normal origin, nonce and account checks; they
        # grant no model admission and no account administration capability.
        if method.upper() == "POST" and path in {
            "/api/v1/account/nonce", "/api/v1/account/login",
            "/api/v1/account/reauth", "/api/v1/account/logout",
        }:
            return True
        target = self.target["identity"]["id"]
        if self.target.get("kind") == "director_saved_video":
            base = f"/api/v1/director/pipelines/{self.target['pipeline_id']}"
            if (method.upper(), path) == ("POST", base + "/clips/0/rerun-video"):
                with self._lock:
                    return self._images is None and self.dispatch_ready()
            with self._lock:
                children = self._images.children.values() if self._images is not None else ()
                if any((method.upper(), path) == ("POST", f"/api/v1/cancel/{entry[0]['id']}") for entry in children):
                    return True
            return (method.upper(), path) == ("PUT", "/api/v1/access-context/share-url")
        if self.target.get("kind") == "director_saved_images":
            base = f"/api/v1/director/pipelines/{self.target['pipeline_id']}"
            if (method.upper(), path) == ("POST", base + "/clips/0/rerun-image"):
                with self._lock:
                    return (self._images is None or type(self._images) is _SavedImageAdmission
                        and not self._images.closed and not self._images.started) and self.dispatch_ready()
            with self._lock:
                children = self._images.children.values() if self._images is not None else ()
                if any((method.upper(), path) == ("POST", f"/api/v1/cancel/{entry[0]['id']}") for entry in children):
                    return True
            return (method.upper(), path) == ("PUT", "/api/v1/access-context/share-url")
        if self.target.get("kind") in {"director_planning", "director_images"}:
            base = f"/api/v1/director/pipeline/{self.target['pipeline_id']}"
            if (method.upper(), path) == ("POST", base + "/continue"):
                return (self.target.get("kind") != "director_images" or self._images is None) and self.dispatch_ready()
            return (method.upper(), path) in {("POST", base + "/stop"),
                ("PUT", "/api/v1/access-context/share-url")}
        if (method.upper(), path) == ("POST", f"/api/v1/queue/{target}/{self.release_action}"):
            return self.dispatch_ready()
        original = re.fullmatch(r"director-([0-9a-f]{8})-[0-9]+-[0-9a-f]{24}", target)
        if original is not None:
            base = f"/api/v1/director/pipelines/{original[1]}"
            if (method.upper(), path) == ("POST", base + "/repair"):
                return self.dispatch_ready()
            if (method.upper(), path) == ("POST", base + "/repair/cancel"):
                return True
        return (method.upper(), path) in {
            ("POST", f"/api/v1/cancel/{target}"),
            ("PUT", "/api/v1/access-context/share-url"),
        }


class _ReservedLock:
    def __init__(self, lock, reservation):
        self._lock, self._reservation = lock, reservation

    def acquire(self, *args, **kwargs):
        self._reservation.require_model_admission()
        acquired = self._lock.acquire(*args, **kwargs)
        if acquired:
            try:
                self._reservation.require_model_admission()
            except BaseException:
                self._lock.release()
                raise
        return acquired

    def release(self):
        # Ordinary Lock supports cross-thread release; cleanup retains that rule.
        return self._lock.release()

    def locked(self):
        return self._lock.locked()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *unused):
        self.release()
        return False


def _load_startup_plan():
    value = os.environ.get("MAESTRO_NATIVE_ACCEPTANCE_PLAN")
    expected = os.environ.get("MAESTRO_NATIVE_ACCEPTANCE_PLAN_SHA256")
    if not value and not expected:
        return None, None, None
    if os.name != "posix" or not value or not expected:
        raise NativeAcceptanceReserved("Invalid native acceptance startup binding")
    root = Path(__file__).resolve().parents[2]
    path = Path(value)
    try:
        relative = path.relative_to(root / ".artifacts-temp")
        if not path.is_absolute() or ".." in relative.parts:
            raise ValueError()
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError()
        if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
            raise ValueError()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError()
            raw = handle.read(65537)
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError()
        plan = json.loads(raw)
        target = plan["target"]
        planning = target.get("kind") == "director_planning"
        saved_video = target.get("kind") == "director_saved_video"
        saved_images = target.get("kind") == "director_saved_images"
        images = target.get("kind") in {"director_images", "director_saved_images"}
        if saved_video:
            required = {"kind", "identity", "pipeline_id", "request_sha256", "plans_sha256",
                "state_sha256", "generated_request_sha256", "input_seal_sha256", "video_slot"}
            if "original_terminal_sha256" in target:
                required.add("original_terminal_sha256")
            slot = target.get("video_slot")
            if (set(target) != required or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", target["pipeline_id"])
                    or any(not re.fullmatch(r"[0-9a-f]{64}", target[key]) for key in required - {
                        "kind", "identity", "pipeline_id", "video_slot"})
                    or type(slot) is not dict or set(slot) != {"kind", "variant", "index"}
                    or slot.get("kind") != "video_clip_rerun" or type(slot.get("variant")) is not int
                    or slot["variant"] < 1 or type(slot.get("index")) is not int or slot["index"] != 0
                    or plan.get("release_action") != "rerun-video"):
                raise ValueError()
        elif planning or images:
            required_target = {"kind", "identity", "pipeline_id", "request_sha256"}
            required_target |= {"plans_sha256", "image_slots", "image_settings_sha256"} if images else {"llm_selection_sha256"}
            if saved_images:
                required_target.add("state_sha256")
            hash_keys = ("request_sha256", "plans_sha256", "image_settings_sha256") if images else (
                "request_sha256", "llm_selection_sha256")
            if (set(target) != required_target
                    or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", target["pipeline_id"])
                    or any(not re.fullmatch(r"[0-9a-f]{64}", target[key])
                        for key in hash_keys)
                    or saved_images and (not re.fullmatch(r"[0-9a-f]{64}", target["state_sha256"])
                        or type(target["image_slots"]) is not list
                        or len(target["image_slots"]) != 2
                        or any(type(slot) is not dict for slot in target["image_slots"])
                        or type(target["image_slots"][0].get("variant")) is not int
                        or target["image_slots"][0]["variant"] < 1
                        or target["image_slots"] != [
                            {"kind": "image_anchor", "variant": target["image_slots"][0]["variant"], "index": 0},
                            {"kind": "image_start", "variant": target["image_slots"][0]["variant"], "index": 0}])
                    or images and not saved_images and target["image_slots"] != [
                        {"kind": "image_anchor", "variant": 0, "index": 0},
                        {"kind": "image_start", "variant": 0, "index": 0}]
                    or plan.get("release_action") != ("rerun-image" if saved_images else "continue")):
                raise ValueError()
        elif (target.get("kind", "generation") != "generation"
                or type(target["execution_attempt"]) is not int or target["execution_attempt"] < 1
                or type(target["recovery_attempt"]) is not int or target["recovery_attempt"] < 0
                or ("running_execution_attempt" in target and (
                    type(target["running_execution_attempt"]) is not int
                    or target["running_execution_attempt"] != target["execution_attempt"] + 1))
                or plan.get("release_action", "start-next") not in {"start-next", "resume", "recovery-retry"}
                or plan.get("release_action") == "recovery-retry" and (
                    target.get("running_execution_attempt") != target["execution_attempt"] + 1
                    or type(target.get("running_recovery_attempt")) is not int
                    or target["running_recovery_attempt"] != target["recovery_attempt"] + 1)
                or plan.get("release_action") != "recovery-retry" and "running_recovery_attempt" in target):
            raise ValueError()
        if (plan["schema"] != "maestro/native-acceptance-reservation/v1"
                or plan["workspace"] != str(root)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", target["identity"]["id"])
                or type(plan.get("release_action", "start-next")) is not str
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", plan["agent_id"])
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", plan["generation"])
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", plan["guardian_request"])
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}\.service", plan["guardian_unit"])):
            raise ValueError()
        required = {"app/services/native_acceptance_reservation.py", "app/services/job_lifecycle.py",
            "app/wgp.py", "app/services/llm_service.py", "app/launch.py"}
        if planning or images or saved_video:
            required.add("app/services/director_pipeline.py")
        if not required.issubset(plan["source_pins"]):
            raise ValueError()
        for name, source_hash in plan["source_pins"].items():
            source = root / name
            if (Path(name).is_absolute() or ".." in Path(name).parts or source.is_symlink()
                    or hashlib.sha256(source.read_bytes()).hexdigest() != source_hash):
                raise ValueError()
        return plan, path, expected
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise NativeAcceptanceReserved("Native acceptance startup plan did not verify") from error


_PLAN, _PLAN_PATH, _PLAN_HASH = _load_startup_plan()


def _stdio_record(path, value):
    # Linux acceptance records become visible only after complete durable
    # publication. RENAME_NOREPLACE preserves the once-only protocol.
    import ctypes
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        call = ctypes.CDLL(None, use_errno=True).renameat2
        call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        if call(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
            raise OSError(ctypes.get_errno(), "SDK ownership publication failed")
    finally:
        temporary.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_stdio_record(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise NativeAcceptanceReserved("Invalid SDK ownership record")
        raw = handle.read(8193)
    if len(raw) > 8192:
        raise NativeAcceptanceReserved("Oversized SDK ownership record")
    return json.loads(raw)


def prepare_stdio_ownership(args):
    """Fence a configured acceptance SDK spawn before it can leave our SID.

    An unresolved intent is deliberately retained after a spawn error or parent
    crash. The guardian must resolve/drain it before withdrawing GPU authority.
    Ordinary clients retain their exact argv and create no ownership records.
    """
    if _PLAN is None:
        return args, None
    args = tuple(args)
    if len(args) < 2 or args[0] != "-c" or type(args[1]) is not str:
        raise NativeAcceptanceReserved("Invalid native acceptance SDK bootstrap")
    root = _PLAN_PATH.parent / "stdio"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise NativeAcceptanceReserved("Invalid native acceptance SDK directory")
    entry = root / uuid.uuid4().hex
    entry.mkdir(mode=0o700)
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    intent = {"schema": "maestro/native-acceptance-stdio/v1", "id": entry.name,
        "request": _PLAN["guardian_request"], "plan_sha256": _PLAN_HASH,
        "parent_pid": os.getpid(), "parent_start_ticks": fields[19],
        "bootstrap_sha256": hashlib.sha256(args[1].encode()).hexdigest()}
    # The child records its birth before importing any MCP/application code and
    # waits for the guardian to bind its pidfd. This also covers SDK setsid→exec.
    prefix = f'''import ctypes,json,os,stat,time,uuid
from pathlib import Path
_entry = Path({str(entry)!r})
_intent = {intent!r}
_fields = Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
_child = {{**_intent, 'pid':os.getpid(), 'start_ticks':_fields[19],
    'sid':int(_fields[3]), 'uid':os.getuid(), 'exe':os.readlink('/proc/self/exe')}}
_temporary=_entry/('.child.'+uuid.uuid4().hex)
_fd=os.open(_temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(_fd,'w') as _file:
    json.dump(_child,_file,sort_keys=True); _file.write('\\n'); _file.flush(); os.fsync(_file.fileno())
_rename=ctypes.CDLL(None,use_errno=True).renameat2
_rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
_rename.restype=ctypes.c_int
if _rename(-100,os.fsencode(_temporary),-100,os.fsencode(_entry/'child.json'),1)!=0:
    raise OSError(ctypes.get_errno(),'SDK child publication failed')
_fd=os.open(_entry,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
os.fsync(_fd); os.close(_fd)
_deadline=time.monotonic()+30
while True:
    try:
        _fd=os.open(_entry/'ack.json',os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError:
        if time.monotonic()>=_deadline: raise RuntimeError('SDK ownership was not acknowledged')
        time.sleep(.05); continue
    with os.fdopen(_fd,'rb') as _file:
        _info=os.fstat(_file.fileno()); _raw=_file.read(8193)
    if (_info.st_uid!=os.getuid() or stat.S_IMODE(_info.st_mode)!=0o600
            or not stat.S_ISREG(_info.st_mode) or _info.st_nlink!=1
            or len(_raw)>8192 or json.loads(_raw)!=_child):
        raise RuntimeError('SDK ownership acknowledgment changed')
    break
'''
    owned_args = (args[0], prefix + args[1], *args[2:])
    # Keep the argv hash outside the embedded prefix to avoid a circular hash.
    # The guardian validates it against the actual process argv independently.
    intent["argv_sha256"] = digest(list(owned_args))
    _stdio_record(entry / "intent.json", intent)
    return owned_args, entry


def finish_stdio_ownership(entry):
    """Record SDK exit only after its exact child birth is independently absent."""
    if entry is None:
        return
    try:
        child = _read_stdio_record(entry / "child.json")
    except FileNotFoundError:
        return  # Pre-spawn/crash ambiguity remains unresolved.
    intent = _read_stdio_record(entry / "intent.json")
    identity = {key: value for key, value in intent.items() if key != "argv_sha256"}
    process_keys = {"pid", "start_ticks", "sid", "uid", "exe"}
    if (set(child) != set(identity) | process_keys
            or any(child[key] != value for key, value in identity.items())
            or type(child["pid"]) is not int or child["pid"] <= 1
            or child["sid"] != child["pid"] or child["uid"] != os.getuid()
            or type(child["start_ticks"]) is not str or not child["start_ticks"].isdigit()):
        raise NativeAcceptanceReserved("SDK child ownership changed")
    try:
        fields = (Path("/proc") / str(child["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        pass
    else:
        if fields[19] == child["start_ticks"] and fields[0] not in {"Z", "X"}:
            return
    _stdio_record(entry / "closed.json", child)


def _guardian_ready():
    path = _PLAN_PATH.parent / "guard-receipt.json"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            return False
        raw = handle.read(65537)
    if len(raw) > 65536:
        return False
    receipt = json.loads(raw)
    fresh_at = receipt.get("last_check", receipt.get("ready_at"))
    freshness = 2 if _PLAN["target"].get("kind") in {"director_planning", "director_images", "director_saved_images", "director_saved_video"} else 10
    if type(fresh_at) not in {int, float} or not 0 <= time.time() - fresh_at < freshness:
        return False
    if (receipt.get("ready") is not True or receipt.get("failure")
            or receipt.get("owned_runtime_stopped") or receipt.get("foreign_dispatch_fenced")
            or receipt.get("request") != _PLAN["guardian_request"]
            or receipt["admission_ack"]["plan_sha256"] != _PLAN_HASH
            or receipt["admission_ack"]["target_sha256"] != digest(_PLAN["target"])):
        return False
    pid = receipt["pid"]
    if type(pid) is not int or pid <= 1:
        return False
    process = Path("/proc") / str(pid)
    fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
    groups = [line.split(":", 2)[2] for line in (process / "cgroup").read_text().splitlines()
        if line.startswith("0::")]
    return (process.stat().st_uid == os.getuid() and fields[0] not in {"Z", "X"}
        and fields[19] == receipt["guardian_start_ticks"]
        and len(groups) == 1 and groups[0].endswith("/" + _PLAN["guardian_unit"]))


reservation = Reservation(_PLAN["target"] if _PLAN is not None else None,
    dispatch_check=_guardian_ready if _PLAN is not None else None,
    release_action=_PLAN.get("release_action", "start-next") if _PLAN is not None else "start-next")
_installed = set()


def installed(boundary):
    """Acknowledge all source-pinned boundaries from this actual server process."""
    if _PLAN is None:
        return
    if boundary not in {"queue", "model", "http"}:
        raise NativeAcceptanceReserved("Unknown native acceptance boundary")
    with reservation._lock:
        _installed.add(boundary)
        if _installed != {"queue", "model", "http"}:
            return
        fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        receipt = {"schema": "maestro/native-acceptance-ack/v1", "pid": os.getpid(),
            "start_ticks": fields[19], "session_id": int(fields[3]),
            "workspace": _PLAN["workspace"], "generation": _PLAN["generation"],
            "plan_sha256": _PLAN_HASH, "target_sha256": digest(_PLAN["target"]),
            "boundaries": sorted(_installed)}
        path = _PLAN_PATH.parent / "admission-ack.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(receipt, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
