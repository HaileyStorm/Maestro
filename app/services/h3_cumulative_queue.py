"""Private H3 queue receipts and restore after the actual WGP model load.

The server supplies authority from its authorized job and project instance.
Nothing here authorizes a request, replans shots, or concatenates media.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass

from services.h3_cumulative_dispatch import H3CumulativeDispatch
from services.h3_cumulative_latents import _validate_step
from services.h3_cumulative_recovery import (
    H3CumulativeIdentity,
    load_h3_cumulative_checkpoint,
    validate_h3_cumulative_receipt,
    verify_h3_cumulative_checkpoint,
    write_h3_cumulative_checkpoint,
)
from services.h3_native_continuation import H3NativeContinuationStep
from services.queue_recovery_runtime import QueueRecoveryRuntimeError


@dataclass(frozen=True)
class H3QueueAuthority:
    owner_id: str
    project_id: str
    chain_id: str
    job_id: str
    width: int
    height: int

    def __post_init__(self):
        self.identity("0" * 64)

    def identity(self, bundle_sha256):
        return H3CumulativeIdentity(
            self.owner_id,
            self.project_id,
            self.chain_id,
            self.job_id,
            bundle_sha256,
            self.width,
            self.height,
        )


def encode_h3_queue_receipt(receipt):
    """Expose only the exact durable receipt, using a journal-safe digest key."""
    if type(receipt) is not dict or type(receipt.get("identity")) is not dict:
        raise QueueRecoveryRuntimeError("H3 cumulative queue receipt is invalid.")
    try:
        identity = H3CumulativeIdentity(**receipt["identity"])
    except TypeError:
        raise QueueRecoveryRuntimeError(
            "H3 cumulative queue identity is invalid."
        ) from None
    validate_h3_cumulative_receipt(receipt, identity, receipt.get("dependency"))
    result = dict(receipt, identity=dict(receipt["identity"]))
    result["identity"]["bundle_sha256"] = result["identity"].pop("runtime_sha256")
    return result


def decode_h3_queue_receipt(receipt, authority, dependency):
    if not isinstance(authority, H3QueueAuthority):
        raise QueueRecoveryRuntimeError("H3 cumulative queue authority is required.")
    if type(receipt) is not dict or type(receipt.get("identity")) is not dict:
        raise QueueRecoveryRuntimeError("H3 cumulative queue receipt is invalid.")
    declared = receipt["identity"]
    if set(declared) != {
        "owner_id",
        "project_id",
        "chain_id",
        "job_id",
        "width",
        "height",
        "bundle_sha256",
    }:
        raise QueueRecoveryRuntimeError("H3 cumulative queue identity is invalid.")
    identity = authority.identity(declared["bundle_sha256"])
    raw = dict(receipt, identity=dict(declared))
    raw["identity"]["runtime_sha256"] = raw["identity"].pop("bundle_sha256")
    validate_h3_cumulative_receipt(raw, identity, dependency)
    return raw, identity


def verify_h3_queue_receipt(
    project_directory,
    receipt,
    authority,
    dependency,
    *,
    frame_count,
    published_frames,
):
    raw, identity = decode_h3_queue_receipt(receipt, authority, dependency)
    if (
        type(frame_count) is not int
        or type(published_frames) is not int
        or raw["frame_count"] != frame_count
        or raw["published_frames"] != published_frames
    ):
        raise QueueRecoveryRuntimeError("H3 cumulative queue timeline changed.")
    verify_h3_cumulative_checkpoint(project_directory, raw, identity, dependency)


class H3CumulativeQueueDispatch(H3CumulativeDispatch):
    """One authorized call, restoring AV only after WGP finalizes its load."""

    def __init__(
        self,
        *,
        frames,
        project_directory,
        authority,
        previous_receipt=None,
        previous_dependency=None,
        step=None,
    ):
        super().__init__(frames=frames)
        if not isinstance(authority, H3QueueAuthority):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative queue authority is required."
            )
        if previous_receipt is None:
            if previous_dependency is not None or step is not None:
                raise QueueRecoveryRuntimeError(
                    "H3 cumulative predecessor is incomplete."
                )
        elif (
            not isinstance(step, H3NativeContinuationStep)
            or frames != step.target_frames
        ):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative predecessor step is invalid."
            )
        # Copy and strictly validate JSON metadata; never keep a caller-owned
        # mutable receipt or a loaded model strongly alive between jobs.
        self._receipt = None
        if previous_receipt is not None:
            raw, _ = decode_h3_queue_receipt(
                previous_receipt, authority, previous_dependency
            )
            self._receipt = encode_h3_queue_receipt(raw)
        self._dependency = previous_dependency
        self._pending_step = step
        self._project = project_directory
        self._authority = authority
        self._identity = None
        self._model = None
        self._sealed = False

    def bind_loaded_model(self, model):
        if self._identity is not None or self._phase != "running":
            raise QueueRecoveryRuntimeError(
                "H3 cumulative queue dispatch was already bound."
            )
        if (
            model is None
            or getattr(model, "selected_model_type", None) != "minimax_h3"
            or getattr(model, "reference_mode", True) is not False
        ):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative queue requires loaded native FL2VA."
            )
        identity = self._authority.identity(model.verified_h3_runtime_sha256())
        if self._receipt is not None:
            raw, declared = decode_h3_queue_receipt(
                self._receipt, self._authority, self._dependency
            )
            if declared != identity:
                raise QueueRecoveryRuntimeError("H3 cumulative loaded bundle changed.")
            recovered = load_h3_cumulative_checkpoint(
                self._project, raw, identity, self._dependency
            )
            _validate_step(recovered.state, self._pending_step)
            previous = model.restore_h3_cumulative_handoff(
                recovered, expected_identity=identity
            )
            self.previous, self.step = previous, self._pending_step
        self._identity = identity
        self._model = weakref.ref(model)
        self._receipt = self._pending_step = None

    def seal_completed(self, dependency, *, abort_check=None):
        """Seal AV after successful media completion; caller seals the media unit.

        A receipt alone never marks media completed. The queue must commit the
        same dependency in its media sidecar and journal before skipping a unit.
        """
        if self._phase != "completed" or self.handoff is None or self._sealed:
            raise QueueRecoveryRuntimeError("H3 cumulative output is not sealable.")
        model = None if self._model is None else self._model()
        if (
            model is None
            or model.verified_h3_runtime_sha256() != self._identity.runtime_sha256
        ):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative loaded bundle is no longer verified."
            )
        receipt = write_h3_cumulative_checkpoint(
            self._project,
            self.handoff["state"],
            self._identity,
            dependency,
            abort_check=abort_check,
        )
        result = encode_h3_queue_receipt(receipt)
        self.handoff = self._model = None
        self._sealed = True
        return result

    def discard(self):
        super().discard()
        self._receipt = self._pending_step = self._model = None
