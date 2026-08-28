"""Source-only dependency candidates for the isolated H3 prompt rewriter.

This module validates bounded canonical JSON without inspecting the host,
downloading or importing proposed packages, or authorizing installation or
execution.  Submitted wheel rows and resolver reports remain unreviewed
candidates until a later durable, source-bound receipt schema exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from services import h3_prompt_rewriter as rewriter


DEPENDENCY_INPUT_SCHEMA = "maestro.h3-prompt-rewriter.dependency-input.v1"
DEPENDENCY_PLAN_SCHEMA = "maestro.h3-prompt-rewriter.dependency-plan.v1"

MAX_INPUT_BYTES = 512 * 1024
MAX_PACKAGES = 512
MAX_EDGES = 4096
MAX_JSON_DEPTH = 10
MAX_JSON_NODES = 8192
MAX_STRING_LENGTH = 2048

RUNTIME_TARGET = {
    "architecture": "x86_64",
    "glibc_minimum": "2.35",
    "isolation": "feature_specific_process",
    "operating_system": "linux",
    "python_abi": "cp312",
    "python_implementation": "cpython",
    "python_version": "3.12.14",
}

# torchvision and Pillow are direct image-stack dependencies in the reviewed
# upstream requirements.  Exact target versions are selections, not artifact
# availability or compatibility receipts.
ROOT_PACKAGE_PINS = (
    ("accelerate", "1.12.0"),
    ("peft", rewriter.PEFT_VERSION),
    ("pillow", "12.2.0"),
    ("safetensors", "0.8.0"),
    ("tokenizers", "0.22.1"),
    ("torch", "2.10.0+cu128"),
    ("torchvision", "0.25.0+cu128"),
    ("transformers", "4.57.1"),
)
ROOT_REQUIREMENTS = tuple(f"{name}=={version}" for name, version in ROOT_PACKAGE_PINS)

_INPUT_KEYS = {
    "schema",
    "runtime_target",
    "model_receipt_dependencies",
    "root_requirements",
    "packages",
    "resolution_claim",
}
_PACKAGE_KEYS = {
    "name",
    "version",
    "requirement",
    "dependencies",
    "dependency_metadata_complete",
    "wheel_candidates",
}
_WHEEL_KEYS = {"wheel_name", "sha256", "size_bytes", "provenance"}
_RESOLUTION_KEYS = {
    "transitive_complete",
    "resolver",
    "resolver_version",
    "resolver_report_sha256",
    "resolver_inventory_sha256",
    "offline_replay_sha256",
    "offline_replay_inventory_sha256",
}
_MODEL_KEYS = {"adapter", "base"}

_PACKAGE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_VERSION = re.compile(r"[0-9][a-z0-9.!+_-]{0,127}")
_REQUIREMENT = re.compile(
    r"(?P<name>[a-z0-9][a-z0-9._-]{0,127})=="
    r"(?P<version>[0-9][a-z0-9.!+_-]{0,127})"
)
_WHEEL_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")
_WHEEL_VERSION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+]*")
_WHEEL_TAG = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_WHEEL_BUILD_TAG = re.compile(r"[0-9][A-Za-z0-9_]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_PUBLIC_KEY = re.compile(
    r"(?:^|_)(?:path|filepath|directory|cwd|url|uri)(?:$|_)", re.IGNORECASE
)
_PURE_WHEEL_FORBIDDEN = frozenset(
    {"pillow", "safetensors", "tokenizers", "torch", "torchvision"}
)
_ROOT_PACKAGE_NAMES = frozenset(name for name, _version in ROOT_PACKAGE_PINS)


class H3PromptRewriterDependencyClosureError(RuntimeError):
    """Canonical dependency evidence is malformed or contradictory."""


class H3PromptRewriterDependencyClosureSecurityError(
    H3PromptRewriterDependencyClosureError
):
    """Ambiguous, hostile, or non-canonical input was rejected."""


@dataclass(frozen=True, slots=True, init=False)
class H3PromptRewriterDependencyClosurePlan:
    """Immutable blocked candidate document, never an execution grant."""

    _encoded: bytes

    @classmethod
    def _from_document(
        cls, document: dict[str, object]
    ) -> H3PromptRewriterDependencyClosurePlan:
        value = object.__new__(cls)
        object.__setattr__(value, "_encoded", _canonical_json(document))
        return value

    @property
    def document(self) -> dict[str, object]:
        return json.loads(self._encoded.decode("ascii"))

    @property
    def sha256(self) -> str:
        return _sha256_bytes(self._encoded)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _mapping_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_json(value))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise H3PromptRewriterDependencyClosureSecurityError(
                "dependency input contains duplicate fields"
            )
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise H3PromptRewriterDependencyClosureSecurityError(
        "dependency input contains a non-finite number"
    )


def _bounded_plain_json(
    value: object, *, depth: int = 0, nodes: list[int] | None = None
) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise H3PromptRewriterDependencyClosureSecurityError(
            "dependency input exceeds its structure bound"
        )
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        if len(value) > MAX_STRING_LENGTH:
            raise H3PromptRewriterDependencyClosureSecurityError(
                "dependency input contains an oversized string"
            )
        return
    if type(value) is list:
        if len(value) > max(MAX_PACKAGES, MAX_EDGES):
            raise H3PromptRewriterDependencyClosureSecurityError(
                "dependency input contains an oversized list"
            )
        for item in value:
            _bounded_plain_json(item, depth=depth + 1, nodes=nodes)
        return
    if type(value) is dict:
        if len(value) > MAX_PACKAGES:
            raise H3PromptRewriterDependencyClosureSecurityError(
                "dependency input contains too many fields"
            )
        for key, item in value.items():
            if (
                type(key) is not str
                or not key
                or len(key) > 128
                or _FORBIDDEN_PUBLIC_KEY.search(key)
            ):
                raise H3PromptRewriterDependencyClosureSecurityError(
                    "dependency input contains a path-like or invalid field"
                )
            _bounded_plain_json(item, depth=depth + 1, nodes=nodes)
        return
    raise H3PromptRewriterDependencyClosureSecurityError(
        "dependency input must contain plain JSON values"
    )


def _load_canonical_input(payload: object) -> dict[str, Any]:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_INPUT_BYTES:
        raise H3PromptRewriterDependencyClosureSecurityError(
            "dependency input bytes are outside their bound"
        )
    if payload.startswith(b"\xef\xbb\xbf"):
        raise H3PromptRewriterDependencyClosureSecurityError(
            "dependency input must not contain a BOM"
        )
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except H3PromptRewriterDependencyClosureSecurityError:
        raise
    except (RecursionError, UnicodeError, ValueError) as error:
        raise H3PromptRewriterDependencyClosureSecurityError(
            "dependency input JSON is invalid"
        ) from error
    _bounded_plain_json(value)
    if type(value) is not dict:
        raise H3PromptRewriterDependencyClosureSecurityError(
            "dependency input must be one object"
        )
    if payload != _canonical_json(value) + b"\n":
        raise H3PromptRewriterDependencyClosureSecurityError(
            "dependency input is not canonical JSON"
        )
    return value


def _exact_keys(value: object, expected: set[str], *, field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise H3PromptRewriterDependencyClosureError(f"{field} fields are not exact")
    return value


def _normalize_package(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _exact_requirement(value: object, *, field: str) -> tuple[str, str]:
    if type(value) is not str or (match := _REQUIREMENT.fullmatch(value)) is None:
        raise H3PromptRewriterDependencyClosureError(
            f"{field} must be one exact pinned requirement"
        )
    return _normalize_package(match.group("name")), match.group("version")


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3PromptRewriterDependencyClosureError(
            f"{field} must be one lowercase SHA-256 digest"
        )
    return value


def _model_receipts() -> dict[str, object]:
    return {
        "adapter": rewriter.adapter_descriptor(),
        "base": rewriter.base_descriptor(),
    }


def _validate_manylinux_x86_64(platform_tag: str) -> None:
    for platform in platform_tag.split("."):
        if platform == "manylinux2014_x86_64":
            continue
        match = re.fullmatch(r"manylinux_2_([0-9]+)_x86_64", platform)
        if match is None or int(match.group(1)) > 35:
            raise H3PromptRewriterDependencyClosureError(
                "wheel platform exceeds the reviewed Linux x86_64 glibc 2.35 target"
            )


def _validate_wheel_filename(filename: str, *, name: str, version: str) -> None:
    """Validate cp312/Linux, pure, or constrained NVIDIA py3-none-manylinux."""

    if not filename.endswith(".whl"):
        raise H3PromptRewriterDependencyClosureError(
            "dependency candidates must be wheels, never sdists"
        )
    components = filename[:-4].split("-")
    if len(components) not in {5, 6}:
        raise H3PromptRewriterDependencyClosureError(
            "wheel filename is not canonical"
        )
    distribution, wheel_version = components[:2]
    if (
        _WHEEL_COMPONENT.fullmatch(distribution) is None
        or _WHEEL_VERSION.fullmatch(wheel_version) is None
        or (len(components) == 6 and _WHEEL_BUILD_TAG.fullmatch(components[2]) is None)
        or _normalize_package(distribution) != _normalize_package(name)
        or wheel_version.replace("_", "-") != version.replace("_", "-")
    ):
        raise H3PromptRewriterDependencyClosureError(
            "wheel filename does not bind its package and version"
        )
    python_tag, abi_tag, platform_tag = components[-3:]
    if any(
        _WHEEL_TAG.fullmatch(tag) is None
        for tag in (python_tag, abi_tag, platform_tag)
    ):
        raise H3PromptRewriterDependencyClosureError(
            "wheel tags are not canonical"
        )
    python_tags = set(python_tag.split("."))
    abi_tags = set(abi_tag.split("."))
    normalized_name = _normalize_package(name)
    py3_none = python_tags == {"py3"} and abi_tags == {"none"}
    if py3_none and platform_tag == "any":
        if (
            normalized_name in _PURE_WHEEL_FORBIDDEN
            or normalized_name.startswith("nvidia-")
        ):
            raise H3PromptRewriterDependencyClosureError(
                "binary dependency may not use a pure-Python wheel"
            )
        return
    if py3_none:
        if (
            normalized_name in _ROOT_PACKAGE_NAMES
            or not normalized_name.startswith("nvidia-")
        ):
            raise H3PromptRewriterDependencyClosureError(
                "platform-specific py3-none wheel is restricted to NVIDIA transitive packages"
            )
        _validate_manylinux_x86_64(platform_tag)
        return
    exact = python_tags == {"cp312"} and abi_tags == {"cp312"}
    abi3 = abi_tags == {"abi3"} and any(
        (match := re.fullmatch(r"cp3([0-9]+)", tag)) is not None
        and 2 <= int(match.group(1)) <= 12
        for tag in python_tags
    )
    if not (exact or abi3):
        raise H3PromptRewriterDependencyClosureError(
            "wheel ABI is not compatible with reviewed CPython 3.12"
        )
    _validate_manylinux_x86_64(platform_tag)


def _validate_wheel_candidates(
    value: object, *, package: str, version: str
) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > 1:
        raise H3PromptRewriterDependencyClosureError(
            f"{package} must select at most one wheel candidate"
        )
    if not value:
        return []
    item = _exact_keys(value[0], _WHEEL_KEYS, field=f"{package} wheel candidate")
    if type(item["wheel_name"]) is not str or len(item["wheel_name"]) > 255:
        raise H3PromptRewriterDependencyClosureError(
            f"{package} wheel candidate name is invalid"
        )
    _validate_wheel_filename(item["wheel_name"], name=package, version=version)
    _digest(item["sha256"], field=f"{package} unreviewed wheel digest")
    if type(item["size_bytes"]) is not int or not 1 <= item["size_bytes"] <= 2 * 1024**4:
        raise H3PromptRewriterDependencyClosureError(
            f"{package} wheel size is outside its bound"
        )
    if item["provenance"] != "unreviewed_candidate":
        raise H3PromptRewriterDependencyClosureError(
            "wheel candidate may not self-assert reviewed provenance"
        )
    return [dict(item)]


def _validate_package(value: object) -> dict[str, object]:
    item = _exact_keys(value, _PACKAGE_KEYS, field="dependency package")
    name = item["name"]
    version = item["version"]
    if type(name) is not str or _PACKAGE.fullmatch(name) is None:
        raise H3PromptRewriterDependencyClosureError("package name is invalid")
    if type(version) is not str or _VERSION.fullmatch(version) is None:
        raise H3PromptRewriterDependencyClosureError("package version is invalid")
    requirement_name, requirement_version = _exact_requirement(
        item["requirement"], field="package requirement"
    )
    if requirement_name != _normalize_package(name) or requirement_version != version:
        raise H3PromptRewriterDependencyClosureError(
            "package identity contradicts its requirement"
        )
    dependencies = item["dependencies"]
    if type(dependencies) is not list or len(dependencies) > MAX_EDGES:
        raise H3PromptRewriterDependencyClosureError(
            "package dependency list is outside its bound"
        )
    dependency_identities = [
        _exact_requirement(dependency, field="transitive dependency")
        for dependency in dependencies
    ]
    if dependencies != sorted(dependencies):
        raise H3PromptRewriterDependencyClosureError(
            "package dependencies are not in canonical order"
        )
    if len(dependency_identities) != len(set(dependency_identities)):
        raise H3PromptRewriterDependencyClosureError(
            "package dependencies contain duplicate normalized identities"
        )
    if type(item["dependency_metadata_complete"]) is not bool:
        raise H3PromptRewriterDependencyClosureError(
            "dependency metadata completeness must be explicit"
        )
    return {
        "name": name,
        "version": version,
        "requirement": item["requirement"],
        "dependencies": list(dependencies),
        "dependency_metadata_complete": item["dependency_metadata_complete"],
        "wheel_candidates": _validate_wheel_candidates(
            item["wheel_candidates"], package=name, version=version
        ),
    }


def _validate_graph(packages: list[dict[str, object]]) -> list[dict[str, str]]:
    by_name = {_normalize_package(str(item["name"])): item for item in packages}
    if len(by_name) != len(packages):
        raise H3PromptRewriterDependencyClosureError(
            "dependency closure contains duplicate normalized packages"
        )
    roots = {
        _normalize_package(name): version for name, version in ROOT_PACKAGE_PINS
    }
    if any(
        by_name.get(name, {}).get("version") != version
        for name, version in roots.items()
    ):
        raise H3PromptRewriterDependencyClosureError(
            "closure misses an exact isolated-runtime root pin"
        )

    edges: list[dict[str, str]] = []
    adjacency: dict[str, list[str]] = {name: [] for name in by_name}
    for name, package in by_name.items():
        for requirement in package["dependencies"]:
            dependency_name, dependency_version = _exact_requirement(
                requirement, field="transitive dependency"
            )
            dependency = by_name.get(dependency_name)
            if dependency is None or dependency["version"] != dependency_version:
                raise H3PromptRewriterDependencyClosureError(
                    "transitive dependency is unresolved"
                )
            adjacency[name].append(dependency_name)
            edges.append(
                {"from": name, "to": dependency_name, "requirement": requirement}
            )
    if len(edges) > MAX_EDGES:
        raise H3PromptRewriterDependencyClosureError(
            "dependency graph exceeds its edge bound"
        )

    state = {name: 0 for name in by_name}
    for root in sorted(roots):
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            name, exiting = stack.pop()
            if exiting:
                state[name] = 2
                continue
            if state[name] == 1:
                raise H3PromptRewriterDependencyClosureError(
                    "dependency graph contains a cycle"
                )
            if state[name] == 2:
                continue
            state[name] = 1
            stack.append((name, True))
            for dependency in reversed(adjacency[name]):
                if state[dependency] == 1:
                    raise H3PromptRewriterDependencyClosureError(
                        "dependency graph contains a cycle"
                    )
                if state[dependency] == 0:
                    stack.append((dependency, False))
    if {name for name, status in state.items() if status == 2} != set(by_name):
        raise H3PromptRewriterDependencyClosureError(
            "dependency graph contains unreachable packages"
        )
    return sorted(edges, key=lambda item: (item["from"], item["to"], item["requirement"]))


def _inventory_sha256(
    target: dict[str, object], roots: list[str], packages: list[dict[str, object]]
) -> str:
    return _mapping_sha256(
        {
            "runtime_target": target,
            "root_requirements": roots,
            "packages": packages,
        }
    )


def _validate_resolution_claim(
    value: object, *, inventory_sha256: str
) -> dict[str, object]:
    item = _exact_keys(value, _RESOLUTION_KEYS, field="resolver claim")
    if type(item["transitive_complete"]) is not bool:
        raise H3PromptRewriterDependencyClosureError(
            "transitive completeness must be explicit"
        )
    evidence_keys = (
        "resolver",
        "resolver_version",
        "resolver_report_sha256",
        "resolver_inventory_sha256",
        "offline_replay_sha256",
        "offline_replay_inventory_sha256",
    )
    if not item["transitive_complete"]:
        if any(item[key] is not None for key in evidence_keys):
            raise H3PromptRewriterDependencyClosureError(
                "incomplete resolution must not carry completion claims"
            )
        return dict(item)
    if item["resolver"] != "uv":
        raise H3PromptRewriterDependencyClosureError(
            "complete resolution claim must name the reviewed resolver"
        )
    if type(item["resolver_version"]) is not str or _VERSION.fullmatch(
        item["resolver_version"]
    ) is None:
        raise H3PromptRewriterDependencyClosureError(
            "resolver version is not exact"
        )
    _digest(item["resolver_report_sha256"], field="resolver report digest")
    _digest(item["offline_replay_sha256"], field="offline replay digest")
    for field in (
        "resolver_inventory_sha256",
        "offline_replay_inventory_sha256",
    ):
        if _digest(item[field], field=field) != inventory_sha256:
            raise H3PromptRewriterDependencyClosureError(
                "resolver or offline replay claim is not bound to the inventory"
            )
    return dict(item)


def _validated_input(value: object) -> dict[str, object]:
    document = _exact_keys(value, _INPUT_KEYS, field="dependency input")
    if document["schema"] != DEPENDENCY_INPUT_SCHEMA:
        raise H3PromptRewriterDependencyClosureError(
            "dependency input schema is unsupported"
        )
    target = _exact_keys(
        document["runtime_target"], set(RUNTIME_TARGET), field="runtime target"
    )
    if target != RUNTIME_TARGET:
        raise H3PromptRewriterDependencyClosureError(
            "runtime target is not the reviewed isolated CPython ABI"
        )
    model_receipts = _exact_keys(
        document["model_receipt_dependencies"], _MODEL_KEYS, field="model receipts"
    )
    if _canonical_json(model_receipts) != _canonical_json(_model_receipts()):
        raise H3PromptRewriterDependencyClosureError(
            "model receipt dependency identity drifted"
        )
    roots = document["root_requirements"]
    if type(roots) is not list or tuple(roots) != ROOT_REQUIREMENTS:
        raise H3PromptRewriterDependencyClosureError(
            "root requirements are not the exact reviewed ordered pins"
        )
    raw_packages = document["packages"]
    if type(raw_packages) is not list or not 1 <= len(raw_packages) <= MAX_PACKAGES:
        raise H3PromptRewriterDependencyClosureError(
            "dependency package inventory is outside its bound"
        )
    packages = [_validate_package(item) for item in raw_packages]
    names = [_normalize_package(str(item["name"])) for item in packages]
    if names != sorted(names):
        raise H3PromptRewriterDependencyClosureError(
            "dependency packages are not in canonical order"
        )
    if len(names) != len(set(names)):
        raise H3PromptRewriterDependencyClosureError(
            "dependency closure contains duplicate package identities"
        )
    candidate_names = [
        str(candidate["wheel_name"])
        for package in packages
        for candidate in package["wheel_candidates"]
    ]
    candidate_digests = [
        str(candidate["sha256"])
        for package in packages
        for candidate in package["wheel_candidates"]
    ]
    if len(candidate_names) != len(set(candidate_names)):
        raise H3PromptRewriterDependencyClosureError(
            "dependency closure contains duplicate wheel candidates"
        )
    if len(candidate_digests) != len(set(candidate_digests)):
        raise H3PromptRewriterDependencyClosureError(
            "dependency closure contains duplicate candidate digests"
        )
    edges = _validate_graph(packages)
    inventory_sha256 = _inventory_sha256(target, roots, packages)
    resolution = _validate_resolution_claim(
        document["resolution_claim"], inventory_sha256=inventory_sha256
    )
    return {
        "schema": DEPENDENCY_INPUT_SCHEMA,
        "runtime_target": dict(RUNTIME_TARGET),
        "model_receipt_dependencies": _model_receipts(),
        "root_requirements": list(ROOT_REQUIREMENTS),
        "packages": packages,
        "edges": edges,
        "inventory_sha256": inventory_sha256,
        "resolution_claim": resolution,
    }


def _seed_mapping() -> dict[str, object]:
    return {
        "schema": DEPENDENCY_INPUT_SCHEMA,
        "runtime_target": dict(RUNTIME_TARGET),
        "model_receipt_dependencies": _model_receipts(),
        "root_requirements": list(ROOT_REQUIREMENTS),
        "packages": [
            {
                "name": name,
                "version": version,
                "requirement": f"{name}=={version}",
                "dependencies": [],
                "dependency_metadata_complete": False,
                "wheel_candidates": [],
            }
            for name, version in ROOT_PACKAGE_PINS
        ],
        "resolution_claim": {
            "transitive_complete": False,
            "resolver": None,
            "resolver_version": None,
            "resolver_report_sha256": None,
            "resolver_inventory_sha256": None,
            "offline_replay_sha256": None,
            "offline_replay_inventory_sha256": None,
        },
    }


def reviewed_h3_prompt_rewriter_dependency_seed_bytes() -> bytes:
    """Return the deterministic unresolved source-only seed."""

    return _canonical_json(_seed_mapping()) + b"\n"


def build_h3_prompt_rewriter_dependency_closure_plan(
    payload: object, *, expected_input_sha256: object | None = None
) -> H3PromptRewriterDependencyClosurePlan:
    """Validate candidates and return an always-blocked integrity document."""

    value = _validated_input(_load_canonical_input(payload))
    input_value = {key: value[key] for key in _INPUT_KEYS}
    input_sha256 = _mapping_sha256(input_value)
    integrity_bound = False
    if expected_input_sha256 is not None:
        expected = _digest(expected_input_sha256, field="expected input digest")
        if not hmac.compare_digest(expected, input_sha256):
            raise H3PromptRewriterDependencyClosureSecurityError(
                "dependency input does not match the expected integrity digest"
            )
        integrity_bound = True

    packages = value["packages"]
    resolution = value["resolution_claim"]
    blockers = {"durable_reviewed_artifact_receipts_missing"}
    if not resolution["transitive_complete"]:
        blockers.update(
            {
                "complete_hashed_transitive_wheel_closure_missing",
                "offline_replay_evidence_missing",
            }
        )
    else:
        blockers.add("resolution_and_replay_claims_unreviewed")
    if any(
        not package["dependency_metadata_complete"] or not package["wheel_candidates"]
        for package in packages
    ):
        blockers.add("target_wheel_candidate_inventory_incomplete")
    if not integrity_bound:
        blockers.add("input_integrity_binding_missing")

    environment_candidates = {
        "runtime_target": value["runtime_target"],
        "root_requirements": value["root_requirements"],
        "packages": packages,
        "edges": value["edges"],
        "inventory_sha256": value["inventory_sha256"],
        "resolution_claim": resolution,
    }
    document = {
        "schema": DEPENDENCY_PLAN_SCHEMA,
        "status": "blocked",
        "mutation": False,
        "installability_claimed": False,
        "installation_authorized": False,
        "execution_authorized": False,
        "runtime_accepted": False,
        "gpu_accepted": False,
        "input_sha256": input_sha256,
        "input_integrity_bound": integrity_bound,
        "blockers": sorted(blockers),
        "model_receipt_dependencies": value["model_receipt_dependencies"],
        "model_receipts_in_environment_candidates": False,
        "environment_candidates": environment_candidates,
        "environment_candidates_sha256": _mapping_sha256(environment_candidates),
        "separation_scope": {
            "python_requirements_mutation_authorized": False,
            "dockerfile_reviewed": False,
            "repository_wide_separation_claimed": False,
        },
    }
    return H3PromptRewriterDependencyClosurePlan._from_document(document)


__all__ = [
    "DEPENDENCY_INPUT_SCHEMA",
    "DEPENDENCY_PLAN_SCHEMA",
    "H3PromptRewriterDependencyClosureError",
    "H3PromptRewriterDependencyClosurePlan",
    "H3PromptRewriterDependencyClosureSecurityError",
    "ROOT_REQUIREMENTS",
    "RUNTIME_TARGET",
    "build_h3_prompt_rewriter_dependency_closure_plan",
    "reviewed_h3_prompt_rewriter_dependency_seed_bytes",
]
