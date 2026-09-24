"""Exact local artifact identity for the FLUX.2 Klein Character Sheet LoRA.

The LoRA is an attachment to the native ``flux2_klein_9b`` image model, not
a model type of its own. Keep its source pin and installed-file check separate
from prompt planning and from the WGP model registry.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


QUAD_FLUX_RECIPE_ID = "character_sheet_quad_flux2_klein_9b"
QUAD_FLUX_BASE_MODEL = "flux2_klein_9b"
QUAD_FLUX_LORA_REPOSITORY = "Alissonerdx/CharacterSheet"
QUAD_FLUX_LORA_REVISION = "3dc4295163dacc924d213168d67bf16850fd954f"
QUAD_FLUX_LORA_FILENAME = "QuadView_klein9b_v1.safetensors"
QUAD_FLUX_LORA_SIZE = 331379560
QUAD_FLUX_LORA_SHA256 = (
    "d05d84e1dfcfffa8b099e562a11b0e26720a982b0dad0f872289ce5945c75d71"
)


class QuadLoraArtifactError(ValueError):
    """The selected Quad LoRA is missing, altered, or bound to the wrong base."""


def quad_lora_name_matches(name: object) -> bool:
    """Recognize case-only aliases on case-insensitive filesystems too."""
    return os.path.basename(str(name).replace("\\", "/")).casefold() == QUAD_FLUX_LORA_FILENAME.casefold()


def require_quad_lora_base(model_type: str) -> None:
    if model_type != QUAD_FLUX_BASE_MODEL:
        raise QuadLoraArtifactError(
            "Quad Character Sheet requires the FLUX.2 Klein 9B image model."
        )


def require_quad_lora_artifact(path: str | os.PathLike[str]) -> None:
    """Hash the on-disk file before native loading, including pre-existing files."""
    candidate = Path(path)
    try:
        with candidate.open("rb") as source:
            before = os.fstat(source.fileno())
            if before.st_size != QUAD_FLUX_LORA_SIZE:
                raise QuadLoraArtifactError(
                    "Quad Character Sheet LoRA has an unexpected file size."
                )
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(source.fileno())
    except OSError as exc:
        raise QuadLoraArtifactError(
            "Quad Character Sheet LoRA is unavailable on this host."
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise QuadLoraArtifactError(
            "Quad Character Sheet LoRA changed while it was being verified."
        )
    if digest.hexdigest() != QUAD_FLUX_LORA_SHA256:
        raise QuadLoraArtifactError(
            "Quad Character Sheet LoRA failed its published SHA-256 check."
        )
