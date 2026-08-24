"""Read-only inspection for an owner-supplied future storage-tier plan.

This module deliberately does not create directories, mount filesystems, copy
data, or change Maestro/WGP configuration.  It validates an external JSON plan
and describes the bindings that a later, separately authorized cutover could
apply.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


PLAN_ENVIRONMENT_KEY = "MAESTRO_STORAGE_TIER_PLAN_FILE"
STORAGE_TIER_PLAN_SCHEMA = 1
STORAGE_TIER_ROLES = ("hot", "warm_models", "warm_bulk", "cold")
MAX_PLAN_BYTES = 256 * 1024

_PLAN_KEYS = frozenset({"schema_version", "tiers"})
_TIER_KEYS = frozenset({"root", "write_intent", "identity"})
_IDENTITY_KEYS = frozenset({"filesystem_uuid", "partition_uuid"})
_STABLE_ID = re.compile(r"^[A-Za-z0-9._:+-]{1,128}$")
_WRITE_INTENTS = frozenset({"read_write", "read_only"})
_REQUIRED_WRITE_INTENTS = {
    "hot": "read_write",
    "warm_models": "read_write",
    "warm_bulk": "read_write",
    "cold": "read_only",
}

# Relative layout only.  The owner supplies every absolute root in the ignored
# external plan.  These values are reported, never created or applied.
_ENVIRONMENT_LAYOUT = {
    "HF_HOME": ("warm_models", "caches/huggingface"),
    "TORCH_HOME": ("warm_models", "caches/torch"),
    "MAESTRO_LLM_CACHE": ("warm_models", "caches/maestro-llm"),
    "GRADIO_TEMP_DIR": ("hot", "temporary/gradio"),
}
_WGP_LAYOUT = {
    "checkpoint_primary": ("warm_models", "models/maestro/ckpts"),
    "checkpoint_linked": ("cold", "models/maestro/ckpts"),
    "save_path": ("warm_bulk", "outputs/maestro"),
}

IdentityProbe = Callable[[Path], Mapping[str, str]]


def _not_configured() -> dict[str, Any]:
    return {
        "schema_version": STORAGE_TIER_PLAN_SCHEMA,
        "configured": False,
        "status": "not_configured",
        "plan_file": None,
        "tiers": {},
        "proposed_bindings": {"environment": {}, "wgp": {}},
        "issues": [],
        "applied": False,
    }


def _issue(code: str, message: str, *, role: str | None = None) -> dict[str, str]:
    item = {"code": code, "message": message}
    if role is not None:
        item["role"] = role
    return item


def _normalize_identity(value: Any, *, role: str, issues: list[dict[str, str]]) -> dict[str, str]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        issues.append(_issue("invalid_identity", "identity must be an object.", role=role))
        return {}
    unknown = sorted(set(value) - _IDENTITY_KEYS)
    if unknown:
        issues.append(_issue(
            "invalid_identity",
            f"identity has unsupported fields: {', '.join(unknown)}.",
            role=role,
        ))
    result: dict[str, str] = {}
    for key in _IDENTITY_KEYS:
        raw = value.get(key)
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or _STABLE_ID.fullmatch(raw.strip()) is None:
            issues.append(_issue(
                "invalid_identity",
                f"identity.{key} must be a stable identifier.",
                role=role,
            ))
            continue
        result[key] = raw.strip().lower()
    if value and not result:
        issues.append(_issue(
            "invalid_identity",
            "identity must contain filesystem_uuid or partition_uuid.",
            role=role,
        ))
    return result


def _findmnt_identity(root: Path) -> Mapping[str, str]:
    """Return stable mount identity using a read-only system query."""
    executable = shutil.which("findmnt")
    if executable is None:
        return {}
    try:
        completed = subprocess.run(
            [
                executable,
                "--json",
                "--output",
                "UUID,PARTUUID",
                "--target",
                os.fspath(root),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout)
        filesystems = payload.get("filesystems")
        row = filesystems[0] if isinstance(filesystems, list) and filesystems else {}
    except (AttributeError, IndexError, TypeError, ValueError):
        return {}
    result = {}
    for source, target in (("uuid", "filesystem_uuid"), ("partuuid", "partition_uuid")):
        value = row.get(source) if isinstance(row, Mapping) else None
        if isinstance(value, str) and value.strip():
            result[target] = value.strip().lower()
    return result


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        common = os.path.commonpath([os.fspath(left), os.fspath(right)])
    except ValueError:
        return False
    return os.path.normcase(common) in {
        os.path.normcase(os.fspath(left)),
        os.path.normcase(os.fspath(right)),
    }


def _symlink_component(path: Path) -> Path | None:
    """Return the first symlink in an absolute path without resolving it."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return current
        except OSError:
            return current
    return None


def _binding(role: str, relative: str, tiers: Mapping[str, Mapping[str, Any]], *, write_intent: str) -> dict[str, Any]:
    tier = tiers.get(role) or {}
    resolved_root = tier.get("resolved_root")
    if not resolved_root:
        return {
            "tier": role,
            "relative_path": relative,
            "path": None,
            "state": "unbound",
            "write_intent": write_intent,
            "apply": False,
        }
    candidate = Path(str(resolved_root), *Path(relative).parts)
    if _symlink_component(candidate) is not None:
        return {
            "tier": role,
            "relative_path": relative,
            "path": os.fspath(candidate),
            "state": "unsafe_symlink",
            "write_intent": write_intent,
            "apply": False,
        }
    try:
        root_real = os.fspath(Path(str(resolved_root)).resolve(strict=True))
        candidate_real = os.fspath(candidate.resolve(strict=False))
        contained = os.path.normcase(os.path.commonpath([
            root_real,
            candidate_real,
        ])) == os.path.normcase(root_real)
    except (OSError, RuntimeError, ValueError):
        contained = False
    if not contained:
        return {
            "tier": role,
            "relative_path": relative,
            "path": os.fspath(candidate),
            "state": "unsafe_escape",
            "write_intent": write_intent,
            "apply": False,
        }
    exists = candidate.is_dir()
    return {
        "tier": role,
        "relative_path": relative,
        "path": os.fspath(candidate),
        "state": "ready" if exists else "missing",
        "write_intent": write_intent,
        "apply": False,
    }


def _proposed_bindings(tiers: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    environment = {
        name: _binding(role, relative, tiers, write_intent="read_write")
        for name, (role, relative) in _ENVIRONMENT_LAYOUT.items()
    }
    primary = _binding(*_WGP_LAYOUT["checkpoint_primary"], tiers, write_intent="read_write")
    linked = _binding(*_WGP_LAYOUT["checkpoint_linked"], tiers, write_intent="read_only")
    save_path = _binding(*_WGP_LAYOUT["save_path"], tiers, write_intent="read_write")
    return {
        "environment": environment,
        "wgp": {
            "checkpoint_primary": primary,
            "checkpoint_linked": [linked],
            "checkpoints_paths": [primary, linked],
            "save_path": save_path,
        },
    }


def inspect_storage_tier_plan(
    plan_file: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    identity_probe: IdentityProbe | None = None,
) -> dict[str, Any]:
    """Validate and describe a storage plan without changing host state."""
    env = os.environ if environ is None else environ
    configured_path = os.fspath(plan_file) if plan_file is not None else str(env.get(PLAN_ENVIRONMENT_KEY) or "").strip()
    if not configured_path:
        return _not_configured()

    issues: list[dict[str, str]] = []
    plan_path = Path(configured_path)
    report: dict[str, Any] = {
        "schema_version": STORAGE_TIER_PLAN_SCHEMA,
        "configured": True,
        "status": "invalid",
        "plan_file": os.fspath(plan_path),
        "tiers": {},
        "proposed_bindings": {"environment": {}, "wgp": {}},
        "issues": issues,
        "applied": False,
    }
    if not plan_path.is_absolute():
        issues.append(_issue("invalid_plan_path", f"{PLAN_ENVIRONMENT_KEY} must be an absolute path."))
        return report
    try:
        plan_path = plan_path.resolve(strict=True)
        report["plan_file"] = os.fspath(plan_path)
        if not plan_path.is_file():
            raise OSError("not a regular file")
        size = plan_path.stat().st_size
        if size > MAX_PLAN_BYTES:
            issues.append(_issue("plan_too_large", f"Plan exceeds {MAX_PLAN_BYTES} bytes."))
            return report
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append(_issue("missing_plan", "The configured storage-tier plan file does not exist."))
        return report
    except (OSError, UnicodeError) as exc:
        issues.append(_issue("unreadable_plan", f"The storage-tier plan cannot be read: {exc}."))
        return report
    except json.JSONDecodeError as exc:
        issues.append(_issue("invalid_json", f"The storage-tier plan is not valid JSON: line {exc.lineno}, column {exc.colno}."))
        return report

    if not isinstance(payload, Mapping):
        issues.append(_issue("invalid_plan", "The storage-tier plan root must be an object."))
        return report
    unknown_plan_keys = sorted(set(payload) - _PLAN_KEYS)
    if unknown_plan_keys:
        issues.append(_issue("invalid_plan", f"Plan has unsupported fields: {', '.join(unknown_plan_keys)}."))
    if payload.get("schema_version") != STORAGE_TIER_PLAN_SCHEMA:
        issues.append(_issue("invalid_schema", f"schema_version must be {STORAGE_TIER_PLAN_SCHEMA}."))
    raw_tiers = payload.get("tiers")
    if not isinstance(raw_tiers, Mapping):
        issues.append(_issue("invalid_tiers", "tiers must be an object with all four storage roles."))
        return report
    unknown_roles = sorted(set(raw_tiers) - set(STORAGE_TIER_ROLES))
    if unknown_roles:
        issues.append(_issue("invalid_role", f"Unsupported storage roles: {', '.join(unknown_roles)}."))

    probe = identity_probe or _findmnt_identity
    roots: list[tuple[str, Path]] = []
    normalized_tiers: dict[str, dict[str, Any]] = {}
    for role in STORAGE_TIER_ROLES:
        raw = raw_tiers.get(role)
        if not isinstance(raw, Mapping):
            issues.append(_issue("missing_role", "The storage role is missing or invalid.", role=role))
            normalized_tiers[role] = {"state": "unbound", "root": None}
            continue
        unknown_tier_keys = sorted(set(raw) - _TIER_KEYS)
        if unknown_tier_keys:
            issues.append(_issue(
                "invalid_tier",
                f"Tier has unsupported fields: {', '.join(unknown_tier_keys)}.",
                role=role,
            ))
        write_intent = raw.get("write_intent")
        if write_intent not in _WRITE_INTENTS:
            issues.append(_issue(
                "invalid_write_intent",
                "write_intent must be read_write or read_only.",
                role=role,
            ))
        required = _REQUIRED_WRITE_INTENTS.get(role)
        if required is not None and write_intent != required:
            issues.append(_issue(
                "invalid_write_intent",
                f"{role} must use {required} for the fixed tier contract.",
                role=role,
            ))
        expected_identity = _normalize_identity(raw.get("identity"), role=role, issues=issues)
        root_value = raw.get("root")
        tier_report: dict[str, Any] = {
            "root": root_value if isinstance(root_value, str) else None,
            "resolved_root": None,
            "state": "unbound",
            "write_intent": write_intent,
            "expected_identity": expected_identity,
            "observed_identity": {},
        }
        normalized_tiers[role] = tier_report
        if root_value in (None, ""):
            continue
        if not isinstance(root_value, str) or not Path(root_value).is_absolute():
            issues.append(_issue("invalid_root", "A bound root must be an absolute path.", role=role))
            tier_report["state"] = "invalid"
            continue
        symlink = _symlink_component(Path(root_value))
        if symlink is not None:
            issues.append(_issue(
                "symlink_root",
                "A bound root may not contain a symbolic-link path component.",
                role=role,
            ))
            tier_report["state"] = "invalid"
            continue
        try:
            resolved = Path(root_value).resolve(strict=True)
        except (OSError, RuntimeError):
            issues.append(_issue("missing_root", "The bound root does not exist.", role=role))
            tier_report["state"] = "missing"
            continue
        if not resolved.is_dir():
            issues.append(_issue("invalid_root", "The bound root is not a directory.", role=role))
            tier_report["state"] = "invalid"
            continue
        tier_report["resolved_root"] = os.fspath(resolved)
        tier_report["state"] = "bound"
        tier_report["device_number"] = int(resolved.stat().st_dev)
        tier_report["observed_write_access"] = bool(os.access(resolved, os.W_OK))
        roots.append((role, resolved))
        if write_intent == "read_write" and not tier_report["observed_write_access"]:
            issues.append(_issue(
                "write_access_unavailable",
                "The bound root is not writable by the current account.",
                role=role,
            ))
        if expected_identity:
            try:
                observed_raw = probe(resolved)
            except Exception:
                observed_raw = {}
            observed = {
                key: str(value).strip().lower()
                for key, value in dict(observed_raw).items()
                if key in _IDENTITY_KEYS and str(value).strip()
            }
            tier_report["observed_identity"] = observed
            if not observed:
                issues.append(_issue(
                    "identity_unavailable",
                    "Stable filesystem identity could not be verified.",
                    role=role,
                ))
            for key, expected in expected_identity.items():
                if observed.get(key) != expected:
                    issues.append(_issue(
                        "identity_mismatch",
                        f"Observed {key} does not match the plan.",
                        role=role,
                    ))

    for index, (left_role, left_root) in enumerate(roots):
        for right_role, right_root in roots[index + 1:]:
            if _paths_overlap(left_root, right_root):
                issues.append(_issue(
                    "overlapping_roots",
                    f"{left_role} and {right_role} resolve to duplicate, aliased, or overlapping roots.",
                ))

    report["tiers"] = normalized_tiers
    report["proposed_bindings"] = _proposed_bindings(normalized_tiers)
    bindings = report["proposed_bindings"]
    checked_bindings = [
        *bindings["environment"].items(),
        ("wgp.checkpoint_primary", bindings["wgp"]["checkpoint_primary"]),
        *(
            (f"wgp.checkpoint_linked[{index}]", item)
            for index, item in enumerate(bindings["wgp"]["checkpoint_linked"])
        ),
        ("wgp.save_path", bindings["wgp"]["save_path"]),
    ]
    for name, binding in checked_bindings:
        if binding.get("state") in {"unsafe_symlink", "unsafe_escape"}:
            reason = (
                "contains a symbolic-link path component"
                if binding["state"] == "unsafe_symlink"
                else "resolves outside its assigned tier root"
            )
            issues.append(_issue(
                "symlink_binding" if binding["state"] == "unsafe_symlink" else "escaping_binding",
                f"Proposed binding {name} {reason}.",
                role=str(binding["tier"]),
            ))
    if issues:
        report["status"] = "invalid"
    elif any(tier.get("state") == "unbound" for tier in normalized_tiers.values()):
        report["status"] = "unbound"
    else:
        report["status"] = "ready"
    return report
