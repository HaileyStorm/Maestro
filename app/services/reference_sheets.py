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
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

SCHEMA_VERSION = 1
PLANNER_VERSION = "reference-sheet-v1"
MODES = frozenset({"production", "hybrid", "draft"})
ASSET_TYPES = frozenset({"character", "setting", "item", "style"})

# V1 above remains executable for stored reference-sheet records. New authoring
# uses the adaptive, ordered pack contract below; keeping the identifiers
# separate prevents a v2 plan from being mistaken for a legacy collage plan.
PACK_SCHEMA_VERSION = 2
PACK_PLANNER_VERSION = "reference-pack-v2"
PACK_INTENTS = frozenset({"exact_spec", "generic", "brainstorming"})
PACK_DEPTHS = frozenset({"compact", "standard", "comprehensive", "custom"})
PACK_REFERENCE_TYPES = frozenset({
    "character", "location", "prop", "vehicle", "creature", "wardrobe", "world",
})
PACK_REFERENCE_TYPE_ALIASES: Mapping[str, str] = MappingProxyType({
    "character": "character",
    "setting": "location",
    "location": "location",
    "item": "prop",
    "prop": "prop",
    "vehicle": "vehicle",
    "machine": "vehicle",
    "creature": "creature",
    "wardrobe": "wardrobe",
    "accessory": "wardrobe",
    "style": "world",
    "world": "world",
})
MAX_PACK_SHEETS = 5
PACK_DEPTH_COUNTS: Mapping[str, int] = MappingProxyType({
    "compact": 1,
    "standard": 3,
    "comprehensive": 5,
})
PACK_TYPE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "character": ("poses", "outfits"),
    "location": ("zones", "lighting"),
    "prop": ("functions", "scale"),
    "vehicle": ("views", "mechanisms"),
    "creature": ("poses", "anatomy"),
    "wardrobe": ("views", "materials"),
    "world": ("composition", "lighting"),
})
# Group and option order is part of the authored-settings wire contract. Option
# identifiers are derived as ``<group>:<slug>``; labels are server-owned and
# must round-trip exactly for a non-custom item.
PACK_TYPE_FIELD_GROUPS: Mapping[
    str, Mapping[str, tuple[tuple[str, str, tuple[str, ...]], ...]]
] = MappingProxyType({
    "character": MappingProxyType({
        "poses": (
            ("views", "Views", ("front", "profile", "three-quarter", "back")),
            ("poses", "Poses", ("neutral", "action", "seated", "movement")),
            ("expressions", "Expressions", ("neutral", "joy", "anger", "fear")),
            ("anatomy", "Anatomy anchor", ("anatomy", "nude anatomy")),
        ),
        "outfits": (("wardrobe", "Wardrobe", (
            "primary outfit", "underwear / underlayers", "individual garments",
            "accessories", "alternate outfit",
        )),),
    }),
    "location": MappingProxyType({
        "zones": (
            ("views", "Views", ("establishing", "entry", "reverse", "overhead")),
            ("zones", "Zones", ("primary zone", "secondary zone", "transitions", "boundaries")),
        ),
        "lighting": (("lighting", "Lighting", (
            "day", "night", "practical lights", "weather variation",
        )),),
    }),
    "prop": MappingProxyType({
        "functions": (
            ("views", "Views", ("front", "side", "back", "top")),
            ("functions", "Functions", ("closed", "in use", "open", "moving parts")),
        ),
        "scale": (("scale", "Scale", (
            "in hand", "beside person", "dimension callout", "environment context",
        )),),
    }),
    "vehicle": MappingProxyType({
        "views": (("views", "Views", ("front", "side", "rear", "three-quarter")),),
        "mechanisms": (("mechanisms", "Mechanisms", (
            "cockpit", "controls", "powertrain", "moving parts",
        )),),
    }),
    "creature": MappingProxyType({
        "poses": (
            ("views", "Views", ("front", "profile", "three-quarter", "back")),
            ("poses", "Poses", ("neutral", "locomotion", "attack", "resting")),
            ("expressions", "Expressions", ("neutral", "alert", "aggressive", "relaxed")),
        ),
        "anatomy": (("anatomy", "Anatomy anchor", (
            "anatomy", "nude anatomy", "skeletal landmarks", "limb detail",
        )),),
    }),
    "wardrobe": MappingProxyType({
        "views": (("views", "Views", ("front", "back", "side", "styled look")),),
        "materials": (("materials", "Materials", (
            "fabric", "hardware", "seams", "surface detail",
        )),),
    }),
    "world": MappingProxyType({
        "composition": (("composition", "Composition", (
            "wide", "medium", "close", "graphic layout",
        )),),
        "lighting": (("lighting", "Lighting", (
            "key lighting", "practical light", "day", "night",
        )),),
    }),
})
PACK_TYPE_PRESETS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "character": ("identity", "wardrobe", "underlayers", "anatomy", "performance"),
    "location": ("spatial", "lighting", "materials"),
    "prop": ("product", "functional", "construction"),
    "vehicle": ("exterior", "interior", "mechanical"),
    "creature": ("identity", "anatomy", "behavior"),
    "wardrobe": ("look", "construction", "accessories"),
    "world": ("visual_language", "environment", "cinematography"),
})
PACK_DEFAULT_PRESETS: Mapping[str, str] = MappingProxyType({
    "character": "identity",
    "location": "spatial",
    "prop": "product",
    "vehicle": "exterior",
    "creature": "identity",
    "wardrobe": "look",
    "world": "visual_language",
})
PACK_DETAIL_KINDS: Mapping[str, frozenset[str]] = MappingProxyType({
    "character": frozenset({"face", "hands", "markings", "garment", "accessory"}),
    "location": frozenset({"material", "fixture", "prop", "signage"}),
    "prop": frozenset({"mechanism", "control", "material", "marking"}),
    "vehicle": frozenset({"mechanism", "control", "interior", "marking"}),
    "creature": frozenset({"face", "limb", "markings", "surface"}),
    "wardrobe": frozenset({"closure", "seam", "material", "accessory"}),
    "world": frozenset({"material", "lighting", "composition", "motion"}),
})
PACK_DETAIL_OPERATIONS = frozenset({"auto", "crop", "enhance", "reconstruct"})
PACK_OPERATION_ORDER = ("generation", "edit", "repair", "callout")
PACK_OPERATION_STATUSES = frozenset({"standard", "applied", "skipped"})

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_AUTHORED_ID_RE = re.compile(
    r"^(?:custom:[A-Za-z0-9][A-Za-z0-9_-]{11,95}|"
    r"legacy:[a-z][a-z0-9_]{0,31}|"
    r"[a-z][a-z0-9_-]{0,31}:[a-z0-9][a-z0-9._-]{0,95})$"
)
_REASON_CODES = frozenset({
    "identity_mismatch",
    "request_mismatch",
    "view_mismatch",
    "accessory_mismatch",
    "style_mismatch",
    "overall_fidelity_mismatch",
    "mature_register_mismatch",
    "violent_register_mismatch",
    "detail_register_mismatch",
})
_CHECK_NAMES = ("identity", "request", "view", "accessory", "style")
_UNRESTRICTED_CHECK_NAMES = (
    *_CHECK_NAMES,
    "overall_fidelity",
    "mature_register_fidelity",
    "violent_register_fidelity",
    "detail_register_fidelity",
)
_REASON_FOR_CHECK = {
    "identity": "identity_mismatch",
    "request": "request_mismatch",
    "view": "view_mismatch",
    "accessory": "accessory_mismatch",
    "style": "style_mismatch",
    "overall_fidelity": "overall_fidelity_mismatch",
    "mature_register_fidelity": "mature_register_mismatch",
    "violent_register_fidelity": "violent_register_mismatch",
    "detail_register_fidelity": "detail_register_mismatch",
}
PACK_REVIEW_CONTRACTS = frozenset({
    "standard_fidelity_v1",
    "explicit_unrestricted_fidelity_v1",
})


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


@dataclass(frozen=True)
class PackSheetRecipe:
    """One ordered page in a v2 candidate pack."""

    role: str
    label: str
    objective: str


# The first recipe is always the immutable canonical anchor for an anchored
# pack. Presets may reprioritize derivatives but can never move that anchor.
PACK_ROLE_RECIPES: Mapping[str, tuple[PackSheetRecipe, ...]] = MappingProxyType({
    "character": (
        PackSheetRecipe("canonical_identity", "CANONICAL IDENTITY", "least-obscured canonical identity and proportions"),
        PackSheetRecipe("turnaround", "TURNAROUND", "front, three-quarter, profile, and rear continuity"),
        PackSheetRecipe("expressions", "EXPRESSIONS", "expression range with invariant identity"),
        PackSheetRecipe("wardrobe", "WARDROBE", "requested clothing and accessory continuity"),
        PackSheetRecipe("identity_details", "IDENTITY DETAILS", "face, hair, hands, materials, and identifying details"),
    ),
    "location": (
        PackSheetRecipe("canonical_establishing", "CANONICAL ESTABLISHING", "least-occluded spatial and geographic anchor"),
        PackSheetRecipe("spatial_layout", "SPATIAL LAYOUT", "connected zones, scale, and navigation relationships"),
        PackSheetRecipe("reverse_angles", "REVERSE ANGLES", "opposing views with matching geography"),
        PackSheetRecipe("lighting_states", "LIGHTING STATES", "requested time and lighting continuity"),
        PackSheetRecipe("material_details", "MATERIAL DETAILS", "surfaces, fixtures, props, and local texture"),
    ),
    "prop": (
        PackSheetRecipe("canonical_hero", "CANONICAL HERO", "least-occluded identifying form and material anchor"),
        PackSheetRecipe("orthographic_views", "ORTHOGRAPHIC VIEWS", "front, side, rear, and top construction"),
        PackSheetRecipe("functional_details", "FUNCTIONAL DETAILS", "controls, moving parts, and use states"),
        PackSheetRecipe("scale_context", "SCALE CONTEXT", "requested scale relationships"),
        PackSheetRecipe("material_details", "MATERIAL DETAILS", "surface, finish, seams, and wear continuity"),
    ),
    "vehicle": (
        PackSheetRecipe("canonical_exterior", "CANONICAL EXTERIOR", "least-occluded exterior silhouette and design anchor"),
        PackSheetRecipe("orthographic_views", "ORTHOGRAPHIC VIEWS", "front, side, rear, and top construction"),
        PackSheetRecipe("interior", "INTERIOR", "cockpit, cabin, controls, and spatial continuity"),
        PackSheetRecipe("mechanisms", "MECHANISMS", "articulation, propulsion, and functional details"),
        PackSheetRecipe("scale_context", "SCALE CONTEXT", "occupant and environment scale relationships"),
    ),
    "creature": (
        PackSheetRecipe("canonical_identity", "CANONICAL IDENTITY", "least-obscured canonical anatomy, identity, and proportions"),
        PackSheetRecipe("turnaround", "TURNAROUND", "front, three-quarter, profile, and rear continuity"),
        PackSheetRecipe("behavior", "BEHAVIOR", "poses, locomotion, and expression range"),
        PackSheetRecipe("anatomy_details", "ANATOMY DETAILS", "head, limbs, extremities, surface, and identifying details"),
        PackSheetRecipe("scale_context", "SCALE CONTEXT", "requested scale relationships"),
    ),
    "wardrobe": (
        PackSheetRecipe("canonical_look", "CANONICAL LOOK", "least-occluded complete worn look and silhouette"),
        PackSheetRecipe("front_back", "FRONT / BACK", "front, side, and rear garment continuity"),
        PackSheetRecipe("construction", "CONSTRUCTION", "layers, seams, closures, and fit"),
        PackSheetRecipe("accessories", "ACCESSORIES", "coordinated accessory details and placement"),
        PackSheetRecipe("material_palette", "MATERIAL / PALETTE", "fabric, finish, trim, and color continuity"),
    ),
    "world": (
        PackSheetRecipe("canonical_keyframe", "CANONICAL KEYFRAME", "least-occluded visual-language and world anchor"),
        PackSheetRecipe("composition_language", "COMPOSITION", "framing, staging, and scale language"),
        PackSheetRecipe("lighting_language", "LIGHTING", "lighting, exposure, and contrast language"),
        PackSheetRecipe("material_language", "MATERIAL", "surface, rendering, and palette language"),
        PackSheetRecipe("motion_language", "MOTION / CAMERA", "motion, lens, and camera language"),
    ),
})


_PACK_PRESET_ROLE_ORDERS: Mapping[tuple[str, str], tuple[str, ...]] = MappingProxyType({
    ("character", "identity"): ("turnaround", "expressions", "wardrobe", "identity_details"),
    ("character", "wardrobe"): ("wardrobe", "turnaround", "identity_details", "expressions"),
    ("character", "underlayers"): ("wardrobe", "identity_details", "turnaround", "expressions"),
    ("character", "anatomy"): ("turnaround", "wardrobe", "identity_details", "expressions"),
    ("character", "performance"): ("expressions", "turnaround", "wardrobe", "identity_details"),
    ("location", "spatial"): ("spatial_layout", "reverse_angles", "lighting_states", "material_details"),
    ("location", "lighting"): ("lighting_states", "reverse_angles", "material_details", "spatial_layout"),
    ("location", "materials"): ("material_details", "spatial_layout", "lighting_states", "reverse_angles"),
    ("prop", "product"): ("orthographic_views", "material_details", "scale_context", "functional_details"),
    ("prop", "functional"): ("functional_details", "orthographic_views", "scale_context", "material_details"),
    ("prop", "construction"): ("orthographic_views", "functional_details", "material_details", "scale_context"),
    ("vehicle", "exterior"): ("orthographic_views", "scale_context", "mechanisms", "interior"),
    ("vehicle", "interior"): ("interior", "orthographic_views", "mechanisms", "scale_context"),
    ("vehicle", "mechanical"): ("mechanisms", "orthographic_views", "interior", "scale_context"),
    ("creature", "identity"): ("turnaround", "behavior", "anatomy_details", "scale_context"),
    ("creature", "anatomy"): ("turnaround", "anatomy_details", "behavior", "scale_context"),
    ("creature", "behavior"): ("behavior", "turnaround", "anatomy_details", "scale_context"),
    ("wardrobe", "look"): ("front_back", "material_palette", "accessories", "construction"),
    ("wardrobe", "construction"): ("construction", "front_back", "material_palette", "accessories"),
    ("wardrobe", "accessories"): ("accessories", "front_back", "material_palette", "construction"),
    ("world", "visual_language"): ("composition_language", "lighting_language", "material_language", "motion_language"),
    ("world", "environment"): ("material_language", "lighting_language", "composition_language", "motion_language"),
    ("world", "cinematography"): ("motion_language", "composition_language", "lighting_language", "material_language"),
})


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
    model: str | None = None


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
class _ReviewedArtifactSeal:
    """Private file identity proven unchanged across one reviewer call."""

    role: str
    index: int
    device: int
    inode: int
    size: int
    sha256: str


@dataclass(frozen=True)
class SemanticReviewResult:
    status: str
    checks: tuple[tuple[str, bool], ...]
    failed_roles: tuple[str, ...]
    reason_codes: tuple[str, ...]
    artifact_seals: tuple[_ReviewedArtifactSeal, ...] = ()

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
    max_repair_attempts: int = 1
    repair_attempts_used: int = 0

    def public_metadata(self) -> dict[str, Any]:
        """Return bounded persistence metadata with no creative text or paths."""
        return {
            "schema_version": self.plan.schema_version,
            "planner_version": self.plan.planner_version,
            "mode": self.plan.mode,
            "asset_type": self.plan.asset_type,
            "model": self.plan.model,
            "generation_model": self.plan.model,
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
            "max_repair_attempts": self.max_repair_attempts,
            "max_repair_attempts_per_candidate": self.max_repair_attempts,
            "repair_attempts_used": self.repair_attempts_used,
            "repair_attempts_used_per_candidate": self.repair_attempts_used,
        }


@dataclass(frozen=True)
class PackTypeFieldItem:
    item_id: str
    label: str
    custom: bool
    group: str

    def private_metadata(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "label": self.label,
            "custom": self.custom,
            "group": self.group,
        }

    def public_metadata(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "custom": self.custom,
            "group": self.group,
        }


@dataclass(frozen=True)
class PackDetailCallout:
    custom_id: str
    label: str
    kind: str
    requested_operation: str
    source_role: str

    @property
    def target_role(self) -> str:
        return f"detail_callout:{self.custom_id}"

    @property
    def label_digest(self) -> str:
        return hashlib.sha256(self.label.encode("utf-8")).hexdigest()

    def public_metadata(self) -> dict[str, str]:
        return {
            "custom_id": self.custom_id,
            "kind": self.kind,
            "requested_operation": self.requested_operation,
            "source_role": self.source_role,
            "target_role": self.target_role,
            "label_digest": self.label_digest,
        }

    def private_metadata(self) -> dict[str, str]:
        return {
            "custom_id": self.custom_id,
            "label": self.label,
            "kind": self.kind,
            "operation": self.requested_operation,
            "source_role": self.source_role,
        }


@dataclass(frozen=True)
class PackIntelligenceSelection:
    requested_model: str
    resolved_model: str | None
    resolved_provider: str
    selection_revision: str = "legacy"

    def public_metadata(self) -> dict[str, Any]:
        metadata = {
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "resolved_provider": self.resolved_provider,
        }
        if self.selection_revision != "legacy":
            metadata["selection_revision"] = self.selection_revision
        return metadata


@dataclass(frozen=True)
class PackModelSchedule:
    model: str
    steps: int
    guidance: float
    guidance_key: str
    source: str

    def public_metadata(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "steps": self.steps,
            "guidance": self.guidance,
            "guidance_key": self.guidance_key,
            "source": self.source,
        }


@dataclass(frozen=True)
class PackLoraSelection:
    lora_id: str
    multiplier: float
    requested_scope: str
    resolved_scopes: tuple[str, ...]
    roles: tuple[str, ...]
    revision: str
    source_sha256: str
    skipped_reason: str | None = None

    def public_metadata(self) -> dict[str, Any]:
        if self.skipped_reason is not None:
            return {
                "id": self.lora_id,
                "weight": self.multiplier,
                "requested_scope": self.requested_scope,
                "reason": self.skipped_reason,
            }
        return {
            "id": self.lora_id,
            "weight": self.multiplier,
            "requested_scope": self.requested_scope,
            "resolved_scope": list(self.resolved_scopes),
            "roles": list(self.roles),
        }


@dataclass(frozen=True)
class PackOperationRoute:
    """One content-neutral, server-resolved image-operation route."""

    operation: str
    requested_capability: str
    requested_model: str | None
    resolved_model: str | None
    status: str
    schedule: PackModelSchedule | Mapping[str, Any] | None = None
    recipe_id: str | None = None
    verification_status: str | None = None
    reason: str | None = None

    def public_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "status": self.status,
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "schedule": (
                self.schedule.public_metadata()
                if isinstance(self.schedule, PackModelSchedule) else None
            ),
        }
        if self.recipe_id is not None:
            metadata["recipe_id"] = self.recipe_id
        if self.verification_status is not None:
            metadata["verification_status"] = self.verification_status
        if self.reason is not None:
            metadata["reason"] = self.reason
        return metadata


@dataclass(frozen=True)
class ReferencePackPlan:
    schema_version: int
    planner_version: str
    mode: str
    intent: str
    reference_type: str
    preset: str
    depth: str
    creative_request: str
    generation_model: str
    editor_model: str | None
    sheets: tuple[PackSheetRecipe, ...]
    sheet_size: tuple[int, int]
    anchor_basis: str
    anchor_privacy: str
    private_output: bool
    managed_layout_assist: str
    user_lora_count: int
    type_fields: tuple[tuple[str, tuple[PackTypeFieldItem, ...]], ...]
    detail_callouts: tuple[PackDetailCallout, ...]
    planning: PackIntelligenceSelection
    review_selection: PackIntelligenceSelection
    generation_schedule: PackModelSchedule | None
    editor_schedule: PackModelSchedule | None
    content_capability: str
    initial_blur: bool
    intelligence_policy: str
    review_contract: str
    operation_routing: tuple[PackOperationRoute, ...]
    additional_loras: tuple[PackLoraSelection, ...]
    resource_seal: str
    role_brief_seal: str
    authored_settings_seal: str
    plan_seal: str

    @property
    def sheet_roles(self) -> tuple[str, ...]:
        return tuple(sheet.role for sheet in self.sheets)

    @property
    def anchor_role(self) -> str | None:
        return None if self.mode == "draft" else self.sheets[0].role

    @property
    def anchor_strategy(self) -> str:
        return "draft_one_shot" if self.mode == "draft" else "canonical_anchor"

    @property
    def output_roles(self) -> tuple[str, ...]:
        return (*self.sheet_roles, *(item.target_role for item in self.detail_callouts))

    def private_authored_settings(self) -> dict[str, Any]:
        return {
            "type_fields": {
                field: [item.private_metadata() for item in items]
                for field, items in self.type_fields
            },
            "detail_callouts": [
                item.private_metadata() for item in self.detail_callouts
            ],
        }

    def public_authored_settings(self) -> dict[str, Any]:
        return {
            "seal": self.authored_settings_seal,
            "type_fields": [
                {
                    "field": field,
                    "items": [item.public_metadata() for item in items],
                }
                for field, items in self.type_fields
            ],
            "detail_callouts": [
                item.public_metadata() for item in self.detail_callouts
            ],
        }

    def operation_route(self, operation: str) -> PackOperationRoute:
        return next(item for item in self.operation_routing if item.operation == operation)

    def public_preview(self, *, candidate_count: int = 1) -> dict[str, Any]:
        """Return the exact prompt/path-free plan shown before publication."""
        return {
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "plan_seal": self.plan_seal,
            "mode": self.mode,
            "intent": self.intent,
            "reference_type": self.reference_type,
            "preset": self.preset,
            "depth": self.depth,
            "sheet_count": len(self.sheets),
            "ordered_sheet_roles": list(self.sheet_roles),
            "detail_callout_count": len(self.detail_callouts),
            "ordered_output_roles": list(self.output_roles),
            "candidate_count": candidate_count,
            "anchor_strategy": self.anchor_strategy,
            "anchor_role": self.anchor_role,
            "anchor_basis": self.anchor_basis,
            "anchor_privacy": self.anchor_privacy,
            "private_output": self.private_output,
            "content_capability": self.content_capability,
            "initial_blur": self.initial_blur,
            "intelligence_policy": self.intelligence_policy,
            **(
                {"review_contract": self.review_contract}
                if self.review_contract != "standard_fidelity_v1" else {}
            ),
            "operation_routing": {
                "requested_capability": self.content_capability,
                "operations": {
                    item.operation: item.public_metadata()
                    for item in self.operation_routing
                },
            },
            "generation_model": self.generation_model,
            "editor_model": self.editor_model,
            "model_schedules": {
                "generation": (
                    self.generation_schedule.public_metadata()
                    if self.generation_schedule is not None else None
                ),
                "editor": (
                    self.editor_schedule.public_metadata()
                    if self.editor_schedule is not None else None
                ),
            },
            "managed_layout_assist": _managed_layout_public_metadata(
                self.managed_layout_assist,
            ),
            "user_loras": {
                "count": self.user_lora_count,
                "preserved": True,
            },
            "additional_loras": {
                "applied": [
                    item.public_metadata() for item in self.additional_loras
                    if item.skipped_reason is None
                ],
                "skipped": [
                    item.public_metadata() for item in self.additional_loras
                    if item.skipped_reason is not None
                ],
            },
            "detail_callouts": [
                callout.public_metadata() for callout in self.detail_callouts
            ],
            "authored_settings": self.public_authored_settings(),
            "planning": self.planning.public_metadata(),
            "review": {
                **self.review_selection.public_metadata(),
                "status": "pending",
            },
        }


@dataclass(frozen=True)
class PackSheetGenerationRequest:
    schema_version: int
    planner_version: str
    mode: str
    intent: str
    reference_type: str
    preset: str
    creative_request: str
    model: str
    role: str
    label: str
    objective: str
    index: int
    sheet_count: int
    sheet_size: tuple[int, int]
    anchor_basis: str
    strategy: str
    routing_operation: str
    plan_seal: str
    source_role: str | None = None
    source_digest: str | None = None
    normalized_crop: tuple[float, float, float, float] | None = None
    operation: str | None = None
    detail_seal: str | None = None
    detail_custom_id: str | None = None
    detail_kind: str | None = None
    requested_operation: str | None = None
    detail_label_digest: str | None = None


@dataclass(frozen=True)
class PackSheetRepairRequest:
    schema_version: int
    planner_version: str
    mode: str
    reference_type: str
    creative_request: str
    model: str
    role: str
    label: str
    objective: str
    sheet_size: tuple[int, int]
    anchor_basis: str
    reason_codes: tuple[str, ...]
    routing_operation: str
    plan_seal: str


@dataclass(frozen=True)
class ReferencePackReviewRequest:
    instruction: str
    creative_request: str
    sheet_paths: tuple[Path, ...]
    sheet_roles: tuple[str, ...]
    response_schema: Mapping[str, Any]
    review_contract: str = "standard_fidelity_v1"


@dataclass(frozen=True)
class ReferencePackArtifact:
    path: Path
    role: str
    index: int
    model: str
    provenance: ArtifactProvenance
    anchor_role: str | None
    reason_codes: tuple[str, ...] = ()
    detail_provenance: Mapping[str, Any] | None = None

    def public_metadata(self) -> dict[str, Any]:
        metadata = {
            "schema_version": PACK_SCHEMA_VERSION,
            "planner_version": PACK_PLANNER_VERSION,
            "role": self.role,
            "index": self.index,
            "model": self.model,
            "provenance": {
                **self.provenance.public_metadata(),
                "anchor_role": self.anchor_role,
            },
            "reason_codes": list(self.reason_codes),
        }
        if self.detail_provenance is not None:
            metadata["detail"] = dict(self.detail_provenance)
        return metadata


@dataclass(frozen=True)
class ReferencePackResult:
    plan: ReferencePackPlan
    artifacts: tuple[ReferencePackArtifact, ...]
    review: SemanticReviewResult
    repaired_roles: tuple[str, ...]
    max_repair_attempts: int
    repair_attempts_used: int
    # Callback results are private source artifacts. The route owns their
    # lifecycle and removes this complete set after atomic store publication.
    private_source_paths: tuple[Path, ...]

    def public_metadata(self) -> dict[str, Any]:
        preview = self.plan.public_preview()
        preview.pop("candidate_count", None)
        preview["review"] = {
            **self.plan.review_selection.public_metadata(),
            "status": self.review.status,
        }
        return {
            **preview,
            "roles": {
                "sheets": list(self.plan.sheet_roles),
                "repaired": list(self.repaired_roles),
            },
            "reason_codes": list(self.review.reason_codes),
            "review_status": self.review.status,
            "max_repair_attempts": self.max_repair_attempts,
            "repair_attempts_used": self.repair_attempts_used,
        }


PanelGenerator = Callable[[PanelGenerationRequest], os.PathLike[str] | str]
PanelEditor = Callable[[Path, PanelGenerationRequest], os.PathLike[str] | str]
DraftGenerator = Callable[[DraftGenerationRequest], os.PathLike[str] | str]
SemanticReviewer = Callable[[SemanticReviewRequest], object]
PanelRepairer = Callable[[Path, FailedPanelRepairRequest], os.PathLike[str] | str]
PackSheetGenerator = Callable[[PackSheetGenerationRequest], os.PathLike[str] | str]
PackSheetEditor = Callable[
    [Path, Path, PackSheetGenerationRequest], os.PathLike[str] | str
]
PackSheetRepairer = Callable[
    [Path, Path, PackSheetRepairRequest], os.PathLike[str] | str
]
PackReviewer = Callable[[ReferencePackReviewRequest], object]


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
        producer_model = (
            _bounded_model(panel.model) if panel.model is not None else None
        )
        resolved.append(PanelFile(panel.role, path, producer_model))
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
            "reason_codes": {
                "type": "array",
                "items": {
                    "enum": [_REASON_FOR_CHECK[name] for name in _CHECK_NAMES],
                },
            },
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
    check_names: Sequence[str] = _CHECK_NAMES,
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
    required_checks = tuple(check_names)
    if (
        not required_checks
        or len(set(required_checks)) != len(required_checks)
        or any(name not in _REASON_FOR_CHECK for name in required_checks)
    ):
        raise ValueError("unsupported review check contract")
    if not isinstance(checks, Mapping) or set(checks) != set(required_checks):
        raise ReferenceSheetReviewError("review_unavailable")
    if any(type(checks[name]) is not bool for name in required_checks):
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
        _REASON_FOR_CHECK[name] for name in required_checks if checks[name] is False
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
        _REASON_FOR_CHECK[name] for name in required_checks if checks[name] is False
    )
    return SemanticReviewResult(
        status=status,
        checks=tuple((name, checks[name]) for name in required_checks),
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
    # Without a verified descriptor/inode snapshot, the path may have been
    # replaced since composition. Leaving an orphan is safer than deleting an
    # unowned regular file or symlink target supplied during that interval.
    if snapshot is None:
        return
    try:
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
    max_repair_attempts: int = 1,
    editor_model: str | None = None,
) -> ReferenceSheetResult:
    """Execute one plan through injected operations and deterministic composition.

    Callback outputs must be new, regular image files.  The service fingerprints
    sources around edit/repair calls and never overwrites a supplied source or an
    existing final path.  Non-draft modes can repair one deterministic panel per
    attempt, up to ``max_repair_attempts``. Draft mode never repairs panels.
    """
    plan = _validate_executable_plan(plan)
    if (
        isinstance(max_repair_attempts, bool)
        or not isinstance(max_repair_attempts, int)
        or not 0 <= max_repair_attempts <= 5
    ):
        raise ValueError("max_repair_attempts must be an integer from 0 through 5")
    effective_editor_model = (
        plan.model if editor_model is None else _bounded_model(editor_model)
    )
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
            panel_files.append(PanelFile(panel.role, path, plan.model))
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
        panel_files.append(PanelFile(anchor_recipe.role, anchor, plan.model))
        anchor_fingerprint = _fingerprint(anchor)
        for index, panel in enumerate(plan.panels[1:], start=1):
            edited = _new_distinct_path(
                edit_panel(anchor, _panel_request(plan, panel, index, "targeted_edit")),
                panel.role,
                generated_paths,
            )
            _assert_preserved(anchor, anchor_fingerprint, anchor_recipe.role)
            generated_paths.append(edited)
            panel_files.append(PanelFile(
                panel.role, edited, effective_editor_model,
            ))
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
            max_repair_attempts=max_repair_attempts,
            repair_attempts_used=0,
        )

    while True:
        try:
            validated = list(validate_panel_files(
                panel_files,
                expected_roles=plan.panel_roles,
                panel_size=plan.panel_size,
            ))
            break
        except ReferenceSheetStructureError as error:
            repair_roles = tuple(
                role for role in plan.panel_roles
                if role in set(error.failed_roles)
            )[:1]
            if (
                repair_panel is None
                or not repair_roles
                or len(repaired_roles) >= max_repair_attempts
            ):
                raise
            role = repair_roles[0]
            index = plan.panel_roles.index(role)
            original = _as_source_path(panel_files[index].path, role)
            fingerprint = _fingerprint(original)
            repaired = _new_distinct_path(
                repair_panel(
                    original,
                    _repair_request(plan, role, (error.reason_code,)),
                ),
                role,
                generated_paths,
            )
            _assert_preserved(original, fingerprint, role)
            generated_paths.append(repaired)
            repair_model = (
                effective_editor_model if plan.mode == "hybrid" else plan.model
            )
            panel_files[index] = PanelFile(role, repaired, repair_model)
            repaired_roles.append(role)

    composition_path = _staging_path(final_path)
    stage_snapshot = None
    try:
        geometry = compose_reference_sheet(plan, validated, composition_path)
        _protect_stage(composition_path)
        stage_snapshot = _stage_snapshot(composition_path, geometry.canvas_size)
        review = review_reference_sheet(plan, composition_path, reviewer)
        _assert_stage_unchanged(composition_path, stage_snapshot)
        repair_roles = build_failed_panel_repair_plan(plan, review)
        while (
            review.status == "fail"
            and repair_roles
            and repair_panel is not None
            and len(repaired_roles) < max_repair_attempts
        ):
            _remove_owned_output(composition_path, stage_snapshot)
            stage_snapshot = None
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
            repair_model = (
                effective_editor_model if plan.mode == "hybrid" else plan.model
            )
            validated[index] = PanelFile(role, repaired, repair_model)
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
            repair_roles = build_failed_panel_repair_plan(plan, review)
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
            model=panel.model or plan.model,
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
        max_repair_attempts=max_repair_attempts,
        repair_attempts_used=len(repaired_roles),
    )


def _managed_layout_public_metadata(mode: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": mode,
        "id": None,
        "provenance": {
            "kind": "server_allowlist",
            "version": "managed-layout-v1",
        },
    }


def normalize_reference_pack_type(value: object) -> str:
    if not isinstance(value, str) or value not in PACK_REFERENCE_TYPE_ALIASES:
        raise ValueError("reference_type is not supported")
    return PACK_REFERENCE_TYPE_ALIASES[value]


def _pack_authored_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise ValueError("authored option label cannot form an identifier")
    return slug[:96]


def reference_pack_type_field_capabilities(
    reference_type: str,
) -> list[dict[str, Any]]:
    """Return ordered, server-owned field groups and built-in option labels."""
    canonical = normalize_reference_pack_type(reference_type)
    result = []
    for field in PACK_TYPE_FIELDS[canonical]:
        groups = []
        for group_id, group_label, labels in PACK_TYPE_FIELD_GROUPS[canonical][field]:
            groups.append({
                "id": group_id,
                "label": group_label,
                "options": [
                    {
                        "id": f"{group_id}:{_pack_authored_slug(label)}",
                        "label": label,
                    }
                    for label in labels
                ],
            })
        result.append({"id": field, "groups": groups})
    return result


def reference_pack_detail_kind_capabilities(
    reference_type: str,
) -> list[dict[str, str]]:
    canonical = normalize_reference_pack_type(reference_type)
    return [
        {"id": kind, "label": kind.replace("_", " ").title()}
        for kind in sorted(PACK_DETAIL_KINDS[canonical])
    ]


def _pack_builtin_type_field_options(
    reference_type: str,
    field: str,
) -> dict[str, tuple[str, str]]:
    result = {}
    for capability in reference_pack_type_field_capabilities(reference_type):
        if capability["id"] != field:
            continue
        for group in capability["groups"]:
            for option in group["options"]:
                result[option["id"]] = (group["id"], option["label"])
    return result


def normalize_reference_pack_type_fields(
    value: object,
    *,
    reference_type: str,
) -> tuple[tuple[str, tuple[PackTypeFieldItem, ...]], ...]:
    """Normalize new ordered items and lossless legacy field strings."""
    canonical = normalize_reference_pack_type(reference_type)
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("type_fields must be an object")
    if set(value).difference(PACK_TYPE_FIELDS[canonical]):
        raise ValueError("type_fields do not match reference_type")
    result = []
    total_items = 0
    for field in PACK_TYPE_FIELDS[canonical]:
        if field not in value:
            continue
        raw_items = value[field]
        if isinstance(raw_items, str):
            if not raw_items.strip() or len(raw_items) > 10_000 or "\x00" in raw_items:
                raise ValueError("legacy type field must contain bounded text")
            items = (PackTypeFieldItem(
                item_id=f"legacy:{field}",
                label=raw_items,
                custom=True,
                group="legacy",
            ),)
        else:
            if (
                not isinstance(raw_items, Sequence)
                or isinstance(raw_items, (str, bytes))
                or len(raw_items) > 64
            ):
                raise ValueError("type field values must be an ordered list")
            normalized = []
            seen = set()
            builtins = _pack_builtin_type_field_options(canonical, field)
            allowed_groups = {
                group_id
                for group_id, _label, _options
                in PACK_TYPE_FIELD_GROUPS[canonical][field]
            }
            for raw_item in raw_items:
                if not isinstance(raw_item, Mapping) or set(raw_item) != {
                    "id", "label", "custom", "group",
                }:
                    raise ValueError("type field item schema is invalid")
                item_id = raw_item.get("id")
                label = raw_item.get("label")
                custom = raw_item.get("custom")
                group = raw_item.get("group")
                legacy_item = (
                    item_id == f"legacy:{field}"
                    and custom is True
                    and group == "legacy"
                )
                if legacy_item:
                    if (
                        len(raw_items) != 1
                        or not isinstance(label, str)
                        or not label.strip()
                        or len(label) > 10_000
                        or "\x00" in label
                    ):
                        raise ValueError("legacy type field item is invalid")
                    normalized.append(PackTypeFieldItem(
                        item_id=item_id,
                        label=label,
                        custom=True,
                        group="legacy",
                    ))
                    seen.add(item_id)
                    continue
                if (
                    not isinstance(item_id, str)
                    or _AUTHORED_ID_RE.fullmatch(item_id) is None
                    or item_id in seen
                    or type(custom) is not bool
                    or not isinstance(label, str)
                    or not label.strip()
                    or label != label.strip()
                    or len(label) > 500
                    or "\x00" in label
                    or not isinstance(group, str)
                ):
                    raise ValueError("type field item is invalid")
                if custom:
                    if not item_id.startswith("custom:") or group not in allowed_groups:
                        raise ValueError("custom type field item is invalid")
                else:
                    expected = builtins.get(item_id)
                    if expected != (group, label):
                        raise ValueError("built-in type field item is not canonical")
                seen.add(item_id)
                normalized.append(PackTypeFieldItem(item_id, label, custom, group))
            items = tuple(normalized)
        total_items += len(items)
        if total_items > 128:
            raise ValueError("type_fields contain too many authored items")
        result.append((field, items))
    return tuple(result)


def _pack_recipes(reference_type: str, preset: str, count: int) -> tuple[PackSheetRecipe, ...]:
    recipes = PACK_ROLE_RECIPES[reference_type]
    by_role = {recipe.role: recipe for recipe in recipes}
    derivative_roles = _PACK_PRESET_ROLE_ORDERS[(reference_type, preset)]
    ordered = (recipes[0], *(by_role[role] for role in derivative_roles))
    return tuple(ordered[:count])


def reference_pack_ordered_roles(
    reference_type: str,
    preset: str,
    count: int = MAX_PACK_SHEETS,
) -> tuple[str, ...]:
    canonical = normalize_reference_pack_type(reference_type)
    if preset not in PACK_TYPE_PRESETS[canonical]:
        raise ValueError("preset does not match reference_type")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
        raise ValueError("role count must be from 1 through 5")
    return tuple(item.role for item in _pack_recipes(canonical, preset, count))


def _pack_callout_source(
    reference_type: str,
    kind: str,
    sheet_roles: Sequence[str],
) -> str:
    anchor = sheet_roles[0]
    preferred = {
        ("character", "garment"): "wardrobe",
        ("character", "accessory"): "wardrobe",
        ("location", "lighting"): "lighting_states",
        ("location", "material"): "material_details",
        ("location", "fixture"): "material_details",
        ("world", "lighting"): "lighting_language",
        ("world", "composition"): "composition_language",
        ("world", "motion"): "motion_language",
        ("world", "material"): "material_language",
        ("wardrobe", "closure"): "construction",
        ("wardrobe", "seam"): "construction",
        ("wardrobe", "material"): "material_palette",
        ("wardrobe", "accessory"): "accessories",
        ("vehicle", "interior"): "interior",
        ("vehicle", "mechanism"): "mechanisms",
        ("vehicle", "control"): "interior",
    }.get((reference_type, kind), anchor)
    return preferred if preferred in sheet_roles else anchor


def _normalize_pack_callouts(
    value: object,
    *,
    reference_type: str,
    intent: str,
    sheet_roles: Sequence[str],
) -> tuple[PackDetailCallout, ...]:
    if value is None:
        return ()
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) > 8
    ):
        raise ValueError("detail_callouts must be a list of at most 8 objects")
    result: list[PackDetailCallout] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("detail callout schema is invalid")
        legacy = set(item) == {"kind", "operation"}
        if not legacy and set(item) != {
            "custom_id", "label", "kind", "operation", "source_role",
        }:
            raise ValueError("detail callout schema is invalid")
        kind = item.get("kind")
        operation = item.get("operation")
        if kind not in {*PACK_DETAIL_KINDS[reference_type], "custom"}:
            raise ValueError("detail callout kind does not match reference_type")
        if operation not in PACK_DETAIL_OPERATIONS:
            raise ValueError("detail callout operation is not supported")
        if intent == "exact_spec" and operation == "reconstruct":
            raise ValueError("exact_spec cannot reconstruct absent identity details")
        if legacy:
            if kind == "custom":
                raise ValueError("legacy detail callout kind is invalid")
            custom_id = f"builtin:{kind}"
            label = kind.replace("_", " ").title()
            source_role = _pack_callout_source(reference_type, kind, sheet_roles)
        else:
            custom_id = item.get("custom_id")
            label = item.get("label")
            source_role = item.get("source_role")
            if (
                not isinstance(custom_id, str)
                or _AUTHORED_ID_RE.fullmatch(custom_id) is None
                or not isinstance(label, str)
                or not label.strip()
                or label != label.strip()
                or len(label) > 500
                or "\x00" in label
                or source_role not in sheet_roles
            ):
                raise ValueError("detail callout identity or source is invalid")
            if kind == "custom":
                if not custom_id.startswith("custom:"):
                    raise ValueError("custom detail callout identity is invalid")
            else:
                canonical_label = kind.replace("_", " ").title()
                if custom_id != f"builtin:{kind}" or label != canonical_label:
                    raise ValueError("built-in detail callout identity is not canonical")
        if custom_id in seen:
            raise ValueError("detail callout identities must be unique")
        seen.add(custom_id)
        result.append(PackDetailCallout(
            custom_id=custom_id,
            label=label,
            kind=kind,
            requested_operation=operation,
            source_role=source_role,
        ))
    return tuple(result)


def _pack_seal(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reference_pack_authored_settings_seal(value: object) -> str:
    """Validate and seal one private, prompt-free authored-settings snapshot."""
    if not isinstance(value, Mapping) or set(value) != {
        "type_fields", "detail_callouts",
    }:
        raise ValueError("private authored settings are invalid")
    type_fields = value.get("type_fields")
    detail_callouts = value.get("detail_callouts")
    if (
        not isinstance(type_fields, Mapping)
        or any(
            not isinstance(field, str)
            or not isinstance(items, list)
            for field, items in type_fields.items()
        )
        or not isinstance(detail_callouts, list)
        or any(not isinstance(item, Mapping) for item in detail_callouts)
    ):
        raise ValueError("private authored settings are invalid")
    try:
        return _pack_seal({
            "type_fields": dict(type_fields),
            "detail_callouts": list(detail_callouts),
        })
    except (TypeError, ValueError) as error:
        raise ValueError("private authored settings are invalid") from error


def _normalize_pack_schedule(
    value: PackModelSchedule | Mapping[str, Any] | None,
    *,
    model: str | None,
) -> PackModelSchedule | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if set(value) != {"model", "steps", "guidance", "guidance_key", "source"}:
            raise ValueError("model schedule schema is invalid")
        value = PackModelSchedule(**dict(value))
    if not isinstance(value, PackModelSchedule) or value.model != model:
        raise ValueError("model schedule does not match selected model")
    if (
        isinstance(value.steps, bool)
        or not isinstance(value.steps, int)
        or not 1 <= value.steps <= 200
        or isinstance(value.guidance, bool)
        or not isinstance(value.guidance, (int, float))
        or not math.isfinite(float(value.guidance))
        or not 0 <= float(value.guidance) <= 30
        or value.guidance_key not in {"guidance_scale", "embedded_guidance_scale"}
        or value.source not in {"model_default", "explicit"}
    ):
        raise ValueError("model schedule is invalid")
    return PackModelSchedule(
        model=value.model,
        steps=value.steps,
        guidance=float(value.guidance),
        guidance_key=value.guidance_key,
        source=value.source,
    )


def _normalize_pack_operation_routing(
    value: Sequence[PackOperationRoute] | None,
    *,
    content_capability: str,
    generation_model: str,
    editor_model: str | None,
    generation_schedule: PackModelSchedule | None,
    editor_schedule: PackModelSchedule | None,
) -> tuple[PackOperationRoute, ...]:
    expected_models = {
        "generation": generation_model,
        "edit": editor_model,
        "repair": editor_model,
        "callout": editor_model,
    }
    expected_schedules = {
        "generation": generation_schedule,
        "edit": editor_schedule,
        "repair": editor_schedule,
        "callout": editor_schedule,
    }
    if value is None:
        status = "standard" if content_capability == "standard" else "skipped"
        reason = (
            None if status == "standard" else "no_verified_compatible_recipe"
        )
        return tuple(PackOperationRoute(
            operation=operation,
            requested_capability=content_capability,
            requested_model=expected_models[operation],
            resolved_model=expected_models[operation],
            status=status,
            schedule=expected_schedules[operation],
            reason=reason,
        ) for operation in PACK_OPERATION_ORDER)
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != len(PACK_OPERATION_ORDER)
    ):
        raise ValueError("operation_routing must contain every operation")
    routes = []
    for operation, route in zip(PACK_OPERATION_ORDER, value):
        if (
            not isinstance(route, PackOperationRoute)
            or route.operation != operation
            or route.requested_capability != content_capability
            or route.requested_model != expected_models[operation]
            or route.status not in PACK_OPERATION_STATUSES
        ):
            raise ValueError("operation routing does not match the sealed plan")
        normalized_schedule = _normalize_pack_schedule(
            route.schedule, model=route.resolved_model,
        )
        if route.status == "standard":
            if (
                content_capability != "standard"
                or route.resolved_model != route.requested_model
                or normalized_schedule != expected_schedules[operation]
                or route.recipe_id is not None
                or route.verification_status is not None
                or route.reason is not None
            ):
                raise ValueError("standard operation routing is invalid")
        elif route.status == "skipped":
            if (
                content_capability != "unrestricted_local"
                or route.resolved_model != route.requested_model
                or normalized_schedule != expected_schedules[operation]
                or route.recipe_id is not None
                or route.verification_status is not None
                or route.reason != "no_verified_compatible_recipe"
            ):
                raise ValueError("skipped operation routing is invalid")
        else:
            if (
                content_capability != "unrestricted_local"
                or route.requested_model is None
                or route.resolved_model is None
                or route.recipe_id is None
                or route.verification_status != "verified"
                or route.reason is not None
                or normalized_schedule is None
            ):
                raise ValueError("applied operation routing is invalid")
            _bounded_model(route.resolved_model)
            _bounded_model(route.recipe_id)
        routes.append(PackOperationRoute(
            operation=route.operation,
            requested_capability=route.requested_capability,
            requested_model=route.requested_model,
            resolved_model=route.resolved_model,
            status=route.status,
            schedule=normalized_schedule,
            recipe_id=route.recipe_id,
            verification_status=route.verification_status,
            reason=route.reason,
        ))
    return tuple(routes)


def build_reference_pack_plan(
    *,
    reference_type: str,
    mode: str,
    intent: str,
    depth: str,
    creative_request: str,
    generation_model: str,
    editor_model: str | None,
    preset: str | None = None,
    sheet_count: int | None = None,
    sheet_size: Sequence[int] = (1024, 1024),
    anchor_basis: str | None = None,
    managed_layout_assist: str = "off",
    user_lora_count: int = 0,
    type_fields: object = None,
    detail_callouts: object = None,
    planning: PackIntelligenceSelection | None = None,
    review_selection: PackIntelligenceSelection | None = None,
    generation_schedule: PackModelSchedule | Mapping[str, Any] | None = None,
    editor_schedule: PackModelSchedule | Mapping[str, Any] | None = None,
    content_capability: str = "standard",
    private_output: bool | None = None,
    initial_blur: bool | None = None,
    intelligence_policy: str = "standard_auto",
    review_contract: str | None = None,
    operation_routing: Sequence[PackOperationRoute] | None = None,
    additional_loras: Sequence[PackLoraSelection] = (),
    role_briefs: Mapping[str, str] | None = None,
) -> ReferencePackPlan:
    """Build one immutable, prompt-private v2 candidate-pack plan."""
    canonical_type = normalize_reference_pack_type(reference_type)
    if mode not in MODES:
        raise ValueError("mode must be production, hybrid, or draft")
    if intent not in PACK_INTENTS:
        raise ValueError("intent must be exact_spec, generic, or brainstorming")
    if depth not in PACK_DEPTHS:
        raise ValueError("depth must be compact, standard, comprehensive, or custom")
    if not isinstance(creative_request, str) or not creative_request.strip():
        raise ValueError("creative_request must be a non-empty string")
    if len(creative_request) > 50_000:
        raise ValueError("creative_request is too long")
    selected_preset = preset or PACK_DEFAULT_PRESETS[canonical_type]
    if selected_preset not in PACK_TYPE_PRESETS[canonical_type]:
        raise ValueError("preset does not match reference_type")
    if depth == "custom":
        if (
            isinstance(sheet_count, bool)
            or not isinstance(sheet_count, int)
            or not 1 <= sheet_count <= MAX_PACK_SHEETS
        ):
            raise ValueError("custom depth requires sheet_count from 1 through 5")
        resolved_count = sheet_count
    else:
        if sheet_count is not None:
            raise ValueError("sheet_count is allowed only for custom depth")
        resolved_count = PACK_DEPTH_COUNTS[depth]

    if canonical_type in {"character", "creature"}:
        resolved_basis = anchor_basis or (
            "anatomy" if selected_preset == "anatomy" else "primary_outfit"
        )
        if resolved_basis not in {"anatomy", "primary_outfit"}:
            raise ValueError("character and creature anchor_basis is invalid")
        if selected_preset == "anatomy" and resolved_basis != "anatomy":
            raise ValueError("anatomy preset requires anatomy anchor_basis")
    else:
        resolved_basis = anchor_basis or "least_occluded"
        if resolved_basis != "least_occluded":
            raise ValueError("reference_type requires least_occluded anchor_basis")

    if managed_layout_assist != "off":
        raise ValueError("managed layout assist is not allowlisted")
    if content_capability not in {"standard", "unrestricted_local"}:
        raise ValueError("content_capability is invalid")
    if private_output is not None and type(private_output) is not bool:
        raise ValueError("private_output must be a boolean")
    resolved_private = resolved_basis == "anatomy" if private_output is None else private_output
    if initial_blur is None:
        initial_blur = resolved_basis == "anatomy"
    if type(initial_blur) is not bool:
        raise ValueError("initial_blur must be a boolean")
    if intelligence_policy not in {"standard_auto", "uncensored_auto"}:
        raise ValueError("intelligence_policy is invalid")
    resolved_review_contract = review_contract or (
        "explicit_unrestricted_fidelity_v1"
        if content_capability == "unrestricted_local"
        else "standard_fidelity_v1"
    )
    if resolved_review_contract not in PACK_REVIEW_CONTRACTS:
        raise ValueError("review_contract is invalid")
    if (
        content_capability == "unrestricted_local"
        and resolved_review_contract != "explicit_unrestricted_fidelity_v1"
    ):
        raise ValueError(
            "unrestricted_local requires the explicit unrestricted fidelity review contract"
        )
    if (
        not isinstance(additional_loras, Sequence)
        or isinstance(additional_loras, (str, bytes))
        or len(additional_loras) > 64
        or any(not isinstance(item, PackLoraSelection) for item in additional_loras)
    ):
        raise ValueError("additional_loras must contain sealed selections")
    sealed_loras = tuple(additional_loras)
    if (
        isinstance(user_lora_count, bool)
        or not isinstance(user_lora_count, int)
        or not 0 <= user_lora_count <= 64
    ):
        raise ValueError("user_lora_count must be from 0 through 64")
    create_model = _bounded_model(generation_model)
    resolved_editor = None if editor_model is None else _bounded_model(editor_model)
    if mode != "draft" and resolved_editor is None:
        raise ValueError("anchored packs require editor_model")
    if mode == "draft" and resolved_editor is not None:
        raise ValueError("draft packs do not use editor_model")
    resolved_generation_schedule = _normalize_pack_schedule(
        generation_schedule, model=create_model,
    )
    resolved_editor_schedule = _normalize_pack_schedule(
        editor_schedule, model=resolved_editor,
    )
    if mode == "draft" and resolved_editor_schedule is not None:
        raise ValueError("draft packs do not use an editor schedule")
    sealed_operation_routing = _normalize_pack_operation_routing(
        operation_routing,
        content_capability=content_capability,
        generation_model=create_model,
        editor_model=resolved_editor,
        generation_schedule=resolved_generation_schedule,
        editor_schedule=resolved_editor_schedule,
    )
    recipes = _pack_recipes(canonical_type, selected_preset, resolved_count)
    if role_briefs is not None:
        if not isinstance(role_briefs, Mapping) or set(role_briefs) != {
            recipe.role for recipe in recipes
        }:
            raise ValueError("role_briefs must exactly match planned roles")
        normalized_recipes = []
        for recipe in recipes:
            brief = role_briefs.get(recipe.role)
            if (
                not isinstance(brief, str)
                or not brief.strip()
                or len(brief.strip()) > 500
                or "\x00" in brief
            ):
                raise ValueError("role brief must contain 1 through 500 characters")
            normalized_recipes.append(PackSheetRecipe(
                recipe.role, recipe.label, brief.strip(),
            ))
        recipes = tuple(normalized_recipes)
    normalized_type_fields = normalize_reference_pack_type_fields(
        type_fields,
        reference_type=canonical_type,
    )
    callouts = _normalize_pack_callouts(
        detail_callouts,
        reference_type=canonical_type,
        intent=intent,
        sheet_roles=tuple(recipe.role for recipe in recipes),
    )
    if mode == "draft" and callouts:
        raise ValueError("draft packs do not support editor-dependent detail callouts")
    planning = planning or PackIntelligenceSelection(
        "deterministic", "deterministic", "local",
    )
    review_selection = review_selection or PackIntelligenceSelection(
        "off", None, "off",
    )
    if not isinstance(planning, PackIntelligenceSelection) or not isinstance(
        review_selection, PackIntelligenceSelection,
    ):
        raise ValueError("planning and review selections must be sealed")
    for selection in (planning, review_selection):
        if (
            not isinstance(selection.requested_model, str)
            or not selection.requested_model
            or len(selection.requested_model) > 256
            or (
                selection.resolved_model is not None
                and (
                    not isinstance(selection.resolved_model, str)
                    or not selection.resolved_model
                    or len(selection.resolved_model) > 256
                )
            )
            or not isinstance(selection.resolved_provider, str)
            or not selection.resolved_provider
            or len(selection.resolved_provider) > 64
            or (
                selection.selection_revision != "legacy"
                and (
                    not isinstance(selection.selection_revision, str)
                    or re.fullmatch(r"[0-9a-f]{64}", selection.selection_revision)
                    is None
                )
            )
        ):
            raise ValueError("planning and review selections must be sealed")
    private_authored_settings = {
        "type_fields": {
            field: [item.private_metadata() for item in items]
            for field, items in normalized_type_fields
        },
        "detail_callouts": [item.private_metadata() for item in callouts],
    }
    authored_settings_seal = reference_pack_authored_settings_seal(
        private_authored_settings,
    )
    seal_payload = {
        "schema_version": PACK_SCHEMA_VERSION,
        "planner_version": PACK_PLANNER_VERSION,
        "mode": mode,
        "intent": intent,
        "reference_type": canonical_type,
        "preset": selected_preset,
        "depth": depth,
        "sheet_roles": [recipe.role for recipe in recipes],
        "sheet_size": list(_dimensions(sheet_size, "sheet_size")),
        "anchor_basis": resolved_basis,
        "private_output": resolved_private,
        "anchor_privacy": (
            f"{'private' if resolved_private else 'project'}_"
            f"{'blurred' if initial_blur else 'visible'}"
        ),
        "managed_layout_assist": managed_layout_assist,
        "user_lora_count": user_lora_count,
        "authored_settings": private_authored_settings,
        "authored_settings_seal": authored_settings_seal,
        "generation_model": create_model,
        "editor_model": resolved_editor,
        "planning": planning.public_metadata(),
        "review": review_selection.public_metadata(),
        "content_capability": content_capability,
        "initial_blur": initial_blur,
        "intelligence_policy": intelligence_policy,
        **(
            {"review_contract": resolved_review_contract}
            if resolved_review_contract != "standard_fidelity_v1" else {}
        ),
        "operation_routing": [
            item.public_metadata() | {"operation": item.operation}
            for item in sealed_operation_routing
        ],
        "additional_loras": [{
            "id": item.lora_id,
            "weight": item.multiplier,
            "requested_scope": item.requested_scope,
            "resolved_scopes": list(item.resolved_scopes),
            "roles": list(item.roles),
            "skipped_reason": item.skipped_reason,
        } for item in sealed_loras],
        "generation_schedule": (
            resolved_generation_schedule.public_metadata()
            if resolved_generation_schedule is not None else None
        ),
        "editor_schedule": (
            resolved_editor_schedule.public_metadata()
            if resolved_editor_schedule is not None else None
        ),
        "creative_request_digest": hashlib.sha256(
            creative_request.encode("utf-8")
        ).hexdigest(),
    }
    plan_seal = _pack_seal(seal_payload)
    resource_seal = _pack_seal({
        "plan_seal": plan_seal,
        "additional_loras": [item.__dict__ for item in sealed_loras],
    })
    role_brief_seal = _pack_seal({
        "plan_seal": plan_seal,
        "role_brief_digests": {
            recipe.role: hashlib.sha256(recipe.objective.encode("utf-8")).hexdigest()
            for recipe in recipes
        },
    })
    return ReferencePackPlan(
        schema_version=PACK_SCHEMA_VERSION,
        planner_version=PACK_PLANNER_VERSION,
        mode=mode,
        intent=intent,
        reference_type=canonical_type,
        preset=selected_preset,
        depth=depth,
        creative_request=creative_request,
        generation_model=create_model,
        editor_model=resolved_editor,
        sheets=recipes,
        sheet_size=_dimensions(sheet_size, "sheet_size"),
        anchor_basis=resolved_basis,
        anchor_privacy=(
            f"{'private' if resolved_private else 'project'}_"
            f"{'blurred' if initial_blur else 'visible'}"
        ),
        private_output=resolved_private,
        managed_layout_assist=managed_layout_assist,
        user_lora_count=user_lora_count,
        type_fields=normalized_type_fields,
        detail_callouts=callouts,
        planning=planning,
        review_selection=review_selection,
        generation_schedule=resolved_generation_schedule,
        editor_schedule=resolved_editor_schedule,
        content_capability=content_capability,
        initial_blur=initial_blur,
        intelligence_policy=intelligence_policy,
        review_contract=resolved_review_contract,
        operation_routing=sealed_operation_routing,
        additional_loras=sealed_loras,
        resource_seal=resource_seal,
        role_brief_seal=role_brief_seal,
        authored_settings_seal=authored_settings_seal,
        plan_seal=plan_seal,
    )


def _validate_reference_pack_plan(plan: object) -> ReferencePackPlan:
    if not isinstance(plan, ReferencePackPlan):
        raise TypeError("unsupported reference-pack plan")
    try:
        canonical = build_reference_pack_plan(
            reference_type=plan.reference_type,
            mode=plan.mode,
            intent=plan.intent,
            depth=plan.depth,
            creative_request=plan.creative_request,
            generation_model=plan.generation_model,
            editor_model=plan.editor_model,
            preset=plan.preset,
            sheet_count=(len(plan.sheets) if plan.depth == "custom" else None),
            sheet_size=plan.sheet_size,
            anchor_basis=plan.anchor_basis,
            managed_layout_assist=plan.managed_layout_assist,
            user_lora_count=plan.user_lora_count,
            type_fields=plan.private_authored_settings()["type_fields"],
            detail_callouts=plan.private_authored_settings()["detail_callouts"],
            planning=plan.planning,
            review_selection=plan.review_selection,
            generation_schedule=plan.generation_schedule,
            editor_schedule=plan.editor_schedule,
            content_capability=plan.content_capability,
            private_output=plan.private_output,
            initial_blur=plan.initial_blur,
            intelligence_policy=plan.intelligence_policy,
            review_contract=plan.review_contract,
            operation_routing=plan.operation_routing,
            additional_loras=plan.additional_loras,
            role_briefs={item.role: item.objective for item in plan.sheets},
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported reference-pack plan") from exc
    if canonical != plan:
        raise ValueError("unsupported reference-pack plan")
    return plan


def apply_reference_pack_role_briefs(
    plan: ReferencePackPlan,
    role_briefs: Mapping[str, str],
) -> ReferencePackPlan:
    """Seal validated planner briefs without changing server-owned structure."""
    _validate_reference_pack_plan(plan)
    return build_reference_pack_plan(
        reference_type=plan.reference_type,
        mode=plan.mode,
        intent=plan.intent,
        depth=plan.depth,
        creative_request=plan.creative_request,
        generation_model=plan.generation_model,
        editor_model=plan.editor_model,
        preset=plan.preset,
        sheet_count=(len(plan.sheets) if plan.depth == "custom" else None),
        sheet_size=plan.sheet_size,
        anchor_basis=plan.anchor_basis,
        managed_layout_assist=plan.managed_layout_assist,
        user_lora_count=plan.user_lora_count,
        type_fields=plan.private_authored_settings()["type_fields"],
        detail_callouts=plan.private_authored_settings()["detail_callouts"],
        planning=plan.planning,
        review_selection=plan.review_selection,
        generation_schedule=plan.generation_schedule,
        editor_schedule=plan.editor_schedule,
        content_capability=plan.content_capability,
        private_output=plan.private_output,
        initial_blur=plan.initial_blur,
        intelligence_policy=plan.intelligence_policy,
        review_contract=plan.review_contract,
        operation_routing=plan.operation_routing,
        additional_loras=plan.additional_loras,
        role_briefs=role_briefs,
    )


def _pack_generation_request(
    plan: ReferencePackPlan,
    recipe: PackSheetRecipe,
    index: int,
    *,
    strategy: str,
    routing_operation: str | None = None,
    source_role: str | None = None,
    source_digest: str | None = None,
    operation: str | None = None,
    normalized_crop: tuple[float, float, float, float] | None = None,
    callout: PackDetailCallout | None = None,
) -> PackSheetGenerationRequest:
    if routing_operation is None:
        routing_operation = (
            "generation"
            if strategy in {"canonical_anchor", "draft_one_shot"}
            else "edit"
        )
    route = plan.operation_route(routing_operation)
    if route.resolved_model is None:
        raise ValueError("operation routing has no executable model")
    crop = (
        normalized_crop
        if normalized_crop is not None
        else (0.0, 0.0, 1.0, 1.0) if source_role is not None else None
    )
    detail_seal = None
    if source_role is not None and source_digest is not None and operation is not None:
        detail_seal = _pack_seal({
            "plan_seal": plan.plan_seal,
            "target_role": recipe.role,
            "source_role": source_role,
            "source_digest": source_digest,
            "normalized_crop": crop,
            "operation": operation,
            "editor_model": None if operation == "crop" else route.resolved_model,
            "custom_id": None if callout is None else callout.custom_id,
            "kind": None if callout is None else callout.kind,
            "requested_operation": (
                None if callout is None else callout.requested_operation
            ),
            "label_digest": None if callout is None else callout.label_digest,
        })
    return PackSheetGenerationRequest(
        schema_version=plan.schema_version,
        planner_version=plan.planner_version,
        mode=plan.mode,
        intent=plan.intent,
        reference_type=plan.reference_type,
        preset=plan.preset,
        creative_request=plan.creative_request,
        model=route.resolved_model,
        role=recipe.role,
        label=recipe.label,
        objective=recipe.objective,
        index=index,
        sheet_count=len(plan.output_roles),
        sheet_size=plan.sheet_size,
        anchor_basis=plan.anchor_basis,
        strategy=strategy,
        routing_operation=routing_operation,
        plan_seal=plan.plan_seal,
        source_role=source_role,
        source_digest=source_digest,
        normalized_crop=crop,
        operation=operation,
        detail_seal=detail_seal,
        detail_custom_id=None if callout is None else callout.custom_id,
        detail_kind=None if callout is None else callout.kind,
        requested_operation=(
            None if callout is None else callout.requested_operation
        ),
        detail_label_digest=None if callout is None else callout.label_digest,
    )


def build_reference_pack_review_request(
    plan: ReferencePackPlan,
    artifacts: Sequence[ReferencePackArtifact],
) -> ReferencePackReviewRequest:
    plan = _validate_reference_pack_plan(plan)
    roles = tuple(artifact.role for artifact in artifacts)
    if roles != plan.output_roles:
        raise ReferenceSheetStructureError("pack_roles_invalid")
    unrestricted = plan.review_contract == "explicit_unrestricted_fidelity_v1"
    check_names = _UNRESTRICTED_CHECK_NAMES if unrestricted else _CHECK_NAMES
    reason_codes = tuple(_REASON_FOR_CHECK[name] for name in check_names)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "checks", "failed_roles", "reason_codes"],
        "properties": {
            "status": {"enum": ["pass", "fail"]},
            "checks": {
                "type": "object",
                "additionalProperties": False,
                "required": list(check_names),
                "properties": {name: {"type": "boolean"} for name in check_names},
            },
            "failed_roles": {
                "type": "array",
                "items": {"enum": list(roles)},
                "maxItems": len(roles),
                "uniqueItems": True,
            },
            "reason_codes": {
                "type": "array",
                "items": {"enum": list(reason_codes)},
                "maxItems": len(reason_codes),
                "uniqueItems": True,
            },
        },
    }
    return ReferencePackReviewRequest(
        instruction=(
            "Review only visual fidelity, completeness, and cross-sheet continuity "
            "against the supplied authored request and ordered roles. "
            + (
                "Also compare whether the outputs preserve any mature, violent, and "
                "detail register explicitly requested by the author, plus overall "
                "fidelity. A register absent from the authored request passes without "
                "inference. "
                if unrestricted else ""
            )
            + "Do not perform content moderation, permissibility decisions, maturity "
            "classification, refusal analysis, policy or safety analysis, or infer a "
            "prompt/register the author did not supply. Return only the strict JSON object."
        ),
        creative_request=plan.creative_request,
        sheet_paths=tuple(artifact.path for artifact in artifacts),
        sheet_roles=roles,
        response_schema=schema,
        review_contract=plan.review_contract,
    )


def _review_descriptor_path(snapshot: _StageSnapshot) -> Path | None:
    """Return a verified stable pathname for an already-open reviewed file."""
    for descriptor_root in (Path("/proc/self/fd"), Path("/dev/fd")):
        candidate = descriptor_root / str(snapshot.descriptor)
        try:
            candidate_stat = candidate.stat()
        except OSError:
            continue
        if (
            candidate_stat.st_dev == snapshot.device
            and candidate_stat.st_ino == snapshot.inode
            and stat.S_ISREG(candidate_stat.st_mode)
        ):
            return candidate
    return None


def review_reference_pack(
    plan: ReferencePackPlan,
    artifacts: Sequence[ReferencePackArtifact],
    reviewer: PackReviewer | None,
) -> SemanticReviewResult:
    if reviewer is None:
        return _review_unavailable()
    review_request = build_reference_pack_review_request(plan, artifacts)
    snapshots: list[tuple[Path, _StageSnapshot]] = []
    try:
        for artifact in artifacts:
            snapshots.append((
                artifact.path,
                _stage_snapshot(artifact.path, plan.sheet_size),
            ))
        review_paths = tuple(
            _review_descriptor_path(snapshot)
            for _path, snapshot in snapshots
        )
        try:
            if any(path is None for path in review_paths):
                result = _review_unavailable()
            else:
                request = replace(
                    review_request,
                    sheet_paths=tuple(review_paths),
                )
                result = parse_semantic_review_result(
                    reviewer(request), allowed_roles=plan.output_roles,
                    check_names=(
                        _UNRESTRICTED_CHECK_NAMES
                        if plan.review_contract == "explicit_unrestricted_fidelity_v1"
                        else _CHECK_NAMES
                    ),
                )
        except Exception:  # noqa: BLE001 - normalize provider/schema failure
            result = _review_unavailable()
        # The route decides whether unavailable review blocks publication.
        # Provider mutation is always a structural integrity failure and must
        # never be converted into review_unavailable.
        for path, snapshot in snapshots:
            _assert_stage_unchanged(path, snapshot)
        return SemanticReviewResult(
            status=result.status,
            checks=result.checks,
            failed_roles=result.failed_roles,
            reason_codes=result.reason_codes,
            artifact_seals=tuple(
                _ReviewedArtifactSeal(
                    role=artifact.role,
                    index=artifact.index,
                    device=snapshot.device,
                    inode=snapshot.inode,
                    size=snapshot.size,
                    sha256=snapshot.digest,
                )
                for artifact, (_path, snapshot) in zip(artifacts, snapshots)
            ),
        )
    finally:
        for _path, snapshot in snapshots:
            try:
                os.close(snapshot.descriptor)
            except OSError:
                pass


def _pack_repair_request(
    plan: ReferencePackPlan,
    recipe: PackSheetRecipe,
    reason_codes: Sequence[str],
) -> PackSheetRepairRequest:
    route = plan.operation_route("repair")
    if route.resolved_model is None:
        raise ValueError("repair routing has no executable model")
    return PackSheetRepairRequest(
        schema_version=plan.schema_version,
        planner_version=plan.planner_version,
        mode=plan.mode,
        reference_type=plan.reference_type,
        creative_request=plan.creative_request,
        model=route.resolved_model,
        role=recipe.role,
        label=recipe.label,
        objective=recipe.objective,
        sheet_size=plan.sheet_size,
        anchor_basis=plan.anchor_basis,
        reason_codes=tuple(reason_codes),
        routing_operation="repair",
        plan_seal=plan.plan_seal,
    )


def _stage_pack_detail_crop(
    source_path: Path,
    *,
    target_size: tuple[int, int],
) -> tuple[Path, tuple[float, float, float, float], bool]:
    """Create a deterministic center crop and report if it needs no editor."""
    with Image.open(source_path) as source:
        image = source.convert("RGB")
        image.load()
    try:
        source_width, source_height = image.size
        target_width, target_height = target_size
        callout_width = max(1, target_width // 2)
        callout_height = max(1, target_height // 2)
        sufficient = (
            source_width >= callout_width
            and source_height >= callout_height
        )
        if sufficient:
            crop_width, crop_height = callout_width, callout_height
        else:
            crop_width = max(1, min(source_width, max(target_width // 2, source_width * 3 // 4)))
            crop_height = max(1, min(source_height, max(target_height // 2, source_height * 3 // 4)))
        left = (source_width - crop_width) // 2
        top = (source_height - crop_height) // 2
        right = left + crop_width
        bottom = top + crop_height
        normalized = (
            left / source_width,
            top / source_height,
            right / source_width,
            bottom / source_height,
        )
        cropped = image.crop((left, top, right, bottom))
        composed = Image.new("RGB", target_size, (22, 22, 26))
        composed.paste(
            cropped,
            ((target_width - crop_width) // 2, (target_height - crop_height) // 2),
        )
        staged = _staging_path(source_path.with_name(f"{source_path.stem}-detail.png"))
        try:
            _save_new_png(composed, staged)
        finally:
            cropped.close()
            composed.close()
        return staged.resolve(), normalized, sufficient
    finally:
        image.close()


def create_reference_pack(
    plan: ReferencePackPlan,
    *,
    generate_sheet: PackSheetGenerator,
    edit_sheet: PackSheetEditor | None = None,
    reviewer: PackReviewer | None = None,
    repair_sheet: PackSheetRepairer | None = None,
    max_repair_attempts: int = 1,
) -> ReferencePackResult:
    """Execute one ordered candidate pack with exactly one immutable anchor."""
    plan = _validate_reference_pack_plan(plan)
    if not callable(generate_sheet):
        raise ValueError("generate_sheet is required")
    if plan.mode != "draft" and not callable(edit_sheet):
        raise ValueError("anchored packs require edit_sheet")
    if (
        isinstance(max_repair_attempts, bool)
        or not isinstance(max_repair_attempts, int)
        or not 0 <= max_repair_attempts <= 5
    ):
        raise ValueError("max_repair_attempts must be an integer from 0 through 5")

    paths: list[Path] = []
    owned_identities: dict[Path, tuple[int, int]] = {}
    artifacts: list[ReferencePackArtifact] = []
    repaired_roles: list[str] = []

    def remember_owned(path: Path) -> None:
        try:
            current = path.lstat()
            if stat.S_ISREG(current.st_mode):
                owned_identities[path] = (current.st_dev, current.st_ino)
            sidecar = path.with_suffix(".meta.json")
            sidecar_stat = sidecar.lstat()
            if stat.S_ISREG(sidecar_stat.st_mode):
                owned_identities[sidecar] = (
                    sidecar_stat.st_dev, sidecar_stat.st_ino,
                )
        except OSError:
            pass

    def track_artifact(
        path: Path,
        *,
        role: str,
        index: int,
        model: str,
        strategy: str,
        anchor_role: str | None,
        reason_codes: Sequence[str] = (),
        detail_provenance: Mapping[str, Any] | None = None,
    ) -> ReferencePackArtifact:
        paths.append(path)
        remember_owned(path)
        if _image_size(path, role) != plan.sheet_size:
            raise ReferenceSheetStructureError(
                "pack_sheet_dimensions_invalid", failed_roles=(role,),
            )
        return ReferencePackArtifact(
            path=path,
            role=role,
            index=index,
            model=model,
            provenance=ArtifactProvenance(strategy, plan.planner_version),
            anchor_role=anchor_role,
            reason_codes=tuple(reason_codes),
            detail_provenance=detail_provenance,
        )

    def build_detail_artifact(
        callout: PackDetailCallout,
        callout_index: int,
        current_artifacts: Sequence[ReferencePackArtifact],
        canonical_path: Path,
        canonical_fingerprint: tuple[int, str],
        *,
        provenance_strategy: str = "detail_callout",
        reason_codes: Sequence[str] = (),
    ) -> ReferencePackArtifact:
        base_artifacts = {
            item.role: item
            for item in current_artifacts
            if item.role in plan.sheet_roles
        }
        source = base_artifacts.get(callout.source_role)
        if source is None:
            raise ReferenceSheetStructureError(
                "pack_detail_source_unavailable",
                failed_roles=(callout.source_role,),
            )
        source_digest = _fingerprint(source.path)[1]
        normalized_crop = (0.0, 0.0, 1.0, 1.0)
        editor_primary = source.path
        direct_crop = False
        if callout.requested_operation in {"auto", "crop"}:
            editor_primary, normalized_crop, direct_crop = _stage_pack_detail_crop(
                source.path, target_size=plan.sheet_size,
            )
            if not direct_crop:
                paths.append(editor_primary)
                remember_owned(editor_primary)
        operation = (
            "crop"
            if direct_crop
            else "inpaint"
            if callout.requested_operation == "reconstruct"
            else "enhance"
        )
        artifact_index = len(plan.sheets) + callout_index
        recipe = PackSheetRecipe(
            role=callout.target_role,
            label=callout.label,
            objective=f"authored detail target: {callout.label}",
        )
        request = _pack_generation_request(
            plan,
            recipe,
            artifact_index,
            strategy=provenance_strategy,
            routing_operation="callout",
            source_role=callout.source_role,
            source_digest=source_digest,
            operation=operation,
            normalized_crop=normalized_crop,
            callout=callout,
        )
        if direct_crop:
            path = editor_primary
            strategy = (
                "deterministic_crop"
                if provenance_strategy == "detail_callout"
                else provenance_strategy
            )
            model = "deterministic"
        else:
            path = _new_distinct_path(
                edit_sheet(editor_primary, canonical_path, request),
                callout.target_role,
                paths,
            )
            strategy = provenance_strategy
            model = request.model
        _assert_preserved(
            canonical_path, canonical_fingerprint, plan.sheet_roles[0],
        )
        return track_artifact(
            path,
            role=callout.target_role,
            index=artifact_index,
            model=model,
            strategy=strategy,
            anchor_role=plan.sheet_roles[0],
            reason_codes=reason_codes,
            detail_provenance={
                "custom_id": callout.custom_id,
                "kind": callout.kind,
                "source_role": callout.source_role,
                "source_digest": source_digest,
                "normalized_crop": list(request.normalized_crop or ()),
                "requested_operation": callout.requested_operation,
                "resolved_operation": operation,
                "editor_model": None if direct_crop else request.model,
                "label_digest": callout.label_digest,
                "seal": request.detail_seal,
            },
        )
    try:
        anchor_path: Path | None = None
        anchor_fingerprint: tuple[int, str] | None = None
        for index, recipe in enumerate(plan.sheets):
            if plan.mode == "draft":
                request = _pack_generation_request(
                    plan, recipe, index, strategy="draft_one_shot",
                )
                path = _new_distinct_path(
                    generate_sheet(request), recipe.role, paths,
                )
                strategy = "draft_one_shot"
                model = request.model
                anchor_role = None
                detail = None
            elif index == 0:
                request = _pack_generation_request(
                    plan, recipe, index, strategy="canonical_anchor",
                )
                path = _new_distinct_path(
                    generate_sheet(request), recipe.role, paths,
                )
                anchor_path = path
                anchor_fingerprint = _fingerprint(path)
                strategy = "canonical_anchor"
                model = request.model
                anchor_role = recipe.role
                detail = None
            else:
                assert anchor_path is not None and anchor_fingerprint is not None
                source_role = plan.sheet_roles[0]
                source_path = anchor_path
                source_digest = _fingerprint(source_path)[1]
                request = _pack_generation_request(
                    plan,
                    recipe,
                    index,
                    strategy="reference_guided_derivative",
                    routing_operation="edit",
                    source_role=source_role,
                    source_digest=source_digest,
                    operation="enhance",
                )
                path = _new_distinct_path(
                    edit_sheet(source_path, anchor_path, request),
                    recipe.role,
                    paths,
                )
                strategy = "reference_guided_derivative"
                model = request.model
                _assert_preserved(anchor_path, anchor_fingerprint, plan.sheet_roles[0])
                anchor_role = plan.sheet_roles[0]
                detail = None
            artifacts.append(track_artifact(
                path,
                role=recipe.role,
                index=index,
                model=model,
                strategy=strategy,
                anchor_role=anchor_role,
                detail_provenance=detail,
            ))

        # Detail callouts are authored output targets, not hints attached to a
        # coincidentally named base recipe. Execute each one exactly once in
        # authored order after every allowed source role is available.
        if plan.detail_callouts:
            assert anchor_path is not None and anchor_fingerprint is not None
        for callout_index, callout in enumerate(plan.detail_callouts):
            artifacts.append(build_detail_artifact(
                callout,
                callout_index,
                artifacts,
                anchor_path,
                anchor_fingerprint,
            ))

        review = review_reference_pack(plan, artifacts, reviewer)
        if plan.mode != "draft":
            assert anchor_path is not None and anchor_fingerprint is not None
            while (
                review.status == "fail"
                and callable(repair_sheet)
                and len(repaired_roles) < max_repair_attempts
            ):
                role = next((
                    candidate for candidate in plan.output_roles
                    if candidate in set(review.failed_roles)
                ), None)
                if role is None:
                    break
                if role == plan.sheet_roles[0]:
                    # A rejected canonical anchor invalidates every derivative.
                    # Regenerate the whole candidate into new files in one
                    # bounded attempt; prior artifacts remain immutable cleanup
                    # inputs and retain their original provenance.
                    previous = tuple(
                        (artifact, _fingerprint(artifact.path))
                        for artifact in artifacts
                    )
                    anchor_recipe = plan.sheets[0]
                    anchor_request = _pack_generation_request(
                        plan,
                        anchor_recipe,
                        0,
                        strategy="canonical_anchor_regeneration",
                        routing_operation="generation",
                    )
                    regenerated_anchor = _new_distinct_path(
                        generate_sheet(anchor_request), role, paths,
                    )
                    regenerated: list[ReferencePackArtifact] = [
                        track_artifact(
                            regenerated_anchor,
                            role=role,
                            index=0,
                            model=anchor_request.model,
                            strategy="canonical_anchor_regeneration",
                            anchor_role=role,
                            reason_codes=review.reason_codes,
                        )
                    ]
                    anchor_path = regenerated_anchor
                    anchor_fingerprint = _fingerprint(anchor_path)
                    for index, recipe in enumerate(plan.sheets[1:], start=1):
                        source_digest = anchor_fingerprint[1]
                        request = _pack_generation_request(
                            plan,
                            recipe,
                            index,
                            strategy="reference_guided_regeneration",
                            routing_operation="edit",
                            source_role=role,
                            source_digest=source_digest,
                            operation="enhance",
                        )
                        regenerated_path = _new_distinct_path(
                            edit_sheet(anchor_path, anchor_path, request),
                            recipe.role,
                            paths,
                        )
                        _assert_preserved(
                            anchor_path, anchor_fingerprint, role,
                        )
                        regenerated.append(track_artifact(
                            regenerated_path,
                            role=recipe.role,
                            index=index,
                            model=request.model,
                            strategy="reference_guided_regeneration",
                            anchor_role=role,
                            reason_codes=review.reason_codes,
                        ))
                    for callout_index, callout in enumerate(
                        plan.detail_callouts
                    ):
                        regenerated.append(build_detail_artifact(
                            callout,
                            callout_index,
                            regenerated,
                            anchor_path,
                            anchor_fingerprint,
                            provenance_strategy="detail_callout_regeneration",
                            reason_codes=review.reason_codes,
                        ))
                    for previous_artifact, fingerprint in previous:
                        _assert_preserved(
                            previous_artifact.path,
                            fingerprint,
                            previous_artifact.role,
                        )
                    artifacts = regenerated
                else:
                    index = plan.output_roles.index(role)
                    current = artifacts[index]
                    preserved = tuple(
                        (artifact, _fingerprint(artifact.path))
                        for artifact in artifacts
                    )
                    if index < len(plan.sheets):
                        recipe = plan.sheets[index]
                        strategy = "reference_guided_repair"
                    else:
                        callout = plan.detail_callouts[
                            index - len(plan.sheets)
                        ]
                        recipe = PackSheetRecipe(
                            role=callout.target_role,
                            label=callout.label,
                            objective=f"authored detail target: {callout.label}",
                        )
                        strategy = "detail_callout_repair"
                    repair_request = _pack_repair_request(
                        plan, recipe, review.reason_codes,
                    )
                    repaired = _new_distinct_path(
                        repair_sheet(
                            current.path,
                            anchor_path,
                            repair_request,
                        ),
                        role,
                        paths,
                    )
                    for preserved_artifact, fingerprint in preserved:
                        _assert_preserved(
                            preserved_artifact.path,
                            fingerprint,
                            preserved_artifact.role,
                        )
                    artifacts[index] = track_artifact(
                        repaired,
                        role=role,
                        index=index,
                        model=repair_request.model,
                        strategy=strategy,
                        anchor_role=plan.sheet_roles[0],
                        reason_codes=review.reason_codes,
                        detail_provenance=current.detail_provenance,
                    )
                repaired_roles.append(role)
                review = review_reference_pack(plan, artifacts, reviewer)
        return ReferencePackResult(
            plan=plan,
            artifacts=tuple(artifacts),
            review=review,
            repaired_roles=tuple(repaired_roles),
            max_repair_attempts=max_repair_attempts,
            repair_attempts_used=len(repaired_roles),
            private_source_paths=tuple(paths),
        )
    except Exception:
        # Only paths returned by injected generators are known-owned here. The
        # route also performs an idempotent cleanup after successful copying.
        for path in reversed(paths):
            for candidate in (path, path.with_suffix(".meta.json")):
                try:
                    identity = owned_identities.get(candidate)
                    current = candidate.lstat()
                    if (
                        identity is not None
                        and stat.S_ISREG(current.st_mode)
                        and (current.st_dev, current.st_ino) == identity
                    ):
                        candidate.unlink()
                except OSError:
                    pass
        raise


__all__ = [
    "ASSET_TYPES",
    "MAX_PACK_SHEETS",
    "MODES",
    "PACK_DEFAULT_PRESETS",
    "PACK_DEPTHS",
    "PACK_DETAIL_KINDS",
    "PACK_DETAIL_OPERATIONS",
    "PACK_INTENTS",
    "PACK_PLANNER_VERSION",
    "PACK_REFERENCE_TYPES",
    "PACK_REFERENCE_TYPE_ALIASES",
    "PACK_ROLE_RECIPES",
    "PACK_SCHEMA_VERSION",
    "PACK_TYPE_FIELDS",
    "PACK_TYPE_PRESETS",
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
    "PackDetailCallout",
    "PackIntelligenceSelection",
    "PackLoraSelection",
    "PackModelSchedule",
    "PackSheetGenerationRequest",
    "PackSheetRecipe",
    "PackSheetRepairRequest",
    "ReferencePackArtifact",
    "ReferencePackPlan",
    "ReferencePackResult",
    "ReferencePackReviewRequest",
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
    "build_reference_pack_plan",
    "build_reference_pack_review_request",
    "build_semantic_review_request",
    "compose_reference_sheet",
    "create_reference_sheet",
    "create_reference_pack",
    "normalize_reference_pack_type",
    "parse_semantic_review_result",
    "review_reference_sheet",
    "review_reference_pack",
    "validate_panel_files",
]
