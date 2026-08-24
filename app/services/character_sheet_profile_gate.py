"""Private, content-free readiness gate for Character Sheet profiles.

This resolver consumes only server-resolved component descriptors.  It does
not accept a client attestation, inspect creative content, or change the public
v1 workflow/capability catalog.  Its result is a non-authoritative readiness
snapshot: a future launch adapter must resolve the server stores again
immediately before execution.  Returned commitments bind the private facts
without exposing identifiers, revisions, evidence records, or policy times.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from services.krea_owner_policy import (
    KREA_POLICY_SERVICE_KEY,
    KREA_ROLE_USE_SCOPES,
    krea_owner_policy_status,
)
from services.character_sheet_workflow import PROFILE_DEFINITIONS
from services.model_terms import model_terms_manifest_valid, model_terms_statuses


PROFILE_GATE_SCHEMA_VERSION = 2
PROFILE_GATE_RESOLVER = "character-sheet-profile-gate-v2"
_COMPONENT_KEYS = frozenset({"base_model", "lora", "project", "vlm", "editor"})
_MODEL_KEYS = frozenset({
    "source", "model_type", "revision", "artifact_ready", "authorization_ready",
})
_PROJECT_KEYS = frozenset({"source", "ready", "revision", "evidence_commitment"})
_LOCAL_KEYS = _PROJECT_KEYS | {"local"}
_KREA_PROFILES = frozenset({"quad_krea2", "dynamic_krea2_experimental"})
_MODEL_ROLES = frozenset({"base_model", "lora"})
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class CharacterSheetProfileGateError(ValueError):
    """Raised when private server gate input is not exact."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CharacterSheetProfileGateError(
            "Character Sheet profile gate input is not canonical JSON."
        ) from exc


def _commit(domain: str, value: Any) -> str:
    digest = hashlib.sha256(
        domain.encode("ascii") + b"\0" + _canonical_json(value)
    ).hexdigest()
    return f"sha256:{digest}"


def _require_exact_dict(value: Any, keys: frozenset[str], *, name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CharacterSheetProfileGateError(f"{name} has an invalid schema.")
    return value


def _require_token(value: Any, *, name: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise CharacterSheetProfileGateError(f"{name} is invalid.")
    return value


def _require_digest(value: Any, *, name: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise CharacterSheetProfileGateError(f"{name} is invalid.")
    return value


def _require_bool(value: Any, *, name: str) -> bool:
    if type(value) is not bool:
        raise CharacterSheetProfileGateError(f"{name} must be a boolean.")
    return value


def _require_server_source(value: Any, *, name: str) -> None:
    if type(value) is not str or value != "server_resolved":
        raise CharacterSheetProfileGateError(
            f"{name} must come from the current server resolver."
        )


def _model_gate(
    services: Mapping[str, object],
    descriptor: dict[str, Any],
    model_defs: Mapping[str, Mapping[str, Any]],
    *,
    label: str,
    profile_id: str,
) -> dict[str, object]:
    _require_exact_dict(descriptor, _MODEL_KEYS, name=label)
    _require_server_source(descriptor["source"], name=f"{label}.source")
    model_type = _require_token(descriptor["model_type"], name=f"{label}.model_type")
    revision = _require_token(descriptor["revision"], name=f"{label}.revision")
    artifact_ready = _require_bool(
        descriptor["artifact_ready"], name=f"{label}.artifact_ready"
    )
    authorization_ready = _require_bool(
        descriptor["authorization_ready"], name=f"{label}.authorization_ready"
    )
    model_definition = model_defs.get(model_type)
    if type(model_definition) is not dict:
        raise CharacterSheetProfileGateError(
            f"{label} is not a registered server model."
        )
    bindings = model_definition.get("character_sheet_profile_bindings")
    if (
        type(bindings) is not dict
        or not bindings
        or any(
            type(bound_profile) is not str
            or bound_profile not in PROFILE_DEFINITIONS
            or type(role) is not str
            or role not in _MODEL_ROLES
            for bound_profile, role in bindings.items()
        )
        or bindings.get(profile_id) != label
    ):
        raise CharacterSheetProfileGateError(
            f"{label} is not bound to the selected Character Sheet profile."
        )
    if not model_terms_manifest_valid(model_type, model_defs):
        raise CharacterSheetProfileGateError(
            f"{label} creator terms manifest is invalid."
        )
    raw_terms = model_terms_statuses(services, model_type, model_defs)
    if type(raw_terms) is not list:
        raise CharacterSheetProfileGateError(f"{label} terms status is invalid.")
    terms: list[dict[str, object]] = []
    for raw in raw_terms:
        if type(raw) is not dict:
            raise CharacterSheetProfileGateError(f"{label} terms status is invalid.")
        term = raw.get("term")
        version = raw.get("version")
        accepted = raw.get("accepted")
        if (
            type(term) is not str
            or type(version) not in (int, str)
            or type(accepted) is not bool
        ):
            raise CharacterSheetProfileGateError(f"{label} terms status is invalid.")
        terms.append({"term": term, "version": version, "accepted": accepted})
    terms_ready = all(item["accepted"] is True for item in terms)
    terms_commitment = _commit(
        f"{PROFILE_GATE_RESOLVER}:{label}:terms",
        {"model_type": model_type, "statuses": terms},
    )
    artifact_commitment = _commit(
        f"{PROFILE_GATE_RESOLVER}:{label}:artifact",
        {
            "model_type": model_type,
            "revision": revision,
            "artifact_ready": artifact_ready,
        },
    )
    authorization_commitment = _commit(
        f"{PROFILE_GATE_RESOLVER}:{label}:authorization",
        {
            "model_type": model_type,
            "revision": revision,
            "authorization_ready": authorization_ready,
        },
    )
    gate = {
        "terms_ready": terms_ready,
        "artifact_ready": artifact_ready,
        "authorization_ready": authorization_ready,
        "terms_commitment": terms_commitment,
        "artifact_commitment": artifact_commitment,
        "authorization_commitment": authorization_commitment,
    }
    gate["commitment"] = _commit(
        f"{PROFILE_GATE_RESOLVER}:{label}:gate", gate
    )
    return gate


def _readiness_gate(
    descriptor: dict[str, Any],
    *,
    label: str,
    require_local: bool,
) -> dict[str, object]:
    keys = _LOCAL_KEYS if require_local else _PROJECT_KEYS
    _require_exact_dict(descriptor, keys, name=label)
    _require_server_source(descriptor["source"], name=f"{label}.source")
    ready = _require_bool(descriptor["ready"], name=f"{label}.ready")
    revision = _require_token(descriptor["revision"], name=f"{label}.revision")
    evidence = _require_digest(
        descriptor["evidence_commitment"], name=f"{label}.evidence_commitment"
    )
    local = True
    if require_local:
        local = _require_bool(descriptor["local"], name=f"{label}.local")
    gate = {
        "ready": ready,
        **({"local": local} if require_local else {}),
    }
    gate["commitment"] = _commit(
        f"{PROFILE_GATE_RESOLVER}:{label}:gate",
        {"ready": ready, "local": local, "revision": revision, "evidence": evidence},
    )
    return gate


def _owner_gate(services: Mapping[str, object], *, applicable: bool) -> dict[str, object]:
    if not applicable:
        gate = {
            "applicable": False,
            "status": "not_applicable",
            "ready": True,
            "attested": False,
            "local_execution_allowed": True,
            "role_scopes_valid": True,
        }
        gate["commitment"] = _commit(
            f"{PROFILE_GATE_RESOLVER}:owner:gate", gate
        )
        return gate

    status = krea_owner_policy_status(services)
    if type(status) is not dict:
        raise CharacterSheetProfileGateError("Krea owner policy status is invalid.")
    attested = status.get("attested") is True
    local_allowed = status.get("local_execution_allowed") is True
    role_scopes = status.get("role_use_scopes")
    role_scopes_valid = (
        type(role_scopes) is dict
        and role_scopes == KREA_ROLE_USE_SCOPES
        and set(role_scopes) == set(KREA_ROLE_USE_SCOPES)
    )
    ready = attested and local_allowed and role_scopes_valid
    owner_record = services.get(KREA_POLICY_SERVICE_KEY)
    try:
        record_commitment = _commit(
            f"{PROFILE_GATE_RESOLVER}:owner:record", owner_record
        )
    except CharacterSheetProfileGateError:
        record_commitment = _commit(
            f"{PROFILE_GATE_RESOLVER}:owner:record-invalid",
            {"present": owner_record is not None},
        )
    gate = {
        "applicable": True,
        "status": (
            "license_conditions_recorded" if ready else "owner_attestation_required"
        ),
        "ready": ready,
        "attested": attested,
        "local_execution_allowed": local_allowed,
        "role_scopes_valid": role_scopes_valid,
        "commitment": record_commitment,
    }
    return gate


def resolve_character_sheet_profile_gate(
    services: dict[str, object],
    *,
    profile: str | None = None,
    components: dict[str, object],
    model_defs: dict[str, Mapping[str, Any]],
) -> dict[str, object]:
    """Return a non-authoritative snapshot for later server-side re-resolution.

    A launch adapter must resolve every server store again immediately before
    execution; this function never grants or represents execution authority.
    """
    if type(services) is not dict:
        raise CharacterSheetProfileGateError("services must be an exact dictionary.")
    if type(model_defs) is not dict or not all(type(key) is str for key in model_defs):
        raise CharacterSheetProfileGateError("model_defs must be an exact dictionary.")
    _require_exact_dict(components, _COMPONENT_KEYS, name="components")
    defaults = [
        profile_id
        for profile_id, definition in PROFILE_DEFINITIONS.items()
        if definition.get("default") is True
    ]
    if len(defaults) != 1:
        raise CharacterSheetProfileGateError(
            "Character Sheet profile defaults are invalid."
        )
    if profile is None:
        profile_id = defaults[0]
        selection = "default"
    else:
        if type(profile) is not str:
            raise CharacterSheetProfileGateError("profile is invalid.")
        profile_id = profile
        selection = "explicit"
    definition = PROFILE_DEFINITIONS.get(profile_id)
    if definition is None:
        raise CharacterSheetProfileGateError("Character Sheet profile is unknown.")
    if (
        type(definition.get("experimental")) is not bool
        or type(definition.get("default")) is not bool
        or type(definition.get("status")) is not str
        or type(definition.get("requires_explicit_selection")) is not bool
    ):
        raise CharacterSheetProfileGateError(
            "Character Sheet profile definition is invalid."
        )
    if selection == "default" and definition["default"] is not True:
        raise CharacterSheetProfileGateError("Character Sheet default profile drifted.")
    if definition["requires_explicit_selection"] is True and selection != "explicit":
        raise CharacterSheetProfileGateError(
            "Character Sheet profile requires explicit selection."
        )

    base_gate = _model_gate(
        services,
        components["base_model"],
        model_defs,
        label="base_model",
        profile_id=profile_id,
    )
    lora_gate = _model_gate(
        services,
        components["lora"],
        model_defs,
        label="lora",
        profile_id=profile_id,
    )
    project_gate = _readiness_gate(
        components["project"], label="project", require_local=False
    )
    vlm_gate = _readiness_gate(
        components["vlm"], label="vlm", require_local=True
    )
    editor_gate = _readiness_gate(
        components["editor"], label="editor", require_local=True
    )
    owner_gate = _owner_gate(services, applicable=profile_id in _KREA_PROFILES)
    gates = {
        "base_model": base_gate,
        "lora": lora_gate,
        "owner": owner_gate,
        "project": project_gate,
        "vlm": vlm_gate,
        "editor": editor_gate,
    }

    reasons: list[str] = []
    if definition["status"] == "later_unavailable":
        reasons.append("profile_later_unavailable")
    else:
        for label, gate in (("base_model", base_gate), ("lora", lora_gate)):
            if gate["terms_ready"] is not True:
                reasons.append(f"{label}_terms_required")
            if gate["artifact_ready"] is not True:
                reasons.append(f"{label}_artifact_unavailable")
            if gate["authorization_ready"] is not True:
                reasons.append(f"{label}_authorization_required")
        if owner_gate["ready"] is not True:
            reasons.append("krea_owner_attestation_required")
        if project_gate["ready"] is not True:
            reasons.append("project_not_ready")
        if vlm_gate["ready"] is not True:
            reasons.append("vlm_not_ready")
        if vlm_gate["local"] is not True:
            reasons.append("vlm_must_be_local")
        if editor_gate["ready"] is not True:
            reasons.append("editor_not_ready")
        if editor_gate["local"] is not True:
            reasons.append("editor_must_be_local")

    status = (
        "later_unavailable"
        if definition["status"] == "later_unavailable"
        else ("ready_snapshot" if not reasons else "blocked")
    )
    decision: dict[str, object] = {
        "schema_version": PROFILE_GATE_SCHEMA_VERSION,
        "resolver": PROFILE_GATE_RESOLVER,
        "profile": profile_id,
        "profile_status": definition["status"],
        "selection": selection,
        "experimental": definition["experimental"],
        "status": status,
        "execution_authority": False,
        "reasons": reasons,
        "gates": gates,
    }
    decision["profile_commitment"] = _commit(
        f"{PROFILE_GATE_RESOLVER}:profile", decision
    )
    return decision


__all__ = [
    "CharacterSheetProfileGateError",
    "PROFILE_GATE_RESOLVER",
    "PROFILE_GATE_SCHEMA_VERSION",
    "resolve_character_sheet_profile_gate",
]
