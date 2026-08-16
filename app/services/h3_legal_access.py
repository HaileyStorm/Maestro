"""Server-owned MiniMax H3 legal availability and execution policy.

The upstream MiniMax H3 license grants rights only inside its defined
Applicable Territory.  That definition excludes the United States, where
this Maestro installation is configured.  A generic model-terms acceptance
is provenance only and cannot replace a separate written MiniMax license.

There is deliberately no runtime, environment, client, or terms-acceptance
override in this milestone.  Future enablement requires a separately reviewed
durable authorization record bound to the host, territory, exact upstream
license, authorized scope, evidence, and expiry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


H3_UPSTREAM_REPOSITORY = "MiniMaxAI/MiniMax-H3"
H3_UPSTREAM_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"
H3_LICENSE_SHA256 = (
    "59b99642b95ea21630e311198ddbfffbfe05aadba0c2f5d884cbdf4efcc90f44"
)
H3_LICENSE_URL = (
    "https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/"
    f"{H3_UPSTREAM_REVISION}/LICENSE"
)
H3_LICENSE_DATE = "2026-08-02"

# Source-owned host configuration.  Do not replace this with an environment
# or request value: unknown/default and excluded territories must fail closed.
H3_DEPLOYMENT_TERRITORY = "US"

H3_REGISTERED_MODEL_TYPES = frozenset({
    "minimax_h3",
    "minimax_h3_pinkcherry_fl2va",
    "minimax_h3_w4a8_fl2va",
    "minimax_h3_ref2va",
})
H3_REGISTERED_ARCHITECTURES = frozenset({
    "minimax_h3",
    "minimax_h3_ref2va",
})

H3_LEGAL_BLOCKED_STATUS = "legal_blocked"
H3_LEGAL_BLOCKED_DETAIL = (
    "MiniMax H3 cannot run on this Maestro installation because the current "
    "upstream license excludes the United States. Accepting model terms does "
    "not grant access. A separate written MiniMax license is required."
)


class H3LegalAccessError(RuntimeError):
    """Raised before work when a registered H3-family model cannot execute."""


@dataclass(frozen=True)
class H3LegalAccessDecision:
    availability_status: str
    execution_allowed: bool
    detail: str

    def public_projection(self) -> dict[str, object]:
        """Return the bounded, content-free catalog/API projection."""
        return {
            "availability_status": self.availability_status,
            "execution_allowed": self.execution_allowed,
        }


_BLOCKED_DECISION = H3LegalAccessDecision(
    availability_status=H3_LEGAL_BLOCKED_STATUS,
    execution_allowed=False,
    detail=H3_LEGAL_BLOCKED_DETAIL,
)


def is_registered_h3_family(
    model_type: object,
    *,
    model_def: Mapping[str, object] | None = None,
    architecture: object = None,
) -> bool:
    """Classify H3 by registered server identity, never names or file paths."""
    registered_id = str(model_type or "").strip()
    registered_architecture = str(architecture or "").strip()
    if registered_id in H3_REGISTERED_MODEL_TYPES:
        return True
    if registered_architecture in H3_REGISTERED_ARCHITECTURES:
        return True
    if not isinstance(model_def, Mapping):
        return False
    return str(model_def.get("architecture") or "").strip() in (
        H3_REGISTERED_ARCHITECTURES
    )


def h3_legal_access_decision(
    model_type: object,
    *,
    model_def: Mapping[str, object] | None = None,
    architecture: object = None,
) -> H3LegalAccessDecision | None:
    """Return the current decision for one registered model, if it is H3."""
    if not is_registered_h3_family(
        model_type, model_def=model_def, architecture=architecture,
    ):
        return None
    # The only current source-owned host configuration is US and there is no
    # written-authorization record.  Keep this unconditional until that
    # separately reviewed record and its validation path exist.
    return _BLOCKED_DECISION


def h3_public_availability(
    model_type: object,
    *,
    model_def: Mapping[str, object] | None = None,
    architecture: object = None,
) -> dict[str, object]:
    decision = h3_legal_access_decision(
        model_type, model_def=model_def, architecture=architecture,
    )
    return decision.public_projection() if decision is not None else {}


def require_h3_execution_allowed(
    model_types: Iterable[object],
    *,
    model_defs: Mapping[str, Mapping[str, object]] | None = None,
    architectures: Mapping[str, object] | None = None,
) -> None:
    """Reject any registered H3 family member before executable work."""
    definitions = model_defs if isinstance(model_defs, Mapping) else {}
    resolved_architectures = (
        architectures if isinstance(architectures, Mapping) else {}
    )
    for value in model_types:
        model_type = str(value or "").strip()
        model_def = definitions.get(model_type)
        decision = h3_legal_access_decision(
            model_type,
            model_def=model_def if isinstance(model_def, Mapping) else None,
            architecture=resolved_architectures.get(model_type),
        )
        if decision is not None and not decision.execution_allowed:
            raise H3LegalAccessError(decision.detail)
