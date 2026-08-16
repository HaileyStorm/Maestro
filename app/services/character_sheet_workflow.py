"""Pure Character Sheet workflow planning, sealing, and repair lineage.

This module is deliberately source-only.  It does not discover models, accept
terms, read project media, call a VLM, run an editor, or dispatch generation.
Callers supply server-resolved identifiers and artifact digests; this contract
binds those facts into a deterministic project-scoped plan.  The canonical
SHA-256 seals detect drift; they are not signatures.  Execution boundaries
must call :func:`assert_character_sheet_execution_authorized` with an
independently held plan seal and authorization-evidence digest.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import hmac
import json
import re
from types import MappingProxyType
from typing import Any


CONTRACT_SCHEMA_VERSION = 1
PLANNER_VERSION = "character-sheet-workflow-v1"
DEFAULT_PROFILE_ID = "quad_flux2_klein"
QWEN_IMAGE_EDIT_OPERATION = "qwen_image_edit"
AUTHORIZATION_MAX_TTL_SECONDS = 900

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_COORDINATE = 1_000_000

_QUAD_ROLES = (
    "identity_front",
    "three_quarter",
    "profile",
    "back",
)
_DYNAMIC_ROLES = (
    "hero",
    "turnaround",
    "action",
    "silhouette",
    "expression_state",
    "detail",
    "metadata",
)
_TRIPLE_ROLES = ("identity_front", "profile", "back")


PROFILE_DEFINITIONS: Mapping[str, Mapping[str, Any]] = MappingProxyType({
    "quad_flux2_klein": MappingProxyType({
        "label": "Quad — FLUX.2 Klein",
        "panel_roles": _QUAD_ROLES,
        "available": False,
        "executable": False,
        "experimental": False,
        "default": True,
        "requires_explicit_selection": False,
        "status": "requires_server_authorization",
    }),
    "quad_krea2": MappingProxyType({
        "label": "Quad — Krea 2",
        "panel_roles": _QUAD_ROLES,
        "available": False,
        "executable": False,
        "experimental": False,
        "default": False,
        "requires_explicit_selection": True,
        "status": "legal_blocked",
    }),
    "dynamic_krea2_experimental": MappingProxyType({
        "label": "Dynamic — Krea 2 (experimental)",
        "panel_roles": _DYNAMIC_ROLES,
        "available": False,
        "executable": False,
        "experimental": True,
        "default": False,
        "requires_explicit_selection": True,
        "status": "legal_blocked",
    }),
    "triple_flux2_klein": MappingProxyType({
        "label": "Triple — FLUX.2 Klein",
        "panel_roles": _TRIPLE_ROLES,
        "available": False,
        "executable": False,
        "experimental": False,
        "default": False,
        "requires_explicit_selection": True,
        "status": "later_unavailable",
    }),
})

_ANCHOR_KEYS = frozenset({
    "schema_version", "project_id", "anchor_id", "kind", "sha256",
})
_PROJECT_KEYS = frozenset({"project_id"})
_RESOURCE_KEYS = frozenset({
    "profile_id", "base_model", "lora", "schedule", "terms", "planner",
    "reviewer", "editor", "execution_authorization",
})
_RESOURCE_BASE_KEYS = _RESOURCE_KEYS - {"execution_authorization"}
_REVISION_RESOURCE_KEYS = frozenset({"id", "revision"})
_LOCAL_RESOURCE_KEYS = frozenset({"id", "revision", "local"})
_EDITOR_KEYS = frozenset({"id", "revision", "local", "operation"})
_TERMS_KEYS = frozenset({"id", "revision", "acceptance_digest"})
_AUTHORIZATION_KEYS = frozenset({
    "status", "revision", "evidence_digest", "nonce", "issued_at_unix",
    "expires_at_unix", "project_id", "profile_id", "anchor_commitment",
    "resource_manifest_commitment", "terms_commitment", "seed_commitment",
})
_PANEL_KEYS = frozenset({"role", "x", "y", "width", "height", "sha256"})
_ROOT_KEYS = frozenset({
    "schema_version", "planner_version", "profile", "project_scope",
    "anchor", "resources", "seed", "panels", "repair_lineage",
    "commitments", "provenance", "parent_plan_seal", "plan_seal",
    "authorization_checked_at_unix",
})
_COMMITMENT_KEYS = frozenset({
    "anchor", "seed", "base_model", "lora", "schedule", "terms",
    "planner", "reviewer", "editor", "execution_authorization",
    "initial_panels", "panels", "repair_lineage",
})
_PROVENANCE_KEYS = frozenset({
    "service", "version", "generation_sequence", "planning_locality",
    "review_locality", "repair_operation", "resource_manifest_commitment",
    "anchor_commitment", "panel_set_commitment", "lineage_commitment",
})
_REPAIR_KEYS = frozenset({
    "attempt", "operation", "failed_roles", "before_panel_commitments",
    "after_panel_commitments", "preserved_panel_commitments",
    "before_panels", "after_panels", "predecessor_plan_seal",
    "anchor_commitment", "editor_commitment", "event_seal",
})
_ROLE_COMMITMENT_KEYS = frozenset({"role", "commitment"})


class CharacterSheetWorkflowError(ValueError):
    """Raised when a Character Sheet contract is incomplete or changed."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CharacterSheetWorkflowError(
            "Character Sheet contract is not canonical JSON."
        ) from exc


def _require_plain_json(value: Any, *, name: str) -> None:
    """Reject equality gadgets and non-JSON containers before comparisons."""
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is list:
        for item in value:
            _require_plain_json(item, name=name)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _require_plain_json(item, name=name)
        return
    raise CharacterSheetWorkflowError(f"{name} must contain exact plain JSON values.")


def _seal(domain: str, value: Any) -> str:
    if type(domain) is not str or not domain:
        raise CharacterSheetWorkflowError("Seal domain is invalid.")
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + _canonical_json(value)
    ).hexdigest()


def _identifier(value: Any, *, name: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise CharacterSheetWorkflowError(f"{name} is invalid.")
    return value


def _digest(value: Any, *, name: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise CharacterSheetWorkflowError(f"{name} must be a lowercase SHA-256 digest.")
    return value


def _integer(value: Any, *, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CharacterSheetWorkflowError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )
    return value


def character_sheet_profile_catalog() -> tuple[dict[str, Any], ...]:
    """Return the ordered, content-free profile capability catalog."""
    return tuple({
        "id": profile_id,
        "label": definition["label"],
        "panel_roles": list(definition["panel_roles"]),
        "available": definition["available"],
        "executable": definition["executable"],
        "experimental": definition["experimental"],
        "default": definition["default"],
        "requires_explicit_selection": definition[
            "requires_explicit_selection"
        ],
        "status": definition["status"],
    } for profile_id, definition in PROFILE_DEFINITIONS.items())


def normalize_character_sheet_profile(
    value: Any = None,
    *,
    available_profile_ids: Any = (),
    require_available: bool = True,
) -> str:
    """Resolve only an omitted selection to the sole conservative default."""
    if type(require_available) is not bool:
        raise CharacterSheetWorkflowError("require_available must be a boolean.")
    profile_id = DEFAULT_PROFILE_ID if value is None else value
    if type(profile_id) is not str or profile_id not in PROFILE_DEFINITIONS:
        raise CharacterSheetWorkflowError("Character Sheet profile is unknown.")
    if (
        not isinstance(available_profile_ids, Sequence)
        or isinstance(available_profile_ids, (str, bytes))
        or any(type(item) is not str for item in available_profile_ids)
    ):
        raise CharacterSheetWorkflowError("Available profile identities are invalid.")
    if require_available and profile_id not in available_profile_ids:
        raise CharacterSheetWorkflowError("Character Sheet profile is not yet available.")
    return profile_id


def _normalize_anchor(value: Any, *, project_id: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _ANCHOR_KEYS:
        raise CharacterSheetWorkflowError("Character Sheet anchor schema is invalid.")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != CONTRACT_SCHEMA_VERSION
    ):
        raise CharacterSheetWorkflowError("Character Sheet anchor version is invalid.")
    anchor_project = _identifier(value["project_id"], name="anchor.project_id")
    if anchor_project != project_id:
        raise CharacterSheetWorkflowError("Character Sheet anchor project does not match.")
    kind = value["kind"]
    if type(kind) is not str or kind not in {"generated", "imported"}:
        raise CharacterSheetWorkflowError("Character Sheet anchor kind is invalid.")
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "project_id": anchor_project,
        "anchor_id": _identifier(value["anchor_id"], name="anchor.anchor_id"),
        "kind": kind,
        "sha256": _digest(value["sha256"], name="anchor.sha256"),
    }


def _revision_resource(value: Any, *, name: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != _REVISION_RESOURCE_KEYS:
        raise CharacterSheetWorkflowError(f"{name} resource schema is invalid.")
    return {
        "id": _identifier(value["id"], name=f"{name}.id"),
        "revision": _identifier(value["revision"], name=f"{name}.revision"),
    }


def _local_resource(value: Any, *, name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _LOCAL_RESOURCE_KEYS:
        raise CharacterSheetWorkflowError(f"{name} resource schema is invalid.")
    if value["local"] is not True:
        raise CharacterSheetWorkflowError(f"{name} must be a local resource.")
    return {
        **_revision_resource(
            {"id": value["id"], "revision": value["revision"]}, name=name,
        ),
        "local": True,
    }


def _normalize_resource_base(value: Any, *, profile_id: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _RESOURCE_BASE_KEYS:
        raise CharacterSheetWorkflowError("Character Sheet resource base schema is invalid.")
    if type(value["profile_id"]) is not str or value["profile_id"] != profile_id:
        raise CharacterSheetWorkflowError("Character Sheet resource profile does not match.")
    terms = value["terms"]
    if type(terms) is not dict or set(terms) != _TERMS_KEYS:
        raise CharacterSheetWorkflowError("terms resource schema is invalid.")
    editor = value["editor"]
    if type(editor) is not dict or set(editor) != _EDITOR_KEYS:
        raise CharacterSheetWorkflowError("editor resource schema is invalid.")
    if (
        editor["local"] is not True
        or type(editor["operation"]) is not str
        or editor["operation"] != QWEN_IMAGE_EDIT_OPERATION
    ):
        raise CharacterSheetWorkflowError(
            "editor must be the local Qwen Image Edit repair operation."
        )
    return {
        "profile_id": profile_id,
        "base_model": _revision_resource(value["base_model"], name="base_model"),
        "lora": _revision_resource(value["lora"], name="lora"),
        "schedule": _revision_resource(value["schedule"], name="schedule"),
        "terms": {
            "id": _identifier(terms["id"], name="terms.id"),
            "revision": _identifier(terms["revision"], name="terms.revision"),
            "acceptance_digest": _digest(
                terms["acceptance_digest"], name="terms.acceptance_digest",
            ),
        },
        "planner": _local_resource(value["planner"], name="planner"),
        "reviewer": _local_resource(value["reviewer"], name="reviewer"),
        "editor": {
            **_revision_resource(
                {"id": editor["id"], "revision": editor["revision"]},
                name="editor",
            ),
            "local": True,
            "operation": QWEN_IMAGE_EDIT_OPERATION,
        },
    }


def _authorization_binding(
    *,
    project_id: str,
    profile_id: str,
    anchor: Mapping[str, Any],
    resource_base: Mapping[str, Any],
    seed: int,
) -> dict[str, str]:
    return {
        "project_id": project_id,
        "profile_id": profile_id,
        "anchor_commitment": _seal("character-sheet-anchor-v1", anchor),
        "resource_manifest_commitment": _seal(
            "character-sheet-resource-base-v1", resource_base,
        ),
        "terms_commitment": _seal(
            "character-sheet-terms-v1", resource_base["terms"],
        ),
        "seed_commitment": _seal("character-sheet-seed-v1", seed),
    }


def build_character_sheet_execution_authorization(
    *,
    project_id: Any,
    profile: Any,
    anchor: Any,
    resource_base: Any,
    revision: Any,
    evidence_digest: Any,
    nonce: Any,
    issued_at_unix: Any,
    expires_at_unix: Any,
    seed: Any,
) -> dict[str, Any]:
    """Bind one short-lived server authorization to exact private inputs."""
    clean_project = _identifier(project_id, name="project_id")
    profile_id = normalize_character_sheet_profile(
        profile, require_available=False,
    )
    if PROFILE_DEFINITIONS[profile_id]["status"] != "requires_server_authorization":
        raise CharacterSheetWorkflowError(
            "Character Sheet profile is blocked by its declared availability."
        )
    clean_anchor = _normalize_anchor(anchor, project_id=clean_project)
    clean_base = _normalize_resource_base(resource_base, profile_id=profile_id)
    clean_seed = _integer(seed, name="seed", minimum=0, maximum=2**63 - 1)
    issued = _integer(
        issued_at_unix, name="authorization.issued_at_unix",
        minimum=0, maximum=2**63 - 1,
    )
    expires = _integer(
        expires_at_unix, name="authorization.expires_at_unix",
        minimum=0, maximum=2**63 - 1,
    )
    if not issued < expires or expires - issued > AUTHORIZATION_MAX_TTL_SECONDS:
        raise CharacterSheetWorkflowError(
            "Character Sheet authorization lifetime is invalid."
        )
    return {
        "status": "available",
        "revision": _identifier(revision, name="authorization.revision"),
        "evidence_digest": _digest(
            evidence_digest, name="authorization.evidence_digest",
        ),
        "nonce": _identifier(nonce, name="authorization.nonce"),
        "issued_at_unix": issued,
        "expires_at_unix": expires,
        **_authorization_binding(
            project_id=clean_project,
            profile_id=profile_id,
            anchor=clean_anchor,
            resource_base=clean_base,
            seed=clean_seed,
        ),
    }


def _normalize_resources(
    value: Any,
    *,
    profile_id: str,
    project_id: str,
    anchor: Mapping[str, Any],
    authorization_checked_at_unix: int,
    seed: int,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _RESOURCE_KEYS:
        raise CharacterSheetWorkflowError("Character Sheet resources schema is invalid.")
    resource_base = _normalize_resource_base(
        {key: value[key] for key in _RESOURCE_BASE_KEYS},
        profile_id=profile_id,
    )
    authorization = value["execution_authorization"]
    if type(authorization) is not dict or set(authorization) != _AUTHORIZATION_KEYS:
        raise CharacterSheetWorkflowError(
            "execution_authorization resource schema is invalid."
        )
    definition = PROFILE_DEFINITIONS[profile_id]
    if definition["status"] != "requires_server_authorization":
        raise CharacterSheetWorkflowError(
            "Character Sheet profile is blocked by its declared availability."
        )
    if type(authorization["status"]) is not str or authorization["status"] != "available":
        raise CharacterSheetWorkflowError(
            "Character Sheet profile lacks server execution authorization."
        )
    issued = _integer(
        authorization["issued_at_unix"], name="authorization.issued_at_unix",
        minimum=0, maximum=2**63 - 1,
    )
    expires = _integer(
        authorization["expires_at_unix"], name="authorization.expires_at_unix",
        minimum=0, maximum=2**63 - 1,
    )
    if (
        not issued <= authorization_checked_at_unix < expires
        or expires - issued > AUTHORIZATION_MAX_TTL_SECONDS
    ):
        raise CharacterSheetWorkflowError("Character Sheet authorization is stale.")
    binding = _authorization_binding(
        project_id=project_id,
        profile_id=profile_id,
        anchor=anchor,
        resource_base=resource_base,
        seed=seed,
    )
    clean_authorization = {
        "status": "available",
        "revision": _identifier(
            authorization["revision"], name="authorization.revision",
        ),
        "evidence_digest": _digest(
            authorization["evidence_digest"], name="authorization.evidence_digest",
        ),
        "nonce": _identifier(authorization["nonce"], name="authorization.nonce"),
        "issued_at_unix": issued,
        "expires_at_unix": expires,
        "project_id": _identifier(
            authorization["project_id"], name="authorization.project_id",
        ),
        "profile_id": _identifier(
            authorization["profile_id"], name="authorization.profile_id",
        ),
        "anchor_commitment": _digest(
            authorization["anchor_commitment"], name="authorization.anchor_commitment",
        ),
        "resource_manifest_commitment": _digest(
            authorization["resource_manifest_commitment"],
            name="authorization.resource_manifest_commitment",
        ),
        "terms_commitment": _digest(
            authorization["terms_commitment"], name="authorization.terms_commitment",
        ),
        "seed_commitment": _digest(
            authorization["seed_commitment"], name="authorization.seed_commitment",
        ),
    }
    if any(clean_authorization[key] != expected for key, expected in binding.items()):
        raise CharacterSheetWorkflowError(
            "Character Sheet authorization does not bind the current inputs."
        )
    return {**resource_base, "execution_authorization": clean_authorization}


def _normalize_panel(value: Any, *, expected_role: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _PANEL_KEYS:
        raise CharacterSheetWorkflowError("Character Sheet panel schema is invalid.")
    if type(value["role"]) is not str or value["role"] != expected_role:
        raise CharacterSheetWorkflowError("Character Sheet panel order or role changed.")
    return {
        "role": expected_role,
        "x": _integer(
            value["x"], name=f"panel.{expected_role}.x",
            minimum=0, maximum=_MAX_COORDINATE,
        ),
        "y": _integer(
            value["y"], name=f"panel.{expected_role}.y",
            minimum=0, maximum=_MAX_COORDINATE,
        ),
        "width": _integer(
            value["width"], name=f"panel.{expected_role}.width",
            minimum=1, maximum=_MAX_COORDINATE,
        ),
        "height": _integer(
            value["height"], name=f"panel.{expected_role}.height",
            minimum=1, maximum=_MAX_COORDINATE,
        ),
        "sha256": _digest(value["sha256"], name=f"panel.{expected_role}.sha256"),
    }


def _normalize_panels(value: Any, *, profile_id: str) -> list[dict[str, Any]]:
    roles = tuple(PROFILE_DEFINITIONS[profile_id]["panel_roles"])
    if (
        type(value) is not list
        or len(value) != len(roles)
    ):
        raise CharacterSheetWorkflowError("Character Sheet panel cardinality is invalid.")
    return [
        _normalize_panel(panel, expected_role=role)
        for role, panel in zip(roles, value, strict=True)
    ]


def _panel_commitments(panels: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {"role": panel["role"], "commitment": _seal("character-sheet-panel-v1", panel)}
        for panel in panels
    ]


def _validate_role_commitments(
    value: Any,
    *,
    roles: Sequence[str],
    name: str,
) -> list[dict[str, str]]:
    if (
        type(value) is not list
        or len(value) != len(roles)
    ):
        raise CharacterSheetWorkflowError(f"{name} is invalid.")
    result: list[dict[str, str]] = []
    for expected_role, item in zip(roles, value, strict=True):
        if type(item) is not dict or set(item) != _ROLE_COMMITMENT_KEYS:
            raise CharacterSheetWorkflowError(f"{name} is invalid.")
        if type(item["role"]) is not str or item["role"] != expected_role:
            raise CharacterSheetWorkflowError(f"{name} role order changed.")
        result.append({
            "role": expected_role,
            "commitment": _digest(item["commitment"], name=f"{name}.commitment"),
        })
    return result


def _resource_commitments(resources: Mapping[str, Any], *, seed: int) -> dict[str, str]:
    return {
        "seed": _seal("character-sheet-seed-v1", seed),
        **{
            key: _seal(f"character-sheet-{key}-v1", resources[key])
            for key in (
                "base_model", "lora", "schedule", "terms", "planner",
                "reviewer", "editor", "execution_authorization",
            )
        },
    }


def _lineage_commitment(lineage: Sequence[Mapping[str, Any]]) -> str:
    return _seal("character-sheet-repair-lineage-v1", list(lineage))


def _normalize_lineage(
    value: Any,
    *,
    profile_id: str,
    project_id: str,
    anchor: Mapping[str, Any],
    resources: Mapping[str, Any],
    seed: int,
    authorization_checked_at_unix: int,
    panel_roles: Sequence[str],
    current_panels: Sequence[Mapping[str, Any]],
    anchor_commitment: str,
    editor_commitment: str,
) -> tuple[list[dict[str, Any]], str]:
    if type(value) is not list:
        raise CharacterSheetWorkflowError("Character Sheet repair lineage is invalid.")
    clean: list[dict[str, Any]] = []
    prior_after_panels: list[dict[str, Any]] | None = None
    current_panel_commitments = _panel_commitments(current_panels)
    initial_commitment = _seal(
        "character-sheet-panel-set-v1", list(current_panel_commitments),
    )
    for index, raw in enumerate(value, start=1):
        if type(raw) is not dict or set(raw) != _REPAIR_KEYS:
            raise CharacterSheetWorkflowError("Character Sheet repair event is invalid.")
        if (
            type(raw["attempt"]) is not int
            or raw["attempt"] != index
            or type(raw["operation"]) is not str
            or raw["operation"] != QWEN_IMAGE_EDIT_OPERATION
        ):
            raise CharacterSheetWorkflowError("Character Sheet repair event identity is invalid.")
        failed = raw["failed_roles"]
        if (
            type(failed) is not list
            or not failed
            or any(type(role) is not str or role not in panel_roles for role in failed)
            or len(set(failed)) != len(failed)
        ):
            raise CharacterSheetWorkflowError("Character Sheet failed panel roles are invalid.")
        expected_failed = [role for role in panel_roles if role in set(failed)]
        if failed != expected_failed:
            raise CharacterSheetWorkflowError("Character Sheet failed panel order changed.")
        before_panels = _normalize_panels(raw["before_panels"], profile_id=profile_id)
        after_panels = _normalize_panels(raw["after_panels"], profile_id=profile_id)
        before = _validate_role_commitments(
            raw["before_panel_commitments"], roles=panel_roles,
            name="repair.before_panel_commitments",
        )
        after = _validate_role_commitments(
            raw["after_panel_commitments"], roles=panel_roles,
            name="repair.after_panel_commitments",
        )
        if before != _panel_commitments(before_panels) or after != _panel_commitments(after_panels):
            raise CharacterSheetWorkflowError("Character Sheet repair panel snapshot changed.")
        if prior_after_panels is not None and before_panels != prior_after_panels:
            raise CharacterSheetWorkflowError("Character Sheet repair chain is broken.")
        preserved_roles = [role for role in panel_roles if role not in set(failed)]
        preserved = _validate_role_commitments(
            raw["preserved_panel_commitments"], roles=preserved_roles,
            name="repair.preserved_panel_commitments",
        )
        before_by_role = {item["role"]: item["commitment"] for item in before}
        after_by_role = {item["role"]: item["commitment"] for item in after}
        before_panels_by_role = {item["role"]: item for item in before_panels}
        after_panels_by_role = {item["role"]: item for item in after_panels}
        if (
            preserved != [
                {"role": role, "commitment": before_by_role[role]}
                for role in preserved_roles
            ]
            or any(before_by_role[role] != after_by_role[role] for role in preserved_roles)
            or any(before_by_role[role] == after_by_role[role] for role in failed)
            or any(
                before_panels_by_role[role] != after_panels_by_role[role]
                for role in preserved_roles
            )
            or any(
                any(
                    before_panels_by_role[role][key]
                    != after_panels_by_role[role][key]
                    for key in ("x", "y", "width", "height")
                )
                for role in failed
            )
            or any(
                before_panels_by_role[role]["sha256"]
                == after_panels_by_role[role]["sha256"]
                for role in failed
            )
        ):
            raise CharacterSheetWorkflowError(
                "Character Sheet repair changed accepted bytes, moved a panel, "
                "or did not replace failed bytes."
            )
        if (
            type(raw["anchor_commitment"]) is not str
            or raw["anchor_commitment"] != anchor_commitment
        ):
            raise CharacterSheetWorkflowError("Character Sheet repair changed the anchor.")
        if (
            type(raw["editor_commitment"]) is not str
            or raw["editor_commitment"] != editor_commitment
        ):
            raise CharacterSheetWorkflowError("Character Sheet repair editor changed.")
        if index == 1:
            initial_commitment = _seal("character-sheet-panel-set-v1", before)
        predecessor_parent = (
            None if index == 1 else clean[-1]["predecessor_plan_seal"]
        )
        predecessor_unsigned = _unsigned_plan(
            profile_id=profile_id,
            project_id=project_id,
            anchor=anchor,
            resources=resources,
            seed=seed,
            panels=before_panels,
            repair_lineage=clean,
            parent_plan_seal=predecessor_parent,
            initial_panels_commitment=initial_commitment,
            authorization_checked_at_unix=authorization_checked_at_unix,
        )
        expected_predecessor = _seal(
            "character-sheet-plan-v1", predecessor_unsigned,
        )
        supplied_predecessor = _digest(
            raw["predecessor_plan_seal"], name="repair.predecessor_plan_seal",
        )
        if not hmac.compare_digest(supplied_predecessor, expected_predecessor):
            raise CharacterSheetWorkflowError("Character Sheet repair predecessor changed.")
        event = {
            "attempt": index,
            "operation": QWEN_IMAGE_EDIT_OPERATION,
            "failed_roles": failed,
            "before_panels": before_panels,
            "after_panels": after_panels,
            "before_panel_commitments": before,
            "after_panel_commitments": after,
            "preserved_panel_commitments": preserved,
            "predecessor_plan_seal": expected_predecessor,
            "anchor_commitment": anchor_commitment,
            "editor_commitment": editor_commitment,
        }
        expected_seal = _seal("character-sheet-repair-event-v1", event)
        if not hmac.compare_digest(
            _digest(raw["event_seal"], name="repair.event_seal"), expected_seal,
        ):
            raise CharacterSheetWorkflowError("Character Sheet repair event changed after sealing.")
        event["event_seal"] = expected_seal
        clean.append(event)
        prior_after_panels = after_panels
    if prior_after_panels is not None and prior_after_panels != list(current_panels):
        raise CharacterSheetWorkflowError("Character Sheet repair result does not match panels.")
    return clean, initial_commitment


def _unsigned_plan(
    *,
    profile_id: str,
    project_id: str,
    anchor: Mapping[str, Any],
    resources: Mapping[str, Any],
    seed: int,
    panels: Sequence[Mapping[str, Any]],
    repair_lineage: Sequence[Mapping[str, Any]],
    parent_plan_seal: str | None,
    initial_panels_commitment: str,
    authorization_checked_at_unix: int,
) -> dict[str, Any]:
    panel_commitments = _panel_commitments(panels)
    panel_set_commitment = _seal("character-sheet-panel-set-v1", panel_commitments)
    anchor_commitment = _seal("character-sheet-anchor-v1", anchor)
    resource_commitments = _resource_commitments(resources, seed=seed)
    lineage_commitment = _lineage_commitment(repair_lineage)
    commitments = {
        "anchor": anchor_commitment,
        **resource_commitments,
        "initial_panels": initial_panels_commitment,
        "panels": panel_set_commitment,
        "repair_lineage": lineage_commitment,
    }
    provenance = {
        "service": "character_sheet_workflow",
        "version": PLANNER_VERSION,
        "generation_sequence": "accepted_anchor_then_sheet_lora",
        "planning_locality": "local_vlm",
        "review_locality": "local_vlm",
        "repair_operation": QWEN_IMAGE_EDIT_OPERATION,
        "resource_manifest_commitment": _seal(
            "character-sheet-resource-manifest-v1", resources,
        ),
        "anchor_commitment": anchor_commitment,
        "panel_set_commitment": panel_set_commitment,
        "lineage_commitment": lineage_commitment,
    }
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "profile": profile_id,
        "project_scope": {"project_id": project_id},
        "anchor": deepcopy(dict(anchor)),
        "resources": deepcopy(dict(resources)),
        "seed": seed,
        "panels": deepcopy(list(panels)),
        "repair_lineage": deepcopy(list(repair_lineage)),
        "commitments": commitments,
        "provenance": provenance,
        "parent_plan_seal": parent_plan_seal,
        "authorization_checked_at_unix": authorization_checked_at_unix,
    }


def build_character_sheet_plan(
    *,
    project_id: Any,
    anchor: Any,
    resources: Any,
    panels: Any,
    seed: Any,
    authorization_checked_at_unix: Any,
    profile: Any = None,
) -> dict[str, Any]:
    """Build one deterministic, sealed initial Character Sheet plan."""
    clean_project_id = _identifier(project_id, name="project_id")
    if type(resources) is not dict:
        raise CharacterSheetWorkflowError("Character Sheet resources schema is invalid.")
    resource_profile = resources.get("profile_id")
    authorization = resources.get("execution_authorization")
    available_profile_ids = (
        (resource_profile,)
        if type(authorization) is dict
        and type(authorization.get("status")) is str
        and authorization.get("status") == "available"
        and type(resource_profile) is str
        and resource_profile in PROFILE_DEFINITIONS
        and PROFILE_DEFINITIONS[resource_profile]["status"]
        == "requires_server_authorization"
        else ()
    )
    profile_id = normalize_character_sheet_profile(
        profile, available_profile_ids=available_profile_ids,
    )
    clean_anchor = _normalize_anchor(anchor, project_id=clean_project_id)
    checked_at = _integer(
        authorization_checked_at_unix,
        name="authorization_checked_at_unix",
        minimum=0,
        maximum=2**63 - 1,
    )
    clean_seed = _integer(seed, name="seed", minimum=0, maximum=2**63 - 1)
    clean_resources = _normalize_resources(
        resources,
        profile_id=profile_id,
        project_id=clean_project_id,
        anchor=clean_anchor,
        authorization_checked_at_unix=checked_at,
        seed=clean_seed,
    )
    clean_panels = _normalize_panels(panels, profile_id=profile_id)
    initial_panel_commitment = _seal(
        "character-sheet-panel-set-v1", _panel_commitments(clean_panels),
    )
    unsigned = _unsigned_plan(
        profile_id=profile_id,
        project_id=clean_project_id,
        anchor=clean_anchor,
        resources=clean_resources,
        seed=clean_seed,
        panels=clean_panels,
        repair_lineage=[],
        parent_plan_seal=None,
        initial_panels_commitment=initial_panel_commitment,
        authorization_checked_at_unix=checked_at,
    )
    return {**unsigned, "plan_seal": _seal("character-sheet-plan-v1", unsigned)}


def validate_character_sheet_plan(
    value: Any,
    *,
    expected_project_id: Any = None,
    expected_anchor: Any = None,
    expected_plan_seal: Any = None,
    expected_authorization_evidence_digest: Any = None,
) -> dict[str, Any]:
    """Validate structure plus any independently held trust-boundary facts."""
    if type(value) is not dict or set(value) != _ROOT_KEYS:
        raise CharacterSheetWorkflowError("Character Sheet plan schema is invalid.")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != CONTRACT_SCHEMA_VERSION
        or type(value["planner_version"]) is not str
        or value["planner_version"] != PLANNER_VERSION
    ):
        raise CharacterSheetWorkflowError("Character Sheet plan version is invalid.")
    raw_resources = value["resources"]
    available_profile_ids = (
        (value["profile"],)
        if type(raw_resources) is dict
        and type(raw_resources.get("execution_authorization")) is dict
        and type(raw_resources["execution_authorization"].get("status")) is str
        and raw_resources["execution_authorization"].get("status") == "available"
        and type(value["profile"]) is str
        and value["profile"] in PROFILE_DEFINITIONS
        and PROFILE_DEFINITIONS[value["profile"]]["status"]
        == "requires_server_authorization"
        else ()
    )
    profile_id = normalize_character_sheet_profile(
        value["profile"], available_profile_ids=available_profile_ids,
    )
    project_scope = value["project_scope"]
    if type(project_scope) is not dict or set(project_scope) != _PROJECT_KEYS:
        raise CharacterSheetWorkflowError("Character Sheet project scope is invalid.")
    project_id = _identifier(project_scope["project_id"], name="project_id")
    if expected_project_id is not None and _identifier(
        expected_project_id, name="expected_project_id",
    ) != project_id:
        raise CharacterSheetWorkflowError("Character Sheet project scope changed.")
    anchor = _normalize_anchor(value["anchor"], project_id=project_id)
    if expected_anchor is not None:
        expected = _normalize_anchor(expected_anchor, project_id=project_id)
        if anchor != expected:
            raise CharacterSheetWorkflowError("Character Sheet anchor changed.")
    checked_at = _integer(
        value["authorization_checked_at_unix"],
        name="authorization_checked_at_unix",
        minimum=0,
        maximum=2**63 - 1,
    )
    seed = _integer(value["seed"], name="seed", minimum=0, maximum=2**63 - 1)
    resources = _normalize_resources(
        raw_resources,
        profile_id=profile_id,
        project_id=project_id,
        anchor=anchor,
        authorization_checked_at_unix=checked_at,
        seed=seed,
    )
    if expected_authorization_evidence_digest is not None and not hmac.compare_digest(
        resources["execution_authorization"]["evidence_digest"],
        _digest(
            expected_authorization_evidence_digest,
            name="expected_authorization_evidence_digest",
        ),
    ):
        raise CharacterSheetWorkflowError(
            "Character Sheet authorization evidence does not match."
        )
    panels = _normalize_panels(value["panels"], profile_id=profile_id)
    anchor_commitment = _seal("character-sheet-anchor-v1", anchor)
    resource_commitments = _resource_commitments(resources, seed=seed)
    lineage, initial_panels_commitment = _normalize_lineage(
        value["repair_lineage"],
        profile_id=profile_id,
        project_id=project_id,
        anchor=anchor,
        resources=resources,
        seed=seed,
        authorization_checked_at_unix=checked_at,
        panel_roles=tuple(PROFILE_DEFINITIONS[profile_id]["panel_roles"]),
        current_panels=panels,
        anchor_commitment=anchor_commitment,
        editor_commitment=resource_commitments["editor"],
    )
    parent = value["parent_plan_seal"]
    if parent is not None:
        parent = _digest(parent, name="parent_plan_seal")
    expected_parent = (
        lineage[-1]["predecessor_plan_seal"] if lineage else None
    )
    if parent != expected_parent:
        raise CharacterSheetWorkflowError("Character Sheet parent plan lineage is invalid.")
    unsigned = _unsigned_plan(
        profile_id=profile_id,
        project_id=project_id,
        anchor=anchor,
        resources=resources,
        seed=seed,
        panels=panels,
        repair_lineage=lineage,
        parent_plan_seal=parent,
        initial_panels_commitment=initial_panels_commitment,
        authorization_checked_at_unix=checked_at,
    )
    commitments = value["commitments"]
    provenance = value["provenance"]
    _require_plain_json(commitments, name="Character Sheet commitments")
    _require_plain_json(provenance, name="Character Sheet provenance")
    if (
        type(commitments) is not dict
        or set(commitments) != _COMMITMENT_KEYS
        or dict(commitments) != unsigned["commitments"]
        or type(provenance) is not dict
        or set(provenance) != _PROVENANCE_KEYS
        or dict(provenance) != unsigned["provenance"]
    ):
        raise CharacterSheetWorkflowError("Character Sheet provenance changed.")
    expected_seal = _seal("character-sheet-plan-v1", unsigned)
    supplied_seal = _digest(value["plan_seal"], name="plan_seal")
    if not hmac.compare_digest(supplied_seal, expected_seal):
        raise CharacterSheetWorkflowError("Character Sheet plan changed after sealing.")
    if expected_plan_seal is not None and not hmac.compare_digest(
        expected_seal, _digest(expected_plan_seal, name="expected_plan_seal"),
    ):
        raise CharacterSheetWorkflowError(
            "Character Sheet plan does not match the trusted seal."
        )
    return {**unsigned, "plan_seal": expected_seal}


def apply_failed_panel_repairs(
    plan: Any,
    *,
    failed_roles: Any,
    repaired_panels: Any,
) -> dict[str, Any]:
    """Replace exactly failed panels while preserving every accepted component."""
    clean = validate_character_sheet_plan(plan)
    roles = tuple(PROFILE_DEFINITIONS[clean["profile"]]["panel_roles"])
    if (
        type(failed_roles) is not list
        or not failed_roles
        or any(type(role) is not str or role not in roles for role in failed_roles)
        or len(set(failed_roles)) != len(failed_roles)
    ):
        raise CharacterSheetWorkflowError("Character Sheet failed panel roles are invalid.")
    ordered_failed = [role for role in roles if role in set(failed_roles)]
    if list(failed_roles) != ordered_failed:
        raise CharacterSheetWorkflowError("Character Sheet failed panel order is invalid.")
    if (
        type(repaired_panels) is not list
        or len(repaired_panels) != len(ordered_failed)
    ):
        raise CharacterSheetWorkflowError("Character Sheet repair cardinality is invalid.")
    replacements = {
        role: _normalize_panel(panel, expected_role=role)
        for role, panel in zip(ordered_failed, repaired_panels, strict=True)
    }
    before_panels = clean["panels"]
    after_panels: list[dict[str, Any]] = []
    for panel in before_panels:
        replacement = replacements.get(panel["role"])
        if replacement is None:
            after_panels.append(deepcopy(panel))
            continue
        if any(
            replacement[key] != panel[key]
            for key in ("x", "y", "width", "height")
        ):
            raise CharacterSheetWorkflowError("Character Sheet repair moved a panel.")
        if replacement["sha256"] == panel["sha256"]:
            raise CharacterSheetWorkflowError(
                "Character Sheet repair did not change a failed panel."
            )
        after_panels.append(replacement)
    before_commitments = _panel_commitments(before_panels)
    after_commitments = _panel_commitments(after_panels)
    failed_set = set(ordered_failed)
    preserved = [
        item for item in before_commitments if item["role"] not in failed_set
    ]
    event = {
        "attempt": len(clean["repair_lineage"]) + 1,
        "operation": QWEN_IMAGE_EDIT_OPERATION,
        "failed_roles": ordered_failed,
        "before_panels": deepcopy(before_panels),
        "after_panels": deepcopy(after_panels),
        "before_panel_commitments": before_commitments,
        "after_panel_commitments": after_commitments,
        "preserved_panel_commitments": preserved,
        "predecessor_plan_seal": clean["plan_seal"],
        "anchor_commitment": clean["commitments"]["anchor"],
        "editor_commitment": clean["commitments"]["editor"],
    }
    event["event_seal"] = _seal("character-sheet-repair-event-v1", event)
    lineage = [*clean["repair_lineage"], event]
    unsigned = _unsigned_plan(
        profile_id=clean["profile"],
        project_id=clean["project_scope"]["project_id"],
        anchor=clean["anchor"],
        resources=clean["resources"],
        seed=clean["seed"],
        panels=after_panels,
        repair_lineage=lineage,
        parent_plan_seal=clean["plan_seal"],
        initial_panels_commitment=clean["commitments"]["initial_panels"],
        authorization_checked_at_unix=clean["authorization_checked_at_unix"],
    )
    repaired = {**unsigned, "plan_seal": _seal("character-sheet-plan-v1", unsigned)}
    return validate_character_sheet_plan(repaired)


def assert_character_sheet_replay(
    expected: Any,
    replayed: Any,
    *,
    expected_project_id: Any = None,
    expected_anchor: Any = None,
) -> dict[str, Any]:
    """Require byte-identical canonical plans across persistence/recovery."""
    left = validate_character_sheet_plan(
        expected,
        expected_project_id=expected_project_id,
        expected_anchor=expected_anchor,
    )
    right = validate_character_sheet_plan(
        replayed,
        expected_project_id=expected_project_id,
        expected_anchor=expected_anchor,
    )
    if not hmac.compare_digest(_canonical_json(left), _canonical_json(right)):
        raise CharacterSheetWorkflowError("Recovered Character Sheet plan does not match.")
    return right


def assert_character_sheet_execution_authorized(
    plan: Any,
    *,
    now_unix: Any,
    expected_plan_seal: Any,
    expected_authorization_evidence_digest: Any,
) -> dict[str, Any]:
    """Recheck trusted plan/evidence identity and authorization freshness."""
    clean = validate_character_sheet_plan(
        plan,
        expected_plan_seal=expected_plan_seal,
        expected_authorization_evidence_digest=(
            expected_authorization_evidence_digest
        ),
    )
    now = _integer(
        now_unix, name="now_unix", minimum=0, maximum=2**63 - 1,
    )
    authorization = clean["resources"]["execution_authorization"]
    if not authorization["issued_at_unix"] <= now < authorization["expires_at_unix"]:
        raise CharacterSheetWorkflowError(
            "Character Sheet execution authorization is not current."
        )
    return clean


def canonical_character_sheet_plan(value: Any) -> bytes:
    """Return canonical replay bytes only after complete validation."""
    return _canonical_json(validate_character_sheet_plan(value))


def public_character_sheet_plan(value: Any) -> dict[str, Any]:
    """Project bounded, content-free status without project/resource identities."""
    plan = validate_character_sheet_plan(value)
    definition = PROFILE_DEFINITIONS[plan["profile"]]
    repaired_roles = [
        role
        for event in plan["repair_lineage"]
        for role in event["failed_roles"]
    ]
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "profile": plan["profile"],
        "profile_status": definition["status"],
        "execution_status": "authorization_was_valid_at_plan_time",
        "experimental": definition["experimental"],
        "anchor_kind": plan["anchor"]["kind"],
        "panel_count": len(plan["panels"]),
        "ordered_panel_roles": [panel["role"] for panel in plan["panels"]],
        "repair_operation": QWEN_IMAGE_EDIT_OPERATION,
        "repair_attempt_count": len(plan["repair_lineage"]),
        "repaired_roles": list(dict.fromkeys(repaired_roles)),
        "planning_locality": "local_vlm",
        "review_locality": "local_vlm",
        "private_output": True,
    }


__all__ = [
    "AUTHORIZATION_MAX_TTL_SECONDS",
    "CONTRACT_SCHEMA_VERSION",
    "DEFAULT_PROFILE_ID",
    "PLANNER_VERSION",
    "PROFILE_DEFINITIONS",
    "QWEN_IMAGE_EDIT_OPERATION",
    "CharacterSheetWorkflowError",
    "apply_failed_panel_repairs",
    "assert_character_sheet_execution_authorized",
    "assert_character_sheet_replay",
    "build_character_sheet_execution_authorization",
    "build_character_sheet_plan",
    "canonical_character_sheet_plan",
    "character_sheet_profile_catalog",
    "normalize_character_sheet_profile",
    "public_character_sheet_plan",
    "validate_character_sheet_plan",
]
