"""Task-local reference preparation evidence, never application authority."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable

_MAX_WINDOWS = 256
_MAX_INDEX = 1_000_000
_CONTEXT_KEYS = frozenset({
    "repeat_index", "window_index", "seed", "frames", "height", "width",
    "fps", "model_filename",
})


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid reference observation integer")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _sha256_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid reference observation digest")
    return value


def _identity(task_id, style_digest):
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", task_id):
        raise ValueError("Invalid reference observation task identity")
    _sha256_value(style_digest)


def _ordinals(value, count, sequence_type):
    if type(value) is not sequence_type or not value or len(value) > count:
        raise ValueError("Invalid reference observation ordinals")
    for ordinal in value:
        _integer(ordinal, 1, count)
    normalized = tuple(value)
    if tuple(sorted(set(normalized))) != normalized:
        raise ValueError("Invalid reference observation ordinals")
    return normalized


def _context(context, *, path_free=False):
    filename_key = "model_filename_digest" if path_free else "model_filename"
    keys = (_CONTEXT_KEYS - {"model_filename"}) | {filename_key}
    if type(context) is not dict or set(context) != keys:
        raise ValueError("Invalid reference observation context")
    normalized = {
        "repeat_index": _integer(context["repeat_index"], 0, _MAX_INDEX),
        "window_index": _integer(context["window_index"], 1, _MAX_WINDOWS),
        "seed": _integer(context["seed"], 0, (1 << 64) - 1),
        "frames": _integer(context["frames"], 1, 1_000_000),
        "height": _integer(context["height"], 1, 32768),
        "width": _integer(context["width"], 1, 32768),
    }
    fps = context["fps"]
    if type(fps) not in (int, float) or not math.isfinite(fps) or not 0 < fps <= 1000:
        raise ValueError("Invalid reference observation frame rate")
    normalized["fps"] = float(fps)
    filename = context[filename_key]
    if path_free:
        normalized[filename_key] = _sha256_value(filename)
    else:
        if (not isinstance(filename, str) or not filename or len(filename) > 4096
                or any(ord(c) < 32 for c in filename)):
            raise ValueError("Invalid reference observation model identity")
        filename_bytes = filename.encode("utf-8")
        if len(filename_bytes) > 8192:
            raise ValueError("Invalid reference observation model identity")
        normalized["model_filename_digest"] = hashlib.sha256(filename_bytes).hexdigest()
    return normalized


def validate_style_reference_receipt(value, *, style_digest=None):
    """Validate preparation evidence shape; this never proves application."""
    try:
        if type(value) is not dict or set(value) != {
                "schema_version", "task_id", "style_digest", "global_repeat_index",
                "style_ordinals", "windows"}:
            raise ValueError("Invalid receipt fields")
        _integer(value["schema_version"], 1, 1)
        _identity(value["task_id"], value["style_digest"])
        if style_digest is not None and _sha256_value(style_digest) != value["style_digest"]:
            raise ValueError("Receipt style binding changed")
        repeat = _integer(value["global_repeat_index"], 0, _MAX_INDEX)
        styles = _ordinals(value["style_ordinals"], 9, list)
        windows = value["windows"]
        if type(windows) is not list or not 1 <= len(windows) <= _MAX_WINDOWS:
            raise ValueError("Invalid receipt windows")
        normalized_rows = []
        expected_count = None
        for window, row in enumerate(windows, 1):
            if type(row) is not dict or set(row) != {
                    "context", "context_sha256", "source_image_count", "encoded_image_ordinals"}:
                raise ValueError("Invalid receipt row")
            context = _context(row["context"], path_free=True)
            if context["window_index"] != window:
                raise ValueError("Receipt windows are not contiguous")
            digest = _sha256_value(row["context_sha256"])
            if digest != _digest({"global_repeat_index": repeat, **context}):
                raise ValueError("Receipt context changed")
            count = _integer(row["source_image_count"], 1, 9)
            if expected_count is not None and count != expected_count:
                raise ValueError("Receipt source count changed")
            expected_count = count
            encoded = _ordinals(row["encoded_image_ordinals"], count, list)
            if not set(styles).issubset(encoded):
                raise ValueError("Receipt style references were not encoded")
            normalized_rows.append({
                "context": context, "context_sha256": digest,
                "source_image_count": count, "encoded_image_ordinals": list(encoded),
            })
        return {
            "schema_version": 1, "task_id": value["task_id"],
            "style_digest": value["style_digest"], "global_repeat_index": repeat,
            "style_ordinals": list(styles), "windows": normalized_rows,
        }
    except Exception:
        raise ValueError("Invalid style reference preparation receipt") from None


class H3StyleReferenceObservation:
    def __init__(self, *, task_id: str, style_digest: str, expected_image_count: int,
                 style_ordinals: tuple, repeat_index: Callable[[], int]):
        _identity(task_id, style_digest)
        _integer(expected_image_count, 1, 9)
        style_ordinals = _ordinals(style_ordinals, expected_image_count, tuple)
        if not callable(repeat_index):
            raise ValueError("Invalid reference observation binding")
        self._task_id = task_id
        self._style_digest = style_digest
        self._expected_image_count = expected_image_count
        self._style_ordinals = style_ordinals
        self._repeat_index = repeat_index
        self._global_repeat = None
        self._highest_repeat = -1
        self._latest_window = 0
        self._active = None
        self._attempt_window = None
        self._windows = {}

    def begin(self, context):
        self._active = None
        self._attempt_window = None
        try:
            global_repeat = _integer(self._repeat_index(), 0, _MAX_INDEX)
            if global_repeat < self._highest_repeat:
                raise ValueError("Reference observation repeat moved backwards")
            self._highest_repeat = global_repeat
            normalized = _context(context)
            if global_repeat != self._global_repeat:
                self._windows.clear()
                self._latest_window = 0
            self._global_repeat = global_repeat
            window = normalized["window_index"]
            self._latest_window = max(window, self._latest_window)
            # A retry replaces its window and any later observations derived
            # from the previous attempt, while retaining completed predecessors.
            self._windows = {i: row for i, row in self._windows.items() if i < window}
            self._windows[window] = None
            self._active = normalized
            self._attempt_window = window
        except Exception:
            self.fail("reference_observer_context_failed")
            raise ValueError("Invalid reference observation context") from None

    def fail(self, code):
        try:
            if self._attempt_window is None:
                self._windows.clear()
            else:
                window = self._attempt_window
                self._windows = {i: row for i, row in self._windows.items() if i < window}
                self._windows[window] = None
            self._active = None
        except Exception:
            self._active = None
            self._windows = {}

    def __call__(self, payload):
        try:
            current = _integer(self._repeat_index(), 0, _MAX_INDEX)
            self._highest_repeat = max(current, self._highest_repeat)
            if (self._active is None or current != self._global_repeat
                    or type(payload) is not dict
                    or set(payload) != {"source_image_count", "encoded_image_ordinals"}):
                raise ValueError("Invalid reference preparation observation")
            count = _integer(payload["source_image_count"], 1, 9)
            ordinals = _ordinals(payload["encoded_image_ordinals"], count, tuple)
            if count != self._expected_image_count or not set(self._style_ordinals).issubset(ordinals):
                raise ValueError("Incomplete style reference encoding")
            window = self._active["window_index"]
            if self._windows.get(window) is not None:
                raise ValueError("Duplicate reference preparation observation")
            self._windows[window] = {
                "context": dict(self._active),
                "context_sha256": _digest({"global_repeat_index": self._global_repeat,
                                           **self._active}),
                "source_image_count": count,
                "encoded_image_ordinals": list(ordinals),
            }
        except Exception:
            self.fail("reference_observer_payload_failed")
            raise ValueError("Invalid reference preparation observation") from None

    def receipt(self, global_repeat_index):
        try:
            _integer(global_repeat_index, 0, _MAX_INDEX)
            current = _integer(self._repeat_index(), 0, _MAX_INDEX)
            self._highest_repeat = max(current, self._highest_repeat)
            if current != self._global_repeat:
                self.fail("reference_observer_repeat_changed")
                return None
            if self._active is None or current != global_repeat_index:
                return None
            latest = self._latest_window
            if len(self._windows) != latest or any(self._windows.get(i) is None for i in range(1, latest + 1)):
                return None
            return validate_style_reference_receipt({
                "schema_version": 1,
                "task_id": self._task_id,
                "style_digest": self._style_digest,
                "global_repeat_index": current,
                "style_ordinals": list(self._style_ordinals),
                "windows": [self._windows[i] for i in range(1, latest + 1)],
            })
        except Exception:
            self.fail("reference_observer_receipt_failed")
            return None


def build_style_delivery_fact(snapshot, *, model_type, skip_reason=None, encoding_receipt=None):
    """Project conditioning evidence for a caller-verified successful producer.

    Encoding alone is not producer or application authority. The caller verifies
    completed media and native conditioning before publishing an applied fact.
    """
    from services.custom_visual_styles import validate_snapshot
    from services.h3_custom_style_handoff import ADAPTER_ID

    try:
        if not isinstance(model_type, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", model_type):
            raise ValueError("Invalid actual style model")
        if skip_reason is not None and skip_reason not in ("segment_reference_drop", "checkpoint_unsupported"):
            raise ValueError("Invalid style skip reason")
        if skip_reason is not None and encoding_receipt is not None:
            raise ValueError("Style encoding contradicts skipped conditioning")
        body = validate_snapshot(snapshot)
        if (set(body) != {"version", "phase", "style", "model_type", "mode", "adapter_id",
                         "references", "application"}
                or type(body["version"]) is not int or body["version"] != 1 or body["phase"] != "planned"):
            raise ValueError("Planned style snapshot is required")
        style_digest = _sha256_value(snapshot["digest"])
        references = body["references"]
        selections = body["style"]["definition"]["references"]
        if len(references) != len(selections):
            raise ValueError("Style reference selection count changed")
        planned = []
        rows = []
        for index, (reference, selection) in enumerate(zip(references, selections)):
            if (set(reference) != {"selection", "sha256", "terms", "access_policy", "delivery", "reason"}
                    or reference["selection"] != selection or reference["sha256"] != selection["expected_sha256"]
                    or reference["delivery"] not in ("planned", "skipped")):
                raise ValueError("Invalid frozen style reference")
            policy = reference["access_policy"]
            if (type(policy) is not dict or policy.get("authorized") is not True
                    or policy.get("project_id") != body["style"]["project_id"]
                    or policy.get("workspace_id") != body["style"]["workspace_id"]):
                raise ValueError("Frozen style reference scope changed")
            sha256 = _sha256_value(reference["sha256"])
            if reference["delivery"] == "planned":
                if reference["reason"] != "":
                    raise ValueError("Invalid planned style reference reason")
                planned.append(index)
            elif reference["reason"] not in ("reference_limit", "borrow_scope_unsupported", "mode_unsupported"):
                raise ValueError("Invalid historical style skip reason")
            rows.append({"index": index, "sha256": sha256, "delivery": reference["delivery"],
                         "reason": reference["reason"], "image_ordinal": None})
        if body["application"] != ("references_planned" if planned else "text_only"):
            raise ValueError("Frozen style application changed")
        receipt = None
        if not planned:
            if encoding_receipt is not None:
                raise ValueError("No style reference encoding was planned")
            application = "text_only"
        else:
            if (body["model_type"] != "minimax_h3_ref2va" or body["mode"] != "ref2va"
                    or body["adapter_id"] != ADAPTER_ID):
                raise ValueError("Frozen style adapter binding changed")
            if skip_reason is not None:
                application = "text_only"
                for index in planned:
                    rows[index].update(delivery="skipped", reason=skip_reason)
            else:
                if model_type != "minimax_h3_ref2va":
                    raise ValueError("Actual checkpoint does not support planned style conditioning")
                if encoding_receipt is None:
                    application = "reference_use_unverified"
                    for index in planned:
                        rows[index].update(delivery="unverified", reason="reference_use_unverified")
                else:
                    receipt = validate_style_reference_receipt(encoding_receipt, style_digest=style_digest)
                    if len(receipt["style_ordinals"]) != len(planned):
                        raise ValueError("Style encoding reference count changed")
                    application = "references_applied"
                    for index, ordinal in zip(planned, receipt["style_ordinals"]):
                        rows[index].update(delivery="applied", reason="", image_ordinal=ordinal)
        return {"version": 1, "style_digest": style_digest, "model_type": model_type,
                "application": application, "references": rows,
                "encoding_receipt_sha256": _digest(receipt) if receipt is not None else None,
                "confirmed_windows": len(receipt["windows"]) if receipt is not None else 0}
    except Exception:
        raise ValueError("Invalid style delivery projection") from None
