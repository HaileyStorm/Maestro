"""Read-only, server-owned selection of the isolated local H3 rewriter.

An optional owner-private file under storage opts into the installed runtime.
HTTP callers cannot supply this configuration. Snapshots contain private paths;
only their commitment may enter public job metadata. Loading performs no model,
coordinator, subprocess, or network work. Admission later verifies installed
bytes and probes metadata in the existing CUDA-masked isolated child.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = "maestro.h3-prompt-rewriter.host-config.v1"
_APP = Path(__file__).resolve().parents[1]
_MAX_BYTES = 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_GENERATION = re.compile(r"[0-9a-f]{32}")
_KEYS = {"schema", "enabled", "feature_root", "artifact_root", "asset_manifest",
         "asset_manifest_sha256", "coordinator_root", "project_id", "cuda_visible_devices"}


class H3PromptRewriterConfigurationError(RuntimeError):
    """Private configuration is missing, changed, or incompatible."""


def default_config_path() -> Path:
    selected = os.environ.get("MAESTRO_H3_PROMPT_REWRITER_CONFIG")
    return Path(selected) if selected else _APP / "storage/h3_prompt_rewriter.json"


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise H3PromptRewriterConfigurationError("Invalid private runtime commitment")
    return value


def _directory(value) -> Path:
    if type(value) is not str:
        raise H3PromptRewriterConfigurationError("An existing canonical runtime directory is required")
    path = Path(value)
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
        raise H3PromptRewriterConfigurationError("An existing canonical runtime directory is required")
    return path


def _document(path: Path, expected=None):
    if not path.is_absolute() or path.parent.resolve(strict=True) != path.parent:
        raise H3PromptRewriterConfigurationError("Private configuration must use a canonical path")
    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or before.st_size > _MAX_BYTES):
            raise H3PromptRewriterConfigurationError("Use a bounded owner-private runtime configuration")
        raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES or identity(before) != identity(os.fstat(stream.fileno())):
            raise H3PromptRewriterConfigurationError("Private configuration changed during observation")
    if identity(before) != identity(path.lstat()):
        raise H3PromptRewriterConfigurationError("Private configuration generation changed")
    digest = _sha(raw)
    if expected is not None and digest != _digest(expected):
        raise H3PromptRewriterConfigurationError("Private runtime input changed")
    def unique(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise H3PromptRewriterConfigurationError("Duplicate private configuration field")
            document[key] = value
        return document
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError()
        _canonical(value)
    except (ValueError, RecursionError) as error:
        raise H3PromptRewriterConfigurationError("Invalid private runtime document") from error
    return value, digest


@dataclass(frozen=True)
class H3PromptRewriterRuntimeSnapshot:
    """Immutable private selection; not a model execution or GPU authority."""

    _config_json: bytes
    _receipt_json: bytes
    _assets_json: bytes
    _inputs: tuple[tuple[Path, str], ...]
    _source_inputs: tuple[tuple[Path, str], ...]
    workspace: Path

    @property
    def commitment(self) -> str:
        return _sha(_canonical({"config": json.loads(self._config_json),
            "runtime_receipt": self._inputs[2][1], "asset_manifest": self._inputs[3][1],
            "source": [digest for _, digest in self._source_inputs],
            "workspace": str(self.workspace)}))

    def gpu_binding(self):
        from services.h3_prompt_rewriter_gpu_lease import H3GpuLeaseBinding
        config = json.loads(self._config_json)
        return H3GpuLeaseBinding(Path(config["coordinator_root"]), config["project_id"], self.workspace)

    def recheck(self) -> None:
        for path, digest in self._inputs:
            _document(path, digest)
        for path, digest in self._source_inputs:
            if _sha(path.read_bytes()) != digest:
                raise H3PromptRewriterConfigurationError("Qualified runtime source changed")

    def build_execution_admission(self, mode, *, cancel_check=None, child_reaped_observer=None):
        self.recheck()
        from services import h3_prompt_rewriter_runtime as runtime
        from services import h3_prompt_rewriter_dependency_closure as closure
        config, receipt = json.loads(self._config_json), json.loads(self._receipt_json)
        payload = closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes()
        dependency = closure.build_h3_prompt_rewriter_dependency_closure_plan(payload)
        assets = Path(config["artifact_root"])
        passive = runtime.build_h3_prompt_rewriter_runtime_admission(
            Path(config["feature_root"]), mode=mode, artifact_trust_root=assets,
            adapter_directory=assets / "adapter", base_directory=assets / "base",
            dependency_payload=payload, expected_dependency_input_sha256=dependency.document["input_sha256"],
            ambient_environment={})
        admission = runtime.build_h3_prompt_rewriter_execution_admission(passive,
            python_executable=receipt["python_executable"], interpreter_seal=receipt["interpreter_seal"],
            asset_seals=json.loads(self._assets_json)["asset_seals"],
            template_path=assets / "adapter/prompt_template.py", runtime_receipt_sha256=self._inputs[2][1],
            runtime_inventory=receipt["runtime_inventory"], device="cuda",
            cuda_visible_devices=config["cuda_visible_devices"], cancel_check=cancel_check,
            child_reaped_observer=child_reaped_observer)
        self.recheck()
        return admission


def load_runtime_snapshot(*, workspace: Path, config_path: Path | None = None):
    """Freeze the host selection before canonical Enhance admission.

    Absence/disabled state is unavailable, never a fallback to ordinary Enhance.
    Paths come exclusively from this owner-private server file. Exact interpreter,
    model roster and directory security are checked by execution admission.
    """
    try:
        path = default_config_path() if config_path is None else config_path
        config, config_sha = _document(path)
        if set(config) != _KEYS or config["schema"] != SCHEMA or config["enabled"] is not True:
            raise H3PromptRewriterConfigurationError("The local H3 rewriter is not configured")
        feature = _directory(config["feature_root"])
        assets = _directory(config["artifact_root"])
        _directory(config["coordinator_root"])
        workspace = _directory(str(workspace))
        if (feature.name != "h3-prompt-rewriter" or not isinstance(config["project_id"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", config["project_id"]) is None
                or type(config["cuda_visible_devices"]) is not str
                or re.fullmatch(r"[0-9]+", config["cuda_visible_devices"]) is None):
            raise H3PromptRewriterConfigurationError("Invalid local H3 runtime binding")
        manifest_path = Path(config["asset_manifest"])
        if not manifest_path.is_relative_to(assets):
            raise H3PromptRewriterConfigurationError("Asset manifest must belong to the configured private assets")
        pointer_path = feature / "state/current-runtime.json"
        pointer, pointer_sha = _document(pointer_path)
        if (set(pointer) != {"schema", "generation", "receipt_sha256"}
                or pointer["schema"] != "maestro.h3-prompt-rewriter.runtime-selection.v1"
                or type(pointer["generation"]) is not str or _GENERATION.fullmatch(pointer["generation"]) is None):
            raise H3PromptRewriterConfigurationError("Invalid installed H3 runtime selection")
        destination = feature / "generations" / pointer["generation"]
        receipt_path = destination / "runtime-receipt.json"
        receipt, receipt_sha = _document(receipt_path, pointer["receipt_sha256"])
        installer = _APP / "scripts/install_h3_prompt_rewriter_runtime.py"
        worker = _APP / "services/h3_prompt_rewriter_worker.py"
        source_inputs = tuple((member, _sha(member.read_bytes())) for member in (
            installer, worker, Path(__file__), _APP / "services/h3_prompt_rewriter_runtime.py",
            _APP / "services/h3_prompt_rewriter.py", _APP / "services/h3_prompt_rewriter_dependency_closure.py"))
        qualification = receipt.get("qualification")
        if (receipt.get("schema") != "maestro.h3-prompt-rewriter.runtime-installation.v1"
                or receipt.get("generation") != pointer["generation"]
                or receipt.get("python_executable") != str(destination / "venv/bin/python")
                or receipt.get("python_version") != "3.12.14"
                or type(qualification) is not dict
                or set(qualification) != {"consistency_checked", "cuda_masked", "model_free"}
                or any(value is not True for value in qualification.values())
                or receipt.get("source_sha256") != source_inputs[0][1]
                or receipt.get("worker_sha256") != source_inputs[1][1]
                or receipt.get("model_execution_accepted") is not False
                or receipt.get("gpu_execution_accepted") is not False):
            raise H3PromptRewriterConfigurationError("Installed H3 runtime is incompatible with this server")
        manifest, manifest_sha = _document(manifest_path, config["asset_manifest_sha256"])
        if manifest.get("private_root") != str(assets) or type(manifest.get("asset_seals")) is not dict:
            raise H3PromptRewriterConfigurationError("Private asset manifest binding differs")
        snapshot = H3PromptRewriterRuntimeSnapshot(_canonical(config), _canonical(receipt), _canonical(manifest),
            ((path, config_sha), (pointer_path, pointer_sha), (receipt_path, receipt_sha), (manifest_path, manifest_sha)),
            source_inputs, workspace)
        # A stable final pass prevents mixing concurrently replaced selections.
        snapshot.recheck()
        return snapshot
    except H3PromptRewriterConfigurationError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise H3PromptRewriterConfigurationError("Local H3 runtime configuration is unavailable") from error
