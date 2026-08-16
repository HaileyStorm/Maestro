"""Source-only dependency evidence for the isolated MiniMax Music 3 runtime.

This module validates one canonical, bounded JSON dependency graph.  It never
downloads, builds, installs, imports a package from the proposed environment,
probes the host, or authorizes staging.  Even a complete graph is only an
independently reviewable input for the existing stage-builder boundary; it is
not an installability claim.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from services import music3_runtime as runtime

DEPENDENCY_INPUT_SCHEMA = "maestro.music3.dependency-closure-input.v1"
DEPENDENCY_PLAN_SCHEMA = "maestro.music3.dependency-closure-plan.v1"
STAGE_BUILDER_INPUT_SCHEMA = "maestro.music3.stage-builder-input.v1"

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_PACKAGES = 4096
MAX_EDGES = 16384
MAX_STRING_LENGTH = 4096
MAX_JSON_DEPTH = 10
MAX_JSON_NODES = 32768

TARGET = {
    "implementation": "cpython",
    "python_version": "3.12.14",
    "python_abi": "cp312",
    "operating_system": "linux",
    "architecture": "x86_64",
    "glibc_minimum": "2.35",
}

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9][a-z0-9.!+_-]{0,127}")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_REQUIREMENT = re.compile(
    r"(?P<name>[a-z0-9][a-z0-9._-]{0,127})"
    r"(?:\[(?P<extras>[a-z0-9][a-z0-9,._-]{0,127})\])?=="
    r"(?P<version>[0-9][a-z0-9.!+_-]{0,127})"
)
_ARTIFACT_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_WHEEL_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")
_WHEEL_TAG = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_WHEEL_BUILD_TAG = re.compile(r"[0-9][A-Za-z0-9_]*")
_CANONICAL_HTTPS_URL = re.compile(r"https://[a-z0-9.-]+/[A-Za-z0-9%+._~!$&'()*,-/:;=@]+")

_PYTHON_ARTIFACT = {
    "artifact_id": "python-cpython-3.12.14-20260814",
    "implementation": "cpython",
    "version": "3.12.14",
    "abi": "cp312",
    "platform": "linux-x86_64",
    "release": "20260814",
    "filename": (
        "cpython-3.12.14+20260814-x86_64-unknown-linux-gnu-"
        "install_only_stripped.tar.gz"
    ),
    "url": (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20260814/cpython-3.12.14%2B20260814-x86_64-unknown-linux-gnu-"
        "install_only_stripped.tar.gz"
    ),
    "sha256": "sha256:5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc",
    "size": 34_143_739,
}

_CUDART_ARTIFACT = {
    "artifact_id": "nvidia-cuda-cudart-13.0.48",
    "component": "cudart",
    "version": "13.0.48",
    "platform": "linux-x86_64",
    "filename": "cuda_cudart-linux-x86_64-13.0.48-archive.tar.xz",
    "url": (
        "https://developer.download.nvidia.com/compute/cuda/redist/cuda_cudart/"
        "linux-x86_64/cuda_cudart-linux-x86_64-13.0.48-archive.tar.xz"
    ),
    "sha256": "sha256:30ffafae23833dafc965cf4e4d034f8553805586959328c64e2c47b3ef3bcd55",
    "size": 1_487_372,
}

_CUDA_REQUIRED_COMPONENTS = (
    "cuda-bindings",
    "cuda-toolkit",
    "cudart",
    "cudnn",
    "cusparselt",
    "nccl",
    "nvshmem",
    "triton",
)
_REVIEWED_ROOT_REQUIREMENTS = tuple(sorted(runtime.REQUIRED_RUNTIME_LOCK_LINES))

_KNOWN_WHEEL_ROWS = (
    (
        "torch", "2.11.0", "torch==2.11.0",
        "torch-2.11.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/1a/c9/82638ef24d7877510f83baf821f5619a61b45568ce21c0a87a91576510aa/torch-2.11.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "0f68f4ac6d95d12e896c3b7a912b5871619542ec54d3649cf48cc1edd4dd2756", 530_712_279,
    ),
    (
        "torchvision", "0.26.0", "torchvision==0.26.0",
        "torchvision-0.26.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/f5/d8/cb6ccda1a1f35a6597645818641701207b3e8e13553e75fce5d86bac74b2/torchvision-0.26.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "d61a5abb6b42a0c0c311996c2ac4b83a94418a97182c83b055a2a4ae985e05aa", 7_522_205,
    ),
    (
        "transformers", "5.12.1", "transformers==5.12.1",
        "transformers-5.12.1-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/df/56/bbd60dd8668055803bf8ba55a81f9b8a8b31497f620109a9671d26a2076d/transformers-5.12.1-py3-none-any.whl",
        "2a5e109d2021265df7098ffbb738295acaf5ad256f12cbc586db2ea4dcbb1a8a", 11_150_587,
    ),
    (
        "sglang", "0.5.16", "sglang==0.5.16",
        "sglang-0.5.16-cp312-cp312-manylinux_2_34_x86_64.whl",
        "https://files.pythonhosted.org/packages/22/98/ee330dbfe49926a5e0c6249f06ae4941673d5af34ccbcd2672789f637514/sglang-0.5.16-cp312-cp312-manylinux_2_34_x86_64.whl",
        "b8ed16e72c7d6a643e31ba52e3ff106439e9dbb78543950e4c810158b826ea8e", 14_614_041,
    ),
    (
        "flashinfer-python", "0.6.14", "flashinfer-python[cu13]==0.6.14",
        "flashinfer_python-0.6.14-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/f2/8f/b101913cb2b3687654f56681cfe9836d447526be663c149966470ef70531/flashinfer_python-0.6.14-py3-none-any.whl",
        "d124369346a3d48eac67e31c42f7a3c813bcc0abc10e2e36db413b7b3dfd97df", 14_574_383,
    ),
    (
        "cache-dit", "1.3.0", "cache-dit==1.3.0",
        "cache_dit-1.3.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/ea/7d/5171701ee0512b965a6fafccbcf8a627b94d91c0ec9feaf3553e81da29d9/cache_dit-1.3.0-py3-none-any.whl",
        "de7445b95e80117734e0cd23406fb960520882933bc66f2c3a4a5ae8ac00ac5b", 356_914,
    ),
    (
        "flash-attn-4", "4.0.0b18", "flash-attn-4==4.0.0b18",
        "flash_attn_4-4.0.0b18-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/0e/04/23be4a0afbb967219e1fecfaf098da7150fd542c33a238306e9b41e33a93/flash_attn_4-4.0.0b18-py3-none-any.whl",
        "613897eea059d3ebbc3fb714a8520973462fb25c4e0ef85cd41d6e26e3170e25", 341_473,
    ),
    (
        "cryptography", "50.0.0", "cryptography==50.0.0",
        "cryptography-50.0.0-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "https://files.pythonhosted.org/packages/85/4f/0fa8c2f4428198f15d9ff8d63400e27afbf94ce833f6108da1eb3753f945/cryptography-50.0.0-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "a91296cb61e8df6f86d0c19cc4068228da256bf59bf86049fbd821084565327f", 4_728_939,
    ),
    (
        "torchaudio", "2.11.0", "torchaudio==2.11.0",
        "torchaudio-2.11.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/88/d8/d6d0f896e064aa67377484efef4911cdcc07bce2929474e1417cc0af18c2/torchaudio-2.11.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "6503c0bdb29daf2e6281bb70ea2dfe2c3553b782b619eb5d73bdadd8a3f7cecf", 1_771_992,
    ),
    (
        "torchcodec", "0.11.1", "torchcodec==0.11.1",
        "torchcodec-0.11.1-cp312-cp312-manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/ca/a9/a2b6ee3e84c55bdd0c45fd991dde71c95a99115ec9e26938b212b4545dcf/torchcodec-0.11.1-cp312-cp312-manylinux_2_28_x86_64.whl",
        "6c26e90e7aa982302644d0af8cb706318682bb390f48a80ecbfeab03499acd04", 2_329_883,
    ),
    (
        "nixl-cu13", "1.1.0", "nixl-cu13==1.1.0",
        "nixl_cu13-1.1.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/13/ad/a3ee9b2cad49e42b2b215d07f55afe0ad38d671d72b6cbd573c98d5a75ba/nixl_cu13-1.1.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "60cc00b12871d8c7d78c2385ad9380070424d5b07d3fe01680f222d6c4f1f428", 36_046_966,
    ),
    (
        "mooncake-transfer-engine-cuda13", "0.3.10", "mooncake-transfer-engine-cuda13==0.3.10",
        "mooncake_transfer_engine_cuda13-0.3.10-cp312-cp312-manylinux_2_35_x86_64.whl",
        "https://files.pythonhosted.org/packages/6a/39/35cb218104bab54b64f0e582eefbf7a386b2ed9215f5f6eb2574f3/mooncake_transfer_engine_cuda13-0.3.10-cp312-cp312-manylinux_2_35_x86_64.whl",
        "5632c0f97a0cd5db639cf97e33f3fc47cbcb1b8fb0b1cc415e959f814c5de672", 42_772_945,
    ),
)

_SOURCE_BUILD_BLOCKERS = (
    {
        "name": "logger",
        "selected_version": "1.4",
        "source_requirement": "logger==1.4",
        "reason": "no-reviewed-wheel",
    },
    {
        "name": "s3prl",
        "selected_version": "0.4.18",
        "source_requirement": "s3prl>=0.4.18",
        "reason": "no-reviewed-wheel",
    },
    {
        "name": "openai-whisper",
        "selected_version": "20250625",
        "source_requirement": "openai-whisper==20250625",
        "reason": "no-reviewed-wheel",
    },
)


class Music3DependencyClosureError(RuntimeError):
    """A content-free dependency-closure contract failure."""


class Music3DependencyClosureSecurityError(Music3DependencyClosureError):
    """Hostile or ambiguous closure input was rejected."""


class Music3DependencyClosureBlocked(Music3DependencyClosureError):
    """The dependency evidence is valid but incomplete."""


@dataclass(frozen=True, slots=True, init=False)
class Music3DependencyClosurePlan:
    """Immutable dependency evidence; never an installer or execution grant."""

    _encoded: bytes

    @classmethod
    def _from_document(cls, document: dict[str, object]) -> Music3DependencyClosurePlan:
        value = object.__new__(cls)
        object.__setattr__(value, "_encoded", _canonical_json(document))
        return value

    @property
    def document(self) -> dict[str, object]:
        return json.loads(self._encoded.decode("ascii"))

    def to_mapping(self) -> dict[str, object]:
        return self.document

    @property
    def sha256(self) -> str:
        return _sha256(self._encoded)

    @property
    def stage_builder_handoff_ready(self) -> bool:
        return self.document.get("status") == "dependency-evidence-complete"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _mapping_sha256(value: object) -> str:
    return _sha256(_canonical_json(value))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Music3DependencyClosureSecurityError("dependency input contains duplicate fields")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise Music3DependencyClosureSecurityError("dependency input contains a non-finite number")


def _bounded_plain_json(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise Music3DependencyClosureSecurityError("dependency input exceeds its structure bound")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        if len(value) > MAX_STRING_LENGTH:
            raise Music3DependencyClosureSecurityError("dependency input contains an oversized string")
        return
    if type(value) is list:
        if len(value) > max(MAX_PACKAGES, MAX_EDGES):
            raise Music3DependencyClosureSecurityError("dependency input contains an oversized list")
        for item in value:
            _bounded_plain_json(item, depth=depth + 1, nodes=nodes)
        return
    if type(value) is dict:
        if len(value) > MAX_PACKAGES:
            raise Music3DependencyClosureSecurityError("dependency input contains too many fields")
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 128:
                raise Music3DependencyClosureSecurityError("dependency input contains an invalid field")
            _bounded_plain_json(item, depth=depth + 1, nodes=nodes)
        return
    raise Music3DependencyClosureSecurityError("dependency input must contain plain JSON values")


def _load_canonical_input(payload: object) -> dict[str, Any]:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_INPUT_BYTES:
        raise Music3DependencyClosureSecurityError("dependency input bytes are outside their bound")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise Music3DependencyClosureSecurityError("dependency input must not contain a BOM")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except Music3DependencyClosureSecurityError:
        raise
    except (RecursionError, UnicodeError, ValueError) as error:
        raise Music3DependencyClosureSecurityError("dependency input JSON is invalid") from error
    _bounded_plain_json(value)
    if type(value) is not dict:
        raise Music3DependencyClosureSecurityError("dependency input must be one object")
    if payload != _canonical_json(value) + b"\n":
        raise Music3DependencyClosureSecurityError("dependency input is not canonical JSON")
    return value


def _exact_keys(value: object, keys: set[str], *, field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise Music3DependencyClosureError(f"{field} fields are not exact")
    return value


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise Music3DependencyClosureError(f"{field} must be one lowercase SHA-256 digest")
    return value


def _positive_size(value: object, *, field: str) -> int:
    if type(value) is not int or not 1 <= value <= 2 * 1024**4:
        raise Music3DependencyClosureError(f"{field} is outside its reviewed bound")
    return value


def _url(value: object, *, field: str) -> str:
    if (
        type(value) is not str
        or len(value) > MAX_STRING_LENGTH
        or _CANONICAL_HTTPS_URL.fullmatch(value) is None
        or "@" in value.partition("/")[2].partition("/")[0]
        or "?" in value
        or "#" in value
        or "//" in value.removeprefix("https://")
    ):
        raise Music3DependencyClosureError(f"{field} is not one canonical HTTPS URL")
    return value


def _normalize_package(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _exact_requirement(value: object, *, field: str) -> tuple[str, str]:
    if type(value) is not str or (match := _REQUIREMENT.fullmatch(value)) is None:
        raise Music3DependencyClosureError(f"{field} must be one exact pinned requirement")
    extras = match.group("extras")
    if extras is not None:
        parts = extras.split(",")
        if len(parts) != len(set(parts)) or parts != sorted(parts):
            raise Music3DependencyClosureError(f"{field} extras are not canonical")
    return _normalize_package(match.group("name")), match.group("version")


def _validate_target(value: object) -> dict[str, str]:
    target = _exact_keys(value, set(TARGET), field="dependency target")
    if target != TARGET:
        raise Music3DependencyClosureError("dependency target is not CPython 3.12 Linux x86_64 glibc 2.35")
    return dict(TARGET)


def _validate_python_artifact(value: object) -> dict[str, object]:
    artifact = _exact_keys(value, set(_PYTHON_ARTIFACT), field="Python artifact")
    if artifact != _PYTHON_ARTIFACT:
        raise Music3DependencyClosureError("Python artifact does not match the reviewed CPython release")
    _url(artifact["url"], field="Python artifact URL")
    _digest(artifact["sha256"], field="Python artifact digest")
    _positive_size(artifact["size"], field="Python artifact size")
    return dict(_PYTHON_ARTIFACT)


def _validate_cudart_artifact(value: object) -> dict[str, object]:
    artifact = _exact_keys(value, set(_CUDART_ARTIFACT), field="CUDART artifact")
    if artifact != _CUDART_ARTIFACT:
        raise Music3DependencyClosureError("CUDART artifact does not match the reviewed redistributable")
    _url(artifact["url"], field="CUDART artifact URL")
    _digest(artifact["sha256"], field="CUDART artifact digest")
    _positive_size(artifact["size"], field="CUDART artifact size")
    return dict(_CUDART_ARTIFACT)


def _validate_wheel_filename(filename: str, *, name: str, version: str) -> None:
    if not filename.endswith(".whl"):
        raise Music3DependencyClosureError("Python dependency artifacts must be wheels, never sdists")
    components = filename[:-4].split("-")
    if len(components) not in {5, 6}:
        raise Music3DependencyClosureError("wheel filename is not canonical")
    distribution, wheel_version = components[:2]
    if (
        _WHEEL_COMPONENT.fullmatch(distribution) is None
        or _WHEEL_COMPONENT.fullmatch(wheel_version) is None
        or (len(components) == 6 and _WHEEL_BUILD_TAG.fullmatch(components[2]) is None)
        or _normalize_package(distribution) != _normalize_package(name)
        or wheel_version.replace("_", "-") != version.replace("_", "-")
    ):
        raise Music3DependencyClosureError("wheel filename does not bind its package and version")
    python_tag, abi_tag, platform_tag = components[-3:]
    if any(_WHEEL_TAG.fullmatch(tag) is None for tag in (python_tag, abi_tag, platform_tag)):
        raise Music3DependencyClosureError("wheel tags are not canonical")
    python_tags = set(python_tag.split("."))
    abi_tags = set(abi_tag.split("."))
    pure = python_tags == {"py3"} and abi_tags == {"none"} and platform_tag == "any"
    exact = python_tags == {"cp312"} and abi_tags == {"cp312"}
    abi3 = abi_tags == {"abi3"} and any(
        (match := re.fullmatch(r"cp3([0-9]+)", tag)) is not None
        and 2 <= int(match.group(1)) <= 12
        for tag in python_tags
    )
    if pure:
        return
    if not (exact or abi3):
        raise Music3DependencyClosureError("wheel ABI is not compatible with reviewed CPython 3.12")
    for platform in platform_tag.split("."):
        if platform == "manylinux2014_x86_64":
            continue
        match = re.fullmatch(r"manylinux_2_([0-9]+)_x86_64", platform)
        if match is None or int(match.group(1)) > 35:
            raise Music3DependencyClosureError("wheel platform exceeds the reviewed glibc 2.35 target")


def _known_wheels() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, version, requirement, filename, url, digest, size in _KNOWN_WHEEL_ROWS:
        result[_normalize_package(name)] = {
            "name": name,
            "version": version,
            "requirement": requirement,
            "artifact_id": f"wheel-{_normalize_package(name)}-{version}",
            "filename": filename,
            "url": url,
            "sha256": f"sha256:{digest}",
            "size": size,
        }
    return result


def _validate_package(value: object) -> dict[str, object]:
    keys = {
        "name", "version", "requirement", "artifact_id", "filename", "url",
        "sha256", "size", "dependencies", "dependency_metadata_complete",
        "provenance", "build_source_sha256",
    }
    item = _exact_keys(value, keys, field="dependency package")
    name = item["name"]
    version = item["version"]
    requirement = item["requirement"]
    artifact_id = item["artifact_id"]
    filename = item["filename"]
    if type(name) is not str or _PACKAGE.fullmatch(name) is None:
        raise Music3DependencyClosureError("dependency package name is not canonical")
    if type(version) is not str or _VERSION.fullmatch(version) is None:
        raise Music3DependencyClosureError("dependency version is not one exact pin")
    required_name, required_version = _exact_requirement(requirement, field="package requirement")
    if required_name != _normalize_package(name) or required_version != version:
        raise Music3DependencyClosureError("package requirement does not match its name and version")
    if type(artifact_id) is not str or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise Music3DependencyClosureError("wheel artifact ID is invalid")
    if type(filename) is not str or len(filename) > 255:
        raise Music3DependencyClosureError("wheel filename is invalid")
    _validate_wheel_filename(filename, name=name, version=version)
    dependencies = item["dependencies"]
    if type(dependencies) is not list or len(dependencies) > MAX_PACKAGES:
        raise Music3DependencyClosureError("package dependencies exceed their bound")
    normalized_dependencies: list[str] = []
    dependency_names: set[str] = set()
    for dependency in dependencies:
        dependency_name, _dependency_version = _exact_requirement(
            dependency, field="transitive dependency",
        )
        if dependency_name == _normalize_package(name):
            raise Music3DependencyClosureError("dependency graph contains a self edge")
        if dependency_name in dependency_names:
            raise Music3DependencyClosureError("dependency graph contains duplicate edges")
        dependency_names.add(dependency_name)
        normalized_dependencies.append(dependency)
    if type(item["dependency_metadata_complete"]) is not bool:
        raise Music3DependencyClosureError("dependency metadata completeness must be explicit")
    provenance = item["provenance"]
    build_source = item["build_source_sha256"]
    if provenance == "index-wheel":
        if build_source is not None:
            raise Music3DependencyClosureError("index wheel must not claim source-build provenance")
    elif provenance == "source-built-wheel":
        _digest(build_source, field="source-built wheel input digest")
    else:
        raise Music3DependencyClosureError("wheel provenance is unsupported")
    artifact_url = _url(item["url"], field="wheel URL")
    if not artifact_url.endswith("/" + filename):
        raise Music3DependencyClosureError("wheel URL does not bind its artifact filename")
    normalized = {
        "name": name,
        "version": version,
        "requirement": requirement,
        "artifact_id": artifact_id,
        "filename": filename,
        "url": artifact_url,
        "sha256": _digest(item["sha256"], field="wheel digest"),
        "size": _positive_size(item["size"], field="wheel size"),
        "dependencies": sorted(normalized_dependencies),
        "dependency_metadata_complete": item["dependency_metadata_complete"],
        "provenance": provenance,
        "build_source_sha256": build_source,
    }
    known = _known_wheels().get(_normalize_package(name))
    if known is not None and any(normalized[key] != expected for key, expected in known.items()):
        raise Music3DependencyClosureError("known wheel metadata differs from reviewed primary-source evidence")
    return normalized


def _reject_duplicate_package_identity(values: list[object]) -> None:
    seen: dict[str, set[str]] = {
        "normalized packages": set(),
        "artifact_id": set(),
        "filename": set(),
        "sha256": set(),
    }
    for value in values:
        if type(value) is not dict:
            continue
        candidates = {
            "normalized packages": (
                _normalize_package(value["name"])
                if type(value.get("name")) is str
                else None
            ),
            "artifact_id": value.get("artifact_id"),
            "filename": value.get("filename"),
            "sha256": value.get("sha256"),
        }
        for field, candidate in candidates.items():
            if type(candidate) is not str:
                continue
            if candidate in seen[field]:
                raise Music3DependencyClosureError(
                    f"dependency closure contains duplicate {field}"
                )
            seen[field].add(candidate)


def _validate_source_blockers(value: object) -> list[dict[str, str]]:
    if type(value) is not list or len(value) > len(_SOURCE_BUILD_BLOCKERS):
        raise Music3DependencyClosureError("source-build blocker list is outside its bound")
    blockers: list[dict[str, str]] = []
    names: set[str] = set()
    expected = {item["name"]: item for item in _SOURCE_BUILD_BLOCKERS}
    for raw in value:
        item = _exact_keys(
            raw,
            {"name", "selected_version", "source_requirement", "reason"},
            field="source-build blocker",
        )
        name = item["name"]
        if type(name) is not str or name in names or item != expected.get(name):
            raise Music3DependencyClosureError("source-build blocker is not exact")
        names.add(name)
        blockers.append(dict(item))
    if names and names != set(expected):
        raise Music3DependencyClosureError("source-build blockers must be complete or resolved")
    return sorted(blockers, key=lambda item: item["name"])


def _validate_cuda_closure(value: object, packages: dict[str, dict[str, object]]) -> dict[str, object]:
    item = _exact_keys(
        value,
        {
            "status", "cudart_artifact", "required_components", "providers",
            "unresolved_components", "evidence_sha256",
        },
        field="CUDA closure",
    )
    cudart = _validate_cudart_artifact(item["cudart_artifact"])
    required = item["required_components"]
    unresolved = item["unresolved_components"]
    providers = item["providers"]
    if required != list(_CUDA_REQUIRED_COMPONENTS):
        raise Music3DependencyClosureError("CUDA required component set is not exact")
    if type(unresolved) is not list or any(component not in _CUDA_REQUIRED_COMPONENTS for component in unresolved):
        raise Music3DependencyClosureError("CUDA unresolved component set is invalid")
    if len(unresolved) != len(set(unresolved)) or unresolved != sorted(unresolved):
        raise Music3DependencyClosureError("CUDA unresolved components are not unique and sorted")
    if type(providers) is not list or len(providers) > len(_CUDA_REQUIRED_COMPONENTS):
        raise Music3DependencyClosureError("CUDA provider list is outside its bound")
    normalized_providers: list[dict[str, str]] = []
    provider_components: set[str] = set()
    provider_package_names: set[str] = set()
    for raw in providers:
        provider = _exact_keys(raw, {"component", "requirement"}, field="CUDA provider")
        component = provider["component"]
        requirement = provider["requirement"]
        if component not in _CUDA_REQUIRED_COMPONENTS or component in provider_components:
            raise Music3DependencyClosureError("CUDA provider component is invalid or duplicated")
        package_name, package_version = _exact_requirement(requirement, field="CUDA provider requirement")
        if package_name in provider_package_names:
            raise Music3DependencyClosureError(
                "each CUDA component must bind a distinct selected wheel"
            )
        package = packages.get(package_name)
        if package is None or package["version"] != package_version:
            raise Music3DependencyClosureError("CUDA provider is not bound to one selected wheel")
        if requirement != package["requirement"]:
            raise Music3DependencyClosureError(
                "CUDA provider requirement does not exactly match its selected wheel"
            )
        provider_components.add(component)
        provider_package_names.add(package_name)
        normalized_providers.append({"component": component, "requirement": requirement})
    status = item["status"]
    evidence = item["evidence_sha256"]
    if status == "unresolved":
        if (
            evidence is not None
            or provider_components & set(unresolved)
            or provider_components | set(unresolved) != set(_CUDA_REQUIRED_COMPONENTS)
        ):
            raise Music3DependencyClosureError("unresolved CUDA evidence is contradictory")
    elif status == "complete":
        _digest(evidence, field="CUDA closure evidence")
        if unresolved or provider_components != set(_CUDA_REQUIRED_COMPONENTS):
            raise Music3DependencyClosureError("CUDA wheel closure is incomplete")
    else:
        raise Music3DependencyClosureError("CUDA closure status is unsupported")
    return {
        "status": status,
        "cudart_artifact": cudart,
        "required_components": list(_CUDA_REQUIRED_COMPONENTS),
        "providers": sorted(normalized_providers, key=lambda provider: provider["component"]),
        "unresolved_components": list(unresolved),
        "evidence_sha256": evidence,
    }


def _validate_resolution(value: object) -> dict[str, object]:
    item = _exact_keys(
        value,
        {
            "transitive_complete", "resolver", "resolver_version", "report_sha256",
            "offline_replay_sha256",
        },
        field="resolver evidence",
    )
    if type(item["transitive_complete"]) is not bool:
        raise Music3DependencyClosureError("transitive completeness must be explicit")
    if item["transitive_complete"]:
        if item["resolver"] != "uv":
            raise Music3DependencyClosureError("complete closure must be produced by the reviewed resolver")
        if type(item["resolver_version"]) is not str or _VERSION.fullmatch(item["resolver_version"]) is None:
            raise Music3DependencyClosureError("resolver version is not exact")
        _digest(item["report_sha256"], field="resolver report digest")
        _digest(item["offline_replay_sha256"], field="offline replay digest")
    elif any(item[key] is not None for key in ("resolver", "resolver_version", "report_sha256", "offline_replay_sha256")):
        raise Music3DependencyClosureError("incomplete closure must not carry resolver completion evidence")
    return dict(item)


def _validate_graph(
    roots: object,
    packages: list[dict[str, object]],
    *,
    require_complete: bool,
) -> tuple[list[str], list[dict[str, str]]]:
    if type(roots) is not list or not 1 <= len(roots) <= MAX_PACKAGES:
        raise Music3DependencyClosureError("root requirement list is outside its bound")
    package_by_name = {_normalize_package(str(item["name"])): item for item in packages}
    normalized_roots: list[str] = []
    root_names: set[str] = set()
    for requirement in roots:
        name, version = _exact_requirement(requirement, field="root requirement")
        if name in root_names:
            raise Music3DependencyClosureError("root requirements contain duplicate packages")
        package = package_by_name.get(name)
        if package is None or package["version"] != version:
            raise Music3DependencyClosureError("root requirement is not bound to one selected wheel")
        root_names.add(name)
        normalized_roots.append(requirement)
    required_runtime = {
        _normalize_package(line.partition("==")[0].partition("[")[0]): line.partition("==")[2]
        for line in runtime.REQUIRED_RUNTIME_LOCK_LINES
    }
    if any(
        package_by_name.get(name, {}).get("version") != version
        for name, version in required_runtime.items()
    ):
        raise Music3DependencyClosureError("closure misses a runtime-required exact wheel")
    root_requirements = set(normalized_roots)
    if not runtime.REQUIRED_RUNTIME_LOCK_LINES.issubset(root_requirements):
        raise Music3DependencyClosureError("root closure misses a runtime-required exact requirement")
    if tuple(sorted(normalized_roots)) != _REVIEWED_ROOT_REQUIREMENTS:
        raise Music3DependencyClosureError("root requirement set is not the reviewed runtime root set")

    edges: list[dict[str, str]] = []
    adjacency: dict[str, list[str]] = {name: [] for name in package_by_name}
    for name, package in package_by_name.items():
        for requirement in package["dependencies"]:
            dependency_name, dependency_version = _exact_requirement(
                requirement, field="transitive dependency",
            )
            dependency = package_by_name.get(dependency_name)
            if dependency is None or dependency["version"] != dependency_version:
                raise Music3DependencyClosureError("transitive dependency is unresolved")
            adjacency[name].append(dependency_name)
            edges.append({"from": name, "to": dependency_name, "requirement": requirement})
    if len(edges) > MAX_EDGES:
        raise Music3DependencyClosureError("dependency graph exceeds its edge bound")

    state = {name: 0 for name in package_by_name}
    for root in sorted(root_names):
        if state[root] == 2:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            name, exiting = stack.pop()
            if exiting:
                state[name] = 2
                continue
            if state[name] == 1:
                raise Music3DependencyClosureError("dependency graph contains a cycle")
            if state[name] == 2:
                continue
            state[name] = 1
            stack.append((name, True))
            for dependency in reversed(adjacency[name]):
                if state[dependency] == 1:
                    raise Music3DependencyClosureError("dependency graph contains a cycle")
                if state[dependency] == 0:
                    stack.append((dependency, False))
    visited = {name for name, status in state.items() if status == 2}
    if require_complete and visited != set(package_by_name):
        raise Music3DependencyClosureError("dependency graph contains unreachable packages")
    return sorted(normalized_roots), sorted(
        edges,
        key=lambda edge: (edge["from"], edge["to"], edge["requirement"]),
    )


def _validated_input(value: object) -> dict[str, object]:
    document = _exact_keys(
        value,
        {
            "schema", "source_revision", "target", "python_artifact", "roots",
            "packages", "source_build_blockers", "cuda_closure", "resolution",
        },
        field="dependency input",
    )
    if document["schema"] != DEPENDENCY_INPUT_SCHEMA:
        raise Music3DependencyClosureError("dependency input schema is unsupported")
    if document["source_revision"] != runtime.PINNED_SGLANG_SOURCE_REVISION:
        raise Music3DependencyClosureError("SGLang-Omni source revision is not the reviewed commit")
    target = _validate_target(document["target"])
    python_artifact = _validate_python_artifact(document["python_artifact"])
    raw_packages = document["packages"]
    if type(raw_packages) is not list or not 1 <= len(raw_packages) <= MAX_PACKAGES:
        raise Music3DependencyClosureError("dependency package inventory is outside its bound")
    _reject_duplicate_package_identity(raw_packages)
    packages = [_validate_package(item) for item in raw_packages]
    by_name = {_normalize_package(str(item["name"])): item for item in packages}
    if len(by_name) != len(packages):
        raise Music3DependencyClosureError("dependency closure contains duplicate normalized packages")
    for field in ("artifact_id", "filename", "sha256"):
        if len({item[field] for item in packages}) != len(packages):
            raise Music3DependencyClosureError(f"dependency closure contains duplicate {field}")
    resolution = _validate_resolution(document["resolution"])
    roots, edges = _validate_graph(
        document["roots"],
        packages,
        require_complete=bool(resolution["transitive_complete"]),
    )
    blockers = _validate_source_blockers(document["source_build_blockers"])
    cuda = _validate_cuda_closure(document["cuda_closure"], by_name)

    source_blocker_versions = {
        _normalize_package(item["name"]): item["selected_version"]
        for item in _SOURCE_BUILD_BLOCKERS
    }
    if blockers:
        if set(by_name) & set(source_blocker_versions):
            raise Music3DependencyClosureError(
                "source-built wheel blocker contradicts a selected package"
            )
    else:
        for name, version in source_blocker_versions.items():
            package = by_name.get(name)
            if (
                package is None
                or package["version"] != version
                or package["provenance"] != "source-built-wheel"
            ):
                raise Music3DependencyClosureError("mandatory source-built wheel is unresolved")
    if resolution["transitive_complete"]:
        if any(not item["dependency_metadata_complete"] for item in packages):
            raise Music3DependencyClosureError("complete closure has incomplete transitive metadata")
        if cuda["status"] != "complete":
            raise Music3DependencyClosureError("complete dependency closure has unresolved CUDA runtime")

    return {
        "schema": DEPENDENCY_INPUT_SCHEMA,
        "source_revision": runtime.PINNED_SGLANG_SOURCE_REVISION,
        "target": target,
        "python_artifact": python_artifact,
        "roots": roots,
        "packages": sorted(packages, key=lambda item: _normalize_package(str(item["name"]))),
        "edges": edges,
        "source_build_blockers": blockers,
        "cuda_closure": cuda,
        "resolution": resolution,
    }


def _current_seed_mapping() -> dict[str, object]:
    known = _known_wheels()
    packages = []
    for item in known.values():
        packages.append({
            **item,
            "dependencies": [],
            "dependency_metadata_complete": False,
            "provenance": "index-wheel",
            "build_source_sha256": None,
        })
    return {
        "schema": DEPENDENCY_INPUT_SCHEMA,
        "source_revision": runtime.PINNED_SGLANG_SOURCE_REVISION,
        "target": dict(TARGET),
        "python_artifact": dict(_PYTHON_ARTIFACT),
        "roots": list(_REVIEWED_ROOT_REQUIREMENTS),
        "packages": sorted(packages, key=lambda item: _normalize_package(str(item["name"]))),
        "source_build_blockers": [dict(item) for item in _SOURCE_BUILD_BLOCKERS],
        "cuda_closure": {
            "status": "unresolved",
            "cudart_artifact": dict(_CUDART_ARTIFACT),
            "required_components": list(_CUDA_REQUIRED_COMPONENTS),
            "providers": [],
            "unresolved_components": list(_CUDA_REQUIRED_COMPONENTS),
            "evidence_sha256": None,
        },
        "resolution": {
            "transitive_complete": False,
            "resolver": None,
            "resolver_version": None,
            "report_sha256": None,
            "offline_replay_sha256": None,
        },
    }


def reviewed_music3_dependency_seed_bytes() -> bytes:
    """Return the exact current primary-source research seed as canonical JSON."""

    return _canonical_json(_current_seed_mapping()) + b"\n"


def build_music3_dependency_closure_plan(
    payload: object,
    *,
    expected_complete_input_sha256: object | None = None,
) -> Music3DependencyClosurePlan:
    """Validate canonical dependency evidence and return a non-executable plan."""

    loaded = _load_canonical_input(payload)
    value = _validated_input(loaded)
    input_sha256 = _mapping_sha256(value)
    independently_reviewed = False
    if expected_complete_input_sha256 is not None:
        expected = _digest(
            expected_complete_input_sha256,
            field="independently reviewed complete-input digest",
        )
        if not hmac.compare_digest(expected, input_sha256):
            raise Music3DependencyClosureSecurityError(
                "dependency input does not match the independently reviewed digest"
            )
        independently_reviewed = True
    packages = value["packages"]
    resolution = value["resolution"]
    cuda = value["cuda_closure"]
    blockers: list[str] = []
    if value["source_build_blockers"]:
        blockers.extend(
            f"source_built_wheel_missing:{item['name']}"
            for item in value["source_build_blockers"]
        )
    if not resolution["transitive_complete"] or any(
        not item["dependency_metadata_complete"] for item in packages
    ):
        blockers.append("complete_hashed_transitive_wheel_lock_missing")
    if cuda["status"] != "complete":
        blockers.append("full_torch_sglang_cuda_wheel_closure_missing")
    if not blockers and not independently_reviewed:
        blockers.append("independent_complete_input_review_missing")
    complete = not blockers and independently_reviewed
    requirements = sorted(str(item["requirement"]) for item in packages)
    dependency_lock_sha256 = _sha256(("\n".join(requirements) + "\n").encode("utf-8"))
    closure = {
        "source_revision": value["source_revision"],
        "target": value["target"],
        "python_artifact": value["python_artifact"],
        "roots": value["roots"],
        "packages": packages,
        "edges": value["edges"],
        "source_build_blockers": value["source_build_blockers"],
        "cuda_closure": cuda,
        "resolution": resolution,
    }
    wheel_lock = [
        {
            key: item[key]
            for key in (
                "name", "version", "requirement", "artifact_id", "filename", "sha256", "size",
            )
        }
        for item in packages
    ]
    document = {
        "schema": DEPENDENCY_PLAN_SCHEMA,
        "status": "dependency-evidence-complete" if complete else "blocked",
        "mutation": False,
        "installability_claimed": False,
        "stage_execution_authorized": False,
        "source_revision": value["source_revision"],
        "target": value["target"],
        "input_sha256": input_sha256,
        "independent_review_bound": independently_reviewed,
        "closure_sha256": _mapping_sha256(closure),
        "blockers": sorted(blockers),
        "python_artifact": value["python_artifact"],
        "roots": value["roots"],
        "packages": packages,
        "edges": value["edges"],
        "source_build_blockers": value["source_build_blockers"],
        "cuda_closure": cuda,
        "resolution": resolution,
        "stage_builder_handoff": {
            "compatible_input_schema": STAGE_BUILDER_INPUT_SCHEMA,
            "ready": complete,
            "requires_independent_manifest_review": True,
            "wheel_lock": wheel_lock,
            "dependency_lock_sha256": dependency_lock_sha256,
        },
    }
    return Music3DependencyClosurePlan._from_document(document)


def validate_complete_music3_dependency_closure(
    payload: object,
    *,
    expected_complete_input_sha256: object,
) -> Music3DependencyClosurePlan:
    """Reject anything short of complete dependency evidence for handoff review."""

    plan = build_music3_dependency_closure_plan(
        payload,
        expected_complete_input_sha256=expected_complete_input_sha256,
    )
    if not plan.stage_builder_handoff_ready:
        raise Music3DependencyClosureBlocked("Music 3 dependency evidence is incomplete")
    return plan


__all__ = [
    "DEPENDENCY_INPUT_SCHEMA",
    "DEPENDENCY_PLAN_SCHEMA",
    "Music3DependencyClosureBlocked",
    "Music3DependencyClosureError",
    "Music3DependencyClosurePlan",
    "Music3DependencyClosureSecurityError",
    "build_music3_dependency_closure_plan",
    "reviewed_music3_dependency_seed_bytes",
    "validate_complete_music3_dependency_closure",
]
