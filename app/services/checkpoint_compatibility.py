"""Compatibility gates for user-imported CivitAI checkpoints.

Maestro can reuse a downloaded checkpoint only when its transformer matches a
pipeline architecture that Maestro already implements.  CivitAI's generic
``Checkpoint`` type also contains SDXL and many other model families that
Maestro cannot load through the WanGP finetune mechanism.  This module keeps
that distinction explicit and validates SafeTensor shapes without loading any
weights into RAM or VRAM.

The code deliberately uses only Python's standard library so it can run before
WanGP (and therefore torch) is imported during startup.
"""

from __future__ import annotations

from dataclasses import dataclass
import glob
import json
import os
import struct
from typing import Callable, Iterable


class CheckpointCompatibilityError(ValueError):
    """Raised when a checkpoint cannot be loaded by the selected pipeline."""


@dataclass(frozen=True)
class CheckpointTarget:
    architecture: str
    template_model_type: str
    name: str

    def as_dict(self, family: str = "") -> dict:
        return {
            "architecture": self.architecture,
            "template_model_type": self.template_model_type,
            "name": self.name,
            "family": family,
        }


# This is intentionally an allowlist.  A CivitAI base that is absent here is
# not silently treated as the first model in Maestro's architecture list.
# Additional families can be enabled once their checkpoint layout is verified
# against Maestro's loader.
_CHECKPOINT_TARGETS: dict[str, tuple[CheckpointTarget, ...]] = {
    "flux.1 d": (
        CheckpointTarget("flux", "flux", "Flux 1 Dev"),
    ),
    "flux.1 s": (
        CheckpointTarget("flux_schnell", "flux_schnell", "Flux 1 Schnell"),
    ),
    "flux.1 krea": (
        CheckpointTarget("flux", "flux_krea", "Flux 1 Krea"),
    ),
    "flux.1 kontext": (
        CheckpointTarget(
            "flux_dev_kontext", "flux_dev_kontext", "Flux 1 Kontext"
        ),
    ),
    "flux.2 d": (
        CheckpointTarget("flux2_dev", "flux2_dev", "Flux 2 Dev"),
    ),
    "flux.2 klein 4b": (
        CheckpointTarget(
            "flux2_klein_4b", "flux2_klein_4b", "Flux 2 Klein 4B"
        ),
    ),
    "flux.2 klein 4b-base": (
        CheckpointTarget(
            "flux2_klein_4b", "flux2_klein_base_4b", "Flux 2 Klein Base 4B"
        ),
    ),
    "flux.2 klein 9b": (
        CheckpointTarget(
            "flux2_klein_9b", "flux2_klein_9b", "Flux 2 Klein 9B"
        ),
    ),
    "flux.2 klein 9b-base": (
        CheckpointTarget(
            "flux2_klein_9b", "flux2_klein_base_9b", "Flux 2 Klein Base 9B"
        ),
    ),
    # CivitAI calls LTX-2.0 ``LTXV2`` and LTX-2.3 ``LTXV 2.3``.  They are
    # different Maestro architectures despite the nearly identical labels.
    "ltxv2": (
        CheckpointTarget("ltx2_19B", "ltx2_19B", "LTX-2.0 19B"),
    ),
    "ltxv 2.3": (
        CheckpointTarget("ltx2_22B", "ltx2_22B", "LTX-2.3 22B"),
    ),
    # CivitAI currently does not distinguish RAW from Turbo in baseModel.
    # Both share the same tensor layout, so require an explicit user choice
    # instead of guessing the sampling schedule.
    "krea 2": (
        CheckpointTarget("krea2_raw", "krea2_raw", "Krea 2 RAW"),
        CheckpointTarget("krea2_turbo", "krea2_turbo", "Krea 2 Turbo"),
    ),
    "qwen": (
        CheckpointTarget("qwen_image_20B", "qwen_image_20B", "Qwen Image 20B"),
    ),
    "zimageturbo": (
        CheckpointTarget("z_image", "z_image", "Z-Image Turbo"),
    ),
}


def _base_key(base_model: str) -> str:
    return " ".join(str(base_model or "").strip().casefold().split())


def checkpoint_targets_for_base(base_model: str) -> tuple[CheckpointTarget, ...]:
    """Return only the verified Maestro targets for a CivitAI base label."""

    return _CHECKPOINT_TARGETS.get(_base_key(base_model), ())


def suggested_checkpoint_architecture(base_model: str) -> str | None:
    """Auto-select only when CivitAI maps to exactly one verified target."""

    targets = checkpoint_targets_for_base(base_model)
    return targets[0].architecture if len(targets) == 1 else None


def checkpoint_template_model_type(
    base_model: str, target_architecture: str
) -> str | None:
    """Resolve the exact defaults template for an allowed base/architecture."""

    matches = [
        target.template_model_type
        for target in checkpoint_targets_for_base(base_model)
        if target.architecture == target_architecture
    ]
    return matches[0] if len(matches) == 1 else None


def unsupported_checkpoint_reason(base_model: str) -> str:
    label = str(base_model or "").strip()
    if not label:
        return (
            "CivitAI did not identify this checkpoint's base model, so Maestro "
            "cannot safely choose a compatible pipeline."
        )
    if label.casefold().startswith("sdxl") or "stable diffusion xl" in label.casefold():
        return (
            f"{label} checkpoints are not supported by Maestro's current image "
            "pipelines. Use a verified Flux, Krea 2, Qwen Image, or Z-Image "
            "checkpoint instead."
        )
    return (
        f"Maestro does not yet have a verified checkpoint-import pipeline for "
        f"CivitAI base '{label}'. The file was not assigned to another model "
        "family because that would fail during generation."
    )


def ensure_allowed_checkpoint_target(base_model: str, target_architecture: str) -> None:
    targets = checkpoint_targets_for_base(base_model)
    if not targets:
        raise CheckpointCompatibilityError(unsupported_checkpoint_reason(base_model))
    allowed = {target.architecture for target in targets}
    if target_architecture not in allowed:
        expected = ", ".join(sorted(allowed))
        raise CheckpointCompatibilityError(
            f"CivitAI identifies this checkpoint as '{base_model}', which is "
            f"compatible with {expected}, not '{target_architecture}'."
        )


def read_safetensors_header(path: str) -> dict:
    """Read only a SafeTensor's JSON header; tensor payloads stay untouched."""

    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as handle:
            raw_length = handle.read(8)
            if len(raw_length) != 8:
                raise CheckpointCompatibilityError("SafeTensor header is truncated")
            header_length = struct.unpack("<Q", raw_length)[0]
            # A legitimate header is tiny relative to multi-GB weights.  Keep a
            # hard ceiling so a corrupt first 8 bytes cannot allocate gigabytes.
            if header_length < 2 or header_length > min(256 * 1024 * 1024, file_size - 8):
                raise CheckpointCompatibilityError(
                    f"invalid SafeTensor header length ({header_length} bytes)"
                )
            header = json.loads(handle.read(header_length))
    except CheckpointCompatibilityError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CheckpointCompatibilityError(f"could not read SafeTensor header: {exc}") from exc
    if not isinstance(header, dict):
        raise CheckpointCompatibilityError("SafeTensor header is not a tensor index")
    payload_size = file_size - 8 - header_length
    dtype_sizes = {
        "BOOL": 1,
        "U8": 1,
        "I8": 1,
        "F8_E4M3": 1,
        "F8_E5M2": 1,
        "F8_E8M0": 1,
        "I16": 2,
        "U16": 2,
        "F16": 2,
        "BF16": 2,
        "I32": 4,
        "U32": 4,
        "F32": 4,
        "I64": 8,
        "U64": 8,
        "F64": 8,
    }
    ranges: list[tuple[int, int, str]] = []
    for key, value in header.items():
        if key == "__metadata__":
            continue
        if not isinstance(value, dict):
            raise CheckpointCompatibilityError(
                f"SafeTensor entry {key!r} is not a tensor descriptor"
            )
        offsets = value.get("data_offsets")
        shape = value.get("shape")
        dtype = value.get("dtype")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(type(offset) is int for offset in offsets)
            or offsets[0] < 0
            or offsets[1] < offsets[0]
            or offsets[1] > payload_size
        ):
            raise CheckpointCompatibilityError(
                f"SafeTensor entry {key!r} points outside the downloaded payload"
            )
        if (
            not isinstance(shape, list)
            or not all(type(dimension) is int and dimension >= 0 for dimension in shape)
            or dtype not in dtype_sizes
        ):
            raise CheckpointCompatibilityError(
                f"SafeTensor entry {key!r} has an invalid dtype or shape"
            )
        element_count = 1
        for dimension in shape:
            element_count *= dimension
        expected_size = element_count * dtype_sizes[dtype]
        if offsets[1] - offsets[0] != expected_size:
            raise CheckpointCompatibilityError(
                f"SafeTensor entry {key!r} has an inconsistent byte range"
            )
        ranges.append((offsets[0], offsets[1], key))
    cursor = 0
    for start, end, key in sorted(ranges):
        if start != cursor:
            raise CheckpointCompatibilityError(
                f"SafeTensor entry {key!r} has an overlapping or gapped byte range"
            )
        cursor = end
    if cursor != payload_size:
        raise CheckpointCompatibilityError(
            "SafeTensor tensor ranges do not cover the downloaded payload"
        )
    return header


_PREFIXES = (
    "_orig_mod.",
    "model.diffusion_model.",
    "diffusion_model.",
    "model.transformer.",
    "transformer.",
    "model.",
    "module.",
)


def _canonical_tensor_key(key: str) -> str:
    canonical = str(key)
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if canonical.startswith(prefix):
                canonical = canonical[len(prefix):]
                changed = True
                break
    if canonical.endswith("._data"):
        canonical = canonical[:-6]
    return canonical


def _tensor_shapes(header: dict) -> dict[str, tuple[int, ...]]:
    shapes: dict[str, tuple[int, ...]] = {}
    priorities: dict[str, int] = {}
    for raw_key, value in header.items():
        if raw_key == "__metadata__" or not isinstance(value, dict):
            continue
        if raw_key.endswith(("._scale", ".input_scale", ".output_scale")):
            continue
        shape = value.get("shape")
        if not isinstance(shape, list) or not all(isinstance(dim, int) for dim in shape):
            continue
        canonical = _canonical_tensor_key(raw_key)
        priority = 2 if raw_key.endswith("._data") else 1
        if priority >= priorities.get(canonical, -1):
            priorities[canonical] = priority
            shapes[canonical] = tuple(shape)
    return shapes


# Each rule is (alternative key suffixes, expected shape).  Multiple anchors
# prevent unrelated models with one coincidentally matching projection from
# passing.  Key suffix matching supports common CivitAI wrappers such as
# ``model.diffusion_model.`` and Quanto's ``weight._data`` representation.
_SIGNATURES: dict[str, tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]] = {
    "flux": (
        (("img_in.weight",), (3072, 64)),
        (("txt_in.weight",), (3072, 4096)),
        (("double_blocks.0.img_attn.qkv.weight",), (9216, 3072)),
    ),
    "flux_schnell": (
        (("img_in.weight",), (3072, 64)),
        (("txt_in.weight",), (3072, 4096)),
        (("double_blocks.0.img_attn.qkv.weight",), (9216, 3072)),
    ),
    "flux_dev_kontext": (
        (("img_in.weight",), (3072, 64)),
        (("txt_in.weight",), (3072, 4096)),
        (("double_blocks.0.img_attn.qkv.weight",), (9216, 3072)),
    ),
    "flux2_dev": (
        (("img_in.weight",), (6144, 128)),
        (("txt_in.weight",), (6144, 15360)),
        (("double_blocks.0.img_attn.qkv.weight",), (18432, 6144)),
    ),
    "flux2_klein_4b": (
        (("img_in.weight",), (3072, 128)),
        (("txt_in.weight",), (3072, 7680)),
        (("double_blocks.0.img_attn.qkv.weight",), (9216, 3072)),
    ),
    "flux2_klein_9b": (
        (("img_in.weight",), (4096, 128)),
        (("txt_in.weight",), (4096, 12288)),
        (("double_blocks.0.img_attn.qkv.weight",), (12288, 4096)),
    ),
    "ltx2_19B": (
        (("patchify_proj.weight",), (4096, 128)),
        (("transformer_blocks.0.attn1.to_q.weight",), (4096, 4096)),
        (("adaln_single.emb.timestep_embedder.linear_1.weight",), (4096, 256)),
    ),
    "ltx2_22B": (
        (("patchify_proj.weight",), (4096, 128)),
        (("transformer_blocks.0.attn1.to_q.weight",), (4096, 4096)),
        (("adaln_single.emb.timestep_embedder.linear_1.weight",), (4096, 256)),
    ),
    "krea2_raw": (
        (("first.weight",), (6144, 64)),
        (("blocks.0.attn.wq.weight",), (6144, 6144)),
        (("blocks.0.attn.wk.weight",), (1536, 6144)),
    ),
    "krea2_turbo": (
        (("first.weight",), (6144, 64)),
        (("blocks.0.attn.wq.weight",), (6144, 6144)),
        (("blocks.0.attn.wk.weight",), (1536, 6144)),
    ),
    "qwen_image_20B": (
        (("img_in.weight",), (3072, 64)),
        (("transformer_blocks.0.attn.to_q.weight",), (3072, 3072)),
    ),
    "z_image": (
        (("x_embedder.weight", "all_x_embedder.2-1.weight"), (3840, 64)),
        (("cap_embedder.1.weight",), (3840, 2560)),
    ),
}


def _find_shape(
    shapes: dict[str, tuple[int, ...]], alternatives: Iterable[str]
) -> tuple[str | None, tuple[int, ...] | None]:
    for expected_key in alternatives:
        for key, shape in shapes.items():
            if key == expected_key or key.endswith("." + expected_key):
                return key, shape
    return None, None


def detect_checkpoint_architectures(path: str) -> list[str]:
    """Return every verified architecture signature matched by ``path``."""

    shapes = _tensor_shapes(read_safetensors_header(path))
    matches: list[str] = []
    for architecture, rules in _SIGNATURES.items():
        if all(_find_shape(shapes, keys)[1] == expected for keys, expected in rules):
            matches.append(architecture)
    return matches


def validate_checkpoint_file(
    path: str,
    base_model: str,
    target_architecture: str,
    *,
    filename: str | None = None,
) -> dict:
    """Validate metadata mapping and transformer tensor layout.

    Returns a small serializable receipt that can be stored in the sidecar and
    finetune definition.  Raises before registration on any mismatch.
    """

    ensure_allowed_checkpoint_target(base_model, target_architecture)
    extension = os.path.splitext(filename or path)[1].casefold()
    if extension not in {".safetensors", ".sft"}:
        raise CheckpointCompatibilityError(
            "Maestro checkpoint import currently supports SafeTensor files only."
        )

    matches = detect_checkpoint_architectures(path)
    if target_architecture not in matches:
        if matches:
            detected = ", ".join(sorted(matches))
            raise CheckpointCompatibilityError(
                f"Checkpoint tensor layout matches {detected}, not the selected "
                f"{target_architecture} pipeline. It cannot be registered."
            )
        raise CheckpointCompatibilityError(
            f"Checkpoint tensors do not match Maestro's verified "
            f"{target_architecture} layout. It may be a full Diffusers bundle, "
            "an unsupported architecture, or a mislabeled upload. It cannot be registered."
        )
    return {
        "status": "verified",
        "architecture": target_architecture,
        "base_model": str(base_model or ""),
        "matched_layouts": sorted(matches),
        "signature_version": 1,
    }


_QUARANTINE_KEY = "maestro_checkpoint_quarantine"
_QUARANTINE_SCHEMA_VERSION = 2


def _field_snapshot(container: dict, key: str) -> dict:
    """Capture presence separately so rollback can restore an absent field."""

    return {
        "present": key in container,
        "value": container.get(key),
    }


def _restore_field(container: dict, key: str, snapshot: dict) -> None:
    if snapshot.get("present"):
        container[key] = snapshot.get("value")
    else:
        container.pop(key, None)


def _new_quarantine_marker(model: dict, civitai: dict) -> dict:
    return {
        "schema_version": _QUARANTINE_SCHEMA_VERSION,
        "previous": {
            "visible": _field_snapshot(model, "visible"),
            "compatibility_status": _field_snapshot(
                civitai, "compatibility_status"
            ),
            "compatibility_reason": _field_snapshot(
                civitai, "compatibility_reason"
            ),
            # The compatibility receipt carries its signature version. Keep
            # the whole prior value so a successful later audit restores the
            # exact provenance instead of silently manufacturing a new one.
            "compatibility": _field_snapshot(civitai, "compatibility"),
        },
    }


def _upgrade_quarantine_marker(marker: dict, model: dict, civitai: dict) -> dict:
    """Upgrade the historical visibility-only marker without losing intent."""

    if marker.get("schema_version") == _QUARANTINE_SCHEMA_VERSION and isinstance(
        marker.get("previous"), dict
    ):
        return dict(marker)
    previous_visible = bool(marker.get("previous_visible", True))
    return {
        "schema_version": _QUARANTINE_SCHEMA_VERSION,
        "previous": {
            "visible": {"present": True, "value": previous_visible},
            # Historical quarantine created these fields, so an old marker
            # means they were absent before the block was applied.
            "compatibility_status": {"present": False, "value": None},
            "compatibility_reason": {"present": False, "value": None},
            "compatibility": _field_snapshot(civitai, "compatibility"),
        },
    }


def _definition_compatibility(
    model: dict,
    checkpoint_root: str,
    resolve_checkpoint: Callable[[str], str | None] | None = None,
) -> tuple[bool, str, bool]:
    civitai = model.get("civitai")
    if not isinstance(civitai, dict) or civitai.get("modelType") != "Checkpoint":
        return True, "", False
    base_model = str(civitai.get("baseModel") or "")
    architecture = str(model.get("architecture") or "")
    try:
        ensure_allowed_checkpoint_target(base_model, architecture)
        filename = str(civitai.get("filename") or "")
        if not filename:
            raise CheckpointCompatibilityError(
                "Checkpoint registration does not identify a weight file."
            )
        if (
            filename != os.path.basename(filename)
            or "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
        ):
            raise CheckpointCompatibilityError(
                "Checkpoint provenance filename is not a safe local component."
            )
        if os.path.splitext(filename)[1].casefold() not in {
            ".safetensors",
            ".sft",
        }:
            raise CheckpointCompatibilityError(
                "Legacy checkpoint registration is not a SafeTensor file and "
                "cannot be verified. Its weights were preserved."
            )
        urls = model.get("URLs")
        if not isinstance(urls, list) or len(urls) != 1:
            raise CheckpointCompatibilityError(
                "Checkpoint definition does not declare exactly one local weight."
            )
        declared = urls[0]
        if not isinstance(declared, str) or declared != filename:
            raise CheckpointCompatibilityError(
                "Checkpoint definition weight does not match its verified "
                "CivitAI provenance."
            )
        try:
            candidate = (
                resolve_checkpoint(filename)
                if resolve_checkpoint is not None
                else os.path.join(checkpoint_root, filename)
            )
        except Exception as exc:
            raise CheckpointCompatibilityError(
                "Checkpoint lookup failed. The definition remains hidden."
            ) from exc
        if not candidate or not os.path.isfile(candidate):
            raise CheckpointCompatibilityError(
                "Checkpoint weight is unavailable. The definition remains "
                "hidden until a later audit can verify it."
            )
        validate_checkpoint_file(candidate, base_model, architecture)
        return True, "", True
    except CheckpointCompatibilityError as exc:
        return False, str(exc), True


def quarantine_incompatible_checkpoint_definitions(
    app_dir: str,
    *,
    checkpoint_root: str | None = None,
    resolve_checkpoint: Callable[[str], str | None] | None = None,
) -> list[dict]:
    """Hide unsafe legacy CivitAI imports before WanGP builds its model list.

    The checkpoint itself is never deleted.  A marker records the prior
    visibility so a future Maestro update that adds verified support can safely
    restore it.  Valid definitions are left untouched.
    """

    finetunes_dir = os.path.join(app_dir, "finetunes")
    checkpoint_root = checkpoint_root or os.path.join(app_dir, "ckpts")
    changes: list[dict] = []
    for path in glob.glob(os.path.join(finetunes_dir, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                definition = json.load(handle)
        except Exception:
            continue
        model = definition.get("model")
        if not isinstance(model, dict):
            continue
        civitai = model.get("civitai")
        if not isinstance(civitai, dict) or civitai.get("modelType") != "Checkpoint":
            continue

        compatible, reason, verified = _definition_compatibility(
            model,
            checkpoint_root,
            resolve_checkpoint,
        )
        marker = model.get(_QUARANTINE_KEY)
        changed = False
        if not compatible:
            if isinstance(marker, dict):
                marker = _upgrade_quarantine_marker(marker, model, civitai)
            else:
                marker = _new_quarantine_marker(model, civitai)
            marker["reason"] = reason
            marker["signature_version"] = 1
            if model.get(_QUARANTINE_KEY) != marker:
                model[_QUARANTINE_KEY] = marker
                changed = True
            if model.get("visible", True) is not False:
                model["visible"] = False
                changed = True
            if civitai.get("compatibility_status") != "blocked":
                civitai["compatibility_status"] = "blocked"
                changed = True
            if civitai.get("compatibility_reason") != reason:
                civitai["compatibility_reason"] = reason
                changed = True
        elif isinstance(marker, dict) and verified:
            marker = _upgrade_quarantine_marker(marker, model, civitai)
            previous = marker["previous"]
            _restore_field(model, "visible", previous["visible"])
            _restore_field(
                civitai,
                "compatibility_status",
                previous["compatibility_status"],
            )
            _restore_field(
                civitai,
                "compatibility_reason",
                previous["compatibility_reason"],
            )
            _restore_field(
                civitai,
                "compatibility",
                previous["compatibility"],
            )
            model.pop(_QUARANTINE_KEY, None)
            changed = True

        if not changed:
            continue
        temporary = f"{path}.maestro-{os.getpid()}-{id(definition)}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(definition, handle, indent=4)
            os.replace(temporary, path)
            changes.append({
                "model_type": os.path.basename(path)[:-5],
                "compatible": compatible,
                "applied": True,
                "reason": reason,
            })
        except OSError as exc:
            changes.append({
                "model_type": os.path.basename(path)[:-5],
                "compatible": compatible,
                "applied": False,
                "reason": reason,
                # Do not expose a host path through startup or API errors.
                "error": f"atomic definition update failed ({type(exc).__name__})",
            })
        finally:
            if os.path.isfile(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
    return changes
