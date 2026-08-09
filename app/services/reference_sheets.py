"""Deterministic, content-neutral reference-sheet planning and composition.

This module intentionally owns neither HTTP routing nor project persistence.
Callers inject generation, image-edit, and VLM operations and persist only the
bounded metadata returned by :meth:`ReferenceSheetResult.public_metadata` and
``ReferenceSheetArtifact.public_metadata``.  Creative requests and filesystem
paths remain transient.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

SCHEMA_VERSION = 1
PLANNER_VERSION = "reference-sheet-v1"
MODES = frozenset({"production", "hybrid", "draft"})
ASSET_TYPES = frozenset({"character", "setting", "item", "style"})

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_REASON_CODES = frozenset({
    "identity_mismatch",
    "request_mismatch",
    "view_mismatch",
    "accessory_mismatch",
    "style_mismatch",
})
_CHECK_NAMES = ("identity", "request", "view", "accessory", "style")
_REASON_FOR_CHECK = {
    "identity": "identity_mismatch",
    "request": "request_mismatch",
    "view": "view_mismatch",
    "accessory": "accessory_mismatch",
    "style": "style_mismatch",
}


class ReferenceSheetError(RuntimeError):
    """Base error for reference-sheet operations."""


class ReferenceSheetStructureError(ReferenceSheetError, ValueError):
    """Raised when generated image artifacts cannot form a valid sheet."""

    def __init__(
        self,
        reason_code: str,
        *,
        failed_roles: Sequence[str] = (),
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.failed_roles = tuple(failed_roles)


class ReferenceSheetReviewError(ReferenceSheetError, ValueError):
    """Raised when a semantic-review response violates the strict schema."""


@dataclass(frozen=True)
class PanelRecipe:
    role: str
    label: str
    objective: str


# Order is part of the versioned contract.  The first role is the Hybrid anchor.
ROLE_RECIPES: Mapping[str, tuple[PanelRecipe, ...]] = MappingProxyType({
    "character": (
        PanelRecipe("identity_front", "IDENTITY / FRONT", "unobstructed identity anchor"),
        PanelRecipe("three_quarter", "THREE-QUARTER", "three-quarter identity view"),
        PanelRecipe("profile", "PROFILE", "clean side profile"),
        PanelRecipe("full_body", "FULL BODY", "head-to-toe proportions and silhouette"),
        PanelRecipe("expression", "EXPRESSION", "requested expression and facial detail"),
        PanelRecipe("accessory_detail", "ACCESSORY DETAIL", "requested wardrobe or accessory detail"),
    ),
    "setting": (
        PanelRecipe("establishing", "ESTABLISHING", "wide spatial anchor"),
        PanelRecipe("reverse_angle", "REVERSE ANGLE", "opposing view with matching geography"),
        PanelRecipe("mid_view", "MID VIEW", "human-scale spatial relationships"),
        PanelRecipe("detail", "DETAIL", "materials, props, and local texture"),
        PanelRecipe("lighting", "LIGHTING", "requested lighting behavior"),
    ),
    "item": (
        PanelRecipe("hero_view", "HERO VIEW", "primary identifying view"),
        PanelRecipe("side_view", "SIDE VIEW", "orthogonal silhouette"),
        PanelRecipe("rear_view", "REAR VIEW", "rear construction and silhouette"),
        PanelRecipe("detail", "DETAIL", "functional material or mechanism detail"),
        PanelRecipe("scale_context", "SCALE CONTEXT", "requested scale relationship"),
    ),
    "style": (
        PanelRecipe("keyframe", "KEYFRAME", "primary style anchor"),
        PanelRecipe("composition", "COMPOSITION", "composition language"),
        PanelRecipe("lighting", "LIGHTING", "lighting and contrast language"),
        PanelRecipe("material", "MATERIAL", "surface and rendering language"),
        PanelRecipe("motion", "MOTION", "motion and camera language"),
    ),
})


@dataclass(frozen=True)
class ReferenceSheetPlan:
    schema_version: int
    planner_version: str
    mode: str
    asset_type: str
    creative_request: str
    model: str
    panels: tuple[PanelRecipe, ...]
    panel_size: tuple[int, int]
    draft_size: tuple[int, int]
    columns: int
    palette_swatches: int

    @property
    def panel_roles(self) -> tuple[str, ...]:
        return tuple(panel.role for panel in self.panels)


@dataclass(frozen=True)
class PanelGenerationRequest:
    schema_version: int
    planner_version: str
    mode: str
    asset_type: str
    creative_request: str
    model: str
    role: str
    label: str
    objective: str
    index: int
    panel_count: int
    panel_size: tuple[int, int]
    strategy: str


@dataclass(frozen=True)
class DraftGenerationRequest:
    schema_version: int
    planner_version: str
    mode: str
    asset_type: str
    creative_request: str
    model: str
    panel_roles: tuple[str, ...]
    panel_labels: tuple[str, ...]
    draft_size: tuple[int, int]
    palette_embedded: bool = True


@dataclass(frozen=True)
class FailedPanelRepairRequest:
    schema_version: int
    planner_version: str
    mode: str
    asset_type: str
    creative_request: str
    model: str
    role: str
    label: str
    objective: str
    panel_size: tuple[int, int]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class PanelFile:
    role: str
    path: Path


@dataclass(frozen=True)
class PanelPlacement:
    role: str
    label_box: tuple[int, int, int, int]
    image_box: tuple[int, int, int, int]


@dataclass(frozen=True)
class CompositionGeometry:
    canvas_size: tuple[int, int]
    palette_box: tuple[int, int, int, int]
    placements: tuple[PanelPlacement, ...]


@dataclass(frozen=True)
class _StageSnapshot:
    descriptor: int
    device: int
    inode: int
    size: int
    digest: str
    image_size: tuple[int, int]


@dataclass(frozen=True)
class SemanticReviewRequest:
    instruction: str
    creative_request: str
    sheet_path: Path
    panel_roles: tuple[str, ...]
    response_schema: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticReviewResult:
    status: str
    checks: tuple[tuple[str, bool], ...]
    failed_roles: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def checks_dict(self) -> dict[str, bool]:
        return dict(self.checks)


@dataclass(frozen=True)
class ArtifactProvenance:
    strategy: str
    version: str

    def public_metadata(self) -> dict[str, str]:
        return {"strategy": self.strategy, "version": self.version}


@dataclass(frozen=True)
class ReferenceSheetArtifact:
    """A transient path paired with safe-to-persist role metadata."""

    path: Path | None
    role: str
    model: str
    provenance: ArtifactProvenance
    reason_codes: tuple[str, ...] = ()

    def public_metadata(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model": self.model,
            "provenance": self.provenance.public_metadata(),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ReferenceSheetResult:
    plan: ReferenceSheetPlan
    sheet_path: Path
    artifacts: tuple[ReferenceSheetArtifact, ...]
    geometry: CompositionGeometry
    review: SemanticReviewResult
    repaired_roles: tuple[str, ...]

    def public_metadata(self) -> dict[str, Any]:
        """Return bounded persistence metadata with no creative text or paths."""
        return {
            "schema_version": self.plan.schema_version,
            "planner_version": self.plan.planner_version,
            "mode": self.plan.mode,
            "asset_type": self.plan.asset_type,
            "model": self.plan.model,
            "provenance": {
                "service": "reference_sheets",
                "version": self.plan.planner_version,
            },
            "roles": {
                "sheet": "sheet",
                "panels": list(self.plan.panel_roles),
                "repaired": list(self.repaired_roles),
            },
            "reason_codes": list(self.review.reason_codes),
            "review_status": self.review.status,
        }


PanelGenerator = Callable[[PanelGenerationRequest], os.PathLike[str] | str]
PanelEditor = Callable[[Path, PanelGenerationRequest], os.PathLike[str] | str]
DraftGenerator = Callable[[DraftGenerationRequest], os.PathLike[str] | str]
SemanticReviewer = Callable[[SemanticReviewRequest], object]
PanelRepairer = Callable[[Path, FailedPanelRepairRequest], os.PathLike[str] | str]


def _bounded_model(value: object) -> str:
    if not isinstance(value, str) or not _MODEL_RE.fullmatch(value):
        raise ValueError("model must be a bounded model identifier")
    if (
        Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in value.replace("\\", "/").split("/")
    ):
        raise ValueError("model must not be a filesystem path")
    return value


def _dimensions(value: object, field: str, *, minimum: int = 64) -> tuple[int, int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 2
        or isinstance(value[0], bool)
        or isinstance(value[1], bool)
        or not isinstance(value[0], int)
        or not isinstance(value[1], int)
    ):
        raise ValueError(f"{field} must contain two integer dimensions")
    width, height = int(value[0]), int(value[1])
    if not (minimum <= width <= 4096 and minimum <= height <= 4096):
        raise ValueError(f"{field} dimensions must be between {minimum} and 4096")
    return width, height


def build_reference_sheet_plan(
    *,
    asset_type: str,
    mode: str,
    creative_request: str,
    model: str,
    panel_size: Sequence[int] = (512, 512),
    draft_size: Sequence[int] = (1024, 1024),
    columns: int = 2,
    palette_swatches: int = 8,
) -> ReferenceSheetPlan:
    """Build a versioned, ordered plan without interpreting creative content."""
    if asset_type not in ASSET_TYPES:
        raise ValueError("asset_type must be character, setting, item, or style")
    if mode not in MODES:
        raise ValueError("mode must be production, hybrid, or draft")
    if not isinstance(creative_request, str) or not creative_request.strip():
        raise ValueError("creative_request must be a non-empty string")
    if len(creative_request) > 50_000:
        raise ValueError("creative_request is too long")
    if isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= 4:
        raise ValueError("columns must be an integer from 1 through 4")
    if (
        isinstance(palette_swatches, bool)
        or not isinstance(palette_swatches, int)
        or not 3 <= palette_swatches <= 12
    ):
        raise ValueError("palette_swatches must be an integer from 3 through 12")
    return ReferenceSheetPlan(
        schema_version=SCHEMA_VERSION,
        planner_version=PLANNER_VERSION,
        mode=mode,
        asset_type=asset_type,
        creative_request=creative_request,
        model=_bounded_model(model),
        panels=ROLE_RECIPES[asset_type],
        panel_size=_dimensions(panel_size, "panel_size"),
        draft_size=_dimensions(draft_size, "draft_size"),
        columns=columns,
        palette_swatches=palette_swatches,
    )


def _panel_request(
    plan: ReferenceSheetPlan,
    panel: PanelRecipe,
    index: int,
    strategy: str,
) -> PanelGenerationRequest:
    return PanelGenerationRequest(
        schema_version=plan.schema_version,
        planner_version=plan.planner_version,
        mode=plan.mode,
        asset_type=plan.asset_type,
        creative_request=plan.creative_request,
        model=plan.model,
        role=panel.role,
        label=panel.label,
        objective=panel.objective,
        index=index,
        panel_count=len(plan.panels),
        panel_size=plan.panel_size,
        strategy=strategy,
    )


def _as_source_path(value: os.PathLike[str] | str, role: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise ReferenceSheetStructureError(
            "panel_path_invalid", failed_roles=(role,),
        ) from exc
    if path.is_symlink() or not path.is_file():
        raise ReferenceSheetStructureError(
            "panel_file_unavailable", failed_roles=(role,),
        )
    return path.resolve()


def _same_physical_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(str(first)) == os.path.normcase(str(second))


def _image_size(path: Path, role: str) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width < 1 or height < 1 or width > 4096 or height > 4096:
                raise ReferenceSheetStructureError(
                    "panel_dimensions_exceed_limit", failed_roles=(role,),
                )
            image.load()
            return image.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ReferenceSheetStructureError(
            "panel_image_invalid", failed_roles=(role,),
        ) from exc


def validate_panel_files(
    panel_files: Sequence[PanelFile],
    *,
    expected_roles: Sequence[str],
    panel_size: Sequence[int],
) -> tuple[PanelFile, ...]:
    """Validate exact role order, dimensions, and unique physical inputs."""
    expected = tuple(expected_roles)
    actual = tuple(panel.role for panel in panel_files)
    if len(set(actual)) != len(actual):
        raise ReferenceSheetStructureError("panel_role_duplicate")
    if actual != expected:
        missing = tuple(role for role in expected if role not in actual)
        raise ReferenceSheetStructureError(
            "panel_roles_invalid", failed_roles=missing,
        )
    expected_size = _dimensions(panel_size, "panel_size")
    resolved: list[PanelFile] = []
    paths: list[Path] = []
    failed_dimensions: list[str] = []
    for panel in panel_files:
        path = _as_source_path(panel.path, panel.role)
        if any(_same_physical_file(path, existing) for existing in paths):
            raise ReferenceSheetStructureError(
                "panel_file_duplicate", failed_roles=(panel.role,),
            )
        paths.append(path)
        if _image_size(path, panel.role) != expected_size:
            failed_dimensions.append(panel.role)
        resolved.append(PanelFile(panel.role, path))
    if failed_dimensions:
        raise ReferenceSheetStructureError(
            "panel_dimensions_invalid", failed_roles=failed_dimensions,
        )
    return tuple(resolved)


def _geometry(
    panels: Sequence[PanelRecipe],
    *,
    panel_size: tuple[int, int],
    columns: int,
    margin: int = 16,
    gutter: int = 12,
    label_height: int = 24,
    palette_height: int = 72,
) -> CompositionGeometry:
    if not panels:
        raise ReferenceSheetStructureError("panel_roles_invalid")
    columns = min(columns, len(panels))
    rows = math.ceil(len(panels) / columns)
    font = ImageFont.load_default()
    label_width = max(
        font.getbbox(panel.label)[2] - font.getbbox(panel.label)[0]
        for panel in panels
    )
    cell_width = max(panel_size[0], label_width + 16)
    width = margin * 2 + columns * cell_width + (columns - 1) * gutter
    row_height = label_height + panel_size[1]
    palette_top = margin + rows * row_height + (rows - 1) * gutter + margin
    height = palette_top + palette_height + margin
    placements: list[PanelPlacement] = []
    for index, panel in enumerate(panels):
        row, column = divmod(index, columns)
        left = margin + column * (cell_width + gutter)
        label_top = margin + row * (row_height + gutter)
        image_top = label_top + label_height
        image_left = left + (cell_width - panel_size[0]) // 2
        placements.append(PanelPlacement(
            role=panel.role,
            label_box=(left, label_top, left + cell_width, image_top),
            image_box=(
                image_left,
                image_top,
                image_left + panel_size[0],
                image_top + panel_size[1],
            ),
        ))
    return CompositionGeometry(
        canvas_size=(width, height),
        palette_box=(margin, palette_top, width - margin, palette_top + palette_height),
        placements=tuple(placements),
    )


def _palette(images: Sequence[Image.Image], count: int) -> tuple[tuple[int, int, int], ...]:
    buckets: Counter[tuple[int, int, int]] = Counter()
    sums: dict[tuple[int, int, int], list[int]] = {}
    for image in images:
        sample = image.convert("RGB").resize((64, 64), Image.Resampling.BOX)
        pixels = sample.load()
        for y in range(sample.height):
            for x in range(sample.width):
                red, green, blue = pixels[x, y]
                key = (red >> 4, green >> 4, blue >> 4)
                buckets[key] += 1
                totals = sums.setdefault(key, [0, 0, 0])
                totals[0] += red
                totals[1] += green
                totals[2] += blue
    ordered = sorted(buckets, key=lambda key: (-buckets[key], key))[:count]
    colors = []
    for key in ordered:
        divisor = buckets[key]
        colors.append(tuple(value // divisor for value in sums[key]))
    while len(colors) < count:
        shade = 32 + len(colors) * (192 // max(1, count - 1))
        colors.append((shade, shade, shade))
    return tuple(colors)


def _save_new_png(image: Image.Image, output_path: Path) -> None:
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(output_path, flags, 0o600)
    except FileExistsError as exc:
        raise ReferenceSheetStructureError("sheet_output_exists") from exc
    created_stat = os.fstat(descriptor)
    try:
        ownership_descriptor = os.dup(descriptor)
    except OSError:
        try:
            current = output_path.lstat()
            if (
                stat.S_ISREG(current.st_mode)
                and current.st_dev == created_stat.st_dev
                and current.st_ino == created_stat.st_ino
            ):
                output_path.unlink()
        except OSError:
            pass
        finally:
            os.close(descriptor)
        raise
    ownership_stat = os.fstat(ownership_descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            image.save(handle, format="PNG", optimize=False, compress_level=9)
            handle.flush()
            os.fsync(handle.fileno())
        current = output_path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != ownership_stat.st_dev
            or current.st_ino != ownership_stat.st_ino
        ):
            raise ReferenceSheetStructureError("sheet_output_replaced")
    except Exception:
        try:
            current = output_path.lstat()
            if (
                stat.S_ISREG(current.st_mode)
                and current.st_dev == ownership_stat.st_dev
                and current.st_ino == ownership_stat.st_ino
            ):
                output_path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(ownership_descriptor)


def _staging_path(output_path: Path) -> Path:
    """Return a high-entropy, nonexistent sibling path for review composition."""
    for _attempt in range(16):
        candidate = output_path.parent / (
            f".{output_path.name}.review-{secrets.token_hex(12)}.png"
        )
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise ReferenceSheetStructureError("sheet_staging_unavailable")


def _promote_new_file(
    destination: Path,
    snapshot: _StageSnapshot,
) -> None:
    """Publish verified descriptor bytes into one exclusively created output."""
    descriptor = -1
    destination_stat = None
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise ReferenceSheetStructureError("sheet_output_exists") from exc
    except OSError as exc:
        raise ReferenceSheetStructureError("sheet_publish_failed") from exc
    try:
        destination_stat = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        os.lseek(snapshot.descriptor, 0, os.SEEK_SET)
        while True:
            block = os.read(snapshot.descriptor, 1024 * 1024)
            if not block:
                break
            offset = 0
            while offset < len(block):
                written = os.write(descriptor, block[offset:])
                if written <= 0:
                    raise OSError("short write while publishing reference sheet")
                offset += written
            size += len(block)
            digest.update(block)
        os.fsync(descriptor)
        published_size, published_digest = _fingerprint_descriptor(descriptor)
        current_descriptor = os.fstat(descriptor)
        current_path = destination.lstat()
        if (
            not stat.S_ISREG(current_descriptor.st_mode)
            or not stat.S_ISREG(current_path.st_mode)
            or current_descriptor.st_dev != destination_stat.st_dev
            or current_descriptor.st_ino != destination_stat.st_ino
            or current_path.st_dev != destination_stat.st_dev
            or current_path.st_ino != destination_stat.st_ino
        ):
            raise ReferenceSheetStructureError("sheet_publish_replaced")
        if size != snapshot.size or digest.hexdigest() != snapshot.digest:
            raise ReferenceSheetStructureError("sheet_stage_modified")
        if published_size != snapshot.size or published_digest != snapshot.digest:
            raise ReferenceSheetStructureError("sheet_publish_modified")
    except Exception:
        if destination_stat is not None:
            try:
                current = destination.lstat()
                if (
                    stat.S_ISREG(current.st_mode)
                    and current.st_dev == destination_stat.st_dev
                    and current.st_ino == destination_stat.st_ino
                ):
                    destination.unlink()
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _protect_stage(path: Path) -> None:
    """Make review stages read-only where descriptor chmod is available."""
    if callable(getattr(os, "fchmod", None)):
        os.chmod(path, 0o400)


def compose_reference_sheet(
    plan: ReferenceSheetPlan,
    panel_files: Sequence[PanelFile],
    output_path: os.PathLike[str] | str,
) -> CompositionGeometry:
    """Compose validated panels without cropping, resizing, or overwriting."""
    plan = _validate_executable_plan(plan)
    validated = validate_panel_files(
        panel_files,
        expected_roles=plan.panel_roles,
        panel_size=plan.panel_size,
    )
    geometry = _geometry(plan.panels, panel_size=plan.panel_size, columns=plan.columns)
    canvas = Image.new("RGB", geometry.canvas_size, (22, 22, 26))
    font = ImageFont.load_default()
    draw = ImageDraw.Draw(canvas)
    opened: list[Image.Image] = []
    try:
        for panel, placement, recipe in zip(validated, geometry.placements, plan.panels):
            with Image.open(panel.path) as source:
                image = source.convert("RGB")
                image.load()
            opened.append(image)
            canvas.paste(image, placement.image_box[:2])
            draw.rectangle(
                (
                    placement.label_box[0],
                    placement.label_box[1],
                    placement.label_box[2] - 1,
                    placement.label_box[3] - 1,
                ),
                fill=(38, 38, 44),
            )
            draw.text(
                (placement.label_box[0] + 8, placement.label_box[1] + 6),
                recipe.label,
                fill=(238, 238, 242),
                font=font,
            )
        _draw_palette(draw, geometry.palette_box, _palette(opened, plan.palette_swatches), font)
        _save_new_png(canvas, Path(output_path))
    finally:
        for image in opened:
            image.close()
        canvas.close()
    return geometry


def _draw_palette(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    colors: Sequence[tuple[int, int, int]],
    font: ImageFont.ImageFont,
) -> None:
    left, top, right, bottom = box
    draw.rectangle(box, fill=(38, 38, 44))
    draw.text((left + 8, top + 6), "PALETTE", fill=(238, 238, 242), font=font)
    swatch_top = top + 26
    swatch_bottom = bottom - 8
    available = right - left - 16
    for index, color in enumerate(colors):
        swatch_left = left + 8 + (available * index // len(colors))
        swatch_right = left + 8 + (available * (index + 1) // len(colors))
        draw.rectangle(
            (swatch_left, swatch_top, max(swatch_left, swatch_right - 2), swatch_bottom),
            fill=color,
        )


def _compose_draft(
    plan: ReferenceSheetPlan,
    draft_path: Path,
    output_path: Path,
) -> CompositionGeometry:
    if _image_size(draft_path, "sheet") != plan.draft_size:
        raise ReferenceSheetStructureError(
            "draft_dimensions_invalid", failed_roles=("sheet",),
        )
    with Image.open(draft_path) as source:
        draft = source.convert("RGB")
        draft.load()
    palette_height = 72
    canvas = Image.new("RGB", (draft.width, draft.height + palette_height), (22, 22, 26))
    canvas.paste(draft, (0, 0))
    palette_box = (0, draft.height, draft.width, draft.height + palette_height)
    _draw_palette(
        ImageDraw.Draw(canvas), palette_box, _palette((draft,), plan.palette_swatches),
        ImageFont.load_default(),
    )
    geometry = CompositionGeometry(
        canvas_size=canvas.size,
        palette_box=palette_box,
        placements=(),
    )
    try:
        _save_new_png(canvas, output_path)
    finally:
        draft.close()
        canvas.close()
    return geometry


def build_semantic_review_request(
    plan: ReferenceSheetPlan,
    sheet_path: os.PathLike[str] | str,
) -> SemanticReviewRequest:
    """Create a fidelity-only VLM request; it makes no permissibility judgment."""
    plan = _validate_executable_plan(plan)
    instruction = (
        "Review only visual fidelity to the supplied creative request and role recipe. "
        "Check identity, requested details, intended view, accessories, and style. "
        "Do not perform content moderation, maturity classification, safety analysis, "
        "policy analysis, or permissibility decisions. Return only the strict JSON object."
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "checks", "failed_roles", "reason_codes"],
        "properties": {
            "status": {"enum": ["pass", "fail"]},
            "checks": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_CHECK_NAMES),
                "properties": {name: {"type": "boolean"} for name in _CHECK_NAMES},
            },
            "failed_roles": {"type": "array", "items": {"enum": list(plan.panel_roles)}},
            "reason_codes": {"type": "array", "items": {"enum": sorted(_REASON_CODES)}},
        },
    }
    return SemanticReviewRequest(
        instruction=instruction,
        creative_request=plan.creative_request,
        sheet_path=Path(sheet_path),
        panel_roles=plan.panel_roles,
        response_schema=schema,
    )


def parse_semantic_review_result(
    value: object,
    *,
    allowed_roles: Sequence[str],
) -> SemanticReviewResult:
    """Parse the exact fidelity-review schema; free-form text is never retained."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReferenceSheetReviewError("review_unavailable") from exc
    if not isinstance(value, Mapping) or set(value) != {
        "status", "checks", "failed_roles", "reason_codes",
    }:
        raise ReferenceSheetReviewError("review_unavailable")
    status = value.get("status")
    checks = value.get("checks")
    failed_roles = value.get("failed_roles")
    reason_codes = value.get("reason_codes")
    if status not in {"pass", "fail"}:
        raise ReferenceSheetReviewError("review_unavailable")
    if not isinstance(checks, Mapping) or set(checks) != set(_CHECK_NAMES):
        raise ReferenceSheetReviewError("review_unavailable")
    if any(type(checks[name]) is not bool for name in _CHECK_NAMES):
        raise ReferenceSheetReviewError("review_unavailable")
    if not isinstance(failed_roles, list) or any(not isinstance(role, str) for role in failed_roles):
        raise ReferenceSheetReviewError("review_unavailable")
    if not isinstance(reason_codes, list) or any(not isinstance(code, str) for code in reason_codes):
        raise ReferenceSheetReviewError("review_unavailable")
    if len(set(failed_roles)) != len(failed_roles) or len(set(reason_codes)) != len(reason_codes):
        raise ReferenceSheetReviewError("review_unavailable")
    allowed = tuple(allowed_roles)
    if any(role not in allowed for role in failed_roles):
        raise ReferenceSheetReviewError("review_unavailable")
    if any(code not in _REASON_CODES for code in reason_codes):
        raise ReferenceSheetReviewError("review_unavailable")
    expected_codes = {
        _REASON_FOR_CHECK[name] for name in _CHECK_NAMES if checks[name] is False
    }
    if set(reason_codes) != expected_codes:
        raise ReferenceSheetReviewError("review_unavailable")
    if status == "pass":
        if failed_roles or reason_codes or not all(checks.values()):
            raise ReferenceSheetReviewError("review_unavailable")
    elif not failed_roles or not reason_codes or all(checks.values()):
        raise ReferenceSheetReviewError("review_unavailable")
    canonical_failed_roles = tuple(role for role in allowed if role in set(failed_roles))
    canonical_reason_codes = tuple(
        _REASON_FOR_CHECK[name] for name in _CHECK_NAMES if checks[name] is False
    )
    return SemanticReviewResult(
        status=status,
        checks=tuple((name, checks[name]) for name in _CHECK_NAMES),
        failed_roles=canonical_failed_roles,
        reason_codes=canonical_reason_codes,
    )


def review_reference_sheet(
    plan: ReferenceSheetPlan,
    sheet_path: Path,
    reviewer: SemanticReviewer | None,
) -> SemanticReviewResult:
    if reviewer is None:
        return _review_unavailable()
    try:
        raw = reviewer(build_semantic_review_request(plan, sheet_path))
        return parse_semantic_review_result(raw, allowed_roles=plan.panel_roles)
    # An injected local VLM is an availability boundary: every provider/runtime
    # failure deliberately collapses to the same path-free review state.
    except Exception:  # noqa: BLE001
        return _review_unavailable()


def _review_unavailable() -> SemanticReviewResult:
    return SemanticReviewResult(
        status="review_unavailable",
        checks=(),
        failed_roles=(),
        reason_codes=("review_unavailable",),
    )


def build_failed_panel_repair_plan(
    plan: ReferenceSheetPlan,
    review: SemanticReviewResult,
) -> tuple[str, ...]:
    """Choose at most one failed role, in recipe order."""
    plan = _validate_executable_plan(plan)
    failed = set(review.failed_roles)
    return tuple(role for role in plan.panel_roles if role in failed)[:1]


def _fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _fingerprint_descriptor(descriptor: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        size += len(block)
        digest.update(block)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return size, digest.hexdigest()


def _assert_preserved(path: Path, before: tuple[int, str], role: str) -> None:
    try:
        after = _fingerprint(path)
    except OSError as exc:
        raise ReferenceSheetStructureError(
            "source_was_modified", failed_roles=(role,),
        ) from exc
    if after != before:
        raise ReferenceSheetStructureError(
            "source_was_modified", failed_roles=(role,),
        )


def _new_distinct_path(
    value: os.PathLike[str] | str,
    role: str,
    existing: Sequence[Path],
) -> Path:
    path = _as_source_path(value, role)
    if any(_same_physical_file(path, item) for item in existing):
        raise ReferenceSheetStructureError(
            "generated_output_not_new", failed_roles=(role,),
        )
    return path


def _validate_executable_plan(plan: object) -> ReferenceSheetPlan:
    if not isinstance(plan, ReferenceSheetPlan):
        raise TypeError("unsupported reference-sheet plan")
    try:
        canonical = build_reference_sheet_plan(
            asset_type=plan.asset_type,
            mode=plan.mode,
            creative_request=plan.creative_request,
            model=plan.model,
            panel_size=plan.panel_size,
            draft_size=plan.draft_size,
            columns=plan.columns,
            palette_swatches=plan.palette_swatches,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported reference-sheet plan") from exc
    if plan != canonical:
        raise ValueError("unsupported reference-sheet plan")
    return plan


def _repair_request(
    plan: ReferenceSheetPlan,
    role: str,
    reason_codes: Sequence[str],
) -> FailedPanelRepairRequest:
    recipe = next(panel for panel in plan.panels if panel.role == role)
    return FailedPanelRepairRequest(
        schema_version=plan.schema_version,
        planner_version=plan.planner_version,
        mode=plan.mode,
        asset_type=plan.asset_type,
        creative_request=plan.creative_request,
        model=plan.model,
        role=role,
        label=recipe.label,
        objective=recipe.objective,
        panel_size=plan.panel_size,
        reason_codes=tuple(reason_codes),
    )


def _stage_snapshot(path: Path, expected_size: tuple[int, int]) -> _StageSnapshot:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        path_before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or before.st_dev != path_before.st_dev
            or before.st_ino != path_before.st_ino
        ):
            raise ReferenceSheetStructureError("sheet_stage_invalid")
        image_size = _image_size(path, "sheet")
        if image_size != expected_size:
            raise ReferenceSheetStructureError("sheet_stage_invalid")
        size, digest = _fingerprint_descriptor(descriptor)
        after = os.fstat(descriptor)
        path_after = path.lstat()
    except (OSError, ReferenceSheetStructureError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(exc, ReferenceSheetStructureError):
            raise
        raise ReferenceSheetStructureError("sheet_stage_invalid") from exc
    if (
        not stat.S_ISREG(after.st_mode)
        or not stat.S_ISREG(path_after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or after.st_dev != path_after.st_dev
        or after.st_ino != path_after.st_ino
        or before.st_size != after.st_size
        or after.st_size != size
    ):
        os.close(descriptor)
        raise ReferenceSheetStructureError("sheet_stage_modified")
    return _StageSnapshot(
        descriptor=descriptor,
        device=after.st_dev,
        inode=after.st_ino,
        size=size,
        digest=digest,
        image_size=image_size,
    )


def _assert_stage_unchanged(path: Path, snapshot: _StageSnapshot) -> None:
    try:
        descriptor_stat = os.fstat(snapshot.descriptor)
        path_stat = path.lstat()
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_dev != snapshot.device
            or path_stat.st_ino != snapshot.inode
            or descriptor_stat.st_dev != snapshot.device
            or descriptor_stat.st_ino != snapshot.inode
            or _image_size(path, "sheet") != snapshot.image_size
        ):
            raise ReferenceSheetStructureError("sheet_stage_modified")
        size, digest = _fingerprint_descriptor(snapshot.descriptor)
    except OSError as exc:
        raise ReferenceSheetStructureError("sheet_stage_modified") from exc
    if size != snapshot.size or digest != snapshot.digest:
        raise ReferenceSheetStructureError("sheet_stage_modified")


def _remove_owned_output(path: Path, snapshot: _StageSnapshot | None = None) -> None:
    try:
        if snapshot is not None:
            current = path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_dev != snapshot.device
                or current.st_ino != snapshot.inode
            ):
                return
        path.unlink()
    except OSError:
        pass
    finally:
        if snapshot is not None:
            try:
                os.close(snapshot.descriptor)
            except OSError:
                pass


def create_reference_sheet(
    plan: ReferenceSheetPlan,
    output_path: os.PathLike[str] | str,
    *,
    generate_panel: PanelGenerator | None = None,
    edit_panel: PanelEditor | None = None,
    generate_draft: DraftGenerator | None = None,
    reviewer: SemanticReviewer | None = None,
    repair_panel: PanelRepairer | None = None,
) -> ReferenceSheetResult:
    """Execute one plan through injected operations and deterministic composition.

    Callback outputs must be new, regular image files.  The service fingerprints
    sources around edit/repair calls and never overwrites a supplied source or an
    existing final path.  A semantic failure can repair at most one panel once.
    """
    plan = _validate_executable_plan(plan)
    requested_path = Path(output_path).expanduser()
    if requested_path.exists() or requested_path.is_symlink():
        raise ReferenceSheetStructureError("sheet_output_exists")
    final_path = requested_path.absolute()

    panel_files: list[PanelFile] = []
    artifacts: list[ReferenceSheetArtifact] = []
    repaired_roles: list[str] = []
    generated_paths: list[Path] = []

    if plan.mode == "production":
        if generate_panel is None:
            raise ValueError("production mode requires generate_panel")
        for index, panel in enumerate(plan.panels):
            path = _new_distinct_path(
                generate_panel(_panel_request(plan, panel, index, "independent")),
                panel.role,
                generated_paths,
            )
            generated_paths.append(path)
            panel_files.append(PanelFile(panel.role, path))
    elif plan.mode == "hybrid":
        if generate_panel is None or edit_panel is None:
            raise ValueError("hybrid mode requires generate_panel and edit_panel")
        anchor_recipe = plan.panels[0]
        anchor = _new_distinct_path(
            generate_panel(_panel_request(plan, anchor_recipe, 0, "identity_anchor")),
            anchor_recipe.role,
            generated_paths,
        )
        generated_paths.append(anchor)
        panel_files.append(PanelFile(anchor_recipe.role, anchor))
        anchor_fingerprint = _fingerprint(anchor)
        for index, panel in enumerate(plan.panels[1:], start=1):
            edited = _new_distinct_path(
                edit_panel(anchor, _panel_request(plan, panel, index, "targeted_edit")),
                panel.role,
                generated_paths,
            )
            _assert_preserved(anchor, anchor_fingerprint, anchor_recipe.role)
            generated_paths.append(edited)
            panel_files.append(PanelFile(panel.role, edited))
    else:
        if generate_draft is None:
            raise ValueError("draft mode requires generate_draft")
        draft_request = DraftGenerationRequest(
            schema_version=plan.schema_version,
            planner_version=plan.planner_version,
            mode=plan.mode,
            asset_type=plan.asset_type,
            creative_request=plan.creative_request,
            model=plan.model,
            panel_roles=plan.panel_roles,
            panel_labels=tuple(panel.label for panel in plan.panels),
            draft_size=plan.draft_size,
        )
        draft = _as_source_path(generate_draft(draft_request), "sheet")
        if os.path.normcase(str(draft)) == os.path.normcase(str(final_path.resolve())):
            raise ReferenceSheetStructureError("generated_output_not_new", failed_roles=("sheet",))
        draft_before = _fingerprint(draft)
        stage_path = _staging_path(final_path)
        stage_snapshot = None
        try:
            geometry = _compose_draft(plan, draft, stage_path)
            _assert_preserved(draft, draft_before, "sheet")
            _protect_stage(stage_path)
            stage_snapshot = _stage_snapshot(stage_path, geometry.canvas_size)
            review = review_reference_sheet(plan, stage_path, reviewer)
            _assert_stage_unchanged(stage_path, stage_snapshot)
            _promote_new_file(final_path, stage_snapshot)
        finally:
            _remove_owned_output(stage_path, stage_snapshot)
        for role in plan.panel_roles:
            artifacts.append(ReferenceSheetArtifact(
                path=None,
                role=role,
                model=plan.model,
                provenance=ArtifactProvenance(
                    strategy="one_shot_logical_panel",
                    version=plan.planner_version,
                ),
            ))
        artifacts.append(ReferenceSheetArtifact(
            path=final_path,
            role="sheet",
            model=plan.model,
            provenance=ArtifactProvenance(
                strategy="one_shot",
                version=plan.planner_version,
            ),
            reason_codes=review.reason_codes,
        ))
        return ReferenceSheetResult(
            plan=plan,
            sheet_path=final_path,
            artifacts=tuple(artifacts),
            geometry=geometry,
            review=review,
            repaired_roles=(),
        )

    try:
        validated = list(validate_panel_files(
            panel_files, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
        ))
    except ReferenceSheetStructureError as error:
        repair_roles = tuple(
            role for role in plan.panel_roles if role in set(error.failed_roles)
        )[:1]
        if repair_panel is None or not repair_roles:
            raise
        role = repair_roles[0]
        index = plan.panel_roles.index(role)
        original = _as_source_path(panel_files[index].path, role)
        fingerprint = _fingerprint(original)
        repaired = _new_distinct_path(
            repair_panel(original, _repair_request(plan, role, (error.reason_code,))),
            role,
            generated_paths,
        )
        _assert_preserved(original, fingerprint, role)
        generated_paths.append(repaired)
        panel_files[index] = PanelFile(role, repaired)
        repaired_roles.append(role)
        validated = list(validate_panel_files(
            panel_files, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
        ))

    composition_path = _staging_path(final_path)
    stage_snapshot = None
    try:
        geometry = compose_reference_sheet(plan, validated, composition_path)
        _protect_stage(composition_path)
        stage_snapshot = _stage_snapshot(composition_path, geometry.canvas_size)
        review = review_reference_sheet(plan, composition_path, reviewer)
        _assert_stage_unchanged(composition_path, stage_snapshot)
        repair_roles = build_failed_panel_repair_plan(plan, review)
        if (
            review.status == "fail"
            and repair_roles
            and repair_panel is not None
            and not repaired_roles
        ):
            _remove_owned_output(composition_path, stage_snapshot)
            role = repair_roles[0]
            index = plan.panel_roles.index(role)
            original = validated[index].path
            fingerprint = _fingerprint(original)
            repaired = _new_distinct_path(
                repair_panel(original, _repair_request(plan, role, review.reason_codes)),
                role,
                generated_paths,
            )
            _assert_preserved(original, fingerprint, role)
            generated_paths.append(repaired)
            validated[index] = PanelFile(role, repaired)
            validate_panel_files(
                validated, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
            )
            composition_path = _staging_path(final_path)
            stage_snapshot = None
            geometry = compose_reference_sheet(plan, validated, composition_path)
            _protect_stage(composition_path)
            stage_snapshot = _stage_snapshot(composition_path, geometry.canvas_size)
            repaired_roles.append(role)
            review = review_reference_sheet(plan, composition_path, reviewer)
            _assert_stage_unchanged(composition_path, stage_snapshot)
        _promote_new_file(final_path, stage_snapshot)
    finally:
        _remove_owned_output(composition_path, stage_snapshot)

    strategy = "independent" if plan.mode == "production" else "anchor_edit"
    for panel in validated:
        panel_strategy = strategy
        if panel.role in repaired_roles:
            panel_strategy = "repaired_panel"
        artifacts.append(ReferenceSheetArtifact(
            path=panel.path,
            role=panel.role,
            model=plan.model,
            provenance=ArtifactProvenance(
                strategy=panel_strategy,
                version=plan.planner_version,
            ),
        ))
    artifacts.append(ReferenceSheetArtifact(
        path=final_path,
        role="sheet",
        model=plan.model,
        provenance=ArtifactProvenance(
            strategy="deterministic_collage",
            version=plan.planner_version,
        ),
        reason_codes=review.reason_codes,
    ))
    return ReferenceSheetResult(
        plan=plan,
        sheet_path=final_path,
        artifacts=tuple(artifacts),
        geometry=geometry,
        review=review,
        repaired_roles=tuple(repaired_roles),
    )


__all__ = [
    "ASSET_TYPES",
    "MODES",
    "PLANNER_VERSION",
    "ROLE_RECIPES",
    "SCHEMA_VERSION",
    "ArtifactProvenance",
    "CompositionGeometry",
    "DraftGenerationRequest",
    "FailedPanelRepairRequest",
    "PanelFile",
    "PanelGenerationRequest",
    "PanelPlacement",
    "PanelRecipe",
    "ReferenceSheetArtifact",
    "ReferenceSheetError",
    "ReferenceSheetPlan",
    "ReferenceSheetResult",
    "ReferenceSheetReviewError",
    "ReferenceSheetStructureError",
    "SemanticReviewRequest",
    "SemanticReviewResult",
    "build_failed_panel_repair_plan",
    "build_reference_sheet_plan",
    "build_semantic_review_request",
    "compose_reference_sheet",
    "create_reference_sheet",
    "parse_semantic_review_result",
    "review_reference_sheet",
    "validate_panel_files",
]
