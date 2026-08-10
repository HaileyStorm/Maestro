"""Server-owned image recipe license/self-review gates.

This module resolves terms from declared model relationships only. It never
inspects prompts, input media, outputs, paths, tokens, or user identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.host_terms import (
    BFL_FLUX1_REVIEW_TERM,
    BFL_FLUX2_REVIEW_TERM,
    CIVITAI_PORNMASTER_V4_CREATOR_TERM,
    CURRENT_HOST_TERM_BINDINGS,
    CURRENT_HOST_TERM_VERSIONS,
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
    KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH,
    KREA2_MOODY_CUTIE_V4_RECIPE_ID,
    KREA2_MOODY_MIX_V7_CREATOR_TERM,
    KREA2_MOODY_MIX_V7_RECIPE_GRAPH,
    KREA2_MOODY_MIX_V7_RECIPE_ID,
    KREA2_MOODY_RECIPE_GRAPHS,
    KREA2_REVIEW_TERM,
    PONPOKE_FLUX2_KLEIN4B_TERM,
    PONPOKE_FLUX2_KLEIN9B_TERM,
    PORNMASTER_V4_RECIPE_GRAPH,
    PORNMASTER_V4_RECIPE_ID,
    host_term_accepted,
)


PORNMASTER_V4_PONPOKE_RECIPE = PORNMASTER_V4_RECIPE_ID
PORNMASTER_V4_REQUIRED_TERMS = tuple(
    PORNMASTER_V4_RECIPE_GRAPH["required_host_terms"]
)
REGISTERED_MANUAL_RECIPE_IDS = frozenset({
    PORNMASTER_V4_PONPOKE_RECIPE,
    *KREA2_MOODY_RECIPE_GRAPHS,
})


MODEL_TERM_DOCUMENTS: dict[str, dict[str, Any]] = {
    BFL_FLUX1_REVIEW_TERM: {
        "title": "FLUX.1 dev non-commercial license and self-review",
        "license_url": (
            "https://huggingface.co/black-forest-labs/FLUX.1-dev/"
            "blob/3de623f/LICENSE.md"
        ),
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm the FLUX.1 dev non-commercial terms apply to your use "
            "and remain responsible for license compliance and lawful use. "
            "Optional local fidelity QA may evaluate visual quality, "
            "artifacts, and consistency only; it is not moderation, does "
            "not decide permissibility, and never accepts terms."
        ),
    },
    BFL_FLUX2_REVIEW_TERM: {
        "title": "FLUX non-commercial license and self-review",
        "license_url": (
            "https://huggingface.co/"
            f"{PORNMASTER_V4_RECIPE_GRAPH['base']['license_repository']}/"
            f"blob/{PORNMASTER_V4_RECIPE_GRAPH['base']['license_revision']}/"
            "LICENSE.md"
        ),
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm the FLUX non-commercial terms apply to your use and "
            "remain responsible for license compliance and lawful use. "
            "Optional local fidelity QA may evaluate visual quality, "
            "artifacts, and consistency only; it is not moderation, does "
            "not decide permissibility, and never accepts terms."
        ),
    },
    KREA2_REVIEW_TERM: {
        "title": (
            "Krea 2 Community License, Acceptable Use Policy, and human review"
        ),
        "license_url": (
            "https://huggingface.co/krea/Krea-2-Turbo/"
            "blob/98e0fe1/README.md"
        ),
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm the Krea 2 Community License and Acceptable Use Policy "
            "apply, and complete the required human review. Legitimate "
            "intended or potential broad-capability research, evaluation, "
            "and fine-tune development are not automatically circumvention; "
            "artifacts explicitly designed to defeat safety filters remain "
            "excluded from Maestro's curated routing. Optional local fidelity "
            "QA evaluates quality only; it is not moderation and does not "
            "decide permissibility."
        ),
    },
    PONPOKE_FLUX2_KLEIN4B_TERM: {
        "title": "Ponpoke FLUX.2 Klein 4B encoder terms and self-review",
        "license_url": (
            "https://huggingface.co/ponpoke/"
            "flux2-klein-4b-uncensored-text-encoder/blob/"
            "633217e588e4c0bc76619052e05d3ce0e057cd83/README.md"
        ),
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm the separately gated Ponpoke encoder access conditions "
            "and FLUX non-commercial v2.1 terms apply, and remain responsible "
            "for compliance and lawful use. Optional local fidelity QA "
            "evaluates quality only; it is not moderation, does not decide "
            "permissibility, and never accepts terms."
        ),
    },
    PONPOKE_FLUX2_KLEIN9B_TERM: {
        "title": "Ponpoke FLUX.2 Klein 9B encoder terms and self-review",
        "license_url": (
            "https://huggingface.co/"
            f"{PORNMASTER_V4_RECIPE_GRAPH['encoder']['repository']}/blob/"
            f"{PORNMASTER_V4_RECIPE_GRAPH['encoder']['revision']}/README.md"
        ),
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm the separately gated Ponpoke encoder access conditions "
            "and FLUX non-commercial v2.1 terms apply, and remain responsible "
            "for compliance and lawful use. Optional local fidelity QA "
            "evaluates quality only; it is not moderation, does not decide "
            "permissibility, and never accepts terms."
        ),
    },
    CIVITAI_PORNMASTER_V4_CREATOR_TERM: {
        "title": "PornMaster V4 Turbo FP8 creator terms and self-review",
        "license_url": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["source_url"],
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm iamddtla's exact PornMaster V4 creator terms apply: "
            "credit is required, derivatives are allowed, and the published "
            "commercial scope is RentCivit only. The underlying FLUX base "
            "remains non-commercial. You remain responsible for compliance "
            "and lawful use. Optional local fidelity QA evaluates quality "
            "only; it is not moderation, does not decide permissibility, and "
            "never accepts terms."
        ),
    },
    KREA2_MOODY_MIX_V7_CREATOR_TERM: {
        "title": "Moody Krea 2 Mix V7 creator terms and self-review",
        "license_url": KREA2_MOODY_MIX_V7_RECIPE_GRAPH["checkpoint"][
            "source_url"
        ],
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm catlover1937's exact Moody Krea 2 Mix V7 creator "
            "terms apply: credit is required, derivatives are forbidden, "
            "and commercial use is limited to RentCivit. The Krea 2 "
            "Community License and Acceptable Use Policy also apply. "
            "Evaluation may inform separate Krea-base work, but it does not "
            "permit Moody derivatives or derivative tooling. You remain "
            "responsible for compliance and lawful use; optional local "
            "fidelity QA evaluates quality only, is not moderation, does not "
            "decide permissibility, and never accepts terms."
        ),
    },
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM: {
        "title": "Moody Cutie V4 creator terms and self-review",
        "license_url": KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH["checkpoint"][
            "source_url"
        ],
        "review_mode": "manual_self_review",
        "notice": (
            "Confirm catlover1937's exact Moody Cutie V4 creator terms "
            "apply: credit is required, derivatives are forbidden, and "
            "commercial use is limited to RentCivit. The Krea 2 Community "
            "License and Acceptable Use Policy also apply. Evaluation may "
            "inform separate Krea-base work, but it does not permit Moody "
            "derivatives or derivative tooling. You remain responsible for "
            "compliance and lawful use; optional local fidelity QA evaluates "
            "quality only, is not moderation, does not decide permissibility, "
            "and never accepts terms."
        ),
    },
}

# Explicit recipe roots plus architecture roots whose exact upstream license
# covers their derivatives. Stock FLUX.2 Klein 4B is intentionally absent
# because its exact upstream weights are Apache-2.0. Its separately gated
# Ponpoke encoder derivative is an explicit root. The mature Qwen OpenRAIL++
# LoRA is also absent: its exact terms impose no separate acknowledgement gate.
_RECIPE_TERM_ROOTS = {
    "flux_krea": BFL_FLUX1_REVIEW_TERM,
    "flux_dev_kontext": BFL_FLUX1_REVIEW_TERM,
    "flux_dev_kontext_dreamomni2": BFL_FLUX1_REVIEW_TERM,
    "flux2_dev": BFL_FLUX2_REVIEW_TERM,
    "flux2_dev_nvfp4": BFL_FLUX2_REVIEW_TERM,
    "flux2_klein_9b": BFL_FLUX2_REVIEW_TERM,
    "flux2_klein_base_9b": BFL_FLUX2_REVIEW_TERM,
    "flux2_klein_4b_uncensored": PONPOKE_FLUX2_KLEIN4B_TERM,
    "flux2_klein_9b_uncensored": (
        BFL_FLUX2_REVIEW_TERM,
        PONPOKE_FLUX2_KLEIN9B_TERM,
    ),
    PORNMASTER_V4_PONPOKE_RECIPE: PORNMASTER_V4_REQUIRED_TERMS,
    KREA2_MOODY_MIX_V7_RECIPE_ID: tuple(
        KREA2_MOODY_MIX_V7_RECIPE_GRAPH["required_host_terms"]
    ),
    KREA2_MOODY_CUTIE_V4_RECIPE_ID: tuple(
        KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH["required_host_terms"]
    ),
    "krea2_raw": KREA2_REVIEW_TERM,
    "krea2_turbo": KREA2_REVIEW_TERM,
}

_RELATION_KEYS = (
    "URLs",
    "URLs2",
    "modules",
    "loras",
    "preload_URLs",
    "text_encoder_URLs",
)


def _declared_recipe_terms(model_def: Mapping[str, Any]) -> tuple[str, ...]:
    """Read only recognized, server-authored recipe term identifiers."""
    raw = model_def.get("required_host_terms")
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return tuple(
        value for value in values
        if isinstance(value, str) and value in MODEL_TERM_DOCUMENTS
    )


def _pornmaster_v4_manifest_matches(model_def: Mapping[str, Any]) -> bool:
    """Cross-check one immutable creator/base/encoder acceptance graph."""
    creator_binding = CURRENT_HOST_TERM_BINDINGS.get(
        CIVITAI_PORNMASTER_V4_CREATOR_TERM,
    )
    base_binding = CURRENT_HOST_TERM_BINDINGS.get(BFL_FLUX2_REVIEW_TERM)
    encoder_binding = CURRENT_HOST_TERM_BINDINGS.get(
        PONPOKE_FLUX2_KLEIN9B_TERM,
    )
    graph = (
        creator_binding.get("recipe_graph")
        if isinstance(creator_binding, Mapping) else None
    )
    if not isinstance(graph, Mapping):
        return False
    checkpoint_graph = graph.get("checkpoint")
    base_graph = graph.get("base")
    encoder_graph = graph.get("encoder")
    capability_graph = graph.get("capability")
    availability_graph = graph.get("availability")
    if not all(isinstance(value, Mapping) for value in (
        checkpoint_graph,
        base_graph,
        encoder_graph,
        capability_graph,
        availability_graph,
    )):
        return False
    if graph != PORNMASTER_V4_RECIPE_GRAPH:
        return False

    expected_creator_binding = {
        "license_id": checkpoint_graph.get("license_id"),
        "repository": checkpoint_graph.get("repository"),
        "revision": checkpoint_graph.get("revision"),
        "source_url": checkpoint_graph.get("source_url"),
        "creator": checkpoint_graph.get("creator"),
        "model_id": checkpoint_graph.get("model_id"),
        "model_version_id": checkpoint_graph.get("version_id"),
        "filename": checkpoint_graph.get("filename"),
        "file_size_bytes": checkpoint_graph.get("size_bytes"),
        "file_sha256": checkpoint_graph.get("sha256"),
        "creator_restrictions": {
            key: value
            for key, value in (
                checkpoint_graph.get("creator_terms") or {}
            ).items()
            if key != "underlying_base_license"
        },
        "underlying_base_license": (
            checkpoint_graph.get("creator_terms") or {}
        ).get("underlying_base_license"),
    }
    covered_repositories = (
        base_binding.get("covered_repositories")
        if isinstance(base_binding, Mapping) else None
    )
    if not isinstance(covered_repositories, (list, tuple)):
        return False
    if (
        not isinstance(creator_binding, Mapping)
        or any(
            creator_binding.get(key) != value
            for key, value in expected_creator_binding.items()
        )
        or not isinstance(base_binding, Mapping)
        or base_graph.get("term") != BFL_FLUX2_REVIEW_TERM
        or base_binding.get("license_id") != base_graph.get("license_id")
        or base_binding.get("repository")
        != base_graph.get("license_repository")
        or base_binding.get("revision") != base_graph.get("license_revision")
        or [
            item
            for item in covered_repositories
            if isinstance(item, Mapping)
            and item.get("repository") == base_graph.get("repository")
        ] != [{
            "repository": base_graph.get("repository"),
            "revision": base_graph.get("revision"),
        }]
        or not isinstance(encoder_binding, Mapping)
        or encoder_graph.get("term") != PONPOKE_FLUX2_KLEIN9B_TERM
        or encoder_binding.get("license_id") != encoder_graph.get("license_id")
        or encoder_binding.get("repository") != encoder_graph.get("repository")
        or encoder_binding.get("revision") != encoder_graph.get("revision")
    ):
        return False

    raw_terms = model_def.get("required_host_terms")
    if not isinstance(raw_terms, (list, tuple)):
        return False
    if tuple(raw_terms) != PORNMASTER_V4_REQUIRED_TERMS:
        return False
    if graph.get("required_host_term_versions") != {
        term: CURRENT_HOST_TERM_VERSIONS.get(term)
        for term in PORNMASTER_V4_REQUIRED_TERMS
    }:
        return False

    executable = {
        "architecture": graph.get("architecture"),
        "URLs": [checkpoint_graph.get("filename")],
        "text_encoder_URLs": [encoder_graph.get("url")],
        "text_encoder_folder": encoder_graph.get("folder"),
        "text_encoder_tokenizer_folder": encoder_graph.get("tokenizer_folder"),
        "text_encoder_quantization": encoder_graph.get("quantization"),
        **availability_graph,
    }
    if any(model_def.get(key) != value for key, value in executable.items()):
        return False
    capability = model_def.get("capability_recipe")
    if not isinstance(capability, Mapping) or (
        capability.get("base_model") != base_graph.get("model_type")
        or any(
            capability.get(key) != value
            for key, value in capability_graph.items()
        )
    ):
        return False

    provenance = model_def.get("artifact_provenance")
    checkpoint = (
        provenance.get("checkpoint")
        if isinstance(provenance, Mapping) else None
    )
    if not isinstance(checkpoint, Mapping):
        return False
    checkpoint_manifest = {
        key: value
        for key, value in checkpoint_graph.items()
        if key not in {"license_id", "repository", "revision"}
    }
    if any(
        checkpoint.get(key) != value
        for key, value in checkpoint_manifest.items()
    ):
        return False

    encoder = provenance.get("text_encoder")
    if not isinstance(encoder, Mapping):
        return False
    encoder_provenance = {
        "provider": encoder_graph.get("provider"),
        "artifact_kind": encoder_graph.get("artifact_kind"),
        "repo_id": encoder_graph.get("repository"),
        "revision": encoder_graph.get("revision"),
        "path": encoder_graph.get("path"),
        "size_bytes": encoder_graph.get("size_bytes"),
        "access": encoder_graph.get("access"),
        "license": encoder_graph.get("license"),
    }
    return all(
        encoder.get(key) == value
        for key, value in encoder_provenance.items()
    )


def _moody_krea2_manifest_matches(
    model_type: str,
    model_def: Mapping[str, Any],
) -> bool:
    """Cross-check one exact Moody checkpoint, creator, and Krea base graph."""
    expected_graph = KREA2_MOODY_RECIPE_GRAPHS.get(model_type)
    if not isinstance(expected_graph, Mapping):
        return False
    required_terms = tuple(expected_graph.get("required_host_terms") or ())
    if len(required_terms) != 2:
        return False
    creator_term = required_terms[0]
    creator_binding = CURRENT_HOST_TERM_BINDINGS.get(creator_term)
    base_binding = CURRENT_HOST_TERM_BINDINGS.get(KREA2_REVIEW_TERM)
    graph = (
        creator_binding.get("recipe_graph")
        if isinstance(creator_binding, Mapping) else None
    )
    if graph != expected_graph:
        return False
    checkpoint_graph = graph.get("checkpoint")
    base_graph = graph.get("base")
    capability_graph = graph.get("capability")
    availability_graph = graph.get("availability")
    if not all(isinstance(value, Mapping) for value in (
        checkpoint_graph, base_graph, capability_graph, availability_graph,
    )):
        return False
    expected_creator_binding = {
        "license_id": checkpoint_graph.get("license_id"),
        "repository": checkpoint_graph.get("repository"),
        "revision": checkpoint_graph.get("revision"),
        "source_url": checkpoint_graph.get("source_url"),
        "creator": checkpoint_graph.get("creator"),
        "model_id": checkpoint_graph.get("model_id"),
        "model_version_id": checkpoint_graph.get("version_id"),
        "file_id": checkpoint_graph.get("file_id"),
        "filename": checkpoint_graph.get("filename"),
        "file_size_bytes": checkpoint_graph.get("size_bytes"),
        "file_sha256": checkpoint_graph.get("sha256"),
        "creator_restrictions": {
            key: value
            for key, value in (
                checkpoint_graph.get("creator_terms") or {}
            ).items()
            if key != "underlying_base_license"
        },
        "underlying_base_license": (
            checkpoint_graph.get("creator_terms") or {}
        ).get("underlying_base_license"),
    }
    if (
        not isinstance(creator_binding, Mapping)
        or any(
            creator_binding.get(key) != value
            for key, value in expected_creator_binding.items()
        )
        or not isinstance(base_binding, Mapping)
        or base_graph.get("term") != KREA2_REVIEW_TERM
        or any(
            base_binding.get(key) != base_graph.get(key)
            for key in (
                "license_id", "repository", "revision",
                "license_repository", "license_revision",
            )
        )
    ):
        return False
    raw_terms = model_def.get("required_host_terms")
    if not isinstance(raw_terms, (list, tuple)) or tuple(raw_terms) != required_terms:
        return False
    if graph.get("required_host_term_versions") != {
        term: CURRENT_HOST_TERM_VERSIONS.get(term) for term in required_terms
    }:
        return False
    executable = {
        "architecture": graph.get("architecture"),
        "URLs": [checkpoint_graph.get("filename")],
        **availability_graph,
    }
    if any(model_def.get(key) != value for key, value in executable.items()):
        return False
    capability = model_def.get("capability_recipe")
    if not isinstance(capability, Mapping) or (
        capability.get("base_model") != base_graph.get("model_type")
        or any(
            capability.get(key) != value
            for key, value in capability_graph.items()
        )
    ):
        return False
    provenance = model_def.get("artifact_provenance")
    checkpoint = (
        provenance.get("checkpoint") if isinstance(provenance, Mapping) else None
    )
    if not isinstance(checkpoint, Mapping):
        return False
    checkpoint_manifest = {
        key: value
        for key, value in checkpoint_graph.items()
        if key not in {"license_id", "repository", "revision"}
    }
    if any(
        checkpoint.get(key) != value
        for key, value in checkpoint_manifest.items()
    ):
        return False
    base = provenance.get("base")
    expected_base = {
        "provider": "huggingface",
        "artifact_kind": "preserved_krea2_base_contract",
        **{
            key: base_graph.get(key)
            for key in (
                "model_type", "license_id", "repository", "revision",
                "license_repository", "license_revision",
            )
        },
    }
    return isinstance(base, Mapping) and all(
        base.get(key) == value for key, value in expected_base.items()
    )


def model_terms_manifest_valid(
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    """Validate every creator-bound node reachable from one recipe."""
    definitions = model_defs if isinstance(model_defs, Mapping) else {}
    pending = [str(model_type or "")]
    seen: set[str] = set()
    while pending:
        candidate = pending.pop(0)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        model_def = definitions.get(candidate)
        if candidate == PORNMASTER_V4_PONPOKE_RECIPE and (
            not isinstance(model_def, Mapping)
            or not _pornmaster_v4_manifest_matches(model_def)
        ):
            return False
        if candidate in KREA2_MOODY_RECIPE_GRAPHS and (
            not isinstance(model_def, Mapping)
            or not _moody_krea2_manifest_matches(candidate, model_def)
        ):
            return False
        if isinstance(model_def, Mapping):
            pending[0:0] = _declared_model_relations(model_def, definitions)
    return True


class ModelTermsRequiredError(PermissionError):
    """Safe, content-free refusal raised before download or model use."""

    stage = "model_terms"
    code = "model_terms_required"

    def __init__(self, model_type: str, term: str):
        document = MODEL_TERM_DOCUMENTS[term]
        super().__init__(
            f"Review and accept {document['title']} for this host before "
            f"downloading or using model recipe '{model_type}'."
        )
        self.model_type = model_type
        self.term = term


class ModelTermsContractError(PermissionError):
    """Raised when exact creator metadata cannot support an acceptance."""

    stage = "model_terms"
    code = "model_terms_contract_invalid"

    def __init__(self, model_type: str):
        super().__init__(
            "This manual model recipe is unavailable because its exact "
            "creator terms or frozen source metadata are incomplete."
        )
        self.model_type = model_type


def _declared_model_relations(
    model_def: Mapping[str, Any],
    model_defs: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    related: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            # Registered recipe IDs remain meaningful relations even if a
            # malformed/incomplete registry omitted their target definition.
            # This exact allowlist is server authority; arbitrary provider
            # URLs and owner-imported IDs are never classified by shape.
            if (
                value in model_defs or value in _RECIPE_TERM_ROOTS
            ) and value not in related:
                related.append(value)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    for key in _RELATION_KEYS:
        collect(model_def.get(key))
    recipe = model_def.get("capability_recipe")
    if isinstance(recipe, Mapping):
        collect(recipe.get("base_model"))
    return tuple(related)


def required_model_terms(
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[str, ...]:
    """Resolve all inherited/composite terms without inspecting content."""
    definitions = model_defs if isinstance(model_defs, Mapping) else {}
    pending = [str(model_type or "")]
    seen: set[str] = set()
    terms: list[str] = []
    while pending:
        candidate = pending.pop(0)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        direct = _RECIPE_TERM_ROOTS.get(candidate)
        direct_terms = direct if isinstance(direct, tuple) else (direct,)
        for direct_term in direct_terms:
            if direct_term is not None and direct_term not in terms:
                terms.append(direct_term)
        model_def = definitions.get(candidate)
        if not isinstance(model_def, Mapping):
            continue
        for declared in _declared_recipe_terms(model_def):
            if declared not in terms:
                terms.append(declared)
        # Walk each declared branch to completion before the next sibling so
        # composite notices retain the recipe's server-authored relation
        # order, including aliases nested behind encoder fields.
        pending[0:0] = _declared_model_relations(model_def, definitions)
    return tuple(terms)


def required_model_term(
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> str | None:
    """Compatibility projection for recipes currently carrying one term."""
    terms = required_model_terms(model_type, model_defs)
    return terms[0] if terms else None


def model_availability_policy(
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Resolve declared installation policy through server recipe relations.

    Policy is never inferred from an architecture, filename, provider ID,
    tag, or description. A reachable explicit ``downloadable: false`` is
    cumulative and cannot be weakened by an alias. Arbitrary owner-imported
    models with direct file URLs therefore retain the generic local workflow.
    """
    definitions = model_defs if isinstance(model_defs, Mapping) else {}
    pending = [str(model_type or "")]
    seen: set[str] = set()
    manual_nodes = 0
    manual_ready = True
    manual_status: str | None = None
    direct_status: str | None = None
    while pending:
        candidate = pending.pop(0)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        model_def = definitions.get(candidate)
        if not isinstance(model_def, Mapping):
            if candidate in REGISTERED_MANUAL_RECIPE_IDS:
                # An alias cannot weaken the registered manual-only root by
                # deleting its target definition. The manifest validator will
                # hide it; download admission independently stays fail closed.
                manual_nodes += 1
                manual_ready = False
                if manual_status is None:
                    manual_status = "manual_installation_contract_unavailable"
            continue
        status = model_def.get("availability_status")
        if candidate == model_type and isinstance(status, str) and status:
            direct_status = status
        registered_manual = candidate in REGISTERED_MANUAL_RECIPE_IDS
        if registered_manual:
            manual_nodes += 1
            contract_ready = (
                model_def.get("downloadable") is False
                and model_def.get("manual_installation_ready") is True
            )
            manual_ready = manual_ready and contract_ready
            if manual_status is None:
                manual_status = (
                    status
                    if contract_ready and isinstance(status, str) and status
                    else "manual_installation_contract_unavailable"
                )
        elif model_def.get("downloadable") is False:
            manual_nodes += 1
            manual_ready = (
                manual_ready
                and model_def.get("manual_installation_ready") is True
            )
            if manual_status is None and isinstance(status, str) and status:
                manual_status = status
        pending[0:0] = _declared_model_relations(model_def, definitions)

    downloadable = manual_nodes == 0
    return {
        "downloadable": downloadable,
        "manual_installation_ready": manual_ready if manual_nodes else False,
        "availability_status": (
            manual_status
            or direct_status
            or (
                "available"
                if downloadable else "manual_installation_required"
            )
        ),
    }


def model_terms_status(
    services: Mapping[str, Any] | None,
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    term = required_model_term(model_type, model_defs)
    if term is None:
        return None
    document = MODEL_TERM_DOCUMENTS[term]
    return {
        "term": term,
        "version": CURRENT_HOST_TERM_VERSIONS[term],
        "accepted": host_term_accepted(services, term),
        "title": document["title"],
        "license_url": document["license_url"],
        "review_mode": document["review_mode"],
        "notice": document["notice"],
    }


def model_terms_statuses(
    services: Mapping[str, Any] | None,
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    statuses = []
    for term in required_model_terms(model_type, model_defs):
        document = MODEL_TERM_DOCUMENTS[term]
        statuses.append({
            "term": term,
            "version": CURRENT_HOST_TERM_VERSIONS[term],
            "accepted": host_term_accepted(services, term),
            "title": document["title"],
            "license_url": document["license_url"],
            "review_mode": document["review_mode"],
            "notice": document["notice"],
        })
    return statuses


def require_model_terms(
    services: Mapping[str, Any] | None,
    model_type: str,
    model_defs: Mapping[str, Mapping[str, Any]] | None,
) -> None:
    definitions = model_defs if isinstance(model_defs, Mapping) else {}
    if not model_terms_manifest_valid(str(model_type or ""), definitions):
        raise ModelTermsContractError(str(model_type or ""))
    for term in required_model_terms(model_type, model_defs):
        if not host_term_accepted(services, term):
            raise ModelTermsRequiredError(str(model_type or ""), term)


__all__ = [
    "MODEL_TERM_DOCUMENTS",
    "ModelTermsContractError",
    "ModelTermsRequiredError",
    "PORNMASTER_V4_PONPOKE_RECIPE",
    "PORNMASTER_V4_REQUIRED_TERMS",
    "model_terms_manifest_valid",
    "model_availability_policy",
    "model_terms_status",
    "model_terms_statuses",
    "required_model_term",
    "required_model_terms",
    "require_model_terms",
]
