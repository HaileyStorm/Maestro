"""Versioned, host-wide acknowledgement records.

These records describe only which published notice/version and immutable
public license binding were accepted, and when. They never carry an accepting
identity, project, prompt, or media data.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from typing import Any


LAWFUL_USE_TERM = "lawful_use"
REF2VA_TERM = "minimax_h3_ref2va"
BFL_FLUX1_REVIEW_TERM = "bfl_flux1_self_review"
BFL_FLUX2_REVIEW_TERM = "bfl_flux2_self_review"
KREA2_REVIEW_TERM = "krea2_self_review"
KREA2_MOODY_MIX_V7_CREATOR_TERM = (
    "civitai_2731187_3209007_creator_terms"
)
KREA2_MOODY_CUTIE_V4_CREATOR_TERM = (
    "civitai_2764429_3211049_creator_terms"
)
PONPOKE_FLUX2_KLEIN4B_TERM = "ponpoke_flux2_klein_4b_self_review"
PONPOKE_FLUX2_KLEIN9B_TERM = "ponpoke_flux2_klein_9b_self_review"
CIVITAI_PORNMASTER_V4_CREATOR_TERM = (
    "civitai_2382648_2973304_creator_terms"
)
PORNMASTER_V4_RECIPE_ID = (
    "flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke"
)
PORNMASTER_V4_RECIPE_GRAPH = {
    "model_type": PORNMASTER_V4_RECIPE_ID,
    "architecture": "flux2_klein_9b",
    "required_host_terms": [
        CIVITAI_PORNMASTER_V4_CREATOR_TERM,
        BFL_FLUX2_REVIEW_TERM,
        PONPOKE_FLUX2_KLEIN9B_TERM,
    ],
    "required_host_term_versions": {
        CIVITAI_PORNMASTER_V4_CREATOR_TERM: 1,
        BFL_FLUX2_REVIEW_TERM: 1,
        PONPOKE_FLUX2_KLEIN9B_TERM: 1,
    },
    "checkpoint": {
        "license_id": "civitai-creator-terms-2382648-2973304",
        "repository": "civitai/models/2382648",
        "revision": "2973304",
        "provider": "civitai",
        "artifact_kind": "exact_family_tune",
        "creator": "iamddtla",
        "model_id": 2382648,
        "version_id": 2973304,
        "filename": "pornmasterFlux2Klein_v4TurboFp8.safetensors",
        "size_bytes": 9433104872,
        "sha256": (
            "E90EEB50140A10806341B7521C340214C6F76CEC2F8F8DAE7A443C5806072DF7"
        ),
        "source_url": (
            "https://civitai.com/models/2382648?modelVersionId=2973304"
        ),
        "download_url": "https://civitai.com/api/download/models/2973304",
        "download_policy": "manual_hash_verified_only",
        "loader_auto_download": False,
        "precision": "fp8",
        "size_kb": 9212016.476,
        "exact_family": "FLUX.2 Klein 9B",
        "operations": ["generation", "editing"],
        "creator_terms": {
            "allowNoCredit": False,
            "allowDerivatives": True,
            "allowCommercialUse": ["RentCivit"],
            "underlying_base_license": "FLUX non-commercial",
        },
    },
    "base": {
        "model_type": "flux2_klein_9b",
        "term": BFL_FLUX2_REVIEW_TERM,
        "license_id": "flux-non-commercial-license-v2.1",
        "license_repository": "black-forest-labs/FLUX.2-dev",
        "license_revision": "0cb56aa",
        "repository": "black-forest-labs/FLUX.2-klein-9B",
        "revision": "07c5ac6",
    },
    "encoder": {
        "term": PONPOKE_FLUX2_KLEIN9B_TERM,
        "license_id": "flux-non-commercial-v2.1",
        "provider": "huggingface",
        "artifact_kind": "conditioning_encoder",
        "repository": "ponpoke/flux2-klein-9b-uncensored-text-encoder",
        "revision": "fba36e796aac081246708dd30392a401ba44922e",
        "path": "model.safetensors",
        "size_bytes": 16381516808,
        "access": "gated-auto",
        "license": (
            "FLUX non-commercial v2.1 plus repository access conditions"
        ),
        "url": (
            "https://huggingface.co/ponpoke/"
            "flux2-klein-9b-uncensored-text-encoder/resolve/"
            "fba36e796aac081246708dd30392a401ba44922e/model.safetensors"
        ),
        "folder": "flux2_klein_9b_uncensored_text_encoder",
        "tokenizer_folder": "qwen3_8b",
        "quantization": "bf16",
    },
    "capability": {
        "kind": "layered_checkpoint_and_conditioning_encoder",
        "operations": ["generation", "editing"],
        "changed_components": ["transformer", "text_encoder"],
        "preserved_components": ["vae", "tokenizer_config"],
        "quality_status": "experimental_requires_benchmark",
    },
    "availability": {
        "selection_policy": "manual_only",
        "automatic_routing": False,
        "verified": False,
        "default_for_operations": [],
        "downloadable": False,
        "manual_installation_ready": True,
        "availability_status": "experimental_manual_installation",
    },
}


def _moody_krea2_recipe_graph(
    *,
    model_type: str,
    creator_term: str,
    display_name: str,
    model_id: int,
    version_id: int,
    file_id: int,
    filename: str,
    sha256: str,
) -> dict[str, Any]:
    return {
        "model_type": model_type,
        "architecture": "krea2_raw",
        "required_host_terms": [creator_term, KREA2_REVIEW_TERM],
        "required_host_term_versions": {
            creator_term: 1,
            KREA2_REVIEW_TERM: 2,
        },
        "checkpoint": {
            "license_id": f"civitai-creator-terms-{model_id}-{version_id}",
            "repository": f"civitai/models/{model_id}",
            "revision": str(version_id),
            "provider": "civitai",
            "artifact_kind": "exact_family_tune",
            "title": display_name,
            "creator": "catlover1937",
            "model_id": model_id,
            "version_id": version_id,
            "file_id": file_id,
            "filename": filename,
            "size_bytes": 14125457032,
            "sha256": sha256,
            "source_url": (
                f"https://civitai.com/models/{model_id}"
                f"?modelVersionId={version_id}"
            ),
            "download_url": (
                f"https://civitai.com/api/download/models/{version_id}"
                "?type=Diffusion%20Model&format=SafeTensor&fp=fp8"
            ),
            "download_policy": "manual_hash_verified_only",
            "loader_auto_download": False,
            "precision": "fp8",
            "exact_family": "Krea 2 RAW",
            "operations": ["generation"],
            "creator_terms": {
                "allowNoCredit": False,
                "allowDerivatives": False,
                "allowCommercialUse": ["RentCivit"],
                "underlying_base_license": (
                    "Krea 2 Community License and Acceptable Use Policy"
                ),
            },
        },
        "base": {
            "model_type": "krea2_raw",
            "term": KREA2_REVIEW_TERM,
            "license_id": "krea-2-community-license-v1",
            "repository": "krea/Krea-2-Turbo",
            "revision": "98e0fe1",
            "license_repository": "krea-ai/krea-2",
            "license_revision": (
                "db3984fbc6e13b34c0064990fc2d95ac64d00058"
            ),
        },
        "capability": {
            "kind": "exact_family_checkpoint",
            "operations": ["generation"],
            "changed_components": ["transformer"],
            "preserved_components": [
                "vae", "text_encoder", "tokenizer_config",
            ],
            "quality_status": "experimental_requires_benchmark",
        },
        "availability": {
            "selection_policy": "manual_only",
            "automatic_routing": False,
            "verified": False,
            "default_for_operations": [],
            "downloadable": False,
            "manual_installation_ready": True,
            "availability_status": "experimental_manual_installation",
            "revenue_eligible": False,
            "fine_tuning_eligible": False,
            "derivative_tooling": False,
        },
        "display_name": display_name,
    }


KREA2_MOODY_MIX_V7_RECIPE_ID = "krea2_moody_mix_v7_fp8"
KREA2_MOODY_CUTIE_V4_RECIPE_ID = "krea2_moody_cutie_v4_fp8"
KREA2_MOODY_MIX_V7_RECIPE_GRAPH = _moody_krea2_recipe_graph(
    model_type=KREA2_MOODY_MIX_V7_RECIPE_ID,
    creator_term=KREA2_MOODY_MIX_V7_CREATOR_TERM,
    display_name="Moody Krea 2 Mix V7",
    model_id=2731187,
    version_id=3209007,
    file_id=3090691,
    filename="moodyKrea2Mix_v70.safetensors",
    sha256=(
        "405DB6A1D060075D176C3578063B6FA2FEB07B58BB61DDB403DDBA0669A35A6D"
    ),
)
KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH = _moody_krea2_recipe_graph(
    model_type=KREA2_MOODY_CUTIE_V4_RECIPE_ID,
    creator_term=KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
    display_name="Moody Cutie V4",
    model_id=2764429,
    version_id=3211049,
    file_id=3092831,
    filename="moodyCutieMixKrea2_v40.safetensors",
    sha256=(
        "6C54D783AAAAB1A6924FAFCFA3AFA9F36ABE72A59723D424E932484A8C98316A"
    ),
)
KREA2_MOODY_RECIPE_GRAPHS = {
    KREA2_MOODY_MIX_V7_RECIPE_ID: KREA2_MOODY_MIX_V7_RECIPE_GRAPH,
    KREA2_MOODY_CUTIE_V4_RECIPE_ID: KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH,
}
HOST_TERMS_CONFIG_KEY = "host_terms_acceptance"
CURRENT_HOST_TERM_VERSIONS = {
    LAWFUL_USE_TERM: 1,
    REF2VA_TERM: 1,
    BFL_FLUX1_REVIEW_TERM: 1,
    BFL_FLUX2_REVIEW_TERM: 1,
    KREA2_REVIEW_TERM: 2,
    KREA2_MOODY_MIX_V7_CREATOR_TERM: 1,
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM: 1,
    PONPOKE_FLUX2_KLEIN4B_TERM: 1,
    PONPOKE_FLUX2_KLEIN9B_TERM: 1,
    CIVITAI_PORNMASTER_V4_CREATOR_TERM: 1,
}

# Immutable, server-owned document identity.  Required image-recipe
# acknowledgements are accepted only for this exact license/repository/
# revision tuple.  Changing any tuple must also advance its notice version.
CURRENT_HOST_TERM_BINDINGS = {
    BFL_FLUX1_REVIEW_TERM: {
        "license_id": "flux-1-dev-non-commercial-license-v1.1.1",
        "repository": "black-forest-labs/FLUX.1-dev",
        "revision": "3de623f",
    },
    BFL_FLUX2_REVIEW_TERM: {
        "license_id": "flux-non-commercial-license-v2.1",
        "repository": "black-forest-labs/FLUX.2-dev",
        "revision": "0cb56aa",
        "covered_repositories": [
            {
                "repository": "black-forest-labs/FLUX.2-dev",
                "revision": "0cb56aa",
            },
            {
                "repository": PORNMASTER_V4_RECIPE_GRAPH["base"][
                    "repository"
                ],
                "revision": PORNMASTER_V4_RECIPE_GRAPH["base"]["revision"],
            },
        ],
    },
    KREA2_REVIEW_TERM: {
        "license_id": "krea-2-community-license-v1",
        "repository": "krea/Krea-2-Turbo",
        "revision": "98e0fe1",
        "license_repository": "krea-ai/krea-2",
        "license_revision": "db3984fbc6e13b34c0064990fc2d95ac64d00058",
    },
    KREA2_MOODY_MIX_V7_CREATOR_TERM: {
        "license_id": KREA2_MOODY_MIX_V7_RECIPE_GRAPH["checkpoint"][
            "license_id"
        ],
        "repository": KREA2_MOODY_MIX_V7_RECIPE_GRAPH["checkpoint"][
            "repository"
        ],
        "revision": KREA2_MOODY_MIX_V7_RECIPE_GRAPH["checkpoint"]["revision"],
        "source_url": KREA2_MOODY_MIX_V7_RECIPE_GRAPH["checkpoint"][
            "source_url"
        ],
        "creator": "catlover1937",
        "model_id": 2731187,
        "model_version_id": 3209007,
        "file_id": 3090691,
        "filename": "moodyKrea2Mix_v70.safetensors",
        "file_size_bytes": 14125457032,
        "file_sha256": (
            "405DB6A1D060075D176C3578063B6FA2FEB07B58BB61DDB403DDBA0669A35A6D"
        ),
        "creator_restrictions": {
            "allowNoCredit": False,
            "allowDerivatives": False,
            "allowCommercialUse": ["RentCivit"],
        },
        "underlying_base_license": (
            "Krea 2 Community License and Acceptable Use Policy"
        ),
        "recipe_graph": copy.deepcopy(KREA2_MOODY_MIX_V7_RECIPE_GRAPH),
    },
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM: {
        "license_id": KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH["checkpoint"][
            "license_id"
        ],
        "repository": KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH["checkpoint"][
            "repository"
        ],
        "revision": KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH["checkpoint"][
            "revision"
        ],
        "source_url": KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH["checkpoint"][
            "source_url"
        ],
        "creator": "catlover1937",
        "model_id": 2764429,
        "model_version_id": 3211049,
        "file_id": 3092831,
        "filename": "moodyCutieMixKrea2_v40.safetensors",
        "file_size_bytes": 14125457032,
        "file_sha256": (
            "6C54D783AAAAB1A6924FAFCFA3AFA9F36ABE72A59723D424E932484A8C98316A"
        ),
        "creator_restrictions": {
            "allowNoCredit": False,
            "allowDerivatives": False,
            "allowCommercialUse": ["RentCivit"],
        },
        "underlying_base_license": (
            "Krea 2 Community License and Acceptable Use Policy"
        ),
        "recipe_graph": copy.deepcopy(KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH),
    },
    PONPOKE_FLUX2_KLEIN4B_TERM: {
        "license_id": "flux-non-commercial-v2.1",
        "repository": "ponpoke/flux2-klein-4b-uncensored-text-encoder",
        "revision": "633217e588e4c0bc76619052e05d3ce0e057cd83",
    },
    PONPOKE_FLUX2_KLEIN9B_TERM: {
        "license_id": PORNMASTER_V4_RECIPE_GRAPH["encoder"]["license_id"],
        "repository": PORNMASTER_V4_RECIPE_GRAPH["encoder"]["repository"],
        "revision": PORNMASTER_V4_RECIPE_GRAPH["encoder"]["revision"],
    },
    CIVITAI_PORNMASTER_V4_CREATOR_TERM: {
        "license_id": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["license_id"],
        "repository": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["repository"],
        "revision": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["revision"],
        "source_url": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["source_url"],
        "creator": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["creator"],
        "model_id": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["model_id"],
        "model_version_id": PORNMASTER_V4_RECIPE_GRAPH[
            "checkpoint"
        ]["version_id"],
        "filename": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["filename"],
        "file_size_bytes": PORNMASTER_V4_RECIPE_GRAPH[
            "checkpoint"
        ]["size_bytes"],
        "file_sha256": PORNMASTER_V4_RECIPE_GRAPH["checkpoint"]["sha256"],
        "creator_restrictions": {
            key: copy.deepcopy(value)
            for key, value in PORNMASTER_V4_RECIPE_GRAPH[
                "checkpoint"
            ]["creator_terms"].items()
            if key != "underlying_base_license"
        },
        "underlying_base_license": PORNMASTER_V4_RECIPE_GRAPH[
            "checkpoint"
        ]["creator_terms"]["underlying_base_license"],
        "recipe_graph": copy.deepcopy(PORNMASTER_V4_RECIPE_GRAPH),
    },
}


class UnknownHostTermError(ValueError):
    """Raised when a client names a document Maestro does not publish."""


class StaleHostTermVersionError(ValueError):
    """Raised when a client did not accept the exact current version."""


def _nonempty_timestamp(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _stored_record(services: Mapping[str, Any], term: str) -> Mapping[str, Any]:
    records = services.get(HOST_TERMS_CONFIG_KEY)
    if not isinstance(records, Mapping):
        return {}
    record = records.get(term)
    return record if isinstance(record, Mapping) else {}


def _accepted_record(services: Mapping[str, Any], term: str) -> tuple[int | None, str | None]:
    record = _stored_record(services, term)
    version = record.get("version")
    accepted_at = _nonempty_timestamp(record.get("accepted_at"))
    if isinstance(version, bool) or not isinstance(version, int) or accepted_at is None:
        version = None
        accepted_at = None

    # The predecessor lawful-use notice stored only this timestamp.  It maps
    # specifically to v1, never to whatever version may be current later.
    if term == LAWFUL_USE_TERM and accepted_at is None:
        accepted_at = _nonempty_timestamp(services.get("nsfw_accepted_at"))
        if accepted_at is not None:
            version = 1
    return version, accepted_at


def _binding_matches(record: Mapping[str, Any], term: str) -> bool:
    expected = CURRENT_HOST_TERM_BINDINGS.get(term)
    if expected is None:
        return True
    stored = record.get("binding")
    return isinstance(stored, Mapping) and dict(stored) == expected


def host_terms_status(services: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    config = services if isinstance(services, Mapping) else {}
    status: dict[str, dict[str, Any]] = {}
    for term, current_version in CURRENT_HOST_TERM_VERSIONS.items():
        accepted_version, accepted_at = _accepted_record(config, term)
        binding = CURRENT_HOST_TERM_BINDINGS.get(term)
        binding_matches = _binding_matches(_stored_record(config, term), term)
        status[term] = {
            "current_version": current_version,
            "accepted_version": accepted_version,
            "accepted_at": accepted_at,
            "accepted": (
                accepted_version == current_version
                and accepted_at is not None
                and binding_matches
            ),
        }
        if binding is not None:
            status[term]["binding"] = copy.deepcopy(binding)
    return status


def host_term_accepted(services: Mapping[str, Any] | None, term: str) -> bool:
    document = host_terms_status(services).get(term)
    return bool(document and document["accepted"] is True)


def _materialize_legacy_lawful_use(services: MutableMapping[str, Any]) -> None:
    legacy_at = _nonempty_timestamp(services.get("nsfw_accepted_at"))
    records = services.setdefault(HOST_TERMS_CONFIG_KEY, {})
    if not isinstance(records, MutableMapping):
        records = {}
        services[HOST_TERMS_CONFIG_KEY] = records
    if legacy_at is not None and not isinstance(records.get(LAWFUL_USE_TERM), Mapping):
        records[LAWFUL_USE_TERM] = {"version": 1, "accepted_at": legacy_at}


def accept_host_term(
    services: MutableMapping[str, Any],
    term: str,
    version: Any,
    *,
    accepted_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    current_version = CURRENT_HOST_TERM_VERSIONS.get(term)
    if current_version is None:
        raise UnknownHostTermError("Unknown host terms document")
    if isinstance(version, bool) or not isinstance(version, int) or version != current_version:
        raise StaleHostTermVersionError(
            "The notice changed; review and accept the current version"
        )

    _materialize_legacy_lawful_use(services)
    records = services.setdefault(HOST_TERMS_CONFIG_KEY, {})
    if not isinstance(records, MutableMapping):
        records = {}
        services[HOST_TERMS_CONFIG_KEY] = records

    existing_version, existing_at = _accepted_record(services, term)
    existing_binding_matches = _binding_matches(_stored_record(services, term), term)
    if (
        existing_version != current_version
        or existing_at is None
        or not existing_binding_matches
    ):
        timestamp = accepted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = {"version": current_version, "accepted_at": timestamp}
        binding = CURRENT_HOST_TERM_BINDINGS.get(term)
        if binding is not None:
            record["binding"] = copy.deepcopy(binding)
        records[term] = record
        if term == LAWFUL_USE_TERM:
            # Keep the predecessor field as a read-only compatibility mirror.
            services["nsfw_accepted_at"] = timestamp
    return host_terms_status(services)


__all__ = [
    "BFL_FLUX1_REVIEW_TERM",
    "BFL_FLUX2_REVIEW_TERM",
    "CIVITAI_PORNMASTER_V4_CREATOR_TERM",
    "CURRENT_HOST_TERM_BINDINGS",
    "CURRENT_HOST_TERM_VERSIONS",
    "HOST_TERMS_CONFIG_KEY",
    "LAWFUL_USE_TERM",
    "KREA2_REVIEW_TERM",
    "KREA2_MOODY_CUTIE_V4_CREATOR_TERM",
    "KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH",
    "KREA2_MOODY_CUTIE_V4_RECIPE_ID",
    "KREA2_MOODY_MIX_V7_CREATOR_TERM",
    "KREA2_MOODY_MIX_V7_RECIPE_GRAPH",
    "KREA2_MOODY_MIX_V7_RECIPE_ID",
    "KREA2_MOODY_RECIPE_GRAPHS",
    "PONPOKE_FLUX2_KLEIN4B_TERM",
    "PONPOKE_FLUX2_KLEIN9B_TERM",
    "PORNMASTER_V4_RECIPE_GRAPH",
    "PORNMASTER_V4_RECIPE_ID",
    "REF2VA_TERM",
    "StaleHostTermVersionError",
    "UnknownHostTermError",
    "accept_host_term",
    "host_term_accepted",
    "host_terms_status",
]
