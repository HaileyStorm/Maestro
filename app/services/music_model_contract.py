"""Pure, fail-closed contract for local music-generation adapters.

This module describes product semantics only.  It deliberately does not name
or import a model implementation, discover artifacts, launch a process, or
select runtime defaults.  A future adapter must provide an exact identity and
a capability probe before Maestro accepts any generation request.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping


CONTRACT_SCHEMA_VERSION = 1
MAX_IDENTITY_BYTES = 512
MAX_LYRICS_BYTES = 256 * 1024
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_FRAME_COUNT = 2**31 - 1
MIN_SEED = -(2**63)
MAX_SEED = 2**63 - 1

IDENTITY_KEYS = frozenset({
    "purpose",
    "provider",
    "engine",
    "model",
    "exact_revision",
})
CAPABILITY_KEYS = frozenset({
    "schema_version",
    "lyrics",
    "instrumental",
    "duration_seconds",
    "seed",
    "max_frames",
    "output_formats",
    "cancel",
    "health",
    "unload",
})
DURATION_KEYS = frozenset({"minimum", "maximum"})
REQUEST_KEYS = frozenset({
    "lyrics",
    "instrumental",
    "duration_seconds",
    "seed",
    "max_frames",
    "output_format",
})

LIFECYCLE_STATES = frozenset({
    "unprobed",
    "probing",
    "ready",
    "working",
    "unavailable",
    "unloading",
    "unloaded",
    "failed",
})
LIFECYCLE_TRANSITIONS: Mapping[str, frozenset[str]] = MappingProxyType({
    "unprobed": frozenset({"probing"}),
    "probing": frozenset({"ready", "unavailable", "failed"}),
    "ready": frozenset({"working", "unavailable", "unloading", "failed"}),
    "working": frozenset({"ready", "unavailable", "failed"}),
    "unavailable": frozenset({"probing", "unloading"}),
    "unloading": frozenset({"unloaded", "failed"}),
    "unloaded": frozenset({"probing"}),
    "failed": frozenset({"probing", "unloading"}),
})

class MusicModelContractError(ValueError):
    """A model identity, probe, request, or lifecycle value is invalid."""


class UnsupportedMusicRequest(MusicModelContractError):
    """A well-formed request asks for an unprobed model capability."""


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
    *,
    label: str,
) -> Mapping[str, object]:
    if type(value) is not dict:
        raise MusicModelContractError(f"{label} must be a plain mapping")
    keys = set(value)
    if not all(type(key) is str for key in keys):
        raise MusicModelContractError(f"{label} keys must be text")
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise MusicModelContractError(
            f"{label} keys are invalid (missing={missing}, unknown={unknown})"
        )
    return value


def _require_bounded_text(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise MusicModelContractError(f"{label} must be text")
    if not value or value != value.strip():
        raise MusicModelContractError(f"{label} must be non-empty and trimmed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MusicModelContractError(f"{label} contains a control character")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise MusicModelContractError(f"{label} is not valid Unicode") from error
    if len(encoded) > MAX_IDENTITY_BYTES:
        raise MusicModelContractError(f"{label} is too large")
    return value


def _require_bool(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise MusicModelContractError(f"{label} must be boolean")
    return value


def _require_finite_number(value: object, *, label: str) -> int | float:
    if type(value) not in (int, float):
        raise MusicModelContractError(f"{label} must be numeric")
    if type(value) is int and not -MAX_DURATION_SECONDS <= value <= MAX_DURATION_SECONDS:
        raise MusicModelContractError(f"{label} is outside global numeric bounds")
    if not math.isfinite(value):
        raise MusicModelContractError(f"{label} must be finite")
    return float(value)


def _require_int(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise MusicModelContractError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise MusicModelContractError(f"{label} is outside the supported bounds")
    return value


def _require_sha256(value: object, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.casefold()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MusicModelContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_exact_revision(value: object) -> str:
    revision = _require_bounded_text(value, label="exact_revision")
    kind, separator, digest = revision.partition(":")
    valid_length = (
        kind == "sha256" and len(digest) == 64
    ) or (
        kind == "git" and len(digest) in {40, 64}
    )
    if (
        separator != ":"
        or not valid_length
        or digest != digest.casefold()
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise MusicModelContractError(
            "exact_revision must be git:<40-or-64-lowercase-hex> "
            "or sha256:<64-lowercase-hex>"
        )
    return revision


@dataclass(frozen=True, slots=True)
class MusicModelIdentity:
    """One exact model target without any implied runtime or artifact lookup."""

    purpose: str
    provider: str
    engine: str
    model: str
    exact_revision: str

    def __post_init__(self) -> None:
        for field, value in (
            ("purpose", self.purpose),
            ("provider", self.provider),
            ("engine", self.engine),
            ("model", self.model),
        ):
            _require_bounded_text(value, label=field)
        _require_exact_revision(self.exact_revision)

    def to_mapping(self) -> dict[str, str]:
        return {
            "purpose": self.purpose,
            "provider": self.provider,
            "engine": self.engine,
            "model": self.model,
            "exact_revision": self.exact_revision,
        }


@dataclass(frozen=True, slots=True)
class MusicCapabilityProbe:
    """Closed, immutable feature report returned by a future adapter."""

    lyrics: bool
    instrumental: bool
    minimum_duration_seconds: int | float
    maximum_duration_seconds: int | float
    seed: bool
    max_frames: int
    output_formats: tuple[str, ...]
    cancel: bool
    health: bool
    unload: bool

    def __post_init__(self) -> None:
        for field, value in (
            ("lyrics", self.lyrics),
            ("instrumental", self.instrumental),
            ("seed", self.seed),
            ("cancel", self.cancel),
            ("health", self.health),
            ("unload", self.unload),
        ):
            _require_bool(value, label=field)
        minimum = _require_finite_number(
            self.minimum_duration_seconds,
            label="duration_seconds.minimum",
        )
        maximum = _require_finite_number(
            self.maximum_duration_seconds,
            label="duration_seconds.maximum",
        )
        if minimum <= 0 or maximum < minimum or maximum > MAX_DURATION_SECONDS:
            raise MusicModelContractError("duration_seconds bounds are invalid")
        object.__setattr__(self, "minimum_duration_seconds", minimum)
        object.__setattr__(self, "maximum_duration_seconds", maximum)
        _require_int(
            self.max_frames,
            label="max_frames",
            minimum=1,
            maximum=MAX_FRAME_COUNT,
        )
        if (
            type(self.output_formats) is not tuple
            or not self.output_formats
            or len(self.output_formats) > 32
        ):
            raise MusicModelContractError("output_formats must be a non-empty bounded tuple")
        formats = tuple(
            _require_bounded_text(item, label="output_format")
            for item in self.output_formats
        )
        if len(set(formats)) != len(formats) or list(formats) != sorted(formats):
            raise MusicModelContractError("output_formats must be unique and sorted")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "lyrics": self.lyrics,
            "instrumental": self.instrumental,
            "duration_seconds": {
                "minimum": self.minimum_duration_seconds,
                "maximum": self.maximum_duration_seconds,
            },
            "seed": self.seed,
            "max_frames": self.max_frames,
            "output_formats": list(self.output_formats),
            "cancel": self.cancel,
            "health": self.health,
            "unload": self.unload,
        }


@dataclass(frozen=True, slots=True)
class ValidatedMusicRequest:
    """All caller-supplied request fields, retained in canonical key order."""

    items: tuple[tuple[str, object], ...]
    capability_probe_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(
            self.capability_probe_sha256,
            label="capability_probe_sha256",
        )
        if (
            type(self.items) is not tuple
            or not self.items
            or not all(
                type(item) is tuple and len(item) == 2
                for item in self.items
            )
        ):
            raise MusicModelContractError("validated request items are invalid")
        keys = tuple(item[0] for item in self.items)
        if (
            not all(type(key) is str for key in keys)
            or keys != tuple(sorted(keys))
            or len(set(keys)) != len(keys)
            or not set(keys).issubset(REQUEST_KEYS)
        ):
            raise MusicModelContractError("validated request keys are invalid")
        values = dict(self.items)
        if "lyrics" in values:
            lyrics = values["lyrics"]
            if type(lyrics) is not str:
                raise MusicModelContractError("lyrics must be text")
            try:
                encoded_lyrics = lyrics.encode("utf-8", errors="strict")
            except UnicodeError as error:
                raise MusicModelContractError("lyrics are not valid Unicode") from error
            if not encoded_lyrics or len(encoded_lyrics) > MAX_LYRICS_BYTES:
                raise MusicModelContractError("lyrics size is invalid")
        if "instrumental" in values:
            _require_bool(values["instrumental"], label="instrumental")
        if values.get("instrumental") is True and "lyrics" in values:
            raise MusicModelContractError("instrumental mode and lyrics are mutually exclusive")
        if "duration_seconds" in values:
            duration = _require_finite_number(
                values["duration_seconds"],
                label="duration_seconds",
            )
            if not 0 < duration <= MAX_DURATION_SECONDS:
                raise MusicModelContractError("duration_seconds is outside global bounds")
            values["duration_seconds"] = duration
        if "seed" in values:
            _require_int(
                values["seed"],
                label="seed",
                minimum=MIN_SEED,
                maximum=MAX_SEED,
            )
        if "max_frames" in values:
            _require_int(
                values["max_frames"],
                label="max_frames",
                minimum=1,
                maximum=MAX_FRAME_COUNT,
            )
        if "output_format" in values:
            _require_bounded_text(values["output_format"], label="output_format")
        object.__setattr__(self, "items", tuple(sorted(values.items())))

    def to_mapping(self) -> dict[str, object]:
        return dict(self.items)


@dataclass(frozen=True, slots=True)
class MusicModelContract:
    """Exact identity plus the probe that was validated for that target."""

    identity: MusicModelIdentity
    capabilities: MusicCapabilityProbe

    def __post_init__(self) -> None:
        if type(self.identity) is not MusicModelIdentity:
            raise MusicModelContractError("identity must be an exact MusicModelIdentity")
        if type(self.capabilities) is not MusicCapabilityProbe:
            raise MusicModelContractError("capabilities must be an exact MusicCapabilityProbe")


def _capability_probe_sha256(capabilities: MusicCapabilityProbe) -> str:
    encoded = json.dumps(
        capabilities.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_music_model_identity(value: object) -> MusicModelIdentity:
    """Parse an identity whose key set and revision semantics are exact."""

    source = _require_exact_keys(value, IDENTITY_KEYS, label="music model identity")
    values = {
        key: _require_bounded_text(source[key], label=key)
        for key in sorted(IDENTITY_KEYS)
    }
    return MusicModelIdentity(
        purpose=values["purpose"],
        provider=values["provider"],
        engine=values["engine"],
        model=values["model"],
        exact_revision=_require_exact_revision(values["exact_revision"]),
    )


def parse_music_capability_probe(value: object) -> MusicCapabilityProbe:
    """Validate the complete probe without ignoring unknown or missing keys."""

    source = _require_exact_keys(value, CAPABILITY_KEYS, label="music capability probe")
    schema = _require_int(
        source["schema_version"],
        label="schema_version",
        minimum=CONTRACT_SCHEMA_VERSION,
        maximum=CONTRACT_SCHEMA_VERSION,
    )
    if schema != CONTRACT_SCHEMA_VERSION:
        raise MusicModelContractError("music capability schema is unsupported")

    duration = _require_exact_keys(
        source["duration_seconds"],
        DURATION_KEYS,
        label="duration_seconds",
    )
    minimum_duration = _require_finite_number(
        duration["minimum"],
        label="duration_seconds.minimum",
    )
    maximum_duration = _require_finite_number(
        duration["maximum"],
        label="duration_seconds.maximum",
    )
    if (
        minimum_duration <= 0
        or maximum_duration < minimum_duration
        or maximum_duration > MAX_DURATION_SECONDS
    ):
        raise MusicModelContractError("duration_seconds bounds are invalid")

    raw_formats = source["output_formats"]
    if (
        type(raw_formats) not in (list, tuple)
        or not raw_formats
        or len(raw_formats) > 32
    ):
        raise MusicModelContractError("output_formats must be a non-empty bounded sequence")
    formats = tuple(
        _require_bounded_text(item, label="output_format")
        for item in raw_formats
    )
    if len(set(formats)) != len(formats) or list(formats) != sorted(formats):
        raise MusicModelContractError("output_formats must be unique and sorted")

    return MusicCapabilityProbe(
        lyrics=_require_bool(source["lyrics"], label="lyrics"),
        instrumental=_require_bool(source["instrumental"], label="instrumental"),
        minimum_duration_seconds=minimum_duration,
        maximum_duration_seconds=maximum_duration,
        seed=_require_bool(source["seed"], label="seed"),
        max_frames=_require_int(
            source["max_frames"],
            label="max_frames",
            minimum=1,
            maximum=MAX_FRAME_COUNT,
        ),
        output_formats=formats,
        cancel=_require_bool(source["cancel"], label="cancel"),
        health=_require_bool(source["health"], label="health"),
        unload=_require_bool(source["unload"], label="unload"),
    )


def validate_music_request(
    value: object,
    capabilities: MusicCapabilityProbe,
) -> ValidatedMusicRequest:
    """Reject malformed or unprobed request fields before runtime work begins."""

    if type(capabilities) is not MusicCapabilityProbe:
        raise TypeError("capabilities must be an exact MusicCapabilityProbe")
    if type(value) is not dict:
        raise MusicModelContractError("music request must be a plain mapping")
    if not value:
        raise MusicModelContractError("music request cannot be empty")
    keys = set(value)
    if not all(type(key) is str for key in keys):
        raise MusicModelContractError("music request keys must be text")
    unknown = sorted(keys - REQUEST_KEYS)
    if unknown:
        raise MusicModelContractError(f"music request contains unknown fields: {unknown}")

    validated: dict[str, object] = {}
    if "lyrics" in value:
        if not capabilities.lyrics:
            raise UnsupportedMusicRequest("lyrics were not advertised by the probe")
        lyrics = value["lyrics"]
        if type(lyrics) is not str:
            raise MusicModelContractError("lyrics must be text")
        try:
            encoded_lyrics = lyrics.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise MusicModelContractError("lyrics are not valid Unicode") from error
        if not encoded_lyrics or len(encoded_lyrics) > MAX_LYRICS_BYTES:
            raise MusicModelContractError("lyrics size is invalid")
        validated["lyrics"] = lyrics

    if "instrumental" in value:
        instrumental = _require_bool(value["instrumental"], label="instrumental")
        if instrumental and not capabilities.instrumental:
            raise UnsupportedMusicRequest("instrumental mode was not advertised by the probe")
        validated["instrumental"] = instrumental

    if value.get("instrumental") is True and "lyrics" in value:
        raise MusicModelContractError("instrumental mode and lyrics are mutually exclusive")

    if "duration_seconds" in value:
        duration = _require_finite_number(
            value["duration_seconds"],
            label="duration_seconds",
        )
        if not (
            capabilities.minimum_duration_seconds
            <= duration
            <= capabilities.maximum_duration_seconds
        ):
            raise UnsupportedMusicRequest("duration_seconds is outside the probed range")
        validated["duration_seconds"] = duration

    if "seed" in value:
        if not capabilities.seed:
            raise UnsupportedMusicRequest("seed was not advertised by the probe")
        validated["seed"] = _require_int(
            value["seed"],
            label="seed",
            minimum=MIN_SEED,
            maximum=MAX_SEED,
        )

    if "max_frames" in value:
        frames = _require_int(
            value["max_frames"],
            label="max_frames",
            minimum=1,
            maximum=MAX_FRAME_COUNT,
        )
        if frames > capabilities.max_frames:
            raise UnsupportedMusicRequest("max_frames exceeds the probed limit")
        validated["max_frames"] = frames

    if "output_format" in value:
        output_format = _require_bounded_text(
            value["output_format"],
            label="output_format",
        )
        if output_format not in capabilities.output_formats:
            raise UnsupportedMusicRequest("output_format was not advertised by the probe")
        validated["output_format"] = output_format

    if set(validated) != keys:
        raise MusicModelContractError("music request fields were not retained exactly")
    return ValidatedMusicRequest(
        tuple(sorted(validated.items())),
        _capability_probe_sha256(capabilities),
    )


def canonical_music_provenance(
    contract: MusicModelContract,
    request: ValidatedMusicRequest,
) -> Mapping[str, object]:
    """Return deterministic provenance without lyrics or content-derived hashes."""

    if type(contract) is not MusicModelContract:
        raise TypeError("contract must be an exact MusicModelContract")
    if type(request) is not ValidatedMusicRequest:
        raise TypeError("request must be an exact ValidatedMusicRequest")

    capability_digest = _capability_probe_sha256(contract.capabilities)
    if request.capability_probe_sha256 != capability_digest:
        raise MusicModelContractError(
            "validated request belongs to a different capability probe"
        )
    revalidated = validate_music_request(request.to_mapping(), contract.capabilities)
    if revalidated.items != request.items:
        raise MusicModelContractError("validated request is not canonical")
    request_values = revalidated.to_mapping()
    request_projection = {
        key: value
        for key, value in request_values.items()
        if key != "lyrics"
    }
    if "lyrics" in request_values:
        request_projection["lyrics_supplied"] = True
    projection: dict[str, object] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "identity": MappingProxyType(contract.identity.to_mapping()),
        "capability_probe_sha256": capability_digest,
        "request": MappingProxyType(dict(sorted(request_projection.items()))),
    }
    return MappingProxyType(projection)


def validate_music_lifecycle_state(value: object) -> str:
    """Return one recognized lifecycle state or fail closed."""

    if type(value) is not str or value not in LIFECYCLE_STATES:
        raise MusicModelContractError("music lifecycle state is invalid")
    return value


def validate_music_lifecycle_transition(previous: object, current: object) -> tuple[str, str]:
    """Validate one explicit adapter lifecycle transition."""

    previous_state = validate_music_lifecycle_state(previous)
    current_state = validate_music_lifecycle_state(current)
    if current_state not in LIFECYCLE_TRANSITIONS[previous_state]:
        raise MusicModelContractError(
            f"music lifecycle transition is invalid: {previous_state} -> {current_state}"
        )
    return previous_state, current_state


__all__ = [
    "CAPABILITY_KEYS",
    "CONTRACT_SCHEMA_VERSION",
    "DURATION_KEYS",
    "IDENTITY_KEYS",
    "LIFECYCLE_STATES",
    "LIFECYCLE_TRANSITIONS",
    "MAX_DURATION_SECONDS",
    "MAX_FRAME_COUNT",
    "MAX_LYRICS_BYTES",
    "MAX_SEED",
    "MIN_SEED",
    "MusicCapabilityProbe",
    "MusicModelContract",
    "MusicModelContractError",
    "MusicModelIdentity",
    "REQUEST_KEYS",
    "UnsupportedMusicRequest",
    "ValidatedMusicRequest",
    "canonical_music_provenance",
    "parse_music_capability_probe",
    "parse_music_model_identity",
    "validate_music_lifecycle_state",
    "validate_music_lifecycle_transition",
    "validate_music_request",
]
